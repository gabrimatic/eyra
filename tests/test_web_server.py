"""Tests for the built-in web UI helpers."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.settings import Settings
from web.server import (
    build_health_payload,
    create_realtime_session_payload,
    render_index_html,
    speak_local_text,
    validate_realtime_tool_token,
    validate_web_session_token,
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

    def test_realtime_tool_token_requires_realtime_enabled_and_secret(self):
        disabled = Settings(REALTIME_VOICE_ENABLED=False)
        enabled = Settings(REALTIME_VOICE_ENABLED=True)

        assert validate_realtime_tool_token(disabled, "secret", "secret") is False
        assert validate_realtime_tool_token(enabled, "wrong", "secret") is False
        assert validate_realtime_tool_token(enabled, "secret", "secret") is True

    def test_web_session_token_requires_exact_secret(self):
        assert validate_web_session_token("", "secret") is False
        assert validate_web_session_token("wrong", "secret") is False
        assert validate_web_session_token("secret", "secret") is True
