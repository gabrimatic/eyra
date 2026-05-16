"""Durable local trigger definitions."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TriggerStatus(str, Enum):
    """Durable trigger lifecycle states."""

    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


async def wait_for_file_ready(path: str | Path, *, settle_seconds: float = 0.05, attempts: int = 3) -> bool:
    """Wait until a newly appeared file has a stable size and mtime."""
    target = Path(path).expanduser()
    previous: tuple[int, int] | None = None
    for _ in range(max(1, attempts)):
        if not target.exists():
            return False
        stat = target.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if current == previous:
            return True
        previous = current
        await asyncio.sleep(max(0.01, settle_seconds))
    return False


@dataclass
class TriggerRecord:
    """Persisted local trigger metadata."""

    id: str
    title: str
    kind: str
    condition: dict[str, Any]
    action: dict[str, Any]
    original_request: str
    status: TriggerStatus = TriggerStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    last_error: str | None = None


class TriggerStore:
    """SQLite-backed local store for user-created triggers."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with suppress(OSError):
            self.path.chmod(0o600)
        self._lock = threading.RLock()
        self._migrate()

    def create_file_exists_trigger(
        self,
        *,
        title: str,
        source_path: str,
        action: dict[str, Any],
        original_request: str,
    ) -> TriggerRecord:
        now = time.time()
        record = TriggerRecord(
            id=f"tr_{uuid.uuid4().hex[:12]}",
            title=title,
            kind="file_exists",
            condition={"path": source_path},
            action=dict(action),
            original_request=original_request,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO triggers (
                    id, title, kind, status, condition, action, original_request,
                    created_at, updated_at, completed_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._trigger_values(record),
            )
            self._conn.commit()
        return record

    def create_timer_trigger(
        self,
        *,
        title: str,
        fire_at: float,
        action: dict[str, Any],
        original_request: str,
    ) -> TriggerRecord:
        now = time.time()
        record = TriggerRecord(
            id=f"tr_{uuid.uuid4().hex[:12]}",
            title=title,
            kind="timer",
            condition={"fire_at": float(fire_at)},
            action=dict(action),
            original_request=original_request,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO triggers (
                    id, title, kind, status, condition, action, original_request,
                    created_at, updated_at, completed_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._trigger_values(record),
            )
            self._conn.commit()
        return record

    def create_recurring_timer_trigger(
        self,
        *,
        title: str,
        interval_seconds: float,
        next_fire_at: float,
        action: dict[str, Any],
        original_request: str,
    ) -> TriggerRecord:
        now = time.time()
        record = TriggerRecord(
            id=f"tr_{uuid.uuid4().hex[:12]}",
            title=title,
            kind="recurring_timer",
            condition={
                "interval_seconds": float(interval_seconds),
                "next_fire_at": float(next_fire_at),
                "fire_count": 0,
            },
            action=dict(action),
            original_request=original_request,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO triggers (
                    id, title, kind, status, condition, action, original_request,
                    created_at, updated_at, completed_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._trigger_values(record),
            )
            self._conn.commit()
        return record

    def record_recurring_fire(self, trigger_id: str, *, last_fire_at: float, next_fire_at: float) -> TriggerRecord | None:
        record = self.get_trigger(trigger_id)
        if record is None:
            return None
        condition = dict(record.condition)
        condition["last_fire_at"] = float(last_fire_at)
        condition["next_fire_at"] = float(next_fire_at)
        condition["fire_count"] = int(condition.get("fire_count", 0)) + 1
        updated = TriggerRecord(
            **{
                **record.__dict__,
                "condition": condition,
                "updated_at": time.time(),
            }
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE triggers SET
                    title = ?, kind = ?, status = ?, condition = ?, action = ?, original_request = ?,
                    created_at = ?, updated_at = ?, completed_at = ?, last_error = ?
                WHERE id = ?
                """,
                (*self._trigger_values(updated)[1:], updated.id),
            )
            self._conn.commit()
        return updated

    def get_trigger(self, trigger_id: str) -> TriggerRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return self._trigger_from_row(row) if row is not None else None

    def list_triggers(self, limit: int = 50) -> list[TriggerRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM triggers ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._trigger_from_row(row) for row in rows]

    def mark_completed(self, trigger_id: str) -> TriggerRecord | None:
        return self._update_status(trigger_id, TriggerStatus.COMPLETED)

    def mark_cancelled(self, trigger_id: str) -> TriggerRecord | None:
        return self._update_status(trigger_id, TriggerStatus.CANCELLED)

    def mark_paused(self, trigger_id: str) -> TriggerRecord | None:
        return self._update_status(trigger_id, TriggerStatus.PAUSED)

    def mark_active(self, trigger_id: str) -> TriggerRecord | None:
        return self._update_status(trigger_id, TriggerStatus.ACTIVE)

    def mark_failed(self, trigger_id: str, error: str) -> TriggerRecord | None:
        return self._update_status(trigger_id, TriggerStatus.FAILED, last_error=error)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _update_status(
        self,
        trigger_id: str,
        status: TriggerStatus,
        *,
        last_error: str | None = None,
    ) -> TriggerRecord | None:
        record = self.get_trigger(trigger_id)
        if record is None:
            return None
        completed_at = record.completed_at
        if status in {TriggerStatus.CANCELLED, TriggerStatus.COMPLETED, TriggerStatus.FAILED}:
            completed_at = completed_at or time.time()
        elif status == TriggerStatus.ACTIVE:
            completed_at = None
        updated = TriggerRecord(
            **{
                **record.__dict__,
                "status": status,
                "updated_at": time.time(),
                "completed_at": completed_at,
                "last_error": last_error,
            }
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE triggers SET
                    title = ?, kind = ?, status = ?, condition = ?, action = ?, original_request = ?,
                    created_at = ?, updated_at = ?, completed_at = ?, last_error = ?
                WHERE id = ?
                """,
                (*self._trigger_values(updated)[1:], updated.id),
            )
            self._conn.commit()
        return updated

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;
                PRAGMA user_version = 1;

                CREATE TABLE IF NOT EXISTS triggers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    action TEXT NOT NULL,
                    original_request TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_triggers_status_updated
                    ON triggers(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_triggers_kind_status
                    ON triggers(kind, status);
                """
            )
            self._conn.commit()

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _trigger_values(self, record: TriggerRecord) -> tuple[Any, ...]:
        return (
            record.id,
            record.title,
            record.kind,
            record.status.value,
            self._dump(record.condition),
            self._dump(record.action),
            record.original_request,
            record.created_at,
            record.updated_at,
            record.completed_at,
            record.last_error,
        )

    def _trigger_from_row(self, row: sqlite3.Row) -> TriggerRecord:
        return TriggerRecord(
            id=row["id"],
            title=row["title"],
            kind=row["kind"],
            status=TriggerStatus(row["status"]),
            condition=self._load(row["condition"], {}),
            action=self._load(row["action"], {}),
            original_request=row["original_request"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            completed_at=row["completed_at"],
            last_error=row["last_error"],
        )
