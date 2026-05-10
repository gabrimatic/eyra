"""Tests for first-run provider setup."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime import startup


class TestStartupSelector:
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
