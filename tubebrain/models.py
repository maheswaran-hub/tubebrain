"""Core data models for TubeBrain."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    YOUTUBE = "youtube"
    VIDEO = "video"


class EvidenceKind(str, Enum):
    FRAME = "frame"
    TRANSCRIPT = "transcript"
    OCR = "ocr"


class Run:
    id: str
    source_type: SourceType
    source_path: str
    created_at: datetime
    duration: float
    fps: float
    metadata: dict[str, Any]

    def __init__(
        self,
        id: str | None = None,
        source_type: SourceType = SourceType.VIDEO,
        source_path: str = "",
        created_at: datetime | None = None,
        duration: float = 0.0,
        fps: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ):
        self.id = id or new_run_id()
        self.source_type = source_type
        self.source_path = source_path
        self.created_at = created_at or datetime.now()
        self.duration = duration
        self.fps = fps
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type.value,
            "source_path": self.source_path,
            "created_at": self.created_at.isoformat(),
            "duration": self.duration,
            "fps": self.fps,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        import json
        data = dict(data)
        if isinstance(data.get("source_type"), str):
            data["source_type"] = SourceType(data["source_type"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("metadata"), str):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class Evidence:
    id: str
    run_id: str
    kind: EvidenceKind
    timestamp: float
    data: dict[str, Any]
    file_path: str | None

    def __init__(
        self,
        run_id: str = "",
        kind: EvidenceKind = EvidenceKind.FRAME,
        timestamp: float = 0.0,
        data: dict[str, Any] | None = None,
        file_path: str | None = None,
        id: str | None = None,
    ):
        self.id = id or new_run_id()
        self.run_id = run_id
        self.kind = kind
        self.timestamp = timestamp
        self.data = data or {}
        self.file_path = file_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind.value,
            "timestamp": self.timestamp,
            "data": self.data,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        import json
        data = dict(data)
        if isinstance(data.get("kind"), str):
            data["kind"] = EvidenceKind(data["kind"])
        if isinstance(data.get("data"), str):
            try:
                data["data"] = json.loads(data["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class SearchQuery:
    query: str = ""
    run_id: str | None = None
    time_range: tuple[float, float] | None = None
    kinds: list[EvidenceKind] | None = None
    limit: int = 50

    def __init__(
        self,
        query: str = "",
        run_id: str | None = None,
        time_range: tuple[float, float] | None = None,
        kinds: list[EvidenceKind] | None = None,
        limit: int = 50,
    ):
        self.query = query
        self.run_id = run_id
        self.time_range = time_range
        self.kinds = kinds or []
        self.limit = limit


class SearchResult:
    evidence: list[Evidence]
    total: int
    took_ms: float

    def __init__(self, evidence: list[Evidence], total: int, took_ms: float = 0.0):
        self.evidence = evidence
        self.total = total
        self.took_ms = took_ms


def new_run_id() -> str:
    return str(uuid.uuid4())[:8]


def new_evidence_id() -> str:
    return str(uuid.uuid4())[:8]


def fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
