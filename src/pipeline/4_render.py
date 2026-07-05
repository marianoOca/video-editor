"""
Step 4: Cut the video with ffmpeg using the edit plan, then update Remotion
with subtitles and render (or open Studio for preview).

Usage:
  python3 4_render.py              # cut video + open Remotion Studio
  python3 4_render.py --render     # cut video + render final MP4
"""

import subprocess
import json
import os
import socket
import sys
import time
import argparse
from pathlib import Path
from config import (
    OUT_DIR, OUTPUT_DIR, REMOTION_DIR, IMAGES_DIR, ACTIVE_PROJECT,
    VIDEO_FPS, FFMPEG_AAC_STEREO_ARGS, SIDECAR_PORT, get_mode, run_ffmpeg,
    seconds_to_frame, frames_to_ms, append_manifest_output,
)
from tuning import (
    MAX_WORDS_PER_CAPTION, MAX_CHARS_PER_CAPTION, MIN_CAPTION_DURATION_MS,
    MIN_WORD_OVERLAP,
)
from remotion_sync import write_project_snapshot, regenerate_root, read_snapshot


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def snap_keep_to_frames(keep: list[dict], fps: int = VIDEO_FPS) -> list[dict]:
    """Quantize keep-segment boundaries to the fps grid.

    The cut filtergraph trims video on the frame grid (whole frames only) but audio
    sample-exact (atrim). On float-second boundaries each segment's video duration
    rounds to a whole frame while its audio stays exact, so concat stacks two slightly
    different per-segment lengths — the mismatch random-walks across cuts into visible
    A/V drift (worse the more cuts there are). Snapping both boundaries to k/fps makes
    each segment's video frame-count and audio duration identical (at 44.1 kHz / 30 fps
    a frame is exactly 1470 samples, so a frame boundary is also a sample boundary), so
    video, audio, and the subtitle timeline (built from this same list) stay locked.
    """
    snapped = []
    for seg in keep:
        s = round(seg["start"] * fps) / fps
        e = round(seg["end"] * fps) / fps
        if round(e * fps) > round(s * fps):  # drop any sub-frame segment
            snapped.append({**seg, "start": s, "end": e})
    return snapped


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
        # Intermediate cut: crf 18 locks quality; preset only trades encode
        # time vs file size (NOT quality). veryfast ~1.5-2x faster than fast
        # with all quality tools intact. Stay >= veryfast — superfast/ultrafast
        # disable CABAC/psy tools and do degrade quality.
        "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        # Force constant 30fps. The concat filter can emit variable frame timing
        # (avg_frame_rate != 30), which WebCodecs also rejects as a format error.
        "-r", str(VIDEO_FPS), "-vsync", "cfr",
        "-movflags", "+faststart",
        *FFMPEG_AAC_STEREO_ARGS,
        str(out)
    ]
    run_ffmpeg(cmd)


def build_subtitles(transcript: dict, keep: list[dict]) -> list[dict]:
    """
    Map transcript words to the edited timeline, then group into short caption
    lines (≤ MAX_WORDS_PER_CAPTION words, ≤ MAX_CHARS_PER_CAPTION chars).

    Words fully inside a cut are discarded — no phantom fallback.
    Groups never cross keep-segment boundaries (no text from cut zone bleeds in).
    Returns list of {start (frame), end (frame), text}.
    """
    # Overlap rule + MIN_WORD_OVERLAP documented in tuning.py. Zero-duration
    # words (collapsed DTW starts) carry no overlap, so they fall back to the
    # start-inside test.
    def word_to_edited(word: dict):
        """Map a word's [start, end] to the edited timeline.
        Returns (edited_start, edited_end, keep_idx) or None if the word is cut.
        Both edges are clamped to the segment."""
        duration = word["end"] - word["start"]
        best = None  # (overlap, edited_start, edited_end, keep_idx)
        edited_cursor = 0.0
        for idx, seg in enumerate(keep):
            if duration <= 0:
                if seg["start"] <= word["start"] < seg["end"]:
                    off = edited_cursor + (word["start"] - seg["start"])
                    return (off, off, idx)
            else:
                ov_start = max(word["start"], seg["start"])
                ov_end = min(word["end"], seg["end"])
                overlap = ov_end - ov_start
                if (overlap >= min(MIN_WORD_OVERLAP, duration / 2)
                        and (best is None or overlap > best[0])):
                    best = (
                        overlap,
                        edited_cursor + (ov_start - seg["start"]),
                        edited_cursor + (ov_end - seg["start"]),
                        idx,
                    )
            edited_cursor += seg["end"] - seg["start"]
        return best[1:] if best else None

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


