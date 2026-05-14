"""Tests for the Eyra entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import get_log_file_path
from runtime.cli import _safe_settings, _update_guidance, _version_info
from utils.settings import Settings


class TestLogPath:
    def test_log_path_can_be_overridden(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.log"
        monkeypatch.setenv("EYRA_LOG_FILE", str(custom))

        assert get_log_file_path() == custom

    def test_macos_log_path_is_user_writable(self, monkeypatch):
        monkeypatch.delenv("EYRA_LOG_FILE", raising=False)
        with patch("main.Path.home", return_value=Path("/Users/example")):
            with patch("main.os.uname") as uname:
                uname.return_value.sysname = "Darwin"
                assert get_log_file_path() == Path("/Users/example/Library/Logs/Eyra/eyra.log")


class TestCliSupportCommands:
    def test_safe_settings_report_flags_without_secret_values(self):
        settings = Settings(API_KEY="secret-value", OPENAI_API_KEY="sk-secret-value")

        data = _safe_settings(settings)

        assert data["hasApiKey"] is True
        assert data["hasOpenAiApiKey"] is True
        assert "secret-value" not in str(data)

    def test_update_guidance_preserves_user_data(self):
        result = _update_guidance()

        assert result.ok is True
        assert "never deletes .env, jobs, triggers, logs, or the operation ledger" in result.message

    def test_version_info_is_available_from_source(self):
        info = _version_info()

        assert info["version"]
        assert info["installSource"] in {"source", "homebrew", "uv-tool", "pipx", "wheel", "unknown"}
