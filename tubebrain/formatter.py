"""
Clean YouTube captions for humans and LLMs.

TubeBrain transforms noisy YouTube auto-captions into clean, structured text:
  - Merged paragraphs  (overlapping segments → one clean block)
  - Timestamped segments (each paragraph with MM:SS timestamp)
  - SRT subtitles       (standard subtitle format for any player)
  - JSON               (structured metadata + segments + merged text)

No skill generation — just clean text.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Evidence, EvidenceKind, Run
from .store import EvidenceStore


# ─── Timestamp helpers ────────────────────────────────────────────────────────

def _fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_srt_ts(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _yt_link(url: str, seconds: float) -> str:
    """Build a YouTube link with timestamp."""
    vid = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
    if vid:
        return f"https://youtu.be/{vid.group(1)}?t={int(seconds)}"
    return url


# ─── Caption de-duplication ──────────────────────────────────────────────────

def _is_repetitive_append(before: str, after: str) -> bool:
    """Skip 'after' if >70% of its words are already in 'before'."""
    a_words = set(after.lower().split())
    if not a_words:
        return True
    b_words = set(before.lower().split())
    return len(a_words & b_words) / len(a_words) > 0.7


def _dedupe_self_repeats(text: str) -> str:
    """
    Collapse 2x or 3x repetition within a single caption segment.

    YouTube auto-captions sometimes emit the same phrase 2-3x in one VTT entry.
    e.g. "here's how you do it here's how you do it" → "here's how you do it"
    """
    if len(text) < 30:
        return text
    words = text.split()
    n = len(words)
    for divisor in (2, 3):
        if n % divisor == 0:
            chunk_size = n // divisor
            first = " ".join(words[:chunk_size]).lower()
            if all(
                " ".join(words[i * chunk_size:(i + 1) * chunk_size]).lower() == first
                for i in range(1, divisor)
            ):
                return " ".join(words[:chunk_size])
    if n >= 4 and n % 2 == 0:
        half = n // 2
        if " ".join(words[:half]).lower() == " ".join(words[half:]).lower():
            return " ".join(words[:half])
    return text


def _clean_caption(text: str) -> str:
    """
    Clean a single VTT caption segment:
      - Collapse adjacent duplicate words ("the the cat" → "the cat")
      - Collapse intra-segment repetition ("X X" → "X")
      - Strip extra whitespace
    """
    text = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return _dedupe_self_repeats(text).strip()


def _strip_leading_repeat(current: str, prev: str) -> str:
    """
    Strip the leading repeated phrase when a new caption starts where the last one ended.

    Handles two cases:
      Case 1: prev is a prefix of current ("you can do X" → "you can do X + new part")
      Case 2: last few words of prev overlap with start of current (word-level overlap)
    """
    if not current or not prev:
        return current
    prev_words = prev.lower().split()
    curr_words = current.lower().split()
    if not prev_words or not curr_words:
        return current

    # Case 1: entire prev is a prefix of current
    if current.lower().startswith(prev.lower().strip()):
        remainder = current[len(prev):].strip()
        return remainder if remainder else current

    # Case 2: last N words of prev == first N words of current (N >= 3)
    reuse = 0
    max_check = min(15, len(prev_words), len(curr_words))
    for i in range(max_check, 0, -1):
        head = " ".join(curr_words[:i])
        tail = " ".join(prev_words[-i:]) if len(prev_words) >= i else ""
        if head == tail and i >= 3:
            reuse = i
            break
    if reuse:
        return " ".join(current.split()[reuse:])
    return current


def _merge_captions(caps: list[Evidence]) -> list[tuple[float, str]]:
    """
    Merge noisy YouTube auto-captions into clean (timestamp, text) pairs.

    Algorithm:
      1. Sort by timestamp
      2. Within 3s gap → same paragraph, strip leading overlap
      3. Outside 3s gap → new paragraph
      4. Aggressively deduplicate each segment
    """
    if not caps:
        return []
    sorted_caps = sorted(caps, key=lambda e: e.timestamp)
    merged = []
    current_ts = sorted_caps[0].timestamp
    current_parts = [_clean_caption(sorted_caps[0].data.get("text", "").strip())]

    for cap in sorted_caps[1:]:
        raw = cap.data.get("text", "").strip()
        if not raw:
            continue
        text = _clean_caption(raw)
        if not text:
            continue
        gap = cap.timestamp - current_ts
        if gap <= 3.0:
            # Same paragraph — strip leading overlap against accumulated text
            accumulated = " ".join(current_parts)
            text = _strip_leading_repeat(text, accumulated)
            last = current_parts[-1] if current_parts else ""
            if text and text != last and not _is_repetitive_append(last, text):
                current_parts.append(text)
            current_ts = cap.timestamp
        else:
            merged.append((current_ts, " ".join(current_parts)))
            current_ts = cap.timestamp
            current_parts = [text]

    if current_parts:
        merged.append((current_ts, " ".join(current_parts)))
    return merged


# ─── SRT subtitle generator ────────────────────────────────────────────────

def _merge_captions_for_srt(caps: list[Evidence]) -> list[tuple[float, float, str]]:
    """
    Like _merge_captions, but also returns end timestamps (needed for SRT).
    We use the START of the NEXT segment as the end time, or +5s fallback.
    """
    merged = _merge_captions(caps)
    segments = []
    for i, (ts, text) in enumerate(merged):
        next_ts = merged[i + 1][0] if i + 1 < len(merged) else ts + 5.0
        segments.append((ts, next_ts, text))
    return segments


# ─── Export functions ───────────────────────────────────────────────────────

def _get_run_captions(run: Run, store: EvidenceStore) -> list[Evidence]:
    from .models import SearchQuery
    return store.search_evidence(
        SearchQuery(query="", run_id=run.id, kinds=[EvidenceKind.TRANSCRIPT], limit=100000)
    ).evidence


def _get_run_ocr(run: Run, store: EvidenceStore) -> list[Evidence]:
    from .models import SearchQuery
    return store.search_evidence(
        SearchQuery(query="", run_id=run.id, kinds=[EvidenceKind.OCR], limit=100000)
    ).evidence


def export_paragraph(run: Run, store: EvidenceStore) -> str:
    """
    One clean paragraph — all captions merged into a single readable block,
    followed by on-screen text (OCR) snippets.
    Best for: feeding directly to an LLM, summarization, RAG ingestion.
    """
    caps = _get_run_captions(run, store)
    merged = _merge_captions(caps)
    ocrs = _get_run_ocr(run, store)

    parts = ["## Transcript\n", " ".join(text for _, text in merged)]

    if ocrs:
        # Deduplicate OCR by text content
        seen = set()
        ocr_lines = []
        for o in sorted(ocrs, key=lambda e: e.timestamp):
            text = o.data.get("text", "").strip()
            if text and text not in seen and len(text) >= 3:
                seen.add(text)
                ocr_lines.append(f"[{_fmt_ts(o.timestamp)}] {text}")
        if ocr_lines:
            parts.append("\n\n## On-Screen Text\n")
            parts.append("\n".join(ocr_lines))

    return "".join(parts)


def export_segments(run: Run, store: EvidenceStore) -> str:
    """
    Timestamped segments — each clean paragraph with its MM:SS timestamp,
    plus on-screen text (OCR) at the end.
    Best for: reading with context, searching, understanding flow.
    """
    caps = _get_run_captions(run, store)
    merged = _merge_captions(caps)
    ocrs = _get_run_ocr(run, store)
    url = run.metadata.get("url", "")

    lines = [
        f"# {run.metadata.get('title', 'Untitled')}",
        f"**Source:** {url}",
        f"**Duration:** {_fmt_ts(run.duration)}",
        "",
    ]

    for ts, text in merged:
        link = _yt_link(url, ts)
        lines.append(f"## [{_fmt_ts(ts)}]({link})")
        lines.append(text)
        lines.append("")

    if ocrs:
        seen = set()
        ocr_lines = []
        for o in sorted(ocrs, key=lambda e: e.timestamp):
            text = o.data.get("text", "").strip()
            if text and text not in seen and len(text) >= 3:
                seen.add(text)
                ocr_lines.append(f"- [{_fmt_ts(o.timestamp)}] {text}")
        if ocr_lines:
            lines.extend(["", "## On-Screen Text (OCR)", ""])
            lines.extend(ocr_lines)

    return "\n".join(lines).strip()


def export_srt(run: Run, store: EvidenceStore) -> str:
    """
    Standard SRT subtitle file from captions only.
    On-screen text is not included (SRT is subtitle format, not a knowledge dump).
    Best for: subtitles, accessibility, syncing with video.
    """
    caps = _get_run_captions(run, store)
    segments = _merge_captions_for_srt(caps)
    lines: list[str] = []
    for i, (start, end, text) in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def export_json(run: Run, store: EvidenceStore) -> dict[str, Any]:
    """
    Structured JSON with metadata, segments (captions), and on-screen text (OCR).
    Best for: programmatic use, embedding, building a knowledge base.
    """
    caps = _get_run_captions(run, store)
    ocrs = _get_run_ocr(run, store)
    merged = _merge_captions(caps)

    # Deduplicate OCR
    seen_ocr = set()
    ocr_list = []
    for o in sorted(ocrs, key=lambda e: e.timestamp):
        text = o.data.get("text", "").strip()
        if text and text not in seen_ocr and len(text) >= 3:
            seen_ocr.add(text)
            ocr_list.append({
                "timestamp": o.timestamp,
                "timestamp_formatted": _fmt_ts(o.timestamp),
                "text": text,
                "confidence": o.data.get("confidence"),
            })

    return {
        "id": run.id,
        "title": run.metadata.get("title", ""),
        "url": run.metadata.get("url", ""),
        "duration": run.duration,
        "duration_formatted": _fmt_ts(run.duration),
        "captions_count": len(caps),
        "segments_count": len(merged),
        "ocr_count": len(ocr_list),
        "merged_text": " ".join(text for _, text in merged),
        "segments": [
            {
                "index": i,
                "start": ts,
                "start_formatted": _fmt_ts(ts),
                "text": text,
            }
            for i, (ts, text) in enumerate(merged)
        ],
        "on_screen_text": ocr_list,
    }


# ─── Backward-compatible plain-text format (was format_run) ──────────────────

def format_run(run: Run, store: EvidenceStore) -> str:
    """
    Plain-text format — same as export_segments but with a simpler header.
    Kept for backward compatibility with existing callers.
    """
    return export_segments(run, store)
