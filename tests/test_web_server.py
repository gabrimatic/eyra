"""Tests for the built-in web UI helpers."""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.jobs import JobStatus
from runtime.models import PreflightResult
from runtime.shared import RuntimeSharedState
from runtime.tasks import TaskStatus
from utils.settings import Settings
from web.server import (
    WebAssistantRuntime,
    _EyraWebHandler,
    build_capabilities_payload,
    build_health_payload,
    create_realtime_session_payload,
    realtime_tools,
    render_index_html,
    run_web_server,
    speak_local_text,
    validate_realtime_tool_token,
    validate_request_size,
    validate_web_session_token,
    web_auth_required,
)


class TestWebServerHelpers:
    def test_health_payload_names_voice_and_tool_modes(self):
        payload = build_health_payload(Settings(WEB_UI_ENABLED=True, REALTIME_VOICE_ENABLED=False))

        assert payload["status"] == "ok"
        assert payload["runtime"]["scope"] == "standalone"
        assert payload["web"]["enabled"] is True
        assert payload["voice"]["localWhisper"] is True
        assert payload["voice"]["realtime"] is False
        assert payload["capabilities"]["localFirst"] is True
        assert payload["capabilities"]["privacy"]["leavesMachineByDefault"] is False
        assert "tools" not in payload["capabilities"]

    def test_health_payload_can_report_shared_runtime_scope(self):
        payload = build_health_payload(Settings(WEB_UI_ENABLED=True), runtime_scope="shared")

        assert payload["runtime"]["scope"] == "shared"

    def test_health_payload_uses_preflight_for_backend_and_model_capabilities(self):
        settings = Settings(WEB_UI_ENABLED=True)
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=[settings.MODEL],
            tool_capable_models=[],
            tool_capability_checked_models=[settings.MODEL],
            vision_capable_models=[settings.MODEL],
            vision_capability_checked_models=[settings.MODEL],
            screen_capture_available=True,
        )

        payload = build_health_payload(settings, preflight=preflight)

        assert payload["capabilities"]["models"]["backendReady"] is True
        assert payload["capabilities"]["models"]["mainToolCalling"] == "no"
        assert payload["capabilities"]["models"]["visionImages"] == "yes"

    def test_authenticated_capabilities_payload_redacts_local_paths(self):
        settings = Settings(
            WEB_UI_ENABLED=True,
            FILESYSTEM_ALLOWED_PATHS="/Users/soroush/Documents,/tmp",
            JOB_STORE_PATH="/Users/soroush/.local/share/eyra/jobs.sqlite3",
        )

        payload = build_capabilities_payload(settings)
        rendered = json.dumps(payload)

        assert "/Users/soroush" not in rendered
        assert "~/[user]" in rendered

    def test_index_html_has_phone_ready_controls(self):
        html = render_index_html(Settings(WEB_UI_ENABLED=True, REALTIME_VOICE_ENABLED=True))

        assert "id=\"chatForm\"" in html
        assert "id=\"micButton\"" in html
        assert "Realtime" in html
        assert "Local Whisper" in html
        assert "fetch('/api/chat'" in html
        assert "MediaRecorder" in html
        assert "/api/local-voice-turn" in html
        assert "/api/local-speak" in html
        assert "RTCPeerConnection" in html
        assert "https://api.openai.com/v1/realtime/calls" in html
        assert "EventSource" in html
        assert "/api/events" in html
        assert "setInterval" not in html

    def test_local_speak_uses_whisper_cli_when_available(self):
        with patch("web.server.resolve_wh_bin", return_value="/usr/bin/wh"):
            with patch("web.server.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stderr = ""

                result = speak_local_text("hello")

        assert result == "Local speech started."
        assert run.call_args.args[0][:2] == ["/usr/bin/wh", "whisper"]

    def test_realtime_session_requires_explicit_openai_key(self):
        status, payload = create_realtime_session_payload(
            Settings(
                REALTIME_VOICE_ENABLED=True,
                OPENAI_API_KEY="",
                API_KEY="provider-key-that-must-not-be-sent",
            )
        )

        assert status == 400
        assert "OPENAI_API_KEY" in payload["error"]

    def test_realtime_session_mints_client_secret_server_side(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"value":"ephemeral"}'

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = request.data.decode()
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("web.server.urllib.request.urlopen", fake_urlopen):
            status, payload = create_realtime_session_payload(
                Settings(REALTIME_VOICE_ENABLED=True, OPENAI_API_KEY="sk-real-secret")
            )

        body = captured["body"]
        assert status == 200
        assert payload["value"] == "ephemeral"
        assert captured["url"] == "https://api.openai.com/v1/realtime/client_secrets"
        assert "Bearer sk-real-secret" in captured["headers"]["Authorization"]
        assert '"model": "gpt-realtime"' in body
        assert "sk-real-secret" not in body

    def test_realtime_tool_token_requires_realtime_enabled_and_secret(self):
        disabled = Settings(REALTIME_VOICE_ENABLED=False)
        enabled = Settings(REALTIME_VOICE_ENABLED=True, REALTIME_TOOLS_ENABLED=True)

        assert validate_realtime_tool_token(disabled, "secret", "secret") is False
        assert validate_realtime_tool_token(enabled, "wrong", "secret") is False
        assert validate_realtime_tool_token(enabled, "secret", "secret") is True

    def test_web_session_token_requires_exact_secret(self):
        assert validate_web_session_token("", "secret") is False
        assert validate_web_session_token("wrong", "secret") is False
        assert validate_web_session_token("secret", "secret") is True

    def test_web_auth_auto_requires_token_when_bound_to_lan(self):
        local = Settings(WEB_UI_HOST="127.0.0.1", WEB_UI_REQUIRE_TOKEN="auto")
        exposed = Settings(WEB_UI_HOST="0.0.0.0", WEB_UI_REQUIRE_TOKEN="auto")

        assert web_auth_required(local) is True
        assert web_auth_required(exposed) is True

    def test_web_auth_false_cannot_disable_token_on_lan_bind(self):
        local = Settings(WEB_UI_HOST="127.0.0.1", WEB_UI_REQUIRE_TOKEN="false")
        exposed = Settings(WEB_UI_HOST="0.0.0.0", WEB_UI_REQUIRE_TOKEN="false")

        assert web_auth_required(local) is False
        assert web_auth_required(exposed) is True

    def test_request_size_limit_is_enforced(self):
        settings = Settings(WEB_UI_MAX_REQUEST_BYTES=16)

        assert validate_request_size(settings, 16) is True
        assert validate_request_size(settings, 17) is False

    def test_realtime_tools_are_safe_empty_by_default(self):
        settings = Settings(REALTIME_VOICE_ENABLED=True, REALTIME_TOOLS_ENABLED=False)

        assert realtime_tools(settings) == []

    def test_realtime_allowed_tools_cannot_expose_risky_local_tools(self):
        settings = Settings(
            REALTIME_VOICE_ENABLED=True,
            REALTIME_TOOLS_ENABLED=True,
            REALTIME_ALLOWED_TOOLS="read_clipboard,get_current_time",
        )

        tools = realtime_tools(settings)
        names = {tool["name"] for tool in tools}

        assert "get_current_time" in names
        assert "read_clipboard" not in names

    def test_web_runtime_creates_background_task_for_long_work(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        )
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        runtime = WebAssistantRuntime(settings)

        result = runtime.run_sync(runtime.handle_message(f"Read this PDF and summarize it: {pdf_path}"))

        assert result["taskId"].startswith("t")
        assert "accepted" in result["reply"].lower()
        tasks = runtime.run_sync(runtime.list_tasks())
        assert tasks["tasks"][0]["id"] == result["taskId"]

        runtime.close()

    def test_web_task_payload_redacts_sensitive_request_text(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)

        async def create_task():
            async def worker(task):
                return "opened https://example.com/?token=secret-token"

            task = runtime.task_manager.create_task(
                "Sensitive task",
                "Open https://example.com/?token=secret-token",
                worker,
            )
            await runtime.task_manager.wait_for_task(task.id)
            return await runtime.list_tasks()

        try:
            tasks = runtime.run_sync(create_task())
        finally:
            runtime.close()

        rendered = json.dumps(tasks)
        assert "secret-token" not in rendered
        assert "token=[REDACTED]" in rendered

    def test_web_runtime_publishes_task_events(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        )
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        runtime = WebAssistantRuntime(settings)
        subscriber = runtime.subscribe_task_events()

        try:
            result = runtime.run_sync(runtime.handle_message(f"Read this PDF and summarize it: {pdf_path}"))
            event = subscriber.get(timeout=2)
        finally:
            runtime.unsubscribe_task_events(subscriber)
            runtime.close()

        assert event["event"] == "task"
        assert event["task"]["id"] == result["taskId"]

    def test_web_runtime_close_closes_owned_event_loop(self):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        runtime = WebAssistantRuntime(settings)
        loop = runtime._loop

        runtime.close()

        assert loop.is_closed()

    def test_web_runtime_can_share_terminal_owned_state(self, tmp_path):
        from runtime.models import PreflightResult

        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        preflight = PreflightResult(backend_reachable=True, models_ready=[settings.MODEL])
        shared = RuntimeSharedState.create(settings, preflight=preflight, source_frontend="terminal")
        runtime = WebAssistantRuntime(settings, preflight=preflight, shared=shared)

        async def create_shared_task():
            async def worker(task):
                return "shared task done"

            task = shared.task_manager.create_task("Terminal task", "created by terminal", worker)
            await shared.task_manager.wait_for_task(task.id)
            return task.id

        try:
            approval = shared.approvals.request("run_command", "shell command", {"command": "echo hi"})
            task_id = runtime.run_sync(create_shared_task())
            listed = runtime.run_sync(runtime.list_approvals())
            tasks = runtime.run_sync(runtime.list_tasks())
            runtime.close()

            assert runtime.runtime_scope == "shared"
            assert runtime.approvals is shared.approvals
            assert runtime.job_store is shared.job_store
            assert runtime.task_manager is shared.task_manager
            assert listed["approvals"][0]["id"] == approval.id
            assert tasks["tasks"][0]["id"] == task_id
            assert shared.job_store.get_job(task_id) is not None
        finally:
            shared.close()

    def test_web_runtime_refuses_open_ended_tool_task_without_tool_capable_model(self, tmp_path):
        from runtime.models import PreflightResult

        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        )
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=[settings.MODEL],
            tool_capability_checked_models=[settings.MODEL],
        )
        runtime = WebAssistantRuntime(settings, preflight=preflight)

        result = runtime.run_sync(runtime.handle_message("Organize my Documents folder."))
        tasks = runtime.run_sync(runtime.list_tasks())

        assert "requires a model with native tool calling" in result["reply"]
        assert tasks["tasks"] == []
        runtime.close()

    def test_web_generic_worker_uses_worker_model_across_tiers(self, monkeypatch, tmp_path):
        from runtime.models import PreflightResult

        seen = {}

        async def fake_process_task_stream(**kwargs):
            seen["settings"] = kwargs["settings"]
            seen["require_tools"] = kwargs["require_tools"]
            yield "done"

        monkeypatch.setattr("web.server.process_task_stream", fake_process_task_stream)
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            MODEL="main",
            SIMPLE_MODEL="simple",
            MODERATE_MODEL="moderate",
            WORKER_MODEL="worker",
            COMPLEXITY_ROUTING_ENABLED=True,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        )
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["main", "simple", "moderate", "worker"],
            tool_capability_checked_models=["main", "simple", "moderate", "worker"],
            tool_capable_models=["worker"],
        )
        runtime = WebAssistantRuntime(settings, preflight=preflight)

        try:
            accepted = runtime.run_sync(runtime.handle_message("Organize my Documents folder."))
            task_id = accepted["taskId"]
            runtime.run_sync(runtime.task_manager.wait_for_task(task_id))
        finally:
            runtime.close()

        assert seen["settings"].MODEL == "worker"
        assert seen["settings"].SIMPLE_MODEL == "worker"
        assert seen["settings"].MODERATE_MODEL == "worker"
        assert seen["require_tools"] is True

    def test_web_route_last_returns_policy_trace(self):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=settings.all_model_names,
            tool_capability_checked_models=settings.all_model_names,
            tool_capable_models=settings.all_model_names,
        )
        runtime = WebAssistantRuntime(settings, preflight=preflight)

        try:
            runtime.run_sync(runtime.handle_message("hi"))
            payload = runtime.run_sync(runtime.route_last())
        finally:
            runtime.close()

        assert payload["route"]["executionClass"] == "text_chat"
        assert "source" in payload["route"]

    def test_web_support_diagnostics_are_redacted(self):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        runtime = WebAssistantRuntime(settings)

        try:
            payload = runtime.run_sync(runtime.support_diagnostics())
        finally:
            runtime.close()

        assert "version" in payload
        assert "preflight" in payload
        assert "hasApiKey" in payload["settings"]
        assert "API_KEY" not in json.dumps(payload)

    def test_web_runtime_capability_question_is_local_not_model_chat(self):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        runtime = WebAssistantRuntime(settings)

        try:
            result = runtime.run_sync(runtime.handle_message("What would leave my machine?"))
        finally:
            runtime.close()

        assert "Leaves machine by default" in result["reply"]
        assert "This is a mock response" not in result["reply"]

    def test_web_runtime_refuses_screen_task_without_vision_capable_model(self):
        from runtime.models import PreflightResult

        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=[settings.MODEL],
            vision_capability_checked_models=[settings.MODEL],
        )
        runtime = WebAssistantRuntime(settings, preflight=preflight)

        result = runtime.run_sync(runtime.handle_message("What is on the screen?"))
        tasks = runtime.run_sync(runtime.list_tasks())

        assert "vision-capable model" in result["reply"]
        assert tasks["tasks"] == []
        runtime.close()

    def test_web_runtime_can_cancel_task(self, tmp_path):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        runtime = WebAssistantRuntime(settings)

        result = runtime.run_sync(runtime.handle_message("Read this PDF and summarize it: ~/Documents/missing.pdf"))
        cancel = runtime.run_sync(runtime.cancel_task(result["taskId"]))

        assert cancel["status"] in {"cancelled", "completed", "failed"}
        runtime.close()

    def test_web_runtime_exposes_server_side_approvals(self):
        runtime = WebAssistantRuntime(Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False))
        approval = runtime.approvals.request("run_command", "shell command", {"command": "echo hi"})

        listed = runtime.run_sync(runtime.list_approvals())
        approved = runtime.run_sync(runtime.approve(approval.id))

        assert listed["approvals"][0]["id"] == approval.id
        assert approved["approved"] is True
        runtime.close()

    def test_web_runtime_returns_persisted_job_detail_with_redaction(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)
        job = runtime.job_store.create_job(
            title="Saved job",
            original_user_input="Open this URL with token=secret-token",
            source_frontend="web",
            normalized_task_spec={"url": "https://example.com/?token=secret-token"},
            required_capabilities=["network"],
        )

        try:
            detail = runtime.run_sync(runtime.task_detail(job.id))
        finally:
            runtime.close()

        rendered = json.dumps(detail)
        assert detail["job"]["id"] == job.id
        assert detail["job"]["riskLevel"] == "read_only"
        assert "secret-token" not in rendered
        assert "token=[REDACTED]" in rendered

    def test_web_runtime_file_appears_trigger_moves_file_when_created(self, tmp_path):
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
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
            TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
            TRIGGER_TIMEOUT_SECONDS=2,
        )
        runtime = WebAssistantRuntime(settings)
        runtime._path_in_named_folder = lambda folder, name: str((tmp_path / folder.title() / name))
        source = downloads / "eyra-web-trigger.txt"
        destination = documents / "eyra-web-trigger.txt"

        try:
            result = runtime.run_sync(
                runtime.handle_message("When eyra-web-trigger.txt appears in my Downloads, move it to Documents.")
            )
            source.write_text("trigger me")
            runtime.run_sync(runtime.task_manager.wait_for_task(result["taskId"]), timeout=3)
            triggers = runtime.run_sync(runtime.list_triggers())
            task = runtime.task_manager.get_task(result["taskId"])
        finally:
            runtime.close()

        assert "Trigger" in result["reply"]
        assert task.status.value == "completed"
        assert not source.exists()
        assert destination.read_text() == "trigger me"
        assert triggers["triggers"][0]["status"] == "completed"

    def test_web_runtime_direct_trigger_works_when_model_lacks_tools(self, tmp_path):
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
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
            TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
            TRIGGER_TIMEOUT_SECONDS=2,
        )
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=[settings.MODEL],
            tool_capable_models=[],
            tool_capability_checked_models=[settings.MODEL],
        )
        runtime = WebAssistantRuntime(settings, preflight=preflight)
        runtime._path_in_named_folder = lambda folder, name: str((tmp_path / folder.title() / name))
        source = downloads / "eyra-web-trigger-no-tools.txt"
        destination = documents / "eyra-web-trigger-no-tools.txt"

        try:
            result = runtime.run_sync(
                runtime.handle_message("When eyra-web-trigger-no-tools.txt appears in my Downloads, move it to Documents.")
            )
            time.sleep(0.05)
            task = runtime.task_manager.get_task(result["taskId"])
            assert task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
            source.write_text("trigger me")
            runtime.run_sync(runtime.task_manager.wait_for_task(result["taskId"]), timeout=3)
            task = runtime.task_manager.get_task(result["taskId"])
        finally:
            runtime.close()

        assert "Trigger" in result["reply"]
        assert task.status.value == "completed"
        assert not source.exists()
        assert destination.read_text() == "trigger me"

    def test_web_runtime_reminder_trigger_completes_after_delay(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)

        try:
            result = runtime.run_sync(runtime.handle_message("Remind me in 0.01 seconds to stretch."))
            runtime.run_sync(runtime.task_manager.wait_for_task(result["taskId"]), timeout=3)
            tasks = runtime.run_sync(runtime.list_tasks())
            triggers = runtime.run_sync(runtime.list_triggers())
        finally:
            runtime.close()

        assert "Reminder" in result["reply"]
        assert tasks["tasks"][0]["status"] == "completed"
        assert tasks["tasks"][0]["result"] == "Reminder: stretch"
        assert triggers["triggers"][0]["kind"] == "timer"
        assert triggers["triggers"][0]["status"] == "completed"

    def test_web_runtime_recurring_reminder_runs_until_cancelled(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
            TRIGGER_CHECK_INTERVAL_SECONDS=0.01,
        )
        runtime = WebAssistantRuntime(settings)

        try:
            result = runtime.run_sync(runtime.handle_message("Every 0.01 seconds remind me to stretch."))
            trigger_id = result["triggerId"]
            for _ in range(50):
                triggers = runtime.run_sync(runtime.list_triggers())
                if triggers["triggers"][0]["condition"].get("fire_count", 0) >= 2:
                    break
                time.sleep(0.01)
            runtime.run_sync(runtime.update_trigger(trigger_id, "cancel"))
            runtime.run_sync(runtime.task_manager.wait_for_task(result["taskId"]), timeout=3)
            tasks = runtime.run_sync(runtime.list_tasks())
            triggers = runtime.run_sync(runtime.list_triggers())
        finally:
            runtime.close()

        assert "Recurring reminder" in result["reply"]
        assert tasks["tasks"][0]["status"] == "completed"
        assert tasks["tasks"][0]["result"] == "Recurring reminder cancelled."
        assert triggers["triggers"][0]["kind"] == "recurring_timer"
        assert triggers["triggers"][0]["condition"]["fire_count"] >= 2
        assert triggers["triggers"][0]["status"] == "cancelled"

    def test_web_runtime_coding_job_waits_for_approval_then_runs_agent(self, tmp_path):
        agent_config = tmp_path / "agents.json"
        agent_config.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "codex",
                            "type": "cli",
                            "command": [sys.executable, "-c", "import sys; print('web coding done: ' + sys.stdin.read())"],
                            "cwdPolicy": "request",
                            "requiresApproval": True,
                            "timeoutSeconds": 5,
                        }
                    ]
                }
            )
        )
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            AGENT_TOOLS_ENABLED=True,
            EXTERNAL_AGENT_TOOLS_ENABLED=True,
            EXTERNAL_AGENT_CONFIG_PATH=str(agent_config),
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)

        try:
            result = runtime.run_sync(runtime.handle_message("Start a coding job with Codex to update the README."))
            approvals = runtime.run_sync(runtime.list_approvals())
            approved = runtime.run_sync(runtime.approve(approvals["approvals"][0]["id"]))
            runtime.run_sync(runtime.task_manager.wait_for_task(result["taskId"]), timeout=3)
            tasks = runtime.run_sync(runtime.list_tasks())
        finally:
            runtime.close()

        assert "Coding job" in result["reply"]
        assert approved["approved"] is True
        assert tasks["tasks"][0]["status"] == "completed"
        assert "web coding done" in tasks["tasks"][0]["result"]
        assert "update the README" in tasks["tasks"][0]["result"]

    def test_web_runtime_refuses_coding_job_when_agent_tools_are_disabled(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            AGENT_TOOLS_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
            FILESYSTEM_DEFAULT_PATH=str(tmp_path),
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)

        try:
            result = runtime.run_sync(runtime.handle_message("Start a coding job with Codex to update the README."))
            tasks = runtime.run_sync(runtime.list_tasks())
        finally:
            runtime.close()

        assert "Agent tools are disabled" in result["reply"]
        assert tasks["tasks"] == []

    def test_web_runtime_dictation_captures_text_without_model_response(self, tmp_path):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)

        try:
            start = runtime.run_sync(runtime.handle_message("Start dictation."))
            captured = runtime.run_sync(runtime.handle_message("The first line."))
            ended = runtime.run_sync(runtime.handle_message("End dictation."))
        finally:
            runtime.close()

        assert "Dictation started" in start["reply"]
        assert "Captured" in captured["reply"]
        assert "Dictation ended" in ended["reply"]
        assert "The first line." in ended["reply"]
        assert "This is a mock response" not in ended["reply"]

    def test_web_runtime_dictation_saves_to_file(self, tmp_path):
        documents = tmp_path / "Documents"
        documents.mkdir()
        settings = Settings(
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            FILESYSTEM_ALLOWED_PATHS=str(documents),
            FILESYSTEM_DEFAULT_PATH=str(documents),
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)
        runtime._path_in_named_folder = lambda folder, name: str((tmp_path / folder.title() / name))
        target = documents / "web-dictation.txt"

        try:
            runtime.run_sync(runtime.handle_message("Start dictation to a file named web-dictation.txt in my Documents."))
            runtime.run_sync(runtime.handle_message("Saved from the web runtime."))
            ended = runtime.run_sync(runtime.handle_message("End dictation."))
        finally:
            runtime.close()

        assert "Dictation saved" in ended["reply"]
        assert target.read_text() == "Saved from the web runtime."

    def test_web_api_lists_persisted_triggers(self, tmp_path):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="true",
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)
        runtime.trigger_store.create_file_exists_trigger(
            title="Move web download",
            source_path=str(tmp_path / "Downloads" / "a.txt"),
            action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
            original_request="When a.txt appears in Downloads, move it to Documents.",
        )
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(base + "/api/triggers", headers={"X-Eyra-Web-Token": "secret-token"})
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

        assert payload["triggers"][0]["title"] == "Move web download"

    def test_web_api_updates_trigger_status(self, tmp_path):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="true",
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)
        trigger = runtime.trigger_store.create_file_exists_trigger(
            title="Move web download",
            source_path=str(tmp_path / "Downloads" / "a.txt"),
            action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
            original_request="When a.txt appears in Downloads, move it to Documents.",
        )
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(
                base + "/api/trigger",
                method="POST",
                data=json.dumps({"triggerId": trigger.id, "action": "pause"}).encode(),
                headers={"Content-Type": "application/json", "X-Eyra-Web-Token": "secret-token"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

        assert payload["trigger"]["status"] == "paused"

    def test_web_api_exposes_job_logs_artifacts_and_clear_completed(self, tmp_path):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="true",
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
            JOB_STORE_PATH=str(tmp_path / "jobs.sqlite3"),
            TRIGGER_STORE_PATH=str(tmp_path / "triggers.sqlite3"),
        )
        runtime = WebAssistantRuntime(settings)
        job = runtime.job_store.create_job(
            title="Web durable job",
            original_user_input="Do web durable work",
            source_frontend="web",
        )
        runtime.job_store.update_job(
            job.id,
            status=JobStatus.COMPLETED,
            artifacts=[{"type": "file", "path": "/tmp/web-result.txt"}],
        )
        runtime.job_store.record_log(job.id, "Web job started.")
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            logs_request = urllib.request.Request(
                base + f"/api/job/{job.id}/logs",
                headers={"X-Eyra-Web-Token": "secret-token"},
            )
            with urllib.request.urlopen(logs_request, timeout=5) as response:
                logs_payload = json.loads(response.read().decode())
            artifacts_request = urllib.request.Request(
                base + f"/api/job/{job.id}/artifacts",
                headers={"X-Eyra-Web-Token": "secret-token"},
            )
            with urllib.request.urlopen(artifacts_request, timeout=5) as response:
                artifacts_payload = json.loads(response.read().decode())
            clear_request = urllib.request.Request(
                base + "/api/tasks/clear-completed",
                method="POST",
                data=b"{}",
                headers={"X-Eyra-Web-Token": "secret-token", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(clear_request, timeout=5) as response:
                clear_payload = json.loads(response.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

        assert logs_payload["logs"][0]["message"] == "Web job started."
        assert artifacts_payload["artifacts"][0]["path"] == "/tmp/web-result.txt"
        assert clear_payload["cleared"] == 1

    def test_web_api_rejects_unauthorized_requests_when_token_required(self):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="auto",
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        runtime = WebAssistantRuntime(settings)
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            try:
                urllib.request.urlopen(base + "/api/tasks", timeout=5)
                raise AssertionError("unauthorized request unexpectedly succeeded")
            except urllib.error.HTTPError as e:
                assert e.code == 401

            request = urllib.request.Request(base + "/api/tasks", headers={"X-Eyra-Web-Token": "secret-token"})
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = response.read().decode()

            assert response.status == 200
            assert "tasks" in payload
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

    def test_web_api_rejects_cross_origin_requests(self):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="true",
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        runtime = WebAssistantRuntime(settings)
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(
                base + "/api/tasks",
                headers={"X-Eyra-Web-Token": "secret-token", "Origin": "https://example.com"},
            )
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError("cross-origin request unexpectedly succeeded")
            except urllib.error.HTTPError as e:
                assert e.code == 403
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

    def test_web_api_rejects_oversized_local_voice_uploads(self):
        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_REQUIRE_TOKEN="true",
            WEB_UI_MAX_REQUEST_BYTES=4,
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        runtime = WebAssistantRuntime(settings)
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(
                base + "/api/local-voice-turn",
                method="POST",
                data=b"12345",
                headers={"X-Eyra-Web-Token": "secret-token", "Content-Type": "audio/webm"},
            )
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError("oversized upload unexpectedly succeeded")
            except urllib.error.HTTPError as e:
                assert e.code == 413
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()

    def test_run_web_server_stops_when_preflight_fails(self, capsys):
        from runtime.models import PreflightResult

        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        preflight = PreflightResult(backend_reachable=False)

        with patch("web.server.EyraThreadingHTTPServer") as server:
            run_web_server(settings, preflight=preflight)

        server.assert_not_called()
        assert "Backend is not reachable" in capsys.readouterr().out

    def test_run_web_server_reports_bind_failure_without_traceback(self, capsys):
        from runtime.models import PreflightResult

        settings = Settings(
            WEB_UI_HOST="127.0.0.1",
            WEB_UI_PORT=12345,
            USE_MOCK_CLIENT=True,
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        preflight = PreflightResult(backend_reachable=True, models_ready=[settings.MODEL])

        with patch("web.server.EyraThreadingHTTPServer", side_effect=OSError("Address already in use")):
            run_web_server(settings, preflight=preflight)

        out = capsys.readouterr().out
        assert "Could not start Eyra web UI" in out
        assert "Address already in use" in out
        assert "Traceback" not in out

    def test_web_responses_include_security_headers(self):
        settings = Settings(USE_MOCK_CLIENT=True, LIVE_LISTENING_ENABLED=False, LIVE_SPEECH_ENABLED=False)
        runtime = WebAssistantRuntime(settings)
        handler = type(
            "TestEyraWebHandler",
            (_EyraWebHandler,),
            {
                "settings": settings,
                "runtime": runtime,
                "web_session_token": "secret-token",
                "realtime_tool_token": "rt-secret",
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}

            assert headers["cache-control"] == "no-store"
            assert headers["referrer-policy"] == "no-referrer"
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["x-frame-options"] == "DENY"
            assert "default-src 'self'" in headers["content-security-policy"]
            assert "access-control-allow-origin" not in headers
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()
