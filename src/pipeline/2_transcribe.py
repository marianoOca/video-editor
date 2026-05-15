"""
Step 2: Transcribe combined.mp4 with Whisper.
Outputs word-level timestamps in out/transcript.json.

Usage: python3 2_transcribe.py [--model small] [--lang es]
"""

import subprocess
import json
import sys
import argparse
from pathlib import Path
from config import OUT_DIR


def extract_audio(video: Path, audio: Path):
    print("  extracting audio...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-ar", "16000", "-ac", "1", "-f", "wav", str(audio)],
        check=True, stderr=subprocess.DEVNULL
    )


def run_whisper(audio: Path, model: str, lang: str) -> dict:
    print(f"  running whisper (model={model}, lang={lang})...")
    result = subprocess.run(
        [
            "whisper", str(audio),
            "--model", model,
            "--language", lang,
            "--output_format", "json",
            "--word_timestamps", "True",
            "--output_dir", str(OUT_DIR),
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("Whisper error:")
        print(result.stderr)
        sys.exit(1)

    json_path = OUT_DIR / (audio.stem + ".json")
    with open(json_path) as f:
        return json.load(f)


def flatten_segments(raw: dict) -> list[dict]:
    """Convert Whisper output to flat list of segments with start/end/text."""
    segments = []
    for seg in raw.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
            "words": [
                {"word": w["word"].strip(), "start": w["start"], "end": w["end"]}
                for w in seg.get("words", [])
            ],
        })
    return segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small", help="Whisper model: tiny/base/small/medium/large")
    parser.add_argument("--lang", default="es", help="Language code (es, en, ...)")
    args = parser.parse_args()

    combined = OUT_DIR / "combined.mp4"
    if not combined.exists():
        print("ERROR: src/data/combined.mp4 not found. Run 1_normalize.py first.")
        sys.exit(1)

    audio = OUT_DIR / "audio.wav"
    extract_audio(combined, audio)

    raw = run_whisper(audio, args.model, args.lang)
    segments = flatten_segments(raw)

    transcript = {
        "model": args.model,
        "language": args.lang,
        "segments": segments,
        "full_text": " ".join(s["text"] for s in segments),
    }

    out_path = OUT_DIR / "transcript.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done.")
    print(f"   transcript.json → {out_path}")
    print(f"   Segments: {len(segments)}")
    print(f"\n--- Preview (first 3 segments) ---")
    for s in segments[:3]:
        print(f"  [{s['start']:.1f}s – {s['end']:.1f}s] {s['text']}")


if __name__ == "__main__":
    main()
