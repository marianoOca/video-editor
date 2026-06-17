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
import time
import argparse
from pathlib import Path
from config import (
    OUT_DIR, OUTPUT_DIR, REMOTION_DIR, IMAGES_DIR, ACTIVE_PROJECT,
    VIDEO_FPS, FFMPEG_AAC_STEREO_ARGS, get_mode,
    seconds_to_frame, frames_to_ms,
)
from captions_config import (
    MAX_WORDS_PER_CAPTION, MAX_CHARS_PER_CAPTION, MIN_CAPTION_DURATION_MS,
)
from remotion_sync import write_project_snapshot, regenerate_root


def cut_video(combined: Path, keep: list[dict], out: Path):
    """Cut and concatenate kept segments with a single-pass PTS-trim filtergraph.

    The source is decoded ONCE and each keep block is selected by PTS via
    trim/atrim + setpts/asetpts, then concatenated. This is frame-accurate and
    immune to keyframe spacing.

    Why not per-segment input seeking (`-ss` before `-i`): combined.mp4 has
    sparse, scene-cut keyframes (multi-second gaps). When a segment starts deep
    inside a keyframe gap, the decoded pre-roll frames (keyframe → seek point)
    leak through the concat filtergraph instead of being discarded, so the
    segment opens on stale footage from a different moment. Trimming by PTS
    avoids any input seek and therefore any pre-roll.
    """
    print(f"  cutting {len(keep)} segments...")

    parts, labels = [], []
    for i, seg in enumerate(keep):
        s, e = seg["start"], seg["end"]
        parts.append(
            f"[0:v]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")

    filter_complex = (
        ";".join(parts) + ";" +
        "".join(labels) + f"concat=n={len(keep)}:v=1:a=1[outv][outa]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(combined),                 # single decode pass — no input seeking
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
                # Which cut (kept segment) this caption belongs to. group_keep_idx
                # holds the current group's cut at every flush() call site.
                "keep_idx": group_keep_idx,
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
            "cutIndex": grp["keep_idx"],
            "words": [
                {"start": seconds_to_frame(w["start"]), "end": seconds_to_frame(w["end"])}
                for w in grp["words"]
            ],
        }
        for grp in groups
    ]


