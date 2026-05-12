"""Durable local job state and operation ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    """Durable job lifecycle states."""

    QUEUED = "queued"
    PLANNING = "planning"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RECOVERING = "recovering"


class RiskLevel(str, Enum):
    """Risk categories used by approvals, jobs, and ledger entries."""

    READ_ONLY = "read_only"
    LOW_RISK_CHANGE = "low_risk_change"
    MEDIUM_RISK_CHANGE = "medium_risk_change"
    HIGH_RISK_CHANGE = "high_risk_change"
    IRREVERSIBLE_OR_EXTERNAL = "irreversible_or_external"


@dataclass
class JobRecord:
    """Persisted local job metadata."""

    id: str
    title: str
    original_user_input: str
    source_frontend: str
    status: JobStatus = JobStatus.QUEUED
    priority: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    parent_id: str | None = None
    normalized_task_spec: dict[str, Any] = field(default_factory=dict)
    current_plan: list[str] = field(default_factory=list)
    current_step: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    final_result: str | None = None
    error: str | None = None
    cancellation_requested: bool = False
    rollback: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    required_capabilities: list[str] = field(default_factory=list)
    used_capabilities: list[str] = field(default_factory=list)
    affected_targets: list[str] = field(default_factory=list)


@dataclass
class JobLogEntry:
    """Append-only job log line."""

    id: int
    job_id: str
    timestamp: float
    level: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationLedgerEntry:
    """Append-only record of a computer-changing operation."""

    id: str
    job_id: str
    user_request: str
    normalized_action: dict[str, Any]
    capability: str
    target: str
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    risk_level: RiskLevel
    timestamp: float
    success: bool
    undo: dict[str, Any] = field(default_factory=dict)
    approval_id: str | None = None
    error: str | None = None


class DurableJobStore:
    """SQLite-backed local store for jobs, logs, and operation ledger entries."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._migrate()

    def create_job(
        self,
        *,
        title: str,
        original_user_input: str,
        source_frontend: str,
        id: str | None = None,
        normalized_task_spec: dict[str, Any] | None = None,
        status: JobStatus = JobStatus.QUEUED,
        priority: int = 0,
        parent_id: str | None = None,
        current_plan: list[str] | None = None,
        current_step: str = "",
        risk_level: RiskLevel = RiskLevel.READ_ONLY,
        required_capabilities: list[str] | None = None,
    ) -> JobRecord:
        now = time.time()
        record = JobRecord(
            id=id or f"j_{uuid.uuid4().hex[:12]}",
            title=title,
            original_user_input=original_user_input,
            source_frontend=source_frontend,
            status=status,
            priority=priority,
            created_at=now,
            updated_at=now,
            parent_id=parent_id,
            normalized_task_spec=dict(normalized_task_spec or {}),
            current_plan=list(current_plan or []),
            current_step=current_step,
            risk_level=risk_level,
            required_capabilities=list(required_capabilities or []),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, title, original_user_input, source_frontend, status, priority, created_at, updated_at,
                    completed_at, parent_id, normalized_task_spec, current_plan, current_step, artifacts,
                    approvals, final_result, error, cancellation_requested, rollback, risk_level,
                    required_capabilities, used_capabilities, affected_targets
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._job_values(record),
            )
            self._conn.commit()
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row is not None else None

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        current_step: str | None = None,
        current_plan: list[str] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        approvals: list[str] | None = None,
        final_result: str | None = None,
        error: str | None = None,
        cancellation_requested: bool | None = None,
        rollback: dict[str, Any] | None = None,
        used_capabilities: list[str] | None = None,
        affected_targets: list[str] | None = None,
    ) -> JobRecord | None:
        existing = self.get_job(job_id)
        if existing is None:
            return None
        completed_at = existing.completed_at
        if status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED} and completed_at is None:
            completed_at = time.time()
        updated = JobRecord(
            **{
                **existing.__dict__,
                "status": status or existing.status,
                "updated_at": time.time(),
                "completed_at": completed_at,
                "current_step": existing.current_step if current_step is None else current_step,
                "current_plan": existing.current_plan if current_plan is None else list(current_plan),
                "artifacts": existing.artifacts if artifacts is None else list(artifacts),
                "approvals": existing.approvals if approvals is None else list(approvals),
                "final_result": existing.final_result if final_result is None else final_result,
                "error": existing.error if error is None else error,
                "cancellation_requested": (
                    existing.cancellation_requested if cancellation_requested is None else cancellation_requested
                ),
                "rollback": existing.rollback if rollback is None else dict(rollback),
                "used_capabilities": existing.used_capabilities if used_capabilities is None else list(used_capabilities),
                "affected_targets": existing.affected_targets if affected_targets is None else list(affected_targets),
            }
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs SET
                    title = ?, original_user_input = ?, source_frontend = ?, status = ?, priority = ?,
                    created_at = ?, updated_at = ?, completed_at = ?, parent_id = ?, normalized_task_spec = ?,
                    current_plan = ?, current_step = ?, artifacts = ?, approvals = ?, final_result = ?,
                    error = ?, cancellation_requested = ?, rollback = ?, risk_level = ?,
                    required_capabilities = ?, used_capabilities = ?, affected_targets = ?
                WHERE id = ?
                """,
                (*self._job_values(updated)[1:], updated.id),
            )
            self._conn.commit()
        return updated

    def record_log(
        self,
        job_id: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> JobLogEntry:
        timestamp = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO job_logs (job_id, timestamp, level, message, data) VALUES (?, ?, ?, ?, ?)",
                (job_id, timestamp, level, message, self._dump(data or {})),
            )
            self._conn.commit()
            log_id = int(cursor.lastrowid)
        return JobLogEntry(id=log_id, job_id=job_id, timestamp=timestamp, level=level, message=message, data=data or {})

    def list_logs(self, job_id: str, limit: int = 100) -> list[JobLogEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM job_logs WHERE job_id = ? ORDER BY id ASC LIMIT ?",
                (job_id, max(1, int(limit))),
            ).fetchall()
        return [self._log_from_row(row) for row in rows]

    def record_operation(
        self,
        *,
        job_id: str,
        user_request: str,
        normalized_action: dict[str, Any],
        capability: str,
        target: str,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        risk_level: RiskLevel,
        success: bool,
        undo: dict[str, Any] | None = None,
        approval_id: str | None = None,
        error: str | None = None,
    ) -> OperationLedgerEntry:
        entry = OperationLedgerEntry(
            id=f"op_{uuid.uuid4().hex[:12]}",
            job_id=job_id,
            user_request=user_request,
            normalized_action=dict(normalized_action),
            capability=capability,
            target=target,
            before_state=dict(before_state),
            after_state=dict(after_state),
            risk_level=risk_level,
            timestamp=time.time(),
            success=success,
            undo=dict(undo or {}),
            approval_id=approval_id,
            error=error,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO operation_ledger (
                    id, job_id, user_request, normalized_action, capability, target, before_state, after_state,
                    risk_level, timestamp, success, undo, approval_id, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.job_id,
                    entry.user_request,
                    self._dump(entry.normalized_action),
                    entry.capability,
                    entry.target,
                    self._dump(entry.before_state),
                    self._dump(entry.after_state),
                    entry.risk_level.value,
                    entry.timestamp,
                    int(entry.success),
                    self._dump(entry.undo),
                    entry.approval_id,
                    entry.error,
                ),
            )
            self._conn.commit()
        return entry

    def list_operations(self, job_id: str | None = None, limit: int = 100) -> list[OperationLedgerEntry]:
        with self._lock:
            if job_id:
                rows = self._conn.execute(
                    "SELECT * FROM operation_ledger WHERE job_id = ? ORDER BY timestamp ASC LIMIT ?",
                    (job_id, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM operation_ledger ORDER BY timestamp DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
        return [self._operation_from_row(row) for row in rows]

    def clear_terminal_jobs(self) -> int:
        """Delete completed, failed, and cancelled jobs plus their logs and ledger entries."""
        terminal_statuses = (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN (?, ?, ?)",
                terminal_statuses,
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            for job_id in job_ids:
                self._conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
                self._conn.execute("DELETE FROM operation_ledger WHERE job_id = ?", (job_id,))
                self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()
        return len(job_ids)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    original_user_input TEXT NOT NULL,
                    source_frontend TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    parent_id TEXT,
                    normalized_task_spec TEXT NOT NULL,
                    current_plan TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    artifacts TEXT NOT NULL,
                    approvals TEXT NOT NULL,
                    final_result TEXT,
                    error TEXT,
                    cancellation_requested INTEGER NOT NULL,
                    rollback TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    required_capabilities TEXT NOT NULL,
                    used_capabilities TEXT NOT NULL,
                    affected_targets TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operation_ledger (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    normalized_action TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    target TEXT NOT NULL,
                    before_state TEXT NOT NULL,
                    after_state TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    success INTEGER NOT NULL,
                    undo TEXT NOT NULL,
                    approval_id TEXT,
                    error TEXT
                );
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

    def _job_values(self, record: JobRecord) -> tuple[Any, ...]:
        return (
            record.id,
            record.title,
            record.original_user_input,
            record.source_frontend,
            record.status.value,
            record.priority,
            record.created_at,
            record.updated_at,
            record.completed_at,
            record.parent_id,
            self._dump(record.normalized_task_spec),
            self._dump(record.current_plan),
            record.current_step,
            self._dump(record.artifacts),
            self._dump(record.approvals),
            record.final_result,
            record.error,
            int(record.cancellation_requested),
            self._dump(record.rollback),
            record.risk_level.value,
            self._dump(record.required_capabilities),
            self._dump(record.used_capabilities),
            self._dump(record.affected_targets),
        )

    def _job_from_row(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            title=row["title"],
            original_user_input=row["original_user_input"],
            source_frontend=row["source_frontend"],
            status=JobStatus(row["status"]),
            priority=int(row["priority"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            completed_at=row["completed_at"],
            parent_id=row["parent_id"],
            normalized_task_spec=self._load(row["normalized_task_spec"], {}),
            current_plan=self._load(row["current_plan"], []),
            current_step=row["current_step"],
            artifacts=self._load(row["artifacts"], []),
            approvals=self._load(row["approvals"], []),
            final_result=row["final_result"],
            error=row["error"],
            cancellation_requested=bool(row["cancellation_requested"]),
            rollback=self._load(row["rollback"], {}),
            risk_level=RiskLevel(row["risk_level"]),
            required_capabilities=self._load(row["required_capabilities"], []),
            used_capabilities=self._load(row["used_capabilities"], []),
            affected_targets=self._load(row["affected_targets"], []),
        )

    def _log_from_row(self, row: sqlite3.Row) -> JobLogEntry:
        return JobLogEntry(
            id=int(row["id"]),
            job_id=row["job_id"],
            timestamp=float(row["timestamp"]),
            level=row["level"],
            message=row["message"],
            data=self._load(row["data"], {}),
        )

    def _operation_from_row(self, row: sqlite3.Row) -> OperationLedgerEntry:
        return OperationLedgerEntry(
            id=row["id"],
            job_id=row["job_id"],
            user_request=row["user_request"],
            normalized_action=self._load(row["normalized_action"], {}),
            capability=row["capability"],
            target=row["target"],
            before_state=self._load(row["before_state"], {}),
            after_state=self._load(row["after_state"], {}),
            risk_level=RiskLevel(row["risk_level"]),
            timestamp=float(row["timestamp"]),
            success=bool(row["success"]),
            undo=self._load(row["undo"], {}),
            approval_id=row["approval_id"],
            error=row["error"],
        )
