"""
Every editing-feel / heuristic knob of the pipeline, in one place, grouped by
the step that consumes it. Edit here to retune without touching pipeline logic.

These are TASTE and EMPIRICAL values — there is no ground truth to compute them
from, and keeping them as fixed constants keeps cut behavior deterministic
(timestamp bug reports only make sense when the machinery doesn't vary per
video). Where real math exists it is derived in the code instead (e.g. the
gap-cut threshold = MAX_KEEP_GAP + 2*KEEP_PAD).

Format/protocol constants (sample rates, ports, resolutions, ffmpeg presets)
live in config.py — they are requirements, not taste.
"""

# ============================================================================
# Step 1 — normalize / noise-floor measurement (1_normalize.py)
# ============================================================================

# Adaptive silence threshold: instead of trusting one absolute dB for every
# recording, step 1 measures the video's own noise floor (a low percentile of
# windowed voice-band RMS) and writes silence_db = floor + margin (clamped) into
# mode.json for step 3. Different mics/rooms shift the floor; the fixed -35dB
# clipped soft word tails on quiet-floor recordings (measured evidence in
# src/HANDOFF_adaptive_silence_floor.md). Step 3 falls back to SILENCE_DB when
# mode.json predates this field.
FLOOR_WINDOW_SEC = 0.5   # seconds — RMS measurement window
FLOOR_PERCENTILE = 10    # low percentile of window RMS = the noise floor
                         # (most windows contain speech; the low tail is room tone)
FLOOR_MARGIN_DB = 10.0   # threshold sits this far above the measured floor
FLOOR_DB_MIN = -38.0     # clamp: threshold never stricter than this. Validated
                         # July 2026 on two quiet-floor projects: -38 fixed the
                         # clipped soft tails at +~1% duration; -45 tripled the
                         # duration cost and flipped more knife-edge snap
                         # decisions for no extra tail coverage.
FLOOR_DB_MAX = -30.0     # ...and never looser — protects against a bad measurement

# ============================================================================
# Step 2 — transcription (2_transcribe.py)
# ============================================================================

# Gap between words that forces a new segment even without sentence punctuation
SEGMENT_GAP_SEC = 1.0

# Max word duration (seconds). Whisper.cpp gives accurate word STARTS (DTW) but
# no reliable word ends, so each end is derived from the next word's start and
# capped here — otherwise a word before a pause stretches across the whole gap.
MAX_WORD_DUR = 0.7

# Spoken multi-word numbers ("ciento veinte", "dos mil veinticuatro") collapse into a
# single digit token ("120", "2024"); the 0.7s cap then truncates the word to its first
# syllables and the rest is cut as a phantom no-speech gap (see merge_tokens_to_words
# note re "ciento veinte"->"120"). Numeric tokens get a wider cap so the keep block covers
# the whole spoken number. 3.0s covers any number a person says aloud in one breath.
MAX_NUM_WORD_DUR = 3.0

# A continuation token's DTW onset proves the word's audio is still going at that
# instant, so the word must cover at least that far plus this much tail — even past
# the MAX_WORD_DUR cap. Without it, a restarted/stretched word ("U...vitas") keeps
# only its first token's 0.7s and the rest is cut as phantom no-speech.
SUBTOKEN_TAIL = 0.25

# Word-timestamp repair (transcript-internal, no energy). Whisper's DTW onset can
# hallucinate a LOW-confidence token's start backward into a pause; its char-offset
# (which tiles within contiguous speech) is then the better onset. See
# merge_tokens_to_words for the signed-direction re-anchor + char-contiguity merge.
CONF_REANCHOR = 0.6     # only re-anchor tokens whisper itself flagged unsure
CHAR_CONTIG_TOL = 0.06  # seconds — char-offsets tile (==) for true sub-word splits

# --- Chunked transcription (long-audio hallucination guard) -----------------
# Long files trip whisper's long-form context loop (repeated phrases + DTW
# timestamp collapse). We split at pauses into ~1-min chunks, each a fresh
# whisper call, so no previous-text context carries across a boundary.
CHUNK_TRIGGER_SEC = 90.0    # only chunk when audio is longer than this
CHUNK_TARGET_SEC = 60.0     # aim for ~1-min chunks
CHUNK_SEARCH_SEC = 20.0     # search this far around the target for a pause to split on
CHUNK_MIN_SEC = 20.0        # never emit a chunk shorter than this (absorb the tail)
CHUNK_EDGE_PAD = 0.25       # seconds — keep this much silence around each chunk's
                            # speech; a longer trailing silence crashes whisper.cpp's
                            # DTW pass (WHISPER_ASSERT filter_width < a->ne[2])
CHUNK_SILENCE_DB = -30      # dB — split only in confident pauses (quieter than this;
                            # deliberately stricter than step 3's SILENCE_DB)
CHUNK_SILENCE_MIN = 0.5     # seconds — min pause length to be a split candidate

# ============================================================================
# Step 3 — cut analysis (3_analyze.py)
# ============================================================================

