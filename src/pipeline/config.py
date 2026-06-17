import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).parent
SRC_DIR = PIPELINE_DIR.parent
REPO_ROOT = SRC_DIR.parent
DATA_ROOT = SRC_DIR / "data"
INPUT_DIR = SRC_DIR.parent / "input"
OUTPUT_DIR = SRC_DIR.parent / "output"
REMOTION_DIR = SRC_DIR / "remotion"
HYPERFRAMES_PORT = 9847
SIDECAR_PORT = 9848  # local sidecar for Studio "Apply" (delete-cut → re-render)

# --- Multi-project ---
# A "project" is a named workspace under src/data/<name>/ holding all pipeline
# intermediates for one edit. input/ and output/ stay shared at the repo root.
# The active project is resolved at import time (path constants depend on it):
#   VE_PROJECT env var  >  .ve_active_project state file  >  "default".
# run_all.py / project.py set VE_PROJECT before spawning steps; subprocess steps
# inherit it. Direct step invocations fall back to the state file.
STATE_FILE = REPO_ROOT / ".ve_active_project"


def sanitize_project_name(stem: str) -> str:
    """Reduce an arbitrary video stem to a valid Remotion composition id
    (^[a-zA-Z0-9-]+$). Used for both the data/<name>/ folder and the id so they
    always match. Falls back to 'default' if nothing survives."""
    s = re.sub(r"[^a-zA-Z0-9-]+", "-", stem)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "default"


def first_input_video_stem() -> Optional[str]:
    """First-alphabetical stem of input/*.{mp4,mov}. This is the historical
    default project: the pipeline merges all input videos and names the project
    after the first one. Returns None if input/ has no videos."""
    if not INPUT_DIR.exists():
        return None
    vids = sorted(
        [*INPUT_DIR.glob("*.mp4"), *INPUT_DIR.glob("*.mov")],
        key=lambda p: p.name.lower(),
    )
    return vids[0].stem if vids else None


def resolve_active_project() -> Optional[str]:
    """VE_PROJECT env > .ve_active_project state file > first input video.
    Returns None only when none of those exist (input/ is empty too).
    Management tools (project.py) set VE_ALLOW_NO_PROJECT=1 to skip the
    input-video fallback so merely listing projects can't spawn a workspace."""
    name = os.environ.get("VE_PROJECT")
    if name and name.strip():
        return sanitize_project_name(name.strip())
    if STATE_FILE.exists():
        n = STATE_FILE.read_text(encoding="utf-8").strip()
        if n:
            return sanitize_project_name(n)
    if os.environ.get("VE_ALLOW_NO_PROJECT") == "1":
        return None
    stem = first_input_video_stem()
    if stem:
        return sanitize_project_name(stem)
    return None


ACTIVE_PROJECT = resolve_active_project()

# --- Video target ---
# Resolution presets for the two pipeline modes.
REEL_W, REEL_H = 1080, 1920    # vertical (Reels/Shorts)
YT_W, YT_H = 1920, 1080        # landscape (YouTube)
VIDEO_FPS = 30

# Backwards-compat aliases. Old code paths (4b_place_images.py, 5_motion_graphics.py)
# still import VIDEO_W_PX/VIDEO_H_PX directly. New code should call get_mode() instead.
VIDEO_W_PX = REEL_W
VIDEO_H_PX = REEL_H

# --- ffmpeg encoding fragments ---
FFMPEG_X264_FAST_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
FFMPEG_AAC_STEREO_ARGS = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]

# --- Image overlays ---
IMAGES_DIR = INPUT_DIR / "images"
IMAGE_WIDTH_FRAC = 0.35

# Pipeline steps need an active project. Tools that legitimately run without one
# (project.py list/switch/...) set VE_ALLOW_NO_PROJECT=1 before importing config.
if ACTIVE_PROJECT is None:
    if os.environ.get("VE_ALLOW_NO_PROJECT") == "1":
        OUT_DIR = None
        MODE_PATH = None
    else:
        raise SystemExit(
            "ERROR: no active project (input/ has no videos and none is selected).\n"
            "  Add a video to input/ and run:  python3 run_all.py\n"
            "  Or target one explicitly:       python3 run_all.py --input <video> | --project <name>\n"
            "  Or select an existing one:      python3 project.py switch <name>"
        )
else:
    OUT_DIR = DATA_ROOT / ACTIVE_PROJECT
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MODE_PATH = OUT_DIR / "mode.json"


def get_mode() -> dict:
    """Read pipeline mode written by step 1. Returns a dict with keys:
    mode ('reel'|'youtube'), width, height, fps, subtitles (bool)."""
    if not MODE_PATH.exists():
        raise SystemExit(
            "ERROR: data/mode.json not found. Run 1_normalize.py first."
        )
    return json.loads(MODE_PATH.read_text())


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def call_claude(prompt: str, extra_args: Optional[list] = None, timeout: int = 90) -> Optional[dict]:
    """Run Claude CLI, strip markdown fences, parse JSON. Returns parsed dict or None on failure."""
    cmd = ["claude", "-p", prompt] + (extra_args or [])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        print(f"  WARNING: claude CLI error: {result.stderr[:200]}")
        return None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.stdout.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARNING: could not parse JSON response: {e}")
        print(f"  Raw: {raw[:300]}")
        return None


def seconds_to_frame(seconds: float, fps: int = VIDEO_FPS) -> int:
    return round(seconds * fps)


def frames_to_ms(frames: int, fps: int = VIDEO_FPS) -> int:
    return round(frames * 1000 / fps)


def ms_to_s(ms: int) -> float:
    return ms / 1000.0
