"""
Step 3: Build the edit plan (keep-driven).

Keep = where speech is, from transcript word coverage. Each word covers
[start, end] — START is whisper.cpp's DTW onset (accurate across pauses), END
is capped upstream at MAX_WORD_DUR. Consecutive words within MAX_KEEP_GAP join
a block; everything outside blocks — silence, background noise, leading/
trailing junk, AND noise sitting in a pause between two words — is cut by
construction (no word covers it). Each block gets KEEP_PAD of headroom.

Before building blocks, words whose whole [start, end] sits inside a detected
silence are dropped: whisper.cpp DTW sometimes displaces a word's timestamp into
a pause (a 'hallucination island'), and that mislabel would otherwise seed a
dead-air keep block. The word's real audio, if any, is preserved by its
neighbours + edge snapping.

After blocks are built, real silence that a word bridged INTO a block (a pause
shorter than MAX_WORD_DUR start-to-start, hidden from the word-gap splitter
because whisper.cpp gives no reliable word end) is cut straight from
silencedetect: any silence longer than MAX_KEEP_GAP + 2*KEEP_PAD is removed,
leaving KEEP_PAD of breathing room on each side. Mirrors editor-pro-max's
pad-then-merge cut list.

Whisper-labeled non-speech (bracketed tokens like '[Toc, toc, toc]', recorded
as transcript['nonspeech']) is subtracted too — it catches loud noise fused
into a word's coverage that the gap rule alone would keep.

Optionally (--repetitions), Claude detects retakes/restarted sentences and
those intervals are subtracted from the keep blocks.

Usage:
  python3 3_analyze.py                 # gap-based cuts only
  python3 3_analyze.py --repetitions   # also remove retakes via Claude CLI
"""

import json
import argparse
import sys
from pathlib import Path
from config import OUT_DIR, get_duration, call_claude, silencedetect

# for YouTube MAX_KEEP_GAP = 0.3 & KEEP_PAD = 0.3,  and we use microphone, better error tolerance for youtube videos with no mic or longer silences and more tone variance
# for reels MAX_KEEP_GAP = 0.2 & KEEP_PAD = 1, this should make the videos quicker, sanppier, but more prone to errors but handable if video short
MAX_KEEP_GAP = 0.3    # seconds — no-talk spans longer than this are cut, consider final removed gaps will be > MAX_KEEP_GAP + 2 * KEEP_PAD
KEEP_PAD = 0.3       # seconds — headroom when no silence boundary to snap to
MIN_SEGMENT = 0.2     # seconds — keep fragments shorter than this are dropped
NONSPEECH_PAD = 0.05  # seconds — outward pad on whisper-labeled non-speech cuts

# Edge-snapping: word DTW starts can land late (clipping a word's onset) and
# capped word ends overshoot into trailing noise. We snap each keep block's
# edges to the real silence→speech / speech→silence boundaries instead.
SILENCE_DB = -35      # dB — energy below this counts as silence
SILENCE_MIN = 0.15    # seconds — min silence to register (fine, to sit between taps)
SNAP_LEAD = 0.6       # seconds — how far before a word's start a silence edge may be
                      # and still count as that word's onset (covers DTW lateness)
SNAP_SLOP = 0.15      # seconds — silence edge may sit slightly past the word start too
SNAP_MIN = 0.05       # seconds — a closing silence must begin at least this far in

REPETITION_CHUNK_SEC = 180     # seconds of speech per Claude request when transcript is long
REPETITION_CHUNK_OVERLAP = 10  # seconds of overlap between consecutive chunks


def flatten_words(transcript: dict) -> list[dict]:
    """Return all words from all segments, sorted by start time."""
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            if "start" in w and "end" in w:
                words.append(w)
    words.sort(key=lambda w: w["start"])
    return words


def detect_silences(video: Path, total_duration: float) -> list[dict]:
    """Silence intervals [{start, end}] via the shared config.silencedetect helper.
    Used only to snap keep-block edges to real speech boundaries. Passing
    total_duration closes a trailing pause that ends at EOF (which silencedetect
    would otherwise leave unpaired)."""
    return [{"start": s, "end": e}
            for s, e in silencedetect(video, SILENCE_DB, SILENCE_MIN,
                                      total_duration=total_duration)]


