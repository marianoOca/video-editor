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

## Known open issues
Pulled from memory `known_issues_pipeline` — verify still live before relying on these.
- **Reel `final.mp4` has no captions** — step 5 (motion graphics) overwrites the step-4 render that carried captions.
- **On-video caption edit writes to a stale `VideoEditor` id** in some paths instead of the active project id.
- **Soft trailing speech below -35dB gets clipped at block ends** ("School" tail, a final soft
  "día" cut mid-word). Pre-existing; slightly amplified by the voice bandpass in
  `config.silencedetect`. Chosen fix direction: adaptive per-video silence floor — full
  self-contained brief in `src/HANDOFF_adaptive_silence_floor.md` (implement in a clean session).
- ~~Words clipped/skipped at cut boundaries~~ **FIXED Jul 2026** (memory `clipped_words_diagnosis`):
  word extent from sub-token DTW (`evidence` field), energy safety net in step 3 (bounded rescue),
  overlap-rule caption mapping, `…` tokens kept as words, silence-margin on hallucination drop.

## Maintenance
Update this file when the active focus shifts, an issue opens/closes, or a deferred area becomes active.
Keep it short — current state only. Durable "how it works" goes in CLAUDE.md; external pointers go in REFERENCES.md.
