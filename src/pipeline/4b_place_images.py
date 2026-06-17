"""
Step 4b: Place images over the edited video.

For each image in input/images/:
  1. Claude reads subtitles (edited timeline) and decides when/how long to show each image.
  2. ffmpeg extracts a keyframe at that timestamp.
  3. Claude Vision (via CLI @filepath) analyzes pointing direction.
  4. Claude picks x/y placement where the hand is pointing.

Output: data/image_plan.json
  [{ "file": "img.png", "timestamp_ms": 12400, "duration_ms": 3200, "x": 0.6, "y": 0.1 }, ...]

Usage: python3 4b_place_images.py
"""

import re
import subprocess
import json
import sys
from pathlib import Path
from config import OUT_DIR, IMAGES_DIR, IMAGE_WIDTH_FRAC, ACTIVE_PROJECT, call_claude, get_mode
from remotion_sync import read_snapshot, attach_images, regenerate_root


def get_images() -> list[Path]:
    if not IMAGES_DIR.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    return sorted(p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in exts)


def ask_claude_timing(images: list[Path], subtitles: list[dict], video_duration_ms: int) -> list[dict]:
    """Ask Claude to decide when each image appears, using the edited-timeline subtitles."""
    print("  asking Claude for image timing...")

    segments_text = "\n".join(
        f"[{s['startMs']}ms – {s['endMs']}ms] {s['text']}"
        for s in subtitles
    )
    image_list = "\n".join(f"- {p.name}" for p in images)

    prompt = (
        "You are a professional video editor. Given these subtitles and a list of product logo images, "
        "decide when each image should appear and for how long.\n\n"
        "Context: the speaker is comparing products (e.g. Claude Code, Claude co-work, Claude chat). "
        "They point with their hand at each product as they mention it. "
        "Match each logo to the exact moment the speaker introduces or mentions that product.\n\n"
        f"IMAGES:\n{image_list}\n\n"
        f"SUBTITLES (timestamps are in the EDITED video timeline, milliseconds):\n{segments_text}\n\n"
        f"TOTAL EDITED VIDEO DURATION: {video_duration_ms}ms\n\n"
        "Rules:\n"
        "- Match each image to the subtitle entry where that product is mentioned.\n"
        "- Use the subtitle startMs as the timestamp_ms.\n"
        "- Duration should match the relevant phrase (min 2000ms, max 8000ms).\n"
        "- timestamp_ms MUST be less than the total video duration.\n"
        "- Reply ONLY with valid JSON, no extra text:\n"
        '{"images": [{"file": "<filename>", "timestamp_ms": <int>, "duration_ms": <int>}]}\n'
    )

    data = call_claude(prompt)
    return data.get("images", []) if data else []


def extract_keyframe(video: Path, timestamp_ms: int, out_png: Path):
    """Extract a single frame from the video at the given timestamp."""
    t = timestamp_ms / 1000.0
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t), "-i", str(video),
         "-vframes", "1", "-q:v", "2", str(out_png)],
        capture_output=True
    )
    if result.returncode != 0 or not out_png.exists():
        raise RuntimeError(f"ffmpeg failed or produced no output at {timestamp_ms}ms")


def ask_claude_placement(frame_png: Path, image_name: str) -> tuple[float, float]:
    """Use Claude Vision via CLI @filepath to detect pointing direction and pick x/y."""
    print(f"  analyzing frame for placement of {image_name}...")

    prompt = (
        f"This is a frame from a vertical 9:16 video. A person is speaking to camera and pointing "
        f"with their hand to indicate a product logo.\n\n"
        f"I want to place an image overlay ({image_name}) on this frame.\n\n"
        f"PRIMARY RULE: If the person's hand/finger is pointing in a specific direction, "
        f"place the image overlay NEAR where their hand is pointing — in the area their finger aims at. "
        f"The logo should appear in that open space, close to the tip of their pointed gesture.\n\n"
        f"FALLBACK: If no clear pointing gesture is visible, place the image without covering the face or body.\n\n"
        f"The image will be about {int(IMAGE_WIDTH_FRAC * 100)}% of the frame width, positioned absolutely.\n\n"
        f"Reply ONLY with valid JSON, no extra text:\n"
        f'{{\"x\": <0.0-1.0>, \"y\": <0.0-1.0>, \"reasoning\": \"<brief>\"}}\n'
        f"Where x=0,y=0 is top-left and x=1,y=1 is bottom-right.\n"
        f"x and y are the top-left corner of the image overlay.\n"
        f"Keep image fully inside frame (account for {int(IMAGE_WIDTH_FRAC * 100)}% width, ~40% height of a square image).\n\n"
        f"@{frame_png}"
    )

    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"  WARNING: claude CLI error: {result.stderr[:200]}")
        return 0.6, 0.05

    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.stdout.strip())
    try:
        data = json.loads(raw)
        x = max(0.0, min(0.65, float(data["x"])))
        y = max(0.0, min(0.60, float(data["y"])))
        print(f"    → x={x:.2f}, y={y:.2f} ({data.get('reasoning', '')})")
        return x, y
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  WARNING: could not parse placement response: {e}")
        print(f"  Raw: {raw[:300]}")
        return 0.6, 0.05


