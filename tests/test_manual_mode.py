"""Tests for manual mode command parsing, image marker normalization, and retry behavior.

All tests call the real production methods on ManualMode — no reimplementations.
A minimal ManualMode instance is constructed with a mock settings object and
no complexity scorer, which is enough to exercise all parsing and state logic.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from chat.session_state import SessionState, QualityMode, LastTaskMeta
from modes.manual_mode import ManualMode, TASK_SHORTCUTS


# ---------------------------------------------------------------------------
# Fixture: real ManualMode with a lightweight stub settings
# ---------------------------------------------------------------------------

class _StubSettings:
    """Minimal settings object to satisfy ManualMode.__init__."""
    SCREENSHOT_INTERVAL = 1


@pytest.fixture
def mode():
    session = SessionState()
    return ManualMode(settings=_StubSettings(), session=session)


# ---------------------------------------------------------------------------
# normalize_image_input (static method on ManualMode)
# ---------------------------------------------------------------------------

class TestNormalizeImageInput:
    def test_bare_image(self):
        prompt, use_selfie = ManualMode.normalize_image_input("#image")
        assert prompt == "Describe this image."
        assert use_selfie is False

    def test_bare_selfie(self):
        prompt, use_selfie = ManualMode.normalize_image_input("#selfie")
        assert prompt == "Describe this image."
        assert use_selfie is True

    def test_uppercase_image(self):
        prompt, _ = ManualMode.normalize_image_input("#IMAGE")
        assert prompt == "Describe this image."

    def test_mixed_case(self):
        prompt, _ = ManualMode.normalize_image_input("#Image")
        assert prompt == "Describe this image."

    def test_uppercase_selfie(self):
        prompt, use_selfie = ManualMode.normalize_image_input("#SELFIE")
        assert prompt == "Describe this image."
        assert use_selfie is True

    def test_text_with_marker(self):
        prompt, _ = ManualMode.normalize_image_input("what is this #image")
        assert "#image" not in prompt.lower()
        assert "what is this" in prompt

    def test_text_with_uppercase_marker(self):
        prompt, _ = ManualMode.normalize_image_input("please #IMAGE explain this")
        assert "#image" not in prompt.lower()
        assert "please" in prompt
        assert "explain this" in prompt

    def test_selfie_with_text(self):
        prompt, use_selfie = ManualMode.normalize_image_input("how do I look #selfie")
        assert use_selfie is True
        assert "how do I look" in prompt
        assert "#selfie" not in prompt.lower()


# ---------------------------------------------------------------------------
# resolve_shortcut (instance method on ManualMode)
# ---------------------------------------------------------------------------

class TestResolveShortcut:
    def test_known_shortcut(self, mode):
        result = mode.resolve_shortcut("#explain")
        assert result is not None
        prompt, uses_image = result
        assert prompt == TASK_SHORTCUTS["#explain"][0]
        assert uses_image is True

    def test_shortcut_with_extra_text(self, mode):
        result = mode.resolve_shortcut("#explain focus on the sidebar")
        prompt, uses_image = result
        assert prompt.endswith("focus on the sidebar")
        assert prompt.startswith(TASK_SHORTCUTS["#explain"][0])

    def test_non_shortcut_returns_none(self, mode):
        assert mode.resolve_shortcut("hello world") is None
        assert mode.resolve_shortcut("#image") is None

    def test_all_shortcuts_resolve(self, mode):
        for key in TASK_SHORTCUTS:
            result = mode.resolve_shortcut(key)
            assert result is not None, f"{key} should resolve"


# ---------------------------------------------------------------------------
# prepare_retry / finish_retry (instance methods on ManualMode)
# ---------------------------------------------------------------------------

class TestRetry:
    def test_no_last_task(self, mode):
        assert mode.prepare_retry() is None

    def test_empty_text_content(self, mode):
        mode.session.last_task = LastTaskMeta("text", "", False)
        assert mode.prepare_retry() is None

    def test_appends_fresh_user_turn(self, mode):
        mode.session.messages.append({"role": "user", "content": "hello"})
        mode.session.messages.append({"role": "assistant", "content": "hi"})
        mode.session.last_task = LastTaskMeta("text", "hello", False)

        meta = mode.prepare_retry()

        assert meta is not None
        assert meta.text_content == "hello"
        assert meta.task_type == "text"
        # Fresh user turn appended — last message is the retry prompt
        assert mode.session.messages[-1] == {"role": "user", "content": "hello"}
        assert len(mode.session.messages) == 3

    def test_image_retry_preserves_metadata(self, mode):
        mode.session.last_task = LastTaskMeta("image", "Describe this image.", True)

        meta = mode.prepare_retry()

        assert meta.task_type == "image"
        assert meta.use_selfie is True
        assert meta.text_content == "Describe this image."

    def test_force_best_overrides_quality_mode(self, mode):
        mode.session.quality_mode = QualityMode.BALANCED
        mode.session.last_task = LastTaskMeta("text", "hello", False)

        mode.prepare_retry(force_best=True)
        assert mode.session.quality_mode == QualityMode.BEST

        mode.finish_retry(force_best=True)
        assert mode.session.quality_mode == QualityMode.BALANCED

    def test_no_force_best_leaves_mode_unchanged(self, mode):
        mode.session.quality_mode = QualityMode.FAST
        mode.session.last_task = LastTaskMeta("text", "hello", False)

        mode.prepare_retry(force_best=False)
        assert mode.session.quality_mode == QualityMode.FAST

        mode.finish_retry(force_best=False)
        assert mode.session.quality_mode == QualityMode.FAST

    def test_retry_after_shortcut_uses_expanded_prompt(self, mode):
        expanded = TASK_SHORTCUTS["#explain"][0]
        mode.session.last_task = LastTaskMeta("image", expanded, False)

        meta = mode.prepare_retry()

        assert meta.text_content == expanded
        assert "#explain" not in meta.text_content
        assert meta.task_type == "image"


# ---------------------------------------------------------------------------
# _handle_mode_command (instance method)
# ---------------------------------------------------------------------------

class TestModeCommand:
    def test_set_fast(self, mode):
        mode._handle_mode_command("/mode fast")
        assert mode.session.quality_mode == QualityMode.FAST

    def test_set_best(self, mode):
        mode._handle_mode_command("/mode best")
        assert mode.session.quality_mode == QualityMode.BEST

    def test_set_balanced(self, mode):
        mode.session.quality_mode = QualityMode.FAST
        mode._handle_mode_command("/mode balanced")
        assert mode.session.quality_mode == QualityMode.BALANCED

    def test_invalid_mode(self, mode):
        mode.session.quality_mode = QualityMode.BALANCED
        mode._handle_mode_command("/mode turbo")
        # Should stay unchanged
        assert mode.session.quality_mode == QualityMode.BALANCED
