import { AbsoluteFill, staticFile } from "remotion";
import { Video } from "@remotion/media";
import { CaptionOverlay } from "./CaptionOverlay";
import { ImageOverlayLayer } from "./ImageOverlay";
import type { CompositionProps } from "./schema";
import { Component } from "react";

// ── Error boundary ────────────────────────────────────────────────────────────
class VideoErrorBoundary extends Component<
  { src: string; children: React.ReactNode },
  { error: string | null }
> {
  state = { error: null };

  static getDerivedStateFromError(err: Error) {
    const msg = err.message ?? String(err);
    if (
      msg.includes("UnsupportedInputFormat") ||
      msg.includes("unrecognizable format") ||
      msg.includes("unsupported")
    ) {
      return { error: "Formato de video no soportado" };
    }
    return { error: msg };
  }

  render() {
    if (this.state.error) {
      return (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            flexDirection: "column",
            gap: 12,
            background: "#111",
          }}
        >
          <div style={{ fontSize: 48 }}>⚠️</div>
          <div
            style={{
              color: "#fff",
              fontSize: 18,
              fontFamily: "system-ui, sans-serif",
              fontWeight: 600,
            }}
          >
            {this.state.error}
          </div>
          <div
            style={{
              color: "#888",
              fontSize: 13,
              fontFamily: "monospace",
            }}
          >
            {this.props.src}
          </div>
        </AbsoluteFill>
      );
    }
    return this.props.children;
  }
}

// ── Composition ───────────────────────────────────────────────────────────────
export const VideoComposition: React.FC<CompositionProps> = ({
  videoSrc,
  imageOverlays,
  captions,
}) => {
  return (
    <AbsoluteFill style={{ background: "black" }}>
      <VideoErrorBoundary src={videoSrc}>
        <Video
          src={staticFile(videoSrc)}
          style={{ width: "100%", height: "100%" }}
          objectFit="cover"
        />
      </VideoErrorBoundary>
      <ImageOverlayLayer overlays={imageOverlays ?? []} />
      <CaptionOverlay captions={captions ?? []} />
    </AbsoluteFill>
  );
};
