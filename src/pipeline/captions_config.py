"""
Tunables for caption grouping and display.
Edit here to retune without touching pipeline logic.
"""

MAX_WORDS_PER_CAPTION = 3    # max words shown on screen at once
MAX_CHARS_PER_CAPTION = 20   # max total chars incl. spaces; overrides word count if exceeded
MIN_CAPTION_DURATION_MS = 300  # extend captions shorter than this (avoids flash subs)
