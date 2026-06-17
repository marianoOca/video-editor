import React, { useEffect, useRef, useState } from "react";
import type { CaptionSegment } from "../schema";

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
  position: "sticky",
  top: 0,
  zIndex: 1,
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

// Timestamp = click-to-seek; accent blue, pointer.
const timestamp: React.CSSProperties = {
  flexShrink: 0,
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
const editWrap: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "8px 12px",
  borderBottom: "1px solid rgba(255,255,255,0.07)",
  background: "rgba(11,132,243,0.08)",
};

const input: React.CSSProperties = {
  background: "#1e1e1e",
  border: "1px solid #444",
  borderRadius: 0,
  color: "#fff",
  padding: "8px 10px",
  fontSize: 13,
  fontFamily: "inherit",
  width: "100%",
  boxSizing: "border-box",
  outline: "none",
};

const msInput: React.CSSProperties = {
  ...input,
  fontFamily: "monospace",
  width: 110,
};

const fieldLabel: React.CSSProperties = {
  fontFamily: "monospace",
  fontSize: 10,
  color: "#A6A7A9",
  fontWeight: 600,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  marginBottom: 2,
  display: "block",
};

const btn = (variant: "save" | "cancel"): React.CSSProperties => ({
  border: variant === "save" ? "none" : "1px solid #444",
  background: variant === "save" ? "#3aa657" : "transparent",
  color: variant === "save" ? "#06210f" : "#aaa",
  fontWeight: 700,
  fontSize: 12,
  borderRadius: 0,
  padding: "6px 14px",
  cursor: "pointer",
});

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

