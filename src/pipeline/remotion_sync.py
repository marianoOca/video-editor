"""
Multi-tenant Remotion store.

Each processed project becomes a permanent Remotion composition. Per project we keep:
  - src/remotion/public/projects/<name>/edited.mp4   (the cut video, served by staticFile)
  - src/remotion/public/projects/<name>/images/*     (overlay images)
  - src/remotion/src/projects/<name>.json            (render-ready snapshot)

Root.tsx discovers projects at runtime via require.context over ./projects/*.json
(one <Composition> per snapshot, grouped under a Studio <Folder> so the sidebar acts
as the project switcher). Root.tsx is therefore CONSTANT — adding or deleting a
snapshot json changes the composition set on the next recompile with no Root.tsx
regeneration needed. Composition props (duration/dims/captions/overlays) are read
from the imported snapshot json, so editing a snapshot updates that project.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from config import REMOTION_DIR, DATA_ROOT, STATE_FILE


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


# Constant Root.tsx. Projects are discovered at runtime from ./projects/*.json via
# webpack require.context (Remotion's bundler is webpack), so this file never bakes
# in a per-project import list: adding or deleting a snapshot json updates the Studio
# sidebar on the next recompile, with no codegen and no dead import to crash the app.
# require.context registers ./projects as a watched dependency, so deleting a json
# triggers a live Fast Refresh and the composition drops from the sidebar. recursive
# is false so sibling dirs (e.g. old scene folders) and .DS_Store are ignored.
ROOT_TSX = '''import { Composition, Folder } from "remotion";
import { VideoComposition } from "./Composition";
import { compositionSchema, type CompositionProps } from "./schema";

type ProjectSnapshot = CompositionProps & {
  name: string;
  durationInFrames: number;
  width: number;
  height: number;
  fps: number;
};

const ctx = (
  require as unknown as {
    context(
      dir: string,
      recursive: boolean,
      regexp: RegExp,
    ): { keys(): string[]; (id: string): unknown };
  }
).context("./projects", false, /\\.json$/);

const PROJECTS: ProjectSnapshot[] = ctx
  .keys()
  .sort()
  .map((key) => ctx(key) as ProjectSnapshot);

export const RemotionRoot: React.FC = () => {
  return (
    <Folder name="Projects">
      {PROJECTS.map((p) => (
        <Composition
          key={p.name}
          id={p.name}
          calculateMetadata={() => ({ defaultOutName: `${p.name}-edited` })}
          component={VideoComposition}
          durationInFrames={p.durationInFrames}
          fps={p.fps}
          width={p.width}
          height={p.height}
          schema={compositionSchema}
          defaultProps={{
            project: p.name,
            videoSrc: p.videoSrc,
            videoVersion: p.videoVersion ?? 0,
            imageOverlays: p.imageOverlays,
            captions: p.captions,
            captionsEnabled: p.captionsEnabled ?? false,
            titleCards: p.titleCards,
          }}
        />
      ))}
    </Folder>
  );
};
'''


def regenerate_root(remotion_dir: Path = REMOTION_DIR) -> None:
    """Write the constant dynamic Root.tsx (see ROOT_TSX). The composition set is
    discovered at runtime via require.context, so this content never varies with the
    project list — kept as a function (not a static file) so a fresh checkout and
    `project.py rebuild` can (re)create it. Callers (steps 4/4b, delete) keep working;
    each call is a harmless idempotent rewrite."""
    (remotion_dir / "src" / "Root.tsx").write_text(ROOT_TSX, encoding="utf-8")


def delete_project(name: str, remotion_dir: Path = REMOTION_DIR) -> list[str]:
    """Remove ALL of a project's files and regenerate Root.tsx. Idempotent — missing
    pieces are skipped. Returns the list of removed paths. Wipes, for project <name>:
      - src/data/<name>/                       (pipeline intermediates)
      - src/remotion/src/projects/<name>.json  (render-ready snapshot)
      - src/remotion/public/projects/<name>/   (edited.mp4 + overlay images)
    Clears the active-project state file if it pointed at <name>."""
    removed: list[str] = []

    data_dir = DATA_ROOT / name
    if data_dir.exists():
        shutil.rmtree(data_dir)
        removed.append(str(data_dir))

    snap = snapshot_path(name, remotion_dir)
    if snap.exists():
        snap.unlink()
        removed.append(str(snap))

    pub = public_project_dir(name, remotion_dir)
    if pub.exists():
        shutil.rmtree(pub)
        removed.append(str(pub))

    if STATE_FILE.exists() and STATE_FILE.read_text(encoding="utf-8").strip() == name:
        STATE_FILE.unlink()

    regenerate_root(remotion_dir)
    return removed
