"""
Step 5: Render motion graphics overlay via Hyperframes and composite onto edited video.

Reads:
  data/image_plan.json   — image overlays with timing + position
  data/edit_plan.json    — kept segments with transcript text (for lower-thirds)

Outputs:
  data/graphics_overlay.webm  — transparent WebM with all motion graphics
  output/final.mp4            — edited video + motion graphics composited

Requires Hyperframes producer server running:
  npx hyperframes-producer   (default port 9847)

Usage:
  python3 5_motion_graphics.py
  python3 5_motion_graphics.py --no-lower-thirds   # skip text lower-thirds
  python3 5_motion_graphics.py --no-images         # skip image Ken Burns
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from config import (
    HYPERFRAMES_PORT,
    OUT_DIR,
    OUTPUT_DIR,
    get_duration,
    IMAGES_DIR,
    VIDEO_W_PX,
    VIDEO_H_PX,
    VIDEO_FPS,
    IMAGE_WIDTH_FRAC,
    FFMPEG_X264_FAST_ARGS,
)


# ---------------------------------------------------------------------------
# Hyperframes server health check
# ---------------------------------------------------------------------------

def check_server():
    url = f"http://localhost:{HYPERFRAMES_PORT}/health"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
    except Exception as e:
        print(
            f"ERROR: Hyperframes producer not reachable at {url}.\n"
            f"  Start it with: npx hyperframes-producer\n"
            f"  Error: {e}"
        )
        sys.exit(1)
    print(f"  Hyperframes server OK ({url})")


# ---------------------------------------------------------------------------
# Claude: decide lower-thirds from transcript
# ---------------------------------------------------------------------------

def ask_claude_lower_thirds(edit_plan: list[dict]) -> list[dict]:
    """Ask Claude which segments deserve a text lower-third and what to say."""
    print("  asking Claude for lower-third suggestions...")

    segments_text = "\n".join(
        f"[{s['start']:.2f}s – {s['end']:.2f}s] {s.get('text', '').strip()}"
        for s in edit_plan
        if s.get("text", "").strip()
    )

    if not segments_text:
        return []

    prompt = (
        "You are a motion graphics designer for short-form vertical video (TikTok/Reels).\n"
        "Given this transcript of kept segments, decide which moments deserve a text lower-third "
        "(animated text overlay at the bottom of the frame) to highlight a key point or call to action.\n\n"
        f"TRANSCRIPT:\n{segments_text}\n\n"
        "Rules:\n"
        "- Pick 1–4 moments maximum. Quality over quantity.\n"
        "- Lower-thirds should reinforce or emphasize what's being said, not just repeat it verbatim.\n"
        "- Keep text short: main line ≤ 5 words, optional subtext ≤ 6 words.\n"
        "- timestamp_s is start time in the edited video (seconds).\n"
        "- duration_s between 2.0 and 5.0 seconds.\n"
        "- If no moments are worth a lower-third, return empty list.\n"
        "Reply ONLY with valid JSON, no extra text:\n"
        '{"lower_thirds": [{"timestamp_s": <float>, "duration_s": <float>, '
        '"text": "<main line>", "subtext": "<optional, or empty string>"}]}\n'
    )

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=90,
    )

    if result.returncode != 0:
        print(f"  WARNING: claude CLI error: {result.stderr[:200]}")
        return []

    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.stdout.strip())
    try:
        return json.loads(raw).get("lower_thirds", [])
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  WARNING: could not parse lower-thirds response: {e}")
        print(f"  Raw: {raw[:300]}")
        return []


# ---------------------------------------------------------------------------
# Build Hyperframes HTML composition
# ---------------------------------------------------------------------------

def ms_to_s(ms: int) -> float:
    return ms / 1000.0


def build_composition_html(
    image_plan: list[dict],
    lower_thirds: list[dict],
    video_duration_s: float,
    include_images: bool,
    include_lower_thirds: bool,
) -> str:
    """Generate a Hyperframes HTML composition with transparent background."""

    elements: list[str] = []

    # Image overlays — Ken Burns pan + zoom
    if include_images:
        for entry in image_plan:
            fname = entry["file"]
            start_s = ms_to_s(entry["timestamp_ms"])
            end_s = start_s + ms_to_s(entry["duration_ms"])
            x_frac = entry.get("x", 0.6)
            y_frac = entry.get("y", 0.05)

            # Convert 0-1 fractions to pixel positions
            px_x = int(x_frac * VIDEO_W_PX)
            px_y = int(y_frac * VIDEO_H_PX)
            img_w = int(IMAGE_WIDTH_FRAC * VIDEO_W_PX)

            total_frames = int((end_s - start_s) * VIDEO_FPS)
            # Ken Burns: drift 3% right + 2% down, zoom 1.0 → 1.12
            drift_x = int(0.03 * VIDEO_W_PX)
            drift_y = int(0.02 * VIDEO_H_PX)

            keyframes = json.dumps([
                {"frame": 0, "x": px_x, "y": px_y},
                {"frame": total_frames, "x": px_x + drift_x, "y": px_y + drift_y},
            ])
            zoom_keyframes = json.dumps([
                {"frame": 0, "scale": 1.0},
                {"frame": total_frames, "scale": 1.12},
            ])

            elements.append(
                f'  <img src="images/{fname}"\n'
                f'    data-start="{start_s:.3f}" data-end="{end_s:.3f}"\n'
                f'    data-x="{px_x}" data-y="{px_y}"\n'
                f'    data-width="{img_w}"\n'
                f'    data-keyframes=\'{keyframes}\'\n'
                f'    data-zoom-keyframes=\'{zoom_keyframes}\'\n'
                f'    style="border-radius:12px;"/>'
            )

    # Text lower-thirds — slide up from bottom with fade
    if include_lower_thirds:
        for lt in lower_thirds:
            start_s = float(lt["timestamp_s"])
            end_s = start_s + float(lt["duration_s"])
            text = lt.get("text", "").strip()
            subtext = lt.get("subtext", "").strip()

            if not text:
                continue

            total_frames = int((end_s - start_s) * VIDEO_FPS)
            fade_frames = min(9, total_frames // 4)  # ~0.3s fade

            # Slide up: start 60px below final position, land at y=1680 (bottom zone)
            final_y = 1680
            start_y = final_y + 60
            keyframes = json.dumps([
                {"frame": 0, "y": start_y, "opacity": 0},
                {"frame": fade_frames, "y": final_y, "opacity": 1},
                {"frame": total_frames - fade_frames, "y": final_y, "opacity": 1},
                {"frame": total_frames, "y": final_y, "opacity": 0},
            ])

            sub_el = ""
            if subtext:
                sub_el = (
                    f'\n    <p data-font-size="28" data-color="#cccccc" '
                    f'data-font-family="Arial" style="margin:4px 0 0 0;">{subtext}</p>'
                )

            elements.append(
                f'  <div\n'
                f'    data-start="{start_s:.3f}" data-end="{end_s:.3f}"\n'
                f'    data-x="60" data-y="{final_y}"\n'
                f'    data-keyframes=\'{keyframes}\'\n'
                f'    style="background:rgba(0,0,0,0.55);padding:16px 24px;border-radius:8px;'
                f'max-width:960px;">\n'
                f'    <p data-font-size="42" data-font-weight="700" data-color="#ffffff" '
                f'data-font-family="Arial Black" style="margin:0;">{text}</p>{sub_el}\n'
                f'  </div>'
            )

    elements_html = "\n".join(elements) if elements else "  <!-- no graphics -->"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <style>
    body {{ margin: 0; background: transparent; overflow: hidden; }}
  </style>
</head>
<body data-resolution="portrait" data-composition-width="{VIDEO_W_PX}" data-composition-height="{VIDEO_H_PX}">
{elements_html}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Render via Hyperframes HTTP API
# ---------------------------------------------------------------------------

def render_composition(html: str, out_webm: Path):
    print(f"  sending composition to Hyperframes ({len(html)} chars)...")

    payload = {
        "input": {"type": "html", "value": html},
        "output": {
            "width": VIDEO_W_PX,
            "height": VIDEO_H_PX,
            "fps": {"num": VIDEO_FPS, "den": 1},
            "format": "webm",
            "quality": "high",
        },
    }

    url = f"http://localhost:{HYPERFRAMES_PORT}/render/stream"
    with requests.post(url, json=payload, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        output_path = None
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = event.get("status", "")
            if status == "progress":
                frame = event.get("frame", "?")
                total = event.get("totalFrames", "?")
                print(f"\r  rendering frame {frame}/{total}", end="", flush=True)
            elif status == "completed":
                print()
                output_path = event.get("outputPath")
            elif status == "error":
                print(f"\nERROR from Hyperframes: {event.get('message', event)}")
                sys.exit(1)

    if not output_path:
        print("ERROR: Hyperframes did not return an output path.")
        sys.exit(1)

    # Move rendered file to our output location
    shutil.move(output_path, out_webm)
    print(f"  overlay rendered → {out_webm}")


# ---------------------------------------------------------------------------
# ffmpeg composite
# ---------------------------------------------------------------------------

def composite(edited_mp4: Path, overlay_webm: Path, final_mp4: Path):
    print(f"  compositing overlay onto video → {final_mp4}...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(edited_mp4),
            "-i", str(overlay_webm),
            "-filter_complex", "[0:v][1:v]overlay=0:0[v]",
            "-map", "[v]", "-map", "0:a",
            *FFMPEG_X264_FAST_ARGS,
            "-c:a", "copy",
            str(final_mp4),
        ],
        check=True,
    )
    print(f"  done.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-lower-thirds", action="store_true",
                        help="Skip text lower-thirds generation")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip image Ken Burns overlays")
    args = parser.parse_args()

    include_images = not args.no_images
    include_lower_thirds = not args.no_lower_thirds

    print("\nStep 5 — motion graphics")

    # Load inputs
    image_plan_path = OUT_DIR / "image_plan.json"
    edit_plan_path = OUT_DIR / "edit_plan.json"
    edited_mp4 = OUT_DIR / "edited.mp4"
    final_mp4 = OUTPUT_DIR / "final.mp4"
    overlay_webm = OUT_DIR / "graphics_overlay.webm"
    comp_html = OUT_DIR / "composition.html"

    if not edited_mp4.exists():
        print("ERROR: data/edited.mp4 not found. Run 4_render.py first.")
        sys.exit(1)

    image_plan: list[dict] = []
    if image_plan_path.exists() and include_images:
        with open(image_plan_path, encoding="utf-8") as f:
            image_plan = json.load(f)

    edit_plan: list[dict] = []
    if edit_plan_path.exists() and include_lower_thirds:
        with open(edit_plan_path, encoding="utf-8") as f:
            raw = json.load(f)
            # edit_plan.json is {"segments": [...]}
            edit_plan = raw.get("segments", raw) if isinstance(raw, dict) else raw

    has_images = bool(image_plan) and include_images
    has_lower_thirds_source = bool(edit_plan) and include_lower_thirds

    if not has_images and not has_lower_thirds_source:
        print("  No graphics to render. Copying edited.mp4 → output/final.mp4.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edited_mp4, final_mp4)
        print(f"  {final_mp4}")
        return

    # Check Hyperframes server
    check_server()

    # Get lower-thirds from Claude
    lower_thirds: list[dict] = []
    if has_lower_thirds_source:
        lower_thirds = ask_claude_lower_thirds(edit_plan)
        print(f"  {len(lower_thirds)} lower-third(s) planned")

    if not has_images and not lower_thirds:
        print("  Nothing to render. Copying edited.mp4 → output/final.mp4.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edited_mp4, final_mp4)
        print(f"  {final_mp4}")
        return

    video_duration_s = get_duration(edited_mp4)

    # Build and save HTML composition
    html = build_composition_html(
        image_plan=image_plan,
        lower_thirds=lower_thirds,
        video_duration_s=video_duration_s,
        include_images=include_images,
        include_lower_thirds=include_lower_thirds,
    )
    with open(comp_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  composition.html written ({len(html)} chars)")

    # Render overlay
    render_composition(html, overlay_webm)

    # Composite
    composite(edited_mp4, overlay_webm, final_mp4)

    print(f"\nDone. Final video: {final_mp4}")
    if image_plan:
        print(f"  {len(image_plan)} image overlay(s) with Ken Burns")
    if lower_thirds:
        print(f"  {len(lower_thirds)} lower-third(s)")


if __name__ == "__main__":
    main()
