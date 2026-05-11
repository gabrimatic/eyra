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
                    "AGENT_TOOLS_ENABLED=true",
                    "MCP_TOOLS_ENABLED=true",
                    "MCP_CONFIG_PATH=~/mcp.json",
                    "WEB_UI_ENABLED=true",
                    "WEB_UI_HOST=0.0.0.0",
                    "WEB_UI_PORT=9999",
                    "REALTIME_VOICE_ENABLED=true",
                    "REALTIME_MODEL=gpt-realtime-2",
                    "REALTIME_VOICE=marin",
                    "OPENAI_API_KEY=sk-test",
                    "",
                ]
            )
        )
        monkeypatch.setattr(startup, "_ENV", env_path)

        startup._write_env("http://localhost:11434/v1", "ollama", "gemma3:4b")

        content = env_path.read_text()
        assert "OS_TOOLS_ENABLED=true" in content
        assert "AGENT_TOOLS_ENABLED=true" in content
        assert "MCP_TOOLS_ENABLED=true" in content
        assert "WEB_UI_ENABLED=true" in content
        assert "REALTIME_VOICE_ENABLED=true" in content
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
