import React, { useEffect, useRef, useState } from "react";
import type { CaptionSegment } from "../schema";
import { activeCaptionIndex } from "../caption-utils";
import { useProjectBuild } from "./NewProjectLauncher";

// Studio-chrome panel: the selected composition's transcript, grouped by cut,
// with inline editing + a "display on video" toggle. Mounted into Remotion
// Studio's right sidebar via a patch on @remotion/studio's OptionsPanel.js (see
// patches/). The patch passes the raw `defaultProps` + `setDefaultProps` so all
// read/write logic lives here and the patch never needs to change again. UI copy
// is English to match Remotion's native Studio chrome (Props / Renders / Controls).

type SetDefaultProps = (
  updater: (prev: Record<string, unknown>) => Record<string, unknown>,
  opts: { shouldSave: boolean }
) => void;

const msToTimestamp = (ms: number): string => {
  const totalSeconds = Math.max(0, ms) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.floor(totalSeconds % 60);
  const tenths = Math.floor((totalSeconds * 10) % 10);
  return `${minutes.toString().padStart(2, "0")}:${seconds
    .toString()
    .padStart(2, "0")}.${tenths}`;
};

// Editable clock format — humans can't read raw ms. `m:ss.mmm` (hours prepended
// only when needed). Parser is tolerant: `ss`, `ss.mmm`, `m:ss`, `m:ss.mmm`, `h:mm:ss.mmm`.
const msToClock = (ms: number): string => {
  const total = Math.max(0, Math.round(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor((total % 3_600_000) / 60_000);
  const s = Math.floor((total % 60_000) / 1000);
  const millis = total % 1000;
  const ss = s.toString().padStart(2, "0");
  const mmm = millis.toString().padStart(3, "0");
  return h > 0 ? `${h}:${m.toString().padStart(2, "0")}:${ss}.${mmm}` : `${m}:${ss}.${mmm}`;
};

const clockToMs = (raw: string): number | null => {
  const s = raw.trim();
  if (s === "") return null;
  const parts = s.split(":");
  if (parts.length > 3 || parts.some((p) => p.trim() === "")) return null;
  let h = 0;
  let m = 0;
  let sec = 0;
  if (parts.length === 1) {
    sec = parseFloat(parts[0]);
  } else if (parts.length === 2) {
    m = parseInt(parts[0], 10);
    sec = parseFloat(parts[1]);
  } else {
    h = parseInt(parts[0], 10);
    m = parseInt(parts[1], 10);
    sec = parseFloat(parts[2]);
  }
  if (![h, m, sec].every(Number.isFinite) || h < 0 || m < 0 || sec < 0) return null;
  return Math.round(((h * 60 + m) * 60 + sec) * 1000);
};

// Match Remotion Studio chrome (helpers/colors.js + Tabs.js): Arial stack,
// #fff text, #A6A7A9 muted, #0b84f3 accent.
const outer: React.CSSProperties = {
  height: "100%",
  width: "100%",
  display: "flex",
  flexDirection: "column",
  fontFamily: "Arial, Helvetica, sans-serif",
  fontSize: 13,
  lineHeight: 1.4,
  color: "#fff",
};

// Match Studio's Props control bar (Schema|JSON): panel bg + #000 bottom border.
const header: React.CSSProperties = {
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  padding: 12,
  borderBottom: "1px solid #000",
  background: "rgb(31,36,40)",
};

// Same text style as Studio's "Schema | JSON" segments: sans, 15px, muted.
const headerLabel: React.CSSProperties = {
  fontSize: 15,
  color: "#A6A7A9",
  userSelect: "none",
};

const switchTrack = (on: boolean): React.CSSProperties => ({
  width: 38,
  height: 20,
  borderRadius: 10,
  background: on ? "#0b84f3" : "#555",
  position: "relative",
  cursor: "pointer",
  transition: "background 120ms",
  flexShrink: 0,
});

const switchKnob = (on: boolean): React.CSSProperties => ({
  position: "absolute",
  top: 2,
  left: on ? 20 : 2,
  width: 16,
  height: 16,
  borderRadius: "50%",
  background: "#fff",
  transition: "left 120ms",
});

const list: React.CSSProperties = {
  flex: 1,
  overflowY: "scroll", // permanent scrollbar (styled via .ve-cap-list below)
  // Studio's bottom controls bar (Render + video controls) overlays the panel
  // bottom. marginBottom ends the WHOLE scroll box — content AND scrollbar — above
  // it, so neither the last caption nor the scrollbar slips under the bar (mirrors
  // how the video preview stops above it). Use margin, not padding: padding leaves
  // the element full-height so the scrollbar would still run under the bar.
  marginBottom: 39,
};

const emptyState: React.CSSProperties = {
  flex: 1,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
  textAlign: "center",
  color: "#A6A7A9",
  fontStyle: "italic",
};

const cutHeader: React.CSSProperties = {
  top: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "4px 12px",
  fontFamily: "monospace",
  fontSize: 13,
  fontWeight: 400,
  letterSpacing: 0,
  textTransform: "none",
  color: "#A6A7A9",
  background: "#1b1f27",
  borderTop: "1px solid rgba(255,255,255,0.1)",
  borderBottom: "1px solid rgba(255,255,255,0.06)",
  userSelect: "none",
};

const row: React.CSSProperties = {
  display: "flex",
  gap: 10,
  padding: "6px 12px",
  borderBottom: "1px solid rgba(255,255,255,0.07)",
  alignItems: "baseline",
};

// Shared width: the display timestamp slot and the edit-mode left label use the
// SAME width, so the caption text input lands at the exact x of the display text —
// clicking to edit never shifts the text horizontally.
const LEFT_COL = 54;

// Timestamp = click-to-seek; accent blue, pointer.
const timestamp: React.CSSProperties = {
  flexShrink: 0,
  width: LEFT_COL,
  fontVariantNumeric: "tabular-nums",
  fontFamily: "monospace",
  fontSize: 12,
  color: "#0b84f3",
  cursor: "pointer",
  userSelect: "none",
};

// Text = click-to-edit.
const text: React.CSSProperties = {
  flex: 1,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  cursor: "pointer",
};

// ── Inline edit styling ───────────────────────────────────────────────────────
// Edit-mode row. Since the text itself gets no box, the row carries the whole
// "this line is being edited" affordance: a stronger tint + a left accent bar
// (inset box-shadow, like the active-caption highlight). Padding matches the display
// row so row 1 (the text) starts at the same y.
const editRow: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: "6px 12px",
  borderBottom: "1px solid rgba(255,255,255,0.07)",
  background: "rgba(11,132,243,0.12)",
  boxShadow: "inset 3px 0 0 #0b84f3",
};

// One inner line of the edit box. Row 1 = text, row 2 = clocks + split/merge.
const editLine: React.CSSProperties = {
  display: "flex",
  gap: 10,
  alignItems: "center",
};

// Left label fills LEFT_COL (== the display timestamp width) so the text input
// starts at the same x as the display text — no horizontal jump on edit.
// Extra space between a label word and its box — bump this to widen the label→box
// gap. Added as the label's right padding (inside the fixed-width column), so it
// pushes the WORD left without moving the boxes or the caption text.
const LABEL_BOX_GAP = 5;
const editLeftLabel: React.CSSProperties = {
  flexShrink: 0,
  width: LEFT_COL,
  paddingRight: LABEL_BOX_GAP,
  // Right-align so the label word hugs the right edge of its fixed-width column.
  // Otherwise (left-aligned) the trailing empty space differs by word length, so
  // "TEXT" would sit further from its box than "START" — the word→box gap must be
  // equal across all three labels.
  textAlign: "right",
  fontFamily: "monospace",
  fontSize: 12, // match the display timestamp's height
  fontWeight: 600,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: "#A6A7A9",
  userSelect: "none",
};

const inlineLabel: React.CSSProperties = {
  flexShrink: 0,
  paddingRight: LABEL_BOX_GAP,
  fontFamily: "monospace",
  fontSize: 12, // match the display timestamp's height
  fontWeight: 600,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: "#A6A7A9",
  userSelect: "none",
};

// THE RULE: the caption text must occupy the exact same position + size whether the
// line is in display or edit mode. We DO want a visible box here, so to keep the text
// off the box frame without moving it we pad the box AND shift the box left by that
// same padding:
//   • padding-left TEXT_PAD_X gives the text breathing room inside the box;
//   • margin-left -TEXT_PAD_X pulls the whole box left by the same amount, so the
//     text glyph returns to the exact x of the display `text` span (net shift = 0);
//   • the border is drawn with box-shadow (no layout space) so the only offset to
//     cancel is the padding — margin = -padding, exactly;
//   • horizontal padding only — vertical breathing comes from line-height (1.5), so
//     the text's y is untouched;
//   • NO font props → 16px/1.5/Arial from `.css-reset` = the display line's size.
// The box bleeds TEXT_PAD_X left into the 10px label gap (→ 4px clearance).
const TEXT_PAD_X = 6;
const editTextInput: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: `0 ${TEXT_PAD_X}px`,
  margin: `0 0 0 -${TEXT_PAD_X}px`,
  border: "none",
  outline: "none",
  borderRadius: 0, // square, matching the other UI boxes
  background: "#1e1e1e",
  color: "#fff",
  boxShadow: "0 0 0 1px #444", // 1px border look, same color as the clock boxes
};

