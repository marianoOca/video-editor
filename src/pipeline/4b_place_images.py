"""
Step 4b: Place images over the edited video.

For each image in input/images/:
  1. Claude reads transcript and decides when/how long to show the image.
  2. ffmpeg extracts a keyframe at that timestamp.
  3. Claude Vision (via CLI) analyzes face/body position.
  4. Claude picks x/y placement to avoid covering the subject.

Output: data/image_plan.json
  [{ "file": "img.png", "timestamp_ms": 12400, "duration_ms": 3200, "x": 0.6, "y": 0.1 }, ...]

Usage: python3 4b_place_images.py
"""

import shutil
import subprocess
import json
import sys
import tempfile
from pathlib import Path
from config import OUT_DIR, REMOTION_DIR, IMAGES_DIR, IMAGE_WIDTH_FRAC, call_claude


def get_images() -> list[Path]:
    if not IMAGES_DIR.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    return sorted(p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in exts)


def ask_claude_timing(images: list[Path], transcript: dict) -> list[dict]:
    """Ask Claude to decide when each image appears and for how long."""
    print("  asking Claude for image timing...")

    segments_text = "\n".join(
        f"[{s['start']:.2f}s – {s['end']:.2f}s] {s['text']}"
        for s in transcript["segments"]
    )
    image_list = "\n".join(f"- {p.name}" for p in images)

    prompt = (
        "You are a professional video editor. Given this transcript and a list of images, "
        "decide when each image should appear and for how long, based on the content context.\n\n"
        f"IMAGES:\n{image_list}\n\n"
        f"TRANSCRIPT:\n{segments_text}\n\n"
        "Rules:\n"
        "- Match each image to the most relevant moment in the transcript.\n"
        "- Duration should match the relevant sentence or phrase (min 2s, max 8s).\n"
        "- timestamp_ms is the start time in milliseconds in the EDITED video timeline.\n"
        "- Reply ONLY with valid JSON, no extra text:\n"
        '{"images": [{"file": "<filename>", "timestamp_ms": <int>, "duration_ms": <int>}]}\n'
    )

    data = call_claude(prompt)
    return data.get("images", []) if data else []


def extract_keyframe(video: Path, timestamp_ms: int, out_png: Path):
    """Extract a single frame from the video at the given timestamp."""
    t = timestamp_ms / 1000.0
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t), "-i", str(video),
         "-vframes", "1", "-q:v", "2", str(out_png)],
        check=True, stderr=subprocess.DEVNULL
    )


def ask_claude_placement(frame_png: Path, image_name: str) -> tuple[float, float]:
    """Ask Claude Vision to analyze face/body position and pick safe x/y for the image overlay."""
    print(f"  analyzing frame for placement of {image_name}...")

    prompt = (
        f"This is a frame from a vertical 9:16 video. A person is speaking to camera.\n"
        f"I want to place an image overlay ({image_name}) on this frame without covering "
        f"the person's face or body.\n\n"
        f"Analyze where the face and body are, then pick the best position for the image overlay.\n"
        f"The image will be about {int(IMAGE_WIDTH_FRAC * 100)}% of the frame width, positioned absolutely.\n\n"
        f"Reply ONLY with valid JSON, no extra text:\n"
        f'{{\"x\": <0.0–1.0>, \"y\": <0.0–1.0>, \"reasoning\": \"<brief>\"}}\n'
        f"Where x=0,y=0 is top-left and x=1,y=1 is bottom-right.\n"
        f"x and y are the top-left corner of the image overlay.\n"
        f"Keep image fully inside frame (account for {int(IMAGE_WIDTH_FRAC * 100)}% width, ~40% height of a square image).\n"
    )

    data = call_claude(prompt, extra_args=["--image", str(frame_png)], timeout=60)
    if data is None:
        return 0.6, 0.05
    try:
        x = max(0.0, min(0.65, float(data["x"])))  # clamp so image stays in frame
        y = max(0.0, min(0.60, float(data["y"])))
        print(f"    → x={x:.2f}, y={y:.2f} ({data.get('reasoning', '')})")
        return x, y
    except (KeyError, ValueError) as e:
        print(f"  WARNING: could not parse placement response: {e}")
        return 0.6, 0.05


def copy_images_to_public(images: list[Path]):
    """Copy images to remotion/public/images/ so Remotion can serve them."""
    dest_dir = REMOTION_DIR / "public" / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for img in images:
        dest = dest_dir / img.name
        shutil.copy2(img, dest)
    print(f"  copied {len(images)} image(s) → remotion/public/images/")


def main():
    images = get_images()
    if not images:
        print("No images found in input/images/ — skipping step 4b.")
        # Write empty plan so downstream steps don't crash
        out_path = OUT_DIR / "image_plan.json"
        with open(out_path, "w") as f:
            json.dump([], f)
        return

    transcript_path = OUT_DIR / "transcript.json"
    if not transcript_path.exists():
        print("ERROR: data/transcript.json not found. Run 2_transcribe.py first.")
        sys.exit(1)

    edited_video = OUT_DIR / "edited.mp4"
    if not edited_video.exists():
        print("ERROR: data/edited.mp4 not found. Run 4_render.py first.")
        sys.exit(1)

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    print(f"\nStep 4b — placing {len(images)} image(s)...")

    timing_entries = ask_claude_timing(images, transcript)
    if not timing_entries:
        print("  No timing decisions returned. Skipping.")
        out_path = OUT_DIR / "image_plan.json"
        with open(out_path, "w") as f:
            json.dump([], f)
        return

    plan = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for entry in timing_entries:
            fname = entry["file"]
            timestamp_ms = int(entry["timestamp_ms"])
            duration_ms = int(entry["duration_ms"])

            img_path = IMAGES_DIR / fname
            if not img_path.exists():
                print(f"  WARNING: {fname} not found in input/images/, skipping.")
                continue

            frame_png = Path(tmpdir) / f"frame_{fname}.png"
            try:
                extract_keyframe(edited_video, timestamp_ms, frame_png)
            except subprocess.CalledProcessError:
                print(f"  WARNING: could not extract frame at {timestamp_ms}ms, using default placement.")
                plan.append({"file": fname, "timestamp_ms": timestamp_ms, "duration_ms": duration_ms, "x": 0.6, "y": 0.05})
                continue

            x, y = ask_claude_placement(frame_png, fname)
            plan.append({
                "file": fname,
                "timestamp_ms": timestamp_ms,
                "duration_ms": duration_ms,
                "x": x,
                "y": y,
            })

    copy_images_to_public(images)

    out_path = OUT_DIR / "image_plan.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    print(f"\n✅ Done.")
    print(f"   image_plan.json → {out_path}")
    for entry in plan:
        print(f"   {entry['file']} @ {entry['timestamp_ms']}ms for {entry['duration_ms']}ms  x={entry['x']:.2f} y={entry['y']:.2f}")


if __name__ == "__main__":
    main()