const SIDECAR_URL = "http://127.0.0.1:9848/apply-cuts";

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

  // ── Delete staging (whole cuts + single lines) ──────────────────────────────
  const project = typeof defaultProps?.project === "string" ? defaultProps.project : null;
  const [pendingDrops, setPendingDrops] = useState<Set<number>>(new Set()); // cut indices
  const [pendingLines, setPendingLines] = useState<Set<number>>(new Set()); // caption indices
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const pendingCount = pendingDrops.size + pendingLines.size;

  // ── Auto-scroll (follow the playhead) ───────────────────────────────────────
  // The playhead arrives via window.__veSubtitleFrame, published by FrameBridge
  // inside the composition (this tab is chrome — no useCurrentFrame()).
  const listRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true); // center the active line while playing
  const editingRef = useRef(false);
  const captionsRef = useRef(captions);
  const lastManualScrollRef = useRef(0);
  captionsRef.current = captions;
  editingRef.current = editingIndex !== null;

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
        let active = -1;
        for (let i = 0; i < caps.length; i++) {
          const nextStart = i + 1 < caps.length ? caps[i + 1].startMs : caps[i].endMs;
          if (caps[i].startMs <= ms && ms < nextStart) {
            active = i;
            break;
          }
        }
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
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const hasCaptions = captions.length > 0;
  const groups = groupByCut(captions);

  // In-memory only (shouldSave:false). Remotion's shouldSave path runs a codemod
  // that rewrites the matching <Composition> in Root.tsx, but our Root.tsx renders
  // compositions in a .map() with id={p.name} (not a string literal), so the
  // codemod finds no tag and throws "Could not find defaultProps for composition".
  // The snapshot json is the source of truth; live edits stay in the Studio store
  // for the session (durable default comes from the pipeline per mode).
  const toggle = () => {
    setDefaultProps?.(
      (p) => ({ ...p, captionsEnabled: !p.captionsEnabled }),
      { shouldSave: false }
    );
  };

  const beginEdit = (absIndex: number) => {
    const seg = captions[absIndex];
    setEditingIndex(absIndex);
    setDraftText(seg.text);
    setDraftStart(msToClock(seg.startMs));
    setDraftEnd(msToClock(seg.endMs));
  };

  const cancelEdit = () => setEditingIndex(null);

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
  const editValid =
    startMs !== null && endMs !== null && startMs >= 0 && endMs > startMs;

  const saveEdit = () => {
    if (editingIndex === null || !editValid) return;
    const next = captions.map((c, i) =>
      i === editingIndex ? { ...c, text: draftText, startMs: startMs!, endMs: endMs! } : c
    );
    // shouldSave:false — same reason as toggle (codemod can't match the .map()'d
    // <Composition id={p.name}>). Edit lives in the Studio store for the session.
    setDefaultProps?.((p) => ({ ...p, captions: next }), { shouldSave: false });
    setEditingIndex(null);
  };

  const toggleDrop = (cutIndex: number) => {
    setApplyError(null);
    setPendingDrops((prev) => {
      const next = new Set(prev);
      next.has(cutIndex) ? next.delete(cutIndex) : next.add(cutIndex);
      return next;
    });
  };

  // Stage a single caption line for deletion (edit-row X). Mirrors the cut toggle;
  // closes the editor. Committed by the same Apply as a video cut (its time span).
  const toggleLine = (absIndex: number) => {
    setApplyError(null);
    setPendingLines((prev) => {
      const next = new Set(prev);
      next.has(absIndex) ? next.delete(absIndex) : next.add(absIndex);
      return next;
    });
    setEditingIndex(null);
  };

  // Apply staged deletions: POST to the sidecar, which re-cuts the video via
  // 4_render.py (--drop-cuts for whole cuts, --drop-ranges for single lines).
  // Studio then hot-reloads the shorter composition.
  const applyDrops = async () => {
    if (pendingCount === 0 || applying || !project) return;
    setApplying(true);
    setApplyError(null);
    try {
      const res = await fetch(SIDECAR_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project,
          dropCutIndices: [...pendingDrops],
          dropRanges: [...pendingLines].map((i) => ({
            startMs: captions[i].startMs,
            endMs: captions[i].endMs,
          })),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.log || data.error || `sidecar error ${res.status}`);
      }
      setPendingDrops(new Set()); // HMR refreshes the composition from the new snapshot
      setPendingLines(new Set());
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
          style={applyBtn(pendingCount > 0, applying)}
          onClick={applyDrops}
          disabled={pendingCount === 0 || applying || !project}
          title={
            !project
              ? "Project unknown — re-run the pipeline to enable Apply"
              : pendingCount > 0
                ? "Re-cut the video, removing the marked cuts/lines"
                : "Mark a cut or line for deletion to enable"
          }
        >
          {applying ? "Applying…" : pendingCount > 0 ? `Apply (${pendingCount})` : "Apply"}
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

      {hasCaptions ? (
        <div style={list} className="ve-cap-list" ref={listRef} onWheel={disarm} onTouchMove={disarm}>
          {groups.map((group, gi) => {
            const cutMarked = group.cutIndex !== null && pendingDrops.has(group.cutIndex);
            return (
            <div key={group.cutIndex ?? `g${gi}`}>
              {group.cutIndex !== null && (
                <div style={cutHeader}>
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
              {group.rows.map(({ caption, absIndex }) =>
                editingIndex === absIndex ? (
                  <div style={editWrap} key={absIndex}>
                    <div>
                      <label style={fieldLabel}>Text</label>
                      <textarea
                        value={draftText}
                        onChange={(e) => setDraftText(e.target.value)}
                        rows={2}
                        autoFocus
                        style={{ ...input, resize: "none", lineHeight: 1.4 }}
                      />
                    </div>
                    {/* Start/End + buttons share one row; wrap to stack when the
                        sidebar is too narrow for all four. */}
                    <div
                      style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}
                    >
                      <div>
                        <label style={fieldLabel}>Start</label>
                        <input
                          type="text"
                          value={draftStart}
                          onChange={(e) => setDraftStart(e.target.value)}
                          placeholder="0:00.000"
                          title="m:ss.mmm"
                          style={{ ...msInput, borderColor: startMs === null ? "#c0392b" : "#444" }}
                        />
                      </div>
                      <div>
                        <label style={fieldLabel}>End</label>
                        <input
                          type="text"
                          value={draftEnd}
                          onChange={(e) => setDraftEnd(e.target.value)}
                          placeholder="0:00.000"
                          title="m:ss.mmm"
                          style={{ ...msInput, borderColor: endMs === null ? "#c0392b" : "#444" }}
                        />
                      </div>
                      <div style={{ display: "flex", gap: 8, marginLeft: "auto", alignItems: "flex-end" }}>
                        <button
                          style={{ ...btn("cancel"), borderColor: "#c0392b", color: "#e88", fontWeight: 800 }}
                          onClick={() => toggleLine(absIndex)}
                          title="Delete this line — cuts it from the video on Apply"
                        >
                          ✕
                        </button>
                        <button style={btn("cancel")} onClick={cancelEdit}>
                          Cancel
                        </button>
                        <button
                          style={{
                            ...btn("save"),
                            opacity: editValid ? 1 : 0.5,
                            cursor: editValid ? "pointer" : "default",
                          }}
                          onClick={saveEdit}
                          disabled={!editValid}
                          title={editValid ? "Save" : "End must be after Start"}
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
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
                  </div>
                )
              )}
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
