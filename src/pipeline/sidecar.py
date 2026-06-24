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
                      -> regenerate from the last valid step (no Studio re-open,
                         no final render); {"ok": bool, "log": "..."}
    POST /delete-project  body {"project": "<name>"}
                      -> delete the project entirely (data + snapshot + public +
                         state); {"ok": bool, "removed": [...]}. The patched Studio
                         native-delete handler POSTs here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# config resolves the active project at import; the sidecar itself doesn't need
# one (it's passed per-request), so tolerate its absence.
os.environ.setdefault("VE_ALLOW_NO_PROJECT", "1")
from config import SIDECAR_PORT  # noqa: E402
from remotion_sync import update_snapshot, read_snapshot, delete_project  # noqa: E402

PIPELINE_DIR = Path(__file__).parent
PROJECT_RE = re.compile(r"^[A-Za-z0-9-]+$")


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
        if self.path == "/health":
            self._json(200, {"ok": True})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/project-health":
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
        if self.path == "/apply-cuts":
            self._apply_cuts()
        elif self.path == "/save-captions":
            self._save_captions()
        elif self.path == "/fix":
            self._fix()
        elif self.path == "/delete-project":
            self._delete_project()
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
        """Regenerate a corrupt project from the last valid step, in place, without
        opening a second Studio or running motion graphics / the final render."""
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

        if step >= 4:
            cmd = [sys.executable, "4_render.py", "--no-open"]
        else:
            # --project pins the target (step 1 would otherwise grab an input video).
            cmd = [sys.executable, "run_all.py",
                   "--from", str(step), "--until", "4", "--no-open",
                   "--project", project]
        # No timeout: re-transcribe/analyze/re-cut scale with video length.
        self._run_and_respond(cmd, project)

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

    def log_message(self, *args) -> None:  # quiet default request logging
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", SIDECAR_PORT), Handler)
    print(f"sidecar listening on http://127.0.0.1:{SIDECAR_PORT} "
          f"(POST /apply-cuts /fix /delete-project, GET /health /project-health) — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nsidecar stopped")
        server.server_close()


if __name__ == "__main__":
    main()
