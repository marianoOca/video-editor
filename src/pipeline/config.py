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
#   VE_PROJECT env var  >  .ve_active_project state file  >  first input video.
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


VIDEO_EXTS = (".mp4", ".mov")


def list_videos(directory: Path) -> list[Path]:
    """Video files in `directory`, extension-matched case-insensitively (iPhone
    files arrive as .MOV/.MP4; a POSIX glob would miss them), sorted by
    lowercased name so "first video" means the same file everywhere it matters:
    project naming, merge order, and mode detection."""
    if not directory.exists():
        return []
    return sorted((p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
                  key=lambda p: p.name.lower())


def first_input_video_stem() -> Optional[str]:
    """First-alphabetical stem of the input/ videos. This is the historical
    default project: the pipeline merges all input videos and names the project
    after the first one. Returns None if input/ has no videos."""
    vids = list_videos(INPUT_DIR)
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
FFMPEG_AAC_STEREO_ARGS = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]

# whisper.cpp accepts ONLY 16 kHz mono WAV — a format requirement, not a knob.
WHISPER_SAMPLE_RATE = 16000

# Voice-band pre-filter (~80-3000 Hz) applied before ANY loudness measurement, so
# low rumble (AC/fridge/mic handling) and high hiss don't read as speech energy.
# Shared by silencedetect and the step-1 noise-floor measurement — both MUST see
# the same signal or their dB values aren't comparable.
VOICE_BANDPASS = "highpass=f=80,lowpass=f=3000"

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


