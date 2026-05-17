import json
import subprocess
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
SRC_DIR = PIPELINE_DIR.parent
OUT_DIR = SRC_DIR / "data"
INPUT_DIR = SRC_DIR.parent / "input"
OUTPUT_DIR = SRC_DIR.parent / "output"
REMOTION_DIR = SRC_DIR / "remotion"
HYPERFRAMES_PORT = 9847

# --- Video target ---
VIDEO_W_PX = 1080
VIDEO_H_PX = 1920
VIDEO_FPS = 30

# --- ffmpeg encoding fragments ---
FFMPEG_X264_FAST_ARGS = ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
FFMPEG_AAC_STEREO_ARGS = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]

# --- Image overlays ---
IMAGES_DIR = INPUT_DIR / "images"
IMAGE_WIDTH_FRAC = 0.35

OUT_DIR.mkdir(exist_ok=True)


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
