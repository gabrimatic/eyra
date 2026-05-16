"""Tests for first-run provider setup."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime import startup


class TestStartupSelector:
    def test_write_env_preserves_optional_capability_settings(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "OS_TOOLS_ENABLED=true",
                    "SCREEN_OCR_COMMAND=/usr/local/bin/ocr",
                    "AGENT_TOOLS_ENABLED=true",
                    "EXTERNAL_AGENT_TOOLS_ENABLED=true",
                    "EXTERNAL_AGENT_CONFIG_PATH=~/agents.json",
                    "CONNECTORS_ENABLED=true",
                    "CONNECTORS_CONFIG_PATH=~/connectors.json",
                    "CONNECTORS_ALLOWED_ROOTS=~/Documents",
                    "CONNECTORS_TIMEOUT_SECONDS=123",
                    "CONNECTORS_OUTPUT_CAP_BYTES=456",
                    "CONNECTORS_ALLOW_REMOTE=true",
                    "CONNECTORS_ALLOW_PYTHON_MODULE=true",
                    "MCP_TOOLS_ENABLED=true",
                    "MCP_CONFIG_PATH=~/mcp.json",
                    "VOICE_MAX_DURATION_SECONDS=222",
                    "HANDS_FREE_MODE=true",
                    "JOB_STORE_PATH=~/jobs.sqlite3",
                    "TRIGGER_STORE_PATH=~/triggers.sqlite3",
                    "TRIGGER_CHECK_INTERVAL_SECONDS=2",
                    "TRIGGER_TIMEOUT_SECONDS=99",
                    "WEB_UI_ENABLED=true",
                    "WEB_UI_HOST=0.0.0.0",
                    "WEB_UI_PORT=9999",
                    "WEB_UI_REQUIRE_TOKEN=auto",
                    "WEB_UI_MAX_REQUEST_BYTES=1234",
                    "REALTIME_VOICE_ENABLED=true",
                    "REALTIME_MODEL=gpt-realtime",
                    "REALTIME_VOICE=marin",
                    "OPENAI_API_KEY=sk-test",
                    "REALTIME_TOOLS_ENABLED=true",
                    "REALTIME_ALLOWED_TOOLS=get_current_time",
                    "VISION_MODEL=gemma3:4b",
                    "ROUTING_DEBUG=true",
                    "",
                ]
            )
        )
        monkeypatch.setattr(startup, "_ENV", env_path)

        startup._write_env("http://localhost:11434/v1", "ollama", "gemma3:4b")

        content = env_path.read_text()
        assert "OS_TOOLS_ENABLED=true" in content
        assert "SCREEN_OCR_COMMAND=/usr/local/bin/ocr" in content
        assert "AGENT_TOOLS_ENABLED=true" in content
        assert "EXTERNAL_AGENT_TOOLS_ENABLED=true" in content
        assert "EXTERNAL_AGENT_CONFIG_PATH=~/agents.json" in content
        assert "CONNECTORS_ENABLED=true" in content
        assert "CONNECTORS_CONFIG_PATH=~/connectors.json" in content
        assert "CONNECTORS_ALLOWED_ROOTS=~/Documents" in content
        assert "CONNECTORS_TIMEOUT_SECONDS=123" in content
        assert "CONNECTORS_OUTPUT_CAP_BYTES=456" in content
        assert "CONNECTORS_ALLOW_REMOTE=true" in content
        assert "CONNECTORS_ALLOW_PYTHON_MODULE=true" in content
        assert "MCP_TOOLS_ENABLED=true" in content
        assert "VOICE_MAX_DURATION_SECONDS=222" in content
        assert "HANDS_FREE_MODE=true" in content
        assert "JOB_STORE_PATH=~/jobs.sqlite3" in content
        assert "TRIGGER_STORE_PATH=~/triggers.sqlite3" in content
        assert "TRIGGER_CHECK_INTERVAL_SECONDS=2" in content
        assert "TRIGGER_TIMEOUT_SECONDS=99" in content
        assert "WEB_UI_ENABLED=true" in content
        assert "WEB_UI_REQUIRE_TOKEN=auto" in content
        assert "REALTIME_TOOLS_ENABLED=true" in content
        assert "REALTIME_ALLOWED_TOOLS=get_current_time" in content
        assert "VISION_MODEL=gemma3:4b" in content
        assert "ROUTING_POLICY_ENABLED" not in content
        assert "ROUTING_DEBUG=true" in content
        assert "REALTIME_VOICE_ENABLED=true" in content
        assert "REALTIME_MODEL=gpt-realtime" in content
        assert "OPENAI_API_KEY=sk-test" in content

    def test_mock_client_env_skips_provider_setup(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("USE_MOCK_CLIENT=true\n")
        monkeypatch.setattr(startup, "_ENV", env_path)

        with patch("runtime.startup.input") as mock_input:
            assert startup.maybe_run_startup_selector() is False

        mock_input.assert_not_called()

    def test_live_env_overrides_env_file_for_mock_mode(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("API_BASE_URL=http://missing.local/v1\nUSE_MOCK_CLIENT=false\n")
        monkeypatch.setattr(startup, "_ENV", env_path)
        monkeypatch.setenv("USE_MOCK_CLIENT", "true")

        with patch("runtime.startup.input") as mock_input:
            assert startup.maybe_run_startup_selector() is False

        mock_input.assert_not_called()

    def test_noninteractive_unreachable_backend_does_not_prompt(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("API_BASE_URL=http://missing.local/v1\nMODEL=model-a\n")
        monkeypatch.setattr(startup, "_ENV", env_path)
        monkeypatch.delenv("USE_MOCK_CLIENT", raising=False)

        with patch("runtime.startup.sys.stdin.isatty", return_value=False):
            with patch("runtime.startup._is_reachable", return_value=False):
                with patch("runtime.startup.input") as mock_input:
                    assert startup.maybe_run_startup_selector() is False

        mock_input.assert_not_called()
