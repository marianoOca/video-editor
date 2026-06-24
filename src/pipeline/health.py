"""
Project artifact integrity probe for the Studio "Subtitles" tab Fix button.

The tab asks the sidecar whether a project's pipeline artifacts are intact and,
if not, from which step to regenerate. Only steps 1-4 gate the Studio preview:
it shows edited.mp4 + the snapshot (captions / image overlays). If edited.mp4
decodes cleanly the project is healthy regardless of later artifacts.

Detection is two-tier per mp4: a fast header probe (parseable, positive
duration, real video stream) catches missing/truncated files; a short decode
pass catches duplicate-MOOV / invalid-NAL corruption that the header probe
misses but the browser decoder (mediabunny, via @remotion/media) chokes on —
exactly the prueba4 'Decoding error' case.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from config import DATA_ROOT, probe


def _mp4_ok(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False

    # Tier 1 — header probe: ffprobe parses it, format duration > 0, and at
    # least one video stream has a known codec + nonzero dimensions.
    try:
        info = probe(path)
    except Exception:
        return False
    try:
        if float(info.get("format", {}).get("duration", 0)) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    has_video = any(
        s.get("codec_type") == "video"
        and s.get("codec_name")
        and int(s.get("width", 0) or 0) > 0
        and int(s.get("height", 0) or 0) > 0
        for s in info.get("streams", [])
    )
    if not has_video:
        return False

    # Tier 2 — decode probe: decode the first couple of seconds and bail on the
    # first error (-xerror). Container/codec corruption (duplicate MOOV, invalid
    # NAL units, truncated atoms) surfaces here as a nonzero exit or stderr text
    # even when the header probe was happy.
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror",
             "-i", str(path), "-t", "2", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and not proc.stderr.strip()


def _json_ok(path: Path, required_key: str) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and required_key in data


def probe_project(name: str) -> dict:
    """Return {project, corrupt, resumeStep, artifacts}. resumeStep is the first
    broken step in 1-4 (the step to re-run from), or None if the preview is healthy."""
    out = DATA_ROOT / name
    # Ordered step -> (artifact, integrity check). First failure sets resumeStep.
    checks = [
        (1, "combined.mp4", lambda p: _mp4_ok(p)),
        (2, "transcript.json", lambda p: _json_ok(p, "segments")),
        (3, "edit_plan.json", lambda p: _json_ok(p, "keep")),
        (4, "edited.mp4", lambda p: _mp4_ok(p)),
    ]
    artifacts = []
    resume_step: Optional[int] = None
    for step, fname, ok in checks:
        valid = ok(out / fname)
        artifacts.append({"step": step, "file": fname, "ok": valid})
        if not valid and resume_step is None:
            resume_step = step
    return {
        "project": name,
        "corrupt": resume_step is not None,
        "resumeStep": resume_step,
        "artifacts": artifacts,
    }