def drop_silent_words(words: list[dict],
                      silences: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop words whose entire [start, end] sits inside a silence interval.

    silencedetect marks [a, b] as silence only when audio stays below SILENCE_DB,
    and real speech has energy there — so a word fully inside a silence interval
    has no speech at its timestamp. It is a whisper.cpp DTW mislabel (a word's
    onset displaced off its real audio into a pause). Dropping it stops that
    phantom from seeding a dead-air keep block; a real-onset word never lands
    fully inside a silence, so this only removes mislabels, never spoken audio.

    Returns (kept, dropped).
    """
    if not silences:
        return words, []
    kept, dropped = [], []
    for w in words:
        in_silence = any(s["start"] <= w["start"] and w["end"] <= s["end"]
                         for s in silences)
        (dropped if in_silence else kept).append(w)
    return kept, dropped


def build_keep_blocks(words: list[dict], silences: list[dict],
                      total_duration: float) -> list[dict]:
    """
    Keep = where speech is. Word coverage decides the keep regions and the split
    points; silence boundaries fix each block's exact edges.

    Word coverage: each word covers [start, capped-end]. Consecutive words within
    MAX_KEEP_GAP join one block; a longer gap is cut. This removes noise that
    sits in a PAUSE between two words (a knock has energy, so silence detection
    keeps it, but no word covers it) and any leading/trailing audio with no words.

    Edge snapping: whisper.cpp DTW starts can land late (clipping a word's onset)
    and capped ends overshoot into trailing noise. So a block's START snaps back
    to the end of the silence just before its first word (the true onset), and
    its END snaps to the start of the silence just after its last word (the true
    offset, which also drops trailing taps the cap would otherwise keep). When no
    silence is in range, fall back to a plain KEEP_PAD.
    """
    if not words:
        return []

    # Word-coverage blocks; track first and last word starts for snapping.
    raw = [{"first": words[0]["start"], "last": words[0]["start"], "end": words[0]["end"]}]
    for w in words[1:]:
        if w["start"] - raw[-1]["end"] <= MAX_KEEP_GAP:
            raw[-1]["end"] = max(raw[-1]["end"], w["end"])
            raw[-1]["last"] = w["start"]
        else:
            raw.append({"first": w["start"], "last": w["start"], "end": w["end"]})

    blocks = []
    for b in raw:
        # START: latest silence whose end sits just before (or barely after) the
        # first word — that silence end is the real speech onset.
        lead = [s["end"] for s in silences
                if b["first"] - SNAP_LEAD <= s["end"] <= b["first"] + SNAP_SLOP]
        start = max(lead) if lead else b["first"] - KEEP_PAD

        # END: earliest silence that begins after the last word's onset — its
        # start is the real speech offset. Only trust it when it lands near the
        # word's (capped) end; otherwise the word was too quiet for silencedetect
        # to bound and the next silence is far away, so fall back to a plain pad.
        trail = [s["start"] for s in silences if s["start"] >= b["last"] + SNAP_MIN]
        fallback_end = b["end"] + KEEP_PAD
        end = min(trail) if trail else fallback_end
        if end > fallback_end + SNAP_LEAD:
            end = fallback_end

        start = max(0.0, start)
        end = min(total_duration, end)
        if end - start >= MIN_SEGMENT:
            blocks.append({"start": start, "end": end})

    # Merge any overlaps snapping created
    if not blocks:
        return []
    merged = [blocks[0]]
    for b in blocks[1:]:
        if b["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], b["end"])
        else:
            merged.append(b)
    return merged


def cut_silence_gaps(keep: list[dict], silences: list[dict],
                     min_gap: float, pad: float) -> tuple[list[dict], list[dict]]:
    """Cut real silence that survives INSIDE a keep block.

    build_keep_blocks splits on gaps in word *coverage*, but a word's end is
    synthesised from the next word's start (whisper.cpp gives no reliable end —
    its endMs tiles contiguously across pauses). So any pause shorter than
    MAX_WORD_DUR of start-to-start spacing is bridged by the preceding word and
    its silence lives inside a block, invisible to the word-gap splitter. This
    reads the gaps straight from silencedetect and subtracts them.

    A silence is cut only when its duration exceeds ``min_gap + 2*pad``, and
    ``pad`` seconds are left on each side as breathing room — so a cut removes
    (duration - 2*pad) and the join keeps 2*pad of natural pause. Shorter
    silences are left untouched. This mirrors editor-pro-max's pad-then-merge
    cut list (buildCutList + mergeSegments): a gap is removed iff
    gap > mergeGap + 2*padding. Block-edge silences are already excluded by
    build_keep_blocks' snapping, so in practice this only trims interior gaps.

    Returns (new_keep, cuts_applied).
    """
    threshold = min_gap + 2 * pad
    cuts = [{"start": s["start"] + pad, "end": s["end"] - pad}
            for s in silences if (s["end"] - s["start"]) > threshold]
    if not cuts:
        return keep, []
    return subtract_intervals(keep, cuts), cuts


def _format_words_for_prompt(words: list[dict]) -> str:
    """Render words grouped by ~2-second windows, with timestamp prefix per line."""
    if not words:
        return ""
    lines = []
    bucket_start = words[0]["start"]
    bucket = []
    for w in words:
        if w["start"] - bucket_start >= 2.0 and bucket:
            lines.append(f"[{bucket_start:.2f}s] {' '.join(x['word'].strip() for x in bucket)}")
            bucket_start = w["start"]
            bucket = []
        bucket.append(w)
    if bucket:
        lines.append(f"[{bucket_start:.2f}s] {' '.join(x['word'].strip() for x in bucket)}")
    return "\n".join(lines)


def _build_repetition_prompt(words_text: str) -> str:
    return (
        "You are editing a video where the speaker often restarts sentences, "
        "saying the same idea 2 or more times with small variations before "
        "landing on the final version.\n\n"
        "Your job: for each group of repeated attempts at the same idea, mark "
        "ALL attempts EXCEPT THE LAST ONE for removal. The last attempt is "
        "almost always the keeper (cleanest delivery).\n\n"
        "Rules:\n"
        "- An \"attempt\" can be a partial sentence the speaker abandons, "
        "or a full sentence they immediately rephrase.\n"
        "- Two phrasings are \"the same idea\" if they convey the same meaning, "
        "even if word choice differs.\n"
        "- Do NOT cut emphatic or rhetorical repetition (\"no, no, no\"; lists; "
        "intentional anaphora).\n"
        "- Return tight boundaries: start at the first word of the dropped "
        "attempt, end just before the first word of the next attempt.\n\n"
        f"TRANSCRIPT (word-level, timestamps in seconds):\n{words_text}\n\n"
        "Reply ONLY with valid JSON, no additional text:\n"
        '{"repetitions": [{"start": <seconds>, "end": <seconds>, "reason": "<brief description>"}]}\n'
        "If there are no repetitions: {\"repetitions\": []}."
    )


def find_repetitions(words: list[dict]) -> list[dict]:
    """
    Use the claude CLI to identify retake/repetition segments.
    Feeds word-level data so cut boundaries align with word edges.
    For long transcripts, chunks the input with overlap and merges results.
    """
    print("  asking Claude to identify repetitions...")
    if not words:
        return []

    total_span = words[-1]["end"] - words[0]["start"]

    if total_span <= REPETITION_CHUNK_SEC:
        prompt = _build_repetition_prompt(_format_words_for_prompt(words))
        data = call_claude(prompt)
        return data.get("repetitions", []) if data else []

    # Chunked path for long transcripts: overlap chunks so a repetition that
    # straddles a boundary still gets seen as a contiguous group in one chunk.
    all_reps: list[dict] = []
    chunk_start = words[0]["start"]
    end_time = words[-1]["end"]
    while chunk_start < end_time:
        chunk_end = chunk_start + REPETITION_CHUNK_SEC
        chunk_words = [w for w in words if chunk_start <= w["start"] < chunk_end]
        if chunk_words:
            prompt = _build_repetition_prompt(_format_words_for_prompt(chunk_words))
            data = call_claude(prompt)
            all_reps.extend(data.get("repetitions", []) if data else [])
        chunk_start = chunk_end - REPETITION_CHUNK_OVERLAP

    # Deduplicate overlapping reports from adjacent chunks.
    all_reps.sort(key=lambda r: (r["start"], r["end"]))
    deduped: list[dict] = []
    for r in all_reps:
        if deduped and r["start"] <= deduped[-1]["end"]:
            deduped[-1]["end"] = max(deduped[-1]["end"], r["end"])
        else:
            deduped.append(dict(r))
    return deduped


def subtract_intervals(keep: list[dict], cuts: list[dict]) -> list[dict]:
    """Subtract cut intervals from keep blocks, dropping fragments shorter
    than MIN_SEGMENT."""
    result = []
    for block in keep:
        pieces = [dict(block)]
        for cut in cuts:
            next_pieces = []
            for p in pieces:
                if cut["end"] <= p["start"] or cut["start"] >= p["end"]:
                    next_pieces.append(p)
                    continue
                if cut["start"] > p["start"]:
                    next_pieces.append({"start": p["start"], "end": cut["start"]})
                if cut["end"] < p["end"]:
                    next_pieces.append({"start": cut["end"], "end": p["end"]})
            pieces = next_pieces
        result.extend(p for p in pieces if p["end"] - p["start"] >= MIN_SEGMENT)
    return result


def keep_to_cuts(keep: list[dict], total_duration: float) -> list[dict]:
    """Complement of the keep blocks — for reporting and edit_plan.json."""
    cuts = []
    cursor = 0.0
    for block in keep:
        if block["start"] - cursor > 0:
            cuts.append({"start": cursor, "end": block["start"], "type": "no-speech"})
        cursor = block["end"]
    if total_duration - cursor > 0:
        cuts.append({"start": cursor, "end": total_duration, "type": "no-speech"})
    return cuts


def _sum_duration(items: list[dict]) -> float:
    return sum(i["end"] - i["start"] for i in items)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", action="store_true",
                        help="Also detect and cut retakes/restarted sentences (Claude CLI)")
    args = parser.parse_args()

    combined = OUT_DIR / "combined.mp4"
    transcript_path = OUT_DIR / "transcript.json"

    if not combined.exists():
        print("ERROR: src/data/combined.mp4 not found. Run 1_normalize.py first.")
        sys.exit(1)
    if not transcript_path.exists():
        print("ERROR: src/data/transcript.json not found. Run 2_transcribe.py first.")
        sys.exit(1)

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    total_duration = get_duration(combined)
    print(f"  Video duration: {total_duration:.1f}s")

    words = flatten_words(transcript)
    print(f"  Transcript words: {len(words)}")

    silences = detect_silences(combined, total_duration)

    words, dropped_silent = drop_silent_words(words, silences)
    if dropped_silent:
        detail = ", ".join(f"{w['word'].strip()!r}@{w['start']:.2f}s"
                           for w in dropped_silent)
        print(f"  dropped {len(dropped_silent)} word(s) stranded in silence "
              f"(whisper hallucination): {detail}")

    keep = build_keep_blocks(words, silences, total_duration)
    print(f"  Keep blocks (word coverage, edges snapped to silence): "
          f"{len(keep)} ({_sum_duration(keep):.1f}s)")

    # Cut real silence that words bridged into a block. whisper.cpp has no
    # reliable word end, so a pause shorter than MAX_WORD_DUR of start-to-start
    # spacing is hidden from the word-gap splitter and survives as in-block dead
    # air. Read gaps straight from silencedetect: cut any silence longer than
    # MAX_KEEP_GAP + 2*KEEP_PAD, leaving KEEP_PAD of breathing room each side.
    keep, gap_cuts = cut_silence_gaps(keep, silences, MAX_KEEP_GAP, KEEP_PAD)
    if gap_cuts:
        print(f"  cut {len(gap_cuts)} in-block silence gap(s) "
              f"> {MAX_KEEP_GAP + 2 * KEEP_PAD:.2f}s → {len(keep)} blocks "
              f"({_sum_duration(keep):.1f}s)")

    # Cut whisper-labeled non-speech regions ([Toc...], [música]). These catch
    # loud noise fused into a word's coverage that the gap rule alone keeps.
    # Pad outward so the whole burst goes.
    nonspeech = transcript.get("nonspeech", [])
    if nonspeech:
        padded = [{"start": max(0.0, n["start"] - NONSPEECH_PAD),
                   "end": n["end"] + NONSPEECH_PAD} for n in nonspeech]
        keep = subtract_intervals(keep, padded)
        print(f"  Cut {len(nonspeech)} non-speech region(s) "
              f"({_sum_duration(nonspeech):.1f}s) → {len(keep)} blocks "
              f"({_sum_duration(keep):.1f}s)")

    repetitions = []
    if args.repetitions:
        repetitions = find_repetitions(words)
        print(f"  Found {len(repetitions)} repetition intervals "
              f"({_sum_duration(repetitions):.1f}s)")
        keep = subtract_intervals(keep, repetitions)

    cuts = keep_to_cuts(keep, total_duration)

    kept_time = _sum_duration(keep)
    cut_time = total_duration - kept_time

    edit_plan = {
        "total_duration": total_duration,
        "final_duration": kept_time,
        "cut_time": cut_time,
        "cuts": cuts,
        "keep": keep,
        "transcript": transcript,
    }

    out_path = OUT_DIR / "edit_plan.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(edit_plan, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done.")
    print(f"   edit_plan.json → {out_path}")
    print(f"   Original:  {total_duration:.1f}s")
    print(f"   Cutting:   {cut_time:.1f}s ({cut_time/total_duration*100:.0f}%)"
          + (f" (incl. {_sum_duration(repetitions):.1f}s repetitions)" if repetitions else ""))
    print(f"   Final:     {kept_time:.1f}s ({kept_time/60:.1f} min)")
    print(f"   Segments to keep: {len(keep)}")


if __name__ == "__main__":
    main()
