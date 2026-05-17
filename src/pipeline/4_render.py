"""
Step 4: Cut the video with ffmpeg using the edit plan, then update Remotion
with subtitles and render (or open Studio for preview).

Usage:
  python3 4_render.py              # cut video + open Remotion Studio
  python3 4_render.py --render     # cut video + render final MP4
"""

import re
import subprocess
import json
import sys
import argparse
import shutil
from pathlib import Path
from config import (
    OUT_DIR, OUTPUT_DIR, REMOTION_DIR,
    VIDEO_FPS, FFMPEG_AAC_STEREO_ARGS,
)


def cut_video(combined: Path, keep: list[dict], out: Path):
    """Use ffmpeg complex filter to cut and concatenate kept segments."""
    print(f"  cutting {len(keep)} segments...")

    inputs = []
    filter_parts = []
    for i, seg in enumerate(keep):
        start = seg["start"]
        duration = seg["end"] - seg["start"]
        inputs += ["-ss", str(start), "-t", str(duration), "-i", str(combined)]
        filter_parts.append(f"[{i}:v][{i}:a]")

    filter_complex = "".join(filter_parts) + f"concat=n={len(keep)}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.1",
        "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        *FFMPEG_AAC_STEREO_ARGS,
        str(out)
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)


def seconds_to_frame(seconds: float, fps: int = VIDEO_FPS) -> int:
    return int(seconds * fps)


def build_subtitles(transcript: dict, keep: list[dict]) -> list[dict]:
    """
    Map transcript segments to the edited timeline.
    Segments that fall inside a cut are removed.
    Segments that span cuts are trimmed.
    Returns list of {start (frame), end (frame), text}.
    """
    def original_to_edited(t: float):
        edited_cursor = 0.0
        for seg in keep:
            if seg["start"] <= t <= seg["end"]:
                return edited_cursor + (t - seg["start"])
            if t < seg["start"]:
                return None  # t was cut
            edited_cursor += seg["end"] - seg["start"]
        return None

    subtitles = []
    for seg in transcript["segments"]:
        edited_start = original_to_edited(seg["start"])
        edited_end = original_to_edited(seg["end"])

        if edited_start is None and edited_end is None:
            continue

        if edited_start is None:
            edited_start = edited_end or 0.0
        if edited_end is None:
            edited_end = edited_start + 0.5

        subtitles.append({
            "start": seconds_to_frame(edited_start),
            "end": seconds_to_frame(edited_end),
            "text": seg["text"],
        })

    return subtitles


def frames_to_ms(frames: int, fps: int = VIDEO_FPS) -> int:
    return int(frames * 1000 / fps)


def update_remotion(edited_video: Path, subtitles: list[dict], duration_frames: int):
    """Copy edited video to Remotion public/ and rewrite Root.tsx with correct props."""
    public_dir = REMOTION_DIR / "public"
    public_dir.mkdir(exist_ok=True)

    dest = public_dir / "edited.mp4"
    shutil.copy2(edited_video, dest)
    print(f"  copied edited video → src/remotion/public/edited.mp4")

    # Convert subtitles from frames to ms (format expected by CaptionOverlay)
    captions = [
        {
            "startMs": frames_to_ms(s["start"]),
            "endMs":   frames_to_ms(s["end"]),
            "text":    s["text"].strip(),
        }
        for s in subtitles
    ]

    # Load image overlays if available
    image_overlays = []
    image_plan_path = OUT_DIR / "image_plan.json"
    if image_plan_path.exists():
        with open(image_plan_path, encoding="utf-8") as f:
            image_overlays = json.load(f)
        if image_overlays:
            print(f"  injected {len(image_overlays)} image overlay(s) into Root.tsx defaultProps")

    # Build defaultProps as plain dict → serialize to JSON → embed in JSX
    default_props = {
        "videoSrc": "edited.mp4",
        "captions": captions,
        "imageOverlays": image_overlays,
    }
    props_json = json.dumps(default_props, ensure_ascii=False)

    # Rewrite Root.tsx from scratch (avoids fragile regex over JSX)
    root_tsx = f"""import {{ Composition }} from "remotion";
import {{ VideoComposition }} from "./Composition";
import {{ compositionSchema }} from "./schema";

export const RemotionRoot: React.FC = () => {{
  return (
    <Composition
      id="VideoEditor"
      component={{VideoComposition}}
      durationInFrames={{{duration_frames}}}
      fps={{30}}
      width={{608}}
      height={{1080}}
      schema={{compositionSchema}}
      defaultProps={{{props_json}}}
    />
  );
}};
"""
    root_path = REMOTION_DIR / "src" / "Root.tsx"
    root_path.write_text(root_tsx, encoding="utf-8")

    # Also write subtitles.json (legacy static import, kept for compatibility)
    subs_path = REMOTION_DIR / "src" / "subtitles.json"
    with open(subs_path, "w", encoding="utf-8") as f:
        json.dump(subtitles, f, indent=2, ensure_ascii=False)

    print(f"  updated Root.tsx ({duration_frames} frames) and subtitles.json ({len(subtitles)} entries)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true", help="Render final MP4 instead of opening Studio")
    args = parser.parse_args()

    edit_plan_path = OUT_DIR / "edit_plan.json"
    if not edit_plan_path.exists():
        print("ERROR: src/data/edit_plan.json not found. Run 3_analyze.py first.")
        sys.exit(1)

    with open(edit_plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    keep = plan["keep"]
    combined = OUT_DIR / "combined.mp4"
    edited = OUT_DIR / "edited.mp4"

    cut_video(combined, keep, edited)

    final_duration = plan["final_duration"]
    duration_frames = int(final_duration * VIDEO_FPS)
    subtitles = build_subtitles(plan["transcript"], keep)

    update_remotion(edited, subtitles, duration_frames)

    print(f"\n✅ Edited video ready: {edited}")
    print(f"   Duration: {final_duration:.1f}s | {duration_frames} frames")
    print(f"   Subtitles: {len(subtitles)} entries")

    if args.render:
        print("\n  rendering final video with Remotion...")
        OUTPUT_DIR.mkdir(exist_ok=True)
        final_out = OUTPUT_DIR / "final.mp4"
        subprocess.run(
            ["npx", "remotion", "render", "VideoEditor", str(final_out)],
            cwd=REMOTION_DIR, check=True
        )
        print(f"\n🎬 Final video: {final_out}")
    else:
        print("\n  opening Remotion Studio for preview...")
        print("  (Ctrl+C to stop, then run with --render to export)")
        subprocess.run(["npm", "run", "dev"], cwd=REMOTION_DIR)


if __name__ == "__main__":
    main()
