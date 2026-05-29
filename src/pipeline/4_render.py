"""
Step 4: Cut the video with ffmpeg using the edit plan, then update Remotion
with subtitles and render (or open Studio for preview).

Usage:
  python3 4_render.py              # cut video + open Remotion Studio
  python3 4_render.py --render     # cut video + render final MP4
"""

import subprocess
import json
import sys
import argparse
import shutil
from pathlib import Path
from config import (
    OUT_DIR, OUTPUT_DIR, REMOTION_DIR,
    VIDEO_FPS, FFMPEG_AAC_STEREO_ARGS, get_mode,
    seconds_to_frame, frames_to_ms,
)
from captions_config import (
    MAX_WORDS_PER_CAPTION, MAX_CHARS_PER_CAPTION, MIN_CAPTION_DURATION_MS,
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
        # High profile + level 4.2 covers 1080p. Level 3.1 maxes at 720p, and
        # 1080p clips at that level are rejected by chromium/WebCodecs
        # (@remotion/media) as an unsupported format even though ffmpeg plays them.
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.2",
        "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        # Force constant 30fps. The concat filter can emit variable frame timing
        # (avg_frame_rate != 30), which WebCodecs also rejects as a format error.
        "-r", str(VIDEO_FPS), "-vsync", "cfr",
        "-movflags", "+faststart",
        *FFMPEG_AAC_STEREO_ARGS,
        str(out)
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)


def build_subtitles(transcript: dict, keep: list[dict]) -> list[dict]:
    """
    Map transcript words to the edited timeline, then group into short caption
    lines (≤ MAX_WORDS_PER_CAPTION words, ≤ MAX_CHARS_PER_CAPTION chars).

    Words that span or fall inside a cut are discarded — no phantom fallback.
    Groups never cross keep-segment boundaries (no text from cut zone bleeds in).
    Returns list of {start (frame), end (frame), text}.
    """
    def word_to_edited(word: dict):
        """Map a word's [start, end] to the edited timeline.
        Returns (edited_start, edited_end, keep_idx) or None if the word is cut.
        Words whose start falls in a segment are accepted; end is clamped to the
        segment boundary so that WhisperX over-long word timestamps don't drop words."""
        edited_cursor = 0.0
        for idx, seg in enumerate(keep):
            if word["start"] >= seg["start"] and word["start"] < seg["end"]:
                offset = edited_cursor
                clamped_end = min(word["end"], seg["end"])
                return (
                    offset + (word["start"] - seg["start"]),
                    offset + (clamped_end - seg["start"]),
                    idx,
                )
            if word["start"] < seg["start"]:
                return None  # word is before this segment and wasn't in a previous one
            edited_cursor += seg["end"] - seg["start"]
        return None

    # Collect all words mapped to the edited timeline
    mapped_words = []
    dropped = 0
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            result = word_to_edited(w)
            if result is None:
                dropped += 1
                continue
            e_start, e_end, keep_idx = result
            mapped_words.append({
                "word": w["word"].strip(),
                "start": e_start,
                "end": e_end,
                "keep_idx": keep_idx,
            })

    if dropped:
        print(f"  subtitle mapping: dropped {dropped} word(s) that fell inside cuts")

    if not mapped_words:
        return []

    # Group into caption lines respecting word/char limits and keep-segment boundaries
    groups = []
    group_words: list[dict] = []
    group_keep_idx: int | None = None

    def flush():
        if group_words:
            groups.append({
                "start": group_words[0]["start"],
                "end": group_words[-1]["end"],
                "text": " ".join(w["word"] for w in group_words),
                "words": [{"start": w["start"], "end": w["end"]} for w in group_words],
            })

    for mw in mapped_words:
        # Reset group when crossing into a different keep-segment
        if group_keep_idx is not None and mw["keep_idx"] != group_keep_idx:
            flush()
            group_words = []

        projected = " ".join(w["word"] for w in group_words + [mw])

        if len(group_words) >= MAX_WORDS_PER_CAPTION or len(projected) > MAX_CHARS_PER_CAPTION:
            flush()
            group_words = []

        group_words.append(mw)
        group_keep_idx = mw["keep_idx"]

    flush()

    # Extend captions shorter than MIN_CAPTION_DURATION_MS (avoid flash subs)
    min_dur_sec = MIN_CAPTION_DURATION_MS / 1000.0
    for i, grp in enumerate(groups):
        if grp["end"] - grp["start"] < min_dur_sec:
            cap = groups[i + 1]["start"] if i + 1 < len(groups) else grp["end"] + min_dur_sec
            grp["end"] = min(grp["start"] + min_dur_sec, cap)

    return [
        {
            "start": seconds_to_frame(grp["start"]),
            "end": seconds_to_frame(grp["end"]),
            "text": grp["text"],
            "words": [
                {"start": seconds_to_frame(w["start"]), "end": seconds_to_frame(w["end"])}
                for w in grp["words"]
            ],
        }
        for grp in groups
    ]


