"""
Step 2: Transcribe combined.mp4 with Whisper.cpp (token-level timestamps).

Uses @remotion/install-whisper-cpp (via src/remotion/scripts/transcribe.mjs)
to run Whisper.cpp locally with DTW token-level timestamps — no audio leaves
the machine. Word timestamps come from the model's own attention path, which
avoids the forced-alignment failure mode where a word's end gets stretched
across trailing silence.

Long audio is transcribed in chunks. Whisper's long-form sequential decode
carries previous-text context across its native 30s windows; on long audio that
context loop self-reinforces into repeated phrases AND collapses DTW token
timestamps onto a single instant (hundreds of zero-width words at one time).
We split long audio at pauses into ~1-min chunks and transcribe each with a
FRESH whisper call — no context carries across a boundary, so the loop can't
form — then offset each chunk's timestamps back onto the full timeline. Short
audio (<= CHUNK_TRIGGER_SEC) stays single-pass.

Outputs word-level timestamps in data/transcript.json.

Usage: python3 2_transcribe.py [--model medium] [--lang es]
"""

import subprocess
import json
import os
import re
import sys
import argparse
from pathlib import Path
from config import (OUT_DIR, REMOTION_DIR, SRC_DIR, WHISPER_SAMPLE_RATE,
                    get_duration, run_ffmpeg, silencedetect)
from tuning import (
    SEGMENT_GAP_SEC, MAX_WORD_DUR, MAX_NUM_WORD_DUR, SUBTOKEN_TAIL,
    CONF_REANCHOR, CHAR_CONTIG_TOL,
    CHUNK_TRIGGER_SEC, CHUNK_TARGET_SEC, CHUNK_SEARCH_SEC, CHUNK_MIN_SEC,
    CHUNK_EDGE_PAD, CHUNK_SILENCE_DB, CHUNK_SILENCE_MIN,
)

WHISPER_DIR = SRC_DIR / "whisper.cpp"
TRANSCRIBE_SCRIPT = REMOTION_DIR / "scripts" / "transcribe.mjs"

SENTENCE_END_RE = re.compile(r"[.!?…]+$")


def extract_audio(video: Path, audio: Path):
    """Whisper.cpp requires 16kHz mono WAV."""
    print("  extracting audio...")
    run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(video), "-ar", str(WHISPER_SAMPLE_RATE),
         "-ac", "1", "-f", "wav", str(audio)]
    )


def _run_whisper(audio: Path, captions_path: Path, model: str, lang: str) -> list[dict]:
    """Run Whisper.cpp via the Node script on one audio file. Returns the caption
    list [{text, startMs, endMs, timestampMs, confidence}, ...]."""
    result = subprocess.run(
        [
            "node", str(TRANSCRIBE_SCRIPT),
            str(audio), str(captions_path), model, lang, str(WHISPER_DIR),
        ],
        cwd=REMOTION_DIR,
    )
    if result.returncode != 0:
        print("ERROR: whisper.cpp transcription failed (see output above).")
        sys.exit(1)
    with open(captions_path, encoding="utf-8") as f:
        return json.load(f)


