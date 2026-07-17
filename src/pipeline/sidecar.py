"""
Local sidecar for the Remotion Studio "Subtitles" tab.

Browser JS can't run a Python script, so the tab's "Apply" button POSTs here and
this server shells out to `4_render.py --drop-cuts` to re-cut the video. Mirrors
the Hyperframes-on-9847 idiom: a tiny local endpoint you start in a terminal.

Start it (alongside Remotion Studio):
    cd src/pipeline && python3 sidecar.py

Endpoints (CORS-open for localhost Studio on :3000):
    GET  /health          -> {"ok": true}
    GET  /project-health?project=<name>
                          -> probe artifacts; {"ok", "corrupt", "resumeStep", "artifacts"}
    GET  /project-files?project=<name>
                          -> shared source/output files (from manifest) for the Delete
                             modal; {"input":{files,exists}, "output":{files,exists}}
    POST /apply-cuts  body {"project": "<name>", "dropCutIndices": [1, 3]}
                      -> persists the edited captions, then ENQUEUES a background
                         re-cut job (4_render.py --drop-cuts/--drop-ranges); {"jobId"}.
                         The tab hands off to the sidebar row; the build store hard-
                         reloads Studio when the recut job finishes.
    POST /fix         body {"project": "<name>"}
                      -> repair a corrupt project: re-run the pipeline from the last
                         valid step through the cut (step 4) as a BACKGROUND JOB,
                         like /rerun-pipeline (so the in-list bar tracks it). {"jobId"}
    POST /delete-project  body {"project": "<name>", "deleteInput"?, "deleteOutput"?}
                      -> delete the project entirely (data + snapshot + public +
                         state); {"ok": bool, "removed": [...]}. The patched Studio
                         native-delete handler POSTs here. deleteInput/deleteOutput
                         also unlink the project's source video(s) under input/ and/or
                         its rendered output(s) (the modal's cleanup options).
    POST /duplicate-project  body {"from": "<src>", "to": "<dst>"}
                      -> copy the project under a new name (all three folders);
                         {"ok": bool, "created": [...]} (409 if <dst> exists).
    POST /rename-project  body {"from": "<old>", "to": "<new>"}
                      -> rename the project (move all three folders); {"ok": bool,
                         "moved": [...]} (409 if <new> exists). The patched Studio
                         native duplicate/rename handlers POST here.

  "+ New project" (Studio Compositions panel → modal):
    GET  /input-videos    -> {videos:[{name,sizeMB}], projects:[...], hyperframesUp}
    POST /upload-video?filename=<n>  raw body -> stream a dropped video into input/
    POST /import-path     body {"path": "/abs/video.mp4"} -> reference it in place
    POST /run-pipeline    body {"inputs":[...], "project", "overwrite"?, "render"?}
                      -> enqueue run_all.py 1-6 as a background job; render=true adds
                         a final render step (output/<project>.mp4); {"jobId"}
    POST /rerun-pipeline  body {"project", "fromStep": 2}
                      -> enqueue a re-run from step N (run_all --from N); {"jobId"}.
                         Step 1 rebuilds from the project's manifest sources.
    POST /dequeue     body {"id": "<jobId>"}
                      -> cancel a QUEUED job (before it starts) or dismiss a
                         FINISHED done/error one; 409 if it's currently running.
    GET  /pipeline-status -> {"jobs": [{id,project,kind,state,step,total,label,
                              error,queuePos}, ...]}

  Job queue: new / rerun / fix / recut are all appended to ONE FIFO queue drained
  by a single worker thread — they run one at a time, next-starts-when-current-
  finishes, in the order added. A project already queued/running rejects a second
  job for it (409). A failed job flips to "error" and the queue keeps going.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# config resolves the active project at import; the sidecar itself doesn't need
# one (it's passed per-request), so tolerate its absence.
os.environ.setdefault("VE_ALLOW_NO_PROJECT", "1")
from config import (  # noqa: E402
    SIDECAR_PORT, INPUT_DIR, HYPERFRAMES_PORT, sanitize_project_name,
    VIDEO_EXTS, list_videos, read_manifest,
)
from remotion_sync import (  # noqa: E402
    update_snapshot, delete_project, duplicate_project,
    rename_project, list_projects, project_input_files, project_output_files,
)

PIPELINE_DIR = Path(__file__).parent
PROJECT_RE = re.compile(r"^[A-Za-z0-9-]+$")

MAX_UPLOAD_BYTES = 50 * 1024 ** 3  # 50 GB sanity cap (multi-GB videos exist)

# --- Background pipeline queue ---
# Heavy pipeline work (new project, re-run, fix, Subtitles-tab re-cut) takes
# minutes — far longer than an HTTP request should block. Every such action is
# turned into a JOB and appended to a single FIFO queue; one long-lived worker
# thread drains it, running exactly ONE job at a time (so all writers against the
# same src/data/<name>/ stay serialized) and starting the next the moment the
# current finishes. The POST returns immediately (202 + jobId); the Studio UI
# polls /pipeline-status and shows per-project progress/queued state in the
# sidebar. This replaces the old "one slot, else 409" model: nothing is refused
# for being busy — it queues (dedupe: a project already queued/running is 409).
#
# A job dict: {id, project, kind, cmd, state, step, total, label, error,
#              returncode, finishedAt}. kind ∈ {new, rerun, fix, recut}.
# state ∈ {queued, running, done, error}.
_CV = threading.Condition()                       # guards all queue state below
_QUEUE: deque[dict] = deque()                     # pending jobs, FIFO
_RUNNING: dict | None = None                      # job currently executing
_JOBS: "OrderedDict[str, dict]" = OrderedDict()   # every non-evicted job, by id
_DONE_TTL = 6.0                                    # seconds a finished job lingers
_TOTAL_STEPS = 6
# run_all.py prints "▶  <token>" at each step boundary (see its run_step()). The
# token is the script name, except the optional final render step emits "render".
_STEP_LABELS = {
    "1_normalize.py": (1, "Normalizing"),
    "2_transcribe.py": (2, "Transcribing"),
    "3_analyze.py": (3, "Analyzing"),
    "4_render.py": (4, "Cutting + preview"),
    "4b_place_images.py": (5, "Placing images"),
    "5_motion_graphics.py": (6, "Motion graphics"),
    "render": (7, "Rendering"),
}
# Any non-space token; the `in _STEP_LABELS` guard ignores unrelated ▶ lines
# (e.g. run_all's "▶ project: <name>").
_STEP_RE = re.compile(r"^▶\s+(\S+)")


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _project_state(project: str) -> str | None:
    """'running' | 'queued' | None for a project — caller must hold _CV."""
    for j in _JOBS.values():
        if j["project"] == project and j["state"] in ("running", "queued"):
            return j["state"]
    return None


def _project_running(project: str) -> bool:
    with _CV:
        return any(j["project"] == project and j["state"] == "running"
                   for j in _JOBS.values())


def _enqueue(cmd: list[str], project: str, kind: str,
             total: int = _TOTAL_STEPS) -> dict | None:
    """Append a job (dedupe by project). Returns the job, or None if the project
    already has a queued/running job (caller answers 409)."""
    with _CV:
        if _project_state(project) is not None:
            return None
        job = {
            "id": uuid.uuid4().hex[:12],
            "project": project, "kind": kind, "cmd": cmd,
            "state": "queued", "step": 0, "total": total,
            "label": "Queued", "error": None,
            "returncode": None, "finishedAt": None,
        }
        _JOBS[job["id"]] = job
        _QUEUE.append(job)
        _CV.notify_all()
        return job


def _dequeue(job_id: str) -> tuple[bool, str]:
    """Remove a QUEUED job (cancel) or a FINISHED done/error job (dismiss).
    Returns (ok, reason). Refuses a running job."""
    with _CV:
        job = _JOBS.get(job_id)
        if job is None:
            return False, "not found"
        if job["state"] == "running":
            return False, "running"
        try:
            _QUEUE.remove(job)
        except ValueError:
            pass  # a finished job is no longer in the queue
        del _JOBS[job_id]
        return True, "removed"


def _cancel_queued_project(project: str) -> list[str]:
    """Drop a project's pending (queued, not running) job — used by delete/rename
    so a project with a queued re-run can be removed without waiting."""
    with _CV:
        removed = []
        for j in list(_JOBS.values()):
            if j["project"] == project and j["state"] == "queued":
                try:
                    _QUEUE.remove(j)
                except ValueError:
                    pass
                del _JOBS[j["id"]]
                removed.append(j["id"])
        return removed


def _status_jobs() -> list[dict]:
    """Public, JSON-safe view of all jobs (queued in order, running, recently
    finished). Evicts 'done' jobs older than _DONE_TTL so the list self-clears
    even if the tab was closed when they finished; 'error' jobs persist until
    dequeued. Includes queuePos (0-based) for queued jobs."""
    now = time.monotonic()
    with _CV:
        for jid in [j["id"] for j in _JOBS.values()
                    if j["state"] == "done" and j["finishedAt"] is not None
                    and now - j["finishedAt"] > _DONE_TTL]:
            _JOBS.pop(jid, None)
        qpos = {j["id"]: i for i, j in enumerate(_QUEUE)}
        return [{
            "id": j["id"], "project": j["project"], "kind": j["kind"],
            "state": j["state"], "step": j["step"], "total": j["total"],
            "label": j["label"], "error": j["error"],
            "queuePos": qpos.get(j["id"]),
        } for j in _JOBS.values()]


def _run_job(job: dict) -> None:
    """Run one job to completion, streaming stdout to advance step/label on each
    run_all "▶ <script>" boundary. A re-cut runs 4_render.py directly (no markers)
    so it stays step 0 with a 'Re-cutting…' label until it finishes."""
    # PYTHONUNBUFFERED so run_all's step markers stream live (its stdout would
    # otherwise block-buffer against the pipe and only flush on exit — bar stuck
    # at 0 then jumps to 100). Inherited by the child step subprocesses too.
    env = {**os.environ, "VE_PROJECT": job["project"], "PYTHONUNBUFFERED": "1"}
    print(f"[sidecar] start job {job['id']} ({job['project']}, {job['kind']}): "
          f"{' '.join(job['cmd'])}")
    try:
        proc = subprocess.Popen(
            job["cmd"], cwd=PIPELINE_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
        )
    except Exception as e:  # spawn failure — never leave the job "running"
        with _CV:
            job["state"], job["label"] = "error", "Failed"
            job["error"], job["finishedAt"] = str(e), time.monotonic()
        return
    # errors="replace": one undecodable byte in ffmpeg/whisper output would
    # otherwise kill this reader mid-stream and hang the job "running" forever.
    for line in proc.stdout:  # blocking read stays OUTSIDE the lock
        m = _STEP_RE.match(line.rstrip("\n"))
        if m and m.group(1) in _STEP_LABELS:
            with _CV:
                job["step"], job["label"] = _STEP_LABELS[m.group(1)]
    proc.wait()
    with _CV:
        job["returncode"] = proc.returncode
        job["finishedAt"] = time.monotonic()
        if proc.returncode == 0:
            job["state"], job["step"], job["label"] = "done", job["total"], "Done"
        else:
            job["state"], job["label"] = "error", "Failed"
            job["error"] = f"pipeline exited with code {proc.returncode}"


def _worker() -> None:
    """Single daemon thread: wait for a queued job with nothing running, run it,
    repeat. A failed job just flips to 'error' and the loop continues (the queue
    keeps going — one bad video never stalls the rest)."""
    global _RUNNING
    while True:
        with _CV:
            while not _QUEUE or _RUNNING is not None:
                _CV.wait()
            job = _QUEUE.popleft()
            _RUNNING = job
            job["state"] = "running"
            job["label"] = "Re-cutting…" if job["kind"] == "recut" else "Starting"
        _run_job(job)
        with _CV:
            _RUNNING = None
            _CV.notify_all()


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _read_project(self) -> tuple[str, dict]:
        """Parse + validate (project, body) from a POST JSON body. Raises on bad input."""
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        project = str(data.get("project", ""))
        if not PROJECT_RE.match(project):
            raise ValueError("invalid project name")
        return project, data

    def _read_from_to(self) -> tuple[str, str]:
        """Parse + validate (from, to) project names from a POST JSON body. Used by
        duplicate/rename (the patched Studio codemod handler POSTs {from, to})."""
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        src = str(data.get("from", ""))
        dst = str(data.get("to", ""))
        if not PROJECT_RE.match(src) or not PROJECT_RE.match(dst):
            raise ValueError("invalid project name")
        return src, dst

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/input-videos":
            self._input_videos()
            return
        if path == "/pipeline-status":
            self._json(200, {"ok": True, "jobs": _status_jobs()})
            return
        if path == "/project-files":
            self._project_files(parse_qs(parsed.query).get("project", [""])[0])
            return
        if path == "/project-health":
            project = (parse_qs(parsed.query).get("project", [""])[0]).strip()
            if not PROJECT_RE.match(project):
                self._json(400, {"ok": False, "error": "invalid project name"})
                return
            try:
                from health import probe_project
                report = probe_project(project)
            except Exception as e:  # never let a probe failure crash the tab
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, **report})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/apply-cuts":
            self._apply_cuts()
        elif path == "/save-captions":
            self._save_captions()
        elif path == "/fix":
            self._fix()
        elif path == "/delete-project":
            self._delete_project()
        elif path == "/duplicate-project":
            self._duplicate_project()
        elif path == "/rename-project":
            self._rename_project()
        elif path == "/upload-video":
            self._upload_video()
        elif path == "/import-path":
            self._import_path()
        elif path == "/run-pipeline":
            self._run_pipeline()
        elif path == "/rerun-pipeline":
            self._rerun_pipeline()
        elif path == "/dequeue":
            self._dequeue_job()
        else:
            self._json(404, {"ok": False, "error": "not found"})

    @staticmethod
    def _persist_captions(project: str, captions) -> None:
        """Write an edited caption list into the project snapshot so manual edits
        (text/splits/merges) are durable + visible to a CLI render. Best-effort:
        a missing snapshot or bad payload is swallowed (preview-only fallback)."""
        if not isinstance(captions, list):
            return
        try:
            update_snapshot(project, captions=captions)
        except FileNotFoundError:
            pass  # no snapshot yet — nothing to persist onto

    def _apply_cuts(self) -> None:
        try:
            project, data = self._read_project()
            drop = [int(i) for i in data.get("dropCutIndices", [])]
            ranges = [
                (float(r["startMs"]), float(r["endMs"]))
                for r in data.get("dropRanges", [])
            ]
            if not drop and not ranges:
                raise ValueError("nothing to delete (dropCutIndices + dropRanges empty)")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        # Persist the tab's current (edited) captions BEFORE enqueuing so the
        # re-cut job's 4_render re-maps THEM through the cut (manual edits/splits
        # survive). Done synchronously here so it's on disk before the worker can
        # pick the job up. Best-effort: a missing snapshot is swallowed.
        self._persist_captions(project, data.get("captions"))

        cmd = [sys.executable, "4_render.py"]
        if drop:
            cmd += ["--drop-cuts", ",".join(str(i) for i in drop)]
        if ranges:
            cmd += ["--drop-ranges", ",".join(f"{s:.0f}-{e:.0f}" for s, e in ranges)]
        # Enqueue as a background job (kind=recut) instead of blocking the request.
        # total=1: 4_render emits no run_all "▶" step markers, so the bar is
        # indeterminate until done. The tab hands off to the sidebar row; on job
        # completion the build store hard-reloads to pick up the re-mapped snapshot
        # (dropping the in-memory caption override). 409 if this project already has
        # a queued/running job.
        job = _enqueue(cmd, project, "recut", total=1)
        if job is None:
            self._json(409, {"ok": False,
                             "error": "this project already has a job queued or running"})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project,
                         "state": job["state"]})

    def _save_captions(self) -> None:
        """Persist edited captions to the snapshot (debounced auto-save from the
        tab). No re-cut — just makes text/split/merge edits durable. An optional
        `captionsEnabled` rides along (the 'Display on video' toggle) so the on/off
        state persists across reloads + re-cuts via the same save path."""
        try:
            project, data = self._read_project()
            captions = data.get("captions")
            if not isinstance(captions, list):
                raise ValueError("captions must be a list")
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        if _project_running(project):
            self._json(409, {"ok": False,
                             "error": "project has a run in progress; save skipped"})
            return
        fields = {"captions": captions}
        if "captionsEnabled" in data:
            fields["captionsEnabled"] = bool(data["captionsEnabled"])
        try:
            update_snapshot(project, **fields)
        except FileNotFoundError:
            self._json(404, {"ok": False, "error": "snapshot not found"})
            return
        self._json(200, {"ok": True, "count": len(captions)})

    def _fix(self) -> None:
        """Repair a corrupt project by re-running the pipeline from its last valid
        step through the cut (step 4), as a BACKGROUND JOB — the same mechanism as
        /rerun-pipeline, so the Studio in-list progress bar tracks it and the UX is
        consistent with a re-run / a new project. Always routed through run_all.py
        (step>=4 becomes `--from 4 --until 4`, i.e. step 4 alone) so its "▶ <step>"
        boundary markers drive the bar; calling 4_render.py directly would emit no
        markers and the bar wouldn't advance. --project pins the target (step 1 would
        otherwise grab an input video). No final render, no second Studio."""
        try:
            project, _ = self._read_project()
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        from health import probe_project
        step = probe_project(project)["resumeStep"]
        # Healthy (or unknown) → re-cut from step 4 anyway: cheap and idempotent.
        if step is None:
            step = 4

        cmd = [sys.executable, "run_all.py",
               "--from", str(step), "--until", "4", "--no-open", "--project", project]
        job = _enqueue(cmd, project, "fix")
        if job is None:  # this project already has a queued/running job
            self._json(409, {"ok": False,
                             "error": "this project already has a job queued or running"})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project,
                         "state": job["state"]})

    def _dequeue_job(self) -> None:
        """Cancel a QUEUED job (before it starts) or dismiss a FINISHED done/error
        job. Refuses a running job (can't cancel a live pipeline). The queued-row
        ✕ and the build store's recut-done cleanup both POST here."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            job_id = str(data.get("id", ""))
            if not job_id:
                raise ValueError("id required")
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        ok, reason = _dequeue(job_id)
        if ok:
            self._json(200, {"ok": True, "removed": [job_id]})
        elif reason == "running":
            self._json(409, {"ok": False, "error": "job is running; can't cancel"})
        else:
            self._json(404, {"ok": False, "error": reason})

    def _project_files(self, project: str) -> None:
        """Report a project's SHARED source + rendered files, so the Delete modal can
        enable/disable its cleanup options. GET, read-only. Returns basenames + an
        `exists` flag for each of input/output. Outputs come from the manifest AND a
        naming-convention glob (project_output_files), so a Studio-rendered file —
        never recorded in the manifest — is still offered for deletion; a legacy
        project with no manifest still reports its outputs."""
        project = (project or "").strip()
        if not PROJECT_RE.match(project):
            self._json(400, {"ok": False, "error": "invalid project name"})
            return
        try:
            inputs = project_input_files(project)
            outputs = project_output_files(project)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {
            "ok": True,
            "input": {"files": [p.name for p in inputs], "exists": bool(inputs)},
            "output": {"files": [p.name for p in outputs], "exists": bool(outputs)},
        })

    def _delete_project(self) -> None:
        """Delete a project entirely: data dir + Remotion snapshot + public assets,
        clearing active-project state. The patched Studio delete handler POSTs here
        (Root.tsx discovers projects via require.context, so the composition drops
        from the sidebar on the next recompile). Optional {deleteInput, deleteOutput}
        also remove the project's source video(s) under input/ and/or its rendered
        output(s) — the modal's three cleanup choices."""
        try:
            project, data = self._read_project()
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        del_input = bool(data.get("deleteInput"))
        del_output = bool(data.get("deleteOutput"))
        # A RUNNING project can't be deleted (files are being written); a QUEUED
        # one is fine — cancel its pending job first, then delete. Deleting a
        # different project during a run is always allowed.
        if _project_running(project):
            self._json(409, {"ok": False, "error": "project has a run in progress"})
            return
        _cancel_queued_project(project)
        try:
            removed = delete_project(project, delete_input=del_input,
                                     delete_output=del_output)
        except Exception as e:  # never crash the caller
            self._json(500, {"ok": False, "error": str(e)})
            return
        print(f"[sidecar] deleted project {project}: {removed}")
        self._json(200, {"ok": True, "removed": removed})

    def _duplicate_project(self) -> None:
        """Copy a project under a new name (all three folders + rewritten snapshot).
        The patched Studio native-duplicate handler POSTs here; Root.tsx discovers
        projects via require.context, so the copy appears in the sidebar on the next
        recompile."""
        try:
            src, dst = self._read_from_to()
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        if _project_running(src) or _project_running(dst):
            self._json(409, {"ok": False, "error": "project has a run in progress"})
            return
        _cancel_queued_project(src)
        _cancel_queued_project(dst)
        try:
            created = duplicate_project(src, dst)
        except FileExistsError as e:
            self._json(409, {"ok": False, "error": str(e)})
            return
        except Exception as e:  # never crash the caller
            self._json(500, {"ok": False, "error": str(e)})
            return
        print(f"[sidecar] duplicated {src} -> {dst}: {created}")
        self._json(200, {"ok": True, "created": created})

    def _rename_project(self) -> None:
        """Rename a project (move all three folders + rewrite/move snapshot). The
        patched Studio native-rename handler POSTs here; the sidebar updates on the
        next recompile."""
        try:
            src, dst = self._read_from_to()
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        if _project_running(src) or _project_running(dst):
            self._json(409, {"ok": False, "error": "project has a run in progress"})
            return
        _cancel_queued_project(src)
        _cancel_queued_project(dst)
        try:
            moved = rename_project(src, dst)
        except FileExistsError as e:
            self._json(409, {"ok": False, "error": str(e)})
            return
        except Exception as e:  # never crash the caller
            self._json(500, {"ok": False, "error": str(e)})
            return
        print(f"[sidecar] renamed {src} -> {dst}: {moved}")
        self._json(200, {"ok": True, "moved": moved})

    # --- "+ New project" (Studio Compositions panel) ---

    def _input_videos(self) -> None:
        """List videos staged in input/ + existing projects (for collision warn) +
        whether the Hyperframes producer is up (reel step 6 needs it)."""
        videos = [{"name": p.name, "sizeMB": round(p.stat().st_size / 1e6, 1)}
                  for p in list_videos(INPUT_DIR)]
        self._json(200, {
            "ok": True,
            "hyperframesUp": _port_open(HYPERFRAMES_PORT),
            "videos": videos,
            "projects": list_projects(),
        })

    def _upload_video(self) -> None:
        """Stream a dropped video (raw octet-stream body) into input/. Validates
        ext + Content-Length cap BEFORE reading; writes in 1 MB chunks so multi-GB
        files never buffer in memory. Staging only — does not start a job."""
        raw_name = parse_qs(urlparse(self.path).query).get("filename", [""])[0]
        ext = Path(raw_name).suffix.lower()
        if ext not in VIDEO_EXTS:
            self.close_connection = True  # body unread → don't reuse the socket
            self._json(400, {"ok": False, "error": "only .mp4/.mov supported"})
            return
        stem = sanitize_project_name(Path(raw_name).stem)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self.close_connection = True
            self._json(400, {"ok": False, "error": "missing Content-Length"})
            return
        if length > MAX_UPLOAD_BYTES:
            self.close_connection = True
            self._json(413, {"ok": False, "error": "file too large"})
            return

        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        dest = INPUT_DIR / f"{stem}{ext}"
        remaining = length
        try:
            with open(dest, "wb") as f:
                while remaining > 0:
                    buf = self.rfile.read(min(1024 * 1024, remaining))
                    if not buf:
                        break
                    f.write(buf)
                    remaining -= len(buf)
        except OSError as e:
            self._json(500, {"ok": False, "error": str(e)})
            return
        if remaining > 0:
            # Client disconnected mid-body: a truncated video must not stay staged
            # in input/ looking like a complete upload.
            dest.unlink(missing_ok=True)
            self._json(400, {"ok": False,
                             "error": f"upload truncated ({remaining} bytes missing)"})
            return
        print(f"[sidecar] uploaded {dest.name} ({length / 1e6:.1f} MB)")
        self._json(202, {"ok": True, "name": dest.name, "savedAs": f"input/{dest.name}"})

    def _import_path(self) -> None:
        """Reference a local video by absolute path — no copy. The path is validated
        here and later passed straight to run_all --input (1_normalize reads abs
        paths in place), so this works for any file size with zero duplication."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            raw = str(data.get("path", "")).strip()
            if not raw:
                raise ValueError("path required")
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if not p.is_file():
            self._json(400, {"ok": False, "error": f"not a file: {p}"})
            return
        if p.suffix.lower() not in VIDEO_EXTS:
            self._json(400, {"ok": False, "error": "only .mp4/.mov supported"})
            return
        self._json(202, {"ok": True, "name": p.name, "path": str(p), "external": True})

    def _run_pipeline(self) -> None:
        """Spawn run_all.py steps 1-6 on the selected inputs (bare input/ names
        and/or absolute external paths), merged in order, as a background job."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            inputs = data.get("inputs", [])
            if not isinstance(inputs, list) or not inputs:
                raise ValueError("inputs must be a non-empty list")
            inputs = [str(x) for x in inputs]
            project = sanitize_project_name(str(data.get("project", "")).strip())
            if not PROJECT_RE.match(project):
                raise ValueError("invalid project name")
            overwrite = bool(data.get("overwrite", False))
            render = bool(data.get("render", False))
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        if not overwrite and project in list_projects():
            self._json(409, {"ok": False, "error": "project exists", "project": project})
            return

        cmd = [sys.executable, "run_all.py",
               "--from", "1", "--until", "6", "--no-open",
               "--project", project, "--input", *inputs]
        # "Create & render": run the pipeline, then render output/<project>.mp4 as a
        # final step (step 7 in the progress bar). --render + --no-open coexist:
        # step 4 writes the snapshot without opening Studio, the render runs at the end.
        if render:
            cmd.append("--render")
        job = _enqueue(cmd, project, "new", total=7 if render else _TOTAL_STEPS)
        if job is None:  # this project already has a queued/running job
            self._json(409, {"ok": False,
                             "error": "this project already has a job queued or running"})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project,
                         "state": job["state"]})

    def _rerun_pipeline(self) -> None:
        """Re-run the pipeline for an EXISTING project from a chosen step
        (run_all.py --from N --project <name>, running through step 6). Unlike
        /run-pipeline this does NOT refuse an existing project — re-running one is
        the whole point. --project wins over the input-stem fallback in run_all, so
        the named project is always the target. Step 1 must rebuild from the project's
        ORIGINAL sources (from the manifest), NOT from whatever currently sits in the
        shared input/ folder — so from_step==1 passes the manifest inputs explicitly
        as --input, and is refused if any source is missing."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            project = str(data.get("project", ""))
            if not PROJECT_RE.match(project):
                raise ValueError("invalid project name")
            from_step = int(data.get("fromStep", 2))
            if not 1 <= from_step <= 6:
                raise ValueError("fromStep must be 1-6")
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        # from_step==1 re-normalizes: feed the project's own source videos (the
        # manifest records every one, incl. external paths), not the shared input/
        # glob — otherwise a multi-video project gets rebuilt from the wrong set.
        step1_inputs: list[str] = []
        if from_step == 1:
            try:
                manifest_inputs = read_manifest(project).get("inputs", [])
            except FileNotFoundError as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            missing = [p for p in manifest_inputs if not Path(p).exists()]
            if not manifest_inputs or missing:
                self._json(400, {"ok": False, "error":
                                 "step 1 needs the project's source video(s); "
                                 f"missing/none: {missing or 'no sources recorded'}"})
                return
            step1_inputs = manifest_inputs

        cmd = [sys.executable, "run_all.py",
               "--from", str(from_step), "--no-open", "--project", project]
        if step1_inputs:
            cmd += ["--input", *step1_inputs]
        job = _enqueue(cmd, project, "rerun")
        if job is None:  # this project already has a queued/running job
            self._json(409, {"ok": False,
                             "error": "this project already has a job queued or running"})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project,
                         "state": job["state"]})

    def log_message(self, *args) -> None:  # quiet default request logging
        pass


def main() -> None:
    # Single daemon worker drains the job queue (new / rerun / fix / recut), one
    # at a time, starting the next as soon as the current finishes.
    threading.Thread(target=_worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", SIDECAR_PORT), Handler)
    print(f"sidecar listening on http://127.0.0.1:{SIDECAR_PORT}\n"
          f"  POST /apply-cuts /fix /delete-project /save-captions\n"
          f"  POST /duplicate-project /rename-project /dequeue\n"
          f"  POST /upload-video /import-path /run-pipeline /rerun-pipeline\n"
          f"  GET  /health /project-health /input-videos /pipeline-status /project-files\n"
          f"queue: heavy jobs run one at a time via a FIFO worker\n"
          f"Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nsidecar stopped")
        server.server_close()


if __name__ == "__main__":
    main()
