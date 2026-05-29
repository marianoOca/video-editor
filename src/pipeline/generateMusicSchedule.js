const fs = require('fs');
const path = require('path');

// Paths
const repoRoot = path.resolve(__dirname, '../..');
const configPath = path.join(repoRoot, 'src/remotion/src/caro/config.ts');
const manifestPath = path.join(repoRoot, 'src/remotion/src/caro/manifest.ts');

// Read config for FPS and PHOTO_DURATION_SEC
const configContent = fs.readFileSync(configPath, 'utf8');
const fpsMatch = configContent.match(/export const FPS = (\d+);/);
const photoDurationSecMatch = configContent.match(/export const PHOTO_DURATION_SEC = (\d+\.?\d*);/);
if (!fpsMatch || !photoDurationSecMatch) {
  throw new Error('Could not parse fps or photo duration from config');
}
const FPS = parseInt(fpsMatch[1], 10);
const PHOTO_DURATION_SEC = parseFloat(photoDurationSecMatch[1]);
const PHOTO_DURATION_FRAMES = Math.round(PHOTO_DURATION_SEC * FPS);

// Read manifest to get MEDIA array
const manifestContent = fs.readFileSync(manifestPath, 'utf8');
const mediaMatch = manifestContent.match(/export const MEDIA: MediaItem\[\] = (\[.*?\]);/s);
if (!mediaMatch) {
  throw new Error('Could not parse MEDIA array from manifest');
}
const mediaJson = mediaMatch[1];
const media = JSON.parse(mediaJson);

// Group by year and sum durations
const yearDurations = {};
for (const item of media) {
  const year = item.year;
  const duration = item.type === 'image' ? PHOTO_DURATION_FRAMES : item.durationFrames;
  yearDurations[year] = (yearDurations[year] || 0) + duration;
}
const years = Object.keys(yearDurations).map(Number).sort((a,b) => a-b);

// Compute cumulative frames for each year range
let cumulative = 0;
const yearRanges = [];
for (const year of years) {
  const duration = yearDurations[year];
  yearRanges.push({
    year,
    start: cumulative,
    duration,
    end: cumulative + duration
  });
  cumulative += duration;
}
const totalPhotoFrames = cumulative;

// Compute other durations from CaroVideo.tsx
const caroVideoPath = path.join(repoRoot, 'src/remotion/src/caro/CaroVideo.tsx');
const caroContent = fs.readFileSync(caroVideoPath, 'utf8');

