# SETUP — Video Editor with Remotion + Claude Code

> **For Claude Code:** this file is the installation plan for this project after **cloning the repo from GitHub**. Read it top to bottom and execute the steps in order. At the end, report to the user what was installed and what failed (if anything failed).

> **Paths:** every command below assumes the repo root is in `$REPO`. Set it once from inside the cloned repo:
> ```bash
> REPO="$(git rev-parse --show-toplevel)"
> ```
> Then all `cd "$REPO/..."` commands work on any machine.

---

## 1. Project context

This is a mini automated video editing system. The end goal: with a single natural-language command — "edit this video for Reels" — Claude Code runs the following on a raw video:

1. **Automatic subtitles** synced with audio.
2. **Translation**: English audio → Spanish subtitles.
3. **Cut repeated parts** (second takes / retakes).
4. **Cut silences** and blank spaces.

Everything runs **locally**. Nothing is uploaded to the cloud.

Stack:
- **Remotion** — "video as code" framework in React. The render engine + Studio preview.
- **Claude Code** — agent that runs the pipeline and edits Remotion code from natural-language requests.
- **whisper.cpp / ffmpeg** — transcription, translation, silence analysis.
- **Hyperframes** — motion-graphics overlays (Ken Burns + lower-thirds) in the final render step.

The Remotion project, the custom Studio patches, the pipeline, and the `remotion-best-practices` skill are all **committed to this repo** — a clone already contains them. This SETUP only installs the environment + dependencies that are NOT committed.

---

## 2. System prerequisites (macOS)

You (Claude Code) are already installed and authenticated — that's the precondition for running this file, so it's not a setup step. The pipeline reuses your `claude` auth when it shells out to `claude -p ...` (steps 3/4b); nothing to install or log in.

Verify the remaining tools:

```bash
node -v            # must be ≥ 18
python3 --version  # must be ≥ 3.10
pip3 --version
ffmpeg -version | head -1
```

If any are missing:
- **Node** → `brew install node` (or from nodejs.org)
- **Python** → `brew install python@3.11`
- **ffmpeg** → `brew install ffmpeg`

