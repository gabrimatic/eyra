"""Tests for local runtime context snapshots."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.context import build_context_snapshot, format_context_answer
from runtime.jobs import DurableJobStore, RiskLevel
from runtime.models import LiveRuntimeState
from utils.settings import Settings


def test_context_snapshot_includes_goal_cwd_recent_jobs_and_operations(tmp_path):
    store = DurableJobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(
        title="Move selected PDF",
        original_user_input="Move the selected PDF to Downloads.",
        source_frontend="voice",
        risk_level=RiskLevel.LOW_RISK_CHANGE,
    )
    store.record_operation(
        job_id=job.id,
        user_request="Move the selected PDF to Downloads.",
        normalized_action={"type": "file.move"},
        capability="filesystem.move",
        target=str(tmp_path / "Downloads" / "a.pdf"),
        before_state={},
        after_state={},
        risk_level=RiskLevel.LOW_RISK_CHANGE,
        success=True,
        undo={"type": "file.move"},
    )
    state = LiveRuntimeState(current_goal="Finish the local operator")

    snapshot = build_context_snapshot(Settings(), state=state, job_store=store, cwd=str(tmp_path))

    assert snapshot["currentGoal"] == "Finish the local operator"
    assert snapshot["cwd"] == str(tmp_path)
    assert snapshot["recentJobs"][0]["title"] == "Move selected PDF"
    assert snapshot["recentOperations"][0]["action"] == "file.move"
    assert snapshot["recentOperations"][0]["target"].endswith("a.pdf")
    store.close()


def test_context_answer_is_compact_and_local(tmp_path):
    store = DurableJobStore(tmp_path / "jobs.sqlite3")
    state = LiveRuntimeState(current_goal="Test context")

    answer = format_context_answer(build_context_snapshot(Settings(), state=state, job_store=store, cwd=str(tmp_path)))

    assert "Current context" in answer
    assert "Goal: Test context" in answer
    assert f"Working directory: {tmp_path}" in answer
    assert "Recent jobs: none" in answer
    store.close()