# for YouTube MAX_KEEP_GAP = 0.3 & KEEP_PAD = 0.3,  and we use microphone, better error tolerance for youtube videos with no mic or longer silences and more tone variance
# for reels MAX_KEEP_GAP = 0.2 & KEEP_PAD = 0.1, this should make the videos quicker, sanppier, but more prone to errors but handable if video short
MAX_KEEP_GAP = 0.3    # seconds — no-talk spans longer than this are cut, consider final removed gaps will be > MAX_KEEP_GAP + 2 * KEEP_PAD
KEEP_PAD = 0.3        # seconds — headroom when no silence boundary to snap to
MIN_SEGMENT = 0.2     # seconds — keep fragments shorter than this are dropped
NONSPEECH_PAD = 0.05  # seconds — outward pad on whisper-labeled non-speech cuts

# A word counts as stranded-in-silence (whisper hallucination, dropped) only when
# its start sits at least this deep inside the silence. A start a few ms past the
# silence edge is a DTW/energy float tie on a word whose real audio is right at
# the boundary — dropping it loses a caption for audio that IS kept.
SILENT_WORD_MARGIN = 0.1

# Energy safety net: an uncovered speech burst (per silencedetect) at least this
# long, within this gap of a keep edge, is rescued by extending that keep. Catches
# real speech whisper mistimed (late DTW onset, capped word end) that word
# coverage would cut by construction. Bursts far from any keep stay cut — noise
# in a pause has wide silence on both sides, so it never qualifies. A LONG burst
# (>= ENERGY_NET_LONG_BURST) is almost certainly speech, not a breath or a tap,
# so it earns the wider ENERGY_NET_LONG_GAP. ENERGY_NET_MAX_BURST bounds a rescue
# to the sub-second/one-second mistiming this net exists for: anything longer is
# not a mistimed word but sustained un-transcribed audio (music, typing, a second
# voice fused to the speech edge) and must stay cut.
ENERGY_NET_MIN_BURST = 0.2
ENERGY_NET_MAX_GAP = 0.3
ENERGY_NET_LONG_BURST = 0.5
ENERGY_NET_LONG_GAP = 0.5
ENERGY_NET_MAX_BURST = 1.5

# The energy net rescues MISTIMED SPEECH, but steady background noise (hum, room
# tone, a fan — sitting above the silence floor) also survives silencedetect and
# can abut a keep edge, so it is indistinguishable from a late word onset on
# timing OR loudness alone (a measured noise burst landed dead-centre in the
# legit-rescue dB range). The separator is temporal shape: speech has phoneme
# structure (peaks and valleys), steady noise is flat. A rescue-candidate burst
# whose voice-band RMS varies LESS than this (std across FLAT_WINDOW_SEC windows)
# is treated as noise, not a word, and left cut. 4.0 dB sits below every measured
# legit rescue (std >= 4.6) and above the flagged noise (std ~2.9) with margin.
ENERGY_NET_FLAT_STD_DB = 4.0
# Only flatness-test bursts at least this long: a shorter burst adds negligible
# audio, and a quiet word onset can itself look flat, so testing it risks a false
# cut. The noise this exists to catch is always the sustained (~1s) kind.
ENERGY_NET_FLAT_MIN_DUR = 0.5
# RMS-window length for the flatness measurement — the std is only meaningful
# against a fixed window (mirrors FLOOR_WINDOW_SEC's role in step 1).
FLAT_WINDOW_SEC = 0.1

# Edge-snapping: word DTW starts can land late (clipping a word's onset) and
# capped word ends overshoot into trailing noise. We snap each keep block's
# edges to the real silence→speech / speech→silence boundaries instead.
SILENCE_DB = -35      # dB — energy below this counts as silence
SILENCE_MIN = 0.15    # seconds — min silence to register (fine, to sit between taps)
SNAP_LEAD = 0.6       # seconds — how far before a word's start a silence edge may be
                      # and still count as that word's onset (covers DTW lateness)
SNAP_SLOP = 0.15      # seconds — silence edge may sit slightly past the word start too
SNAP_MIN = 0.05       # seconds — a closing silence must begin at least this far in

REPETITION_CHUNK_SEC = 180     # seconds of speech per Claude request when transcript is long
REPETITION_CHUNK_OVERLAP = 10  # seconds of overlap between consecutive chunks

# ============================================================================
# Step 4 — captions (4_render.py)
# ============================================================================

MAX_WORDS_PER_CAPTION = 3    # max words shown on screen at once
MAX_CHARS_PER_CAPTION = 20   # max total chars incl. spaces; overrides word count if exceeded
MIN_CAPTION_DURATION_MS = 300  # extend captions shorter than this (avoids flash subs)

# A word belongs to the keep segment it overlaps most, as long as the overlap is
# at least min(MIN_WORD_OVERLAP, half the word). A start-inside-segment test
# instead drops a word whose start sits a few ms before its keep (word timing
# and snapped keep edges disagree slightly) — its audio plays but no caption
# shows, an "untranscribed island".
MIN_WORD_OVERLAP = 0.1  # seconds
