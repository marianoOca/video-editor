# SETUP — Video Editor with Remotion + Claude Code

> **For Claude Code:** this file is the installation plan for this project. Read it top to bottom and execute the steps in order. At the end, report to the user what was installed and what failed (if anything failed).

---

## 1. Project context

This is a mini automated video editing system. The end goal is that with a single natural-language command — "edit this video for Reels" — Claude Code does the following on a raw video:

1. **Automatic subtitles** synced with audio.
2. **Translation**: when the video audio is in English, subtitles appear in Spanish.
3. **Cut repeated parts** (second takes, "retakes" where the creator repeats the same sentence).
4. **Cut silences** and blank spaces.

All editing runs **locally** on the user's machine. Nothing is uploaded to the cloud.

The stack is:
- **Remotion** — "video as code" framework in React. It's the render engine.
- **Claude Code** — agent that writes/modifies Remotion code based on natural-language requests.
- **Whisper / ffmpeg** — for transcription, translation, and silence analysis.

In the future this project will be packaged as a distributable skill (probably via a GitHub repo) so non-technical users can install it in their own Claude.

---

## 2. System prerequisites (macOS)

Before starting, verify the user has:

```bash
node -v           # must be ≥ 18
python3 --version # must be ≥ 3.10
pip3 --version
ffmpeg -version | head -1
claude --version  # Claude Code CLI
```

If any are missing:

- **Node** → `brew install node` (or from nodejs.org)
- **Python** → `brew install python@3.11`
- **ffmpeg** → `brew install ffmpeg`
- **Claude Code** → `npm install -g @anthropic-ai/claude-code`

If Homebrew is not installed: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`

**Important for macOS:** the system `pip3` is locked by PEP 668. If `pip install` fails with `externally-managed-environment`, use one of these options:
- Quick alternative: add `--break-system-packages` to the end of the pip command.

---

## 4. Create the Remotion project

The Remotion project lives inside this same folder (`video-editor/`), at `src/remotion/`.

```bash
cd "/Users/mar/Documents/Claude/Projects/FA Automations/video-editor/src"
npx create-video@latest remotion
```

The command is interactive. When asked for a template, **choose `hello-world`** (it's the simplest and sufficient as a base — it gets modified with code later). If asked JavaScript vs TypeScript, **choose TypeScript**.

If the interactive command doesn't work for some reason, manual alternative:

```bash
cd "/Users/mar/Documents/Claude/Projects/FA Automations/video-editor"
git clone --depth 1 https://github.com/remotion-dev/template-helloworld.git remotion
cd remotion
rm -rf .git
npm install
```

**Verification:**
```bash
cd src/remotion
npm run dev
```

Should open Remotion Studio at `http://localhost:3000` showing a welcome video. Stop it with `Ctrl+C` after confirming it starts.

---

## 5. Install the official Remotion skill for Claude Code

```bash
cd "/Users/mar/Documents/Claude/Projects/FA Automations/video-editor/src/remotion"
npx skills add remotion-dev/skills
```

> **Note:** the command above installs the skill bundle called `remotion-dev/skills`. Once installed, Claude Code registers it as the skill `remotion-best-practices`. If `npx skills add` fails, check the Remotion docs or clone the repo manually to `~/.claude/skills/remotion-best-practices/`.

After installing, restart the Claude Code session so it loads the new skill. Verify with:
```
/remotion-best-practices
```
or check that `.claude/skills/remotion-best-practices` resolves correctly (not a broken symlink).

---

## 6. Custom Studio chrome (patch-package) + dev workflow

> Steps 4–5 scaffold a *vanilla* Remotion project. This repo also ships custom Studio features that a fresh `create-video` does NOT include. When **cloning this repo** (vs bootstrapping from scratch), the setup is just `cd src/remotion && npm install` — `postinstall` applies them automatically.

Two `patch-package` patches live in `src/remotion/patches/` and apply on every `npm install`:
- `@remotion+studio+<ver>.patch` — adds the **Subtitles tab** to Studio's sidebar and rewords the **Delete project** dialog (frontend bundle).
- `@remotion+studio-server+<ver>.patch` — makes Studio's native **Delete** wipe the whole project, not just edit source.

Run Studio with **`npm run dev`** (from `src/remotion/`) — it starts Studio **and** the Python **sidecar** (port 9848) together via `concurrently`. The sidecar powers the Subtitles tab's Apply/Fix and the Delete button; `npm run dev:studio` runs Studio alone (those features degrade gracefully). On a Remotion version bump the patches must be re-applied (mechanical) — see `CLAUDE.md` and memory `remotion-studio-custom-tab` / `remotion-native-delete-internals`.

