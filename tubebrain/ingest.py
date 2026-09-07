"""
YouTube download, caption extraction, and frame processing.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2


@dataclass
class IngestResult:
    run_id: str
    video_path: Path
    caption_count: int


def download_youtube(url: str, output_dir: Path) -> Path:
    """Download video using yt-dlp. Returns the path to the downloaded file."""
    import yt_dlp

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        downloaded = Path(filename)
        if not downloaded.exists():
            downloaded = downloaded.with_suffix(".mp4")
        if not downloaded.exists():
            raise RuntimeError(f"Downloaded file not found: {filename}")

    return downloaded


def download_captions(url: str, output_dir: Path, lang: str = "en") -> list[dict[str, Any]]:
    """Download YouTube auto-captions. Returns list of segments."""
    import yt_dlp

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": [lang],
        "skip_download": True,
        "outtmpl": str(output_dir / f"captions.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.extract_info(url, download=True)
        except Exception:
            return []

    vtt_files = list(output_dir.glob(f"captions.{lang}.vtt"))
    if not vtt_files:
        return []

    return _parse_vtt(vtt_files[0])


def get_playlist_urls(url: str) -> list[str] | None:
    """Return a list of video URLs if the URL is a playlist, else None.

    Uses yt-dlp's extract_flat to avoid downloading anything — just inspects
    the playlist metadata. Returns None for single video URLs.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception:
            return None
    if not info:
        return None
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries", []) if e]
        urls = []
        for e in entries:
            if e.get("url"):
                urls.append(e["url"])
            elif e.get("id"):
                urls.append(f"https://www.youtube.com/watch?v={e['id']}")
        return urls
    return None


def _parse_vtt(vtt_path: Path) -> list[dict[str, Any]]:
    """Parse a VTT file into timestamped segments."""
    content = vtt_path.read_text(encoding="utf-8", errors="ignore")
    content = re.sub(r"^WEBVTT.*?\n", "", content, flags=re.MULTILINE)

    timestamp_pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
    )

    segments = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        match = timestamp_pattern.match(line)
        if match:
            start_str, end_str = match.groups()
            start = _vtt_time_to_seconds(start_str)
            end = _vtt_time_to_seconds(end_str)

            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1

            text = re.sub(r"<[^>]+>", "", " ".join(text_lines))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                segments.append({"start": start, "end": end, "text": text})
        else:
            i += 1

    return segments


def _vtt_time_to_seconds(time_str: str) -> float:
    """Convert HH:MM:SS.mmm to seconds."""
    parts = time_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(time_str)


def extract_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = 1.0,
) -> list[dict[str, Any]]:
    """Extract frames from video at given fps. Returns list of frame info."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(video_fps / fps)) if fps > 0 else 1

    frames = []
    frame_idx = 0
    saved = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            timestamp = frame_idx / video_fps
            frame_name = f"frame_{saved:06d}.jpg"
            frame_path = output_dir / frame_name
            cv2.imwrite(str(frame_path), frame)

            frames.append({
                "index": saved,
                "timestamp": timestamp,
                "file_path": str(frame_path),
                "width": frame.shape[1],
                "height": frame.shape[0],
            })
            saved += 1

        frame_idx += 1

    cap.release()
    return frames


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


class OCRProcessor:
    """Tesseract OCR wrapper."""

    def __init__(self, langs: list[str] | None = None):
        import pytesseract
        self.langs = langs or ["eng"]
        self.lang_str = "+".join(self.langs)

    def extract_text(self, image_path: str) -> list[dict[str, Any]]:
        import pytesseract
        from PIL import Image

        try:
            image = Image.open(image_path)
            data = pytesseract.image_to_data(image, lang=self.lang_str, output_type=pytesseract.Output.DICT)
        except Exception:
            return []

        results = []
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            if text and int(data["conf"][i]) > 30:
                results.append({
                    "text": text,
                    "bbox": (
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                    ),
                    "confidence": data["conf"][i],
                })
        return results
