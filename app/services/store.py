"""SQLite persistence for runs, datasets, and schedules.

Runs and their datasets used to live only in memory — a restart lost
everything. This module is a thin write-through layer under the in-memory
stores: every mutation lands in SQLite (stdlib, WAL mode, no new deps),
and on startup the stores hydrate back from disk. Dataset items are
loaded lazily so a big history doesn't slow startup.

Set SCRAPEX_DB_FILE="" to disable persistence entirely (memory-only,
the old behavior).
"""
import json
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    data TEXT NOT NULL,
    request TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_items (
    dataset_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    item TEXT NOT NULL,
    PRIMARY KEY (dataset_id, seq)
);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


class PersistentStore:
    """Lazy-connecting SQLite store. All methods are no-ops when
    SCRAPEX_DB_FILE is empty."""

    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(settings.db_file)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(settings.db_file, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def reset(self) -> None:
        """Close the connection so the next call reopens settings.db_file.
        Lets tests point at a fresh temp database."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ---- runs / datasets -------------------------------------------------

    def save_run(self, run_json: str, request_json: str, run_id: str, dataset_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO runs (id, dataset_id, data, request) VALUES (?, ?, ?, ?)",
                (run_id, dataset_id, run_json, request_json),
            )
            conn.commit()

    def update_run(self, run_id: str, run_json: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE runs SET data = ? WHERE id = ?", (run_json, run_id))
            conn.commit()

    def save_dataset(self, dataset_id: str, run_id: str, platform: str, created_at: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO datasets (id, run_id, platform, created_at) VALUES (?, ?, ?, ?)",
                (dataset_id, run_id, platform, created_at),
            )
            conn.commit()

    def append_items(self, dataset_id: str, start_seq: int, items: List[Dict[str, Any]]) -> None:
        if not self.enabled or not items:
            return
        rows = [
            (dataset_id, start_seq + i, json.dumps(item, ensure_ascii=False, default=str))
            for i, item in enumerate(items)
        ]
        with self._lock:
            conn = self._connect()
            conn.executemany(
                "INSERT OR REPLACE INTO dataset_items (dataset_id, seq, item) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()

    def load_items(self, dataset_id: str) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT item FROM dataset_items WHERE dataset_id = ? ORDER BY seq", (dataset_id,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def count_items(self, dataset_id: str) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) FROM dataset_items WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return int(row[0])

    def load_runs(self) -> List[Tuple[str, str]]:
        """All (run_json, request_json) rows, oldest first (insertion order)."""
        if not self.enabled:
            return []
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT data, request FROM runs ORDER BY rowid").fetchall()
        return [(r[0], r[1]) for r in rows]

    def load_datasets(self) -> List[Tuple[str, str, str, str]]:
        """All (id, run_id, platform, created_at) rows, oldest first."""
        if not self.enabled:
            return []
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, run_id, platform, created_at FROM datasets ORDER BY rowid"
            ).fetchall()
        return list(rows)

    def delete_run(self, run_id: str, dataset_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
            conn.execute("DELETE FROM dataset_items WHERE dataset_id = ?", (dataset_id,))
            conn.commit()

    def wipe_runs(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM runs")
            conn.execute("DELETE FROM datasets")
            conn.execute("DELETE FROM dataset_items")
            conn.commit()

    # ---- schedules -------------------------------------------------------

    def save_schedule(self, schedule_id: str, data_json: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO schedules (id, data) VALUES (?, ?)",
                (schedule_id, data_json),
            )
            conn.commit()

    def load_schedules(self) -> List[str]:
        if not self.enabled:
            return []
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT data FROM schedules ORDER BY rowid").fetchall()
        return [r[0] for r in rows]

    def delete_schedule(self, schedule_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            conn.commit()

    def wipe_schedules(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM schedules")
            conn.commit()


persistent_store = PersistentStore()
