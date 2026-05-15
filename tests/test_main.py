"""Tests for the Eyra entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import get_log_file_path
from runtime.cli import (
    _is_source_checkout,
    _paths,
    _primary_env_path,
    _safe_settings,
    _setup,
    _uninstall,
    _update_guidance,
    _version_info,
)
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

    def test_uninstall_removes_shims_and_shell_lines_without_data(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        shim = bin_dir / "eyra"
        shim.write_text("#!/bin/bash\n")
        zshrc = home / ".zshrc"
        zshrc.write_text(
            'export PATH="$HOME/.local/bin:$PATH" # eyra\n'
            "alias eyra='/tmp/old-eyra' # eyra\n"
            "export KEEP_ME=true\n"
        )
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)

        result = _uninstall(dry_run=False, assume_yes=True, with_data=False)

        assert result.ok is True
        assert not shim.exists()
        assert "KEEP_ME" in zshrc.read_text()
        assert "# eyra" not in zshrc.read_text()
        assert (home / ".config" / "eyra").exists() is False

    def test_source_checkout_with_git_uses_repo_env(self, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("")
        (repo / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: repo)

        assert _is_source_checkout(repo) is True
        assert _primary_env_path() == repo / ".env"

    def test_release_app_without_git_uses_user_config_env(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)

        assert _is_source_checkout(app) is False
        assert _primary_env_path() == home / ".config" / "eyra" / ".env"

    def test_settings_loads_cwd_env_only_for_git_source_checkout(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        app = tmp_path / "app"
        (home / ".config" / "eyra").mkdir(parents=True)
        (home / ".config" / "eyra" / ".env").write_text("MODEL=user-model\n")
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        (app / ".env").write_text("MODEL=app-model\n")
        monkeypatch.chdir(app)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.delenv("MODEL", raising=False)

        assert Settings.load_from_env().MODEL == "user-model"

        (app / ".git").mkdir()
        assert Settings.load_from_env().MODEL == "app-model"

    def test_environment_variables_override_user_config(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        (home / ".config" / "eyra").mkdir(parents=True)
        (home / ".config" / "eyra" / ".env").write_text("MODEL=user-model\n")
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.setenv("MODEL", "env-model")

        assert Settings.load_from_env().MODEL == "env-model"

    def test_setup_preserves_existing_user_config_for_installed_app(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        (app / ".env.example").write_text("MODEL=example-model\n")
        existing = home / ".config" / "eyra" / ".env"
        existing.parent.mkdir(parents=True)
        existing.write_text("MODEL=keep-me\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)

        result = _setup(non_interactive=True)

        assert result.ok is True
        assert existing.read_text() == "MODEL=keep-me\n"
        assert result.data["preservedEnv"] is True
        assert _paths()["env"] == str(existing)