const editClock: React.CSSProperties = {
  width: 96,
  // Flat 1px border (NO box-shadow) so the boxes look flush, not raised/recessed.
  // A real border sits 1px INSIDE the box, whereas the text box's box-shadow sits
  // OUTSIDE its border-box — so bleed left by TEXT_PAD_X + 1 (the extra border px) to
  // land the visible 1px line AND the value at the same x as the text box. textAlign
  // left so the value never reads as right-shifted. padding "2px Npx" → height 24px
  // (12px line 18 + 4 pad + 2 border) = text box; horizontal gap = TEXT_PAD_X.
  margin: `0 0 0 -${TEXT_PAD_X + 1}px`,
  background: "#1e1e1e",
  border: "1px solid #444",
  borderRadius: 0,
  color: "#fff",
  textAlign: "left",
  padding: `2px ${TEXT_PAD_X}px`,
  fontSize: 12,
  fontFamily: "monospace",
  boxSizing: "border-box",
  outline: "none",
};

const delBtn: React.CSSProperties = {
  flexShrink: 0,
  cursor: "pointer",
  userSelect: "none",
  fontWeight: 700,
  color: "#c0392b",
};

// Apply button styled like a Props SegmentedControl segment: bordered, muted when
// idle (no pending deletions), filled #2f363d + white when there's something to apply.
const applyBtn = (active: boolean, busy: boolean): React.CSSProperties => ({
  border: "1px solid rgba(0,0,0,0.6)",
  background: active ? "#2f363d" : "transparent",
  color: active ? "#fff" : "#A6A7A9",
  fontWeight: 700,
  fontSize: 15,
  letterSpacing: 0.3,
  borderRadius: 0,
  padding: "4px 12px",
  cursor: active && !busy ? "pointer" : "default",
  opacity: busy ? 0.6 : 1,
});

const SIDECAR_BASE = "http://127.0.0.1:9848";
const SIDECAR_URL = `${SIDECAR_BASE}/apply-cuts`;
const HEALTH_URL = `${SIDECAR_BASE}/project-health`;
const FIX_URL = `${SIDECAR_BASE}/fix`;
const SAVE_URL = `${SIDECAR_BASE}/save-captions`;

