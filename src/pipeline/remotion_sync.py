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
import os
import shutil
from pathlib import Path

from config import REMOTION_DIR, DATA_ROOT, STATE_FILE, INPUT_DIR, read_manifest


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via a temp file + os.replace so a watcher never reads a
    half-written file. Studio's bundler watches ./projects/*.json (require.context)
    and Root.tsx; a plain truncate-then-write left a window where a mid-write read
    hit invalid JSON, which failed the whole context module and blanked the
    composition list ("Composition <id> not found"). os.replace is atomic on the
    same filesystem, so the temp file sits in the target's own directory."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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

    # Video — via temp + os.replace: Studio streams this exact path, and an
    # in-place copy lets it read a half-written file mid-publish (torn frames).
    tmp = pub / "edited.mp4.tmp"
    shutil.copy2(edited_mp4, tmp)
    os.replace(tmp, pub / "edited.mp4")

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
    _atomic_write_text(snap_path, json.dumps(snapshot, indent=2, ensure_ascii=False))
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
    _atomic_write_text(
        snapshot_path(name, remotion_dir),
        json.dumps(snap, indent=2, ensure_ascii=False),
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
    `project.py rebuild` can (re)create it. Callers (steps 4/4b, delete) keep working.

    No-op when the content already matches: ROOT_TSX is constant, so a re-cut/re-run
    that rewrites it only churns the bundler (each rewrite = a needless Studio
    recompile, contributing to the re-run flicker). Write only on a real diff."""
    root = remotion_dir / "src" / "Root.tsx"
    if root.exists() and root.read_text(encoding="utf-8") == ROOT_TSX:
        return
    _atomic_write_text(root, ROOT_TSX)


def project_input_files(name: str) -> list[Path]:
    """The project's source videos that still exist AND live under input/. Reads the
    manifest (raises FileNotFoundError if it's missing — a hard invariant). External
    sources (referenced by absolute path outside input/) are deliberately excluded:
    the delete feature only removes "resources at /input"."""
    input_root = INPUT_DIR.resolve()
    files: list[Path] = []
    for raw in read_manifest(name).get("inputs", []):
        try:
            resolved = Path(raw).resolve()
        except OSError:
            continue
        if resolved.exists() and input_root in resolved.parents:
            files.append(resolved)
    return files


def project_output_files(name: str) -> list[Path]:
    """The project's rendered outputs that still exist (from the manifest). Reads the
    manifest (raises FileNotFoundError if it's missing)."""
    files: list[Path] = []
    for raw in read_manifest(name).get("outputs", []):
        p = Path(raw)
        if p.exists():
            files.append(p.resolve())
    return files


def delete_project(name: str, remotion_dir: Path = REMOTION_DIR,
                   delete_input: bool = False, delete_output: bool = False) -> list[str]:
    """Remove ALL of a project's files and regenerate Root.tsx. Idempotent — missing
    pieces are skipped. Returns the list of removed paths. Always wipes, for <name>:
      - src/data/<name>/                       (pipeline intermediates)
      - src/remotion/src/projects/<name>.json  (render-ready snapshot)
      - src/remotion/public/projects/<name>/   (edited.mp4 + overlay images)
    Clears the active-project state file if it pointed at <name>.

    Optional cleanup of the SHARED staging folders (off by default so the CLI/native
    delete keep source footage): delete_input unlinks the project's source video(s)
    under input/, delete_output unlinks its rendered output(s). Both read the manifest
    BEFORE the data dir is wiped (the manifest lives inside it)."""
    removed: list[str] = []

    # Resolve manifest-backed files first — the data dir (which holds manifest.json)
    # is wiped below, so this must happen before the rmtree.
    input_files = project_input_files(name) if delete_input else []
    output_files = project_output_files(name) if delete_output else []

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

    for f in input_files + output_files:
        if f.exists():
            f.unlink()
            removed.append(str(f))

    regenerate_root(remotion_dir)
    return removed


def _assert_free(name: str, remotion_dir: Path = REMOTION_DIR) -> None:
    """Raise FileExistsError if any of a project's three folders already exist."""
    if (
        snapshot_path(name, remotion_dir).exists()
        or public_project_dir(name, remotion_dir).exists()
        or (DATA_ROOT / name).exists()
    ):
        raise FileExistsError(f"project '{name}' already exists")


def duplicate_project(src: str, dst: str, remotion_dir: Path = REMOTION_DIR) -> list[str]:
    """Copy a project under a new name: public assets + pipeline data + snapshot.
    The mirror of delete_project (which wipes all three folders). The data dir is
    copied too so the duplicate is independently re-cuttable via the Subtitles tab
    (4_render.py reads src/data/<project>/). The snapshot's name + videoSrc are
    rewritten to point at <dst>. Refuses if <dst> already exists. Returns the list
    of created paths.

    The Duplicate modal's new dimension/fps/duration fields are intentionally
    ignored: our composition dims derive from the real edited video, so changing
    them here would only desync video/captions. A duplicate is a faithful copy."""
    _assert_free(dst, remotion_dir)
    snap = read_snapshot(src, remotion_dir)
    if snap is None:
        raise FileNotFoundError(f"project '{src}' not found")

    created: list[str] = []

    src_pub = public_project_dir(src, remotion_dir)
    if src_pub.exists():
        dst_pub = public_project_dir(dst, remotion_dir)
        shutil.copytree(src_pub, dst_pub)
        created.append(str(dst_pub))

    src_data = DATA_ROOT / src
    if src_data.exists():
        dst_data = DATA_ROOT / dst
        shutil.copytree(src_data, dst_data)
        created.append(str(dst_data))

    snap["name"] = dst
    snap["videoSrc"] = f"projects/{dst}/edited.mp4"
    dst_snap = snapshot_path(dst, remotion_dir)
    dst_snap.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(dst_snap, json.dumps(snap, indent=2, ensure_ascii=False))
    created.append(str(dst_snap))

    regenerate_root(remotion_dir)
    return created


def rename_project(old: str, new: str, remotion_dir: Path = REMOTION_DIR) -> list[str]:
    """Rename a project: move its public assets + pipeline data, rewrite + move the
    snapshot, and follow the active-project state file if it pointed at <old>. The
    mirror of delete_project, but moving (not removing) the three folders. Refuses
    if <new> already exists. Returns the list of new paths."""
    if old == new:
        raise ValueError("new name equals old name")
    _assert_free(new, remotion_dir)
    snap = read_snapshot(old, remotion_dir)
    if snap is None:
        raise FileNotFoundError(f"project '{old}' not found")

    moved: list[str] = []

    src_pub = public_project_dir(old, remotion_dir)
    if src_pub.exists():
        dst_pub = public_project_dir(new, remotion_dir)
        shutil.move(str(src_pub), str(dst_pub))
        moved.append(str(dst_pub))

    src_data = DATA_ROOT / old
    if src_data.exists():
        dst_data = DATA_ROOT / new
        shutil.move(str(src_data), str(dst_data))
        moved.append(str(dst_data))

    snap["name"] = new
    snap["videoSrc"] = f"projects/{new}/edited.mp4"
    new_snap = snapshot_path(new, remotion_dir)
    new_snap.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(new_snap, json.dumps(snap, indent=2, ensure_ascii=False))
    moved.append(str(new_snap))

    old_snap = snapshot_path(old, remotion_dir)
    if old_snap.exists():
        old_snap.unlink()

    if STATE_FILE.exists() and STATE_FILE.read_text(encoding="utf-8").strip() == old:
        STATE_FILE.write_text(new, encoding="utf-8")

    regenerate_root(remotion_dir)
    return moved
