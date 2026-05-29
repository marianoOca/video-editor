"""
Step 2: Transcribe combined.mp4 with WhisperX.

WhisperX runs faster-whisper for transcription and then performs forced
alignment with wav2vec2 to produce accurate word-level timestamps
(~20-50ms precision). Everything runs locally — no audio leaves the machine.

Outputs word-level timestamps in data/transcript.json.

Usage: python3 2_transcribe.py [--model small] [--lang es]
"""

import subprocess
import json
import os
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


def run_whisperx(audio: Path, model: str, lang: str) -> dict:
    """Run the WhisperX CLI. Returns the parsed JSON output."""
    print(f"  running whisperx (model={model}, lang={lang})...")
    # torch >=2.6 defaults torch.load to weights_only=True, which rejects the
    # omegaconf globals pickled in pyannote's VAD checkpoint. Force the legacy
    # full-unpickle behavior — the model comes from a trusted source.
    env = {**os.environ, "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"}
    result = subprocess.run(
        [
            sys.executable, "-m", "whisperx", str(audio),
            "--model", model,
            "--language", lang,
            "--output_format", "json",
            "--output_dir", str(OUT_DIR),
            "--compute_type", "int8",
        ],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print("WhisperX error:")
        print(result.stderr)
        sys.exit(1)

    json_path = OUT_DIR / (audio.stem + ".json")
    with open(json_path) as f:
        return json.load(f)


def flatten_segments(raw: dict) -> list[dict]:
    """Convert WhisperX output to a flat list of segments with start/end/text.
    WhisperX words also carry a 'score' field that we drop here."""
    segments = []
    total_dropped = 0
    for seg in raw.get("segments", []):
        aligned = []
        for w in seg.get("words", []) or []:
            if "start" in w and "end" in w:
                aligned.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})
            else:
                total_dropped += 1
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
            "words": aligned,
        })
    if total_dropped:
        print(f"  WARNING: dropped {total_dropped} word(s) without alignment timestamps "
              f"(WhisperX forced-alignment failure)")
    return segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="large-v3", help="Whisper model: tiny/base/small/medium/large-v3")
    parser.add_argument("--lang", default="es", help="Language code (es, en, ...)")
    args = parser.parse_args()

    combined = OUT_DIR / "combined.mp4"
    if not combined.exists():
        print("ERROR: data/combined.mp4 not found. Run 1_normalize.py first.")
        sys.exit(1)

    audio = OUT_DIR / "audio.wav"
    extract_audio(combined, audio)

    raw = run_whisperx(audio, args.model, args.lang)
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
