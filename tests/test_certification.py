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
        "real_local_model_startup",
        "typed_command_path",
        "voice_diagnostics",
        "local_whisper_tts",
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
        "web_standalone_runtime",
        "web_shared_runtime",
        "web_auth",
        "web_approval_api",
        "web_event_stream",
        "web_job_logs_artifacts_api",
        "web_trigger_api",
        "capability_privacy_answers",
        "screen_vision_model_split",
        "browser_enabled_open_url",
        "browser_enabled_click",
        "browser_enabled_fill_form",
        "browser_enabled_page_screenshot",
        "browser_download_approval",
        "browser_upload_approval",
        "browser_download_sandbox_refusal",
        "browser_upload_sandbox_refusal",
        "network_disabled_refusal",
        "os_enabled_list_open_apps",
        "os_tools_disabled_refusal",
        "agent_enabled_coding_approval",
        "mcp_enabled_tool_approval",
        "mcp_disabled_default",
        "agent_bridge_disabled_default",
        "realtime_disabled_default",
        "route_text_chat",
        "route_screen_controller_owned",
        "route_pdf_controller_owned",
        "route_clipboard_private_read",
        "route_file_read_no_mutating_tools",
        "route_file_write_has_only_relevant_mutating_tools",
        "route_browser_disabled",
        "route_browser_enabled",
        "route_os_disabled",
        "route_os_enabled",
        "route_shell_disabled",
        "route_shell_enabled",
        "route_mcp_disabled",
        "route_mcp_enabled",
        "route_agent_disabled",
        "route_agent_enabled",
        "route_realtime_no_risky_tools",
        "route_trace_redaction",
        "route_terminal_web_parity",
        "route_unknown_tool_capability",
        "route_verified_tool_capability",
        "route_verified_non_tool_model_refusal",
        "route_worker_model_override",
        "route_complexity_routing_off",
        "route_complexity_routing_on",
        "handsfree_status",
        "handsfree_approve_reject",
        "handsfree_numbered_choice",
        "handsfree_undo",
        "handsfree_dictation",
        "barge_in_no_self_interruption",
        "barge_in_human_phrase_required",
        "external_agent_registry_disabled_default",
        "external_agent_capability_snapshot",
        "external_agent_config_missing",
        "external_agent_unknown_name",
        "external_agent_output_cap",
        "external_agent_sandbox_cwd",
        "external_agent_realtime_not_exposed",
        "competitor_positioning_doc_exists",
    }

    assert required.issubset({row.name for row in report.rows})
    assert all(row.status in {"passed", "failed", "skipped"} for row in report.rows)
    assert all(row.reason for row in report.rows)
    status_by_name = {row.name: row.status for row in report.rows}
    assert all(status_by_name[name] == "passed" for name in required if name.startswith(("route_", "handsfree_", "barge_in_", "external_agent_", "competitor_")))


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


def test_certification_voice_failure_reason_includes_multiple_diagnostic_failures(tmp_path):
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
        checks=[
            DiagnosticCheck("captured_audio", "failed", "microphone input is silent/all-zero"),
            DiagnosticCheck(
                "alternate_input_device",
                "failed",
                "No alternate input device delivered nonzero audio (Jump Desktop Microphone: all-zero).",
            ),
        ],
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
    assert "captured_audio: microphone input is silent/all-zero" in row.reason
    assert "alternate_input_device: No alternate input device delivered nonzero audio" in row.reason


def test_certification_passes_synthetic_mic_flag_to_barge_in_diagnostics(tmp_path):
    from runtime.certification import run_certification
    from runtime.voice_diagnostics import DiagnosticCheck, DiagnosticReport
    from utils.settings import Settings

    settings = Settings(
        LIVE_LISTENING_ENABLED=True,
        LIVE_SPEECH_ENABLED=True,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )
    report = DiagnosticReport(
        title="Voice diagnostics",
        checks=[
            DiagnosticCheck("captured_audio", "passed", "Captured audio"),
            DiagnosticCheck(
                "tts_interrupt_by_mic_speech",
                "passed",
                "Synthetic microphone audio interrupted TTS and ASR returned text.",
            ),
        ],
    )
    seen_kwargs = {}

    class FakeDiagnostics:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **kwargs):
            seen_kwargs.update(kwargs)
            return report

    import runtime.certification as certification

    original = certification.VoiceDiagnostics
    certification.VoiceDiagnostics = FakeDiagnostics
    try:
        result = run_certification(settings=settings, include_physical=True, synthetic_mic=True)
    finally:
        certification.VoiceDiagnostics = original

    row = next(row for row in result.rows if row.name == "physical_barge_in")
    assert row.status == "passed"
    assert seen_kwargs["synthetic_mic"] is True


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


