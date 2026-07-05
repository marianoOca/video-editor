# Current Project

## What we are building
Using Remotion as a base, a Python pipeline for automatic editing of YouTube and shorts videos.
**Polishing the YouTube (landscape) path now.** Shorts/reel exists but is not the current focus.

## What good looks like
- Clear code, clear steps.
- Consistent code: configurable parameters live in config files (`config.py`, `config.ts`), never hardcoded.
- Patches to the Remotion app are allowed and encouraged (see Studio tabs, native Delete).
- The knowledge base (CLAUDE.md / CONTEXT.md / REFERENCES.md + memory) is kept current.

## What to avoid
- Don't invest in the shorts pipeline or its steps yet — later.
- No hardcoded magic numbers.
- Don't add to the knowledge base for the sake of it. Only durable, reusable facts. Discard one-off edge cases.

## Current focus
- **YouTube (landscape) path.** Reel/shorts path works but is parked.
- YouTube mode = subtitles built + shown in Subtitles tab, but **not burned into the video** (`captionsEnabled` false by default for youtube).

## Recent changes
- **Re-render queue**: the sidecar now runs heavy jobs (new project, re-run, fix, Subtitles-tab
  re-cut) through ONE FIFO worker instead of refusing when busy — queue several re-renders and they
  run in order. Each project row shows running/`Queued · Nth`/error with an ✕ to cancel a pending one.
  Re-cut is now a background job (reload happens on completion). Details in CLAUDE.md ("Background job
  model") + memory `remotion-rerun-queue`.
- **Per-project manifest** (`data/<project>/manifest.json`, mandatory): records the abs source
  video(s) + rendered output(s) of each project — the map from a project to the shared `input/` and
  `output/` folders. Existing projects were backfilled by hand. Details in CLAUDE.md ("Project
  manifest"). It powers two things:
  - **Delete modal cleanup options**: the native Delete dialog now offers Input / Output / All radios
    (disabled per file existence; default All when both exist, else none). `delete_project` unlinks
    the manifest-recorded source(s) under `input/` and/or the render(s).
  - **Re-run-from-step-1 fix**: previously re-normalize re-globbed all of the shared `input/`,
    rebuilding a project from the wrong videos. Now the sidecar passes the project's own manifest
    inputs as `--input`.

## Known open issues
Pulled from memory `known_issues_pipeline` — verify still live before relying on these.
- **Reel `final.mp4` has no captions** — step 5 (motion graphics) overwrites the step-4 render that carried captions.
- **On-video caption edit writes to a stale `VideoEditor` id** in some paths instead of the active project id.
- ~~Soft trailing speech below -35dB clipped at block ends~~ **FIXED Jul 2026** via adaptive
  silence floor: step 1 measures the noise floor → `silence_db` in mode.json (clamped to
  [-38, -30]) → step 3 uses it (falls back to -35 for projects whose step 1 predates the field).
  "School"-class tails covered; a final ultra-faint "día" tail remains (accepted stretch case).
  Design record + validation numbers: `src/HANDOFF_adaptive_silence_floor.md`.
- ~~Words clipped/skipped at cut boundaries~~ **FIXED Jul 2026** (memory `clipped_words_diagnosis`):
  word extent from sub-token DTW (`evidence` field), energy safety net in step 3 (bounded rescue),
  overlap-rule caption mapping, `…` tokens kept as words, silence-margin on hallucination drop.

## Maintenance
Update this file when the active focus shifts, an issue opens/closes, or a deferred area becomes active.
Keep it short — current state only. Durable "how it works" goes in CLAUDE.md; external pointers go in REFERENCES.md.
