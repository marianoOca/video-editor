// Base video configuration - reusable across projects
export const VIDEO_CONFIG = {
  // Core video properties
  FPS: 30,

  // Common dimensions - can be overridden per project
  WIDTH: 1080,
  HEIGHT: 1920, // Vertical video default (mobile-first)

  // Animation timing (in frames at default FPS)
  TIMING: {
    SHORT: 8,    // ~0.27s
    BASE: 15,    // ~0.5s
    LONG: 24,    // ~0.8s
    VERY_LONG: 36, // ~1.2s
  },

  // Spacing system (pixels)
  SPACING: {
    XS: 4,
    SM: 8,
    MD: 16,
    LG: 24,
    XL: 32,
    XXL: 48,
  },

  // Border radii
  RADII: {
    SM: 4,
    MD: 8,
    LG: 16,
    PILL: 9999,
  },
} as const;

// Helper to calculate duration in frames
export const framesFromSeconds = (seconds: number, fps: number = VIDEO_CONFIG.FPS): number => {
  return Math.round(seconds * fps);
};

// Helper to calculate duration in seconds from frames
export const secondsFromFrames = (frames: number, fps: number = VIDEO_CONFIG.FPS): number => {
  return frames / fps;
};