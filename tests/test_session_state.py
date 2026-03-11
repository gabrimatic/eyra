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