def update_remotion(edited_video: Path, subtitles: list[dict],
                    duration_frames: int, mode: dict,
                    no_subtitles: bool = False, no_title: bool = False):
    """Write this project's render-ready snapshot into the multi-tenant Remotion
    store (public/projects/<name>/ + src/projects/<name>.json) and regenerate
    Root.tsx so every project is a registered composition.
    no_subtitles / no_title override mode-based defaults to force-disable each feature."""
    # Always store the transcript as captions so the Studio "Subtitles" tab shows
    # it for every project (incl. youtube). captions_enabled controls only whether
    # they render burned onto the video.
    captions = [
        {
            "startMs": frames_to_ms(s["start"]),
            "endMs":   frames_to_ms(s["end"]),
            "text":    s["text"].strip(),
            "cutIndex": s.get("cutIndex"),
            "words":   [
                {"startMs": frames_to_ms(w["start"]), "endMs": frames_to_ms(w["end"])}
                for w in (s.get("words") or [])
            ],
        }
        for s in subtitles
    ]
    captions_enabled = mode["subtitles"] and not no_subtitles
    if not captions_enabled:
        reason = "--no-subtitles flag" if no_subtitles else f"mode: {mode['mode']}"
        print(f"  on-video subtitles disabled ({reason}); "
              f"transcript still available in the Studio Subtitles tab")

    # Load image overlays — reel-only (youtube videos stay clean)
    image_overlays = []
    image_plan_path = OUT_DIR / "image_plan.json"
    if mode["mode"] == "youtube":
        print(f"  image overlays disabled (youtube mode)")
    elif image_plan_path.exists():
        with open(image_plan_path, encoding="utf-8") as f:
            image_overlays = json.load(f)
        if image_overlays:
            print(f"  injected {len(image_overlays)} image overlay(s) into Root.tsx defaultProps")

    # Load title cards — disabled for youtube or when --no-title is set
    title_cards = []
    title_cards_path = OUT_DIR / "title_cards.json"
    if title_cards_path.exists() and mode["mode"] != "youtube" and not no_title:
        with open(title_cards_path, encoding="utf-8") as f:
            title_cards = json.load(f)
        # Normalize: ensure every card has titleHighlight (defaults to "").
        # Required by the zod schema's resolved type that Remotion validates against.
        for card in title_cards:
            card.setdefault("titleHighlight", "")
            card.setdefault("subtitle", "")
        if title_cards:
            print(f"  injected {len(title_cards)} title card(s) into Root.tsx defaultProps")
    elif no_title:
        print(f"  title cards disabled (--no-title flag)")
    elif mode["mode"] == "youtube":
        print(f"  title cards disabled (youtube mode)")

    width, height, fps = mode["width"], mode["height"], mode["fps"]

    # Gather the image files referenced by the overlay plan so the snapshot is
    # self-contained (re-running step 4 alone preserves a prior step-4b placement).
    image_files = [
        IMAGES_DIR / o["file"]
        for o in image_overlays
        if (IMAGES_DIR / o["file"]).exists()
    ]

    # Cache-bust token so Studio's <Video> reloads the re-cut edited.mp4 (same
    # path on disk → browser would otherwise serve the stale file).
    video_version = int(time.time())

    write_project_snapshot(
        ACTIVE_PROJECT,
        edited_mp4=edited_video,
        captions=captions,
        captions_enabled=captions_enabled,
        title_cards=title_cards,
        image_overlays=image_overlays,
        width=width,
        height=height,
        fps=fps,
        duration_frames=duration_frames,
        video_version=video_version,
        image_files=image_files,
    )
    regenerate_root()

    print(f"  wrote project '{ACTIVE_PROJECT}' snapshot "
          f"({duration_frames} frames, {width}×{height}, {len(captions)} captions) "
          f"and regenerated Root.tsx")


