"""Background task ownership and lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from utils.semantic_history import sanitize_semantic_entries


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting for user"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
_id_counter = itertools.count(1)


def _new_task_id() -> str:
    return f"t{next(_id_counter)}_{uuid.uuid4().hex[:8]}"


@dataclass
class BackgroundTask:
    """User-visible task metadata."""

    title: str
    original_request: str
    id: str = field(default_factory=_new_task_id)
    status: TaskStatus = TaskStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress_summary: str = "Queued"
    final_result: str | None = None
    error: str | None = None
    needs_user_input: bool = False
    used_tools: bool = False
    required_network: bool = False
    required_filesystem: bool = False
    required_vision: bool = False
    normalized_task_spec: dict = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    cancellation_requested: bool = False
    pause_requested: bool = False
    related_context: list[dict] = field(default_factory=list)
    _asyncio_task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    def mark_progress(self, summary: str) -> None:
        self.progress_summary = " ".join(summary.split()) if summary else self.progress_summary
        self.updated_at = time.time()

    def mark_waiting(self, summary: str) -> None:
        self.status = TaskStatus.WAITING_FOR_USER
        self.needs_user_input = True
        self.mark_progress(summary)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


TaskWorker = Callable[[BackgroundTask], Awaitable[str | None]]
TaskEventCallback = Callable[[BackgroundTask, str], None]


class BackgroundTaskManager:
    """Owns queued/running/recent tasks and keeps failures contained."""

    def __init__(
        self,
        max_concurrent: int = 2,
        task_timeout_seconds: float = 300,
        on_event: TaskEventCallback | None = None,
        job_store: DurableJobStore | None = None,
        source_frontend: str = "terminal",
    ) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._order: list[str] = []
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._timeout = max(1.0, float(task_timeout_seconds))
        self._on_event = on_event
        self._event_listeners: list[TaskEventCallback] = []
        self._job_store = job_store
        self._source_frontend = source_frontend
        self._pause_events: dict[str, asyncio.Event] = {}

    def create_task(
        self,
        title: str,
        original_request: str,
        worker: TaskWorker,
        *,
        related_context: list[dict] | None = None,
        used_tools: bool = False,
        required_network: bool = False,
        required_filesystem: bool = False,
        required_vision: bool = False,
        normalized_task_spec: dict | None = None,
        risk_level: RiskLevel = RiskLevel.READ_ONLY,
    ) -> BackgroundTask:
        task = BackgroundTask(
            title=title,
            original_request=original_request,
            related_context=list(related_context or []),
            used_tools=used_tools,
            required_network=required_network,
            required_filesystem=required_filesystem,
            required_vision=required_vision,
            normalized_task_spec=dict(normalized_task_spec or {}),
            risk_level=risk_level,
        )
        self._tasks[task.id] = task
        self._order.append(task.id)
        pause_event = asyncio.Event()
        pause_event.set()
        self._pause_events[task.id] = pause_event
        self._persist_created_task(task)
        self._emit(task, "accepted")
        task._asyncio_task = asyncio.create_task(self._run_task(task, worker), name=f"eyra-task-{task.id}")
        return task

    def add_event_listener(self, listener: TaskEventCallback) -> None:
        """Register an additional task event listener."""
        if listener not in self._event_listeners:
            self._event_listeners.append(listener)

    def remove_event_listener(self, listener: TaskEventCallback) -> None:
        """Remove a previously registered task event listener."""
        with contextlib.suppress(ValueError):
            self._event_listeners.remove(listener)

    async def _run_task(self, task: BackgroundTask, worker: TaskWorker) -> None:
        await self._wait_if_paused(task)
        async with self._semaphore:
            await self._wait_if_paused(task)
            if task.cancellation_requested:
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled before start")
                self._persist_task(task, JobStatus.CANCELLED)
                self._emit(task, "cancelled")
                return
            task.status = TaskStatus.RUNNING
            task.mark_progress("Started")
            self._persist_task(task, JobStatus.RUNNING, current_step="Started")
            self._emit(task, "started")
            try:
                result = await asyncio.wait_for(worker(task), timeout=self._timeout)
            except asyncio.CancelledError:
                task.cancellation_requested = True
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled")
                self._persist_task(task, JobStatus.CANCELLED)
                self._emit(task, "cancelled")
                return
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.error = f"Task timed out after {int(self._timeout)} seconds."
                task.mark_progress("Timed out")
                self._persist_task(task, JobStatus.FAILED)
                self._emit(task, "failed")
                return
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc) or exc.__class__.__name__
                task.mark_progress("Failed")
                self._persist_task(task, JobStatus.FAILED)
                self._emit(task, "failed")
                return

            if task.status == TaskStatus.WAITING_FOR_USER:
                self._persist_task(task, JobStatus.WAITING_FOR_USER)
                self._emit(task, "waiting")
                return
            if task.cancellation_requested:
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled")
                self._persist_task(task, JobStatus.CANCELLED)
                self._emit(task, "cancelled")
                return
            task.status = TaskStatus.COMPLETED
            task.final_result = (result or "").strip() or "Done."
            task.mark_progress("Completed")
            self._persist_task(task, JobStatus.COMPLETED)
            self._emit(task, "completed")

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, include_recent: bool = True, limit: int = 10) -> list[BackgroundTask]:
        ids = list(reversed(self._order))
        rows: list[BackgroundTask] = []
        for task_id in ids:
            task = self._tasks[task_id]
            if include_recent or not task.is_terminal:
                rows.append(task)
            if len(rows) >= limit:
                break
        return rows

    def active_tasks(self) -> list[BackgroundTask]:
        return [
            self._tasks[task_id]
            for task_id in self._order
            if self._tasks[task_id].status in {
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_FOR_USER,
                TaskStatus.PAUSED,
            }
        ]

    def latest_active_task(self) -> BackgroundTask | None:
        active = self.active_tasks()
        return active[-1] if active else None

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal:
            return False
        task.cancellation_requested = True
        pause_event = self._pause_events.get(task_id)
        if pause_event is not None:
            pause_event.set()
        self._persist_task(task, JobStatus.CANCELLING, cancellation_requested=True)
        if task._asyncio_task is not None:
            task._asyncio_task.cancel()
        return True

    def pause_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal or task.status != TaskStatus.QUEUED:
            return False
        task.pause_requested = True
        task.status = TaskStatus.PAUSED
        task.mark_progress("Paused")
        pause_event = self._pause_events.get(task_id)
        if pause_event is not None:
            pause_event.clear()
        self._persist_task(task, JobStatus.PAUSED)
        self._emit(task, "paused")
        return True

    def resume_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != TaskStatus.PAUSED:
            return False
        task.pause_requested = False
        task.status = TaskStatus.QUEUED
        task.mark_progress("Queued")
        pause_event = self._pause_events.get(task_id)
        if pause_event is not None:
            pause_event.set()
        self._persist_task(task, JobStatus.QUEUED)
        self._emit(task, "resumed")
        return True

    def cancel_all(self) -> int:
        count = 0
        for task in self.active_tasks():
            if self.cancel_task(task.id):
                count += 1
        return count

    def clear_terminal_tasks(self) -> int:
        """Forget completed, failed, and cancelled in-memory task rows."""
        terminal_ids = [task_id for task_id, task in self._tasks.items() if task.is_terminal]
        for task_id in terminal_ids:
            self._tasks.pop(task_id, None)
            self._pause_events.pop(task_id, None)
            with contextlib.suppress(ValueError):
                self._order.remove(task_id)
        return len(terminal_ids)

    async def wait_for_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task._asyncio_task is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            await task._asyncio_task

    async def shutdown(self) -> None:
        self.cancel_all()
        running = [task._asyncio_task for task in self._tasks.values() if task._asyncio_task is not None]
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _wait_if_paused(self, task: BackgroundTask) -> None:
        try:
            while task.status == TaskStatus.PAUSED and not task.cancellation_requested:
                pause_event = self._pause_events.get(task.id)
                if pause_event is None:
                    return
                await pause_event.wait()
        except asyncio.CancelledError:
            task.cancellation_requested = True
            task.status = TaskStatus.CANCELLED
            task.mark_progress("Cancelled")
            self._persist_task(task, JobStatus.CANCELLED)
            self._emit(task, "cancelled")
            raise

    def _emit(self, task: BackgroundTask, event: str) -> None:
        task.updated_at = time.time()
        if self._job_store is not None:
            self._job_store.record_log(task.id, task.progress_summary, data={"event": event})
        if self._on_event:
            self._on_event(task, event)
        for listener in list(self._event_listeners):
            listener(task, event)

    def _persist_created_task(self, task: BackgroundTask) -> None:
        if self._job_store is None:
            return
        capabilities = []
        if task.required_filesystem:
            capabilities.append("filesystem")
        if task.required_network:
            capabilities.append("network")
        if task.required_vision:
            capabilities.append("vision")
        if task.used_tools:
            capabilities.append("tools")
        self._job_store.create_job(
            id=task.id,
            title=task.title,
            original_user_input=task.original_request,
            source_frontend=self._source_frontend,
            normalized_task_spec=task.normalized_task_spec
            or {
                "compatibility_task": True,
                "related_context": sanitize_semantic_entries(task.related_context),
            },
            risk_level=task.risk_level,
            required_capabilities=capabilities,
            current_plan=["Accepted", "Run worker", "Verify result"],
        )

    def _persist_task(
        self,
        task: BackgroundTask,
        status: JobStatus,
        *,
        current_step: str | None = None,
        cancellation_requested: bool | None = None,
    ) -> None:
        if self._job_store is None:
            return
        self._job_store.update_job(
            task.id,
            status=status,
            current_step=current_step if current_step is not None else task.progress_summary,
            final_result=task.final_result,
            error=task.error,
            cancellation_requested=task.cancellation_requested
            if cancellation_requested is None
            else cancellation_requested,
        )
