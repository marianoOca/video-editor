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
import os
import sys
from pathlib import Path
import re
from typing import Optional
from config import (OUT_DIR, MODE_PATH, get_duration, call_claude, silencedetect,
                    probe, run_ffmpeg, VOICE_BANDPASS)
from tuning import (
    MAX_KEEP_GAP, KEEP_PAD, MIN_SEGMENT, NONSPEECH_PAD, SILENT_WORD_MARGIN,
    ENERGY_NET_MIN_BURST, ENERGY_NET_MAX_GAP, ENERGY_NET_LONG_BURST,
    ENERGY_NET_LONG_GAP, ENERGY_NET_MAX_BURST,
    ENERGY_NET_FLAT_STD_DB, ENERGY_NET_FLAT_MIN_DUR, FLAT_WINDOW_SEC,
    SILENCE_DB, SILENCE_MIN, SNAP_LEAD, SNAP_SLOP, SNAP_MIN,
    REPETITION_CHUNK_SEC, REPETITION_CHUNK_OVERLAP,
)


def flatten_words(transcript: dict) -> list[dict]:
    """Return all words from all segments, sorted by start time."""
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            if "start" in w and "end" in w:
                words.append(w)
    words.sort(key=lambda w: w["start"])
    return words


def resolve_silence_db() -> float:
    """Per-video silence threshold: step 1 measures the recording's noise floor
    and writes silence_db into mode.json (knobs in tuning.py's step-1 section).
    Falls back to the fixed SILENCE_DB when mode.json is absent or predates the
    field, so old projects re-analyze byte-identically."""
    if MODE_PATH is not None and MODE_PATH.exists():
        return float(json.loads(MODE_PATH.read_text()).get("silence_db", SILENCE_DB))
    return SILENCE_DB


def detect_silences(video: Path, total_duration: float,
                    noise_db: float = SILENCE_DB) -> list[dict]:
    """Silence intervals [{start, end}] via the shared config.silencedetect helper.
    Used only to snap keep-block edges to real speech boundaries. Passing
    total_duration closes a trailing pause that ends at EOF (which silencedetect
    would otherwise leave unpaired)."""
    return [{"start": s, "end": e}
            for s, e in silencedetect(video, noise_db, SILENCE_MIN,
                                      total_duration=total_duration)]


