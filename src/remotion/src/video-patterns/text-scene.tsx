import * as React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type Stanza = string[];

export type StanzaMode = "accumulate" | "replace";

type StanzaTiming = {
  start: number;
  enter: number;
  enterEnd: number;
  holdEnd: number;
  exitEnd: number;
};

const computeStanzaLayout = (
  stanzas: Stanza[],
  mode: StanzaMode,
  lineFadeFrames: number,
  lineDelayFrames: number,
  stanzaHoldFrames: number,
  stanzaExitFrames: number,
  interStanzaGapFrames: number,
): StanzaTiming[] => {
  const out: StanzaTiming[] = [];
  let cursor = 0;
  for (const stanza of stanzas) {
    const enter = (stanza.length - 1) * lineDelayFrames + lineFadeFrames;
    const start = cursor;
    const enterEnd = start + enter;
    const holdEnd = enterEnd + stanzaHoldFrames;
    const exitEnd = mode === "replace" ? holdEnd + stanzaExitFrames : holdEnd;
    out.push({ start, enter, enterEnd, holdEnd, exitEnd });
    cursor = mode === "replace" ? exitEnd : enterEnd + interStanzaGapFrames;
  }
  return out;
};

export const stanzaSceneDurationFrames = (
  stanzas: Stanza[],
  mode: StanzaMode = "accumulate",
  lineFadeFrames?: number,
  lineDelayFrames?: number,
  stanzaHoldFrames?: number,
  sceneOutroFadeFrames?: number,
  fadeToBlackFrames?: number,
  fps: number = 30,
): number => {
  // Default values if not provided (sensible defaults)
  const actualLineFadeFrames = lineFadeFrames ?? 18;
  const actualLineDelayFrames = lineDelayFrames ?? 44;
  const actualStanzaHoldFrames = stanzaHoldFrames ?? Math.round(3.5 * fps);
  const actualStanzaExitFrames = mode === "replace" ? 24 : 0; // Only used in replace mode
  const actualInterStanzaGapFrames = mode === "accumulate" ? 35 : 0;

  const layout = computeStanzaLayout(
    stanzas,
    mode,
    actualLineFadeFrames,
    actualLineDelayFrames,
    actualStanzaHoldFrames,
    actualStanzaExitFrames,
    actualInterStanzaGapFrames,
  );
  const last = layout[layout.length - 1];
  return (
    (last?.exitEnd ?? 0) +
    Math.max(sceneOutroFadeFrames ?? 12, fadeToBlackFrames ?? 24)
  );
};

type TextSceneProps = {
  stanzas: Stanza[];
  mode?: StanzaMode;
  lineFadeFrames?: number;
  lineDelayFrames?: number;
  stanzaHoldSec?: number;
  stanzaExitFrames?: number;
  sceneOutroFadeFrames?: number;
  fadeToBlackFrames?: number;
  fadeInFromBlack?: boolean;
  fadeOutToBlack?: boolean;
  textStyle?: React.CSSProperties;
  containerStyle?: React.CSSProperties;
  align?: "left" | "center" | "right";
};

