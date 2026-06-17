"""
Multi-tenant Remotion store.

Each processed project becomes a permanent Remotion composition. Per project we keep:
  - src/remotion/public/projects/<name>/edited.mp4   (the cut video, served by staticFile)
  - src/remotion/public/projects/<name>/images/*     (overlay images)
  - src/remotion/src/projects/<name>.json            (render-ready snapshot)

Root.tsx is regenerated from the set of snapshot files: one <Composition> per
project, grouped under a Studio <Folder> so the sidebar acts as the project
switcher. Composition props (duration/dims/captions/overlays) are read from the
imported snapshot json, so editing a snapshot updates that project without
regenerating Root.tsx — regeneration is only needed when the SET of projects
changes (a project is added or removed).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from config import REMOTION_DIR


def snapshot_dir(remotion_dir: Path = REMOTION_DIR) -> Path:
    return remotion_dir / "src" / "projects"


def snapshot_path(name: str, remotion_dir: Path = REMOTION_DIR) -> Path:
    return snapshot_dir(remotion_dir) / f"{name}.json"


def public_project_dir(name: str, remotion_dir: Path = REMOTION_DIR) -> Path:
    return remotion_dir / "public" / "projects" / name


def read_snapshot(name: str, remotion_dir: Path = REMOTION_DIR) -> dict | None:
    p = snapshot_path(name, remotion_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_project_snapshot(
    name: str,
    *,
    edited_mp4: Path,
    captions: list[dict],
    title_cards: list[dict],
    image_overlays: list[dict],
    width: int,
    height: int,
    fps: int,
    duration_frames: int,
    captions_enabled: bool = False,
    video_version: int = 0,
    image_files: list[Path] = (),
    remotion_dir: Path = REMOTION_DIR,
) -> dict:
    """Copy the project's assets into public/projects/<name>/ and write its
    render-ready snapshot json. Returns the snapshot dict."""
    pub = public_project_dir(name, remotion_dir)
    pub.mkdir(parents=True, exist_ok=True)

    # Video
    shutil.copy2(edited_mp4, pub / "edited.mp4")

    # Images — clear stale ones first so a previous render's overlays can't leak in.
    images_dest = pub / "images"
    if images_dest.exists():
        shutil.rmtree(images_dest)
    if image_files:
        images_dest.mkdir(parents=True, exist_ok=True)
        for img in image_files:
            shutil.copy2(img, images_dest / Path(img).name)

    snapshot = {
        "name": name,
        "durationInFrames": duration_frames,
        "width": width,
        "height": height,
        "fps": fps,
        "videoSrc": f"projects/{name}/edited.mp4",
        "videoVersion": video_version,
        "imageOverlays": image_overlays,
        "captions": captions,
        "captionsEnabled": captions_enabled,
        "titleCards": title_cards,
    }
    snap_path = snapshot_path(name, remotion_dir)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot


def update_snapshot(name: str, remotion_dir: Path = REMOTION_DIR, **fields) -> dict:
    """Merge fields into an existing snapshot json (no asset copy). Used by step
    4b to attach image overlays after step 4 wrote the base snapshot."""
    snap = read_snapshot(name, remotion_dir)
    if snap is None:
        raise FileNotFoundError(
            f"snapshot for project '{name}' not found — run step 4 (4_render.py) first"
        )
    snap.update(fields)
    snapshot_path(name, remotion_dir).write_text(
        json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return snap


def attach_images(
    name: str,
    image_overlays: list[dict],
    image_files: list[Path],
    remotion_dir: Path = REMOTION_DIR,
) -> dict:
    """Copy overlay images into public/projects/<name>/images/ and record the
    overlay plan in the snapshot. Called by step 4b."""
    images_dest = public_project_dir(name, remotion_dir) / "images"
    if images_dest.exists():
        shutil.rmtree(images_dest)
    if image_files:
        images_dest.mkdir(parents=True, exist_ok=True)
        for img in image_files:
            shutil.copy2(img, images_dest / Path(img).name)
    return update_snapshot(name, remotion_dir=remotion_dir, imageOverlays=image_overlays)


def list_projects(remotion_dir: Path = REMOTION_DIR) -> list[str]:
    d = snapshot_dir(remotion_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def regenerate_root(remotion_dir: Path = REMOTION_DIR) -> None:
    """Rewrite Root.tsx to register one <Composition> per snapshot json, grouped
    under a Studio <Folder>. Values are read from the imported snapshots."""
    names = list_projects(remotion_dir)

    imports = "\n".join(
        f'import proj{i} from "./projects/{name}.json";' for i, name in enumerate(names)
    )
    array_items = ", ".join(f"proj{i}" for i in range(len(names)))

    root_tsx = f'''import {{ Composition, Folder }} from "remotion";
import {{ VideoComposition }} from "./Composition";
import {{ compositionSchema, type CompositionProps }} from "./schema";
{imports}

type ProjectSnapshot = CompositionProps & {{
  name: string;
  durationInFrames: number;
  width: number;
  height: number;
  fps: number;
}};

const PROJECTS: ProjectSnapshot[] = [{array_items}] as unknown as ProjectSnapshot[];

export const RemotionRoot: React.FC = () => {{
  return (
    <Folder name="Projects">
      {{PROJECTS.map((p) => (
        <Composition
          key={{p.name}}
          id={{p.name}}
          calculateMetadata={{() => ({{ defaultOutName: `${{p.name}}-edited` }})}}
          component={{VideoComposition}}
          durationInFrames={{p.durationInFrames}}
          fps={{p.fps}}
          width={{p.width}}
          height={{p.height}}
          schema={{compositionSchema}}
          defaultProps={{{{
            project: p.name,
            videoSrc: p.videoSrc,
            videoVersion: p.videoVersion ?? 0,
            imageOverlays: p.imageOverlays,
            captions: p.captions,
            captionsEnabled: p.captionsEnabled ?? false,
            titleCards: p.titleCards,
          }}}}
        />
      ))}}
    </Folder>
  );
}};
'''
    (remotion_dir / "src" / "Root.tsx").write_text(root_tsx, encoding="utf-8")


def delete_project(name: str, remotion_dir: Path = REMOTION_DIR) -> None:
    """Remove a project's Remotion snapshot + public assets, then regenerate Root."""
    snap = snapshot_path(name, remotion_dir)
    if snap.exists():
        snap.unlink()
    pub = public_project_dir(name, remotion_dir)
    if pub.exists():
        shutil.rmtree(pub)
    regenerate_root(remotion_dir)
