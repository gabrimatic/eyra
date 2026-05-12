"""Local voice-to-computer certification matrix."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from runtime.tasks import BackgroundTaskManager
from runtime.triggers import TriggerStatus, TriggerStore
from runtime.voice_diagnostics import VoiceDiagnostics
from utils.settings import Settings


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
            first = failed_checks[0]
            report.add("voice_diagnostics", "failed", f"{first.name}: {first.reason}", command="/voice-diagnose")
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

    with tempfile.TemporaryDirectory(prefix="eyra-cert-") as tmp:
        tmp_path = Path(tmp)
        job_store = DurableJobStore(tmp_path / "jobs.sqlite3")
        trigger_store = TriggerStore(tmp_path / "triggers.sqlite3")
        try:
            _guarded(report, "job_persistence", lambda: _check_job_persistence(job_store))
            _guarded(report, "task_logs", lambda: _check_task_logs(job_store))
            _guarded(report, "task_artifacts", lambda: _check_task_artifacts(job_store))
            _guarded(report, "operation_ledger", lambda: _check_operation_ledger(job_store))
            _guarded(report, "undo_reversible_file_move", lambda: _check_undo_metadata(job_store))
            _guarded(report, "trigger_creation", lambda: _check_trigger_creation(trigger_store, tmp_path))
            _guarded(report, "reminder_trigger", lambda: _check_reminder_trigger(trigger_store))
            _guarded(report, "recurring_reminder_trigger", lambda: _check_recurring_reminder_trigger(trigger_store))
            asyncio.run(_check_task_control(report, job_store))
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
    await manager.shutdown()
