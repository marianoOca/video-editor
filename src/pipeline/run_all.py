"""
Runs the full pipeline end to end.

Steps:
  1  1_normalize.py        normalize + concatenate input videos
  2  2_transcribe.py       transcribe with Whisper
  3  3_analyze.py          detect silences + repetitions
  4  4_render.py           cut video + update Remotion
  5  4b_place_images.py    place images from input/images/ (skipped if folder empty)
  6  5_motion_graphics.py  Ken Burns image overlays + text lower-thirds via Hyperframes

Usage:
  python3 run_all.py                    # full pipeline, opens Remotion Studio at the end
  python3 run_all.py --render           # full pipeline, renders final MP4
  python3 run_all.py --from 2           # skip step 1, start from step 2
  python3 run_all.py --from 6           # only re-run motion graphics
  python3 run_all.py --mode reel        # force reel mode (vertical, subtitles ON)
  python3 run_all.py --mode youtube     # force youtube mode (landscape, subtitles OFF)
  python3 run_all.py --model medium     # use a bigger Whisper model
  python3 run_all.py --lang en          # transcribe in English
  python3 run_all.py --no-lower-thirds  # skip text lower-thirds in step 6
  python3 run_all.py --no-images        # skip Ken Burns image overlays in step 6

Note: step 6 requires Hyperframes producer server running:
  npx hyperframes-producer
"""

import subprocess
import sys
import argparse
import shutil
from pathlib import Path

PIPELINE = Path(__file__).parent


def run_step(script: str, extra_args: list[str] = []):
    print(f"\n{'='*50}")
    print(f"▶  {script}")
    print(f"{'='*50}")
    result = subprocess.run(
        [sys.executable, str(PIPELINE / script), *extra_args]
    )
    if result.returncode != 0:
        print(f"\n❌ {script} failed. Stopping.")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_step", type=int, default=1,
                        help="Start from step N (1-6)")
    parser.add_argument("--mode", choices=["reel", "youtube"], default=None,
                        help="Override auto-detected mode (passed to step 1)")
    parser.add_argument("--model", default="large-v3",
                        help="Whisper model (tiny/base/small/medium/large-v3)")
    parser.add_argument("--lang", default="es",
                        help="Transcription language (es, en, ...)")
    parser.add_argument("--render", action="store_true",
                        help="Render final MP4 (instead of opening Studio)")
    parser.add_argument("--no-lower-thirds", action="store_true",
                        help="Skip text lower-thirds in step 6")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip Ken Burns image overlays in step 6")
    args = parser.parse_args()

    motion_extra = []
    if args.no_lower_thirds:
        motion_extra.append("--no-lower-thirds")
    if args.no_images:
        motion_extra.append("--no-images")

    normalize_extra = ["--mode", args.mode] if args.mode else []

    steps = [
        (1, "1_normalize.py", normalize_extra),
        (2, "2_transcribe.py", ["--model", args.model, "--lang", args.lang]),
        (3, "3_analyze.py", []),
        (4, "4_render.py", ["--render"] if args.render else []),
        (5, "4b_place_images.py", []),
        (6, "5_motion_graphics.py", motion_extra),
    ]

    for num, script, extra in steps:
        if num >= args.from_step:
            run_step(script, extra)

    print("\n🎬 Pipeline complete.")
    # Copy music assets to public folder for Remotion
    src_music = Path(__file__).parent.parent / "src" / "remotion" / "src" / "caro" / ".." / ".." / "assets" / "music"
    dest_music = Path(__file__).parent.parent / "src" / "remotion" / "public" / "music"
    if src_music.is_dir():
        shutil.copytree(src_music, dest_music, dirs_exist_ok=True)
        print(f"✅ Copied music assets to {dest_music}")
    else:
        print("⚠️ No music assets found to copy.")


if __name__ == "__main__":
    main()