---

## 8. Final smoke test

From Claude Code, inside `video-editor/src/remotion/`, try asking something simple:

> Add a "Hello World" title to the main video with a 30-frame fade-in.

Claude should: query the graph, identify the main component, make the change, and give you a preview at `http://localhost:3000`.

If this works, **setup is complete**.

---

## 9. End state — report this to the user

When you finish the installation, leave a message to the user using this template:

```
✅ Installed:
- Node v__, Python __, ffmpeg __
- Remotion project at video-editor/src/remotion/
- Skill remotion-dev/skills
- Initial graph indexed (__ nodes, __ edges)

⚠️ Pending / attention:
- (anything that failed or is incomplete)

▶️ Suggested next step:
Design the workflow for the 4 tasks (subtitles, EN→ES translation,
silence cuts, repetition cuts).
```

---

## 10. What is NOT included in this setup (we build it later)

To be clear: this setup gets the **environment** ready. The 4 tasks the user actually wants — auto subtitles, English→Spanish translation, repetition cuts, silence cuts — are **already implemented** in the pipeline. See `src/pipeline/` for the current implementation.

| Task | Tool | Status |
|---|---|---|
| Subtitles | whisper.cpp, DTW token-level timestamps (via `@remotion/install-whisper-cpp`) | ✅ Implemented — `2_transcribe.py` |
| Transcription language | `--lang` sets the source language (whisper.cpp's own translate is English-only) | Use `--lang es` flag |
| Cut silences | transcript keep-blocks + `ffmpeg silencedetect` gate | ✅ Implemented — `3_analyze.py` |
| Cut repetitions | Claude CLI analyzes transcript | ✅ Implemented — `3_analyze.py` (`--repetitions`) |
| Mode detection (reel vs youtube) | ffprobe on first input video | ✅ Implemented — `1_normalize.py` |

### whisper.cpp (required for step 2)

Transcription runs **whisper.cpp locally** (not WhisperX / Python) via `@remotion/install-whisper-cpp`, driven by `src/remotion/scripts/transcribe.mjs`. The build + model live at `<repo>/whisper.cpp/`. Everything runs locally — no audio leaves the machine. There is **nothing to pip-install** for transcription.

The install + model download are handled automatically on the first `2_transcribe.py` run (the Node script calls `installWhisperCpp` + `downloadWhisperModel`). It just needs `node` (≥18) and a C compiler (`make`, present on macOS via Xcode CLT).

**Important — the local `whisper.cpp/main` is a hand-patched v1.5.5** (a DTW short-window guard). `@remotion/install-whisper-cpp` installs the **pristine** v1.5.5, which **crashes** on short trailing windows. So on a fresh checkout (or after re-cloning / resetting `whisper.cpp/`), re-apply the guard before transcribing — see §11 troubleshooting and memory `whisper-dtw-assert-fix`. WhisperX / `openai-whisper` are NOT used and need not be installed.

---

## 11. Quick troubleshooting

- **"externally-managed-environment" on `pip install`** → use `--break-system-packages` or `pipx`.
- **`npm run dev` fails with port error** → `lsof -ti:3000 | xargs kill` and retry.
- **Render fails but preview works** → issue with `@remotion/renderer` or ffmpeg, not the code.
- **`npx create-video@latest` hangs** → use the `git clone` fallback from step 4.
- **Step 2 crashes with `WHISPER_ASSERT: whisper.cpp:7003: filter_width < a->ne[2]`** → known whisper.cpp v1.5.5 bug: the experimental DTW token-timestamp pass aborts on a window too short for its median filter (a sub-~150ms final window, e.g. a hallucinated trailing "Gracias ."). **The local `whisper.cpp/main` is a hand-patched v1.5.5** — guarded with `n_frames / 2 > 7` at the single DTW call site in `whisper.cpp/whisper.cpp`. A `rm -rf whisper.cpp` / reinstall (or a `git restore`/`git checkout` inside that checkout) reverts to the pristine, **crashing** binary. To re-apply:
  ```bash
  cd "<repo>/whisper.cpp"
  git apply ../src/whisper-dtw-shortwindow-guard.patch   # the saved diff
  make main                                              # rebuild (~1 min, Apple clang, no cmake)
  ```
  A version bump does NOT fix it (the unguarded assert persists to master, and ≥1.7.4 breaks the `@remotion/install-whisper-cpp` make-only build). Full background in memory `whisper-dtw-assert-fix`.
