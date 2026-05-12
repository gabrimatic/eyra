"""Tests for the local voice-to-computer certification matrix."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_certification_matrix_contains_required_structured_rows(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)

    required = {
        "mock_terminal_startup",
        "voice_diagnostics",
        "job_persistence",
        "task_logs",
        "task_artifacts",
        "direct_file_write",
        "overwrite_refusal",
        "explicit_overwrite_approval",
        "direct_file_move",
        "direct_file_copy",
        "append_prepend",
        "compare_files",
        "rename_path",
        "duplicate_path",
        "trash_delete",
        "restore_from_trash",
        "permanent_delete_approval",
        "zip_unzip",
        "zip_path_traversal_refusal",
        "background_task_creation",
        "cancel",
        "pause_resume",
        "task_retry",
        "clear_completed",
        "operation_ledger",
        "undo_reversible_file_move",
        "undo_reversible_file_operations",
        "trigger_creation",
        "trigger_fire",
        "trigger_pause_resume_cancel",
        "reminder_trigger",
        "recurring_reminder_trigger",
        "network_disabled_refusal",
        "os_tools_disabled_refusal",
        "mcp_disabled_default",
        "agent_bridge_disabled_default",
        "realtime_disabled_default",
    }

    assert required.issubset({row.name for row in report.rows})
    assert all(row.status in {"passed", "failed", "skipped"} for row in report.rows)
    assert all(row.reason for row in report.rows)


def test_certification_report_renders_machine_readable_summary(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    rendered = run_certification(settings=settings, include_physical=False).render()

    assert "Voice-to-computer certification" in rendered
    assert "status" in rendered.lower()
    assert "mock_terminal_startup" in rendered


def test_certification_fails_when_voice_diagnostics_have_failed_check(tmp_path):
    from runtime.certification import run_certification
    from runtime.voice_diagnostics import DiagnosticCheck, DiagnosticReport
    from utils.settings import Settings

    settings = Settings(
        LIVE_LISTENING_ENABLED=True,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )
    report = DiagnosticReport(
        title="Voice diagnostics",
        checks=[DiagnosticCheck("captured_audio", "failed", "microphone input is silent/all-zero")],
    )

    class FakeDiagnostics:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **_kwargs):
            return report

    import runtime.certification as certification

    original = certification.VoiceDiagnostics
    certification.VoiceDiagnostics = FakeDiagnostics
    try:
        result = run_certification(settings=settings, include_physical=False)
    finally:
        certification.VoiceDiagnostics = original

    row = next(row for row in result.rows if row.name == "voice_diagnostics")
    assert row.status == "failed"
    assert result.failed is True


def test_certification_exercises_real_file_operations_and_approval_paths(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    file_rows = {
        "direct_file_write",
        "overwrite_refusal",
        "explicit_overwrite_approval",
        "direct_file_move",
        "direct_file_copy",
        "append_prepend",
        "compare_files",
        "rename_path",
        "duplicate_path",
        "trash_delete",
        "restore_from_trash",
        "permanent_delete_approval",
        "zip_unzip",
        "zip_path_traversal_refusal",
        "undo_reversible_file_operations",
    }

    assert file_rows.issubset(rows)
    assert {rows[name].status for name in file_rows} == {"passed"}


def test_certification_exercises_job_and_trigger_lifecycle_paths(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    lifecycle_rows = {
        "background_task_creation",
        "task_retry",
        "clear_completed",
        "trigger_fire",
        "trigger_pause_resume_cancel",
    }

    assert lifecycle_rows.issubset(rows)
    assert {rows[name].status for name in lifecycle_rows} == {"passed"}


def test_certification_run_does_not_print_runtime_noise(tmp_path, capsys):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    run_certification(settings=settings, include_physical=False)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
