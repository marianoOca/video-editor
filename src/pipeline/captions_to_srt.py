"""
Convert captions.json → captions.srt for easy editing.
Groups words into lines of ~5 words for readability.

Usage: python3 captions_to_srt.py
"""

import json
from pathlib import Path
from srt_utils import ms_to_srt

CAPTIONS_JSON = Path(__file__).parent.parent / "remotion/public/captions.json"
CAPTIONS_SRT  = Path(__file__).parent.parent / "remotion/public/captions.srt"
WORDS_PER_LINE = 5


def main():
    with open(CAPTIONS_JSON, encoding="utf-8") as f:
        words = json.load(f)

    if not words:
        print("⚠️  captions.json is empty — nothing to convert.")
        return

    lines = []
    # Python slice returns fewer elements for the last group — trailing remainder handled implicitly.
    for i in range(0, len(words), WORDS_PER_LINE):
        group = words[i : i + WORDS_PER_LINE]
        lines.append({
            "text": "".join(w["text"] for w in group).strip(),
            "startMs": group[0]["startMs"],
            "endMs":   group[-1]["endMs"],
        })

    srt = ""
    for idx, line in enumerate(lines, 1):
        srt += f"{idx}\n"
        srt += f"{ms_to_srt(line['startMs'])} --> {ms_to_srt(line['endMs'])}\n"
        srt += f"{line['text']}\n\n"

    with open(CAPTIONS_SRT, "w", encoding="utf-8") as f:
        f.write(srt)

    print(f"✅ {len(lines)} lines → {CAPTIONS_SRT}")
    print("   Edit the .srt then run srt_to_captions.py")


if __name__ == "__main__":
    main()
