import {
  useCurrentFrame,
  useVideoConfig,
  Sequence,
  Img,
  staticFile,
  interpolate,
  getRemotionEnvironment,
} from "remotion";
import { useState, useCallback, useRef } from "react";
import type { ImageOverlay } from "./schema";

const FADE_FRAMES = 10;

const SingleImageOverlay: React.FC<{
  overlay: ImageOverlay;
  index: number;
  durationFrames: number;
  project?: string;
}> = ({ overlay, index, durationFrames, project }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const { isStudio } = getRemotionEnvironment();

  const [dragging, setDragging] = useState(false);
  // Live drag position (Studio only). null → use the persisted overlay.x/y.
  // Lets the image follow the cursor via local re-renders WITHOUT writing to the
  // Studio store on every mousemove — the durable write lands once on mouseup.
  const [livePos, setLivePos] = useState<{ x: number; y: number } | null>(null);
  const dragStart = useRef<{ mouseX: number; mouseY: number; x: number; y: number } | null>(null);

  const posX = livePos?.x ?? overlay.x;
  const posY = livePos?.y ?? overlay.y;
  const imageWidth = Math.round(width * 0.35);
  const left = Math.round(posX * width);
  const top = Math.round(posY * height);

  // Fade in/out. The interpolate input range must be strictly increasing, which
  // breaks for very short overlays (fadeOutStart could reach/exceed durationFrames),
  // so cap it below durationFrames and hold full opacity when there's no room to fade.
  const halfDur = Math.max(1, Math.floor(durationFrames / 2));
  const fadeDuration = Math.min(FADE_FRAMES, halfDur);
  const fadeOutStart = Math.min(
    durationFrames - 1,
    Math.max(fadeDuration + 1, durationFrames - fadeDuration)
  );
  const opacity =
    durationFrames < 3
      ? 1
      : interpolate(
          frame,
          [0, fadeDuration, fadeOutStart, durationFrames],
          [0, 1, 1, 0],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
        );

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!isStudio) return;
      e.stopPropagation();
      setDragging(true);
      const origin = { mouseX: e.clientX, mouseY: e.clientY, x: overlay.x, y: overlay.y };
      dragStart.current = origin;
      let latest = { x: overlay.x, y: overlay.y };

      // Local-only live feedback — no store write per move (was hammering
      // updateDefaultProps on every mousemove with an async handler).
      const onMove = (ev: MouseEvent) => {
        const dx = (ev.clientX - origin.mouseX) / width;
        const dy = (ev.clientY - origin.mouseY) / height;
        latest = {
          x: Math.max(0, Math.min(0.65, origin.x + dx)),
          y: Math.max(0, Math.min(0.60, origin.y + dy)),
        };
        setLivePos(latest);
      };

      const onUp = async () => {
        setDragging(false);
        dragStart.current = null;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        if (!project) {
          setLivePos(null);
          return;
        }
        // Single durable write on release. compositionId is the project name —
        // Root.tsx registers <Composition id={p.name}>; the old hardcoded
        // "VideoEditor" id matched no composition, so every write silently no-op'd.
        const { updateDefaultProps } = await import("@remotion/studio");
        await updateDefaultProps({
          compositionId: project,
          defaultProps: ({ savedDefaultProps }: { savedDefaultProps: Record<string, unknown> }) => {
            const overlays = (savedDefaultProps.imageOverlays as ImageOverlay[]) ?? [];
            return {
              ...savedDefaultProps,
              imageOverlays: overlays.map((o, i) =>
                i === index ? { ...o, x: latest.x, y: latest.y } : o
              ),
            };
          },
        });
        setLivePos(null); // store now holds latest → drop the local override
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [isStudio, overlay.x, overlay.y, width, height, index, project]
  );

  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width: imageWidth,
        opacity,
        cursor: isStudio ? (dragging ? "grabbing" : "grab") : "default",
        userSelect: "none",
      }}
      onMouseDown={isStudio ? onMouseDown : undefined}
    >
      <Img
        src={staticFile(`images/${overlay.file}`)}
        style={{ width: "100%", height: "auto", borderRadius: 12, pointerEvents: "none" }}
      />
      {isStudio && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            border: dragging ? "2px solid #FFE033" : "2px dashed rgba(255,224,51,0.5)",
            borderRadius: 12,
            pointerEvents: "none",
          }}
        />
      )}
    </div>
  );
};

export const ImageOverlayLayer: React.FC<{ overlays: ImageOverlay[]; project?: string }> = ({
  overlays,
  project,
}) => {
  const { fps } = useVideoConfig();

  if (!overlays || overlays.length === 0) return null;

  return (
    <>
      {overlays.map((overlay, index) => {
        const startFrame = Math.round((overlay.timestamp_ms / 1000) * fps);
        const durationFrames = Math.max(1, Math.round((overlay.duration_ms / 1000) * fps));
        return (
          <Sequence
            key={`${overlay.file}-${index}`}
            from={startFrame}
            durationInFrames={durationFrames}
            layout="none"
          >
            <SingleImageOverlay
              overlay={overlay}
              index={index}
              durationFrames={durationFrames}
              project={project}
            />
          </Sequence>
        );
      })}
    </>
  );
};