def plan_chunks(duration: float,
                pauses: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Split [0, duration] into ~CHUNK_TARGET_SEC chunks bounded by SPEECH, not raw
    clock time. Pick a pause near each target as the split, then make each chunk
    span from the previous split pause's END (speech onset) to the next split
    pause's START (speech offset), plus a small CHUNK_EDGE_PAD clamped to half the
    pause (so adjacent chunks never overlap → no duplicated boundary words).

    Dropping the split-pause silence keeps words intact AND, crucially, stops a
    chunk from ending in a long trailing silence — a trailing near-empty window
    crashes whisper.cpp's experimental DTW timestamp pass
    (WHISPER_ASSERT filter_width < a->ne[2]). Leading/trailing global silence is
    treated as virtual boundary pauses so the first/last chunk is speech-bounded
    too (the last chunk's trailing silence is exactly what triggered the assert)."""
    # Choose boundary pauses near each ~target mark.
    boundaries: list[tuple[float, float]] = []
    cursor = 0.0
    while duration - cursor > CHUNK_TARGET_SEC + CHUNK_MIN_SEC:
        ideal = cursor + CHUNK_TARGET_SEC
        lo, hi = ideal - CHUNK_SEARCH_SEC, ideal + CHUNK_SEARCH_SEC
        cands = [p for p in pauses
                 if lo <= (p[0] + p[1]) / 2 <= hi
                 and p[0] > cursor + CHUNK_MIN_SEC]
        # Prefer the LONGEST pause in the window (most confident silence → cleaner
        # chunk edges, less whisper edge-token mangling), tie-broken by closeness
        # to the ideal target. Falls back to a hard split if no pause qualifies.
        p = (max(cands, key=lambda p: (p[1] - p[0], -abs((p[0] + p[1]) / 2 - ideal)))
             if cands else (ideal, ideal))  # degenerate pause = hard split
        boundaries.append(p)
        cursor = (p[0] + p[1]) / 2

    lead = next((p for p in pauses if p[0] <= 0.05), (0.0, 0.0))
    trail = next((p for p in reversed(pauses) if p[1] >= duration - 0.05),
                 (duration, duration))
    edges = [lead] + boundaries + [trail]

    chunks = []
    for a, b in zip(edges, edges[1:]):
        pad_s = min(CHUNK_EDGE_PAD, (a[1] - a[0]) / 2)
        pad_e = min(CHUNK_EDGE_PAD, (b[1] - b[0]) / 2)
        start = max(0.0, a[1] - pad_s)        # speech onset (- pad)
        end = min(duration, b[0] + pad_e)     # speech offset (+ pad)
        if end - start > 1.0:
            chunks.append((start, end))
    return chunks


def _offset_captions(captions: list[dict], offset_s: float) -> list[dict]:
    """Shift a chunk's caption timestamps onto the full-audio timeline."""
    off = offset_s * 1000.0
    for c in captions:
        for k in ("startMs", "endMs", "timestampMs"):
            if c.get(k) is not None:
                c[k] = c[k] + off
    return captions


def transcribe_audio(audio: Path, model: str, lang: str) -> list[dict]:
    """Transcribe `audio`, chunking long files to dodge whisper's long-form
    repetition/timestamp-collapse loop. Short audio goes single-pass. Long audio
    is split at pauses into ~1-min chunks, each transcribed by a FRESH whisper
    call (no previous-text context carries across a boundary → the loop can't
    form), then chunk timestamps are offset back onto the full timeline."""
    duration = get_duration(audio)
    if duration <= CHUNK_TRIGGER_SEC:
        print(f"  running whisper.cpp single-pass "
              f"(model={model}, lang={lang}, {duration:.0f}s)...")
        return _run_whisper(audio, OUT_DIR / "whisper_captions.json", model, lang)

    # Stricter threshold than the cut step so we only split in clear pauses.
    pauses = silencedetect(audio, CHUNK_SILENCE_DB, CHUNK_SILENCE_MIN,
                           total_duration=duration)
    chunks = plan_chunks(duration, pauses)
    print(f"  long audio ({duration:.0f}s) → {len(chunks)} chunk(s) "
          f"(~{CHUNK_TARGET_SEC:.0f}s, split at pauses; context reset each chunk)")

    # Per-chunk cache → step 2 is resumable: if whisper aborts on a later chunk
    # (e.g. the DTW assert), completed chunks are reloaded on re-run instead of
    # re-transcribed. Cache is keyed by audio size + model + lang + chunk bounds,
    # so it invalidates when the input, model, or chunk plan changes.
    cache_dir = OUT_DIR / "_chunks"
    cache_dir.mkdir(exist_ok=True)
    audio_size = audio.stat().st_size
    chunk_wav = OUT_DIR / "_chunk.wav"
    chunk_json = OUT_DIR / "_chunk_captions.json"
    all_captions: list[dict] = []
    for i, (start, end) in enumerate(chunks):
        cache = cache_dir / f"chunk_{i:03d}.json"
        if cache.exists():
            # Guarded parse: a run interrupted mid-write leaves a truncated json;
            # crashing on it would break the resumability the cache exists for.
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            if (data.get("audio_size") == audio_size and data.get("model") == model
                    and data.get("lang") == lang
                    and abs(data.get("start", -1) - start) < 0.01
                    and abs(data.get("end", -1) - end) < 0.01):
                print(f"  chunk {i + 1}/{len(chunks)}: {start:.1f}s–{end:.1f}s "
                      f"(cached, {len(data['captions'])} tokens)")
                all_captions.extend(data["captions"])
                continue
        print(f"  chunk {i + 1}/{len(chunks)}: {start:.1f}s–{end:.1f}s "
              f"({end - start:.1f}s)")
        run_ffmpeg(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
             "-i", str(audio), "-ar", str(WHISPER_SAMPLE_RATE), "-ac", "1",
             "-f", "wav", str(chunk_wav)]
        )
        captions = _offset_captions(_run_whisper(chunk_wav, chunk_json, model, lang),
                                    start)
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"audio_size": audio_size, "model": model,
                                   "lang": lang, "start": start, "end": end,
                                   "captions": captions}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, cache)  # atomic: never leave a half-written cache entry
        all_captions.extend(captions)

    # Persist the merged token stream for debugging, mirroring single-pass output.
    with open(OUT_DIR / "whisper_captions.json", "w", encoding="utf-8") as f:
        json.dump(all_captions, f, indent=2, ensure_ascii=False)
    return all_captions


