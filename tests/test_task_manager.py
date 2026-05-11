"""Tests for background task lifecycle and coordinator behavior."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.tasks import BackgroundTaskManager, TaskStatus


def _run(coro):
    return asyncio.run(coro)


class TestBackgroundTaskManager:
    def test_task_completes_with_final_result(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=5)

            async def worker(task):
                task.mark_progress("halfway")
                return "done"

            task = manager.create_task("Summary", "summarize this", worker)
            await manager.wait_for_task(task.id)

            detail = manager.get_task(task.id)
            assert detail is not None
            assert detail.status == TaskStatus.COMPLETED
            assert detail.progress_summary == "Completed"
            assert detail.final_result == "done"
            assert detail.error is None

        _run(run())

    def test_task_failure_is_captured_without_crashing_manager(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=5)

            async def worker(task):
                raise RuntimeError("boom")

            task = manager.create_task("Broken", "fail", worker)
            await manager.wait_for_task(task.id)

            detail = manager.get_task(task.id)
            assert detail is not None
            assert detail.status == TaskStatus.FAILED
            assert "boom" in (detail.error or "")

        _run(run())

    def test_task_cancellation(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=30)
            started = asyncio.Event()

            async def worker(task):
                started.set()
                await asyncio.sleep(30)
                return "should not finish"

            task = manager.create_task("Long", "wait", worker)
            await started.wait()
            assert manager.cancel_task(task.id) is True
            await manager.wait_for_task(task.id)

            detail = manager.get_task(task.id)
            assert detail is not None
            assert detail.status == TaskStatus.CANCELLED
            assert detail.cancellation_requested is True

        _run(run())

    def test_task_timeout_marks_failed(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=0.05)

            async def worker(task):
                await asyncio.sleep(30)
                return "late"

            task = manager.create_task("Slow", "timeout", worker)
            await manager.wait_for_task(task.id)

            detail = manager.get_task(task.id)
            assert detail is not None
            assert detail.status == TaskStatus.FAILED
            assert "timed out" in (detail.error or "").lower()

        _run(run())

    def test_list_tasks_includes_active_and_recent_done(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=5)

            async def worker(task):
                return "done"

            task = manager.create_task("One", "do one", worker)
            await manager.wait_for_task(task.id)

            rows = manager.list_tasks(include_recent=True)
            assert [row.id for row in rows] == [task.id]
            assert rows[0].status == TaskStatus.COMPLETED

        _run(run())

    def test_shutdown_cancels_running_tasks_cleanly(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=30)
            started = asyncio.Event()

            async def worker(task):
                started.set()
                await asyncio.sleep(30)

            task = manager.create_task("Long", "run", worker)
            await started.wait()
            await manager.shutdown()

            detail = manager.get_task(task.id)
            assert detail is not None
            assert detail.status == TaskStatus.CANCELLED

        _run(run())
