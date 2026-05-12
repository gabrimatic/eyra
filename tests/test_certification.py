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
        "cancel",
        "pause_resume",
        "operation_ledger",
        "undo_reversible_file_move",
        "trigger_creation",
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
