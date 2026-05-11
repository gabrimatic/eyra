"""Tests for the built-in web UI helpers."""

import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.settings import Settings
from web.server import (
    WebAssistantRuntime,
    _EyraWebHandler,
    build_health_payload,
    create_realtime_session_payload,
    realtime_tools,
    render_index_html,
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
        assert payload["web"]["enabled"] is True
        assert payload["voice"]["localWhisper"] is True
        assert payload["voice"]["realtime"] is False

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