// Split (`][`) + merge (`⬆ ⬇`) buttons in the edit row. Muted when disabled.
// Sized to the text box height (24px): font 12 line 18 + 4 pad + 2 border.
const toolBtn = (enabled: boolean): React.CSSProperties => ({
  border: "1px solid #444",
  background: "transparent",
  color: enabled ? "#cfd2d4" : "#5a5e62",
  fontWeight: 700,
  fontSize: 12,
  fontFamily: "monospace",
  borderRadius: 0,
  padding: "2px 8px",
  cursor: enabled ? "pointer" : "default",
});

// Caret-driven split point: number of words before the caret, or null if the
// caret isn't on a whitespace boundary with at least one word on each side.
const splitAt = (text: string, caret: number): number | null => {
  if (caret <= 0 || caret >= text.length) return null;
  if (!/\s/.test(text[caret - 1]) && !/\s/.test(text[caret])) return null;
  const before = text.slice(0, caret).split(/\s+/).filter(Boolean);
  const after = text.slice(caret).split(/\s+/).filter(Boolean);
  if (before.length === 0 || after.length === 0) return null;
  return before.length;
};

type Group = {
  cutIndex: number | null;
  rows: { caption: CaptionSegment; absIndex: number }[];
};

// Group consecutive captions sharing the same cutIndex. Captions never cross a
// cut boundary (enforced in the pipeline), so consecutive grouping is exact.
const groupByCut = (captions: CaptionSegment[]): Group[] => {
  const groups: Group[] = [];
  captions.forEach((caption, absIndex) => {
    const ci = typeof caption.cutIndex === "number" ? caption.cutIndex : null;
    const last = groups[groups.length - 1];
    if (last && last.cutIndex === ci) {
      last.rows.push({ caption, absIndex });
    } else {
      groups.push({ cutIndex: ci, rows: [{ caption, absIndex }] });
    }
  });
  return groups;
};

// ── Session-scoped pending-deletion store ────────────────────────────────────
// Pending deletion marks (whole cuts + single lines) are STAGING state: they must
// live for the Studio session and die on a full page reload. A re-cut reloads, so
// a fresh page must NEVER resurrect a mark. A module-level per-project store gives
// exactly that lifetime — it survives a tab switch (Props↔Subtitles unmounts the
// component but the module stays loaded) yet a reload re-evaluates this module and
// starts empty.
//
// This replaces a localStorage mirror. localStorage persisted ACROSS reloads and,
// because marks are keyed by caption ARRAY INDEX, a stale `{lines:[0]}` was
// resurrected onto whatever caption then sat at index 0 after a cut — the "deleted
// line's mark jumps to the next line after Apply" bug, sticky across hard reloads
// (a reload never clears localStorage). We no longer read the old `ve-pending-*`
// keys, so any existing poison is inert.
type Pending = { drops: Set<number>; lines: Set<number> };
const pendingStore = new Map<string, Pending>();
const getPending = (project: string | null): Pending =>
  (project && pendingStore.get(project)) || { drops: new Set(), lines: new Set() };

