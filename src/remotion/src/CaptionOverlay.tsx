import {
  useVideoConfig,
  useCurrentFrame,
  AbsoluteFill,
  getRemotionEnvironment,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Outfit";
import type { CaptionSegment } from "./schema";
import { activeCaptionIndex } from "./caption-utils";

// ── Brand caption styling (@marian.o.ia) ─────────────────────────────────────
// Rendered subtitle follows the brand system: Outfit Bold uppercase, Cream
// inactive words with Forest halo, active word in a Cream pill with Deep
// Forest text + Gold outline ring + subtle scale pop.
// Outfit is loaded the Remotion-recommended way (same as TitleCard): loadFont
// handles delayRender/continueRender internally — no manual @import or
// document.fonts dance, so it works offline and during a CLI render.
const { fontFamily: BRAND_FONT } = loadFont("normal", {
  weights: ["400", "600", "700"],
  subsets: ["latin"],
});
const BRAND_WORD = "#FAF5ED"; // Cream
const BRAND_FOREST = "#1F3329"; // Deep Forest
const BRAND_GOLD = "#D4A03A"; // Gold
// Multi-layer Forest halo so Cream words stay legible on busy video bgs
const FOREST_HALO =
  "0px 0px 14px rgba(31,51,41,0.95), 0px 3px 10px rgba(31,51,41,0.85), 0px 0px 4px rgba(31,51,41,1)";

// ── Caption word renderer ─────────────────────────────────────────────────────
const CaptionPage: React.FC<{
  segment: CaptionSegment;
  currentMs: number;
  captionIndex: number;
  isStudio: boolean;
}> = ({ segment, currentMs, captionIndex, isStudio }) => {
  const { width, height } = useVideoConfig();

  // Click a caption on the video → ask the Studio Subtitles tab to open its
  // inline editor on this caption. The tab lives in the parent window and polls
  // window.__veEditRequest in its rAF loop (same channel as FrameBridge). The
  // nonce makes a repeat click on the same caption re-trigger.
  const requestEdit = () => {
    const w = (window.parent ?? window) as Window & {
      __veEditRequest?: { index: number; nonce: number };
    };
    w.__veEditRequest = { index: captionIndex, nonce: (w.__veEditRequest?.nonce ?? 0) + 1 };
  };

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
        onClick={isStudio ? requestEdit : undefined}
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
    </AbsoluteFill>
  );
};

// ── Main export ───────────────────────────────────────────────────────────────
export const CaptionOverlay: React.FC<{ captions: CaptionSegment[] }> = ({
  captions,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;
  const { isStudio } = getRemotionEnvironment();

  // Read captions straight from props — edits flow through the Studio store
  // (the Subtitles tab's setDefaultProps), which re-renders this overlay. No
  // local mirror needed.
  const activeIndex = activeCaptionIndex(captions, currentMs);
  if (activeIndex === -1) return null;

  return (
    <CaptionPage
      segment={captions[activeIndex]}
      currentMs={currentMs}
      captionIndex={activeIndex}
      isStudio={isStudio}
    />
  );
};