def run_ffmpeg(cmd: list, *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run an ffmpeg (or other) command, surfacing stderr on failure.

    Captures stderr (stdout discarded by default) so a failing command prints the
    real ffmpeg error tail instead of dying with a bare CalledProcessError traceback.
    Replaces scattered subprocess.run(..., stderr=subprocess.DEVNULL) calls that hid
    diagnostics. Pass check=False to inspect the returncode without raising."""
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, **kwargs)
    if check and proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        print(f"  ERROR: {cmd[0]} failed (exit {proc.returncode}):\n{tail}")
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=proc.stderr)
    return proc


def silencedetect(path: Path, noise_db: float, min_dur: float,
                  total_duration: Optional[float] = None) -> list:
    """Run ffmpeg silencedetect on `path`; return [(start, end), ...] pauses (seconds).

    Shared by step 2 (chunk split points) and step 3 (keep-block edge snapping). If
    the file ends mid-silence ffmpeg emits a trailing silence_start with no matching
    silence_end; when total_duration is given that unpaired start is closed at
    total_duration instead of being silently dropped by zip()."""
    # Detection-only: the audio/video is untouched, VOICE_BANDPASS just sharpens
    # the pause-vs-speech call.
    proc = run_ffmpeg(
        ["ffmpeg", "-i", str(path),
         "-af", f"{VOICE_BANDPASS},silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"]
    )
    out = proc.stderr or ""
    # ffmpeg emits negative starts at the stream head ("silence_start: -0.00232")
    # and can use exponent notation for tiny values. A pattern that misses one
    # start shifts the zip pairing and corrupts EVERY interval after it.
    num = r"(-?[\d.]+(?:[eE][+-]?\d+)?)"
    starts = [max(0.0, float(m)) for m in re.findall(rf"silence_start: {num}", out)]
    ends = [max(0.0, float(m)) for m in re.findall(rf"silence_end: {num}", out)]
    pairs = list(zip(starts, ends))
    if len(starts) > len(ends) and total_duration is not None:
        pairs.append((starts[len(ends)], total_duration))
    return pairs


def call_claude(prompt: str, extra_args: Optional[list] = None, timeout: Optional[int] = None) -> Optional[dict]:
    """Run Claude CLI, strip markdown fences, parse JSON. Returns parsed dict or None on failure.
    timeout defaults to None (no limit): analysis scales with transcript length and a
    half-hour video must not be killed mid-call. Pass an int only for a deliberate cap."""
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


# --- Per-project manifest (source inputs + rendered outputs) ---
# manifest.json is the single source of truth mapping a project to the SHARED
# input/ and output/ folders. It records every source video the project was built
# from (inputs) and every file it rendered (outputs). Paths INSIDE the project
# folder are stored RELATIVE to REPO_ROOT so the whole folder can be moved/renamed
# without breaking them; genuinely external sources (an --import-path reference
# outside the folder) stay absolute. read_manifest rehydrates every entry back to
# an absolute path against the current folder location, so consumers are unchanged.
# Step 1 writes inputs at (re)creation; steps 4/5 append outputs; the delete and
# re-run-pipeline features read it. Mandatory: every project must have one — its
# absence means a project was created outside the pipeline (a real bug), so
# read_manifest raises rather than degrading to a guess.

def manifest_path(project: Optional[str] = None) -> Path:
    """Path to a project's manifest.json (defaults to the active project)."""
    name = project or ACTIVE_PROJECT
    return DATA_ROOT / name / "manifest.json"


def _store_path(p) -> str:
    """Serialize a path for the manifest. A path inside the project folder is stored
    RELATIVE to REPO_ROOT so it survives moving the whole folder; a genuinely external
    source (outside the folder, e.g. an --import-path reference) stays absolute."""
    ap = Path(p).resolve()
    try:
        return str(ap.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(ap)


def _load_path(s: str) -> Path:
    """Rehydrate a stored manifest path against the CURRENT folder location. A relative
    entry rejoins REPO_ROOT. A stale absolute entry from a previous location (written
    before this scheme, or by a copy at a different path) self-heals to
    INPUT_DIR/<basename> when that file exists, so old manifests keep working post-move."""
    p = Path(s)
    if not p.is_absolute():
        return REPO_ROOT / p
    if not p.exists() and (INPUT_DIR / p.name).exists():
        return INPUT_DIR / p.name
    return p


def read_manifest(project: Optional[str] = None) -> dict:
    """Read a project's manifest, with inputs/outputs always present as lists.
    Raises FileNotFoundError if the manifest is missing (hard invariant)."""
    p = manifest_path(project)
    if not p.exists():
        raise FileNotFoundError(
            f"manifest.json missing for project '{project or ACTIVE_PROJECT}': {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["inputs"] = [str(_load_path(s)) for s in data.get("inputs", [])]
    data["outputs"] = [str(_load_path(s)) for s in data.get("outputs", [])]
    return data


def write_manifest_inputs(paths: list, project: Optional[str] = None) -> None:
    """Write a fresh manifest: source inputs (absolute) recorded, outputs empty.
    Called by step 1, so a new source set resets the record for a re-run."""
    p = manifest_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"inputs": [_store_path(x) for x in paths], "outputs": []}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_manifest_output(path: Path, project: Optional[str] = None) -> None:
    """Append a rendered output path (absolute, deduped) to the manifest. Called by
    step 4 (--render) and step 5. Tolerates a not-yet-written manifest."""
    p = manifest_path(project)
    try:
        data = read_manifest(project)
    except FileNotFoundError:
        data = {"inputs": [], "outputs": []}
    abs_path = str(Path(path).resolve())
    if abs_path not in data["outputs"]:
        data["outputs"].append(abs_path)
    # Re-serialize to storage form (relative-inside-folder), which also opportunistically
    # migrates any legacy absolute inputs read above to the move-safe scheme.
    payload = {
        "inputs": [_store_path(x) for x in data["inputs"]],
        "outputs": [_store_path(x) for x in data["outputs"]],
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def seconds_to_frame(seconds: float, fps: int = VIDEO_FPS) -> int:
    return round(seconds * fps)


def frames_to_ms(frames: int, fps: int = VIDEO_FPS) -> int:
    return round(frames * 1000 / fps)


def ms_to_s(ms: int) -> float:
    return ms / 1000.0
