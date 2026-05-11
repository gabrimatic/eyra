"""Background task ownership and lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting for user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
_id_counter = itertools.count(1)


@dataclass
class BackgroundTask:
    """User-visible task metadata."""

    title: str
    original_request: str
    id: str = field(default_factory=lambda: f"t{next(_id_counter)}")
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
    cancellation_requested: bool = False
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
    ) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._order: list[str] = []
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._timeout = max(1.0, float(task_timeout_seconds))
        self._on_event = on_event

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
    ) -> BackgroundTask:
        task = BackgroundTask(
            title=title,
            original_request=original_request,
            related_context=list(related_context or []),
            used_tools=used_tools,
            required_network=required_network,
            required_filesystem=required_filesystem,
            required_vision=required_vision,
        )
        self._tasks[task.id] = task
        self._order.append(task.id)
        self._emit(task, "accepted")
        task._asyncio_task = asyncio.create_task(self._run_task(task, worker), name=f"eyra-task-{task.id}")
        return task

    async def _run_task(self, task: BackgroundTask, worker: TaskWorker) -> None:
        async with self._semaphore:
            if task.cancellation_requested:
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled before start")
                self._emit(task, "cancelled")
                return
            task.status = TaskStatus.RUNNING
            task.mark_progress("Started")
            self._emit(task, "started")
            try:
                result = await asyncio.wait_for(worker(task), timeout=self._timeout)
            except asyncio.CancelledError:
                task.cancellation_requested = True
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled")
                self._emit(task, "cancelled")
                return
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.error = f"Task timed out after {int(self._timeout)} seconds."
                task.mark_progress("Timed out")
                self._emit(task, "failed")
                return
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc) or exc.__class__.__name__
                task.mark_progress("Failed")
                self._emit(task, "failed")
                return

            if task.status == TaskStatus.WAITING_FOR_USER:
                self._emit(task, "waiting")
                return
            if task.cancellation_requested:
                task.status = TaskStatus.CANCELLED
                task.mark_progress("Cancelled")
                self._emit(task, "cancelled")
                return
            task.status = TaskStatus.COMPLETED
            task.final_result = (result or "").strip() or "Done."
            task.mark_progress("Completed")
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
            if self._tasks[task_id].status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER}
        ]

    def latest_active_task(self) -> BackgroundTask | None:
        active = self.active_tasks()
        return active[-1] if active else None

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal:
            return False
        task.cancellation_requested = True
        if task._asyncio_task is not None:
            task._asyncio_task.cancel()
        return True

    def cancel_all(self) -> int:
        count = 0
        for task in self.active_tasks():
            if self.cancel_task(task.id):
                count += 1
        return count

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

    def _emit(self, task: BackgroundTask, event: str) -> None:
        task.updated_at = time.time()
        if self._on_event:
            self._on_event(task, event)
