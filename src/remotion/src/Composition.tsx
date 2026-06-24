import {
  AbsoluteFill,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  getRemotionEnvironment,
} from "remotion";
import { Video } from "@remotion/media";
import { CaptionOverlay } from "./CaptionOverlay";
import { ImageOverlayLayer } from "./ImageOverlay";
import { TitleCardLayer } from "./TitleCard";
import type { CompositionProps } from "./schema";
import { Component } from "react";

// Locks the video's timeline position in Remotion Studio.
//
// In dev, Studio's `setup-sequence-stack-traces` entry proxies React.createElement/jsx
// to inject `stack: new Error().stack` onto every Sequence/Video/Audio element — BUT only
// when the caller did not already supply a `stack` prop:
//     props?.stack ? props : { ...props, stack: new Error().stack }
// That real stack is the source file:line Studio rewrites when you drag the clip on the
// timeline (it inserts/overwrites `from={n}` in this file — desyncs captions, gaps the start).
//
// By passing our own `stack`, we keep Studio from capturing the real source location. Studio
// resolves the drag target via getLocationOfSequence() → parseStack(), which only keeps lines
// matching an `at … :line` / Firefox frame regex. This sentinel matches neither, so it parses
// to zero frames → null location → the timeline drag writeback no-ops (cannot edit/insert
// `from` in source). The track + audio waveform stay visible (showInTimeline is untouched),
// and `stack` is Studio-dev-only metadata — render and audio ignore it. See HANDOFF-video-timeline-drag.md.
const TIMELINE_DRAG_LOCK = "video-track-locked-no-timeline-drag";

// ── Frame bridge (Studio only) ─────────────────────────────────────────────────
// The Subtitles tab lives in Studio chrome, where useCurrentFrame() does not
// work. This component runs inside the composition (where it does), and publishes
// the playhead to the parent (Studio) window so the tab can follow it. Always
// mounted — independent of captionsEnabled — so youtube projects scroll too.
const FrameBridge: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const parent = (typeof window !== "undefined" ? window.parent : undefined) as
    | (Window & { __veSubtitleFrame?: { frame: number; fps: number } })
    | undefined;
  if (parent) parent.__veSubtitleFrame = { frame, fps };
  return null;
};

// Sidecar endpoint that regenerates a corrupt project from its last valid
// pipeline step (same server the Subtitles tab uses). CORS-open, localhost only.
const FIX_URL = "http://127.0.0.1:9848/fix";

// ── Error boundary ────────────────────────────────────────────────────────────
// Catches canvas <Video> decode failures (async, via getDerivedStateFromError).
// When the file is corrupt it offers a Fix button that POSTs to the sidecar to
// regenerate the project in place — Studio HMR then remounts this boundary fresh.
// NOTE: the timeline audio-waveform "Decoding error" lives in a Studio worker and
// never reaches here; the Subtitles-tab banner (ffprobe health check) covers that.
class VideoErrorBoundary extends Component<
  { src: string; project?: string; children: React.ReactNode },
  { error: string | null; fixing: boolean; fixMsg: string | null }
> {
  state = { error: null, fixing: false, fixMsg: null };

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

  fix = async () => {
    if (!this.props.project || this.state.fixing) return;
    this.setState({ fixing: true, fixMsg: null });
    try {
      const res = await fetch(FIX_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: this.props.project }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.log || data.error || `sidecar error ${res.status}`);
      }
      // Snapshot rewritten — HMR reloads the composition and remounts this fresh.
      this.setState({ fixMsg: "Fixed — reloading…" });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.setState({
        fixing: false,
        fixMsg: msg.includes("Failed to fetch")
          ? "Sidecar not running — start it: cd src/pipeline && python3 sidecar.py"
          : msg,
      });
    }
  };

  render() {
    if (this.state.error) {
      return (
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            flexDirection: "column",
            gap: 12,
            padding: 24,
            textAlign: "center",
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
          {this.props.project && (
            <button
              onClick={this.fix}
              disabled={this.state.fixing}
              style={{
                marginTop: 8,
                border: "none",
                borderRadius: 8,
                padding: "10px 28px",
                fontSize: 15,
                fontWeight: 800,
                fontFamily: "system-ui, sans-serif",
                color: "#fff",
                background: "#0b84f3",
                cursor: this.state.fixing ? "default" : "pointer",
                opacity: this.state.fixing ? 0.6 : 1,
              }}
            >
              {this.state.fixing ? "Fixing…" : "Fix"}
            </button>
          )}
          {this.state.fixMsg && (
            <div style={{ color: "#A6A7A9", fontSize: 12, fontFamily: "monospace", maxWidth: 520 }}>
              {this.state.fixMsg}
            </div>
          )}
        </AbsoluteFill>
      );
    }
    return this.props.children;
  }
}

// ── Composition ───────────────────────────────────────────────────────────────
export const VideoComposition: React.FC<CompositionProps> = ({
  videoSrc,
  videoVersion,
  project,
  imageOverlays,
  captions,
  titleCards,
  captionsEnabled,
}) => {
  // Cache-bust so Studio reloads the re-cut edited.mp4 (same path on disk).
  const videoUrl = `${staticFile(videoSrc)}?v=${videoVersion ?? 0}`;
  return (
    <AbsoluteFill style={{ background: "black" }}>
      <VideoErrorBoundary src={videoSrc} project={project}>
        <Video
          src={videoUrl}
          objectFit="cover"
          style={{ width: "100%", height: "100%" }}
          stack={TIMELINE_DRAG_LOCK}
          delayRenderTimeoutInMilliseconds={300_000} />
      </VideoErrorBoundary>
      <ImageOverlayLayer overlays={imageOverlays ?? []} project={project} />
      {captionsEnabled ? <CaptionOverlay captions={captions ?? []} /> : null}
      <TitleCardLayer titleCards={titleCards ?? []} />
      {getRemotionEnvironment().isStudio ? <FrameBridge /> : null}
    </AbsoluteFill>
  );
};