// Extract BEGINNING_STANZAS array
const beginningStanzasMatch = caroContent.match(/const BEGINNING_STANZAS: Stanza\[\]\s*=\s*\[([\s\S]*?)\];/);
if (!beginningStanzasMatch) throw new Error('Could not find BEGINNING_STANZAS');
// The stanzas are represented as arrays of arrays of strings - use JSON.parse after fixing brackets
const beginningStanzasStr = beginningStanzasMatch[1];
try {
  const beginningStanzas = JSON.parse('[' + beginningStanzasStr + ']');
  // Compute BEGINNING_DURATION using same logic as TextScene.stanzaSceneDurationFrames
  // constants from config.ts
  const constants = {
    LINE_FADE_FRAMES: parseInt(configContent.match(/export const LINE_FADE_FRAMES = (\d+);/)[1], 10),
    LINE_DELAY_FRAMES: parseInt(configContent.match(/export const LINE_DELAY_FRAMES = (\d+);/)[1], 10),
    TEXT_STANZA_HOLD_FRAMES: parseInt(configContent.match(/export const TEXT_STANZA_HOLD_FRAMES = (\d+);/)[1], 10),
    STANZA_EXIT_FRAMES: parseInt(configContent.match(/export const STANZA_EXIT_FRAMES = (\d+);/)[1], 10),
    INTER_STANZA_GAP_FRAMES: parseInt(configContent.match(/export const INTER_STANZA_GAP_FRAMES = (\d+);/)[1], 10),
    SCENE_OUTRO_FADE_FRAMES: parseInt(configContent.match(/export const SCENE_OUTRO_FADE_FRAMES = (\d+);/)[1], 10),
    FADE_TO_BLACK_FRAMES: parseInt(configContent.match(/export const FADE_TO_BLACK_FRAMES = (\d+);/)[1], 10),
  };
  // layout calculation
  let cursor = 0;
  for (const stanza of beginningStanzas) {
    const enter = (stanza.length - 1) * constants.LINE_DELAY_FRAMES + constants.LINE_FADE_FRAMES;
    const start = cursor;
    const enterEnd = start + enter;
    const holdEnd = enterEnd + constants.TEXT_STANZA_HOLD_FRAMES;
    const exitEnd = holdEnd + constants.STANZA_EXIT_FRAMES; // accumulate mode
    cursor = enterEnd + constants.INTER_STANZA_GAP_FRAMES;
  }
  const layout = []; cursor = 0;
  for (const stanza of beginningStanzas) {
    const enter = (stanza.length - 1) * constants.LINE_DELAY_FRAMES + constants.LINE_FADE_FRAMES;
    const start = cursor;
    const enterEnd = start + enter;
    const holdEnd = enterEnd + constants.TEXT_STANZA_HOLD_FRAMES;
    const exitEnd = holdEnd; // accumulate exit is holdEnd
    layout.push({ start, enter, enterEnd, holdEnd, exitEnd });
    cursor = enterEnd + constants.INTER_STANZA_GAP_FRAMES;
  }
  const last = layout[layout.length - 1];
  const beginningDuration = (last?.exitEnd ?? 0) + Math.max(constants.SCENE_OUTRO_FADE_FRAMES, constants.FADE_TO_BLACK_FRAMES);
  const introDurationFrames = parseInt(configContent.match(/export const INTRO_DURATION_FRAMES = (\d+);/)[1], 10);
  const beginningStartFrame = introDurationFrames;
  const beginningEndFrame = beginningStartFrame + beginningDuration;
  // Extract END_STANZAS similarly
  const endingStanzasMatch = caroContent.match(/const END_STANZAS: Stanza\[\]\s*=\s*\[([\s\S]*?)\];/);
  if (!endingStanzasMatch) throw new Error('Could not find END_STANZAS');
  const endingStanzasStr = endingStanzasMatch[1];
  const endingStanzas = JSON.parse('[' + endingStanzasStr + ']');
  // Compute endingDuration
  let endingCursor = 0;
  for (const stanza of endingStanzas) {
    const enter = (stanza.length - 1) * constants.LINE_DELAY_FRAMES + constants.LINE_FADE_FRAMES;
    const start = endingCursor;
    const enterEnd = start + enter;
    const holdEnd = enterEnd + constants.TEXT_STANZA_HOLD_FRAMES;
    const exitEnd = holdEnd + constants.STANZA_EXIT_FRAMES; // replace mode
    endingCursor = exitEnd;
  }
  const endingLayout = []; endingCursor = 0;
  for (const stanza of endingStanzas) {
    const enter = (stanza.length - 1) * constants.LINE_DELAY_FRAMES + constants.LINE_FADE_FRAMES;
    const start = endingCursor;
    const enterEnd = start + enter;
    const holdEnd = enterEnd + constants.TEXT_STANZA_HOLD_FRAMES;
    const exitEnd = holdEnd + constants.STANZA_EXIT_FRAMES;
    endingLayout.push({ start, enter, enterEnd, holdEnd, exitEnd });
    endingCursor = exitEnd;
  }
  const endingLast = endingLayout[endingLayout.length - 1];
  const endingDuration = (endingLast?.exitEnd ?? 0) + Math.max(constants.SCENE_OUTRO_FADE_FRAMES, constants.FADE_TO_BLACK_FRAMES);
  const endingStartFrame = beginningEndFrame + cumulative; // after photo sequence
  const endingEndFrame = endingStartFrame + endingDuration;

  // Now compute group durations
  const groupDurations = {
    '2013-2019': 0,
    '2020-2023': 0,
    '2024-2026': 0
  };
  for (const r of yearRanges) {
    if (r.year >= 2013 && r.year <= 2019) groupDurations['2013-2019'] += r.duration;
    if (r.year >= 2020 && r.year <= 2023) groupDurations['2020-2023'] += r.duration;
    if (r.year >= 2024 && r.year <= 2026) groupDurations['2024-2026'] += r.duration;
  }
  // Find start frames for each group
  let groupStart = {};
  let currentPos = beginningEndFrame; // start of photo sequence
  for (const r of yearRanges) {
    if (!groupStart['2013-2019'] && r.year >= 2013 && r.year <= 2019) {
      groupStart['2013-2019'] = currentPos;
    }
    if (!groupStart['2020-2023'] && r.year >= 2020 && r.year <= 2023) {
      groupStart['2020-2023'] = currentPos;
    }
    if (!groupStart['2024-2026'] && r.year >= 2024 && r.year <= 2026) {
      groupStart['2024-2026'] = currentPos;
    }
    currentPos += r.duration;
  }
  const track1Start = 0;
  const track2Start = beginningStartFrame + yearRanges[0].start;
  const track3Start = beginningStartFrame + yearRanges[1].start;
  const track4Start = groupStart['2013-2019'];
  const track5Start = groupStart['2020-2023'];
  const track6Start = groupStart['2024-2026'];

  // But to simplify: track1 resumes at endingStartFrame (after photo sequence)
  let track1Resume = endingStartFrame;

  const totalFrames = endingEndFrame;
  const results = {
    fps: FPS,
    introDurationFrames,
    beginningDurationFrames: beginningDuration,
    endingDurationFrames: endingDuration,
    totalFrames,
    trackAssignments: {
      track1: {
        start: track1Start,
        resume: track1Resume
      },
      track2: {
        start: track2Start
      },
      track3: {
        start: track3Start
      },
      track4: {
        start: track4Start
      },
      track5: {
        start: track5Start
      },
      track6: {
        start: track6Start
      }
    }
  };
  console.log(JSON.stringify(results, null, 2));
} catch (e) {
  console.error('Error parsing stanzas:', e);
  process.exit(1);
}