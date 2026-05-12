"""Tests for task-oriented command and natural-language handling."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from runtime.live_session import LiveSession
from runtime.models import LiveRuntimeState, PreflightResult
from runtime.tasks import TaskStatus
from runtime.triggers import TriggerStatus
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


def _session() -> LiveSession:
    temp_root = Path(tempfile.mkdtemp(prefix="eyra-test-session-"))
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(temp_root / "eyra-jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(temp_root / "eyra-triggers.sqlite3"),
    )
    preflight = PreflightResult(backend_reachable=True, models_ready=[settings.MODEL])
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    session = LiveSession(settings, preflight, state, ComplexityScorer())
    session.speech = MagicMock()
    session.speech.interrupt = AsyncMock()
    session.speech.speak = AsyncMock()
    session.speech.wait_for_speech = AsyncMock()
    session.speech.cancel_listen = MagicMock()
    return session


def _session_with_fs(tmp_path: Path, *, model_tools: bool = True, agent_tools: bool = False) -> LiveSession:
    desktop = tmp_path / "Desktop"
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    pictures = tmp_path / "Pictures"
    movies = tmp_path / "Movies"
    music = tmp_path / "Music"
    for folder in (desktop, downloads, documents, pictures, movies, music):
        folder.mkdir()
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        FILESYSTEM_ALLOWED_PATHS=",".join(
            str(p) for p in (desktop, downloads, documents, pictures, movies, music, tmp_path)
        ),
        FILESYSTEM_DEFAULT_PATH=str(documents),
        JOB_STORE_PATH=str(tmp_path / "eyra-jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "eyra-triggers.sqlite3"),
        TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
        TRIGGER_TIMEOUT_SECONDS=2,
        AGENT_TOOLS_ENABLED=agent_tools,
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

    def test_tasks_clear_completed_removes_recent_terminal_rows(self, capsys):
        async def run():
            session = _session()

            async def worker(task):
                return "done"

            task = session.task_manager.create_task("Done task", "do it", worker)
            await session.task_manager.wait_for_task(task.id)
            await session._handle_command("/tasks clear-completed")
            return session, task

        session, task = _run(run())

        assert session.task_manager.list_tasks(include_recent=True) == []
        assert session.job_store.get_job(task.id) is None
        assert "Cleared 1 completed job" in capsys.readouterr().out

    def test_task_command_shows_unknown_id_cleanly(self, capsys):
        session = _session()
        handled = _run(session._handle_command("/task missing"))
        assert handled is True
        assert "No task found" in capsys.readouterr().out

    def test_task_command_can_show_persisted_job_logs_and_artifacts(self, capsys):
        session = _session()
        job = session.job_store.create_job(
            title="Durable job",
            original_user_input="Do durable work",
            source_frontend="terminal",
        )
        session.job_store.update_job(job.id, artifacts=[{"type": "file", "path": "/tmp/result.txt"}])
        session.job_store.record_log(job.id, "Started durable work.")

        _run(session._handle_command(f"/task logs {job.id}"))
        _run(session._handle_command(f"/task artifacts {job.id}"))

        out = capsys.readouterr().out
        assert "Job logs" in out
        assert "Started durable work." in out
        assert "Job artifacts" in out
        assert "/tmp/result.txt" in out

    def test_task_retry_replays_failed_deterministic_job(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "retry-me.txt"
        destination = tmp_path / "Documents" / "retry-me.txt"

        _run(session._handle_user_input("Move retry-me.txt from my Desktop to Documents."))
        failed_job = session.job_store.list_jobs()[0]
        source.write_text("retry")

        _run(session._handle_command(f"/task retry {failed_job.id}"))

        out = capsys.readouterr().out
        assert "Retrying job" in out
        assert "Moved:" in out
        assert not source.exists()
        assert destination.read_text() == "retry"

    def test_show_status_is_handled_as_voice_phrase(self, capsys):
        session = _session()

        _run(session._handle_user_input("Show status."))

        out = capsys.readouterr().out
        assert "Status" in out
        assert "This is a mock response" not in out

    def test_stop_interrupts_speech_without_model_routing(self, capsys):
        session = _session()

        _run(session._handle_user_input("Stop."))

        session.speech.interrupt.assert_awaited_once()
        out = capsys.readouterr().out
        assert "Stopped speech" in out
        assert "This is a mock response" not in out

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

    def test_pause_and_resume_commands_control_queued_task(self, capsys):
        async def run():
            session = _session()
            blocker_started = asyncio.Event()
            release_blocker = asyncio.Event()
            second_started = asyncio.Event()

            async def blocker(task):
                blocker_started.set()
                await release_blocker.wait()

            async def worker(task):
                second_started.set()
                return "done"

            blocker_task = session.task_manager.create_task("Blocker", "wait", blocker)
            await blocker_started.wait()
            task = session.task_manager.create_task("Second", "run later", worker)

            pause_handled = await session._handle_command(f"/pause {task.id}")
            release_blocker.set()
            await session.task_manager.wait_for_task(blocker_task.id)
            await asyncio.sleep(0.05)
            resume_handled = await session._handle_command(f"/resume {task.id}")
            await session.task_manager.wait_for_task(task.id)
            return pause_handled, resume_handled, task, second_started.is_set()

        pause_handled, resume_handled, task, started = _run(run())
        out = capsys.readouterr().out
        assert pause_handled is True
        assert resume_handled is True
        assert "Paused" in out
        assert "Resumed" in out
        assert started is True
        assert task.status == TaskStatus.COMPLETED

    def test_natural_pause_that_pauses_latest_queued_task(self, capsys):
        async def run():
            session = _session()
            blocker_started = asyncio.Event()
            release_blocker = asyncio.Event()

            async def blocker(task):
                blocker_started.set()
                await release_blocker.wait()

            async def worker(task):
                return "done"

            blocker_task = session.task_manager.create_task("Blocker", "wait", blocker)
            await blocker_started.wait()
            task = session.task_manager.create_task("Second", "run later", worker)
            await session._handle_user_input("pause that")
            release_blocker.set()
            await session.task_manager.wait_for_task(blocker_task.id)
            await session.task_manager.shutdown()
            return task

        task = _run(run())
        assert task.status == TaskStatus.CANCELLED
        assert "Paused" in capsys.readouterr().out

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

    def test_network_disabled_bare_domain_request_is_refused_without_task(self, capsys):
        async def run():
            session = _session()
            await session._handle_user_input("Open example.com and summarize it.")
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
        operations = session.job_store.list_operations()
        assert operations[0].normalized_action["type"] == "file.move"
        assert operations[0].undo["type"] == "file.move"

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

    def test_what_changed_reports_recent_operation_ledger(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-ledger.txt"
        source.write_text("move me")

        _run(session._handle_user_input("Move eyra-test-ledger.txt from my Desktop to Downloads."))
        _run(session._handle_user_input("What changed?"))

        out = capsys.readouterr().out
        assert "Recent changes" in out
        assert "file.move" in out
        assert "eyra-test-ledger.txt" in out

    def test_move_latest_downloaded_file_resolves_newest_download(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        old_file = tmp_path / "Downloads" / "old-download.txt"
        new_file = tmp_path / "Downloads" / "new-download.txt"
        destination = tmp_path / "Documents" / "new-download.txt"
        old_file.write_text("old")
        new_file.write_text("new")
        os.utime(old_file, (1000, 1000))
        os.utime(new_file, (2000, 2000))

        _run(session._handle_user_input("Move the latest downloaded file to Documents."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Moved:" in out
        assert "Undid file.move" in out
        assert old_file.read_text() == "old"
        assert new_file.read_text() == "new"
        assert not destination.exists()

    def test_named_media_folders_work_when_allowed_by_sandbox(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Pictures" / "photo.txt"
        destination = tmp_path / "Documents" / "photo.txt"
        source.write_text("photo")

        _run(session._handle_user_input("Move photo.txt from my Pictures to Documents."))

        out = capsys.readouterr().out
        assert "Moved:" in out
        assert not source.exists()
        assert destination.read_text() == "photo"

    def test_open_downloads_runs_safe_open_path_and_records_operation(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as create_proc:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            create_proc.return_value = proc

            _run(session._handle_user_input("Open Downloads."))

        out = capsys.readouterr().out
        assert "Opened:" in out
        assert create_proc.call_args.args[0] == "open"
        assert create_proc.call_args.args[1] == str(tmp_path / "Downloads")

        operations = session.job_store.list_operations(limit=1)
        assert operations[0].normalized_action["type"] == "file.open"
        assert operations[0].target == str(tmp_path / "Downloads")

    def test_open_downloads_does_not_hang_when_open_handoff_stalls(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)

        class StalledOpenProc:
            returncode = None

            async def communicate(self):
                await asyncio.sleep(30)

            def kill(self):
                self.returncode = -9

            async def wait(self):
                return self.returncode

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as create_proc:
            create_proc.return_value = StalledOpenProc()

            _run(session._handle_user_input("Open Downloads."))

        out = capsys.readouterr().out
        assert "Opened:" in out

    def test_undo_that_reverses_last_direct_move(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-undo-move.txt"
        destination = tmp_path / "Downloads" / "eyra-test-undo-move.txt"
        source.write_text("move me")

        _run(session._handle_user_input("Move eyra-test-undo-move.txt from my Desktop to Downloads."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Undid file.move" in out
        assert source.read_text() == "move me"
        assert not destination.exists()

    def test_undo_that_restores_last_trash_operation(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-undo-trash.txt"
        source.write_text("restore me")

        _run(session._handle_user_input("Remove eyra-test-undo-trash.txt from my Desktop."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Undid file.trash" in out
        assert source.read_text() == "restore me"

    def test_undo_that_moves_created_copy_to_trash(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-undo-copy.txt"
        destination = tmp_path / "Downloads" / "eyra-test-undo-copy.txt"
        source.write_text("copy me")

        _run(session._handle_user_input("Copy eyra-test-undo-copy.txt from my Desktop to Downloads."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Undid file.copy" in out
        assert source.read_text() == "copy me"
        assert not destination.exists()

    def test_rename_from_named_folder_records_undoable_operation(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "old-name.txt"
        renamed = tmp_path / "Desktop" / "new-name.txt"
        source.write_text("rename me")

        _run(session._handle_user_input("Rename old-name.txt in my Desktop to new-name.txt."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Renamed:" in out
        assert "Undid file.rename" in out
        assert source.read_text() == "rename me"
        assert not renamed.exists()

    def test_duplicate_from_named_folder_records_undoable_operation(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "note.txt"
        duplicate = tmp_path / "Desktop" / "note copy.txt"
        source.write_text("duplicate me")

        _run(session._handle_user_input("Duplicate note.txt from my Desktop."))
        _run(session._handle_user_input("Undo that."))

        out = capsys.readouterr().out
        assert "Duplicated:" in out
        assert "Undid file.duplicate" in out
        assert source.read_text() == "duplicate me"
        assert not duplicate.exists()

    def test_ambiguous_remove_reads_options_and_accepts_voice_choice(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        first = tmp_path / "Desktop" / "report-a.txt"
        second = tmp_path / "Desktop" / "report-b.txt"
        first.write_text("keep")
        second.write_text("trash")

        _run(session._handle_user_input("Remove report from my Desktop."))
        _run(session._handle_user_input("Read the options."))
        _run(session._handle_user_input("Choose number two."))

        out = capsys.readouterr().out
        assert "I found multiple matches" in out
        assert "1. report-a.txt" in out
        assert "2. report-b.txt" in out
        assert "Moved to Trash:" in out
        assert first.exists()
        assert not second.exists()

    def test_context_command_reports_local_context_without_model(self, capsys):
        session = _session()
        session.state.current_goal = "Finish Eyra"

        _run(session._handle_command("/context"))

        out = capsys.readouterr().out
        assert "Current context" in out
        assert "Goal: Finish Eyra" in out
        assert "This is a mock response" not in out

    def test_terminal_embedded_web_ui_uses_shared_runtime_components(self):
        session = _session()
        session.settings.WEB_UI_ENABLED = True
        fake_runtime = MagicMock(runtime_scope="shared")
        fake_handle = MagicMock()

        with patch("runtime.live_session.WebAssistantRuntime", return_value=fake_runtime) as runtime_cls:
            with patch("runtime.live_session.start_web_server_in_thread", return_value=fake_handle) as start_web:
                session._start_embedded_web_ui()

        shared = runtime_cls.call_args.kwargs["shared"]
        assert shared.conversation is session.state.conversation_messages
        assert shared.approvals is session.approvals
        assert shared.registry is session._tool_registry
        assert shared.job_store is session.job_store
        assert shared.task_manager is session.task_manager
        assert start_web.call_args.kwargs["runtime"] is fake_runtime
        assert session._web_handle is fake_handle

    def test_what_is_happening_reports_context_without_model(self, capsys):
        session = _session()

        _run(session._handle_user_input("What is happening?"))

        out = capsys.readouterr().out
        assert "Current context" in out
        assert "Recent jobs:" in out
        assert "This is a mock response" not in out

    def test_what_can_you_control_is_answered_locally(self, capsys):
        session = _session()

        _run(session._handle_user_input("What can you control right now?"))

        out = capsys.readouterr().out
        assert "Local-first default:" in out
        assert "Filesystem:" in out
        assert "Network tools:" in out
        assert "This is a mock response" not in out

    def test_voice_approve_that_approves_single_pending_action(self, capsys):
        session = _session()
        approval = session.approvals.request("run_command", "shell command", {"command": "echo hi"})

        _run(session._handle_user_input("Approve that."))

        assert session.approvals.get(approval.id).approved is True
        assert "Approved" in capsys.readouterr().out

    def test_voice_yes_approves_single_pending_action(self, capsys):
        session = _session()
        approval = session.approvals.request("run_command", "shell command", {"command": "echo hi"})

        _run(session._handle_user_input("yes"))

        assert session.approvals.get(approval.id).approved is True
        assert "Approved" in capsys.readouterr().out

    def test_voice_reject_that_rejects_single_pending_action(self, capsys):
        session = _session()
        approval = session.approvals.request("run_command", "shell command", {"command": "echo hi"})

        _run(session._handle_user_input("Reject that."))

        assert session.approvals.get(approval.id).rejected is True
        assert "Rejected" in capsys.readouterr().out

    def test_voice_approval_asks_when_multiple_pending_actions(self, capsys):
        session = _session()
        first = session.approvals.request("run_command", "shell command", {"command": "echo one"})
        second = session.approvals.request("run_command", "shell command", {"command": "echo two"})

        _run(session._handle_user_input("Approve that."))

        assert session.approvals.get(first.id).approved is False
        assert session.approvals.get(second.id).approved is False
        out = capsys.readouterr().out
        assert "Multiple pending approvals" in out
        assert first.id in out
        assert second.id in out

    def test_direct_remove_file_moves_to_trash_and_records_restore_metadata(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "eyra-test-remove.txt"
        source.write_text("remove me")

        _run(session._handle_user_input("Remove eyra-test-remove.txt from my Desktop."))

        out = capsys.readouterr().out
        operations = session.job_store.list_operations()
        assert "Moved to Trash" in out
        assert not source.exists()
        assert operations[0].normalized_action["type"] == "file.trash"
        assert operations[0].undo["type"] == "file.restore_from_trash"

    def test_file_appears_trigger_moves_file_when_created(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path)
            source = tmp_path / "Downloads" / "eyra-trigger.txt"
            destination = tmp_path / "Documents" / "eyra-trigger.txt"

            await session._handle_user_input("When eyra-trigger.txt appears in my Downloads, move it to Documents.")
            task = session.task_manager.list_tasks(include_recent=True)[0]
            source.write_text("trigger me")
            await session.task_manager.wait_for_task(task.id)
            return session, source, destination, task

        session, source, destination, task = _run(run())
        out = capsys.readouterr().out

        assert "Trigger" in out
        assert task.status == TaskStatus.COMPLETED
        assert not source.exists()
        assert destination.read_text() == "trigger me"
        assert session.trigger_store.list_triggers()[0].status.value == "completed"

    def test_file_appears_trigger_waits_while_source_is_missing(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path)
            source = tmp_path / "Downloads" / "eyra-trigger-wait.txt"
            destination = tmp_path / "Documents" / "eyra-trigger-wait.txt"

            await session._handle_user_input("When eyra-trigger-wait.txt appears in my Downloads, move it to Documents.")
            task = session.task_manager.list_tasks(include_recent=True)[0]
            await asyncio.sleep(0.05)
            assert task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
            source.write_text("trigger me later")
            await session.task_manager.wait_for_task(task.id)
            return source, destination, task

        source, destination, task = _run(run())

        assert task.status == TaskStatus.COMPLETED
        assert not source.exists()
        assert destination.read_text() == "trigger me later"

    def test_triggers_command_lists_persisted_triggers(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        session.trigger_store.create_file_exists_trigger(
            title="Move download",
            source_path=str(tmp_path / "Downloads" / "a.txt"),
            action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
            original_request="When a.txt appears in Downloads, move it to Documents.",
        )

        _run(session._handle_command("/triggers"))

        out = capsys.readouterr().out
        assert "Triggers" in out
        assert "Move download" in out

    def test_trigger_command_pauses_resumes_and_cancels_trigger(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        trigger = session.trigger_store.create_file_exists_trigger(
            title="Move download",
            source_path=str(tmp_path / "Downloads" / "a.txt"),
            action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
            original_request="When a.txt appears in Downloads, move it to Documents.",
        )

        _run(session._handle_command(f"/trigger pause {trigger.id}"))
        assert session.trigger_store.get_trigger(trigger.id).status.value == "paused"

        _run(session._handle_command(f"/trigger resume {trigger.id}"))
        assert session.trigger_store.get_trigger(trigger.id).status.value == "active"

        _run(session._handle_command(f"/trigger cancel {trigger.id}"))
        assert session.trigger_store.get_trigger(trigger.id).status.value == "cancelled"
        out = capsys.readouterr().out
        assert "Paused trigger" in out
        assert "Resumed trigger" in out
        assert "Cancelled trigger" in out

    def test_paused_file_trigger_waits_until_resumed(self, tmp_path):
        async def run():
            session = _session_with_fs(tmp_path)
            source = tmp_path / "Downloads" / "eyra-paused-trigger.txt"
            destination = tmp_path / "Documents" / "eyra-paused-trigger.txt"

            await session._handle_user_input(
                "When eyra-paused-trigger.txt appears in my Downloads, move it to Documents."
            )
            trigger = session.trigger_store.list_triggers()[0]
            task = session.task_manager.list_tasks(include_recent=True)[0]
            await session._handle_command(f"/trigger pause {trigger.id}")
            source.write_text("wait")
            await asyncio.sleep(0.05)
            assert not destination.exists()

            await session._handle_command(f"/trigger resume {trigger.id}")
            await session.task_manager.wait_for_task(task.id)
            return source, destination, task

        source, destination, task = _run(run())

        assert task.status == TaskStatus.COMPLETED
        assert not source.exists()
        assert destination.read_text() == "wait"

    def test_reminder_trigger_completes_after_delay(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path)

            await session._handle_user_input("Remind me in 0.01 seconds to stretch.")
            trigger = session.trigger_store.list_triggers()[0]
            task = session.task_manager.list_tasks(include_recent=True)[0]
            await session.task_manager.wait_for_task(task.id)
            return session, trigger, task

        session, trigger, task = _run(run())
        out = capsys.readouterr().out
        restored = session.trigger_store.get_trigger(trigger.id)

        assert "Reminder" in out
        assert trigger.kind == "timer"
        assert restored.status == TriggerStatus.COMPLETED
        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == "Reminder: stretch"

    def test_cancelled_reminder_trigger_does_not_fire(self, tmp_path):
        async def run():
            session = _session_with_fs(tmp_path)

            await session._handle_user_input("Remind me in 1 second to stretch.")
            trigger = session.trigger_store.list_triggers()[0]
            task = session.task_manager.list_tasks(include_recent=True)[0]
            await session._handle_command(f"/trigger cancel {trigger.id}")
            await session.task_manager.wait_for_task(task.id)
            return session, trigger, task

        session, trigger, task = _run(run())

        assert session.trigger_store.get_trigger(trigger.id).status == TriggerStatus.CANCELLED
        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == "Reminder cancelled."

    def test_recurring_reminder_runs_until_cancelled(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path)

            await session._handle_user_input("Every 0.01 seconds remind me to stretch.")
            trigger = session.trigger_store.list_triggers()[0]
            task = session.task_manager.list_tasks(include_recent=True)[0]
            for _ in range(50):
                restored = session.trigger_store.get_trigger(trigger.id)
                if restored.condition.get("fire_count", 0) >= 2:
                    break
                await asyncio.sleep(0.01)
            await session._handle_command(f"/trigger cancel {trigger.id}")
            await session.task_manager.wait_for_task(task.id)
            return session, trigger, task

        session, trigger, task = _run(run())
        out = capsys.readouterr().out
        restored = session.trigger_store.get_trigger(trigger.id)

        assert "Recurring reminder" in out
        assert trigger.kind == "recurring_timer"
        assert restored.condition["fire_count"] >= 2
        assert restored.status == TriggerStatus.CANCELLED
        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == "Recurring reminder cancelled."

    def test_coding_job_request_is_refused_when_agent_tools_are_disabled(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path, agent_tools=False)

        _run(session._handle_user_input("Start a coding job with Codex to update the README."))

        assert session.task_manager.list_tasks(include_recent=True) == []
        assert "Agent tools are disabled" in capsys.readouterr().out

    def test_coding_job_waits_for_voice_approval_then_runs_agent(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path, agent_tools=True)
            created = {}

            class FakeProcess:
                returncode = 0

                async def communicate(self):
                    return b"coding done", b""

            async def fake_create_subprocess_exec(*argv, cwd=None, stdout=None, stderr=None):
                created["argv"] = argv
                created["cwd"] = cwd
                return FakeProcess()

            with patch("tools.operator.shutil.which", return_value="/usr/bin/codex"):
                with patch("tools.operator.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                    await session._handle_user_input("Start a coding job with Codex to update the README.")
                    task = session.task_manager.list_tasks(include_recent=True)[0]
                    for _ in range(20):
                        if session.approvals.list_pending():
                            break
                        await asyncio.sleep(0.01)
                    pending = session.approvals.list_pending()
                    await session._handle_user_input("Approve that.")
                    await session.task_manager.wait_for_task(task.id)
                    return session, task, pending, created

        session, task, pending, created = _run(run())
        out = capsys.readouterr().out

        assert pending
        assert "Approved" in out
        assert task.status == TaskStatus.COMPLETED
        assert "coding done" in task.final_result
        assert created["argv"] == ("/usr/bin/codex", "exec", "update the README")
        jobs = session.job_store.list_jobs()
        assert jobs[0].normalized_task_spec["task_type"] == "coding.agent_job"

    def test_what_is_the_coding_agent_doing_reports_coding_jobs(self, tmp_path, capsys):
        async def run():
            session = _session_with_fs(tmp_path, agent_tools=True)
            blocker = asyncio.Event()

            async def worker(task):
                await blocker.wait()

            task = session.task_manager.create_task("Coding job: update README", "Start a coding job", worker)
            await asyncio.sleep(0)
            await session._handle_user_input("What is the coding agent doing?")
            session.task_manager.cancel_task(task.id)
            await session.task_manager.wait_for_task(task.id)
            return task

        task = _run(run())
        out = capsys.readouterr().out

        assert task.id in out
        assert "Coding jobs" in out

    def test_dictation_mode_captures_text_without_model_response(self, capsys):
        session = _session()

        _run(session._handle_user_input("Start dictation."))
        _run(session._handle_user_input("This is the first sentence."))
        _run(session._handle_user_input("This is the second sentence."))
        _run(session._handle_user_input("End dictation."))

        out = capsys.readouterr().out
        assert "Dictation started" in out
        assert "Dictation ended" in out
        assert "This is the first sentence." in out
        assert "This is the second sentence." in out
        assert "This is a mock response" not in out

    def test_dictation_mode_saves_to_file_on_end(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        target = tmp_path / "Documents" / "dictation-note.txt"

        _run(session._handle_user_input("Start dictation to a file named dictation-note.txt in my Documents."))
        _run(session._handle_user_input("Line one."))
        _run(session._handle_user_input("Line two."))
        _run(session._handle_user_input("End dictation."))

        out = capsys.readouterr().out
        assert "Dictation saved" in out
        assert target.read_text() == "Line one.\nLine two."
        assert session.job_store.list_operations()[0].normalized_action["type"] == "dictation.file.write"

    def test_cancel_dictation_discards_text_and_target_file(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        target = tmp_path / "Documents" / "discarded.txt"

        _run(session._handle_user_input("Start dictation to a file named discarded.txt in my Documents."))
        _run(session._handle_user_input("Do not keep this."))
        _run(session._handle_user_input("Cancel dictation."))

        out = capsys.readouterr().out
        assert "Dictation cancelled" in out
        assert not target.exists()

    def test_literal_dictation_keeps_spelled_characters(self, capsys):
        session = _session()

        _run(session._handle_user_input("Start dictation."))
        _run(session._handle_user_input("Literal E Y R A dash four dot zero"))
        _run(session._handle_user_input("End dictation."))

        out = capsys.readouterr().out
        assert "EYRA-4.0" in out

    def test_no_i_meant_corrects_failed_direct_move_target(self, tmp_path, capsys):
        session = _session_with_fs(tmp_path)
        correct = tmp_path / "Desktop" / "correct-file.txt"
        destination = tmp_path / "Documents" / "correct-file.txt"
        correct.write_text("move me")

        _run(session._handle_user_input("Move wrong-file.txt from my Desktop to Documents."))
        _run(session._handle_user_input("No, I meant correct-file.txt."))

        out = capsys.readouterr().out
        assert "Corrected target" in out
        assert not correct.exists()
        assert destination.read_text() == "move me"

    def test_direct_move_records_observe_plan_act_verify_loop(self, tmp_path):
        session = _session_with_fs(tmp_path)
        source = tmp_path / "Desktop" / "loop-move.txt"
        destination = tmp_path / "Documents" / "loop-move.txt"
        source.write_text("move me")

        _run(session._handle_user_input("Move loop-move.txt from my Desktop to Documents."))

        operation = session.job_store.list_operations()[0]
        loop = operation.after_state["operator_loop"]
        assert loop["phase"] == "verified"
        assert loop["observation"]["source_exists"] is True
        assert loop["verification"]["passed"] is True
        assert loop["verification"]["checks"]["source_removed"] is True
        assert loop["verification"]["checks"]["destination_exists"] is True
        assert loop["recovery"]["needed"] is False
        assert destination.read_text() == "move me"

    def test_failed_direct_move_records_recovery_hint(self, tmp_path):
        session = _session_with_fs(tmp_path)

        _run(session._handle_user_input("Move missing-loop-file.txt from my Desktop to Documents."))

        operation = session.job_store.list_operations()[0]
        loop = operation.after_state["operator_loop"]
        assert loop["phase"] == "recovery"
        assert loop["verification"]["passed"] is False
        assert loop["recovery"]["needed"] is True
        assert "correct" in loop["recovery"]["next_step"].lower()
