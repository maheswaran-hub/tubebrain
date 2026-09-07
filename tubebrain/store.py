"""SQLite-backed evidence store for TubeBrain."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .models import Evidence, EvidenceKind, Run, SearchQuery, SearchResult, new_evidence_id


class EvidenceStore:
    def __init__(self, db_path: str | Path):
        import sqlite3
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration REAL NOT NULL DEFAULT 0.0,
                fps REAL NOT NULL DEFAULT 1.0,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                timestamp REAL NOT NULL DEFAULT 0.0,
                data TEXT NOT NULL DEFAULT '{}',
                file_path TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence(run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evidence_kind ON evidence(kind)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_evidence_ts ON evidence(timestamp)")
        self._conn.commit()

    # --- Runs ---

    def create_run(self, run: Run) -> None:
        cur = self._conn.cursor()
        metadata_json = json.dumps(run.metadata) if isinstance(run.metadata, dict) else run.metadata
        cur.execute(
            """INSERT INTO runs (id, source_type, source_path, created_at, duration, fps, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run.id,
                run.source_type.value,
                run.source_path,
                run.created_at.isoformat(),
                run.duration,
                run.fps,
                metadata_json,
            ),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> Optional[Run]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        return Run.from_dict(_row_to_dict(row)) if row else None

    def list_runs(self) -> list[Run]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM runs ORDER BY created_at DESC")
        return [Run.from_dict(_row_to_dict(row)) for row in cur.fetchall()]

    def delete_run(self, run_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM evidence WHERE run_id = ?", (run_id,))
        cur.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- Evidence ---

    def store_evidence(self, evidence: Evidence) -> None:
        cur = self._conn.cursor()
        data_json = json.dumps(evidence.data) if isinstance(evidence.data, dict) else str(evidence.data)
        cur.execute(
            """INSERT INTO evidence (id, run_id, kind, timestamp, data, file_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                evidence.id,
                evidence.run_id,
                evidence.kind.value,
                evidence.timestamp,
                data_json,
                evidence.file_path,
            ),
        )
        self._conn.commit()

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        row = cur.fetchone()
        return Evidence.from_dict(_row_to_dict(row)) if row else None

    def search_evidence(self, query: SearchQuery) -> SearchResult:
        import time
        start = time.time()

        cur = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if query.run_id:
            conditions.append("run_id = ?")
            params.append(query.run_id)

        if query.time_range:
            conditions.append("timestamp >= ? AND timestamp <= ?")
            params.extend(query.time_range)

        if query.kinds:
            kind_conds = " OR ".join(["kind = ?"] * len(query.kinds))
            conditions.append(f"({kind_conds})")
            params.extend(k.value for k in query.kinds)

        if query.query:
            conditions.append("data LIKE ?")
            params.append(f"%{query.query.lower()}%")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        limit = query.limit or 50

        sql = f"SELECT * FROM evidence WHERE {where_clause} ORDER BY timestamp ASC LIMIT ?"
        cur.execute(sql, params + [limit])
        rows = cur.fetchall()

        count_sql = f"SELECT COUNT(*) FROM evidence WHERE {where_clause}"
        total = self._conn.execute(count_sql, params).fetchone()[0]

        evidence_list = [Evidence.from_dict(_row_to_dict(row)) for row in rows]
        took_ms = (time.time() - start) * 1000
        return SearchResult(evidence=evidence_list, total=total, took_ms=took_ms)


def _row_to_dict(row) -> dict[str, Any]:
    """Convert sqlite3.Row to dict, parsing JSON fields."""
    import json
    d = dict(row)
    if isinstance(d.get("data"), str):
        try:
            d["data"] = json.loads(d["data"])
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d