If Homebrew is not installed: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`

### Python dependency: `requests`

The pipeline is otherwise pure-stdlib + shell-outs, but the motion-graphics step (`5_motion_graphics.py`) imports `requests` (it POSTs the composition to the Hyperframes producer). There is no `requirements.txt` — install the one package:

```bash
pip3 install requests
```

**macOS PEP 668:** if this fails with `externally-managed-environment`, append `--break-system-packages`:
```bash
pip3 install requests --break-system-packages
```

---

## 3. Create working folders

These folders are git-ignored, so a fresh clone does **not** include them. Create them empty once — the user drops their own files in later:

```bash
mkdir -p "$REPO/input" "$REPO/input/images" "$REPO/output" "$REPO/resources"
```

- `input/` — raw `.mp4` videos to edit. **Required** (the pipeline reads from here).
- `output/` — final rendered videos land here. **Required.**
- `input/images/` — **optional**: drop images here for overlays; the image step self-skips if empty.
- `resources/` — **optional**: manual staging for shared assets (logos, etc.). Not consumed automatically by any pipeline step — assets are wired into Remotion components by hand.

---

## 4. Install the Remotion project

The Remotion project is already committed at `$REPO/src/remotion/`. Do **not** run `create-video` — that would scaffold a vanilla project over the real one. The only step is installing node modules:

```bash
cd "$REPO/src/remotion"
npm install          # or: npm ci  (exact reproducible install from the committed package-lock.json)
```

`postinstall` runs automatically: `patch-package && rm -rf node_modules/.cache/webpack`. This applies the two committed Studio patches and clears the webpack cache so the patched bundle is served.

**Version pinning:** Remotion + all `@remotion/*` packages are pinned to **4.0.478** (see `package.json`; `package-lock.json` is committed so the install is reproducible). The shipped patches are `patches/@remotion+studio+4.0.478.patch` and `patches/@remotion+studio-server+4.0.478.patch`. If npm ever resolves a different Remotion version, `patch-package` fails to apply — re-pin to 4.0.478, or re-make the patches (see `CLAUDE.md`).

---

## 5. Verify the Remotion skill

The `remotion-best-practices` skill is committed in-tree — do **not** `npx skills add`. Confirm the symlink resolves:

```bash
ls -l "$REPO/.claude/skills/remotion-best-practices"
# → symlink to ../.agents/skills/remotion-best-practices  (source of truth)
```

Then verify Claude Code sees it (restart the session if it was open before the clone):
```
/remotion-best-practices
```

---

## 6. Custom Studio chrome + dev workflow

The clone ships custom Studio features a vanilla Remotion project does not have. They are applied automatically by the `postinstall` from step 4. Two `patch-package` patches in `src/remotion/patches/`:

- `@remotion+studio+4.0.478.patch` (frontend bundle) — adds the **Subtitles tab** to the right sidebar, and turns the left **Compositions** panel into a project manager: **+ New project** / **Re-run pipeline** launchers, in-list build progress, plus native **Rename / Duplicate / Delete** dialog copy.
- `@remotion+studio-server+4.0.478.patch` — routes Studio's native **Delete / Rename / Duplicate** to the sidecar (wipe / move / copy the whole project) instead of editing `Root.tsx` source.

Run Studio with **`npm run dev`** (from `src/remotion/`) — it starts Studio **and** the Python **sidecar** (port 9848) together via `concurrently`. The sidecar powers the Subtitles tab's Apply/Fix, the New project / Re-run pipeline background jobs, and Delete/Rename/Duplicate. `npm run dev:studio` runs Studio alone (those features degrade gracefully).

```bash
cd "$REPO/src/remotion"
npm run dev          # Studio (localhost:3000) + sidecar (localhost:9848)
```

Studio opens showing this project's own compositions (one per project under a `Projects` folder), not a vanilla welcome video. Stop with `Ctrl+C` (kills both).

On a Remotion version bump the patches must be re-applied (mechanical) — see `CLAUDE.md` and memory `remotion-studio-custom-tab` / `remotion-native-delete-internals` / `remotion-new-project-launcher`.

---

## 7. Motion-graphics prerequisite (Hyperframes)

The final pipeline step (step 6, `5_motion_graphics.py`) renders Ken Burns image pans + text lower-thirds via **Hyperframes**. It is a **separate global npm package** — not in any `package.json`, so `npm install` does **not** pull it in. Install it globally and run the producer before a full pipeline run:

```bash
npm install -g @hyperframes/cli @hyperframes/producer
npx hyperframes-producer          # stays running, port 9847
```

Step 6 hard-exits if the producer is not reachable on port 9847. If you don't need motion graphics, stop the pipeline earlier with `--until 5` (see step 9) — then Hyperframes isn't required.

---

## 8. whisper.cpp transcription setup

Transcription runs **whisper.cpp locally** (not WhisperX / Python) via `@remotion/install-whisper-cpp`, driven by `src/remotion/scripts/transcribe.mjs`. The build + model live at `$REPO/src/whisper.cpp/` (gitignored → absent on a fresh clone). Nothing to pip-install for transcription; everything runs locally.

The install + model download happen automatically on the **first** `2_transcribe.py` run (the Node script calls `installWhisperCpp` + `downloadWhisperModel`). It needs `node` (≥18) and a C compiler (`make`, present on macOS via Xcode CLT).

### ⚠️ Fresh-clone first-run sequence (the DTW guard)

`@remotion/install-whisper-cpp` builds the **pristine** whisper.cpp v1.5.5, which **crashes** on short trailing windows (`WHISPER_ASSERT ... filter_width < a->ne[2]`). The auto-install builds and transcribes in the same process, so there is no window to patch it beforehand. Expected sequence on a fresh clone:

```bash
# 1. Run transcription once — this auto-clones + builds whisper.cpp (may crash on the DTW assert).
cd "$REPO/src/pipeline"
python3 2_transcribe.py          # (or run the full pipeline; it will reach this step)

# 2. Apply the committed DTW short-window guard, then rebuild.
cd "$REPO/src/whisper.cpp"
grep -q 'n_frames / 2 > 7' whisper.cpp && echo "guard already applied — skip git apply" \
  || git apply "$REPO/src/whisper-dtw-shortwindow-guard.patch"
make main                        # rebuild (~1 min, Apple clang, no cmake)

# 3. Re-run transcription — now stable.
cd "$REPO/src/pipeline"
python3 2_transcribe.py
```

Notes:
- The auto-installer is a **one-time no-op** once `src/whisper.cpp/` + `main` exist — it never rebuilds. After editing/patching whisper sources you **must** manually `make main`; re-running `2_transcribe.py` alone won't pick up source changes.
- `git apply` is not idempotent — the `grep` guard above skips it if the patch is already present (avoids a confusing "patch does not apply").
- A `rm -rf src/whisper.cpp` / reinstall (or `git restore`/`git checkout` inside that checkout) reverts to the pristine, **crashing** binary — re-run the guard steps.
- A whisper.cpp version bump does NOT fix this (the unguarded assert persists to master; ≥1.7.4 breaks the make-only build). Full background in memory `whisper-dtw-assert-fix`.

---

## 9. Run the pipeline

Drop one or more `.mp4` files in `input/`, then run the full pipeline:

```bash
cd "$REPO/src/pipeline"
python3 run_all.py
```

It picks up all `*.mp4` in `input/`, auto-detects mode from the first video (vertical → `reel` with subtitles; landscape → `youtube`), and by default **opens Remotion Studio** at the end for review (never auto-renders — review first).

Common flags:

| Flag | Meaning |
|---|---|
| `--from N` / `--until N` | Run a bounded range of steps 1–6 (e.g. `--from 6` = only motion graphics; `--until 5` = stop before motion graphics) |
| `--mode reel\|youtube` | Force mode instead of auto-detecting |
| `--lang es\|en\|...` | Transcription language (default `es`) |
| `--model large-v3` | Whisper model (tiny/base/small/medium/large-v2/large-v3) |
| `--repetitions` | Also detect + cut retakes via the Claude CLI (step 3) |
| `--render` | Render final MP4 instead of opening Studio (`output/<name>.mp4`) |
| `--input FILE...` | Process only these file(s), merged in order |
| `--project NAME` | Explicit project name (default: first input video's filename stem) |
| `--no-lower-thirds` / `--no-images` | Skip parts of step 6 |

Reminder: a full run reaches step 6 (motion graphics) → the Hyperframes producer from step 7 must be running, or pass `--until 5`.

**Smoke test:** drop a short `.mp4` in `input/`, run `python3 run_all.py --until 4`. It should transcribe, cut, and open Studio showing the edited video with subtitles. If that works, setup is complete.

---

## 10. Quick troubleshooting

- **`ModuleNotFoundError: No module named 'requests'`** (step 6) → `pip3 install requests` (see step 2).
- **Step 6 exits "Hyperframes producer not reachable"** → start it: `npx hyperframes-producer` (step 7), or run with `--until 5`.
- **`externally-managed-environment` on `pip install`** → append `--break-system-packages`, or use `pipx`.
- **`npm run dev` fails with a port error** → `lsof -ti:3000 | xargs kill` (or `:9848` for the sidecar) and retry.
- **Render fails but preview works** → issue with `@remotion/renderer` or ffmpeg, not the code.
- **`patch-package` fails during `npm install`** → the resolved Remotion version isn't 4.0.478 (see step 4); re-pin and reinstall.
- **Step 2 crashes with `WHISPER_ASSERT: filter_width < a->ne[2]`** → the DTW guard wasn't applied; follow the fresh-clone sequence in step 8.

---

## 11. End state — report this to the user

When you finish the installation, report using this template:

```
✅ Installed:
- Node v__, Python __, ffmpeg __
- Python dep: requests
- Remotion project deps at video-editor/src/remotion/ (patches applied)
- Hyperframes producer (global) — running: yes/no
- whisper.cpp built + DTW guard applied: yes/no
- Skill remotion-best-practices (bundled with repo) — verified

⚠️ Pending / attention:
- (anything that failed or is incomplete)

▶️ Suggested next step:
Drop a video in input/ and run:  cd src/pipeline && python3 run_all.py
```

---

## 12. What the pipeline already does

The 4 editing tasks the user wants are **already implemented** in `src/pipeline/`. This setup only prepares the environment.

| Task | Tool | Script |
|---|---|---|
| Subtitles | whisper.cpp DTW token-level timestamps (via `@remotion/install-whisper-cpp`) | `2_transcribe.py` |
| Transcription language | `--lang` sets the source language | `2_transcribe.py` |
| Cut silences | transcript keep-blocks + `ffmpeg silencedetect` gate | `3_analyze.py` |
| Cut repetitions | Claude CLI analyzes transcript | `3_analyze.py` (`--repetitions`) |
| Mode detection (reel vs youtube) | ffprobe on first input video | `1_normalize.py` |