def subtitles_to_captions(subtitles: list[dict]) -> list[dict]:
    """Convert build_subtitles() output (frame-based) to snapshot captions (ms).
    Always stored — even in youtube mode — so the Studio Subtitles tab is populated
    everywhere; captions_enabled gates only the burned-in render."""
    return [
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


def update_remotion(edited_video: Path, captions: list[dict],
                    duration_frames: int, mode: dict,
                    no_subtitles: bool = False, no_title: bool = False):
    """Write this project's render-ready snapshot into the multi-tenant Remotion
    store (public/projects/<name>/ + src/projects/<name>.json) and regenerate
    Root.tsx so every project is a registered composition. `captions` is the final
    ms-based caption list (freshly derived, or re-mapped through a re-cut).
    no_subtitles / no_title override mode-based defaults to force-disable each feature."""
    # on-video subtitles ("Display on video"): a per-project toggle that must survive
    # reloads AND re-cuts. --no-subtitles force-disables; otherwise preserve the value
    # already in the snapshot (the Subtitles tab's toggle persisted it via the sidecar);
    # only a brand-new project with no snapshot falls back to the mode default
    # (reel → on, youtube → off).
    prev_snap = read_snapshot(ACTIVE_PROJECT)
    prior_enabled = prev_snap.get("captionsEnabled") if prev_snap else None
    if no_subtitles:
        captions_enabled = False
        reason = "--no-subtitles flag"
    elif prior_enabled is not None:
        captions_enabled = bool(prior_enabled)
        reason = "prior toggle"
    else:
        captions_enabled = mode["subtitles"]
        reason = f"mode: {mode['mode']}"
    if not captions_enabled:
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


def drop_wordless_segments(keep: list[dict], transcript: dict) -> list[dict]:
    """Drop keep segments that contain no transcript word.

    A kept segment carries ±0.1s of pad around its speech. When a carve
    (subtract_drops) removes the boundary caption of a cut, the pad on that side
    survives as a tiny word-less sliver (e.g. dropping a cut's FIRST caption leaves
    the leading pad [seg.start, first_word.start]). That sliver would otherwise
    register as a phantom kept segment — an extra "Cut" that shifts every later
    cutIndex, so a dropped first/last line appears to split its cut and mis-mark a
    neighbour. Keep segments only ever hold speech blocks, so a word-less one is
    always such an artifact; remove it. (A mid-line delete still splits a cut into
    two word-ful halves — that is by design and untouched here.)

    Membership test is the word's MIDPOINT, not its start: frame-snapping can
    stretch a leading-pad sliver just past the first word's start, so it clips a few
    ms of that word's edge — a start-inside test would wrongly keep it. The midpoint
    lands in the real speech remnant, never in the pad sliver."""
    mids = [
        (w["start"] + w["end"]) / 2.0
        for seg in transcript.get("segments", [])
        for w in (seg.get("words") or []) if "start" in w and "end" in w
    ]
    return [
        seg for seg in keep
        if any(seg["start"] <= mid < seg["end"] for mid in mids)
    ]


def captions_match_keep(prev_caps: list[dict], old_keep: list[dict]) -> bool:
    """True if the captions plausibly belong to `old_keep`'s edited timeline, so
    re-mapping them through a cut is safe. Catches a stale Studio store (captions
    from a since-rebuilt/longer timeline): a cutIndex past the segment count, or a
    caption ending well beyond the total edited duration. Tolerant of small
    overshoot (MIN_CAPTION_DURATION extension, manual time edits)."""
    if not old_keep:
        return False
    n = len(old_keep)
    total_ms = sum(s["end"] - s["start"] for s in old_keep) * 1000.0
    for c in prev_caps:
        ci = c.get("cutIndex")
        if ci is not None and not (0 <= ci < n):
            return False
        if float(c.get("endMs", 0)) > total_ms + 1500:
            return False
    return True


def remap_captions(prev_caps: list[dict], old_keep: list[dict],
                   new_keep: list[dict]) -> list[dict]:
    """Re-time persisted (edited) captions through a re-cut, preserving manual
    text/split/merge edits instead of re-deriving them from the transcript.

    The removed time is derived from the ACTUAL keep diff (old coverage minus
    new coverage, mapped to old-edited ms) rather than from the requested
    deletions: carved ranges, whole-cut drops AND the word-less pad slivers that
    drop_wordless_segments removes all count. Deriving it from the request
    instead misses the slivers, leaving every later caption late by the pad
    (~0.1-0.3s per deletion). A diff also yields disjoint intervals, so an
    overlapping cut+range pair can't double-shift. Every deletion aligns to
    whole captions, so each surviving caption is fully outside the removed
    intervals — re-timing is a pure left-shift by the dropped time before it.
    cutIndex is recomputed against the new keep segmentation. EDITED-time ms."""
    # removed (old-edited ms) = old_keep coverage minus new_keep coverage.
    removed: list[tuple[float, float]] = []
    cur = 0.0
    for seg in old_keep:
        pieces = [(seg["start"], seg["end"])]
        for k in new_keep:
            nxt = []
            for ps, pe in pieces:
                if k["end"] <= ps or k["start"] >= pe:
                    nxt.append((ps, pe))
                    continue
                if k["start"] > ps:
                    nxt.append((ps, k["start"]))
                if k["end"] < pe:
                    nxt.append((k["end"], pe))
            pieces = nxt
        removed.extend(((cur + ps - seg["start"]) * 1000.0,
                        (cur + pe - seg["start"]) * 1000.0)
                       for ps, pe in pieces if pe - ps > 1e-9)
        cur += seg["end"] - seg["start"]
    removed.sort()

    def removed_before(t: float) -> float:
        return sum(max(0.0, min(t, b) - a) for a, b in removed)

    def overlaps_removed(s: float, e: float) -> bool:
        # Any real overlap with a dropped interval → the caption is gone. Aligned
        # drops make this all-or-nothing; the overlap form also stays correct if a
        # manual time-edit left a caption straddling a boundary (it's dropped, not
        # mis-shifted). Half-ms slack: removed bounds are float keep sums while
        # caption times sit on the frame grid — an exact touch must not count.
        return any(e > a + 0.5 and s < b - 0.5 for a, b in removed)

    # New-edited bounds (ms) per keep segment, for cutIndex recomputation. Rounded to
    # whole ms: the boundaries are a running sum of float durations, so a clean 1100 ms
    # cut surfaces as 1100.0000000000005. An integer compare keeps a caption that starts
    # EXACTLY at a splice on the LATER cut (via a<=t<b) — correct, the first caption after
    # a splice belongs to the new cut — instead of the float tail pulling it back one cut.
    new_bounds, cur = [], 0.0
    for seg in new_keep:
        dur = seg["end"] - seg["start"]
        a = round(cur * 1000.0)
        cur += dur
        new_bounds.append((a, round(cur * 1000.0)))

    def cut_index_for(t_ms: float) -> int:
        t = round(t_ms)
        for i, (a, b) in enumerate(new_bounds):
            if a <= t < b:
                return i
        return max(0, len(new_bounds) - 1)  # clamp rounding at the very end

    out: list[dict] = []
    for cap in prev_caps:
        s, e = float(cap["startMs"]), float(cap["endMs"])
        if overlaps_removed(s, e):  # any part cut → caption gone
            continue
        shift = removed_before(s)  # == removed_before(e): caption is outside R
        out.append({
            "startMs": s - shift,
            "endMs": e - shift,
            "text": cap.get("text", ""),
            "cutIndex": cut_index_for(s - shift),
            "words": [
                {"startMs": float(w["startMs"]) - shift, "endMs": float(w["endMs"]) - shift}
                for w in (cap.get("words") or [])
            ],
        })
    return out


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
    parser.add_argument("--no-open", action="store_true",
                        help="Regenerate edited.mp4 + snapshot in place, then exit WITHOUT opening "
                             "Studio (it's already running; HMR reloads). Used by the Subtitles-tab "
                             "Fix button to repair a corrupt project.")
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
    captions: list[dict] | None = None
    if args.drop_cuts or args.drop_ranges:
        cut_indices = {int(i) for i in (args.drop_cuts or "").split(",") if i.strip() != ""}
        ranges_ms: list[tuple[float, float]] = []
        for r in (args.drop_ranges or "").split(","):
            r = r.strip()
            if not r:
                continue
            s, e = r.split("-")
            ranges_ms.append((float(s), float(e)))
        # Snap the keep to the frame grid ONCE here as the single source of truth: the
        # persisted keep, the cutIndex bounds in remap_captions, and cut_video below all
        # bucket against the SAME frame-aligned cut points. cutIndex must align to where
        # the video is actually spliced (frame grid); computing it on an unsnapped keep
        # while the video is cut on a snapped one lets a caption starting at a splice land
        # on the wrong cut. old_keep is snapped too so the shift math matches the caption
        # timeline (captions were timed against the prior snapped keep).
        old_keep = snap_keep_to_frames(list(plan["keep"]))
        new_keep = subtract_drops(old_keep, cut_indices, ranges_ms)
        # A carve that trims a cut down to its leading/trailing pad leaves a
        # word-less sliver; drop it so it doesn't become a phantom cut that shifts
        # every later cutIndex (dropped first/last line splitting its cut).
        new_keep = drop_wordless_segments(new_keep, plan["transcript"])
        new_keep = snap_keep_to_frames(new_keep)
        if not new_keep:
            print("ERROR: requested deletions would remove every segment; aborting.")
            sys.exit(1)
        plan["keep"] = new_keep
        plan["final_duration"] = sum(k["end"] - k["start"] for k in new_keep)
        tmp = edit_plan_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, edit_plan_path)  # atomic — repo convention
        print(f"  removed {len(cut_indices)} cut(s) + {len(ranges_ms)} line(s); "
              f"{len(new_keep)} segment(s) remain")

        # Preserve manual caption edits (text/splits/merges) by re-mapping the
        # persisted snapshot captions through the cut instead of re-deriving them —
        # but ONLY if those captions are consistent with the current keep. A stale
        # Studio store (e.g. captions from a since-rebuilt timeline) would otherwise
        # be re-mapped into garbage and desync from the video; in that case discard
        # them and fall back to a clean re-derive (manual edits lost, but the cut is
        # correct and the snapshot self-heals).
        prev = read_snapshot(ACTIVE_PROJECT)
        prev_caps = (prev or {}).get("captions")
        if prev_caps and captions_match_keep(prev_caps, old_keep):
            captions = remap_captions(prev_caps, old_keep, new_keep)
            print(f"  re-mapped {len(captions)} caption(s) through the cut (edits preserved)")
        elif prev_caps:
            print("  ⚠ snapshot captions inconsistent with the cut (stale Studio store?) — "
                  "re-deriving from transcript")

    # Snap cut boundaries to the frame grid so the frame-domain video cut and the
    # sample-domain audio cut land on identical boundaries — feeds cut_video AND
    # build_subtitles from one frame-aligned list so picture, voice, and captions
    # stay locked (no per-cut A/V drift that grows with the number of cuts).
    keep = snap_keep_to_frames(plan["keep"])
    combined = OUT_DIR / "combined.mp4"
    edited = OUT_DIR / "edited.mp4"

    cut_video(combined, keep, edited)

    # Recompute from the snapped keep so the composition length matches the
    # frame-aligned edited.mp4 exactly (plan["final_duration"] used unsnapped bounds).
    final_duration = sum(seg["end"] - seg["start"] for seg in keep)
    duration_frames = round(final_duration * VIDEO_FPS)
    # Fresh derive when there are no persisted captions to preserve (initial run, or
    # a full rebuild). Always stored — even youtube — so the Subtitles tab is
    # populated; captions_enabled gates only the burned-in render.
    if captions is None:
        captions = subtitles_to_captions(build_subtitles(plan["transcript"], keep))

    update_remotion(edited, captions, duration_frames, mode,
                    no_subtitles=args.no_subtitles, no_title=args.no_title)

    print(f"\n✅ Edited video ready: {edited}")
    print(f"   Duration: {final_duration:.1f}s | {duration_frames} frames")
    print(f"   Subtitles: {len(captions)} entries")

    # Sidecar / Fix path: files updated, Studio is already running (HMR reloads). Done.
    if args.drop_cuts or args.no_open:
        reason = "--drop-cuts" if args.drop_cuts else "--no-open"
        print(f"   ({reason}: snapshot updated; Studio will hot-reload)")
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
        append_manifest_output(final_out)
        print(f"\n🎬 Final video: {final_out}")
    else:
        print("\n  opening Remotion Studio for preview...")
        print("  (Ctrl+C to stop, then run with --render to export)")
        # Bring up the sidecar (cut/edit doorbell) so the Subtitles tab's Apply works
        # out of the box — but only if one isn't already serving 9848. Studio opens
        # solo (dev:studio); the sidecar we spawn is torn down when Studio exits.
        # (Not `npm run dev`: that bundles both via concurrently --kill-others, which
        # would take Studio down if the bundled sidecar hit a port clash.)
        sidecar_proc = None
        if _port_free(SIDECAR_PORT):
            print(f"  starting sidecar on :{SIDECAR_PORT}")
            sidecar_proc = subprocess.Popen([sys.executable, "sidecar.py"],
                                            cwd=Path(__file__).parent)
        else:
            print(f"  sidecar already running on :{SIDECAR_PORT} — reusing it")
        try:
            subprocess.run(["npm", "run", "dev:studio"], cwd=REMOTION_DIR)
        finally:
            if sidecar_proc:
                sidecar_proc.terminate()


if __name__ == "__main__":
    main()
