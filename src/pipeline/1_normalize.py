"""
Step 1: Normalize all input videos to 1080x1920 30fps and concatenate them.
Handles rotation metadata (common in phone recordings).

Usage: python3 1_normalize.py
Output: out/combined.mp4, out/clip_map.json (frame offsets per clip)
"""

import subprocess
import json
import sys
from pathlib import Path
from config import INPUT_DIR, OUT_DIR, probe

TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30


def get_rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return int(sd["rotation"])
    return 0


def normalize(video: Path, out: Path) -> float:
    """Normalize a single video. Returns duration in seconds."""
    data = probe(video)
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    rotation = get_rotation(vs)
    duration = float(data["format"]["duration"])

    # ffmpeg auto-rotates via decoder when rotation metadata is present.
    # We only need to scale to target dimensions — no transpose needed.
    vf = f"scale={TARGET_W}:{TARGET_H}"

    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", vf,
        "-r", str(TARGET_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(out)
    ]
    print(f"  normalizing {video.name} (rotation={rotation})...")
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
    return duration


def concatenate(norm_files: list[Path], out: Path):
    """Concatenate normalized clips using the concat demuxer."""
    list_file = OUT_DIR / "concat_list.txt"
    with open(list_file, "w") as f:
        for p in norm_files:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out)
    ]
    print("  concatenating clips...")
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)


def main():
    videos = sorted(INPUT_DIR.glob("vid*.mp4"))
    if not videos:
        print("ERROR: no videos found in input/")
        sys.exit(1)

    print(f"Found {len(videos)} videos: {[v.name for v in videos]}")

    norm_files = []
    clip_map = []   # [{name, norm_path, start_sec, duration_sec}]
    cursor = 0.0

    for video in videos:
        norm_path = OUT_DIR / f"norm_{video.name}"
        duration = normalize(video, norm_path)
        clip_map.append({
            "name": video.name,
            "norm_path": str(norm_path),
            "start_sec": cursor,
            "duration_sec": duration,
        })
        cursor += duration
        norm_files.append(norm_path)

    combined = OUT_DIR / "combined.mp4"
    concatenate(norm_files, combined)

    clip_map_path = OUT_DIR / "clip_map.json"
    with open(clip_map_path, "w") as f:
        json.dump(clip_map, f, indent=2)

    print(f"\n✅ Done.")
    print(f"   combined.mp4  → {combined}")
    print(f"   clip_map.json → {clip_map_path}")
    total = sum(c["duration_sec"] for c in clip_map)
    print(f"   Total duration: {total:.1f}s ({total/60:.1f} min)")


if __name__ == "__main__":
    main()
