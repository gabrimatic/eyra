"""Tests for SessionState, retry metadata, and quality modes."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.session_state import SessionState, QualityMode, InteractionStyle, LastTaskMeta


class TestSessionState:
    def test_initial_state(self):
        s = SessionState()
        assert s.quality_mode == QualityMode.BALANCED
        assert s.interaction_style == InteractionStyle.TEXT
        assert s.messages == []
        assert s.last_task is None
        assert s.watch_active is False
        assert s.watch_voice_muted is False

    def test_clear_resets_everything(self):
        s = SessionState()
        s.messages.append({"role": "user", "content": "hi"})
        s.quality_mode = QualityMode.BEST
        s.watch_active = True
        s.watch_goal = "track errors"
        s.watch_voice_muted = True
        s.last_task = LastTaskMeta("image", "screenshot", False)

        s.clear()

        assert s.messages == []
        assert s.quality_mode == QualityMode.BALANCED
        assert s.watch_active is False
        assert s.watch_goal is None
        assert s.watch_voice_muted is False
        assert s.last_task is None

    def test_status_summary(self):
        s = SessionState()
        summary = s.status_summary()
        assert "balanced" in summary
        assert "messages: 0" in summary

    def test_messages_shared_across_reads(self):
        s = SessionState()
        s.messages.append({"role": "user", "content": "hello"})
        assert len(s.messages) == 1


class TestLastTaskMeta:
    def test_text_task(self):
        meta = LastTaskMeta("text", "explain recursion", False)
        assert meta.task_type == "text"
        assert meta.text_content == "explain recursion"
        assert meta.use_selfie is False

    def test_image_task_selfie(self):
        meta = LastTaskMeta("image", "what do I look like?", True)
        assert meta.task_type == "image"
        assert meta.use_selfie is True


class TestModelSelection:
    """Test select_model with quality mode overrides (imported separately to avoid mss)."""

    def test_quality_mode_overrides(self):
        # Import just the function and its deps
        from chat.complexity_scorer import ComplexityLevel

        # Mock settings with model names
        class FakeSettings:
            SIMPLE_TEXT_MODEL = "small-text"
            MODERATE_TEXT_MODEL = "mid-text"
            SIMPLE_IMAGE_MODEL = "small-image"
            MODERATE_IMAGE_MODEL = "mid-image"
            COMPLEX_MODEL = "big"

        from chat.message_handler import select_model

        settings = FakeSettings()

        # FAST forces simple tier
        assert select_model(ComplexityLevel.COMPLEX, "text", settings, QualityMode.FAST) == "small-text"
        assert select_model(ComplexityLevel.COMPLEX, "image", settings, QualityMode.FAST) == "small-image"

        # BEST forces complex tier
        assert select_model(ComplexityLevel.SIMPLE, "text", settings, QualityMode.BEST) == "big"
        assert select_model(ComplexityLevel.SIMPLE, "image", settings, QualityMode.BEST) == "big"

        # BALANCED uses normal routing
        assert select_model(ComplexityLevel.SIMPLE, "text", settings, QualityMode.BALANCED) == "small-text"
        assert select_model(ComplexityLevel.MODERATE, "image", settings, QualityMode.BALANCED) == "mid-image"
        assert select_model(ComplexityLevel.COMPLEX, "text", settings, QualityMode.BALANCED) == "big"
