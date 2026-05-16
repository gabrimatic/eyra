"""Tests for background task lifecycle and coordinator behavior."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.jobs import DurableJobStore
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

    def test_pause_and_resume_queued_task_before_it_starts(self):
        async def run():
            manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=5)
            blocker_started = asyncio.Event()
            release_blocker = asyncio.Event()
            second_started = asyncio.Event()

            async def blocker(task):
                blocker_started.set()
                await release_blocker.wait()
                return "first"

            async def worker(task):
                second_started.set()
                return "second"

            blocker_task = manager.create_task("Blocker", "wait", blocker)
            await blocker_started.wait()
            task = manager.create_task("Second", "run later", worker)

            assert manager.pause_task(task.id) is True
            assert task.status == TaskStatus.PAUSED
            release_blocker.set()
            await manager.wait_for_task(blocker_task.id)
            await asyncio.sleep(0.05)
            assert second_started.is_set() is False

            assert manager.resume_task(task.id) is True
            await manager.wait_for_task(task.id)
            assert second_started.is_set() is True
            assert task.status == TaskStatus.COMPLETED

        _run(run())

    def test_events_fan_out_to_multiple_listeners(self):
        async def run():
            primary_events = []
            listener_events = []
            manager = BackgroundTaskManager(on_event=lambda task, event: primary_events.append(event))
            manager.add_event_listener(lambda task, event: listener_events.append((task.id, event)))

            async def worker(task):
                return "ok"

            task = manager.create_task("Shared event task", "run shared event task", worker)
            await manager.wait_for_task(task.id)
            await manager.shutdown()
            return task, primary_events, listener_events

        task, primary_events, listener_events = _run(run())

        assert "accepted" in primary_events
        assert "completed" in primary_events
        assert (task.id, "accepted") in listener_events
        assert (task.id, "completed") in listener_events

    def test_persisted_related_context_uses_semantic_history(self, tmp_path):
        async def run():
            store = DurableJobStore(tmp_path / "jobs.sqlite3")
            manager = BackgroundTaskManager(job_store=store)

            async def worker(task):
                return "ok"

            task = manager.create_task(
                "Safe summary",
                "summarize this",
                worker,
                related_context=[
                    {"role": "user", "content": "Read /Users/example/private.txt with token=secret-token"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "/Users/example/private.txt", "token": "secret-token"}',
                                },
                            }
                        ],
                    },
                ],
            )
            await manager.wait_for_task(task.id)
            persisted = store.get_job(task.id)
            store.close()
            return persisted

        persisted = _run(run())
        assert persisted is not None
        rendered = str(persisted.normalized_task_spec["related_context"])
        assert "read_file" in rendered
        assert "arguments" not in rendered
        assert "/Users/example" not in rendered
        assert "secret-token" not in rendered
        assert "~/[user]" in rendered
        assert "[REDACTED]" in rendered

    def test_persisted_related_context_keeps_semantic_entries_sanitized(self, tmp_path):
        async def run():
            store = DurableJobStore(tmp_path / "jobs.sqlite3")
            manager = BackgroundTaskManager(job_store=store)

            async def worker(task):
                return "ok"

            task = manager.create_task(
                "Already semantic",
                "continue the safe task",
                worker,
                related_context=[
                    {
                        "role": "assistant",
                        "content": "Used read_file on /Users/example/private.txt with token=secret-token",
                        "privacy": {"localOnly": True, "leavesMachine": False, "dataClasses": ["text"]},
                        "toolCalls": [{"name": "read_file", "arguments": {"path": "/Users/example/private.txt"}}],
                        "metadata": {"toolCallId": "call_1"},
                    }
                ],
            )
            await manager.wait_for_task(task.id)
            persisted = store.get_job(task.id)
            store.close()
            return persisted

        persisted = _run(run())
        assert persisted is not None
        entries = persisted.normalized_task_spec["related_context"]
        rendered = str(entries)

        assert entries[0]["role"] == "assistant"
        assert "privacy" in entries[0]
        assert entries[0]["toolCalls"] == [{"name": "read_file"}]
        assert "metadata" not in entries[0]
        assert "arguments" not in rendered
        assert "/Users/example" not in rendered
        assert "secret-token" not in rendered
        assert "~/[user]" in rendered
        assert "[REDACTED]" in rendered
