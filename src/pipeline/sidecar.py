"""
Local sidecar for the Remotion Studio "Subtitles" tab.

Browser JS can't run a Python script, so the tab's "Apply" button POSTs here and
this server shells out to `4_render.py --drop-cuts` to re-cut the video. Mirrors
the Hyperframes-on-9847 idiom: a tiny local endpoint you start in a terminal.

Start it (alongside Remotion Studio):
    cd src/pipeline && python3 sidecar.py

Endpoints (CORS-open for localhost Studio on :3000):
    GET  /health      -> {"ok": true}
    POST /apply-cuts  body {"project": "<name>", "dropCutIndices": [1, 3]}
                      -> runs 4_render.py --drop-cuts; {"ok": bool, "log": "..."}
"""

from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# config resolves the active project at import; the sidecar itself doesn't need
# one (it's passed per-request), so tolerate its absence.
os.environ.setdefault("VE_ALLOW_NO_PROJECT", "1")
from config import SIDECAR_PORT  # noqa: E402

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

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/apply-cuts":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            project = str(data.get("project", ""))
            if not PROJECT_RE.match(project):
                raise ValueError("invalid project name")
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

        import subprocess

        env = {**os.environ, "VE_PROJECT": project}
        cmd = [sys.executable, "4_render.py"]
        if drop:
            cmd += ["--drop-cuts", ",".join(str(i) for i in drop)]
        if ranges:
            cmd += ["--drop-ranges", ",".join(f"{s:.0f}-{e:.0f}" for s, e in ranges)]
        print(f"[sidecar] {project}: cuts={drop} ranges={ranges} -> {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd, cwd=PIPELINE_DIR, env=env,
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._json(504, {"ok": False, "error": "re-render timed out"})
            return

        log = ((proc.stdout or "") + (proc.stderr or "")).strip()
        tail = "\n".join(log.splitlines()[-12:])
        if proc.returncode == 0:
            self._json(200, {"ok": True, "log": tail})
        else:
            self._json(500, {"ok": False, "log": tail})

    def log_message(self, *args) -> None:  # quiet default request logging
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", SIDECAR_PORT), Handler)
    print(f"sidecar listening on http://127.0.0.1:{SIDECAR_PORT} "
          f"(POST /apply-cuts, GET /health) — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nsidecar stopped")
        server.server_close()


if __name__ == "__main__":
    main()
