"""
Step 1: Detect orientation, normalize all input videos to the target resolution
at 30fps, and concatenate them.

Auto-detects mode from the first video in input/:
  - W > H → youtube mode (1920x1080, subtitles OFF)
  - W < H → reel mode    (1080x1920, subtitles ON)

Mismatched orientation videos are letterboxed to the target.

Single video: normalized directly to combined.mp4 (no concat needed).
Multiple videos: each normalized, then concatenated (always re-encoded for
guaranteed A/V sync at seams).

Usage:
  python3 1_normalize.py                 # auto-detect
  python3 1_normalize.py --mode reel     # force reel
  python3 1_normalize.py --mode youtube  # force youtube

Output:
  data/combined.mp4    concatenated video
  data/clip_map.json   per-clip offsets
  data/mode.json       {mode, width, height, fps, subtitles}
"""

import subprocess
import json
import sys
import argparse
from pathlib import Path
from config import (
    INPUT_DIR, OUT_DIR, MODE_PATH, probe, run_ffmpeg, list_videos,
    REEL_W, REEL_H, YT_W, YT_H, VIDEO_FPS,
    FFMPEG_X264_FAST_ARGS, FFMPEG_AAC_STEREO_ARGS,
)


def get_rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return int(sd["rotation"])
    return 0


def effective_dims(data: dict) -> tuple[int, int]:
    """Return (width, height) accounting for rotation metadata, from probe() output."""
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    w, h = int(vs["width"]), int(vs["height"])
    if abs(get_rotation(vs)) % 180 == 90:
        w, h = h, w
    return w, h


def detect_mode(data: dict) -> str:
    w, h = effective_dims(data)
    if w == h:
        raise SystemExit(
            "ERROR: square video not supported. Use --mode reel|youtube to force a mode."
        )
    return "youtube" if w > h else "reel"


def mode_config(mode: str) -> dict:
    if mode == "reel":
        return {"mode": "reel", "width": REEL_W, "height": REEL_H,
                "fps": VIDEO_FPS, "subtitles": True}
    if mode == "youtube":
        return {"mode": "youtube", "width": YT_W, "height": YT_H,
                "fps": VIDEO_FPS, "subtitles": False}
    raise SystemExit(f"ERROR: unknown mode '{mode}'. Use 'reel' or 'youtube'.")


def normalize(video: Path, out: Path, target_w: int, target_h: int, data: dict) -> float:
    """Normalize a single video to target_w x target_h. Letterboxes if aspect
    differs (so vertical clips in a youtube job, or vice versa, don't stretch).
    `data` is the precomputed probe() output (probed once per video in main).
    Returns duration in seconds."""
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    rotation = get_rotation(vs)
    duration = float(data["format"]["duration"])

    # ffmpeg auto-rotates via the decoder when rotation metadata is present,
    # so we never need a transpose filter — only scale + pad to the target.
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(video),
        "-vf", vf,
        "-r", str(VIDEO_FPS),
        *FFMPEG_X264_FAST_ARGS,
        *FFMPEG_AAC_STEREO_ARGS,
        str(out)
    ]
    print(f"  normalizing {video.name} (rotation={rotation}, {duration:.0f}s)...")
    subprocess.run(cmd, check=True)
    return duration


def concatenate(norm_files: list[Path], out: Path):
    """Concatenate normalized clips using the concat demuxer."""
    list_file = OUT_DIR / "concat_list.txt"
    with open(list_file, "w") as f:
        for p in norm_files:
            # The concat demuxer requires apostrophes inside a quoted path to be
            # escaped as '\'' — an unescaped one ("Mariano's Files") misparses and
            # kills the run after all the slow per-clip re-encodes.
            quoted = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{quoted}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out)
    ]
    print("  concatenating clips...")
    run_ffmpeg(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["reel", "youtube"], default=None,
                        help="Force mode (default: auto-detect from first video)")
    parser.add_argument("--input", nargs="+", default=None,
                        help="Process only these file(s), merged in the given order. Each is an "
                             "absolute path, relative to cwd, or a bare name resolved in input/.")
    args = parser.parse_args()

    if args.input:
        # Resolve each input in order so the merge order matches the selection.
        videos = []
        for name in args.input:
            p = Path(name)
            if not p.is_absolute():
                # Try relative to cwd first, then fall back to input/ so a bare
                # filename (e.g. --input prueba1.mp4) resolves to input/prueba1.mp4.
                cwd_p = Path.cwd() / p
                input_p = INPUT_DIR / p
                p = cwd_p if cwd_p.exists() else input_p
            if not p.exists():
                print(f"ERROR: --input file not found: {p}")
                sys.exit(1)
            videos.append(p)
    else:
        videos = list_videos(INPUT_DIR)
    if not videos:
        print("ERROR: no videos found in input/")
        sys.exit(1)

    print(f"Found {len(videos)} videos: {[v.name for v in videos]}")

    # Probe each video once; reuse for mode detection, mismatch warnings, and
    # normalize (avoids 2x ffprobe per clip).
    probes = {v: probe(v) for v in videos}

    if args.mode:
        mode = args.mode
        print(f"  mode forced via flag: {mode}")
    else:
        mode = detect_mode(probes[videos[0]])
        print(f"  mode auto-detected from {videos[0].name}: {mode}")

    cfg = mode_config(mode)
    target_w, target_h = cfg["width"], cfg["height"]

    combined = OUT_DIR / "combined.mp4"

    if len(videos) == 1:
        # Single video: normalize directly to combined.mp4 — no concat needed
        video = videos[0]
        duration = normalize(video, combined, target_w, target_h, probes[video])
        clip_map = [{
            "name": video.name,
            "norm_path": str(combined),
            "start_sec": 0.0,
            "duration_sec": duration,
        }]
    else:
        # Multiple videos: warn about orientation mismatches, always re-encode
        # each clip (guarantees uniform codec params for clean A/V sync at seams)
        primary_landscape = target_w > target_h
        for v in videos[1:]:
            w, h = effective_dims(probes[v])
            if (w > h) != primary_landscape:
                print(f"  ⚠️  {v.name} orientation differs from target — will be letterboxed")

        norm_files = []
        clip_map = []
        cursor = 0.0

        for video in videos:
            norm_path = OUT_DIR / f"norm_{video.name}"
            duration = normalize(video, norm_path, target_w, target_h, probes[video])
            clip_map.append({
                "name": video.name,
                "norm_path": str(norm_path),
                "start_sec": cursor,
                "duration_sec": duration,
            })
            cursor += duration
            norm_files.append(norm_path)

        concatenate(norm_files, combined)

    clip_map_path = OUT_DIR / "clip_map.json"
    with open(clip_map_path, "w") as f:
        json.dump(clip_map, f, indent=2)

    with open(MODE_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    subs_label = "subtitles ON" if cfg["subtitles"] else "subtitles OFF"
    print(f"\n✅ Done.")
    print(f"   Mode: {mode} ({target_w}×{target_h}, {subs_label})")
    print(f"   combined.mp4  → {combined}")
    print(f"   clip_map.json → {clip_map_path}")
    print(f"   mode.json     → {MODE_PATH}")
    total = sum(c["duration_sec"] for c in clip_map)
    print(f"   Total duration: {total:.1f}s ({total/60:.1f} min)")


if __name__ == "__main__":
    main()
