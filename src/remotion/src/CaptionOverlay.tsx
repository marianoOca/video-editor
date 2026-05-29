import {
  useVideoConfig,
  useCurrentFrame,
  AbsoluteFill,
  getRemotionEnvironment,
  delayRender,
  continueRender,
} from "remotion";
import type { CaptionSegment } from "./schema";
import { useState, useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

// Studio editor chrome accent (track bar, edit panel) — internal UI, not brand-facing.
const HIGHLIGHT_COLOR = "#FFE033";
const SHADOW = "0px 3px 8px rgba(0,0,0,0.9)";

// ── Brand caption styling (@marian.o.ia) ─────────────────────────────────────
// Rendered subtitle follows the brand system: Outfit Bold uppercase, Cream
// inactive words with Forest halo, active word in a Cream pill with Deep
// Forest text + Gold outline ring + subtle scale pop.
const BRAND_FONT = "Outfit, sans-serif";
const BRAND_WORD = "#FAF5ED"; // Cream
const BRAND_FOREST = "#1F3329"; // Deep Forest
const BRAND_GOLD = "#D4A03A"; // Gold
// Multi-layer Forest halo so Cream words stay legible on busy video bgs
const FOREST_HALO =
  "0px 0px 14px rgba(31,51,41,0.95), 0px 3px 10px rgba(31,51,41,0.85), 0px 0px 4px rgba(31,51,41,1)";

let outfitInjected = false;
const useOutfitFont = () => {
  const [handle] = useState(() => delayRender("Loading Outfit caption font"));
  useEffect(() => {
    if (outfitInjected) {
      continueRender(handle);
      return;
    }
    outfitInjected = true;
    const style = document.createElement("style");
    style.innerHTML =
      "@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&display=swap');";
    document.head.appendChild(style);
    Promise.all(
      ['700 100px "Outfit"', '600 100px "Outfit"'].map((spec) =>
        (document as any).fonts?.load(spec).catch(() => null)
      )
    )
      .then(() => continueRender(handle))
      .catch(() => continueRender(handle));
  }, [handle]);
};

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
  const updateGenRef = useRef(0);

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
        // Persist final state; generation guard prevents stale writes from
        // concurrent drag-release sequences overwriting a newer update.
        updateGenRef.current += 1;
        const gen = updateGenRef.current;
        setLocalCaptions((final) => {
          (async () => {
            if (gen !== updateGenRef.current) return;
            const { updateDefaultProps } = await import("@remotion/studio");
            if (gen !== updateGenRef.current) return;
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
  const { width, height } = useVideoConfig();
  const [editing, setEditing] = useState(false);

  const fontSize = Math.round(width * 0.07);
  const paddingH = Math.round(width * 0.02);
  const paddingBottom = Math.round(height * 0.2);

  const WORDS_PER_PAGE = 3;

  const words = segment.text.trim().split(/\s+/);
  const hasWordTimings = segment.words && segment.words.length === words.length;

  // Index of last word that has started — drives page navigation
  const pageWordIndex = (() => {
    if (hasWordTimings) {
      let idx = 0;
      for (let i = 0; i < segment.words!.length; i++) {
        if (currentMs >= segment.words![i].startMs) idx = i;
      }
      return idx;
    }
    const msPerWord = (segment.endMs - segment.startMs) / words.length;
    return Math.min(Math.floor((currentMs - segment.startMs) / msPerWord), words.length - 1);
  })();

  const pageStart = Math.floor(pageWordIndex / WORDS_PER_PAGE) * WORDS_PER_PAGE;
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
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          alignItems: "center",
          gap: 14,
          fontSize,
          fontFamily: BRAND_FONT,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: 1.5,
          lineHeight: 1.2,
          cursor: isStudio ? "pointer" : "default",
        }}
        title={isStudio ? "Click para editar" : undefined}
      >
        {pageWords.map((word, i) => {
          const globalIndex = pageStart + i;
          const isActive = globalIndex === pageWordIndex;
          return (
            <span
              key={globalIndex}
              style={{
                color: isActive ? BRAND_FOREST : BRAND_WORD,
                background: isActive ? BRAND_WORD : "transparent",
                padding: "8px 8px",
                borderRadius: 14,
                textShadow: isActive ? "none" : FOREST_HALO,
                boxShadow: isActive
                  ? `0 10px 24px rgba(31,51,41,0.5), 0 0 0 2px ${BRAND_GOLD}`
                  : "none",
                display: "inline-block",
              }}
            >
              {word}
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
  useOutfitFont();
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

  // Show caption i from its startMs until the NEXT caption starts (bridges inter-segment gaps)
  const activeIndex = (() => {
    for (let i = 0; i < captions.length; i++) {
      const nextStart =
        i + 1 < captions.length ? captions[i + 1].startMs : captions[i].endMs;
      if (captions[i].startMs <= currentMs && currentMs < nextStart) return i;
    }
    return -1;
  })();

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
