import { useCurrentFrame, interpolate, spring, useVideoConfig } from "remotion";

// Reusable text reveal component with spring animation
// Based on agendia's TextReveal but generalized for any project

type Props = {
  children: React.ReactNode;
  delay?: number; // frames to wait before starting animation
  duration?: number; // animation duration in frames
  // Text variants
  variant?: "serif" | "sans" | "caps";
  size?: number; // font size in px
  color?: string; // text color
  weight?: number; // font weight
  italic?: boolean; // font style
  align?: "left" | "center" | "right";
  letterSpacing?: number; // letter spacing in px
  lineHeight?: number; // line height multiplier
  style?: React.CSSProperties; // additional inline styles
};

// Standard fade-in-up appearance
export const TextReveal: React.FC<Props> = ({
  children,
  delay = 0,
  duration = 18,
  variant = "serif",
  size,
  color,
  weight,
  italic,
  align = "center",
  letterSpacing,
  lineHeight = 1.15,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const localFrame = frame - delay;

  // Don't animate before delay
  if (localFrame < 0) {
    return null; // or return children with zero opacity if preferred
  }

  const opacity = interpolate(localFrame, [0, duration], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const translateY = spring({
    frame: Math.max(0, localFrame),
    fps,
    config: { damping: 14, stiffness: 80, mass: 1 },
    durationInFrames: duration,
  });
  // spring: 0 → 1; maps to 30 → 0 px offset
  const ty = (1 - translateY) * 30;

  // Default values - can be overridden by theme/context or props
  let fontFamily = '"Cormorant Garamond", "Playfair Display", Georgia, serif';
  let defaultWeight = 600;
  let defaultItalic = true;
  let defaultLetterSpacing = letterSpacing ?? -0.5;
  let textTransform: React.CSSProperties["textTransform"] = "none";

  if (variant === "sans") {
    fontFamily = '"Outfit", system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
    defaultWeight = 500;
    defaultItalic = false;
    defaultLetterSpacing = letterSpacing ?? 0;
  } else if (variant === "caps") {
    fontFamily = '"Outfit", system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
    defaultWeight = 600;
    defaultItalic = false;
    defaultLetterSpacing = letterSpacing ?? 3;
    textTransform = "uppercase";
  }

  // Default colors - can be overridden by theme/context or props
  const defaultColor = color ?? "#1F3329"; // dark green-black (medical foreground)

  return (
    <div
      style={{
        opacity,
        transform: `translateY(${ty}px)`,
        fontFamily,
        fontSize: size ?? 80, // default size if not provided
        color: defaultColor,
        fontWeight: weight ?? defaultWeight,
        fontStyle: italic ?? defaultItalic ? "italic" : "normal",
        textAlign: align,
        letterSpacing: defaultLetterSpacing,
        lineHeight: lineHeight ?? 1.15,
        textTransform,
        ...style,
      }}
    >
      {children}
    </div>
  );
};