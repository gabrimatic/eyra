"""Tests for the Eyra entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import get_log_file_path
from runtime.cli import (
    _detect_install_source,
    _find_menu_bar_resource,
    _is_source_checkout,
    _launch_menu_bar,
    _logs,
    _paths,
    _primary_env_path,
    _safe_settings,
    _setup,
    _uninstall,
    _update_guidance,
    _version_info,
    cli,
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
        assert info["installSource"] in {"source", "managed-install", "homebrew", "uv-tool", "pipx", "wheel", "unknown"}

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

    def test_homebrew_install_source_is_detected_from_venv_path(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr(sys, "argv", ["/opt/homebrew/opt/eyra/bin/eyra"])
        monkeypatch.setattr(sys, "prefix", "/opt/homebrew/var/eyra/venv")
        monkeypatch.setattr(sys, "base_prefix", "/opt/homebrew/opt/python@3.11")

        assert _detect_install_source()["kind"] == "homebrew"

    def test_managed_install_source_is_detected_from_install_sh_app_venv(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        managed_home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr(sys, "argv", [str(managed_home / ".local" / "share" / "eyra" / "app" / ".venv" / "bin" / "eyra")])
        monkeypatch.setattr(sys, "executable", str(managed_home / ".local" / "share" / "eyra" / "app" / ".venv" / "bin" / "python"))
        monkeypatch.setattr(sys, "prefix", str(managed_home / ".local" / "share" / "eyra" / "app" / ".venv"))
        monkeypatch.setattr(sys, "base_prefix", "/opt/homebrew/opt/python@3.11")

        assert _detect_install_source()["kind"] == "managed-install"

    def test_pipx_install_source_is_detected_before_wheel_fallback(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        pipx_home = tmp_path / "custom-pipx-home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "bin" / "eyra")])
        monkeypatch.setattr(sys, "prefix", str(pipx_home / "venvs" / "eyra"))
        monkeypatch.setattr(sys, "base_prefix", "/Library/Frameworks/Python.framework/Versions/3.11")
        monkeypatch.setenv("PIPX_HOME", str(pipx_home))

        assert _detect_install_source()["kind"] == "pipx"

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

    def test_examples_command_shows_setup_and_use_cases(self, capsys):
        exit_code = cli(["examples"])

        output = capsys.readouterr().out
        assert exit_code == 0
        assert "Eyra examples" in output
        assert "eyra setup" in output
        assert "What would leave my machine?" in output
        assert "Plain requests are fine" in output

    def test_settings_list_redacts_secret_values(self, monkeypatch, tmp_path, capsys):
        app = tmp_path / "app"
        home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        env = home / ".config" / "eyra" / ".env"
        env.parent.mkdir(parents=True)
        env.write_text("API_KEY=secret-value\nOPENAI_API_KEY=sk-secret\nMODEL=test-model\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MODEL", raising=False)

        exit_code = cli(["settings", "--json"])
        output = capsys.readouterr().out

        assert exit_code == 0
        assert "secret-value" not in output
        assert "sk-secret" not in output
        assert "configured" in output

    def test_settings_set_validates_bool_and_preserves_file(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        existing = home / ".config" / "eyra" / ".env"
        existing.parent.mkdir(parents=True)
        existing.write_text("# keep\nMODEL=old-model\nLIVE_SPEECH_ENABLED=true\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)

        exit_code = cli(["settings", "set", "LIVE_SPEECH_ENABLED", "false"])

        assert exit_code == 0
        text = existing.read_text()
        assert "# keep" in text
        assert "LIVE_SPEECH_ENABLED=false" in text

    def test_settings_set_rejects_secret_edits(self, monkeypatch, tmp_path):
        app = tmp_path / "app"
        home = tmp_path / "home"
        (app / "src").mkdir(parents=True)
        (app / "src" / "main.py").write_text("")
        (app / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        monkeypatch.setattr("runtime.cli._repo_root", lambda: app)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: home)

        exit_code = cli(["settings", "set", "API_KEY", "secret"])

        assert exit_code == 1

    def test_logs_reports_paths_without_dumping_content(self):
        result = _logs(open_folder=False)

        assert result.ok is True
        assert "App log:" in result.message
        assert "Do not share" in result.message

    def test_menu_bar_source_checkout_resource_is_discovered(self, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        package = repo / "apps" / "EyraMenuBar"
        package.mkdir(parents=True)
        (package / "Package.swift").write_text("// swift package\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: repo)
        monkeypatch.setattr("runtime.cli._package_menu_bar_resource_path", lambda: None)

        resource = _find_menu_bar_resource()

        assert resource.resource_available is True
        assert resource.mode == "source"
        assert resource.path == package
        assert resource.swift_required is True

    def test_menu_bar_package_resource_is_discovered_for_installed_wheel(self, monkeypatch, tmp_path):
        repo = tmp_path / "install-root"
        package = tmp_path / "site-packages" / "runtime" / "resources" / "EyraMenuBar"
        package.mkdir(parents=True)
        (package / "Package.swift").write_text("// swift package\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: repo)
        monkeypatch.setattr("runtime.cli._package_menu_bar_resource_path", lambda: package)

        resource = _find_menu_bar_resource()

        assert resource.resource_available is True
        assert resource.mode == "package-resource"
        assert resource.path == package

    def test_menu_bar_missing_resource_returns_installed_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr("runtime.cli._repo_root", lambda: tmp_path / "missing")
        monkeypatch.setattr("runtime.cli._package_menu_bar_resource_path", lambda: None)
        monkeypatch.setattr("runtime.cli.Path.home", lambda: tmp_path / "home")

        result = _launch_menu_bar()

        assert result.ok is False
        assert result.data["available"] is False
        assert result.data["mode"] == "unavailable"
        assert result.data["fallbackCommand"] == "eyra open"
        assert "eyra open" in result.message

    def test_menu_bar_without_swift_reports_clear_developer_preview_fallback(self, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        package = repo / "apps" / "EyraMenuBar"
        package.mkdir(parents=True)
        (package / "Package.swift").write_text("// swift package\n")
        monkeypatch.setattr("runtime.cli._repo_root", lambda: repo)
        monkeypatch.setattr("runtime.cli._package_menu_bar_resource_path", lambda: None)
        monkeypatch.setattr("runtime.cli.shutil.which", lambda command: None if command == "swift" else "/usr/bin/tool")

        result = _launch_menu_bar()

        assert result.ok is False
        assert result.data["resourceAvailable"] is True
        assert result.data["available"] is False
        assert result.data["mode"] == "source"
        assert result.data["swiftRequired"] is True
        assert result.data["swiftAvailable"] is False
        assert result.data["fallbackCommand"] == "eyra open"
        assert "developer preview" in result.message
