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
- **Graphify** — skill that indexes the codebase into a graph and saves Claude tokens between sessions.
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
- Recommended: `pipx install graphify` (install pipx first with `brew install pipx`).
- Quick alternative: add `--break-system-packages` to the end of the pip command.

---

## 3. Install Graphify

This is the skill that gives Claude a graph of the codebase to save tokens.

```bash
# Install the package (see note above about --break-system-packages)
pip3 install graphifyy --break-system-packages

# Verify
graphify --version   # should show 0.7.x or higher

# If "graphify: command not found", add to PATH:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Then install the Claude Code integration:

```bash
graphify install --platform claude
```

This does two things:
1. Copies `SKILL.md` to `~/.claude/skills/graphify/SKILL.md`.
2. Creates or updates `~/.claude/CLAUDE.md` with a rule that triggers `/graphify` when needed.

**Verification:** run `cat ~/.claude/CLAUDE.md` and confirm it contains a `# graphify` section.

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

## 6. Index the project with Graphify

Inside Claude Code, with the project open:

```
/graphify .
```

This scans the entire project and generates the `graphify-out/` folder with:
- `graph.json` — the persistent graph
- `graph.html` — interactive visualizer
- `GRAPH_REPORT.md` — human-readable report
- `cache/` — cache for incremental updates

The first run takes a moment. Subsequent runs are incremental and fast.

**Add `graphify-out/` to `.gitignore`** (it's local only, no need to version it):

```bash
cd "/Users/mar/Documents/Claude/Projects/FA Automations/video-editor"
echo "graphify-out/" >> .gitignore
```

---

## 7. Confirm the navigation rule in CLAUDE.md

Open `~/.claude/CLAUDE.md` and make sure this block exists (add it if missing):

```markdown
## Context navigation
When you need to understand the codebase, docs or files in this project:
1. ALWAYS query the graph first: `/graphify query "your question"`
2. Only read raw files if the user explicitly says "read the file" or "look at the raw file"
3. Use `graphify-out/wiki/index.md` as the entry point to navigate the structure
```

This is what enables the real token savings.

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
- Graphify v__ with Claude integration
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
| Subtitles | WhisperX (forced alignment) | ✅ Implemented — `2_transcribe.py` |
| EN→ES translation | WhisperX `translate` mode | Use `--lang es` flag |
| Cut silences | `ffmpeg silencedetect` | ✅ Implemented — `3_analyze.py` |
| Cut repetitions | Claude CLI analyzes transcript | ✅ Implemented — `3_analyze.py` |
| Mode detection (reel vs youtube) | ffprobe on first input video | ✅ Implemented — `1_normalize.py` |

### Install WhisperX (required for step 2)

```bash
pip3 install whisperx --break-system-packages
# First run downloads the wav2vec2 alignment model (~1GB) to ~/.cache/torch.
# Everything runs locally — no audio leaves the machine.
```

The legacy `openai-whisper` package is no longer used by the pipeline. It does not need to be uninstalled.

---

## 11. Quick troubleshooting

- **"externally-managed-environment" on `pip install`** → use `--break-system-packages` or `pipx`.
- **`graphify: command not found`** after installing → `~/.local/bin` missing from PATH (step 3).
- **`npm run dev` fails with port error** → `lsof -ti:3000 | xargs kill` and retry.
- **`/graphify` doesn't respond inside Claude Code** → confirm `~/.claude/CLAUDE.md` has the rule and the skill exists at `~/.claude/skills/graphify/`.
- **Render fails but preview works** → issue with `@remotion/renderer` or ffmpeg, not the code.
- **`npx create-video@latest` hangs** → use the `git clone` fallback from step 4.
