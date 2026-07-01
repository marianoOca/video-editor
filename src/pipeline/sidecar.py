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
    POST /apply-cuts  body {"project": "<name>", "dropCutIndices": [1, 3]}
                      -> runs 4_render.py --drop-cuts; {"ok": bool, "log": "..."}
    POST /fix         body {"project": "<name>"}
                      -> repair a corrupt project: re-run the pipeline from the last
                         valid step through the cut (step 4) as a BACKGROUND JOB,
                         like /rerun-pipeline (so the in-list bar tracks it). {"jobId"}
    POST /delete-project  body {"project": "<name>"}
                      -> delete the project entirely (data + snapshot + public +
                         state); {"ok": bool, "removed": [...]}. The patched Studio
                         native-delete handler POSTs here.
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
    POST /run-pipeline    body {"inputs":[...], "project", "overwrite"?}
                      -> spawn run_all.py 1-6 as a background job; {"jobId"}
    POST /rerun-pipeline  body {"project", "fromStep": 2}
                      -> re-run an existing project from step N (run_all --from N);
                         {"jobId"}. Step 1 refused if input/ is empty.
    GET  /pipeline-status -> {"job": null | {state,step,total,label,logTail,error}}
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# config resolves the active project at import; the sidecar itself doesn't need
# one (it's passed per-request), so tolerate its absence.
os.environ.setdefault("VE_ALLOW_NO_PROJECT", "1")
from config import (  # noqa: E402
    SIDECAR_PORT, INPUT_DIR, HYPERFRAMES_PORT, sanitize_project_name,
)
from remotion_sync import (  # noqa: E402
    update_snapshot, read_snapshot, delete_project, duplicate_project,
    rename_project, list_projects,
)

PIPELINE_DIR = Path(__file__).parent
PROJECT_RE = re.compile(r"^[A-Za-z0-9-]+$")

VIDEO_EXTS = (".mp4", ".mov")
MAX_UPLOAD_BYTES = 50 * 1024 ** 3  # 50 GB sanity cap (multi-GB videos exist)

# --- Background pipeline job ---
# Creating a project (run_all.py steps 1-6) takes minutes — far longer than an
# HTTP request should block. So a "+ New project" run is spawned as a single
# global background job: the /run-pipeline POST returns immediately, and the
# Studio modal polls /pipeline-status until done. One job at a time (a second
# run, or a Subtitles-tab re-cut, is refused with 409 while one is active),
# which also serializes all writers against the same src/data/<name>/.
_JOB_LOCK = threading.Lock()
_JOB: dict | None = None
_TOTAL_STEPS = 6
# run_all.py prints "▶  <script>" at each step boundary (see its run_step()).
_STEP_LABELS = {
    "1_normalize.py": (1, "Normalizing"),
    "2_transcribe.py": (2, "Transcribing"),
    "3_analyze.py": (3, "Analyzing"),
    "4_render.py": (4, "Cutting + preview"),
    "4b_place_images.py": (5, "Placing images"),
    "5_motion_graphics.py": (6, "Motion graphics"),
}
_STEP_RE = re.compile(r"^▶\s+(\S+\.py)")


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pipeline_busy() -> bool:
    with _JOB_LOCK:
        return _JOB is not None and _JOB["state"] == "running"


def _has_input_videos() -> bool:
    """True if input/ holds at least one .mp4/.mov. Gates re-running step 1, which
    re-normalizes from input/ — pointless (and a hard error) with nothing there."""
    return INPUT_DIR.exists() and any(
        True for ext in VIDEO_EXTS for _ in INPUT_DIR.glob(f"*{ext}")
    )


def _job_snapshot() -> dict | None:
    """JSON-safe copy of the current job under the lock (deque → list)."""
    with _JOB_LOCK:
        if _JOB is None:
            return None
        return {
            "id": _JOB["id"],
            "project": _JOB["project"],
            "state": _JOB["state"],
            "step": _JOB["step"],
            "total": _JOB["total"],
            "label": _JOB["label"],
            "logTail": list(_JOB["logTail"]),
            "error": _JOB["error"],
        }


