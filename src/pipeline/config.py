import json
import re
import subprocess
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).parent
SRC_DIR = PIPELINE_DIR.parent
OUT_DIR = SRC_DIR / "data"
INPUT_DIR = SRC_DIR.parent / "input"
OUTPUT_DIR = SRC_DIR.parent / "output"
REMOTION_DIR = SRC_DIR / "remotion"
HYPERFRAMES_PORT = 9847

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

OUT_DIR.mkdir(exist_ok=True)

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
