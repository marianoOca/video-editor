import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont as loadItaliana } from "@remotion/google-fonts/Italiana";
import { loadFont as loadOutfit } from "@remotion/google-fonts/Outfit";
import type { TitleCard as TitleCardData } from "./schema";

// Brand-aligned title card (Mariano | Claude para tu Negocio).
// Top banner: Cream solid background, Italiana title (Deep Forest, one optional
// word in Gold), Outfit pill subtitle. Fully rendered at frame 0, fades out at
// the end. Single source of truth — tweak the look here, not per-video.
const { fontFamily: italiana } = loadItaliana("normal", {
  weights: ["400"],
  subsets: ["latin"],
});
const { fontFamily: outfit } = loadOutfit("normal", {
  weights: ["500", "700"],
  subsets: ["latin"],
});

const FADE_OUT_FRAMES = 20;

const COLORS = {
  cream: "#FAF5ED",
  forest: "#1F3329",
  gold: "#D4A03A",
  rose: "#EBD9D3",
};

const renderTitleWithHighlight = (title: string, highlight: string) => {
  if (!highlight) return title;
  const idx = title.toLowerCase().indexOf(highlight.toLowerCase());
  if (idx === -1) return title;
  const before = title.slice(0, idx);
  const match = title.slice(idx, idx + highlight.length);
  const after = title.slice(idx + highlight.length);
  return (
    <>
      {before}
      <span style={{ color: COLORS.gold }}>{match}</span>
      {after}
    </>
  );
};

const TitleCardItem: React.FC<{
  title: string;
  titleHighlight: string;
  subtitle: string;
  durationInFrames: number;
}> = ({ title, titleHighlight, subtitle, durationInFrames }) => {
  const frame = useCurrentFrame();

  const outStart = durationInFrames - FADE_OUT_FRAMES;
  const opacity = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const translateY = interpolate(frame, [outStart, durationInFrames], [0, -28], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const scale = interpolate(frame, [outStart, durationInFrames], [1, 0.96], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "flex-start",
        paddingTop: "8%",
        paddingLeft: 48,
        paddingRight: 48,
      }}
    >
      <div
        style={{
          opacity,
          transform: `translateY(${translateY}px) scale(${scale})`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
          padding: "40px 52px 46px",
          borderRadius: 28,
          background: COLORS.cream,
          border: `1px solid rgba(31,51,41,0.10)`,
          boxShadow: `0 22px 60px rgba(31,51,41,0.32), 0 0 0 10px rgba(235,217,211,0.30)`,
        }}
      >
        <div
          style={{
            width: 72,
            height: 5,
            borderRadius: 999,
            background: COLORS.gold,
          }}
        />
        <div
          style={{
            color: COLORS.forest,
            fontFamily: italiana,
            fontWeight: 400,
            fontSize: 96,
            lineHeight: 1.02,
            textAlign: "center",
            letterSpacing: 0.5,
          }}
        >
          {renderTitleWithHighlight(title, titleHighlight)}
        </div>
        {subtitle ? (
          <div
            style={{
              color: COLORS.forest,
              background: COLORS.gold,
              fontFamily: outfit,
              fontWeight: 700,
              fontSize: 30,
              lineHeight: 1,
              padding: "12px 24px",
              borderRadius: 999,
              textTransform: "uppercase",
              letterSpacing: 2.2,
              boxShadow: "0 6px 18px rgba(212,160,58,0.32)",
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

export const TitleCardLayer: React.FC<{ titleCards: TitleCardData[] }> = ({ titleCards }) => {
  const { fps } = useVideoConfig();

  return (
    <>
      {titleCards.map((card, i) => {
        const from = Math.round((card.startMs / 1000) * fps);
        const durationInFrames = Math.max(1, Math.round((card.durationMs / 1000) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames} layout="none">
            <TitleCardItem
              title={card.title}
              titleHighlight={card.titleHighlight ?? ""}
              subtitle={card.subtitle}
              durationInFrames={durationInFrames}
            />
          </Sequence>
        );
      })}
    </>
  );
};
