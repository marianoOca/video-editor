import {
  useVideoConfig,
  useCurrentFrame,
  AbsoluteFill,
  getRemotionEnvironment,
} from "remotion";
import type { CaptionSegment } from "./schema";
import { useState, useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

const HIGHLIGHT_COLOR = "#FFE033";
const SHADOW = "0px 3px 8px rgba(0,0,0,0.9)";

// Studio playhead red (Remotion 4.x). Used to find the slider line element and
// bump its z-index above the injected subtitle bar.
const PLAYHEAD_RED_RGB = "rgb(240, 44, 0)";
const PLAYHEAD_Z = "9999";

// Subtitle track bar layout (px).
const BAR_HEIGHT = 52;
const HANDLE_W = 8;
const BLOCK_H = 32;
const BLOCK_TOP = 10;

const INPUT_STYLE: React.CSSProperties = {
  background: "#1e1e1e",
  border: "1px solid #444",
  borderRadius: 6,
  color: "#fff",
  padding: "8px 12px",
  fontSize: 15,
  fontFamily: "system-ui, sans-serif",
  width: "100%",
  boxSizing: "border-box",
  outline: "none",
};

const LABEL_STYLE: React.CSSProperties = {
  color: "#888",
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.8,
  textTransform: "uppercase" as const,
  marginBottom: 4,
  display: "block",
};

// ── Panel rendered into Studio's parent window (below timeline) ───────────────
const CaptionEditorPortal: React.FC<{
  captions: CaptionSegment[];
  activeIndex: number;
  onClose: () => void;
  onSaved: (updated: CaptionSegment[]) => void;
}> = ({ captions, activeIndex, onClose, onSaved }) => {
  const seg = captions[activeIndex];
  const [text, setText] = useState(seg.text);
  const [startMs, setStartMs] = useState(String(seg.startMs));
  const [endMs, setEndMs] = useState(String(seg.endMs));
  const [saving, setSaving] = useState(false);
  const [container, setContainer] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const parentDoc = window.parent?.document ?? document;
    const el = parentDoc.createElement("div");
    el.id = "remotion-caption-editor";
    Object.assign(el.style, {
      position: "fixed",
      bottom: "0",
      left: "0",
      right: "0",
      zIndex: "99999",
      background: "#111",
      borderTop: `3px solid ${HIGHLIGHT_COLOR}`,
      padding: "20px 28px",
      display: "flex",
      gap: "20px",
      alignItems: "flex-end",
      boxShadow: "0 -8px 32px rgba(0,0,0,0.7)",
      fontFamily: "system-ui, sans-serif",
    });
    parentDoc.body.appendChild(el);
    setContainer(el);
    return () => {
      parentDoc.body.removeChild(el);
    };
  }, []);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const updated = captions.map((c, i) =>
        i === activeIndex
          ? { ...c, text, startMs: Number(startMs), endMs: Number(endMs) }
          : c
      );
      const { updateDefaultProps } = await import("@remotion/studio");
      await updateDefaultProps({
        compositionId: "VideoEditor",
        defaultProps: ({ savedDefaultProps }: { savedDefaultProps: Record<string, unknown> }) => ({
          ...savedDefaultProps,
          captions: updated,
        }),
      });
      onSaved(updated);
    } finally {
      setSaving(false);
      onClose();
    }
  }, [text, startMs, endMs, activeIndex, captions, onClose, onSaved]);

  if (!container) return null;

  const ui = (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 0, minWidth: 160 }}>
        <div>
          <div style={{ color: HIGHLIGHT_COLOR, fontWeight: 700, fontSize: 13, letterSpacing: 1 }}>
            ✏️ SUBTÍTULO #{activeIndex + 1}
          </div>
          <div style={{ color: "#555", fontSize: 11, marginTop: 2 }}>
            {seg.startMs}ms → {seg.endMs}ms
          </div>
        </div>
      </div>

      <div style={{ flex: 1 }}>
        <label style={LABEL_STYLE}>Texto</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          autoFocus
          style={{ ...INPUT_STYLE, resize: "none", lineHeight: 1.5 }}
        />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 130 }}>
        <div>
          <label style={LABEL_STYLE}>Inicio (ms)</label>
          <input
            type="number"
            value={startMs}
            onChange={(e) => setStartMs(e.target.value)}
            style={INPUT_STYLE}
          />
        </div>
        <div>
          <label style={LABEL_STYLE}>Fin (ms)</label>
          <input
            type="number"
            value={endMs}
            onChange={(e) => setEndMs(e.target.value)}
            style={INPUT_STYLE}
          />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, alignSelf: "center" }}>
        <button
          onClick={save}
          disabled={saving}
          style={{
            background: HIGHLIGHT_COLOR,
            border: "none",
            color: "#000",
            borderRadius: 8,
            padding: "10px 24px",
            cursor: saving ? "default" : "pointer",
            fontWeight: 800,
            fontSize: 14,
            opacity: saving ? 0.6 : 1,
            minWidth: 110,
          }}
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
        <button
          onClick={onClose}
          style={{
            background: "transparent",
            border: "1px solid #444",
            color: "#888",
            borderRadius: 8,
            padding: "8px 24px",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          Cancelar
        </button>
      </div>
    </>
  );

  return createPortal(ui, container);
};

