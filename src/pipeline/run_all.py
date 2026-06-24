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

import os
import subprocess
import sys
import argparse
import shutil
from pathlib import Path
from typing import Optional

PIPELINE = Path(__file__).parent
REPO_INPUT = Path(__file__).resolve().parents[2] / "input"


def derive_project_name(args, input_dir: Path):
    """Project name = the edited video's filename stem. Precedence:
    --project > stem of --input > (only on a fresh run, --from 1) first-alphabetical
    stem of input/*.{mp4,mov}. Returns None to defer to the active state-file
    project — so `--from N` resumes the current project instead of grabbing a
    leftover input video. Returned raw; config sanitizes it to a valid id."""
    if args.project:
        return args.project
    if args.input:
        return Path(args.input).stem
    if args.from_step == 1:
        vids = sorted(
            [*input_dir.glob("*.mp4"), *input_dir.glob("*.mov")],
            key=lambda p: p.name.lower(),
        )
        if vids:
            return vids[0].stem
    return None


def run_step(script: str, extra_args: Optional[list[str]] = None):
    extra_args = extra_args or []
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
                        help="Whisper model (tiny/base/small/medium/large-v2/large-v3)")
    parser.add_argument("--lang", default="es",
                        help="Transcription language (es, en, ...)")
    parser.add_argument("--render", action="store_true",
                        help="Render final MP4 (instead of opening Studio)")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="Disable subtitles (passed to step 4)")
    parser.add_argument("--no-title", action="store_true",
                        help="Disable title cards (passed to step 4)")
    parser.add_argument("--no-lower-thirds", action="store_true",
                        help="Skip text lower-thirds in step 6")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip Ken Burns image overlays in step 6")
    parser.add_argument("--input", default=None,
                        help="Process only this video file (passed to step 1)")
    parser.add_argument("--project", default=None,
                        help="Project name (default: edited video's filename stem)")
    parser.add_argument("--repetitions", action="store_true",
                        help="Also detect and cut retakes via Claude (passed to step 3)")
    parser.add_argument("--output-name", default=None,
                        help="Output filename stem for --render (e.g. prueba1 → output/prueba1.mp4)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't open Studio at the end (regenerate in place; used by the Fix sidecar)")
    parser.add_argument("--until", dest="until_step", type=int, default=6,
                        help="Stop after step N (1-6); pairs with --from to run a bounded range")
    args = parser.parse_args()

    # Resolve the active project BEFORE importing config (config builds its path
    # constants at import time from VE_PROJECT). Subprocess steps inherit the env
    # var; the state file lets later standalone step runs target the same project.
    # A derived name (fresh run) overrides; otherwise config falls back to the
    # state file (resume) and hard-errors if there is no active project at all.
    name = derive_project_name(args, REPO_INPUT)
    if name:
        os.environ["VE_PROJECT"] = name
    from config import get_mode, ACTIVE_PROJECT, STATE_FILE
    STATE_FILE.write_text(ACTIVE_PROJECT, encoding="utf-8")
    print(f"▶ project: {ACTIVE_PROJECT}  (src/data/{ACTIVE_PROJECT}/)")

    motion_extra = []
    if args.no_lower_thirds:
        motion_extra.append("--no-lower-thirds")
    if args.no_images:
        motion_extra.append("--no-images")

    render_extra = ["--render"] if args.render else []
    if args.no_subtitles:
        render_extra.append("--no-subtitles")
    if args.no_title:
        render_extra.append("--no-title")
    # --no-open only matters in the non-render path (--render never opens Studio).
    if args.no_open and not args.render:
        render_extra.append("--no-open")

    normalize_extra = ["--mode", args.mode] if args.mode else []
    if args.input:
        normalize_extra += ["--input", args.input]

    if args.output_name:
        render_extra += ["--output-name", args.output_name]

    steps = [
        (1, "1_normalize.py", normalize_extra),
        (2, "2_transcribe.py", ["--model", args.model, "--lang", args.lang]),
        (3, "3_analyze.py", ["--repetitions"] if args.repetitions else []),
        (4, "4_render.py", render_extra),
        (5, "4b_place_images.py", []),
        (6, "5_motion_graphics.py", motion_extra),
    ]

    # Image overlays + motion graphics are reel-only. youtube videos stay clean
    # (no images, no title cards, no lower-thirds), so skip steps 5–6 there.
    # Mode is known only after step 1 writes data/mode.json.
    for num, script, extra in steps:
        if num < args.from_step or num > args.until_step:
            continue
        if num >= 5:
            mode = get_mode()["mode"]
            if mode == "youtube":
                print(f"\n⏭  Skipping {script} (youtube mode — overlays are reel-only)")
                continue
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
