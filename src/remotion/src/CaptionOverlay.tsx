import {
  useVideoConfig,
  useCurrentFrame,
  AbsoluteFill,
  getRemotionEnvironment,
  delayRender,
  continueRender,
} from "remotion";
import type { CaptionSegment } from "./schema";
import { useState, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";

// Studio editor chrome accent (the on-video click-to-edit panel) — internal UI,
// not brand-facing.
const HIGHLIGHT_COLOR = "#FFE033";

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
// Opened by clicking a caption on the video (Studio only). NOTE: this still
// persists via a hardcoded compositionId "VideoEditor", which is stale under the
// multi-project Root.tsx — known limitation; the Subtitles tab is the working
// edit path. Kept here for on-video click-to-edit only.
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
  const { fps } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;
  const { isStudio } = getRemotionEnvironment();

  // Local copy so on-video edits reflect immediately in the overlay
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

  if (activeIndex === -1) return null;

  return (
    <CaptionPage
      segment={captions[activeIndex]}
      currentMs={currentMs}
      captionIndex={activeIndex}
      allCaptions={captions}
      isStudio={isStudio}
      onSaved={setCaptions}
    />
  );
};