// ── TikTok-style subtitle track bar ──────────────────────────────────────────
type DragHandle = {
  segIndex: number;
  side: "left" | "right";
  startMouseX: number;
  origStartMs: number;
  origEndMs: number;
  trackWidth: number;
};

const SubtitleTrackBar: React.FC<{
  captions: CaptionSegment[];
  totalDurationMs: number;
  activeIndex: number;
}> = ({ captions, totalDurationMs, activeIndex }) => {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [localCaptions, setLocalCaptions] = useState<CaptionSegment[]>(captions);
  const [dragging, setDragging] = useState<DragHandle | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);

  // Keep localCaptions in sync when captions prop changes (e.g. after a save)
  useEffect(() => {
    setLocalCaptions(captions);
  }, [captions]);

  useEffect(() => {
    const parentDoc = window.parent?.document ?? document;
    const parentWin = window.parent ?? window;
    const existing = parentDoc.getElementById("remotion-subtitle-track");
    if (existing && existing.parentElement) existing.parentElement.removeChild(existing);

    const el = parentDoc.createElement("div");
    el.id = "remotion-subtitle-track";
    Object.assign(el.style, {
      position: "absolute",
      top: "0px",
      left: "0px",
      width: "0px",
      height: `${BAR_HEIGHT}px`,
      background: "#0e0e0e",
      borderTop: "1px solid #2a2a2a",
      padding: "0 16px",
      boxSizing: "border-box",
      fontFamily: "system-ui, sans-serif",
      userSelect: "none",
      visibility: "hidden",
    });
    setContainer(el);

    // Locate the <Video> track row in Studio's timeline by its label text.
    let cachedRow: HTMLElement | null = null;
    const findVideoTrackRow = (): HTMLElement | null => {
      if (cachedRow && parentDoc.body.contains(cachedRow)) return cachedRow;
      const walker = parentDoc.createTreeWalker(parentDoc.body, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walker.nextNode())) {
        const txt = node.textContent ?? "";
        if (!txt.includes("Composition.tsx")) continue;
        let p: HTMLElement | null = node.parentElement;
        while (p && (p.clientHeight < 30 || p.clientWidth < 100)) p = p.parentElement;
        if (p && p.clientHeight <= 400) {
          cachedRow = p;
          return p;
        }
      }
      return null;
    };

    // Scrollable timeline content: outer has class `__remotion-horizontal-scrollbar`
    // (Studio's TimelineScrollable); inner is its first child. Mounting the bar
    // inside inner makes it scroll horizontally with the waveform.
    const findScrollable = (): { outer: HTMLElement; inner: HTMLElement } | null => {
      const outer = parentDoc.querySelector<HTMLElement>(".__remotion-horizontal-scrollbar");
      const inner = outer?.firstElementChild;
      if (!outer || !(inner instanceof HTMLElement)) return null;
      return { outer, inner };
    };

    let raf = 0;
    const tick = () => {
      const row = findVideoTrackRow();
      const sc = findScrollable();
      // Bump Studio's playhead line z-index so it paints above the bar.
      const playheadLine = parentDoc.querySelector<HTMLElement>(
        `[style*="${PLAYHEAD_RED_RGB}"]`
      );
      if (playheadLine && playheadLine.style.zIndex !== PLAYHEAD_Z) {
        playheadLine.style.zIndex = PLAYHEAD_Z;
      }
      if (row && sc) {
        if (el.parentElement !== sc.inner) {
          sc.inner.insertBefore(el, sc.inner.firstChild);
        }
        const r = row.getBoundingClientRect();
        const ir = sc.inner.getBoundingClientRect();
        // Use widest child of inner so bar matches actual timeline content width
        // (TimelineDragHandler stretches to 100*zoom% which equals waveform width).
        let widest = sc.inner.clientWidth;
        for (const c of Array.from(sc.inner.children)) {
          if (c === el) continue;
          if (c instanceof HTMLElement) widest = Math.max(widest, c.scrollWidth, c.clientWidth);
        }
        const w = Math.max(sc.outer.scrollWidth, sc.inner.scrollWidth, widest);
        el.style.top = `${Math.round(r.bottom - ir.top)}px`;
        el.style.left = "0px";
        el.style.width = `${w}px`;
        el.style.visibility = "visible";
      } else {
        el.style.visibility = "hidden";
      }
      raf = parentWin.requestAnimationFrame(tick);
    };
    raf = parentWin.requestAnimationFrame(tick);

    return () => {
      parentWin.cancelAnimationFrame(raf);
      if (el.parentElement) el.parentElement.removeChild(el);
    };
  }, []);

  const startDrag = useCallback(
    (e: React.MouseEvent, segIndex: number, side: "left" | "right") => {
      e.stopPropagation();
      e.preventDefault();
      const trackWidth = trackRef.current?.clientWidth ?? 1;
      const seg = localCaptions[segIndex];
      const handle: DragHandle = {
        segIndex,
        side,
        startMouseX: e.clientX,
        origStartMs: seg.startMs,
        origEndMs: seg.endMs,
        trackWidth,
      };
      setDragging(handle);

      const parentWin = window.parent ?? window;

      const onMove = (ev: MouseEvent) => {
        const deltaMs = ((ev.clientX - handle.startMouseX) / handle.trackWidth) * totalDurationMs;
        setLocalCaptions((prev) =>
          prev.map((c, i) => {
            if (i !== segIndex) return c;
            if (side === "left") {
              const newStart = Math.max(0, Math.min(handle.origStartMs + deltaMs, c.endMs - 100));
              return { ...c, startMs: Math.round(newStart) };
            } else {
              const newEnd = Math.max(c.startMs + 100, Math.min(handle.origEndMs + deltaMs, totalDurationMs));
              return { ...c, endMs: Math.round(newEnd) };
            }
          })
        );
      };

      const onUp = async () => {
        parentWin.removeEventListener("mousemove", onMove);
        parentWin.removeEventListener("mouseup", onUp);
        setDragging(null);
        // Persist final state
        setLocalCaptions((final) => {
          (async () => {
            const { updateDefaultProps } = await import("@remotion/studio");
            await updateDefaultProps({
              compositionId: "VideoEditor",
              defaultProps: ({ savedDefaultProps }: { savedDefaultProps: Record<string, unknown> }) => ({
                ...savedDefaultProps,
                captions: final,
              }),
            });
          })();
          return final;
        });
      };

      parentWin.addEventListener("mousemove", onMove);
      parentWin.addEventListener("mouseup", onUp);
    },
    [localCaptions, totalDurationMs]
  );

  if (!container || totalDurationMs <= 0) return null;

  const ui = (
    <div
      ref={trackRef}
      style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}
    >
      {localCaptions.map((seg, i) => {
        if (seg.endMs <= seg.startMs) return null;
        // Stretch visual end to next visible segment's start so the bar has
        // No black gaps: extend chip until next caption that starts strictly after.
        let visualEnd = seg.endMs;
        for (let j = i + 1; j < localCaptions.length; j++) {
          const next = localCaptions[j];
          if (next.endMs <= next.startMs) continue;
          if (next.startMs > seg.startMs) {
            visualEnd = Math.max(visualEnd, next.startMs);
            break;
          }
        }
        if (visualEnd <= seg.startMs) visualEnd = seg.startMs + 100;
        const leftPct = (seg.startMs / totalDurationMs) * 100;
        const widthPct = ((visualEnd - seg.startMs) / totalDurationMs) * 100;
        const isActive = i === activeIndex;
        const isDraggingThis = dragging?.segIndex === i;

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              minWidth: 4,
              top: BLOCK_TOP,
              height: BLOCK_H,
            }}
          >
            {/* Block body — click to edit */}
            <div
              onClick={() => setEditingIndex(i)}
              style={{
                position: "absolute",
                left: HANDLE_W,
                right: HANDLE_W,
                top: 0,
                bottom: 0,
                background: isActive ? "#3a3000" : "#1e2a1e",
                border: `1px solid ${isActive || isDraggingThis ? HIGHLIGHT_COLOR : "#3a4a3a"}`,
                borderRadius: 4,
                cursor: "pointer",
                overflow: "hidden",
                display: "flex",
                alignItems: "center",
                paddingLeft: 4,
              }}
            >
              <span
                style={{
                  color: isActive ? HIGHLIGHT_COLOR : "#6a9a6a",
                  fontSize: 10,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  pointerEvents: "none",
                }}
              >
                {seg.text}
              </span>
            </div>

            {/* Left drag handle */}
            <div
              onMouseDown={(e) => startDrag(e, i, "left")}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                width: HANDLE_W,
                bottom: 0,
                cursor: "ew-resize",
                background: isDraggingThis && dragging?.side === "left" ? HIGHLIGHT_COLOR : "rgba(255,224,51,0.25)",
                borderRadius: "4px 0 0 4px",
                zIndex: 2,
              }}
            />

            {/* Right drag handle */}
            <div
              onMouseDown={(e) => startDrag(e, i, "right")}
              style={{
                position: "absolute",
                right: 0,
                top: 0,
                width: HANDLE_W,
                bottom: 0,
                cursor: "ew-resize",
                background: isDraggingThis && dragging?.side === "right" ? HIGHLIGHT_COLOR : "rgba(255,224,51,0.25)",
                borderRadius: "0 4px 4px 0",
                zIndex: 2,
              }}
            />
          </div>
        );
      })}

      {editingIndex !== null && (
        <CaptionEditorPortal
          captions={localCaptions}
          activeIndex={editingIndex}
          onClose={() => setEditingIndex(null)}
          onSaved={(updated) => setLocalCaptions(updated)}
        />
      )}
    </div>
  );

  return createPortal(ui, container);
};