def test_certification_exercises_web_and_capability_privacy_paths(tmp_path):
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

    web_rows = {
        "web_standalone_runtime",
        "web_shared_runtime",
        "web_auth",
        "web_approval_api",
        "web_event_stream",
        "web_job_logs_artifacts_api",
        "web_trigger_api",
        "capability_privacy_answers",
    }

    assert web_rows.issubset(rows)
    assert {rows[name].status for name in web_rows} == {"passed"}


def test_certification_exercises_terminal_command_rows(tmp_path):
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

    assert rows["typed_command_path"].status == "passed"
    assert rows["real_local_model_startup"].status == "skipped"
    assert "mock" in rows["real_local_model_startup"].reason.lower()


def test_certification_exercises_local_whisper_tts_when_speech_enabled(tmp_path, monkeypatch):
    from runtime.certification import run_certification
    from utils.settings import Settings

    calls = []

    class FakeProcess:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            self.returncode = 0
            return 0

    async def fake_create_subprocess_exec(*args, **_kwargs):
        calls.append(args)
        return FakeProcess()

    import runtime.certification as certification

    monkeypatch.setattr(certification, "_resolve_cert_wh_bin", lambda _settings: "/tmp/fake-wh")
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=True,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    assert rows["local_whisper_tts"].status == "passed"
    assert calls[0][:2] == ("/tmp/fake-wh", "whisper")


def test_certification_exercises_enabled_browser_tool_path(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        NETWORK_TOOLS_ENABLED=True,
        FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
        FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    browser_rows = {
        "browser_enabled_open_url",
        "browser_enabled_click",
        "browser_enabled_fill_form",
        "browser_enabled_page_screenshot",
        "browser_download_approval",
        "browser_upload_approval",
        "browser_download_sandbox_refusal",
        "browser_upload_sandbox_refusal",
    }

    assert browser_rows.issubset(rows)
    assert {rows[name].status for name in browser_rows} == {"passed"}


def test_certification_exercises_enabled_os_tool_path(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from runtime.certification import run_certification
    from utils.settings import Settings

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["osascript", "-e"]:
            return SimpleNamespace(returncode=0, stdout="Terminal\n", stderr="")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr("tools.operator.subprocess.run", fake_run)
    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        OS_TOOLS_ENABLED=True,
        FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
        FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    assert rows["os_enabled_list_open_apps"].status == "passed"


def test_certification_exercises_enabled_agent_coding_approval_path(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        AGENT_TOOLS_ENABLED=True,
        FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
        FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    assert rows["agent_enabled_coding_approval"].status == "passed"


def test_certification_exercises_enabled_mcp_approval_path(tmp_path):
    from runtime.certification import run_certification
    from utils.settings import Settings

    settings = Settings(
        USE_MOCK_CLIENT=True,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        MCP_TOOLS_ENABLED=True,
        FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
        FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    assert rows["mcp_enabled_tool_approval"].status == "passed"


def test_certification_exercises_screen_vision_model_split_when_available(tmp_path, monkeypatch):
    import runtime.certification as certification
    from runtime.certification import run_certification
    from runtime.models import PreflightResult
    from utils.settings import Settings

    monkeypatch.setattr(
        certification,
        "_run_cert_preflight",
        lambda _settings: PreflightResult(
            backend_reachable=True,
            models_ready=["text-model", "vision-model"],
            screen_capture_available=True,
            vision_capability_checked_models=["vision-model"],
            vision_capable_models=["vision-model"],
        ),
    )
    settings = Settings(
        USE_MOCK_CLIENT=False,
        MODEL="text-model",
        VISION_MODEL="vision-model",
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
    )

    report = run_certification(settings=settings, include_physical=False)
    rows = {row.name: row for row in report.rows}

    assert rows["screen_vision_model_split"].status == "passed"
