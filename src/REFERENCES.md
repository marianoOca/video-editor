# References

## Examples of good work
- **Title card format** (memory `title_card_format`): reel title — top banner, white bold title + gold uppercase subtitle, fade in/out. Reuse for new reel titles.
- **Editing preferences / guardrails** (memory `editing_preferences`): user's accumulated style rules from past feedback. Read + apply at the start of every project.

## Relevant links
- Remotion docs: https://www.remotion.dev/docs
- Custom Studio tabs (unshipped `registerStudioPanel`): https://github.com/remotion-dev/remotion/issues/7200 — why the Subtitles tab is a patch-package hack.
- Hyperframes (motion graphics, step 5): `@hyperframes/cli` + `@hyperframes/producer`, producer on port 9847.
- Related repo `../editor-pro-max` (separate, not a dependency): only the per-project-composition + Studio `<Folder>` idiom was borrowed. video-editor stays independent.

## Notes
- **Memory index**: `~/.claude/projects/-Users-mar-Documents-Claude-Projects-FA-Automations-video-editor/memory/MEMORY.md` — deep notes live here (Studio custom tab, native delete, whisper fixes, cut architecture, etc.). Check it before re-discovering a gotcha.
- **Key docs**: `src/SETUP.md` (install). Subtitles-tab deep notes: memory `remotion-studio-custom-tab`.
- **Skill**: `remotion-best-practices` — mandatory first step for any Remotion work (see CLAUDE.md).
- **Ports**: sidecar 9848, Hyperframes producer 9847, Studio 3000.