def burst_rms_std(video: Path, start: float, end: float) -> float:
    """Std of the voice-band windowed RMS over [start, end], in dB.

    A speech fragment has phoneme structure (RMS peaks and valleys, high std);
    steady background noise is flat (low std). Used by the energy net to tell a
    mistimed word from noise. Same VOICE_BANDPASS + windowed-RMS form as
    1_normalize.measure_silence_floor so the numbers are comparable. Returns a
    large sentinel when too few windows are measured, so a too-short segment is
    never mistaken for flat noise.
    """
    data = probe(video)
    astream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if astream is None:
        return 99.0
    window = max(1, int(int(astream["sample_rate"]) * FLAT_WINDOW_SEC))
    proc = run_ffmpeg(
        ["ffmpeg", "-ss", f"{start}", "-t", f"{end - start}", "-i", str(video),
         "-af", (f"{VOICE_BANDPASS},asetnsamples=n={window},"
                 "astats=metadata=1:reset=1,"
                 "ametadata=print:key=lavfi.astats.Overall.RMS_level"),
         "-f", "null", "-"]
    )
    vals = [float(m) for m in
            re.findall(r"RMS_level=(-?[\d.]+(?:[eE][+-]?\d+)?)", proc.stderr or "")]
    if len(vals) < 2:
        return 99.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def drop_silent_words(words: list[dict],
                      silences: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop words whose entire [start, end] sits inside a silence interval.

    silencedetect marks [a, b] as silence only when audio stays below SILENCE_DB,
    and real speech has energy there — so a word fully inside a silence interval
    has no speech at its timestamp. It is a whisper.cpp DTW mislabel (a word's
    onset displaced off its real audio into a pause). Dropping it stops that
    phantom from seeding a dead-air keep block; a real-onset word never lands
    fully inside a silence, so this only removes mislabels, never spoken audio.

    The start must sit SILENT_WORD_MARGIN inside the silence: a word starting a
    few ms past the silence edge is a float tie on audio that really sits at the
    boundary (and is kept) — dropping it would only lose its caption.

    Returns (kept, dropped).
    """
    if not silences:
        return words, []
    kept, dropped = [], []
    for w in words:
        in_silence = any(s["start"] + SILENT_WORD_MARGIN <= w["start"]
                         and w["end"] <= s["end"]
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

    # Word-coverage blocks; track first and last word starts for snapping, plus
    # the block's latest "evidence" instant (last sub-token DTW onset, written by
    # step 2) — audio is PROVEN to still be running there, so the end snap must
    # not pick a silence before it (that would cut an evidence-extended word
    # right back to its first token).
    def _ev(w):
        return max(w["start"], w.get("evidence", w["start"]))

    raw = [{"first": words[0]["start"], "last": words[0]["start"],
            "evid": _ev(words[0]), "end": words[0]["end"]}]
    for w in words[1:]:
        if w["start"] - raw[-1]["end"] <= MAX_KEEP_GAP:
            raw[-1]["end"] = max(raw[-1]["end"], w["end"])
            raw[-1]["last"] = w["start"]
            raw[-1]["evid"] = max(raw[-1]["evid"], _ev(w))
        else:
            raw.append({"first": w["start"], "last": w["start"],
                        "evid": _ev(w), "end": w["end"]})

    blocks = []
    for b in raw:
        # START: latest silence whose end sits just before (or barely after) the
        # first word — that silence end is the real speech onset.
        lead = [s["end"] for s in silences
                if b["first"] - SNAP_LEAD <= s["end"] <= b["first"] + SNAP_SLOP]
        start = max(lead) if lead else b["first"] - KEEP_PAD

        # END: earliest silence that begins after the last word's onset (or the
        # block's latest evidence instant, if later) — its start is the real
        # speech offset. Only trust it when it lands near the word's (capped)
        # end; otherwise the word was too quiet for silencedetect to bound and
        # the next silence is far away, so fall back to a plain pad.
        floor = max(b["last"], b["evid"])
        trail = [s["start"] for s in silences if s["start"] >= floor + SNAP_MIN]
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


def extend_keeps_to_adjacent_speech(keep: list[dict], silences: list[dict],
                                    total_duration: float,
                                    video: Optional[Path] = None) -> tuple[list[dict], int]:
    """Energy safety net: rescue real speech the transcript mistimed.

    Keep = word coverage, so wherever whisper's word timing is wrong the real
    audio has no coverage and is cut by construction. A speech-energy burst
    (complement of silencedetect) of at least ENERGY_NET_MIN_BURST that survived
    outside every keep, but sits within ENERGY_NET_MAX_GAP of a keep edge, is
    almost always that mistimed speech — a late DTW onset or a capped word end.
    The nearest keep is extended to cover the burst.

    Four bounds protect the word-first design (each covers a verified failure):
    keeps are only ever EXTENDED, never created (isolated noise stays cut); a
    rescue is capped at ENERGY_NET_MAX_BURST (sustained audio fused to a speech
    edge — music, typing, a second voice — is not a mistimed word); a burst
    spanning keep-edge to keep-edge is skipped (that is a pause silencedetect
    missed — re-gluing it would undo a word-gap cut, and with no detected
    silences at all it would undo EVERY cut); and a burst whose voice-band RMS is
    too FLAT (steady background noise, not phoneme-structured speech) is skipped
    when ``video`` is given — timing and loudness alone can't tell a loud noise
    burst from a late word onset, but temporal shape can.

    ``video`` is optional: without it the flatness gate is skipped, keeping the
    function usable in pure-timing tests.

    Returns (new_keep, bursts_rescued).
    """
    if not keep:
        return keep, 0

    # Speech = complement of the silence intervals.
    speech = []
    cursor = 0.0
    for s in silences:
        if s["start"] - cursor > 1e-6:
            speech.append((cursor, s["start"]))
        cursor = max(cursor, s["end"])
    if total_duration - cursor > 1e-6:
        speech.append((cursor, total_duration))

    blocks = [dict(b) for b in keep]
    rescued = 0
    for a, b in speech:
        # Subtract keeps from this speech interval → uncovered bursts.
        pieces = [(a, b)]
        for k in keep:
            nxt = []
            for pa, pb in pieces:
                if k["end"] <= pa or k["start"] >= pb:
                    nxt.append((pa, pb))
                    continue
                if k["start"] > pa:
                    nxt.append((pa, k["start"]))
                if k["end"] < pb:
                    nxt.append((k["end"], pb))
            pieces = nxt
        for pa, pb in pieces:
            if not ENERGY_NET_MIN_BURST <= pb - pa <= ENERGY_NET_MAX_BURST:
                continue
            # A burst whose bounds are keep edges on BOTH sides is the whole gap
            # between two keeps — a pause silencedetect missed, not mistimed
            # speech (a real rescue is silence-bounded on at least one side).
            # Re-gluing it would undo a word-gap cut, so skip it.
            starts_at_keep = any(abs(k["end"] - pa) < 1e-9 for k in keep)
            ends_at_keep = any(abs(k["start"] - pb) < 1e-9 for k in keep)
            if starts_at_keep and ends_at_keep:
                continue
            # Nearest keep edge (0 gap when the burst touches a block). Measured
            # against the ORIGINAL keeps: measuring against already-extended
            # blocks lets rhythmic noise (typing, footsteps) chain-rescue its way
            # arbitrarily far from the real speech edge.
            def gap_to(k):
                if pa >= k["end"]:
                    return pa - k["end"]
                if k["start"] >= pb:
                    return k["start"] - pb
                return 0.0
            idx = min(range(len(keep)), key=lambda i: gap_to(keep[i]))
            max_gap = (ENERGY_NET_LONG_GAP if pb - pa >= ENERGY_NET_LONG_BURST
                       else ENERGY_NET_MAX_GAP)
            if gap_to(keep[idx]) > max_gap:
                continue
            # Flatness gate: a sustained burst with steady voice-band energy is
            # background noise (hum, room tone), not a mistimed word. Only the
            # long ones matter and only they justify the measurement cost.
            if (video is not None and pb - pa >= ENERGY_NET_FLAT_MIN_DUR
                    and burst_rms_std(video, pa, pb) < ENERGY_NET_FLAT_STD_DB):
                continue
            rescued += 1
            blocks[idx]["start"] = min(blocks[idx]["start"], pa)
            blocks[idx]["end"] = max(blocks[idx]["end"], pb)

    if not rescued:
        return keep, 0
    blocks.sort(key=lambda b: b["start"])
    merged = [blocks[0]]
    for b in blocks[1:]:
        if b["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], b["end"])
        else:
            merged.append(b)
    return merged, rescued


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


def guard_two_sided_rescues(base_keep: list[dict], net_keep: list[dict],
                            min_gap: float) -> tuple[list[dict], list[dict]]:
    """Revert energy-net rescues that bridged a long word-gap from BOTH sides.

    build_keep_blocks splits on word-coverage gaps; a legitimate mistiming rescue
    (capped tail OR late onset) is one-sided and leaves a real cut on the other
    side. Background noise filling a pause is the only case that gets rescued from
    BOTH bounding blocks at once, collapsing a gap the word split had cut. Such a
    gap (longer than min_gap = the gap-cut threshold, and covered by the net past
    BOTH of its base edges) is cut straight back to the build_keep_blocks edges.
    Only ever reverts to base edges, so no word-covered audio is ever cut.

    Coverage-based (not block-identity), so it stays correct even if the rescues
    merged two base blocks into one that spans the whole gap.

    Returns (new_keep, reverted_cuts).
    """
    cuts = []
    for a, b in zip(base_keep, base_keep[1:]):
        gL, gR = a["end"], b["start"]
        if gR - gL < min_gap:
            continue
        covers_left = any(k["start"] <= gL + 1e-6 < k["end"] for k in net_keep)
        covers_right = any(k["start"] < gR - 1e-6 <= k["end"] for k in net_keep)
        if covers_left and covers_right:
            cuts.append({"start": gL, "end": gR})
    if not cuts:
        return net_keep, cuts
    return subtract_intervals(net_keep, cuts), cuts


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

    silence_db = resolve_silence_db()
    print(f"  silence threshold: {silence_db:.1f} dB")
    silences = detect_silences(combined, total_duration, silence_db)

    words, dropped_silent = drop_silent_words(words, silences)
    if dropped_silent:
        detail = ", ".join(f"{w['word'].strip()!r}@{w['start']:.2f}s"
                           for w in dropped_silent)
        print(f"  dropped {len(dropped_silent)} word(s) stranded in silence "
              f"(whisper hallucination): {detail}")

    base_keep = build_keep_blocks(words, silences, total_duration)
    print(f"  Keep blocks (word coverage, edges snapped to silence): "
          f"{len(base_keep)} ({_sum_duration(base_keep):.1f}s)")

    # Energy safety net: rescue real speech whisper mistimed (late DTW onset,
    # capped word end) — uncovered speech bursts adjacent to a keep edge. Passing
    # the video enables the flatness gate that rejects steady-noise bursts.
    keep, rescued = extend_keeps_to_adjacent_speech([dict(b) for b in base_keep],
                                                    silences, total_duration, combined)
    if rescued:
        print(f"  energy net: rescued {rescued} uncovered speech burst(s) "
              f"→ {len(keep)} blocks ({_sum_duration(keep):.1f}s)")

    # Revert net rescues that bridged a long word-gap from BOTH sides — the
    # signature of intermittent noise filling a pause (a legit mistiming is
    # one-sided). Reverts only to build_keep_blocks edges, never cutting speech.
    keep, two_sided = guard_two_sided_rescues(base_keep, keep, MAX_KEEP_GAP + 2 * KEEP_PAD)
    if two_sided:
        print(f"  reverted {len(two_sided)} two-sided noise rescue(s) "
              f"→ {len(keep)} blocks ({_sum_duration(keep):.1f}s)")

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
    tmp = out_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(edit_plan, f, indent=2, ensure_ascii=False)
    os.replace(tmp, out_path)  # atomic — repo convention (see remotion_sync)

    print(f"\n✅ Done.")
    print(f"   edit_plan.json → {out_path}")
    print(f"   Original:  {total_duration:.1f}s")
    print(f"   Cutting:   {cut_time:.1f}s ({cut_time/total_duration*100:.0f}%)"
          + (f" (incl. {_sum_duration(repetitions):.1f}s repetitions)" if repetitions else ""))
    print(f"   Final:     {kept_time:.1f}s ({kept_time/60:.1f} min)")
    print(f"   Segments to keep: {len(keep)}")


if __name__ == "__main__":
    main()
