"""
Step 3: Detect silences (ffmpeg) + noise gaps (transcript) + repetitions (Claude).
Outputs out/edit_plan.json with the list of segments to KEEP.

Usage: python3 3_analyze.py
"""

import subprocess
import json
import re
import sys
from pathlib import Path
from config import OUT_DIR, get_duration

SILENCE_THRESHOLD_DB = -25   # dB — aggressive: catches room tone and low background noise
SILENCE_MIN_DURATION = 0.25  # seconds — catch short silences too

NOISE_MIN_GAP = 0.20         # seconds — inter-word gaps longer than this are treated as non-speech audio
NOISE_MARGIN = 0.18          # seconds — padding before/after each cut so speech endings aren't clipped

REPETITION_CHUNK_SEC = 180   # seconds of speech per Claude request when transcript is long
REPETITION_CHUNK_OVERLAP = 10  # seconds of overlap between consecutive chunks


def detect_silences(video: Path) -> list[dict]:
    """Run ffmpeg silencedetect and return list of {start, end} silence intervals."""
    print("  detecting silences...")
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video),
            "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={SILENCE_MIN_DURATION}",
            "-f", "null", "-"
        ],
        capture_output=True, text=True
    )
    output = result.stderr

    silences = []
    starts = re.findall(r"silence_start: ([\d.]+)", output)
    ends = re.findall(r"silence_end: ([\d.]+)", output)

    for s, e in zip(starts, ends):
        silences.append({"start": float(s), "end": float(e)})

    return silences


def flatten_words(transcript: dict) -> list[dict]:
    """Return all words from all segments, sorted by start time."""
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []) or []:
            if "start" in w and "end" in w:
                words.append(w)
    words.sort(key=lambda w: w["start"])
    return words


def detect_noise_gaps(transcript: dict, min_gap: float = NOISE_MIN_GAP) -> list[dict]:
    """
    Find inter-word gaps long enough to be non-speech audio.
    silencedetect already catches true silence; remaining gaps with audio
    are likely noises (coughs, breaths, lip smacks, throat clears) since
    Whisper would have transcribed actual speech.
    Returns list of {start, end} intervals.
    """
    print("  detecting noise gaps...")
    words = flatten_words(transcript)
    gaps = []
    for prev, nxt in zip(words, words[1:]):
        gap = nxt["start"] - prev["end"]
        if gap >= min_gap:
            gaps.append({
                "start": prev["end"] + NOISE_MARGIN,
                "end": nxt["start"] - NOISE_MARGIN,
            })
    return [g for g in gaps if g["end"] > g["start"]]


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


def _call_claude_for_repetitions(prompt: str) -> list[dict]:
    """Run the Claude CLI with the given prompt and parse a {repetitions: [...]} JSON response."""
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"  WARNING: claude CLI error: {result.stderr[:200]}")
        return []

    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.stdout.strip())
    try:
        return json.loads(raw).get("repetitions", [])
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  WARNING: could not parse response: {e}")
        print(f"  Raw: {raw[:300]}")
        return []


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


def find_repetitions(transcript: dict) -> list[dict]:
    """
    Use the claude CLI to identify retake/repetition segments.
    Feeds word-level data so cut boundaries align with word edges.
    For long transcripts, chunks the input with overlap and merges results.
    """
    print("  asking Claude to identify repetitions...")
    words = flatten_words(transcript)
    if not words:
        return []

    total_span = words[-1]["end"] - words[0]["start"]

    if total_span <= REPETITION_CHUNK_SEC:
        prompt = _build_repetition_prompt(_format_words_for_prompt(words))
        return _call_claude_for_repetitions(prompt)

    # Chunked path for long transcripts: overlap chunks so a repetition that
    # straddles a boundary still gets seen as a contiguous group in one chunk.
    all_reps: list[dict] = []
    chunk_start = words[0]["start"]
    end_time = words[-1]["end"]
    while chunk_start < end_time:
        chunk_end = chunk_start + REPETITION_CHUNK_SEC
        chunk_words = [w for w in words if w["start"] >= chunk_start and w["end"] <= chunk_end]
        if chunk_words:
            prompt = _build_repetition_prompt(_format_words_for_prompt(chunk_words))
            all_reps.extend(_call_claude_for_repetitions(prompt))
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


def merge_cuts(
    silences: list[dict],
    repetitions: list[dict],
    noises: list[dict],
    margin: float = 0.1,
) -> list[dict]:
    """Merge silence, noise, and repetition intervals into a single sorted list of cuts."""
    cuts = []
    for s in silences:
        cuts.append({"start": s["start"] + margin, "end": s["end"] - margin, "type": "silence"})
    for n in noises:
        cuts.append({"start": n["start"], "end": n["end"], "type": "noise"})
    for r in repetitions:
        cuts.append({"start": r["start"], "end": r["end"], "type": "repetition",
                     "reason": r.get("reason", "")})

    cuts = [c for c in cuts if c["end"] > c["start"]]
    cuts.sort(key=lambda x: x["start"])

    merged = []
    for cut in cuts:
        if merged and cut["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], cut["end"])
            merged[-1]["type"] = "merged"
        else:
            merged.append(cut)
    return merged


def cuts_to_keep(cuts: list[dict], total_duration: float, min_segment: float = 0.2) -> list[dict]:
    """Invert cuts to get the segments to KEEP."""
    keep = []
    cursor = 0.0
    for cut in cuts:
        if cut["start"] - cursor >= min_segment:
            keep.append({"start": cursor, "end": cut["start"]})
        cursor = cut["end"]
    if total_duration - cursor >= min_segment:
        keep.append({"start": cursor, "end": total_duration})
    return keep


def _sum_duration(items: list[dict]) -> float:
    return sum(i["end"] - i["start"] for i in items)


def main():
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

    silences = detect_silences(combined)
    print(f"  Found {len(silences)} silence intervals ({_sum_duration(silences):.1f}s)")

    noises = detect_noise_gaps(transcript)
    print(f"  Found {len(noises)} noise gaps ({_sum_duration(noises):.1f}s)")

    repetitions = find_repetitions(transcript)
    print(f"  Found {len(repetitions)} repetition intervals ({_sum_duration(repetitions):.1f}s)")

    cuts = merge_cuts(silences, repetitions, noises)
    keep = cuts_to_keep(cuts, total_duration)

    cut_time = _sum_duration(cuts)
    saved = total_duration - cut_time

    edit_plan = {
        "total_duration": total_duration,
        "final_duration": saved,
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
    print(f"   Cutting:   {cut_time:.1f}s "
          f"(silences {_sum_duration(silences):.1f}s, "
          f"noises {_sum_duration(noises):.1f}s, "
          f"repetitions {_sum_duration(repetitions):.1f}s, "
          f"{cut_time/total_duration*100:.0f}% total)")
    print(f"   Final:     {saved:.1f}s ({saved/60:.1f} min)")
    print(f"   Segments to keep: {len(keep)}")


if __name__ == "__main__":
    main()
