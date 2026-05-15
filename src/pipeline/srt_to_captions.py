"""
Convert edited captions.srt → captions.json for Remotion.

Usage: python3 srt_to_captions.py
"""

import json
import re
from pathlib import Path
from srt_utils import srt_to_ms

CAPTIONS_SRT  = Path(__file__).parent.parent / "src/remotion/public/captions.srt"
CAPTIONS_JSON = Path(__file__).parent.parent / "src/remotion/public/captions.json"


def main():
    text = CAPTIONS_SRT.read_text(encoding="utf-8")
    blocks = re.split(r"\n\n+", text.strip())

    captions = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = re.match(r"(\S+) --> (\S+)", lines[1])
        if not match:
            continue
        start_ms = srt_to_ms(match.group(1))
        end_ms   = srt_to_ms(match.group(2))
        caption_text = " ".join(lines[2:])

        words = caption_text.split()
        if not words:
            continue
        ms_per_word = (end_ms - start_ms) / len(words)
        for i, word in enumerate(words):
            w_start = int(start_ms + i * ms_per_word)
            w_end   = int(start_ms + (i + 1) * ms_per_word)
            captions.append({
                "text": (" " if i > 0 else "") + word,
                "startMs": w_start,
                "endMs": w_end,
                "timestampMs": w_start,
                "confidence": None,
            })

    with open(CAPTIONS_JSON, "w", encoding="utf-8") as f:
        json.dump(captions, f, indent=2, ensure_ascii=False)

    print(f"✅ {len(captions)} words → {CAPTIONS_JSON}")
    print("   Remotion Studio reloads automatically.")


if __name__ == "__main__":
    main()
