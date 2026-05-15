"""
Step 3: Detect silences (ffmpeg) + identify repetitions (Claude).
Outputs out/edit_plan.json with the list of segments to KEEP.

Usage: python3 3_analyze.py
"""

import subprocess
import json
import re
import sys
from pathlib import Path
from config import OUT_DIR, get_duration

SILENCE_THRESHOLD_DB = -35   # dB — lower = only very quiet parts
SILENCE_MIN_DURATION = 0.4   # seconds — shorter silences are kept


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


def find_repetitions(transcript: dict) -> list[dict]:
    """Use the claude CLI to identify retake/repetition segments."""
    print("  asking Claude to identify repetitions...")

    segments_text = "\n".join(
        f"[{s['start']:.2f}s – {s['end']:.2f}s] {s['text']}"
        for s in transcript["segments"]
    )

    prompt = (
        "You are a professional video editor. Analyze this transcript and detect "
        "'retakes': parts where the speaker stumbles, repeats themselves, or restarts a sentence.\n\n"
        f"TRANSCRIPT:\n{segments_text}\n\n"
        "Reply ONLY with valid JSON, no additional text:\n"
        '{"repetitions": [{"start": <seconds>, "end": <seconds>, "reason": "<brief description>"}]}\n'
        "If there are no repetitions: {\"repetitions\": []}.\n"
        "Include only the failed attempt, not the correct one."
    )

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"  WARNING: claude CLI error: {result.stderr[:200]}")
        return []

    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.stdout.strip())

    try:
        return json.loads(raw)["repetitions"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  WARNING: Could not parse response: {e}")
        print(f"  Raw: {raw[:300]}")
        return []


def merge_cuts(silences: list[dict], repetitions: list[dict], margin: float = 0.1) -> list[dict]:
    """Merge silence and repetition intervals into a single sorted list of cuts."""
    cuts = []
    for s in silences:
        cuts.append({"start": s["start"] + margin, "end": s["end"] - margin, "type": "silence"})
    for r in repetitions:
        cuts.append({"start": r["start"], "end": r["end"], "type": "repetition",
                     "reason": r.get("reason", "")})

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
    print(f"  Found {len(silences)} silence intervals")

    repetitions = find_repetitions(transcript)
    print(f"  Found {len(repetitions)} repetition intervals")

    cuts = merge_cuts(silences, repetitions)
    keep = cuts_to_keep(cuts, total_duration)

    cut_time = sum(c["end"] - c["start"] for c in cuts)
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
    print(f"   Cutting:   {cut_time:.1f}s ({cut_time/total_duration*100:.0f}%)")
    print(f"   Final:     {saved:.1f}s ({saved/60:.1f} min)")
    print(f"   Segments to keep: {len(keep)}")


if __name__ == "__main__":
    main()
