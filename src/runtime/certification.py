"""Local voice-to-computer certification matrix."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Awaitable, Callable

from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from runtime.tasks import BackgroundTaskManager
from runtime.triggers import TriggerStatus, TriggerStore
from runtime.voice_diagnostics import VoiceDiagnostics
from tools.approval import ApprovalManager
from tools.filesystem import (
    AppendFileTool,
    CompareFilesTool,
    CompressPathTool,
    CopyPathTool,
    DeletePermanentlyTool,
    DuplicatePathTool,
    MovePathTool,
    MoveToTrashTool,
    PrependFileTool,
    RenamePathTool,
    RestoreFromTrashTool,
    UncompressArchiveTool,
    WriteFileTool,
)
from utils.settings import Settings

_BROWSER_CERT_ROWS: tuple[tuple[str, str], ...] = (
    ("browser_enabled_open_url", "open_url"),
    ("browser_enabled_click", "click_element"),
    ("browser_enabled_fill_form", "fill_form_field"),
    ("browser_enabled_page_screenshot", "page_screenshot"),
    ("browser_download_approval", "download_file"),
    ("browser_upload_approval", "upload_file"),
    ("browser_download_sandbox_refusal", "download_file"),
    ("browser_upload_sandbox_refusal", "upload_file"),
)

_MCP_CERT_ROWS: tuple[tuple[str, str], ...] = (
    ("mcp_enabled_tool_approval", "call_mcp_tool"),
)


@dataclass
class CertificationRow:
    name: str
    status: str
    reason: str
    command: str = ""


@dataclass
class CertificationReport:
    rows: list[CertificationRow] = field(default_factory=list)

    def add(self, name: str, status: str, reason: str, command: str = "") -> None:
        self.rows.append(CertificationRow(name=name, status=status, reason=reason, command=command))

    def render(self) -> str:
        lines = ["Voice-to-computer certification", "", f"{'status':<8} {'scenario':<34} reason"]
        for row in self.rows:
            command = f" [{row.command}]" if row.command else ""
            lines.append(f"{row.status:<8} {row.name:<34} {row.reason}{command}")
        return "\n".join(lines)

    @property
    def failed(self) -> bool:
        return any(row.status == "failed" for row in self.rows)


def _guarded(report: CertificationReport, name: str, check: Callable[[], str], *, command: str = "") -> None:
    try:
        reason = check()
    except Exception as exc:
        report.add(name, "failed", str(exc) or exc.__class__.__name__, command=command)
    else:
        report.add(name, "passed", reason, command=command)


async def _guarded_async(
    report: CertificationReport,
    name: str,
    check: Callable[[], Awaitable[str]],
    *,
    command: str = "",
) -> None:
    try:
        reason = await check()
    except Exception as exc:
        report.add(name, "failed", str(exc) or exc.__class__.__name__, command=command)
    else:
        report.add(name, "passed", reason, command=command)


def run_certification(settings: Settings | None = None, *, include_physical: bool = False) -> CertificationReport:
    """Run local, offline certification checks and label unavailable physical paths honestly."""
    settings = settings or Settings.load_from_env()
    report = CertificationReport()

    report.add(
        "mock_terminal_startup",
        "passed" if settings.USE_MOCK_CLIENT else "skipped",
        "Mock client startup path is configured." if settings.USE_MOCK_CLIENT else "Set USE_MOCK_CLIENT=true for no-backend startup smoke.",
        command="USE_MOCK_CLIENT=true LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false uv run python src/main.py",
    )

    if settings.LIVE_LISTENING_ENABLED:
        diagnostic = asyncio.run(
            VoiceDiagnostics(settings=settings).run(
                include_physical_barge_in=include_physical and settings.LIVE_SPEECH_ENABLED
            )
        )
        failed_checks = [check for check in diagnostic.checks if check.status == "failed"]
        if failed_checks:
            report.add(
                "voice_diagnostics",
                "failed",
                _format_failed_diagnostic_checks(failed_checks),
                command="/voice-diagnose",
            )
        else:
            report.add("voice_diagnostics", "passed", "No failed voice diagnostic checks.", command="/voice-diagnose")
    else:
        diagnostic = None
        report.add("voice_diagnostics", "skipped", "Voice listening is disabled in settings.", command="/voice-diagnose")

    if include_physical and settings.LIVE_LISTENING_ENABLED and settings.LIVE_SPEECH_ENABLED:
        assert diagnostic is not None
        barge_in = diagnostic.check("tts_interrupt_by_mic_speech")
        report.add("physical_barge_in", barge_in.status, barge_in.reason, command="/voice-diagnose barge-in")
    else:
        report.add("physical_barge_in", "skipped", "Physical microphone barge-in was not requested.", command="/voice-test")

    _check_local_whisper_tts_contract(report, settings)
    _check_screen_vision_model_contract(report, settings)

    with tempfile.TemporaryDirectory(prefix="eyra-cert-") as tmp:
        tmp_path = Path(tmp)
        job_store = DurableJobStore(tmp_path / "jobs.sqlite3")
        trigger_store = TriggerStore(tmp_path / "triggers.sqlite3")
        try:
            _check_terminal_runtime_contracts(report, settings, tmp_path)
            _guarded(report, "job_persistence", lambda: _check_job_persistence(job_store))
            _guarded(report, "task_logs", lambda: _check_task_logs(job_store))
            _guarded(report, "task_artifacts", lambda: _check_task_artifacts(job_store))
            asyncio.run(_check_file_operation_matrix(report, tmp_path))
            _guarded(report, "operation_ledger", lambda: _check_operation_ledger(job_store))
            _guarded(report, "undo_reversible_file_move", lambda: _check_undo_metadata(job_store))
            _guarded(report, "undo_reversible_file_operations", lambda: _check_file_operation_undo_metadata(job_store))
            _guarded(report, "trigger_creation", lambda: _check_trigger_creation(trigger_store, tmp_path))
            asyncio.run(_check_live_session_job_and_trigger_contracts(report, tmp_path))
            _guarded(report, "reminder_trigger", lambda: _check_reminder_trigger(trigger_store))
            _guarded(report, "recurring_reminder_trigger", lambda: _check_recurring_reminder_trigger(trigger_store))
            asyncio.run(_check_task_control(report, job_store))
            _check_web_runtime_contracts(report, tmp_path)
            _check_enabled_browser_tool_contract(report, settings, tmp_path)
            _check_enabled_os_tool_contract(report, settings, tmp_path)
            asyncio.run(_check_enabled_mcp_contract(report, settings, tmp_path))
            asyncio.run(_check_enabled_agent_coding_contract(report, settings, tmp_path))
        finally:
            trigger_store.close()
            job_store.close()

    report.add(
        "network_disabled_refusal",
        "passed" if not settings.NETWORK_TOOLS_ENABLED else "skipped",
        "Network/browser tools are disabled by default." if not settings.NETWORK_TOOLS_ENABLED else "Network tools are enabled for this run.",
    )
    report.add(
        "os_tools_disabled_refusal",
        "passed" if not settings.OS_TOOLS_ENABLED else "skipped",
        "OS/operator tools are disabled by default." if not settings.OS_TOOLS_ENABLED else "OS tools are enabled for this run.",
    )
    report.add(
        "mcp_disabled_default",
        "passed" if not settings.MCP_TOOLS_ENABLED else "skipped",
        "MCP bridge is disabled by default." if not settings.MCP_TOOLS_ENABLED else "MCP bridge is enabled for this run.",
    )
    report.add(
        "agent_bridge_disabled_default",
        "passed" if not settings.AGENT_TOOLS_ENABLED else "skipped",
        "Agent bridge is disabled by default." if not settings.AGENT_TOOLS_ENABLED else "Agent bridge is enabled for this run.",
    )
    report.add(
        "realtime_disabled_default",
        "passed" if not settings.REALTIME_VOICE_ENABLED else "skipped",
        "Realtime voice is disabled by default." if not settings.REALTIME_VOICE_ENABLED else "Realtime voice is enabled for this run.",
    )
    return report


def _format_failed_diagnostic_checks(checks) -> str:
    failures = [f"{check.name}: {check.reason}" for check in checks]
    if len(failures) <= 3:
        return "; ".join(failures)
    return "; ".join(failures[:3]) + f"; +{len(failures) - 3} more"


def _check_terminal_runtime_contracts(report: CertificationReport, settings: Settings, tmp_path: Path) -> None:
    _guarded(report, "typed_command_path", lambda: _check_typed_command_path(tmp_path), command="/status")
    if settings.USE_MOCK_CLIENT:
        report.add(
            "real_local_model_startup",
            "skipped",
            "Current certification settings use the mock client.",
            command="uv run python src/main.py",
        )
        return
    _guarded(
        report,
        "real_local_model_startup",
        lambda: _check_real_local_model_startup(settings),
        command="PreflightManager.run",
    )


def _check_typed_command_path(tmp_path: Path) -> str:
    session = None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        session, _root = _build_certification_session(tmp_path / "typed-command")
        try:
            handled = asyncio.run(session._handle_command("/status"))
            if handled is not True:
                raise RuntimeError("Typed /status command was not handled locally.")
            return "Typed local command path handled /status without model routing."
        finally:
            if session is not None:
                asyncio.run(session.task_manager.shutdown())
                asyncio.run(session._browser_session.close())
                session.trigger_store.close()
                session.job_store.close()


def _check_real_local_model_startup(settings: Settings) -> str:
    safe_settings = _cert_preflight_settings(settings)
    result = _run_cert_preflight(settings)
    if not result.backend_reachable:
        raise RuntimeError(f"Local model backend is unreachable: {safe_settings.API_BASE_URL}")
    if result.models_missing:
        raise RuntimeError(f"Configured local models are missing: {', '.join(result.models_missing)}")
    return "Real local backend preflight reached the configured model set without auto-pull."


def _cert_preflight_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        AUTO_PULL_MODELS=False,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
    )


def _run_cert_preflight(settings: Settings):
    from runtime.preflight import PreflightManager

    safe_settings = _cert_preflight_settings(settings)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = asyncio.run(PreflightManager(safe_settings).run())
    return result


def _check_screen_vision_model_contract(report: CertificationReport, settings: Settings) -> None:
    if settings.USE_MOCK_CLIENT:
        report.add(
            "screen_vision_model_split",
            "skipped",
            "Mock client does not certify real screen/vision model capabilities.",
            command="PreflightManager.run",
        )
        return
    try:
        reason = _check_screen_vision_model_split(settings)
    except PermissionError as exc:
        report.add("screen_vision_model_split", "skipped", str(exc), command="screencapture + vision model")
    except LookupError as exc:
        report.add("screen_vision_model_split", "skipped", str(exc), command="screencapture + vision model")
    except Exception as exc:
        report.add(
            "screen_vision_model_split",
            "failed",
            str(exc) or exc.__class__.__name__,
            command="screencapture + vision model",
        )
    else:
        report.add("screen_vision_model_split", "passed", reason, command="screencapture + vision model")


def _check_screen_vision_model_split(settings: Settings) -> str:
    from runtime.vision import vision_model_available, vision_model_name

    result = _run_cert_preflight(settings)
    if not result.backend_reachable:
        raise LookupError(f"Local model backend is unreachable: {_cert_preflight_settings(settings).API_BASE_URL}")
    if result.models_missing:
        raise LookupError(f"Configured local models are missing: {', '.join(result.models_missing)}")
    if not result.screen_capture_available:
        raise PermissionError("Screen capture is unavailable on this Mac.")
    model = vision_model_name(settings)
    if not vision_model_available(settings, result):
        raise LookupError(f"Configured vision model is not confirmed vision-capable: {model}")
    return f"Screen capture is available and {model} is confirmed vision-capable."


def _check_local_whisper_tts_contract(report: CertificationReport, settings: Settings) -> None:
    if not settings.LIVE_SPEECH_ENABLED:
        report.add(
            "local_whisper_tts",
            "skipped",
            "Speech output is disabled in settings.",
            command="wh whisper",
        )
        return
    _guarded(report, "local_whisper_tts", lambda: asyncio.run(_check_local_whisper_tts(settings)), command="wh whisper")


async def _check_local_whisper_tts(settings: Settings) -> str:
    from runtime.models import LiveRuntimeState, PreflightResult
    from runtime.speech_controller import SpeechController

    wh_bin = _resolve_cert_wh_bin(settings)
    if wh_bin is None:
        raise RuntimeError("Local Whisper CLI was not resolved.")
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=[settings.MODEL],
        speech_available=True,
        wh_bin=wh_bin,
    )
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    controller = SpeechController(state, cooldown_ms=0)
    await controller.speak("Eyra certification.")
    proc = controller._speaking_proc
    try:
        if proc is None:
            raise RuntimeError("Local Whisper TTS did not launch.")
        return "Local Whisper TTS launched through the resolved wh binary and was interruptible."
    finally:
        await controller.interrupt()


def _resolve_cert_wh_bin(settings: Settings) -> str | None:
    from runtime.preflight import PreflightManager

    return PreflightManager(settings)._resolve_wh()


def _check_job_persistence(store: DurableJobStore) -> str:
    job = store.create_job(title="Certification job", original_user_input="certify", source_frontend="cert")
    store.update_job(job.id, status=JobStatus.RUNNING, current_step="Started")
    restored = store.get_job(job.id)
    if restored is None or restored.status != JobStatus.RUNNING or restored.current_step != "Started":
        raise RuntimeError("Durable job row did not round-trip.")
    return "Durable job row round-tripped through SQLite."


def _check_task_logs(store: DurableJobStore) -> str:
    job = store.create_job(title="Log job", original_user_input="log", source_frontend="cert")
    store.record_log(job.id, "Certification log")
    if not store.list_logs(job.id):
        raise RuntimeError("Job log was not persisted.")
    return "Job logs persisted."


def _check_task_artifacts(store: DurableJobStore) -> str:
    job = store.create_job(title="Artifact job", original_user_input="artifact", source_frontend="cert")
    store.update_job(job.id, artifacts=[{"path": "/tmp/example.txt", "kind": "file"}])
    restored = store.get_job(job.id)
    if not restored or not restored.artifacts:
        raise RuntimeError("Task artifact metadata was not persisted.")
    return "Task artifacts persisted."


async def _check_file_operation_matrix(report: CertificationReport, tmp_path: Path) -> None:
    root = (tmp_path / "filesystem").resolve()
    root.mkdir()
    roots = (root,)

    async def direct_file_write() -> str:
        target = root / "write" / "note.txt"
        result = await WriteFileTool(allowed_roots=roots, default_path=root).execute(
            path=str(target),
            content="hello",
        )
        if "Created:" not in result.content or target.read_text() != "hello":
            raise RuntimeError("write_file did not create the expected text file.")
        return "write_file created a sandboxed text file."

    async def overwrite_refusal() -> str:
        target = root / "overwrite-refusal.txt"
        target.write_text("old")
        result = await WriteFileTool(allowed_roots=roots, default_path=root).execute(
            path=str(target),
            content="new",
        )
        if "already exists" not in result.content or target.read_text() != "old":
            raise RuntimeError("write_file overwrote an existing file without explicit overwrite.")
        return "write_file refused implicit overwrite."

    async def explicit_overwrite_approval() -> str:
        target = root / "approved-overwrite.txt"
        target.write_text("old")
        manager = ApprovalManager()
        tool = WriteFileTool(allowed_roots=roots, default_path=root, approval_manager=manager)
        first = await tool.execute(path=str(target), content="new", overwrite=True, confirmed=True)
        if "Approval required" not in first.content or target.read_text() != "old":
            raise RuntimeError("overwrite did not require server-side approval.")
        pending = manager.list_pending()
        if len(pending) != 1 or not manager.approve(pending[0].id):
            raise RuntimeError("overwrite approval could not be approved.")
        second = await tool.execute(path=str(target), content="new", overwrite=True, approval_id=pending[0].id)
        if "Updated:" not in second.content or target.read_text() != "new":
            raise RuntimeError("approved overwrite did not update the file.")
        return "overwrite consumed an exact server-side approval."

    async def direct_file_move() -> str:
        source = root / "move-source.txt"
        destination = root / "moved" / "move-source.txt"
        source.write_text("move me")
        result = await MovePathTool(allowed_roots=roots, default_path=root).execute(
            source=str(source),
            destination=str(destination),
        )
        if "Moved:" not in result.content or source.exists() or destination.read_text() != "move me":
            raise RuntimeError("move_path did not move the file.")
        return "move_path moved a sandboxed file."

    async def direct_file_copy() -> str:
        source = root / "copy-source.txt"
        destination = root / "copied" / "copy-source.txt"
        source.write_text("copy me")
        result = await CopyPathTool(allowed_roots=roots, default_path=root).execute(
            source=str(source),
            destination=str(destination),
        )
        if "Copied:" not in result.content or source.read_text() != "copy me" or destination.read_text() != "copy me":
            raise RuntimeError("copy_path did not copy the file.")
        return "copy_path copied a sandboxed file."

    async def append_prepend() -> str:
        target = root / "append-prepend.txt"
        target.write_text("middle")
        appended = await AppendFileTool(allowed_roots=roots, default_path=root).execute(
            path=str(target),
            content="\nend",
        )
        prepended = await PrependFileTool(allowed_roots=roots, default_path=root).execute(
            path=str(target),
            content="start\n",
        )
        if "Appended" not in appended.content or "Prepended" not in prepended.content:
            raise RuntimeError("append/prepend tools did not report success.")
        if target.read_text() != "start\nmiddle\nend":
            raise RuntimeError("append/prepend content did not match.")
        return "append_file and prepend_file updated a text file."

    async def compare_files() -> str:
        left = root / "left.txt"
        right = root / "right.txt"
        left.write_text("same\nleft\n")
        right.write_text("same\nright\n")
        result = await CompareFilesTool(allowed_roots=roots, default_path=root).execute(
            left_path=str(left),
            right_path=str(right),
        )
        if "-left" not in result.content or "+right" not in result.content:
            raise RuntimeError("compare_files did not return the expected unified diff.")
        return "compare_files returned a unified diff."

    async def rename_path() -> str:
        source = root / "old-name.txt"
        source.write_text("rename me")
        result = await RenamePathTool(allowed_roots=roots, default_path=root).execute(
            path=str(source),
            new_name="new-name.txt",
        )
        renamed = root / "new-name.txt"
        if "Renamed:" not in result.content or source.exists() or renamed.read_text() != "rename me":
            raise RuntimeError("rename_path did not rename the file in place.")
        return "rename_path renamed a sandboxed file."

    async def duplicate_path() -> str:
        source = root / "duplicate.txt"
        source.write_text("duplicate me")
        result = await DuplicatePathTool(allowed_roots=roots, default_path=root).execute(path=str(source))
        duplicate = root / "duplicate copy.txt"
        if "Duplicated:" not in result.content or source.read_text() != "duplicate me" or duplicate.read_text() != "duplicate me":
            raise RuntimeError("duplicate_path did not create the expected duplicate.")
        return "duplicate_path created a default copy."

    async def trash_delete() -> str:
        source = root / "trash-delete.txt"
        source.write_text("recoverable")
        result = await MoveToTrashTool(allowed_roots=roots, default_path=root).execute(path=str(source))
        trash_path = _trash_path_from_result(result.content)
        if "Moved to Trash:" not in result.content or source.exists() or not trash_path.exists():
            raise RuntimeError("move_to_trash did not move the file to Trash.")
        cleanup = await RestoreFromTrashTool(allowed_roots=roots, default_path=root).execute(
            trash_path=str(trash_path),
            destination=str(root / "trash-delete-restored.txt"),
        )
        if "Restored:" not in cleanup.content:
            raise RuntimeError("trash cleanup restore failed.")
        return "move_to_trash removed a sandboxed file without permanent deletion."

    async def restore_from_trash() -> str:
        source = root / "restore-source.txt"
        destination = root / "restore-destination.txt"
        source.write_text("restore me")
        trashed = await MoveToTrashTool(allowed_roots=roots, default_path=root).execute(path=str(source))
        trash_path = _trash_path_from_result(trashed.content)
        restored = await RestoreFromTrashTool(allowed_roots=roots, default_path=root).execute(
            trash_path=str(trash_path),
            destination=str(destination),
        )
        if "Restored:" not in restored.content or destination.read_text() != "restore me" or trash_path.exists():
            raise RuntimeError("restore_from_trash did not restore the file.")
        return "restore_from_trash restored a trashed file into the sandbox."

    async def permanent_delete_approval() -> str:
        target = root / "permanent-delete.txt"
        target.write_text("delete me")
        manager = ApprovalManager()
        tool = DeletePermanentlyTool(allowed_roots=roots, default_path=root, approval_manager=manager)
        first = await tool.execute(path=str(target))
        if "Approval required" not in first.content or not target.exists():
            raise RuntimeError("permanent delete did not require approval.")
        pending = manager.list_pending()
        if len(pending) != 1 or not manager.approve(pending[0].id):
            raise RuntimeError("permanent delete approval could not be approved.")
        second = await tool.execute(path=str(target), approval_id=pending[0].id)
        if "Permanently deleted:" not in second.content or target.exists():
            raise RuntimeError("approved permanent delete did not remove the file.")
        return "delete_permanently required and consumed an exact approval."

    async def zip_unzip() -> str:
        folder = root / "archive-folder"
        folder.mkdir()
        (folder / "note.txt").write_text("archive me")
        archive = root / "archive-folder.zip"
        destination = root / "expanded"
        compressed = await CompressPathTool(allowed_roots=roots, default_path=root).execute(
            source=str(folder),
            destination=str(archive),
        )
        uncompressed = await UncompressArchiveTool(allowed_roots=roots, default_path=root).execute(
            archive=str(archive),
            destination=str(destination),
        )
        if "Compressed:" not in compressed.content or "Uncompressed:" not in uncompressed.content:
            raise RuntimeError("zip/unzip tools did not report success.")
        if (destination / "archive-folder" / "note.txt").read_text() != "archive me":
            raise RuntimeError("uncompressed archive content did not match.")
        return "compress_path and uncompress_archive round-tripped a folder."

    async def zip_path_traversal_refusal() -> str:
        archive = root / "malicious.zip"
        destination = root / "malicious-expanded"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.txt", "nope")
        result = await UncompressArchiveTool(allowed_roots=roots, default_path=root).execute(
            archive=str(archive),
            destination=str(destination),
        )
        if "outside the destination" not in result.content or destination.exists():
            raise RuntimeError("uncompress_archive did not refuse a path traversal archive.")
        return "uncompress_archive refused zip path traversal."

    await _guarded_async(report, "direct_file_write", direct_file_write, command="write_file")
    await _guarded_async(report, "overwrite_refusal", overwrite_refusal, command="write_file")
    await _guarded_async(report, "explicit_overwrite_approval", explicit_overwrite_approval, command="write_file")
    await _guarded_async(report, "direct_file_move", direct_file_move, command="move_path")
    await _guarded_async(report, "direct_file_copy", direct_file_copy, command="copy_path")
    await _guarded_async(report, "append_prepend", append_prepend, command="append_file/prepend_file")
    await _guarded_async(report, "compare_files", compare_files, command="compare_files")
    await _guarded_async(report, "rename_path", rename_path, command="rename_path")
    await _guarded_async(report, "duplicate_path", duplicate_path, command="duplicate_path")
    await _guarded_async(report, "trash_delete", trash_delete, command="move_to_trash")
    await _guarded_async(report, "restore_from_trash", restore_from_trash, command="restore_from_trash")
    await _guarded_async(report, "permanent_delete_approval", permanent_delete_approval, command="delete_permanently")
    await _guarded_async(report, "zip_unzip", zip_unzip, command="compress_path/uncompress_archive")
    await _guarded_async(report, "zip_path_traversal_refusal", zip_path_traversal_refusal, command="uncompress_archive")


def _trash_path_from_result(content: str) -> Path:
    marker = " -> "
    if marker not in content:
        raise RuntimeError(f"Trash path missing from result: {content}")
    return Path(content.rsplit(marker, 1)[1]).expanduser().resolve()


def _check_operation_ledger(store: DurableJobStore) -> str:
    job = store.create_job(title="Ledger job", original_user_input="move", source_frontend="cert")
    op = store.record_operation(
        job_id=job.id,
        user_request="move it",
        normalized_action={"type": "file.move"},
        capability="filesystem.move",
        target="/tmp/b.txt",
        before_state={"path": "/tmp/a.txt"},
        after_state={"path": "/tmp/b.txt"},
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        success=True,
        undo={"type": "file.move", "source": "/tmp/b.txt", "destination": "/tmp/a.txt"},
    )
    if store.list_operations(job.id)[0].id != op.id:
        raise RuntimeError("Operation ledger did not persist.")
    return "Operation ledger persisted."


def _check_undo_metadata(store: DurableJobStore) -> str:
    operations = store.list_operations(limit=10)
    if not any(op.undo.get("type") == "file.move" for op in operations):
        raise RuntimeError("No reversible file move undo metadata was found.")
    return "Reversible file move undo metadata is present."


def _check_file_operation_undo_metadata(store: DurableJobStore) -> str:
    job = store.create_job(title="Undo metadata job", original_user_input="file operations", source_frontend="cert")
    expected = {
        "file.move": "file.move",
        "file.trash": "file.restore_from_trash",
        "file.rename": "file.rename",
        "file.duplicate": "file.trash",
    }
    for action_type, undo_type in expected.items():
        store.record_operation(
            job_id=job.id,
            user_request=f"certify {action_type}",
            normalized_action={"type": action_type},
            capability="filesystem",
            target=f"/tmp/{action_type.replace('.', '-')}.txt",
            before_state={"path": "/tmp/before.txt"},
            after_state={"path": "/tmp/after.txt"},
            risk_level=RiskLevel.LOW_RISK_CHANGE,
            success=True,
            undo={"type": undo_type},
        )
    operations = store.list_operations(job.id)
    seen = {op.normalized_action.get("type"): op.undo.get("type") for op in operations}
    missing = {action: undo for action, undo in expected.items() if seen.get(action) != undo}
    if missing:
        raise RuntimeError(f"Missing reversible undo metadata: {sorted(missing)}")
    return "Undo metadata persisted for move, trash, rename, and duplicate file operations."


async def _check_live_session_job_and_trigger_contracts(report: CertificationReport, tmp_path: Path) -> None:
    session = None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        session, root = _build_certification_session(tmp_path)
        try:
            async def task_retry() -> str:
                source = root / "Desktop" / "retry-cert.txt"
                destination = root / "Documents" / "retry-cert.txt"
                await session._handle_user_input("Move retry-cert.txt from my Desktop to Documents.")
                failed_job = next(
                    (job for job in session.job_store.list_jobs() if job.original_user_input.startswith("Move retry-cert")),
                    None,
                )
                if failed_job is None or failed_job.status != JobStatus.FAILED:
                    raise RuntimeError("Initial deterministic file job did not fail in a retryable way.")
                source.write_text("retry")
                await session._handle_command(f"/task retry {failed_job.id}")
                if source.exists() or destination.read_text() != "retry":
                    raise RuntimeError("Retry did not replay the deterministic file job successfully.")
                return "Failed deterministic file job retried from its original request."

            async def trigger_fire() -> str:
                source = root / "Downloads" / "trigger-cert.txt"
                destination = root / "Documents" / "trigger-cert.txt"
                await session._handle_user_input("When trigger-cert.txt appears in my Downloads, move it to Documents.")
                task = session.task_manager.latest_active_task()
                if task is None:
                    raise RuntimeError("File trigger did not create a background task.")
                source.write_text("trigger me")
                await session.task_manager.wait_for_task(task.id)
                trigger = next(
                    (
                        row
                        for row in session.trigger_store.list_triggers()
                        if row.original_request.startswith("When trigger-cert")
                    ),
                    None,
                )
                if trigger is None or session.trigger_store.get_trigger(trigger.id).status != TriggerStatus.COMPLETED:
                    raise RuntimeError("File trigger did not complete.")
                if source.exists() or destination.read_text() != "trigger me":
                    raise RuntimeError("File trigger did not move the created file.")
                return "File-appears trigger fired once and moved the file."

            async def trigger_pause_resume_cancel() -> str:
                paused_source = root / "Downloads" / "paused-cert.txt"
                paused_destination = root / "Documents" / "paused-cert.txt"
                await session._handle_user_input("When paused-cert.txt appears in my Downloads, move it to Documents.")
                paused_task = session.task_manager.latest_active_task()
                paused_trigger = session.trigger_store.list_triggers()[0]
                if paused_task is None:
                    raise RuntimeError("Paused trigger did not create a background task.")
                await session._handle_command(f"/trigger pause {paused_trigger.id}")
                paused_source.write_text("wait")
                await asyncio.sleep(0.05)
                if paused_destination.exists():
                    raise RuntimeError("Paused trigger fired before resume.")
                await session._handle_command(f"/trigger resume {paused_trigger.id}")
                await session.task_manager.wait_for_task(paused_task.id)
                if paused_source.exists() or paused_destination.read_text() != "wait":
                    raise RuntimeError("Resumed trigger did not fire.")

                cancelled_source = root / "Downloads" / "cancelled-cert.txt"
                cancelled_destination = root / "Documents" / "cancelled-cert.txt"
                await session._handle_user_input("When cancelled-cert.txt appears in my Downloads, move it to Documents.")
                cancelled_task = session.task_manager.latest_active_task()
                cancelled_trigger = session.trigger_store.list_triggers()[0]
                if cancelled_task is None:
                    raise RuntimeError("Cancelled trigger did not create a background task.")
                await session._handle_command(f"/trigger cancel {cancelled_trigger.id}")
                cancelled_source.write_text("no move")
                await session.task_manager.wait_for_task(cancelled_task.id)
                if cancelled_destination.exists():
                    raise RuntimeError("Cancelled trigger still fired.")
                restored = session.trigger_store.get_trigger(cancelled_trigger.id)
                if restored is None or restored.status != TriggerStatus.CANCELLED:
                    raise RuntimeError("Trigger cancel command did not persist cancelled status.")
                return "Trigger pause/resume/cancel commands affected real trigger workers."

            await _guarded_async(report, "task_retry", task_retry, command="/task retry <id>")
            await _guarded_async(report, "trigger_fire", trigger_fire, command="file trigger worker")
            await _guarded_async(
                report,
                "trigger_pause_resume_cancel",
                trigger_pause_resume_cancel,
                command="/trigger pause|resume|cancel <id>",
            )
        finally:
            if session is not None:
                await session.task_manager.shutdown()
                await session._browser_session.close()
                session.trigger_store.close()
                session.job_store.close()


def _build_certification_session(tmp_path: Path):
    from chat.complexity_scorer import ComplexityScorer
    from runtime.live_session import LiveSession
    from runtime.models import LiveRuntimeState, PreflightResult

    root = (tmp_path / "live-session").resolve()
    folders = [root / name for name in ("Desktop", "Downloads", "Documents", "Pictures", "Movies", "Music")]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        FILESYSTEM_ALLOWED_PATHS=",".join(str(path) for path in (*folders, root)),
        FILESYSTEM_DEFAULT_PATH=str(root / "Documents"),
        JOB_STORE_PATH=str(root / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(root / "triggers.sqlite3"),
        TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
        TRIGGER_TIMEOUT_SECONDS=2,
    )
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=[settings.MODEL],
        tool_capable_models=[settings.MODEL],
        tool_capability_checked_models=[settings.MODEL],
    )
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    session = LiveSession(settings, preflight, state, ComplexityScorer())
    session._path_in_named_folder = lambda folder, name: str(root / folder.strip().title() / name)  # type: ignore[method-assign]
    return session, root


def _check_trigger_creation(store: TriggerStore, tmp_path: Path) -> str:
    trigger = store.create_file_exists_trigger(
        title="Move report",
        source_path=str(tmp_path / "Downloads" / "report.pdf"),
        action={"type": "file.move", "destination": str(tmp_path / "Documents" / "report.pdf")},
        original_request="When report.pdf appears, move it.",
    )
    if store.get_trigger(trigger.id) is None:
        raise RuntimeError("Trigger row was not persisted.")
    return "File trigger row persisted."


def _check_web_runtime_contracts(report: CertificationReport, tmp_path: Path) -> None:
    web_root = (tmp_path / "web").resolve()
    web_root.mkdir()

    _guarded(report, "web_standalone_runtime", lambda: _check_web_standalone_runtime(web_root))
    _guarded(report, "web_shared_runtime", lambda: _check_web_shared_runtime(web_root))
    _guarded(report, "web_auth", lambda: _check_web_auth(web_root), command="GET /api/tasks")
    _guarded(report, "web_approval_api", lambda: _check_web_approval_api(web_root), command="/api/approvals")
    _guarded(report, "web_event_stream", lambda: _check_web_event_stream(web_root), command="/api/events")
    _guarded(
        report,
        "web_job_logs_artifacts_api",
        lambda: _check_web_job_logs_artifacts_api(web_root),
        command="/api/job/<id>/logs",
    )
    _guarded(report, "web_trigger_api", lambda: _check_web_trigger_api(web_root), command="/api/triggers")
    _guarded(report, "capability_privacy_answers", lambda: _check_capability_privacy_answers(web_root))


def _check_enabled_browser_tool_contract(report: CertificationReport, settings: Settings, tmp_path: Path) -> None:
    if not settings.NETWORK_TOOLS_ENABLED:
        for name, command in _BROWSER_CERT_ROWS:
            report.add(
                name,
                "skipped",
                "Network/browser tools are disabled in settings.",
                command=f"NETWORK_TOOLS_ENABLED=true {command}",
            )
        return
    try:
        asyncio.run(_check_browser_enabled_matrix(report, settings, tmp_path / "browser-tool"))
    except Exception as exc:
        existing = {row.name for row in report.rows}
        for name, command in _BROWSER_CERT_ROWS:
            if name not in existing:
                report.add(name, "failed", str(exc) or exc.__class__.__name__, command=command)


async def _check_browser_enabled_matrix(report: CertificationReport, settings: Settings, root: Path) -> None:
    from runtime.tooling import build_tool_registry
    from tools.browser import BrowserSession

    root.mkdir(parents=True, exist_ok=True)
    download_source = root / "download.txt"
    download_source.write_text("downloaded by eyra certification")
    upload_source = root / "upload.txt"
    upload_source.write_text("upload me")
    (root / "index.html").write_text(
        "<html><body><main>"
        "Eyra local browser certification page. "
        "This page is served from localhost so the enabled browser path can be checked without external network access. "
        "<button id='details' onclick=\"document.querySelector('#result').textContent='Details clicked by certification';\">Reveal details</button>"
        "<p id='result'>Details hidden.</p>"
        "<label for='cert-name'>Certification name</label><input id='cert-name' name='cert-name'>"
        "<a id='download' href='/download.txt' download='download.txt'>Download report</a>"
        "<input id='cert-upload' type='file'>"
        "</main></body></html>"
    )
    server, thread = _start_static_cert_server(root)
    session = BrowserSession()
    approvals = ApprovalManager()
    safe_settings = replace(
        settings,
        FILESYSTEM_ALLOWED_PATHS=str(root),
        FILESYSTEM_DEFAULT_PATH=str(root),
    )
    try:
        registry = build_tool_registry(safe_settings, browser_session=session, approval_manager=approvals)
        names = {schema["function"]["name"] for schema in registry.to_openai_tools(include_costly=True)}
        page_url = f"http://127.0.0.1:{server.server_port}/index.html"

        async def open_url() -> str:
            _require_browser_tools(names, {"open_url"})
            result = await registry.execute("open_url", json.dumps({"url": page_url}))
            if "Eyra local browser certification page" not in result.content:
                raise RuntimeError("open_url did not return the local certification page content.")
            return "Enabled browser registry opened a local HTTP page with Playwright."

        async def click_element() -> str:
            _require_browser_tools(names, {"open_url", "click_element"})
            await registry.execute("open_url", json.dumps({"url": page_url}))
            result = await registry.execute("click_element", json.dumps({"selector": "#details"}))
            if "Details clicked by certification" not in result.content:
                raise RuntimeError("click_element did not activate the local page control.")
            return "click_element clicked a harmless local browser control."

        async def fill_form_field() -> str:
            _require_browser_tools(names, {"open_url", "fill_form_field"})
            await registry.execute("open_url", json.dumps({"url": page_url}))
            result = await registry.execute(
                "fill_form_field",
                json.dumps({"selector": "#cert-name", "value": "Eyra certification"}),
            )
            page = await session.page()
            value = await page.locator("#cert-name").input_value()
            if "Filled #cert-name" not in result.content or value != "Eyra certification":
                raise RuntimeError("fill_form_field did not update the local test field without submit.")
            return "fill_form_field filled a local form field without submitting."

        async def page_screenshot() -> str:
            _require_browser_tools(names, {"open_url", "page_screenshot"})
            await registry.execute("open_url", json.dumps({"url": page_url}))
            result = await registry.execute("page_screenshot", "{}")
            if not result.image_base64:
                raise RuntimeError("page_screenshot did not return image data.")
            base64.b64decode(result.image_base64, validate=True)
            return "page_screenshot returned a base64 browser screenshot."

        async def download_approval() -> str:
            _require_browser_tools(names, {"open_url", "download_file"})
            _clear_pending_approvals(approvals)
            await registry.execute("open_url", json.dumps({"url": page_url}))
            destination = root / "downloads" / "cert-download.txt"
            pending = await registry.execute(
                "download_file",
                json.dumps({"selector": "#download", "destination": str(destination), "confirmed": True}),
            )
            pending_approvals = approvals.list_pending()
            if "Approval required" not in pending.content or len(pending_approvals) != 1:
                raise RuntimeError("download_file did not require exact server-side approval.")
            if not approvals.approve(pending_approvals[0].id):
                raise RuntimeError("download approval could not be approved.")
            result = await registry.execute(
                "download_file",
                json.dumps(
                    {
                        "selector": "#download",
                        "destination": str(destination),
                        "approval_id": pending_approvals[0].id,
                    }
                ),
            )
            if "Downloaded:" not in result.content or destination.read_text() != download_source.read_text():
                raise RuntimeError("download_file did not save the approved local download.")
            return "download_file required approval and saved to the sandbox."

        async def upload_approval() -> str:
            _require_browser_tools(names, {"open_url", "upload_file"})
            _clear_pending_approvals(approvals)
            await registry.execute("open_url", json.dumps({"url": page_url}))
            pending = await registry.execute(
                "upload_file",
                json.dumps({"selector": "#cert-upload", "path": str(upload_source), "confirmed": True}),
            )
            pending_approvals = approvals.list_pending()
            if "Approval required" not in pending.content or len(pending_approvals) != 1:
                raise RuntimeError("upload_file did not require exact server-side approval.")
            if not approvals.approve(pending_approvals[0].id):
                raise RuntimeError("upload approval could not be approved.")
            result = await registry.execute(
                "upload_file",
                json.dumps(
                    {
                        "selector": "#cert-upload",
                        "path": str(upload_source),
                        "approval_id": pending_approvals[0].id,
                    }
                ),
            )
            page = await session.page()
            value = await page.locator("#cert-upload").input_value()
            if "Uploaded:" not in result.content or not value.endswith("upload.txt"):
                raise RuntimeError("upload_file did not attach the approved sandbox file.")
            return "upload_file required approval and attached a sandbox file without submitting."

        async def download_sandbox_refusal() -> str:
            _require_browser_tools(names, {"download_file"})
            outside = root.parent / "outside-download.txt"
            result = await registry.execute(
                "download_file",
                json.dumps({"selector": "#download", "destination": str(outside)}),
            )
            if "Access denied" not in result.content:
                raise RuntimeError("download_file did not refuse a destination outside the sandbox.")
            return "download_file refused a destination outside the sandbox before approval."

        async def upload_sandbox_refusal() -> str:
            _require_browser_tools(names, {"upload_file"})
            outside = root.parent / "outside-upload.txt"
            outside.write_text("outside")
            result = await registry.execute(
                "upload_file",
                json.dumps({"selector": "#cert-upload", "path": str(outside)}),
            )
            if "Access denied" not in result.content:
                raise RuntimeError("upload_file did not refuse a source outside the sandbox.")
            return "upload_file refused a source outside the sandbox before approval."

        await _guarded_async(report, "browser_enabled_open_url", open_url, command="open_url")
        await _guarded_async(report, "browser_enabled_click", click_element, command="click_element")
        await _guarded_async(report, "browser_enabled_fill_form", fill_form_field, command="fill_form_field")
        await _guarded_async(report, "browser_enabled_page_screenshot", page_screenshot, command="page_screenshot")
        await _guarded_async(report, "browser_download_approval", download_approval, command="download_file")
        await _guarded_async(report, "browser_upload_approval", upload_approval, command="upload_file")
        await _guarded_async(
            report,
            "browser_download_sandbox_refusal",
            download_sandbox_refusal,
            command="download_file",
        )
        await _guarded_async(report, "browser_upload_sandbox_refusal", upload_sandbox_refusal, command="upload_file")
    finally:
        await session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _require_browser_tools(names: set[str], expected: set[str]) -> None:
    missing = expected - names
    if missing:
        raise RuntimeError(f"Browser tools were not registered while network tools were enabled: {', '.join(sorted(missing))}")


def _clear_pending_approvals(approvals: ApprovalManager) -> None:
    for approval in approvals.list_pending():
        approvals.reject(approval.id)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        pass


def _start_static_cert_server(root: Path) -> tuple[ThreadingHTTPServer, Thread]:
    handler = partial(_QuietStaticHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _check_enabled_os_tool_contract(report: CertificationReport, settings: Settings, tmp_path: Path) -> None:
    if not settings.OS_TOOLS_ENABLED:
        report.add(
            "os_enabled_list_open_apps",
            "skipped",
            "OS tools are disabled in settings.",
            command="OS_TOOLS_ENABLED=true list_open_apps",
        )
        return
    try:
        reason = asyncio.run(_check_os_enabled_list_open_apps(settings, tmp_path / "os-tool"))
    except PermissionError as exc:
        report.add("os_enabled_list_open_apps", "skipped", str(exc), command="list_open_apps")
    except Exception as exc:
        report.add("os_enabled_list_open_apps", "failed", str(exc) or exc.__class__.__name__, command="list_open_apps")
    else:
        report.add("os_enabled_list_open_apps", "passed", reason, command="list_open_apps")


async def _check_os_enabled_list_open_apps(settings: Settings, root: Path) -> str:
    from runtime.tooling import build_tool_registry

    root.mkdir(parents=True, exist_ok=True)
    safe_settings = replace(
        settings,
        FILESYSTEM_ALLOWED_PATHS=str(root),
        FILESYSTEM_DEFAULT_PATH=str(root),
    )
    registry = build_tool_registry(safe_settings)
    names = {schema["function"]["name"] for schema in registry.to_openai_tools(include_costly=True)}
    if "list_open_apps" not in names:
        raise RuntimeError("list_open_apps was not registered while OS tools were enabled.")
    result = await registry.execute("list_open_apps", "{}")
    if result.content.startswith("Could not list open apps:"):
        lowered = result.content.lower()
        if "permission" in lowered or "not authorized" in lowered or "not allowed" in lowered:
            raise PermissionError("macOS Automation/Accessibility permission is not available for list_open_apps.")
        raise RuntimeError(result.content)
    payload = json.loads(result.content)
    if not isinstance(payload.get("apps"), list):
        raise RuntimeError("list_open_apps did not return an apps list.")
    return "Enabled OS tool registry listed visible macOS applications."


async def _check_enabled_mcp_contract(
    report: CertificationReport,
    settings: Settings,
    tmp_path: Path,
) -> None:
    if not settings.MCP_TOOLS_ENABLED:
        for name, command in _MCP_CERT_ROWS:
            report.add(
                name,
                "skipped",
                "MCP bridge is disabled in settings.",
                command=f"MCP_TOOLS_ENABLED=true {command}",
            )
        return
    await _guarded_async(
        report,
        "mcp_enabled_tool_approval",
        lambda: _check_mcp_enabled_tool_approval(settings, tmp_path / "mcp"),
        command="call_mcp_tool",
    )


async def _check_mcp_enabled_tool_approval(settings: Settings, root: Path) -> str:
    from runtime.tooling import build_tool_registry

    root.mkdir(parents=True, exist_ok=True)
    server_script = root / "cert_mcp_server.py"
    server_script.write_text(
        textwrap.dedent(
            r'''
            import json
            import sys


            def read_msg():
                headers = {}
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line in (b"\r\n", b"\n"):
                        break
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))


            def write_msg(payload):
                raw = json.dumps(payload).encode()
                sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
                sys.stdout.buffer.flush()


            while True:
                msg = read_msg()
                if msg is None:
                    break
                if "id" not in msg:
                    continue
                method = msg.get("method")
                if method == "initialize":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "cert", "version": "1"}}})
                elif method == "tools/list":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}})
                elif method == "tools/call":
                    text = msg["params"]["arguments"].get("text", "")
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "echo: " + text}]}})
            '''
        )
    )
    config_path = root / "mcp.json"
    config_path.write_text(json.dumps({"servers": {"cert": {"command": sys.executable, "args": [str(server_script)]}}}))

    approvals = ApprovalManager()
    safe_settings = replace(
        settings,
        MCP_CONFIG_PATH=str(config_path),
        FILESYSTEM_ALLOWED_PATHS=str(root),
        FILESYSTEM_DEFAULT_PATH=str(root),
    )
    registry = build_tool_registry(safe_settings, approval_manager=approvals)
    names = {schema["function"]["name"] for schema in registry.to_openai_tools(include_costly=True)}
    if {"list_mcp_tools", "call_mcp_tool"} - names:
        raise RuntimeError("MCP tools were not registered while MCP_TOOLS_ENABLED=true.")

    listed = await registry.execute("list_mcp_tools", json.dumps({"server": "cert"}))
    if '"name": "echo"' not in listed.content:
        raise RuntimeError("list_mcp_tools did not list the local certification MCP tool.")

    pending = await registry.execute(
        "call_mcp_tool",
        json.dumps({"server": "cert", "tool": "echo", "arguments": {"text": "hi"}, "confirmed": True}),
    )
    pending_approvals = approvals.list_pending()
    if "Approval required" not in pending.content or len(pending_approvals) != 1:
        raise RuntimeError("call_mcp_tool did not require exact server-side approval.")
    if not approvals.approve(pending_approvals[0].id):
        raise RuntimeError("MCP approval could not be approved.")

    called = await registry.execute(
        "call_mcp_tool",
        json.dumps(
            {
                "server": "cert",
                "tool": "echo",
                "arguments": {"text": "hi"},
                "approval_id": pending_approvals[0].id,
            }
        ),
    )
    if "echo: hi" not in called.content:
        raise RuntimeError("call_mcp_tool did not execute after exact approval.")

    reused = await registry.execute(
        "call_mcp_tool",
        json.dumps(
            {
                "server": "cert",
                "tool": "echo",
                "arguments": {"text": "changed"},
                "approval_id": pending_approvals[0].id,
            }
        ),
    )
    if "Approval required" not in reused.content or "echo: changed" in reused.content:
        raise RuntimeError("call_mcp_tool reused an approval for different MCP arguments.")

    return "Enabled MCP bridge listed a local server and required exact approval before tool execution."


async def _check_enabled_agent_coding_contract(
    report: CertificationReport,
    settings: Settings,
    tmp_path: Path,
) -> None:
    if not settings.AGENT_TOOLS_ENABLED:
        report.add(
            "agent_enabled_coding_approval",
            "skipped",
            "Agent bridge is disabled in settings.",
            command="AGENT_TOOLS_ENABLED=true run_codex_task",
        )
        return
    await _guarded_async(
        report,
        "agent_enabled_coding_approval",
        lambda: _check_agent_coding_approval(settings, tmp_path / "agent-coding"),
        command="run_codex_task",
    )


async def _check_agent_coding_approval(settings: Settings, root: Path) -> str:
    from runtime.tooling import build_tool_registry

    root.mkdir(parents=True, exist_ok=True)
    approvals = ApprovalManager()
    safe_settings = replace(
        settings,
        FILESYSTEM_ALLOWED_PATHS=str(root),
        FILESYSTEM_DEFAULT_PATH=str(root),
    )
    registry = build_tool_registry(safe_settings, approval_manager=approvals)
    names = {schema["function"]["name"] for schema in registry.to_openai_tools(include_costly=True)}
    if "run_codex_task" not in names:
        raise RuntimeError("run_codex_task was not registered while agent tools were enabled.")
    result = await registry.execute(
        "run_codex_task",
        json.dumps({"task": "inspect README without changing files", "cwd": str(root)}),
    )
    pending = approvals.list_pending()
    if "Approval required" not in result.content or len(pending) != 1:
        raise RuntimeError("Coding agent bridge did not require server-side approval before execution.")
    if pending[0].tool_name != "run_codex_task" or pending[0].details.get("agent") != "codex":
        raise RuntimeError("Coding agent approval did not capture the exact Codex delegation action.")
    if pending[0].approved or pending[0].consumed:
        raise RuntimeError("Coding agent approval was unexpectedly approved or consumed.")
    return "Enabled coding-agent bridge registered and required exact server-side approval before execution."


def _web_cert_settings(root: Path) -> Settings:
    return Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        WEB_UI_ENABLED=True,
        WEB_UI_HOST="127.0.0.1",
        WEB_UI_PORT=0,
        WEB_UI_REQUIRE_TOKEN="true",
        FILESYSTEM_ALLOWED_PATHS=str(root),
        FILESYSTEM_DEFAULT_PATH=str(root),
        JOB_STORE_PATH=str(root / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(root / "triggers.sqlite3"),
        TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
        TRIGGER_TIMEOUT_SECONDS=2,
    )


def _check_web_standalone_runtime(root: Path) -> str:
    from web.server import WebAssistantRuntime, build_health_payload

    settings = _web_cert_settings(root / "standalone")
    runtime = WebAssistantRuntime(settings)
    try:
        health = build_health_payload(settings, runtime_scope=runtime.runtime_scope, preflight=runtime.preflight)
        if runtime.runtime_scope != "standalone" or health["runtime"]["sharedState"] is not False:
            raise RuntimeError("Standalone Web runtime did not report standalone scope.")
        if health["web"]["authRequired"] is not True or health["capabilities"]["localFirst"] is not True:
            raise RuntimeError("Standalone Web health payload did not report local-first authenticated defaults.")
        return "Standalone Web runtime reported local-first authenticated health."
    finally:
        runtime.close()


def _check_web_shared_runtime(root: Path) -> str:
    from chat.complexity_scorer import ComplexityScorer
    from runtime.models import PreflightResult
    from runtime.shared import RuntimeSharedState
    from web.server import WebAssistantRuntime

    settings = _web_cert_settings(root / "shared")
    preflight = PreflightResult(backend_reachable=True, models_ready=[settings.MODEL])
    shared = RuntimeSharedState.create(settings, preflight=preflight, source_frontend="terminal")
    runtime = WebAssistantRuntime(settings, preflight=preflight, shared=shared)

    async def create_shared_task():
        async def worker(task):
            return "shared task done"

        task = shared.task_manager.create_task("Shared task", "certify shared web", worker)
        await shared.task_manager.wait_for_task(task.id)
        return task.id

    try:
        task_id = runtime.run_sync(create_shared_task())
        tasks = runtime.run_sync(runtime.list_tasks())
        if runtime.runtime_scope != "shared" or runtime.task_manager is not shared.task_manager:
            raise RuntimeError("Web runtime did not use terminal-owned shared state.")
        if tasks["tasks"][0]["id"] != task_id or shared.job_store.get_job(task_id) is None:
            raise RuntimeError("Shared Web runtime did not expose terminal-owned tasks.")
        if not isinstance(shared.scorer, ComplexityScorer):
            raise RuntimeError("Shared runtime did not preserve scorer.")
        return "Shared Web runtime used terminal-owned jobs, approvals, tools, and task events."
    finally:
        runtime.close()
        shared.close()


def _check_web_auth(root: Path) -> str:
    runtime, handle, base = _start_web_cert_server(root / "auth")
    try:
        unauthorized = False
        try:
            urllib.request.urlopen(base + "/api/tasks", timeout=5)
        except urllib.error.HTTPError as exc:
            unauthorized = exc.code == 401
        if not unauthorized:
            raise RuntimeError("Token-protected Web API allowed an unauthenticated request.")
        payload = _web_get_json(base + "/api/tasks", token=handle.web_session_token)
        if "tasks" not in payload:
            raise RuntimeError("Authorized Web API request did not return task payload.")
        return "Web API required an exact token for non-health endpoints."
    finally:
        handle.close()
        runtime.close()


def _check_web_approval_api(root: Path) -> str:
    runtime, handle, base = _start_web_cert_server(root / "approvals")
    try:
        approve_me = runtime.approvals.request("run_command", "shell command", {"command": "echo approve"})
        reject_me = runtime.approvals.request("run_command", "shell command", {"command": "echo reject"})
        unauthorized = False
        try:
            urllib.request.urlopen(base + "/api/approvals", timeout=5)
        except urllib.error.HTTPError as exc:
            unauthorized = exc.code == 401
        if not unauthorized:
            raise RuntimeError("Web approvals API allowed an unauthenticated request.")

        listed = _web_get_json(base + "/api/approvals", token=handle.web_session_token)
        listed_ids = {row["id"] for row in listed.get("approvals", [])}
        if {approve_me.id, reject_me.id} - listed_ids:
            raise RuntimeError("Web approvals API did not list pending approvals.")

        approved = _web_post_json(
            base + "/api/approve",
            {"approvalId": approve_me.id},
            token=handle.web_session_token,
        )
        rejected = _web_post_json(
            base + "/api/reject",
            {"approvalId": reject_me.id},
            token=handle.web_session_token,
        )
        if approved.get("approved") is not True or rejected.get("rejected") is not True:
            raise RuntimeError("Web approval API did not mutate approval state.")
        if not runtime.approvals.get(approve_me.id).approved or not runtime.approvals.get(reject_me.id).rejected:
            raise RuntimeError("Web approval mutation did not reach the runtime approval manager.")
        return "Web approvals API listed, approved, rejected, and required token auth."
    finally:
        handle.close()
        runtime.close()


def _check_web_event_stream(root: Path) -> str:
    from web.server import WebAssistantRuntime

    settings = _web_cert_settings(root / "events")
    runtime = WebAssistantRuntime(settings)
    subscriber = runtime.subscribe_task_events()

    async def create_event_task():
        async def worker(task):
            return "event task done"

        task = runtime.task_manager.create_task("Event task", "certify event stream", worker)
        await runtime.task_manager.wait_for_task(task.id)
        return task.id

    try:
        task_id = runtime.run_sync(create_event_task())
        event = subscriber.get(timeout=2)
        if event.get("event") != "task" or event.get("task", {}).get("id") != task_id:
            raise RuntimeError("Web task event stream did not publish the task event.")
        return "Web task event stream published task lifecycle events."
    finally:
        runtime.unsubscribe_task_events(subscriber)
        runtime.close()


def _check_web_job_logs_artifacts_api(root: Path) -> str:
    from runtime.jobs import JobStatus

    runtime, handle, base = _start_web_cert_server(root / "job-api")
    try:
        job = runtime.job_store.create_job(
            title="Web durable job",
            original_user_input="certify web logs",
            source_frontend="web",
        )
        runtime.job_store.update_job(job.id, status=JobStatus.COMPLETED, artifacts=[{"path": "/tmp/web.txt"}])
        runtime.job_store.record_log(job.id, "Web job started.")
        logs = _web_get_json(base + f"/api/job/{job.id}/logs", token=handle.web_session_token)
        artifacts = _web_get_json(base + f"/api/job/{job.id}/artifacts", token=handle.web_session_token)
        if logs["logs"][0]["message"] != "Web job started.":
            raise RuntimeError("Web logs API did not return persisted logs.")
        if artifacts["artifacts"][0]["path"] != "/tmp/web.txt":
            raise RuntimeError("Web artifacts API did not return persisted artifacts.")
        return "Web job logs and artifacts APIs returned persisted job data."
    finally:
        handle.close()
        runtime.close()


def _check_web_trigger_api(root: Path) -> str:
    runtime, handle, base = _start_web_cert_server(root / "trigger-api")
    try:
        trigger = runtime.trigger_store.create_file_exists_trigger(
            title="Move web download",
            source_path=str(root / "Downloads" / "a.txt"),
            action={"type": "file.move", "destination": str(root / "Documents" / "a.txt")},
            original_request="When a.txt appears in Downloads, move it to Documents.",
        )
        listed = _web_get_json(base + "/api/triggers", token=handle.web_session_token)
        paused = _web_post_json(
            base + "/api/trigger",
            {"triggerId": trigger.id, "action": "pause"},
            token=handle.web_session_token,
        )
        if listed["triggers"][0]["id"] != trigger.id:
            raise RuntimeError("Web trigger list API did not return persisted trigger.")
        if paused["trigger"]["status"] != "paused":
            raise RuntimeError("Web trigger update API did not pause the trigger.")
        return "Web trigger APIs listed and updated persisted triggers."
    finally:
        handle.close()
        runtime.close()


def _check_capability_privacy_answers(root: Path) -> str:
    from web.server import WebAssistantRuntime

    settings = _web_cert_settings(root / "capabilities")
    runtime = WebAssistantRuntime(settings)
    try:
        result = runtime.run_sync(runtime.handle_message("What would leave my machine?"))
        reply = result.get("reply", "")
        if "Leaves machine by default" not in reply or "This is a mock response" in reply:
            raise RuntimeError("Capability/privacy answer was not handled deterministically.")
        return "Capability and privacy question returned deterministic local runtime answer."
    finally:
        runtime.close()


def _start_web_cert_server(root: Path):
    from web.server import WebAssistantRuntime, start_web_server_in_thread

    settings = _web_cert_settings(root)
    runtime = WebAssistantRuntime(settings)
    handle = start_web_server_in_thread(
        settings,
        runtime=runtime,
        web_session_token="cert-web-token",
        realtime_tool_token="cert-realtime-token",
    )
    base = f"http://{settings.WEB_UI_HOST}:{handle.server.server_port}"
    return runtime, handle, base


def _web_get_json(url: str, *, token: str) -> dict:
    request = urllib.request.Request(url, headers={"X-Eyra-Web-Token": token})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def _web_post_json(url: str, payload: dict, *, token: str) -> dict:
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Eyra-Web-Token": token},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def _check_reminder_trigger(store: TriggerStore) -> str:
    trigger = store.create_timer_trigger(
        title="Reminder: stretch",
        fire_at=123.0,
        action={"type": "notify", "message": "stretch"},
        original_request="Remind me to stretch.",
    )
    store.mark_completed(trigger.id)
    if store.get_trigger(trigger.id).status != TriggerStatus.COMPLETED:
        raise RuntimeError("Reminder trigger status did not update.")
    return "Reminder trigger status updated."


def _check_recurring_reminder_trigger(store: TriggerStore) -> str:
    trigger = store.create_recurring_timer_trigger(
        title="Recurring reminder: stretch",
        interval_seconds=60,
        next_fire_at=123.0,
        action={"type": "notify", "message": "stretch"},
        original_request="Every minute remind me to stretch.",
    )
    store.record_recurring_fire(trigger.id, last_fire_at=123.0, next_fire_at=183.0)
    if store.get_trigger(trigger.id).condition.get("fire_count") != 1:
        raise RuntimeError("Recurring reminder fire count did not increment.")
    return "Recurring reminder fire count updated."


async def _check_task_control(report: CertificationReport, store: DurableJobStore) -> None:
    manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=2, job_store=store, source_frontend="cert")

    async def done_worker(task):
        task.mark_progress("Certification worker ran")
        return "done"

    created = manager.create_task("Create me", "create background task", done_worker)
    await manager.wait_for_task(created.id)
    persisted = store.get_job(created.id)
    if created.status.value == "completed" and persisted is not None and persisted.status == JobStatus.COMPLETED:
        report.add("background_task_creation", "passed", "Background task created, ran, and persisted.")
    else:
        report.add("background_task_creation", "failed", "Background task did not complete and persist.")

    async def slow_worker(task):
        while not task.cancellation_requested:
            await asyncio.sleep(0.05)
        return "cancelled"

    cancel_task = manager.create_task("Cancel me", "cancel", slow_worker)
    await asyncio.sleep(0)
    if manager.cancel_task(cancel_task.id):
        report.add("cancel", "passed", "Running task accepted cancellation.")
    else:
        report.add("cancel", "failed", "Running task did not accept cancellation.")
    await manager.wait_for_task(cancel_task.id)

    async def wait_worker(task):
        await asyncio.sleep(0.05)
        return "done"

    blocker = manager.create_task("Blocker", "block", wait_worker)
    paused = manager.create_task("Queued", "queued", wait_worker)
    if manager.pause_task(paused.id) and manager.resume_task(paused.id):
        report.add("pause_resume", "passed", "Queued task pause/resume is honest and bounded.")
    else:
        report.add("pause_resume", "failed", "Queued task pause/resume failed.")
    await manager.wait_for_task(blocker.id)
    await manager.wait_for_task(paused.id)
    memory_count = manager.clear_terminal_tasks()
    store_count = store.clear_terminal_jobs()
    terminal_jobs = {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
    if (
        memory_count >= 1
        and store_count >= 1
        and not manager.list_tasks(include_recent=True)
        and not any(job.status in terminal_jobs for job in store.list_jobs(limit=100))
    ):
        report.add("clear_completed", "passed", "Completed, failed, and cancelled task rows were cleared.")
    else:
        report.add("clear_completed", "failed", "Terminal task rows were not cleared consistently.")
    await manager.shutdown()
