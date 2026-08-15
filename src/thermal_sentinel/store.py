"""Durable storage for completed experiment evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


class ExperimentStore:
    """Store completed runs as canonical JSON documents in SQLite.

    The simulation never depends on this adapter. If persistence fails, the
    caller can still return the in-memory result to the operator.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._open() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS experiments_created_idx "
                "ON experiments(created_at DESC)"
            )

    @staticmethod
    def canonical_json(result: dict[str, Any]) -> str:
        return json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def digest(cls, result: dict[str, Any]) -> str:
        payload = cls.canonical_json(result).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def save(self, result: dict[str, Any]) -> dict[str, Any]:
        run_id = str(result.get("run_id") or uuid.uuid4())
        seed = int(result["seed"])
        created_at = datetime.now(UTC).isoformat()
        payload = self.canonical_json(result)
        digest = self.digest(result)
        with self._lock, self._open() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experiments
                    (id, created_at, seed, digest, result_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, created_at, seed, digest, payload),
            )
        return {"id": run_id, "created_at": created_at, "seed": seed, "digest": digest}

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._open() as connection:
            rows = connection.execute(
                "SELECT id, created_at, seed, digest, result_json "
                "FROM experiments ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            result = json.loads(row["result_json"])
            records.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "seed": row["seed"],
                    "digest": row["digest"],
                    "summary": result.get("comparison", result.get("summary", {})),
                }
            )
        return records

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._open() as connection:
            row = connection.execute(
                "SELECT id, created_at, seed, digest, result_json "
                "FROM experiments WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "seed": row["seed"],
            "digest": row["digest"],
            "result": json.loads(row["result_json"]),
        }