def subtract_drops(keep: list[dict], cut_indices: set[int],
                   ranges_ms: list[tuple[float, float]]) -> list[dict]:
    """Return a new keep list with the requested removals applied.

    Both kinds of deletion become source-time intervals subtracted from `keep`:
      - cut_indices: whole kept-segment indices removed entirely.
      - ranges_ms:   (startMs, endMs) in EDITED time, mapped back to source time
                     and carved out. A caption never crosses a keep boundary, so
                     each edited range maps inside a single segment.
    Carving a hole splits a segment into two; empty pieces are dropped.
    """
    remove: list[tuple[float, float]] = []
    for c in cut_indices:
        if 0 <= c < len(keep):
            remove.append((keep[c]["start"], keep[c]["end"]))

    # Cumulative edited offset per keep segment, to map edited time -> source.
    edited_start = 0.0
    spans = []  # (edited_start, edited_end, source_start)
    for seg in keep:
        dur = seg["end"] - seg["start"]
        spans.append((edited_start, edited_start + dur, seg["start"]))
        edited_start += dur
    for ems_s, ems_e in ranges_ms:
        es, ee = ems_s / 1000.0, ems_e / 1000.0
        for a, b, src in spans:
            if a <= es < b:
                s = src + (es - a)
                e = src + (min(ee, b) - a)
                if e > s:
                    remove.append((s, e))
                break

    if not remove:
        return list(keep)

    new_keep: list[dict] = []
    for seg in keep:
        pieces = [(seg["start"], seg["end"])]
        for rs, re_ in remove:
            nxt = []
            for ps, pe in pieces:
                if re_ <= ps or rs >= pe:  # no overlap
                    nxt.append((ps, pe))
                    continue
                if rs > ps:
                    nxt.append((ps, rs))
                if re_ < pe:
                    nxt.append((re_, pe))
            pieces = nxt
        for ps, pe in pieces:
            if pe - ps > 1e-6:
                new_keep.append({"start": ps, "end": pe})
    return new_keep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true", help="Render final MP4 instead of opening Studio")
    parser.add_argument("--no-subtitles", action="store_true", help="Disable subtitles regardless of mode")
    parser.add_argument("--no-title", action="store_true", help="Disable title cards regardless of mode")
    parser.add_argument("--output-name", default=None,
                        help="Output filename stem (e.g. prueba1 → output/prueba1.mp4; default: final)")
    parser.add_argument("--drop-cuts", default=None,
                        help="Comma-separated kept-segment indices to delete, then re-cut + update "
                             "the snapshot (no render, no Studio). Used by the Subtitles-tab sidecar.")
    parser.add_argument("--drop-ranges", default=None,
                        help="Comma-separated edited-time ranges 'startMs-endMs' to carve out of the "
                             "video (single-caption deletes). Combined with --drop-cuts in one pass.")
    args = parser.parse_args()

    edit_plan_path = OUT_DIR / "edit_plan.json"
    if not edit_plan_path.exists():
        print("ERROR: src/data/edit_plan.json not found. Run 3_analyze.py first.")
        sys.exit(1)

    mode = get_mode()

    with open(edit_plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    # Delete cuts and/or single lines: subtract source-time intervals from keep,
    # recompute duration, and persist the reduced plan so successive drops compose
    # (and tab cutIndex keeps matching keep). combined.mp4 persists, so re-cutting
    # is always possible.
    if args.drop_cuts or args.drop_ranges:
        cut_indices = {int(i) for i in (args.drop_cuts or "").split(",") if i.strip() != ""}
        ranges_ms: list[tuple[float, float]] = []
        for r in (args.drop_ranges or "").split(","):
            r = r.strip()
            if not r:
                continue
            s, e = r.split("-")
            ranges_ms.append((float(s), float(e)))
        plan["keep"] = subtract_drops(plan["keep"], cut_indices, ranges_ms)
        if not plan["keep"]:
            print("ERROR: requested deletions would remove every segment; aborting.")
            sys.exit(1)
        plan["final_duration"] = sum(k["end"] - k["start"] for k in plan["keep"])
        with open(edit_plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"  removed {len(cut_indices)} cut(s) + {len(ranges_ms)} line(s); "
              f"{len(plan['keep'])} segment(s) remain")

    keep = plan["keep"]
    combined = OUT_DIR / "combined.mp4"
    edited = OUT_DIR / "edited.mp4"

    cut_video(combined, keep, edited)

    final_duration = plan["final_duration"]
    duration_frames = round(final_duration * VIDEO_FPS)
    # Always build subtitles (transcript mapped to the edited timeline) so the
    # Studio "Subtitles" tab is populated for every mode; on-video display is gated
    # separately by captions_enabled inside update_remotion.
    subtitles = build_subtitles(plan["transcript"], keep)

    update_remotion(edited, subtitles, duration_frames, mode,
                    no_subtitles=args.no_subtitles, no_title=args.no_title)

    print(f"\n✅ Edited video ready: {edited}")
    print(f"   Duration: {final_duration:.1f}s | {duration_frames} frames")
    print(f"   Subtitles: {len(subtitles)} entries")

    # Sidecar path: files updated, Studio is already running (HMR reloads). Done.
    if args.drop_cuts:
        print("   (--drop-cuts: snapshot updated; Studio will hot-reload)")
        return

    if args.render:
        print("\n  rendering final video with Remotion...")
        OUTPUT_DIR.mkdir(exist_ok=True)
        stem = args.output_name if args.output_name else ACTIVE_PROJECT
        final_out = OUTPUT_DIR / f"{stem}.mp4"
        subprocess.run(
            ["npx", "remotion", "render", ACTIVE_PROJECT, str(final_out)],
            cwd=REMOTION_DIR, check=True
        )
        print(f"\n🎬 Final video: {final_out}")
    else:
        print("\n  opening Remotion Studio for preview...")
        print("  (Ctrl+C to stop, then run with --render to export)")
        subprocess.run(["npm", "run", "dev"], cwd=REMOTION_DIR)


if __name__ == "__main__":
    main()