// ── Caption word renderer ─────────────────────────────────────────────────────
const CaptionPage: React.FC<{
  segment: CaptionSegment;
  currentMs: number;
  captionIndex: number;
  allCaptions: CaptionSegment[];
  isStudio: boolean;
  onSaved: (updated: CaptionSegment[]) => void;
}> = ({ segment, currentMs, captionIndex, allCaptions, isStudio, onSaved }) => {
  const { width } = useVideoConfig();
  const [editing, setEditing] = useState(false);

  const fontSize = Math.round(width * 0.066);
  const paddingH = Math.round(width * 0.026);
  const paddingBottom = Math.round(width * 0.1);

  const WORDS_PER_PAGE = 4;

  const words = segment.text.trim().split(/\s+/);
  const msPerWord = (segment.endMs - segment.startMs) / words.length;
  const activeWordIndex = Math.floor((currentMs - segment.startMs) / msPerWord);

  const pageStart = Math.floor(activeWordIndex / WORDS_PER_PAGE) * WORDS_PER_PAGE;
  const pageWords = words.slice(pageStart, pageStart + WORDS_PER_PAGE);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom,
        paddingLeft: paddingH,
        paddingRight: paddingH,
      }}
    >
      <div
        onClick={isStudio ? () => setEditing(true) : undefined}
        style={{
          fontSize,
          fontFamily: "Arial Black, Arial, sans-serif",
          fontWeight: 900,
          whiteSpace: "pre-wrap",
          textAlign: "center",
          lineHeight: 1.2,
          letterSpacing: -0.5,
          cursor: isStudio ? "pointer" : "default",
          borderRadius: 4,
          padding: isStudio ? "2px 6px" : 0,
        }}
        title={isStudio ? "Click para editar" : undefined}
      >
        {pageWords.map((word, i) => {
          const globalIndex = pageStart + i;
          return (
            <span
              key={globalIndex}
              style={{
                color: globalIndex === activeWordIndex ? HIGHLIGHT_COLOR : "white",
                textShadow:
                  globalIndex === activeWordIndex
                    ? `0px 0px 20px ${HIGHLIGHT_COLOR}88, ${SHADOW}`
                    : SHADOW,
              }}
            >
              {word}
              {i < pageWords.length - 1 ? " " : ""}
            </span>
          );
        })}
      </div>

      {editing && (
        <CaptionEditorPortal
          captions={allCaptions}
          activeIndex={captionIndex}
          onClose={() => setEditing(false)}
          onSaved={(updated) => { onSaved(updated); setEditing(false); }}
        />
      )}
    </AbsoluteFill>
  );
};

// ── Main export ───────────────────────────────────────────────────────────────
export const CaptionOverlay: React.FC<{ captions: CaptionSegment[] }> = ({
  captions: captionsProp,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;
  const totalDurationMs = (durationInFrames / fps) * 1000;
  const { isStudio } = getRemotionEnvironment();

  // Local copy so track bar edits reflect immediately in the video overlay too
  const [captions, setCaptions] = useState<CaptionSegment[]>(captionsProp);
  useEffect(() => {
    setCaptions(captionsProp);
  }, [captionsProp]);

  const activeIndex = captions.findIndex(
    (seg) => seg.startMs <= currentMs && seg.endMs > currentMs
  );

  return (
    <>
      {activeIndex !== -1 && (
        <CaptionPage
          segment={captions[activeIndex]}
          currentMs={currentMs}
          captionIndex={activeIndex}
          allCaptions={captions}
          isStudio={isStudio}
          onSaved={setCaptions}
        />
      )}
      {isStudio && (
        <SubtitleTrackBar
          captions={captions}
          totalDurationMs={totalDurationMs}
          activeIndex={activeIndex}
        />
      )}
    </>
  );
};
