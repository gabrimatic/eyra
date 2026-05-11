"""Tests for the Eyra entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import get_log_file_path


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
