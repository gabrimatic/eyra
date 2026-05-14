"""Tests for session state enums and model selection."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.session_state import InteractionStyle, QualityMode


class TestEnums:
    def test_quality_modes(self):
        assert QualityMode.FAST.value == "fast"
        assert QualityMode.BALANCED.value == "balanced"
        assert QualityMode.BEST.value == "best"

    def test_interaction_styles(self):
        assert InteractionStyle.TEXT.value == "text"
        assert InteractionStyle.VOICE.value == "voice"

    def test_quality_mode_from_string(self):
        assert QualityMode("fast") == QualityMode.FAST
        assert QualityMode("balanced") == QualityMode.BALANCED
        assert QualityMode("best") == QualityMode.BEST


class TestModelSelection:
    """Test select_model with quality mode overrides."""

    def test_quality_mode_overrides(self):
        from chat.complexity_scorer import ComplexityLevel

        class FakeSettings:
            SIMPLE_MODEL = "small"
            MODERATE_MODEL = "mid"
            MODEL = "big"

        from chat.message_handler import select_model

        settings = FakeSettings()

        # FAST forces simple tier
        assert select_model(ComplexityLevel.COMPLEX, settings, QualityMode.FAST) == "small"

        # BEST forces default model
        assert select_model(ComplexityLevel.SIMPLE, settings, QualityMode.BEST) == "big"

        # BALANCED uses normal routing
        assert select_model(ComplexityLevel.SIMPLE, settings, QualityMode.BALANCED) == "small"
        assert select_model(ComplexityLevel.MODERATE, settings, QualityMode.BALANCED) == "mid"
        assert select_model(ComplexityLevel.COMPLEX, settings, QualityMode.BALANCED) == "big"


class TestSettingsRoutingDefaults:
    def test_route_debug_is_off_by_default(self):
        from utils.settings import Settings

        settings = Settings()

        assert settings.ROUTING_DEBUG is False

    def test_load_from_env_reads_user_config_before_source_checkout_env(self, monkeypatch, tmp_path):
        from utils.settings import Settings

        home = tmp_path / "home"
        config = home / ".config" / "eyra"
        config.mkdir(parents=True)
        (config / ".env").write_text("MODEL=user-config-model\nNETWORK_TOOLS_ENABLED=true\n")
        cwd = tmp_path / "cwd"
        (cwd / "src").mkdir(parents=True)
        (cwd / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        (cwd / "src" / "main.py").write_text("")
        (cwd / ".env").write_text("MODEL=cwd-model\n")
        monkeypatch.delenv("MODEL", raising=False)
        monkeypatch.delenv("NETWORK_TOOLS_ENABLED", raising=False)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.chdir(cwd)

        settings = Settings.load_from_env()

        assert settings.MODEL == "cwd-model"
        assert settings.NETWORK_TOOLS_ENABLED is True

    def test_load_from_env_ignores_unrelated_cwd_env(self, monkeypatch, tmp_path):
        from utils.settings import Settings

        home = tmp_path / "home"
        config = home / ".config" / "eyra"
        config.mkdir(parents=True)
        (config / ".env").write_text("MODEL=user-config-model\nNETWORK_TOOLS_ENABLED=true\n")
        cwd = tmp_path / "project"
        cwd.mkdir()
        (cwd / ".env").write_text("MODEL=unrelated-project-model\nNETWORK_TOOLS_ENABLED=false\n")
        monkeypatch.delenv("MODEL", raising=False)
        monkeypatch.delenv("NETWORK_TOOLS_ENABLED", raising=False)
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.chdir(cwd)

        settings = Settings.load_from_env()

        assert settings.MODEL == "user-config-model"
        assert settings.NETWORK_TOOLS_ENABLED is True

    def test_process_environment_overrides_env_files(self, monkeypatch, tmp_path):
        from utils.settings import Settings

        home = tmp_path / "home"
        config = home / ".config" / "eyra"
        config.mkdir(parents=True)
        (config / ".env").write_text("LIVE_LISTENING_ENABLED=true\n")
        cwd = tmp_path / "cwd"
        (cwd / "src").mkdir(parents=True)
        (cwd / "pyproject.toml").write_text('[project]\nname = "eyra"\n')
        (cwd / "src" / "main.py").write_text("")
        (cwd / ".env").write_text("LIVE_LISTENING_ENABLED=true\n")
        monkeypatch.setattr("utils.settings.Path.home", lambda: home)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("LIVE_LISTENING_ENABLED", "false")

        settings = Settings.load_from_env()

        assert settings.LIVE_LISTENING_ENABLED is False