def merge_tokens_to_words(captions: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Whisper.cpp emits sub-word tokens ('mic', 'ró', 'f', 'ono') plus detached
    punctuation. A token starting with a space begins a new word; anything else
    (continuation or punctuation) appends to the current word.

    Timestamps: each token carries two times — 'startMs'/'endMs' (whisper's
    char offsets, which TILE so every end equals the next token's start and
    therefore stretch across pauses) and 'timestampMs' (the token's DTW
    alignment to the audio, accurate even across gaps). We take the word START
    from the DTW timestamp of its first token, and derive the word END from the
    next word's start, capped at MAX_WORD_DUR. Starts are forced monotonic so
    captions never go backwards on a DTW jitter.

    Bracketed runs ('[Toc, toc, toc]', '[música]', '(risas)') are whisper's
    label for NON-SPEECH audio (knocks, music, applause). Depth tracking drops
    the whole run from the words, and its offset time range is returned
    separately as a non-speech interval — a strong cut signal for loud noise
    that is acoustically fused with adjacent speech (energy can't split it).

    Returns (words, nonspeech) where nonspeech is a list of {start, end}.
    """
    words = []
    nonspeech = []
    dropped = 0
    depth = 0
    run_start = None
    run_end = None
    prev_char_end = None  # char-offset end of the previous emitted token (for contiguity)

    def close_run():
        nonlocal run_start, run_end
        if run_start is not None and run_end is not None and run_end > run_start:
            nonspeech.append({"start": run_start, "end": run_end})
        run_start = run_end = None

    for c in captions:
        raw = c.get("text") or ""
        opens = raw.count("[") + raw.count("(")
        closes = raw.count("]") + raw.count(")")
        if depth > 0 or opens > 0:
            # Inside a bracketed non-speech run: accumulate its offset bounds
            s, e = c.get("startMs"), c.get("endMs")
            if s is not None and run_start is None:
                run_start = s / 1000.0
            if e is not None:
                run_end = e / 1000.0
            depth = max(0, depth + opens - closes)
            if depth == 0:
                close_run()
            continue

        start_ms = c.get("timestampMs")
        if start_ms is None:
            start_ms = c.get("startMs")  # fallback when DTW is unavailable
        if not raw.strip() or start_ms is None or raw.strip() == ".":
            # A lone "." is detached sentence punctuation — noise. Multi-dot runs
            # ("...", "…") are different: whisper emits them for REAL speech it
            # could not decode (e.g. "Claude" -> 'cl' + '...'), so they flow
            # through as "…" tokens — their DTW extends the word's coverage and
            # the "…" stays visible (and editable) in the Subtitles tab instead
            # of surfacing as an untranscribed audio island.
            dropped += 1
            continue
        if re.fullmatch(r"[.…]+", raw.strip()):
            raw = (" " if raw.startswith(" ") else "") + "…"

        # Two per-token clocks: dtw (DTW/attention onset, accurate across pauses)
        # and chs/che (char-offset start/end, which TILE contiguously within a
        # chunk). Word START is the DTW onset — EXCEPT when whisper flags the token
        # low-confidence AND its DTW sits more than a word-length BEFORE its own
        # char-offset: that is the hallucination-into-a-pause signature (e.g. a
        # 0.48-conf token thrown 0.85s early into silence). Then trust the
        # char-offset. SIGNED (chs - dtw), not abs: a token whose DTW is LATER than
        # its char-offset is correctly placed after a real pause (first word after a
        # gap) and must NOT be moved.
        dtw = start_ms / 1000.0
        chs = (c["startMs"] if c.get("startMs") is not None else start_ms) / 1000.0
        che = (c["endMs"] if c.get("endMs") is not None
               else c.get("startMs", start_ms)) / 1000.0
        cf = c.get("confidence", 1.0)
        ts = chs if (cf < CONF_REANCHOR and (chs - dtw) > MAX_WORD_DUR) else dtw

        # A no-leading-space token continues the current word (sub-word split:
        # 'mic'+'ró'+'f'+'ono'). The FIRST token of a fresh chunk also lacks a
        # leading space, so it would glue onto the previous chunk's last word —
        # discarding its own DTW (this dropped "ciento veinte"→"120" as "Unos120").
        # The old guard split on a far DTW gap, but that also split a single word
        # whose first token's DTW was hallucinated early ('implic'+'ar'→'implicar').
        # Distinguish a real continuation from a chunk-seam glue by char-offset
        # CONTIGUITY: char-offsets tile exactly within a chunk but jump across a
        # chunk boundary. So only let a far gap force a new word when the token is
        # NOT char-contiguous with the previous one.
        char_contig = (prev_char_end is not None
                       and abs(chs - prev_char_end) < CHAR_CONTIG_TOL)
        far = bool(words) and ts - words[-1]["start"] > MAX_WORD_DUR
        starts_word = raw.startswith(" ") or not words or (far and not char_contig)
        if starts_word:
            words.append({"word": raw.strip(), "start": ts, "_last": ts})
        else:
            text = raw.strip()
            if not (text == "…" and words[-1]["word"].endswith("…")):
                words[-1]["word"] += text
            words[-1]["_last"] = max(words[-1]["_last"], ts)
        prev_char_end = che
    close_run()  # in case a run never closed its bracket

    if dropped:
        print(f"  WARNING: dropped {dropped} caption token(s) without text/timestamps")
    if nonspeech:
        print(f"  marked {len(nonspeech)} non-speech region(s) from bracketed tokens")

    # Force monotonic starts, then derive each end from the next start (capped).
    # The cap ceiling stretches to the word's last sub-token DTW (+SUBTOKEN_TAIL):
    # that onset proves audio was still running there, so the word may exceed
    # MAX_WORD_DUR — but never past the next word's start.
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["start"]:
            words[i]["start"] = words[i - 1]["start"]
    for i, w in enumerate(words):
        next_start = words[i + 1]["start"] if i + 1 < len(words) else float("inf")
        cap = MAX_NUM_WORD_DUR if re.match(r"\s*\d[\d.,]*", w["word"]) else MAX_WORD_DUR
        # "evidence" (kept in transcript.json) = last sub-token DTW onset: the
        # latest instant the word's audio is PROVEN to still be running. Step 3
        # snaps a keep block's end to the first silence after it — never before,
        # which would cut the extension right back off.
        w["evidence"] = w.pop("_last")
        ceiling = max(w["start"] + cap, w["evidence"] + SUBTOKEN_TAIL)
        w["end"] = min(max(next_start, w["start"]), ceiling)
    return words, nonspeech


def words_to_segments(words: list[dict]) -> list[dict]:
    """
    Group merged words into the transcript.json segment schema:
    [{start, end, text, words: [{word, start, end}]}].

    A segment closes on sentence-ending punctuation or a gap > SEGMENT_GAP_SEC
    to the next word.
    """
    segments = []
    current: list[dict] = []

    def flush():
        if current:
            segments.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(w["word"] for w in current),
                "words": list(current),
            })

    for i, w in enumerate(words):
        current.append(w)
        next_gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 0.0
        if SENTENCE_END_RE.search(w["word"]) or next_gap > SEGMENT_GAP_SEC:
            flush()
            current = []
    flush()

    return segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="large-v3",
                        help="Whisper model: tiny/base/small/medium/large-v2/large-v3")
    parser.add_argument("--lang", default="es", help="Language code (es, en, ...)")
    args = parser.parse_args()

    combined = OUT_DIR / "combined.mp4"
    if not combined.exists():
        print("ERROR: data/combined.mp4 not found. Run 1_normalize.py first.")
        sys.exit(1)

    audio = OUT_DIR / "audio.wav"
    extract_audio(combined, audio)

    captions = transcribe_audio(audio, args.model, args.lang)
    words, nonspeech = merge_tokens_to_words(captions)
    segments = words_to_segments(words)

    transcript = {
        "model": args.model,
        "language": args.lang,
        "segments": segments,
        "nonspeech": nonspeech,
        "full_text": " ".join(s["text"] for s in segments),
    }

    out_path = OUT_DIR / "transcript.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done.")
    print(f"   transcript.json → {out_path}")
    print(f"   Segments: {len(segments)}")
    print(f"\n--- Preview (first 3 segments) ---")
    for s in segments[:3]:
        print(f"  [{s['start']:.1f}s – {s['end']:.1f}s] {s['text']}")


if __name__ == "__main__":
    main()