def update_remotion(edited_video: Path, subtitles: list[dict],
                    duration_frames: int, mode: dict):
    """Copy edited video to Remotion public/ and rewrite Root.tsx with correct props.
    Uses mode['width']/['height'] for the composition size; if mode['subtitles']
    is False, captions are written as an empty array."""
    public_dir = REMOTION_DIR / "public"
    public_dir.mkdir(exist_ok=True)

    dest = public_dir / "edited.mp4"
    shutil.copy2(edited_video, dest)
    print(f"  copied edited video → src/remotion/public/edited.mp4")

    if mode["subtitles"]:
        captions = [
            {
                "startMs": frames_to_ms(s["start"]),
                "endMs":   frames_to_ms(s["end"]),
                "text":    s["text"].strip(),
                "words":   [
                    {"startMs": frames_to_ms(w["start"]), "endMs": frames_to_ms(w["end"])}
                    for w in (s.get("words") or [])
                ],
            }
            for s in subtitles
        ]
    else:
        captions = []
        print(f"  mode: {mode['mode']} — subtitles disabled")

    # Load image overlays if available
    image_overlays = []
    image_plan_path = OUT_DIR / "image_plan.json"
    if image_plan_path.exists():
        with open(image_plan_path, encoding="utf-8") as f:
            image_overlays = json.load(f)
        if image_overlays:
            print(f"  injected {len(image_overlays)} image overlay(s) into Root.tsx defaultProps")

    # Load title cards if available (sidecar — survives pipeline re-runs).
    # Schema per card: {title, subtitle, startMs, durationMs}.
    title_cards = []
    title_cards_path = OUT_DIR / "title_cards.json"
    if title_cards_path.exists():
        with open(title_cards_path, encoding="utf-8") as f:
            title_cards = json.load(f)
        # Normalize: ensure every card has titleHighlight (defaults to "").
        # Required by the zod schema's resolved type that Remotion validates against.
        for card in title_cards:
            card.setdefault("titleHighlight", "")
            card.setdefault("subtitle", "")
        if title_cards:
            print(f"  injected {len(title_cards)} title card(s) into Root.tsx defaultProps")

    # Build defaultProps as plain dict → serialize to JSON → embed in JSX
    default_props = {
        "videoSrc": "edited.mp4",
        "captions": captions,
        "imageOverlays": image_overlays,
        "titleCards": title_cards,
    }
    props_json = json.dumps(default_props, ensure_ascii=False)

    width, height, fps = mode["width"], mode["height"], mode["fps"]

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
      fps={{{fps}}}
      width={{{width}}}
      height={{{height}}}
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

    print(f"  updated Root.tsx ({duration_frames} frames, {width}×{height}) "
          f"and subtitles.json ({len(subtitles)} entries)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true", help="Render final MP4 instead of opening Studio")
    args = parser.parse_args()

    edit_plan_path = OUT_DIR / "edit_plan.json"
    if not edit_plan_path.exists():
        print("ERROR: src/data/edit_plan.json not found. Run 3_analyze.py first.")
        sys.exit(1)

    mode = get_mode()

    with open(edit_plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    keep = plan["keep"]
    combined = OUT_DIR / "combined.mp4"
    edited = OUT_DIR / "edited.mp4"

    cut_video(combined, keep, edited)

    final_duration = plan["final_duration"]
    duration_frames = round(final_duration * VIDEO_FPS)
    subtitles = build_subtitles(plan["transcript"], keep) if mode["subtitles"] else []

    update_remotion(edited, subtitles, duration_frames, mode)

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
