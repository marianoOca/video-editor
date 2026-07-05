"""
Manage video-editor projects.

A project is a named workspace under src/data/<name>/ holding one edit's pipeline
intermediates. The pipeline derives the name from the input video (see run_all.py),
and each processed project becomes a permanent Remotion composition (Studio's
sidebar is the project switcher).

Usage:
  python3 project.py list                # list projects; active marked with *
  python3 project.py current             # show the active project + its data dir
  python3 project.py switch <name>       # set the active project (pipeline/render target)
  python3 project.py rebuild             # regenerate Root.tsx from existing snapshots
  python3 project.py delete <name>       # remove a project's data + Remotion composition
"""

import argparse
import json
import os
import sys

# This CLI runs without an active project (list/switch/...); opt out of config's
# missing-project hard error. Must be set before importing config.
os.environ.setdefault("VE_ALLOW_NO_PROJECT", "1")

from config import (
    DATA_ROOT, STATE_FILE, ACTIVE_PROJECT, OUT_DIR, sanitize_project_name,
)
from remotion_sync import snapshot_path, regenerate_root, delete_project, list_projects


def project_mode(name: str) -> str:
    p = DATA_ROOT / name / "mode.json"
    if not p.exists():
        return "-"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("mode", "?")
    except (json.JSONDecodeError, OSError):
        return "?"


def data_projects() -> list[str]:
    if not DATA_ROOT.exists():
        return []
    return sorted(d.name for d in DATA_ROOT.iterdir() if d.is_dir())


def cmd_list(args):
    projects = data_projects()
    if not projects:
        print("No projects yet. Run the pipeline (run_all.py) to create one.")
        return
    print(f"{'':2}{'project':22}{'mode':10}{'composition'}")
    for name in projects:
        mark = "*" if name == ACTIVE_PROJECT else " "
        has_comp = "yes" if snapshot_path(name).exists() else "no"
        print(f"{mark} {name:22}{project_mode(name):10}{has_comp}")


def cmd_current(args):
    if ACTIVE_PROJECT is None:
        print("no active project. Run the pipeline or `project.py switch <name>`.")
        return
    print(f"active project: {ACTIVE_PROJECT}")
    print(f"data dir:       {OUT_DIR}")


def cmd_switch(args):
    name = sanitize_project_name(args.name)
    if not (DATA_ROOT / name).is_dir():
        print(f"ERROR: project '{name}' not found under {DATA_ROOT}.")
        print("Existing projects:", ", ".join(data_projects()) or "(none)")
        sys.exit(1)
    STATE_FILE.write_text(name, encoding="utf-8")
    print(f"switched to '{name}'.")
    if not snapshot_path(name).exists():
        print("  note: no Remotion composition yet — run the pipeline for this project.")


def cmd_rebuild(args):
    regenerate_root()
    print(f"regenerated Root.tsx ({len(list_projects())} composition(s)).")


def cmd_delete(args):
    name = sanitize_project_name(args.name)
    del_input = args.input or args.all
    del_output = args.output or args.all
    # delete_project wipes data dir + snapshot + public assets, clears the
    # active-project state if it pointed here, and regenerates Root.tsx. --input /
    # --output / --all also unlink the project's shared source/rendered files.
    removed = delete_project(name, delete_input=del_input, delete_output=del_output)
    if not removed:
        print(f"project '{name}' not found (nothing to delete).")
        return
    print(f"deleted project '{name}':")
    for path in removed:
        print(f"  - {path}")


def main():
    parser = argparse.ArgumentParser(description="Manage video-editor projects.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List projects").set_defaults(func=cmd_list)
    sub.add_parser("current", help="Show the active project").set_defaults(func=cmd_current)
    sub.add_parser("rebuild", help="Regenerate Root.tsx from snapshots").set_defaults(func=cmd_rebuild)

    p_switch = sub.add_parser("switch", help="Set the active project")
    p_switch.add_argument("name")
    p_switch.set_defaults(func=cmd_switch)

    p_delete = sub.add_parser("delete", help="Delete a project")
    p_delete.add_argument("name")
    p_delete.add_argument("--input", action="store_true",
                          help="Also delete the project's source video(s) under input/")
    p_delete.add_argument("--output", action="store_true",
                          help="Also delete the project's rendered output(s)")
    p_delete.add_argument("--all", action="store_true",
                          help="Also delete both source and output files")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