def main():
    out_path = OUT_DIR / "image_plan.json"

    # Image overlays are reel-only — youtube videos stay clean.
    if get_mode()["mode"] == "youtube":
        print("youtube mode — skipping step 4b (image overlays are reel-only).")
        with open(out_path, "w") as f:
            json.dump([], f)
        return

    images = get_images()
    if not images:
        print("No images found in input/images/ — skipping step 4b.")
        with open(out_path, "w") as f:
            json.dump([], f)
        # Clear any stale overlays from a previous run of this project.
        if read_snapshot(ACTIVE_PROJECT) is not None:
            attach_images(ACTIVE_PROJECT, [], [])
            regenerate_root()
        return

    snapshot = read_snapshot(ACTIVE_PROJECT)
    if snapshot is None:
        print(f"ERROR: Remotion snapshot for project '{ACTIVE_PROJECT}' not found. Run 4_render.py first.")
        sys.exit(1)

    edited_video = OUT_DIR / "edited.mp4"
    if not edited_video.exists():
        print("ERROR: data/edited.mp4 not found. Run 4_render.py first.")
        sys.exit(1)

    subtitles = snapshot.get("captions", [])
    video_duration_ms = int(subtitles[-1]["endMs"]) if subtitles else 0

    print(f"\nStep 4b — placing {len(images)} image(s)...")

    timing_entries = ask_claude_timing(images, subtitles, video_duration_ms)
    if not timing_entries:
        print("  No timing decisions returned. Skipping.")
        out_path = OUT_DIR / "image_plan.json"
        with open(out_path, "w") as f:
            json.dump([], f)
        # Clear any stale overlays from a previous run of this project.
        attach_images(ACTIVE_PROJECT, [], [])
        regenerate_root()
        return

    frames_dir = OUT_DIR / "frames"
    frames_dir.mkdir(exist_ok=True)

    plan = []
    for entry in timing_entries:
        fname = entry["file"]
        timestamp_ms = int(entry["timestamp_ms"])
        duration_ms = int(entry["duration_ms"])

        # Clamp to video duration
        timestamp_ms = min(timestamp_ms, max(0, video_duration_ms - duration_ms))

        img_path = IMAGES_DIR / fname
        if not img_path.exists():
            print(f"  WARNING: {fname} not found in input/images/, skipping.")
            continue

        frame_png = frames_dir / f"frame_{Path(fname).stem}.png"
        try:
            extract_keyframe(edited_video, timestamp_ms, frame_png)
        except RuntimeError as e:
            print(f"  WARNING: {e}, using default placement.")
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

    out_path = OUT_DIR / "image_plan.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)

    # Copy the placed images into public/projects/<name>/images/ and record the
    # overlay plan in the project snapshot, then regenerate Root.tsx.
    placed_files = [IMAGES_DIR / e["file"] for e in plan if (IMAGES_DIR / e["file"]).exists()]
    attach_images(ACTIVE_PROJECT, plan, placed_files)
    regenerate_root()
    print(f"  attached {len(placed_files)} image(s) to project '{ACTIVE_PROJECT}'")

    print(f"\n✅ Done.")
    print(f"   image_plan.json → {out_path}")
    for entry in plan:
        print(f"   {entry['file']} @ {entry['timestamp_ms']}ms for {entry['duration_ms']}ms  x={entry['x']:.2f} y={entry['y']:.2f}")


if __name__ == "__main__":
    main()
