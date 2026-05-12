"""Tests for durable local jobs and operation ledger behavior."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from runtime.tasks import BackgroundTaskManager, TaskStatus
from utils.settings import Settings


def test_job_store_persists_job_details_across_restarts(tmp_path):
    db_path = tmp_path / "eyra-jobs.sqlite3"
    first = DurableJobStore(db_path)
    job = first.create_job(
        title="Move selected PDF",
        original_user_input="Move the selected PDF to Downloads.",
        source_frontend="voice",
        normalized_task_spec={"task_type": "file.move", "target_refs": ["selected Finder item"]},
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        current_plan=["Resolve selected file", "Move to Downloads", "Verify destination"],
        required_capabilities=["filesystem", "finder_selection"],
    )
    first.record_log(job.id, "Resolved selected Finder item.")
    first.update_job(job.id, status=JobStatus.RUNNING, current_step="Move to Downloads")
    first.close()

    second = DurableJobStore(db_path)
    restored = second.get_job(job.id)

    assert restored is not None
    assert restored.title == "Move selected PDF"
    assert restored.original_user_input == "Move the selected PDF to Downloads."
    assert restored.source_frontend == "voice"
    assert restored.normalized_task_spec["task_type"] == "file.move"
    assert restored.risk_level == RiskLevel.LOW_RISK_CHANGE
    assert restored.status == JobStatus.RUNNING
    assert restored.current_step == "Move to Downloads"
    assert restored.current_plan == ["Resolve selected file", "Move to Downloads", "Verify destination"]
    assert restored.required_capabilities == ["filesystem", "finder_selection"]
    assert second.list_logs(job.id)[0].message == "Resolved selected Finder item."
    second.close()


def test_job_store_sets_schema_version_and_common_indexes(tmp_path):
    store = DurableJobStore(tmp_path / "eyra-jobs.sqlite3")
    version = store._conn.execute("PRAGMA user_version").fetchone()[0]
    indexes = {
        row[1]
        for row in store._conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }

    assert version >= 1
    assert "idx_jobs_status_updated" in indexes
    assert "idx_job_logs_job_id_id" in indexes
    assert "idx_operation_ledger_job_id_timestamp" in indexes
    store.close()


def test_operation_ledger_records_reversible_action_metadata(tmp_path):
    store = DurableJobStore(tmp_path / "eyra-jobs.sqlite3")
    job = store.create_job(
        title="Remove file",
        original_user_input="Remove that file.",
        source_frontend="terminal",
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
    )

    entry = store.record_operation(
        job_id=job.id,
        user_request="Remove that file.",
        normalized_action={"type": "file.trash", "path": "/tmp/report.pdf"},
        capability="filesystem.trash",
        target="/tmp/report.pdf",
        before_state={"exists": True},
        after_state={"trash_path": "~/.Trash/report.pdf"},
        risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
        success=True,
        undo={"type": "file.restore_from_trash", "trash_path": "~/.Trash/report.pdf"},
        approval_id="approval-1",
    )

    restored = store.list_operations(job.id)

    assert restored == [entry]
    assert restored[0].target == "/tmp/report.pdf"
    assert restored[0].undo["type"] == "file.restore_from_trash"
    assert restored[0].approval_id == "approval-1"
    store.close()


def test_job_store_clears_completed_failed_and_cancelled_jobs(tmp_path):
    store = DurableJobStore(tmp_path / "eyra-jobs.sqlite3")
    done = store.create_job(
        title="Done",
        original_user_input="done",
        source_frontend="terminal",
        status=JobStatus.COMPLETED,
    )
    failed = store.create_job(
        title="Failed",
        original_user_input="failed",
        source_frontend="terminal",
        status=JobStatus.FAILED,
    )
    running = store.create_job(
        title="Running",
        original_user_input="running",
        source_frontend="terminal",
        status=JobStatus.RUNNING,
    )
    store.record_log(done.id, "done log")

    count = store.clear_terminal_jobs()

    assert count == 2
    assert store.get_job(done.id) is None
    assert store.get_job(failed.id) is None
    assert store.get_job(running.id) is not None
    assert store.list_logs(done.id) == []
    store.close()


def test_background_task_manager_persists_compatibility_task_rows(tmp_path):
    async def run():
        store = DurableJobStore(tmp_path / "eyra-jobs.sqlite3")
        manager = BackgroundTaskManager(max_concurrent=1, task_timeout_seconds=5, job_store=store)

        async def worker(task):
            task.mark_progress("Working")
            return "done"

        task = manager.create_task("Summary", "summarize this", worker, required_filesystem=True)
        await manager.wait_for_task(task.id)

        restored = store.get_job(task.id)
        store.close()
        return task, restored

    task, restored = asyncio.run(run())
    assert restored is not None
    assert restored.id == task.id
    assert restored.status == JobStatus.COMPLETED
    assert restored.final_result == "done"
    assert restored.required_capabilities == ["filesystem"]
    assert task.status == TaskStatus.COMPLETED


def test_settings_loads_job_store_path_from_env(monkeypatch):
    monkeypatch.setenv("JOB_STORE_PATH", "/tmp/custom-eyra-jobs.sqlite3")

    settings = Settings.load_from_env()

    assert settings.JOB_STORE_PATH == "/tmp/custom-eyra-jobs.sqlite3"
