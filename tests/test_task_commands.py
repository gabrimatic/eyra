"""Tests for task-oriented command and natural-language handling."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from runtime.live_session import LiveSession
from runtime.models import LiveRuntimeState, PreflightResult
from runtime.tasks import TaskStatus
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


def _session() -> LiveSession:
    settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
    preflight = PreflightResult(backend_reachable=True, models_ready=[settings.MODEL])
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    session = LiveSession(settings, preflight, state, ComplexityScorer())
    session.speech = MagicMock()
    session.speech.interrupt = AsyncMock()
    session.speech.speak = AsyncMock()
    session.speech.wait_for_speech = AsyncMock()
    session.speech.cancel_listen = MagicMock()
    return session


def _session_with_fs(tmp_path: Path, *, model_tools: bool = True) -> LiveSession:
    desktop = tmp_path / "Desktop"
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    for folder in (desktop, downloads, documents):
        folder.mkdir()
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        FILESYSTEM_ALLOWED_PATHS=",".join(str(p) for p in (desktop, downloads, documents, tmp_path)),
        FILESYSTEM_DEFAULT_PATH=str(documents),
    )
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=[settings.MODEL],
        tool_capable_models=[settings.MODEL] if model_tools else [],
        tool_capability_checked_models=[settings.MODEL],
    )
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    session = LiveSession(settings, preflight, state, ComplexityScorer())
    session.speech = MagicMock()
    session.speech.interrupt = AsyncMock()
    session.speech.speak = AsyncMock()
    session.speech.wait_for_speech = AsyncMock()
    session.speech.cancel_listen = MagicMock()
    session._path_in_named_folder = lambda folder, name: str((tmp_path / folder.title() / name))  # type: ignore[method-assign]
    return session


class TestTaskCommands:
    def test_tasks_command_is_handled_locally(self, capsys):
        session = _session()
        handled = _run(session._handle_command("/tasks"))
        assert handled is True
        assert "Tasks" in capsys.readouterr().out

    def test_task_command_shows_unknown_id_cleanly(self, capsys):
        session = _session()
        handled = _run(session._handle_command("/task missing"))
        assert handled is True
        assert "No task found" in capsys.readouterr().out

    def test_cancel_all_command_cancels_running_tasks(self, capsys):
        async def run():
            session = _session()
            started = asyncio.Event()

            async def worker(task):
                started.set()
                await asyncio.sleep(30)

            task = session.task_manager.create_task("Long", "run", worker)
            await started.wait()
            handled = await session._handle_command("/cancel all")
            await session.task_manager.wait_for_task(task.id)
            return handled, task

        handled, task = _run(run())
        assert handled is True
        assert "Cancelled" in capsys.readouterr().out
        assert task.status == TaskStatus.CANCELLED

    def test_cancel_command_cancels_by_id(self, capsys):
        async def run():
            session = _session()
            started = asyncio.Event()

            async def worker(task):
                started.set()
                await asyncio.sleep(30)

            task = session.task_manager.create_task("Long", "run", worker)
            await started.wait()
            handled = await session._handle_command(f"/cancel {task.id}")
            await session.task_manager.wait_for_task(task.id)
            return handled, task

        handled, task = _run(run())
        assert handled is True
        assert "Cancelled" in capsys.readouterr().out
        assert task.status == TaskStatus.CANCELLED

    def test_user_input_returns_while_background_task_runs(self):
        async def run():
            session = _session()

            async def slow_worker(*args, **kwargs):
                await asyncio.sleep(0.2)

            session._run_worker_task = slow_worker
            await asyncio.wait_for(
                session._handle_user_input("Read this PDF and summarize it"),
                timeout=0.05,
            )
            active = session.task_manager.list_tasks(include_recent=True)
            await session.task_manager.shutdown()
            return active

        active = _run(run())
        assert len(active) == 1
        assert active[0].status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLED}

    def test_natural_cancel_that_cancels_recent_task(self, capsys):
        async def run():
            session = _session()
            started = asyncio.Event()

            async def worker(task):
                started.set()
                await asyncio.sleep(30)

            task = session.task_manager.create_task("Long", "run", worker)
            await started.wait()
            await session._handle_user_input("cancel that")
            await session.task_manager.wait_for_task(task.id)
            return task

        task = _run(run())
        assert task.status == TaskStatus.CANCELLED
        assert "Cancelled" in capsys.readouterr().out

    def test_network_disabled_website_request_is_refused_without_task(self, capsys):
        async def run():
            session = _session()
            await session._handle_user_input("Summarize this website: https://example.com")
            return session.task_manager.list_tasks(include_recent=True)

        tasks = _run(run())
        assert tasks == []
        out = capsys.readouterr().out
        assert "Network tools are disabled" in out

    def test_direct_move_file_uses_filesystem_tool(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-move.txt"
        destination = tmp_path / "Downloads" / "eyra-test-move.txt"
        source.write_text("move me")

        _run(session._handle_user_input("Move eyra-test-move.txt from my Desktop to Downloads."))

        assert not source.exists()
        assert destination.read_text() == "move me"
        assert "Moved:" in capsys.readouterr().out

    def test_direct_move_file_does_not_require_model_native_tools(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path, model_tools=False)
        source = tmp_path / "Desktop" / "eyra-test-move.txt"
        destination = tmp_path / "Downloads" / "eyra-test-move.txt"
        source.write_text("move me")

        _run(session._handle_user_input("Move eyra-test-move.txt from my Desktop to Downloads."))

        assert not source.exists()
        assert destination.read_text() == "move me"
        assert "Moved:" in capsys.readouterr().out

    def test_open_ended_tool_task_without_native_tools_fails_clearly(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path, model_tools=False)
            await session._handle_user_input("Organize my Downloads folder.")
            await asyncio.sleep(0)
            return session.task_manager.list_tasks(include_recent=True)

        tasks = _run(run())
        assert tasks == []
        out = capsys.readouterr().out
        assert "requires a model with native tool calling" in out

    def test_direct_create_file_protects_overwrite_then_allows_explicit_followup(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        target = tmp_path / "Downloads" / "eyra-overwrite-test.txt"
        target.write_text("original")

        _run(
            session._handle_user_input(
                "Create a file named eyra-overwrite-test.txt in my Downloads with the content: replacement"
            )
        )

        assert target.read_text() == "original"
        assert "File already exists" in capsys.readouterr().out

        _run(session._handle_user_input("Overwrite it."))

        assert target.read_text() == "replacement"
        assert "Updated:" in capsys.readouterr().out

    def test_direct_read_outside_sandbox_is_refused(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)

        _run(session._handle_user_input("Read /etc/passwd"))

        out = capsys.readouterr().out
        assert "Access denied" in out
        assert "root:" not in out