export const SubtitlesTab: React.FC<{
  defaultProps?: Record<string, unknown>;
  setDefaultProps?: SetDefaultProps;
}> = ({ defaultProps, setDefaultProps }) => {
  const captions = (defaultProps?.captions as CaptionSegment[] | undefined) ?? [];
  const on = Boolean(defaultProps?.captionsEnabled);

  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draftText, setDraftText] = useState("");
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const [draftCaret, setDraftCaret] = useState(0);
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Delete staging (whole cuts + single lines) ──────────────────────────────
  // Pending deletions are PER-PROJECT and must stay independent across projects.
  // Backed by the module-level `pendingStore` (top of file): survives a tab-switch
  // remount but resets on a full page reload. Two lifecycles:
  //   • Tab switch (Props/Renders/Subtitles) unmounts this component → the lazy
  //     useState initializers re-read the store on remount → marks survive.
  //   • Project switch while staying on the Subtitles tab does NOT remount (same
  //     position, props just change), so state must be reset the instant `project`
  //     changes. We do it DURING RENDER (React's "adjust state when a prop changes"
  //     idiom) — synchronous, before any effect runs.
  const project = typeof defaultProps?.project === "string" ? defaultProps.project : null;

  const [pendingDrops, setPendingDrops] = useState<Set<number>>(
    () => new Set(getPending(project).drops)
  );
  const [pendingLines, setPendingLines] = useState<Set<number>>(
    () => new Set(getPending(project).lines)
  );

  // Reset-on-project-switch. Guarded so it fires only when `project` actually
  // changed (no render loop). Reads the NEW project's store entry, so a fresh
  // project starts empty and a revisited one restores its own marks.
  const [trackedProject, setTrackedProject] = useState(project);
  if (project !== trackedProject) {
    setTrackedProject(project);
    setPendingDrops(new Set(getPending(project).drops));
    setPendingLines(new Set(getPending(project).lines));
    setEditingIndex(null);
  }

  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const pendingCount = pendingDrops.size + pendingLines.size;

  // ── Project health (corrupt-file detection + Fix) ───────────────────────────
  const [health, setHealth] = useState<{ corrupt: boolean; resumeStep: number | null } | null>(null);
  // Both Fix and Apply (re-cut) are background jobs now, tracked by the sidebar's
  // in-list progress bar. Lock the tab (no edits, Apply disabled) while THIS project
  // has a job queued or running, so nothing is edited into a snapshot that's about
  // to be re-mapped. `applying` covers the brief window between POST and hand-off.
  const projectBuild = useProjectBuild(project);
  const jobActive =
    !!projectBuild && (projectBuild.state === "running" || projectBuild.state === "queued");
  const busy = applying || jobActive;

  // ── Auto-scroll (follow the playhead) ───────────────────────────────────────
  // The playhead arrives via window.__veSubtitleFrame, published by FrameBridge
  // inside the composition (this tab is chrome — no useCurrentFrame()).
  const listRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true); // center the active line while playing
  const editingRef = useRef(false);
  const captionsRef = useRef(captions);
  const lastManualScrollRef = useRef(0);
  const busyRef = useRef(busy);
  captionsRef.current = captions;
  editingRef.current = editingIndex !== null;
  busyRef.current = busy;

  // Manual scroll stops following. It re-arms once the active line is back in
  // view and the user has been idle for a moment (handled in the rAF loop).
  const disarm = () => {
    followingRef.current = false;
    lastManualScrollRef.current = performance.now();
  };

  useEffect(() => {
    // Active-row highlight style (injected once into Studio chrome).
    const STYLE_ID = "ve-cap-active-style";
    if (!document.getElementById(STYLE_ID)) {
      const s = document.createElement("style");
      s.id = STYLE_ID;
      s.textContent =
        "[data-cap].ve-cap-active{background:rgba(11,132,243,0.16);box-shadow:inset 3px 0 0 #0b84f3;}" +
        ".ve-cap-list::-webkit-scrollbar{width:10px;}" +
        ".ve-cap-list::-webkit-scrollbar-thumb{background:#3a3f44;border-radius:5px;}" +
        ".ve-cap-list::-webkit-scrollbar-track{background:transparent;}";
      document.head.appendChild(s);
    }

    let raf = 0;
    let prevFrame = -1;
    let prevPlaying = false;
    let lastChange = 0;
    let prevActiveEl: HTMLElement | null = null;
    let centeredForActive = -1;
    let handledEditNonce = -1;
    const tick = () => {
      const bridge = (window as Window & {
        __veSubtitleFrame?: { frame: number; fps: number };
      }).__veSubtitleFrame;
      if (bridge && typeof bridge.frame === "number" && listRef.current) {
        const now = performance.now();
        if (prevFrame === -1) {
          // First observation: record the baseline WITHOUT marking it a change,
          // else a paused video reads as "playing" on the first tick and the
          // tab scroll-jumps (which can scroll the sidebar itself).
          prevFrame = bridge.frame;
        } else if (bridge.frame !== prevFrame) {
          prevFrame = bridge.frame;
          lastChange = now;
        }
        // Playing = the frame advanced recently. On pause the publisher stops
        // re-rendering, so the frame goes stale and this flips to false.
        const playing = lastChange > 0 && now - lastChange < 200;
        if (playing && !prevPlaying) {
          followingRef.current = true; // resume re-arms
          centeredForActive = -1; // force a recenter on resume
        }
        prevPlaying = playing;

        // Active caption at the playhead (caption i shows until the next starts).
        const caps = captionsRef.current;
        const fps = bridge.fps || 30;
        const ms = (bridge.frame / fps) * 1000;
        const active = activeCaptionIndex(caps, ms);
        const el =
          active >= 0
            ? listRef.current.querySelector<HTMLElement>(`[data-cap="${active}"]`)
            : null;

        // Highlight tracks the playhead always (playing or paused).
        if (el !== prevActiveEl) {
          prevActiveEl?.classList.remove("ve-cap-active");
          el?.classList.add("ve-cap-active");
          prevActiveEl = el;
        }

        // Center the active line while playing. Adjust the list's OWN scrollTop
        // only — never el.scrollIntoView(), which scrolls every scrollable
        // ancestor (it was scrolling the sidebar's tab bar off-screen).
        if (el && playing && !editingRef.current) {
          const c = listRef.current;
          const cRect = c.getBoundingClientRect();
          const rRect = el.getBoundingClientRect();
          // Partially visible inside the list viewport?
          const visible = rRect.bottom > cRect.top && rRect.top < cRect.bottom;
          const center = () => {
            const rowCenter = rRect.top - cRect.top + rRect.height / 2;
            c.scrollTop += rowCenter - c.clientHeight / 2;
          };
          if (followingRef.current) {
            // Recenter when the active line changes (keeps it mid-window).
            if (active !== centeredForActive) {
              center();
              centeredForActive = active;
            }
          } else if (visible && now - lastManualScrollRef.current > 2000) {
            // User scrolled the active line back into view + paused scrolling
            // for ~2s → re-arm and recenter.
            followingRef.current = true;
            center();
            centeredForActive = active;
          }
        }
      }

      // On-video caption click → open this caption in the inline editor. The
      // request arrives via window.__veEditRequest (set by CaptionOverlay in the
      // composition window — same bridge pattern as __veSubtitleFrame). The nonce
      // makes a repeat click on the same caption re-trigger.
      const editReq = (window as Window & {
        __veEditRequest?: { index: number; nonce: number };
      }).__veEditRequest;
      if (editReq && editReq.nonce !== handledEditNonce) {
        handledEditNonce = editReq.nonce;
        const seg = captionsRef.current[editReq.index];
        const c = listRef.current;
        if (seg && c && !busyRef.current) {
          // Scroll the target row to the middle of the list BEFORE it becomes the
          // edit form (the editing row has no [data-cap]).
          const el = c.querySelector<HTMLElement>(`[data-cap="${editReq.index}"]`);
          if (el) {
            const cRect = c.getBoundingClientRect();
            const rRect = el.getBoundingClientRect();
            c.scrollTop += rRect.top - cRect.top + rRect.height / 2 - c.clientHeight / 2;
          }
          followingRef.current = false; // don't let auto-follow steal the scroll
          setEditingIndex(editReq.index);
          setDraftText(seg.text);
          setDraftStart(msToClock(seg.startMs));
          setDraftEnd(msToClock(seg.endMs));
          setDraftCaret(seg.text.length);
        }
      }

      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Probe the selected project's artifacts on switch. Sidecar offline → leave
  // health null (no banner) so a stopped sidecar never raises a false alarm.
  useEffect(() => {
    if (!project) {
      setHealth(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${HEALTH_URL}?project=${encodeURIComponent(project)}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) {
          setHealth({ corrupt: Boolean(data.corrupt), resumeStep: data.resumeStep ?? null });
        }
      } catch {
        if (!cancelled) setHealth(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project]);

  // Mirror pending deletions to the module-level store so they survive a
  // tab-switch remount (NOT a reload — staging marks die on reload; see
  // pendingStore at top of file). Keyed by project; clears the entry when nothing
  // is pending. On a project switch this runs AFTER the in-render reset above, so
  // it always writes the new project's own (reset) state — never the old's marks.
  useEffect(() => {
    if (!project) return;
    if (pendingDrops.size === 0 && pendingLines.size === 0) {
      pendingStore.delete(project);
    } else {
      pendingStore.set(project, {
        drops: new Set(pendingDrops),
        lines: new Set(pendingLines),
      });
    }
  }, [pendingDrops, pendingLines, project]);

  // Close the inline editor when the user clicks OUTSIDE the caption list (edits are
  // auto-saved, so closing never loses work). A click on ANOTHER row inside the list
  // is intentionally NOT closed here — that row's own onClick handles it (beginEdit
  // swaps editingIndex in a single render). If we closed here on mousedown instead,
  // the tall edit row would collapse first, shifting a row BELOW upward between
  // mousedown and mouseup, so the click would miss it and never open it. (Rows above
  // happened to work only because closing doesn't move them.)
  useEffect(() => {
    if (editingIndex === null) return;
    const onDown = (e: MouseEvent) => {
      if (listRef.current && !listRef.current.contains(e.target as Node)) {
        setEditingIndex(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [editingIndex]);

  const hasCaptions = captions.length > 0;
  const groups = groupByCut(captions);

  // Flip the in-memory store for instant preview (shouldSave:false — Remotion's
  // shouldSave codemod rewrites a <Composition> with a string-literal id, but our
  // Root.tsx maps compositions with id={p.name}, so it would throw "Could not find
  // defaultProps for composition"). Persist the new value into the snapshot json
  // (the source of truth) via the SAME save path as caption edits — it rides along
  // as captionsEnabled — so the toggle survives reloads AND re-cuts (the pipeline
  // preserves the stored value across an Apply instead of resetting to the mode
  // default). Best-effort: sidecar offline → preview-only for the session.
  const toggle = () => {
    const next = !on;
    setDefaultProps?.((p) => ({ ...p, captionsEnabled: next }), { shouldSave: false });
    if (!project) return;
    fetch(SAVE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, captions, captionsEnabled: next }),
    }).catch(() => {});
  };

  const beginEdit = (absIndex: number) => {
    if (busy) return; // no editing mid-cut — the re-cut + reload would discard it
    const seg = captions[absIndex];
    setEditingIndex(absIndex);
    setDraftText(seg.text);
    setDraftStart(msToClock(seg.startMs));
    setDraftEnd(msToClock(seg.endMs));
    setDraftCaret(seg.text.length);
  };

  // Debounced persist of edited captions to the project snapshot (durable +
  // visible to a CLI render). Best-effort: sidecar offline → preview-only.
  const persistCaptions = (caps: CaptionSegment[]) => {
    if (!project || busy) return; // never persist during a cut — it would race the re-cut
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => {
      fetch(SAVE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project, captions: caps }),
      }).catch(() => {});
    }, 500);
  };

  // Commit a new caption list: update the Studio store for instant preview
  // (shouldSave:false — the .map()'d <Composition id={p.name}> defeats the
  // shouldSave codemod) AND schedule a snapshot persist.
  const commitCaptions = (next: CaptionSegment[]) => {
    setDefaultProps?.((p) => ({ ...p, captions: next }), { shouldSave: false });
    persistCaptions(next);
  };

  // Click a caption's timestamp → seek the Studio player there. `seek` is a public
  // @remotion/studio export reachable from chrome; fps comes from the FrameBridge.
  const seekTo = async (ms: number) => {
    const fps =
      (window as Window & { __veSubtitleFrame?: { fps: number } }).__veSubtitleFrame?.fps ?? 30;
    const { seek } = await import("@remotion/studio");
    seek(Math.round((ms / 1000) * fps));
  };

  const startMs = clockToMs(draftStart);
  const endMs = clockToMs(draftEnd);

  // Auto-save (no Save button): commit the edit live on every keystroke. Called
  // with the NEW values explicitly because draft state updates async. A transiently
  // invalid timestamp (unparseable, negative, or end<=start) is skipped so a
  // half-typed clock never clobbers the caption — the last valid value stays.
  const autoSave = (textVal: string, startVal: string, endVal: string) => {
    if (editingIndex === null) return;
    const s = clockToMs(startVal);
    const e = clockToMs(endVal);
    if (s === null || e === null || s < 0 || e <= s) return;
    const next = captions.map((c, i) =>
      i === editingIndex ? { ...c, text: textVal, startMs: s, endMs: e } : c
    );
    commitCaptions(next);
  };

  // Split the editing line into two at the caret (word boundary). Each half takes
  // its own words' timestamps — frame-exact, no interpolation. Both keep the
  // parent cutIndex (split stays inside the cut). Staged line-deletes remap: a
  // marked line stays marked on BOTH halves; everything after shifts +1.
  const doSplit = () => {
    if (editingIndex === null) return;
    const i = editingIndex;
    const cap = captions[i];
    const words = cap.words ?? [];
    const tokens = draftText.split(/\s+/).filter(Boolean);
    const k = splitAt(draftText, draftCaret);
    if (k === null || words.length !== tokens.length || k <= 0 || k >= words.length) return;
    const aWords = words.slice(0, k);
    const bWords = words.slice(k);
    const lineA: CaptionSegment = {
      ...cap,
      text: tokens.slice(0, k).join(" "),
      startMs: aWords[0].startMs,
      endMs: aWords[aWords.length - 1].endMs,
      words: aWords,
    };
    const lineB: CaptionSegment = {
      ...cap,
      text: tokens.slice(k).join(" "),
      startMs: bWords[0].startMs,
      endMs: bWords[bWords.length - 1].endMs,
      words: bWords,
    };
    const next = [...captions.slice(0, i), lineA, lineB, ...captions.slice(i + 1)];
    setPendingLines((prev) => {
      const out = new Set<number>();
      prev.forEach((j) => {
        if (j < i) out.add(j);
        else if (j === i) {
          out.add(i);
          out.add(i + 1);
        } else out.add(j + 1);
      });
      return out;
    });
    setEditingIndex(null);
    commitCaptions(next);
  };

  // Merge the editing line with its neighbor above (dir -1) or below (dir +1).
  // Allowed only within a cut (same cutIndex) — so it's auto-blocked on the
  // first/last line of a cut. Span union, text space-joined, words concatenated.
  // Per design, merging CLEARS any delete-mark on the two lines; later indices -1.
  const mergeEditing = (dir: -1 | 1) => {
    if (editingIndex === null) return;
    const i = editingIndex;
    const j = i + dir;
    if (j < 0 || j >= captions.length) return;
    const lo = Math.min(i, j);
    const a = captions[lo];
    const b = captions[lo + 1];
    if ((a.cutIndex ?? null) !== (b.cutIndex ?? null)) return;
    const merged: CaptionSegment = {
      ...a,
      text: `${a.text} ${b.text}`.trim(),
      startMs: a.startMs,
      endMs: b.endMs,
      words: [...(a.words ?? []), ...(b.words ?? [])],
    };
    const next = [...captions.slice(0, lo), merged, ...captions.slice(lo + 2)];
    setPendingLines((prev) => {
      const out = new Set<number>();
      prev.forEach((x) => {
        if (x === lo || x === lo + 1) return;
        out.add(x < lo ? x : x - 1);
      });
      return out;
    });
    setEditingIndex(null);
    commitCaptions(next);
  };

  // Delete/undo a whole cut. Canonical form: a fully-deleted cut lives in
  // `pendingDrops` with NONE of its lines in `pendingLines` (the cut-drop subsumes
  // them), so toggling either way clears any per-line marks for that cut.
  const toggleDrop = (cutIndex: number) => {
    setApplyError(null);
    const grp = groups.find((g) => g.cutIndex === cutIndex);
    const lineIdxs = grp ? grp.rows.map((r) => r.absIndex) : [];
    const wasDropped = pendingDrops.has(cutIndex);
    setPendingDrops((prev) => {
      const next = new Set(prev);
      wasDropped ? next.delete(cutIndex) : next.add(cutIndex);
      return next;
    });
    setPendingLines((prev) => {
      const next = new Set(prev);
      lineIdxs.forEach((i) => next.delete(i));
      return next;
    });
  };

  // Stage/undo a single caption line, kept canonical with the cut-level state
  // (see toggleDrop): marking a cut's last line collapses to one cut-drop; undoing
  // one line of a dropped cut expands it back to the remaining lines. Closes the editor.
  const toggleLine = (absIndex: number) => {
    setApplyError(null);
    setEditingIndex(null);
    const grp = groups.find((g) => g.rows.some((r) => r.absIndex === absIndex));
    const cutIndex = grp?.cutIndex ?? null;
    const lineIdxs = grp ? grp.rows.map((r) => r.absIndex) : [absIndex];

    // Part of a whole-cut drop → undo expands it back to the other lines.
    if (cutIndex !== null && pendingDrops.has(cutIndex)) {
      setPendingDrops((prev) => {
        const next = new Set(prev);
        next.delete(cutIndex);
        return next;
      });
      setPendingLines((prev) => {
        const next = new Set(prev);
        lineIdxs.forEach((i) => {
          if (i !== absIndex) next.add(i);
        });
        return next;
      });
      return;
    }

    // Already marked per-line → un-mark it.
    if (pendingLines.has(absIndex)) {
      setPendingLines((prev) => {
        const next = new Set(prev);
        next.delete(absIndex);
        return next;
      });
      return;
    }

    // Marking this line completes the cut → collapse to a single cut-drop.
    if (cutIndex !== null && lineIdxs.every((i) => i === absIndex || pendingLines.has(i))) {
      setPendingDrops((prev) => {
        const next = new Set(prev);
        next.add(cutIndex);
        return next;
      });
      setPendingLines((prev) => {
        const next = new Set(prev);
        lineIdxs.forEach((i) => next.delete(i));
        return next;
      });
      return;
    }

    // Plain per-line mark.
    setPendingLines((prev) => {
      const next = new Set(prev);
      next.add(absIndex);
      return next;
    });
  };

  // Apply staged deletions: POST to the sidecar, which persists the edited captions
  // then ENQUEUES a background re-cut job (4_render.py --drop-cuts for whole cuts,
  // --drop-ranges for single lines). Progress shows under the project's row in the
  // list; when the job finishes the build store hard-reloads Studio to pick up the
  // shorter composition (a reload is still needed — it drops the in-memory caption
  // override and dodges @remotion/media's EncodingError on the hot-swapped file).
  const applyDrops = async () => {
    if (pendingCount === 0 || applying || !project) return;
    // Cancel a queued auto-save: it holds pre-cut captions and would otherwise
    // land AFTER the re-cut and clobber the re-mapped snapshot.
    if (persistTimer.current) clearTimeout(persistTimer.current);
    setEditingIndex(null); // close any open editor — edits are locked during the cut
    setApplying(true);
    setApplyError(null);

    // A line-drop carves the caption's [startMs, endMs] out of the video. But word
    // timings (especially after a split/merge) can make a caption's end run PAST the
    // next caption's start, so the raw range would bleed into — and delete — an
    // adjacent line we're keeping. Clamp each range to the nearest SURVIVING caption
    // on each side. Adjacent dropped lines are NOT clamped against each other (their
    // ranges simply merge in the re-cut). The clamped range still lies inside the
    // dropped caption, so the caption itself is still removed.
    const isDropped = (idx: number) =>
      pendingLines.has(idx) ||
      (typeof captions[idx].cutIndex === "number" &&
        pendingDrops.has(captions[idx].cutIndex as number));
    const dropRangeFor = (i: number) => {
      let startMs = captions[i].startMs;
      let endMs = captions[i].endMs;
      for (let p = i - 1; p >= 0; p--) {
        if (!isDropped(p)) { startMs = Math.max(startMs, captions[p].endMs); break; }
      }
      for (let n = i + 1; n < captions.length; n++) {
        if (!isDropped(n)) { endMs = Math.min(endMs, captions[n].startMs); break; }
      }
      return { startMs, endMs };
    };

    try {
      const res = await fetch(SIDECAR_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          // Authoritative current captions → sidecar persists them, then
          // 4_render re-maps THEM through the cut (manual edits survive).
          captions,
          dropCutIndices: [...pendingDrops],
          dropRanges: [...pendingLines].map(dropRangeFor),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 409) {
          throw new Error(data.error || "This project already has a job queued or running.");
        }
        throw new Error(data.log || data.error || `sidecar error ${res.status}`);
      }
      // Enqueued. Clear the staged marks (the cut is committed to the queue) so no
      // stale mark re-attaches by index to a caption that shifts after the cut. Hand
      // off to the sidebar row via ve-job-started; the build store shows the recut's
      // progress and hard-reloads Studio when the job completes. No reload here —
      // the job hasn't run yet.
      setPendingDrops(new Set());
      setPendingLines(new Set());
      pendingStore.delete(project);
      window.dispatchEvent(
        new CustomEvent("ve-job-started", {
          detail: { project, id: data.jobId, state: data.state },
        })
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setApplyError(
        msg.includes("Failed to fetch")
          ? "Sidecar not running — start it: cd src/pipeline && python3 sidecar.py"
          : msg
      );
    } finally {
      setApplying(false);
    }
  };

  // Repair a corrupt project: kick off the pipeline re-run from the last valid step
  // as a BACKGROUND JOB, then hand it off to the sidebar's in-list progress bar —
  // exactly like the "Re-run pipeline" menu, so the UX is consistent everywhere. The
  // sidecar spawns run_all.py and returns immediately; NewProjectLauncher's build
  // store catches `ve-job-started` and shows the bar under the project's row. Clear
  // the banner optimistically; the job's completion HMR-reloads the repaired
  // composition and the health probe re-runs. (A failure surfaces in the in-list bar.)
  const fixProject = async () => {
    if (!project) return;
    setApplyError(null);
    try {
      const res = await fetch(FIX_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || data.log || `sidecar error ${res.status}`);
      }
      window.dispatchEvent(
        new CustomEvent("ve-job-started", { detail: { project: data.project || project } })
      );
      setHealth({ corrupt: false, resumeStep: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setApplyError(
        msg.includes("Failed to fetch")
          ? "Sidecar not running — start it: cd src/pipeline && python3 sidecar.py"
          : msg
      );
    }
  };

  return (
    <div style={outer} className="css-reset">
      <div style={header}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={headerLabel}>Display on video</span>
          <div
            style={switchTrack(on)}
            role="switch"
            aria-checked={on}
            onClick={toggle}
            title={on ? "Subtitles render on the video" : "Subtitles hidden on the video"}
          >
            <div style={switchKnob(on)} />
          </div>
        </div>
        <button
          style={applyBtn(pendingCount > 0 && !busy, busy)}
          onClick={applyDrops}
          disabled={pendingCount === 0 || busy || !project}
          title={
            !project
              ? "Project unknown — re-run the pipeline to enable Apply"
              : jobActive
                ? "A re-cut for this project is running/queued — see its row in the list"
                : pendingCount > 0
                  ? "Re-cut the video, removing the marked cuts/lines"
                  : "Mark a cut or line for deletion to enable"
          }
        >
          {jobActive ? "Re-cutting…" : applying ? "Applying…" : pendingCount > 0 ? `Apply (${pendingCount})` : "Apply"}
        </button>
      </div>
      {applyError && (
        <div
          style={{
            flexShrink: 0,
            padding: "6px 12px",
            fontSize: 11,
            color: "#f1b0a8",
            background: "rgba(192,57,43,0.15)",
            borderBottom: "1px solid rgba(192,57,43,0.3)",
          }}
        >
          {applyError}
        </div>
      )}
      {health?.corrupt && (
        <div
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            padding: "8px 12px",
            fontSize: 12,
            color: "#f4d35e",
            background: "rgba(192,57,43,0.18)",
            borderBottom: "1px solid rgba(192,57,43,0.35)",
          }}
        >
          <span>
            ⚠ This project&apos;s video is corrupt or incomplete
            {health.resumeStep
              ? ` — Fix re-runs the pipeline from step ${health.resumeStep}.`
              : "."}
          </span>
          <button
            style={applyBtn(true, false)}
            onClick={fixProject}
            disabled={!project}
            title="Re-run the pipeline from the last valid step (progress shows under the project in the list)"
          >
            Fix
          </button>
        </div>
      )}
      {hasCaptions ? (
        <div
          style={busy ? { ...list, opacity: 0.5, pointerEvents: "none" } : list}
          className="ve-cap-list"
          ref={listRef}
          onWheel={disarm}
          onTouchMove={disarm}
        >
          {groups.map((group, gi) => {
            const cutMarked = group.cutIndex !== null && pendingDrops.has(group.cutIndex);
            return (
            <div key={group.cutIndex ?? `g${gi}`}>
              {group.cutIndex !== null && (
                <div style={cutHeader} onClick={() => setEditingIndex(null)}>
                  <span>Cut {group.cutIndex + 1}</span>
                  <span
                    onClick={() => toggleDrop(group.cutIndex as number)}
                    title={cutMarked ? "Undo delete" : "Delete this cut"}
                    style={{
                      cursor: "pointer",
                      userSelect: "none",
                      fontWeight: 700,
                      color: cutMarked ? "#e0b341" : "#c0392b",
                    }}
                  >
                    {cutMarked ? "Undo" : "✕"}
                  </span>
                </div>
              )}
              {group.rows.map(({ caption, absIndex }) => {
                if (editingIndex === absIndex) {
                  const wlen = caption.words?.length ?? 0;
                  const tokenCount = draftText.split(/\s+/).filter(Boolean).length;
                  // Split needs a caret on a word boundary AND text whose word count
                  // still matches the per-word timing array (so each half gets exact ms).
                  const canSplit =
                    splitAt(draftText, draftCaret) !== null && wlen >= 2 && tokenCount === wlen;
                  const sameCut = (s?: CaptionSegment) =>
                    !!s && (s.cutIndex ?? null) === (caption.cutIndex ?? null);
                  const canMergeUp = sameCut(captions[absIndex - 1]);
                  const canMergeDown = sameCut(captions[absIndex + 1]);
                  return (
                  <div style={editRow} key={absIndex}>
                    {/* Row 1: left label (the timestamp slot) · text input · delete ✕.
                        The input sets no font-size, so Remotion's .css-reset rule sizes
                        it to 16px — identical to the inactive display caption text. */}
                    <div style={{ ...editLine, alignItems: "baseline" }}>
                      <span style={editLeftLabel}>Text</span>
                      <input
                        type="text"
                        value={draftText}
                        onChange={(e) => {
                          const v = e.target.value;
                          setDraftText(v);
                          setDraftCaret(e.target.selectionStart ?? 0);
                          autoSave(v, draftStart, draftEnd);
                        }}
                        onSelect={(e) => setDraftCaret(e.currentTarget.selectionStart ?? 0)}
                        onKeyUp={(e) => setDraftCaret(e.currentTarget.selectionStart ?? 0)}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            e.preventDefault();
                            setEditingIndex(null);
                          }
                        }}
                        autoFocus
                        style={editTextInput}
                      />
                      <span
                        style={{
                          ...delBtn,
                          color: cutMarked || pendingLines.has(absIndex) ? "#e0b341" : "#c0392b",
                        }}
                        onClick={() => toggleLine(absIndex)}
                        title={
                          cutMarked || pendingLines.has(absIndex) ? "Undo delete" : "Delete this line"
                        }
                      >
                        {cutMarked || pendingLines.has(absIndex) ? "Undo" : "✕"}
                      </span>
                    </div>
                    {/* Row 2: Start · End clocks (Start aligned under the text) +
                        split/merge. Wraps when the sidebar is too narrow. */}
                    <div style={{ ...editLine, flexWrap: "wrap", rowGap: 6 }}>
                      <span style={editLeftLabel}>Start</span>
                      <input
                        type="text"
                        value={draftStart}
                        onChange={(e) => {
                          const v = e.target.value;
                          setDraftStart(v);
                          autoSave(draftText, v, draftEnd);
                        }}
                        placeholder="0:00.000"
                        title="m:ss.mmm"
                        style={{ ...editClock, borderColor: startMs === null ? "#c0392b" : "#444" }}
                      />
                      <span style={inlineLabel}>End</span>
                      <input
                        type="text"
                        value={draftEnd}
                        onChange={(e) => {
                          const v = e.target.value;
                          setDraftEnd(v);
                          autoSave(draftText, draftStart, v);
                        }}
                        placeholder="0:00.000"
                        title="m:ss.mmm"
                        style={{ ...editClock, borderColor: endMs === null ? "#c0392b" : "#444" }}
                      />
                      <div style={{ display: "flex", gap: 8, marginLeft: "auto", alignItems: "center" }}>
                        <button
                          style={toolBtn(canSplit)}
                          onClick={doSplit}
                          disabled={!canSplit}
                          title={
                            canSplit
                              ? "Split into two lines at the cursor"
                              : "Put the cursor between two words to split"
                          }
                        >
                          ][
                        </button>
                        <button
                          style={toolBtn(canMergeUp)}
                          onClick={() => mergeEditing(-1)}
                          disabled={!canMergeUp}
                          title={canMergeUp ? "Merge with the line above" : "First line of the cut"}
                        >
                          ⬆
                        </button>
                        <button
                          style={toolBtn(canMergeDown)}
                          onClick={() => mergeEditing(1)}
                          disabled={!canMergeDown}
                          title={canMergeDown ? "Merge with the line below" : "Last line of the cut"}
                        >
                          ⬇
                        </button>
                      </div>
                    </div>
                  </div>
                  );
                }
                return (
                  <div
                    style={
                      cutMarked || pendingLines.has(absIndex)
                        ? { ...row, opacity: 0.45 }
                        : row
                    }
                    key={absIndex}
                    data-cap={absIndex}
                  >
                    <span
                      style={timestamp}
                      onClick={() => seekTo(caption.startMs)}
                      title="Seek to this time"
                    >
                      {msToTimestamp(caption.startMs)}
                    </span>
                    <span
                      style={
                        cutMarked || pendingLines.has(absIndex)
                          ? { ...text, textDecoration: "line-through" }
                          : text
                      }
                      onClick={() => beginEdit(absIndex)}
                      title="Click to edit"
                    >
                      {caption.text}
                    </span>
                    <span
                      onClick={() => toggleLine(absIndex)}
                      title={
                        cutMarked || pendingLines.has(absIndex) ? "Undo delete" : "Delete this line"
                      }
                      style={{
                        flexShrink: 0,
                        cursor: "pointer",
                        userSelect: "none",
                        fontWeight: 700,
                        color: cutMarked || pendingLines.has(absIndex) ? "#e0b341" : "#c0392b",
                      }}
                    >
                      {cutMarked || pendingLines.has(absIndex) ? "Undo" : "✕"}
                    </span>
                  </div>
                );
              })}
            </div>
            );
          })}
        </div>
      ) : (
        <div style={emptyState}>No captions in this composition.</div>
      )}
    </div>
  );
};

export default SubtitlesTab;
