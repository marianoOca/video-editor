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
  allOverlays: ImageOverlay[];
  durationFrames: number;
}> = ({ overlay, index, allOverlays, durationFrames }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const { isStudio } = getRemotionEnvironment();

  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ mouseX: number; mouseY: number; x: number; y: number } | null>(null);

  const imageWidth = Math.round(width * 0.35);
  const left = Math.round(overlay.x * width);
  const top = Math.round(overlay.y * height);

  const opacity = interpolate(
    frame,
    [0, FADE_FRAMES, durationFrames - FADE_FRAMES, durationFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!isStudio) return;
      e.stopPropagation();
      setDragging(true);
      dragStart.current = {
        mouseX: e.clientX,
        mouseY: e.clientY,
        x: overlay.x,
        y: overlay.y,
      };

      const onMove = async (ev: MouseEvent) => {
        if (!dragStart.current) return;
        const dx = (ev.clientX - dragStart.current.mouseX) / width;
        const dy = (ev.clientY - dragStart.current.mouseY) / height;
        const newX = Math.max(0, Math.min(0.65, dragStart.current.x + dx));
        const newY = Math.max(0, Math.min(0.60, dragStart.current.y + dy));

        const { updateDefaultProps } = await import("@remotion/studio");
        await updateDefaultProps({
          compositionId: "VideoEditor",
          defaultProps: ({ savedDefaultProps }: { savedDefaultProps: Record<string, unknown> }) => {
            const overlays = (savedDefaultProps.imageOverlays as ImageOverlay[]) ?? [];
            return {
              ...savedDefaultProps,
              imageOverlays: overlays.map((o, i) =>
                i === index ? { ...o, x: newX, y: newY } : o
              ),
            };
          },
        });
      };

      const onUp = () => {
        setDragging(false);
        dragStart.current = null;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [isStudio, overlay.x, overlay.y, width, height, index]
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

export const ImageOverlayLayer: React.FC<{ overlays: ImageOverlay[] }> = ({
  overlays,
}) => {
  const { fps } = useVideoConfig();

  if (!overlays || overlays.length === 0) return null;

  return (
    <>
      {overlays.map((overlay, index) => {
        const startFrame = Math.round((overlay.timestamp_ms / 1000) * fps);
        const durationFrames = Math.round((overlay.duration_ms / 1000) * fps);
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
              allOverlays={overlays}
              durationFrames={durationFrames}
            />
          </Sequence>
        );
      })}
    </>
  );
};
