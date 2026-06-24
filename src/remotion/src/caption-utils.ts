import type { CaptionSegment } from "./schema";

// Active caption at a playhead position (ms). A caption shows from its startMs
// until the NEXT caption starts (this bridges inter-segment gaps); the last
// caption shows until its own endMs. Returns -1 when none is active.
//
// Single source of truth for the bridging rule — used by both the on-video
// CaptionOverlay and the Studio Subtitles tab's auto-scroll, which must agree.
export const activeCaptionIndex = (
  captions: CaptionSegment[],
  ms: number,
): number => {
  for (let i = 0; i < captions.length; i++) {
    const nextStart =
      i + 1 < captions.length ? captions[i + 1].startMs : captions[i].endMs;
    if (captions[i].startMs <= ms && ms < nextStart) return i;
  }
  return -1;
};