export const TextScene: React.FC<TextSceneProps> = ({
  stanzas,
  mode = "accumulate",
  lineFadeFrames,
  lineDelayFrames,
  stanzaHoldSec = 3.5,
  stanzaExitFrames,
  sceneOutroFadeFrames = 12,
  fadeToBlackFrames = 24,
  fadeInFromBlack = true,
  fadeOutToBlack = true,
  textStyle,
  containerStyle,
  align = "center",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  const stanzaHoldFrames = Math.round(stanzaHoldSec * fps);

  const layout = computeStanzaLayout(
    stanzas,
    mode,
    lineFadeFrames ?? 18,
    lineDelayFrames ?? 44,
    stanzaHoldFrames,
    stanzaExitFrames ?? 24,
    mode === "accumulate" ? 35 : 0,
  );

  // Text outro fade (slightly ahead of the black bridge so text exits before full black)
  const sceneOpacity = interpolate(
    frame,
    [durationInFrames - (sceneOutroFadeFrames ?? 12), durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Black bridge
  const blackIn = fadeInFromBlack
    ? interpolate(frame, [0, fadeToBlackFrames ?? 24], [1, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;
  const blackOut = fadeOutToBlack
    ? interpolate(
        frame,
        [durationInFrames - (fadeToBlackFrames ?? 24), durationInFrames],
        [0, 1],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
      )
    : 0;
  const blackOpacity = Math.max(blackIn, blackOut);

  const BlackBridge = (
    <AbsoluteFill
      style={{ backgroundColor: "#000", opacity: blackOpacity }}
    />
  );

  const defaultTextStyle: React.CSSProperties = {
    fontFamily: '"Cormorant Garamond", "Playfair Display", Georgia, serif',
    color: "#1F3329",
    fontSize: 56,
    lineHeight: 1.35,
    fontWeight: 500,
    textShadow: "0 2px 8px rgba(255,255,255,0.55)",
    ...(textStyle ?? {}),
  };

  // Accumulate mode: container-level fade (whole stack exits together)
  // Replace mode: each stanza owns its opacity; container has no extra fade
  //               (the black bridge handles scene-end; combining both causes double-fade on last stanza)
  const containerStyles: React.CSSProperties = {
    alignItems: "center",
    justifyContent: "center",
    padding: "6% 12%",
    opacity: mode === "accumulate" ? sceneOpacity : 1,
    ...(containerStyle ?? {}),
  };

  if (mode === "accumulate") {
    return (
      <AbsoluteFill>
        <AbsoluteFill style={containerStyles}>
          <div
            style={{
              ...defaultTextStyle,
              textAlign: align,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            {stanzas.map((stanza, si) => {
              const { start } = layout[si];
              return (
                <div
                  key={si}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: si < stanzas.length - 1 ? 28 : 0,
                  }}
                >
                  {stanza.map((line, li) => {
                    const lineStart = start + li * (lineDelayFrames ?? 44);
                    const opacity = interpolate(
                      frame,
                      [lineStart, lineStart + (lineFadeFrames ?? 18)],
                      [0, 1],
                      {
                        extrapolateLeft: "clamp",
                        extrapolateRight: "clamp",
                        easing: Easing.bezier(0.16, 1, 0.3, 1),
                      },
                    );
                    const translateY = interpolate(
                      frame,
                      [lineStart, lineStart + (lineFadeFrames ?? 18)],
                      [18, 0],
                      {
                        extrapolateLeft: "clamp",
                        extrapolateRight: "clamp",
                        easing: Easing.bezier(0.16, 1, 0.3, 1),
                      },
                    );
                    return (
                      <div
                        key={li}
                        style={{
                          opacity,
                          transform: `translateY(${translateY}px)`,
                        }}
                      >
                        {line}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </AbsoluteFill>
        {BlackBridge}
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill>
      <AbsoluteFill style={containerStyles}>
        {stanzas.map((stanza, si) => {
          const t = layout[si];
          if (frame < t.start - 2 || frame > t.exitEnd) return null;
          const stanzaOpacity = interpolate(
            frame,
            [t.holdEnd, t.exitEnd],
            [1, 0],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          );
          return (
            <AbsoluteFill
              key={si}
              style={{
                alignItems: "center",
                justifyContent: "center",
                padding: "6% 12%",
                opacity: stanzaOpacity,
              }}
            >
              <div
                style={{
                  ...defaultTextStyle,
                  textAlign: align,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                }}
              >
                {stanza.map((line, li) => {
                  const lineStart = t.start + li * (lineDelayFrames ?? 44);
                  const opacity = interpolate(
                    frame,
                    [lineStart, lineStart + (lineFadeFrames ?? 18)],
                    [0, 1],
                    {
                      extrapolateLeft: "clamp",
                      extrapolateRight: "clamp",
                      easing: Easing.bezier(0.16, 1, 0.3, 1),
                    },
                  );
                  const translateY = interpolate(
                    frame,
                    [lineStart, lineStart + (lineFadeFrames ?? 18)],
                    [18, 0],
                    {
                      extrapolateLeft: "clamp",
                      extrapolateRight: "clamp",
                      easing: Easing.bezier(0.16, 1, 0.3, 1),
                    },
                  );
                  return (
                    <div
                      key={li}
                      style={{
                        opacity,
                        transform: `translateY(${translateY}px)`,
                      }}
                    >
                      {line}
                    </div>
                  );
                })}
              </div>
            </AbsoluteFill>
          );
        })}
      </AbsoluteFill>
      {BlackBridge}
    </AbsoluteFill>
  );
};