def _job_reader(proc: subprocess.Popen, job: dict) -> None:
    """Daemon thread: stream the run's stdout, advance step/label on each
    "▶ <script>" boundary, then finalize state from the return code on EOF."""
    for line in proc.stdout:  # blocking read stays OUTSIDE the lock
        line = line.rstrip("\n")
        m = _STEP_RE.match(line)
        with _JOB_LOCK:
            job["logTail"].append(line)
            if m and m.group(1) in _STEP_LABELS:
                job["step"], job["label"] = _STEP_LABELS[m.group(1)]
    proc.wait()
    with _JOB_LOCK:
        job["returncode"] = proc.returncode
        if proc.returncode == 0:
            job["state"], job["step"], job["label"] = "done", _TOTAL_STEPS, "Done"
        else:
            job["state"] = "error"
            job["label"] = "Failed"
            job["error"] = f"pipeline exited with code {proc.returncode}"


def _start_job(cmd: list[str], project: str) -> dict | None:
    """Spawn the pipeline run for `project` in the background. Returns the job
    dict, or None if one is already running (caller answers 409)."""
    global _JOB
    with _JOB_LOCK:
        if _JOB is not None and _JOB["state"] == "running":
            return None
        job = {
            "id": uuid.uuid4().hex[:12],
            "project": project,
            "state": "running",
            "step": 0,
            "total": _TOTAL_STEPS,
            "label": "Starting",
            "logTail": deque(maxlen=24),
            "error": None,
            "returncode": None,
        }
        _JOB = job
    # PYTHONUNBUFFERED so run_all.py's "▶ <step>" boundary markers stream to
    # _job_reader live. Without it run_all's stdout block-buffers against the pipe
    # (run_all prints little, so the buffer never fills) and the markers only flush
    # when it exits — the progress bar then sits at 0 and jumps straight to 100%.
    # Inherited by the child step subprocesses too, so their output streams as well.
    env = {**os.environ, "VE_PROJECT": project, "PYTHONUNBUFFERED": "1"}
    print(f"[sidecar] start job {job['id']} ({project}): {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, cwd=PIPELINE_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    threading.Thread(target=_job_reader, args=(proc, job), daemon=True).start()
    return job


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

    def _run_and_respond(self, cmd: list[str], project: str,
                         extra_on_success=None) -> None:
        """Run a pipeline command for `project` (VE_PROJECT in env, cwd=pipeline)
        and write the JSON response: {ok, log} on completion. No timeout — re-cuts
        and re-transcribes scale with video length, so a half-hour video must run to
        completion rather than be killed mid-edit. On success, merge
        extra_on_success() (a callable, evaluated post-run) into the payload — used
        to return the freshly written snapshot captions."""
        env = {**os.environ, "VE_PROJECT": project}
        print(f"[sidecar] {project}: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd, cwd=PIPELINE_DIR, env=env,
            capture_output=True, text=True,
        )
        log = ((proc.stdout or "") + (proc.stderr or "")).strip()
        tail = "\n".join(log.splitlines()[-12:])
        ok = proc.returncode == 0
        payload = {"ok": ok, "log": tail}
        if ok and extra_on_success:
            try:
                payload.update(extra_on_success())
            except Exception:
                pass  # never let a post-run readback fail the response
        self._json(200 if ok else 500, payload)

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
            self._json(200, {"ok": True, "job": _job_snapshot()})
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

        if _pipeline_busy():
            self._json(409, {"ok": False, "error": "a pipeline run is already active"})
            return

        # Persist the tab's current (edited) captions before re-cutting so
        # 4_render re-maps THEM through the cut — preserving manual edits/splits.
        self._persist_captions(project, data.get("captions"))

        cmd = [sys.executable, "4_render.py"]
        if drop:
            cmd += ["--drop-cuts", ",".join(str(i) for i in drop)]
        if ranges:
            cmd += ["--drop-ranges", ",".join(f"{s:.0f}-{e:.0f}" for s, e in ranges)]
        # Return the re-mapped captions so the tab can resync its Studio store to
        # the new (shorter) timeline — otherwise the pre-cut caption override
        # survives HMR and shadows the snapshot, desyncing captions from the video.
        self._run_and_respond(
            cmd, project,
            extra_on_success=lambda: {"captions": (read_snapshot(project) or {}).get("captions", [])},
        )

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

        if _pipeline_busy():
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return

        from health import probe_project
        step = probe_project(project)["resumeStep"]
        # Healthy (or unknown) → re-cut from step 4 anyway: cheap and idempotent.
        if step is None:
            step = 4

        cmd = [sys.executable, "run_all.py",
               "--from", str(step), "--until", "4", "--no-open", "--project", project]
        job = _start_job(cmd, project)
        if job is None:  # lost a race for the single slot
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project})

    def _delete_project(self) -> None:
        """Delete a project entirely: data dir + Remotion snapshot + public assets,
        clearing active-project state. The patched Studio delete handler POSTs here
        (Root.tsx discovers projects via require.context, so the composition drops
        from the sidebar on the next recompile)."""
        try:
            project, _ = self._read_project()
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        try:
            removed = delete_project(project)
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
        videos = []
        if INPUT_DIR.exists():
            seen = set()
            for ext in VIDEO_EXTS:
                for p in INPUT_DIR.glob(f"*{ext}"):
                    if p.name in seen:
                        continue
                    seen.add(p.name)
                    videos.append({"name": p.name,
                                   "sizeMB": round(p.stat().st_size / 1e6, 1)})
            videos.sort(key=lambda v: v["name"].lower())
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
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        if _pipeline_busy():
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return
        if not overwrite and project in list_projects():
            self._json(409, {"ok": False, "error": "project exists", "project": project})
            return

        cmd = [sys.executable, "run_all.py",
               "--from", "1", "--until", "6", "--no-open",
               "--project", project, "--input", *inputs]
        job = _start_job(cmd, project)
        if job is None:  # lost a race for the single slot
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project})

    def _rerun_pipeline(self) -> None:
        """Re-run the pipeline for an EXISTING project from a chosen step
        (run_all.py --from N --project <name>, running through step 6). Unlike
        /run-pipeline this does NOT refuse an existing project — re-running one is
        the whole point. --project wins over the input-stem fallback in run_all, so
        the named project is always the target. Step 1 re-normalizes from input/,
        so it's refused when input/ is empty (the UI also greys it out)."""
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

        if from_step == 1 and not _has_input_videos():
            self._json(400, {"ok": False, "error": "step 1 needs a video in input/"})
            return

        if _pipeline_busy():
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return

        cmd = [sys.executable, "run_all.py",
               "--from", str(from_step), "--no-open", "--project", project]
        job = _start_job(cmd, project)
        if job is None:  # lost a race for the single slot
            self._json(409, {"ok": False, "error": "a pipeline run is already active",
                             "job": _job_snapshot()})
            return
        self._json(202, {"ok": True, "jobId": job["id"], "project": project})

    def log_message(self, *args) -> None:  # quiet default request logging
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", SIDECAR_PORT), Handler)
    print(f"sidecar listening on http://127.0.0.1:{SIDECAR_PORT}\n"
          f"  POST /apply-cuts /fix /delete-project /save-captions\n"
          f"  POST /duplicate-project /rename-project\n"
          f"  POST /upload-video /import-path /run-pipeline /rerun-pipeline\n"
          f"  GET  /health /project-health /input-videos /pipeline-status\n"
          f"Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nsidecar stopped")
        server.server_close()


if __name__ == "__main__":
    main()
