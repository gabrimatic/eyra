"""Tests for LiveMode (watch mode) — session state, deduplication, error handling.

All tests mock prepare_image, process_task_stream, and subprocess calls
to avoid real I/O. The production LiveMode class is exercised directly.
"""

import sys
import os
import asyncio
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.session_state import SessionState, InteractionStyle, LastTaskMeta
from modes.live_mode import LiveMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubSettings:
    SCREENSHOT_INTERVAL = 0


_image_counter = 0


def _make_mode(goal="track errors"):
    session = SessionState()
    session.watch_goal = goal
    session.watch_active = True
    session.watch_voice_muted = True

    with patch("shutil.which", return_value=None):
        mode = LiveMode(settings=_StubSettings(), session=session)
    return mode


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _unique_image_factory():
    """Returns a prepare_image mock that yields a unique image each call."""
    counter = 0
    async def fake_prepare(**kwargs):
        nonlocal counter
        counter += 1
        return f"base64_image_{counter}"
    return fake_prepare


def _static_image_factory(image="same_image"):
    """Returns a prepare_image mock that always returns the same image."""
    async def fake_prepare(**kwargs):
        return image
    return fake_prepare


# ---------------------------------------------------------------------------
# Session state setup
# ---------------------------------------------------------------------------

class TestRunSessionState:
    def test_run_sets_interaction_style_and_last_task(self):
        mode = _make_mode(goal="check for errors")

        async def fake_stream(**kwargs):
            mode.session.watch_active = False
            yield "response"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            result = _run(mode.run())

        assert result == "text"
        assert mode.session.interaction_style == InteractionStyle.WATCH
        assert mode.session.last_task == LastTaskMeta("image", "check for errors", False)
        assert mode.session.watch_active is False

    def test_run_prompts_for_goal_when_missing(self):
        session = SessionState()
        session.watch_voice_muted = True

        with patch("shutil.which", return_value=None):
            mode = LiveMode(settings=_StubSettings(), session=session)

        async def fake_stream(**kwargs):
            mode.session.watch_active = False
            yield "ok"

        async def mock_to_thread(func, *args):
            return "my custom goal"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream), \
             patch("modes.live_mode.asyncio.to_thread", side_effect=mock_to_thread):
            result = _run(mode.run())

        assert session.watch_goal == "my custom goal"

    def test_run_uses_default_goal_on_empty_input(self):
        session = SessionState()
        session.watch_voice_muted = True

        with patch("shutil.which", return_value=None):
            mode = LiveMode(settings=_StubSettings(), session=session)

        async def fake_stream(**kwargs):
            mode.session.watch_active = False
            yield "ok"

        async def mock_to_thread(func, *args):
            return ""

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream), \
             patch("modes.live_mode.asyncio.to_thread", side_effect=mock_to_thread):
            _run(mode.run())

        assert session.watch_goal == "Describe what is on the screen in one sentence."


# ---------------------------------------------------------------------------
# Watch loop behavior
# ---------------------------------------------------------------------------

class TestWatchLoop:
    def test_new_response_appended_to_messages(self):
        mode = _make_mode()

        async def fake_stream(**kwargs):
            mode.session.watch_active = False
            yield "new insight"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            _run(mode._watch_loop("track errors"))

        # User goal + assistant response
        assert len(mode.session.messages) == 2
        assert mode.session.messages[0] == {"role": "user", "content": "track errors"}
        assert mode.session.messages[1] == {"role": "assistant", "content": "new insight"}

    def test_unchanged_screen_skips_model_call(self):
        """When screenshot hash is unchanged, the model should not be called."""
        mode = _make_mode()
        model_calls = 0
        iteration = 0

        async def fake_stream(**kwargs):
            nonlocal model_calls
            model_calls += 1
            mode.session.watch_active = False
            yield "response"

        async def noop_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                mode.session.watch_active = False

        # Same image every time — model should only be called once (first time)
        with patch("modes.live_mode.prepare_image", _static_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream), \
             patch("modes.live_mode.asyncio.sleep", side_effect=noop_sleep):
            _run(mode._watch_loop("track errors"))

        assert model_calls == 1

    def test_duplicate_response_suppressed_on_changed_screen(self):
        """Different screenshots but same model output — only first should be stored."""
        mode = _make_mode()
        model_calls = 0

        async def fake_stream(**kwargs):
            nonlocal model_calls
            model_calls += 1
            if model_calls >= 3:
                mode.session.watch_active = False
            yield "same response"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            _run(mode._watch_loop("track errors"))

        # Model called 3 times (different images), but response stored only once
        assert model_calls == 3
        assert len(mode.session.messages) == 2  # 1 user goal + 1 assistant

    def test_changed_screen_calls_model_each_time(self):
        mode = _make_mode()
        model_calls = 0

        async def fake_stream(**kwargs):
            nonlocal model_calls
            model_calls += 1
            if model_calls >= 3:
                mode.session.watch_active = False
            yield f"response {model_calls}"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            _run(mode._watch_loop("track errors"))

        assert model_calls == 3

    def test_empty_response_not_stored(self):
        mode = _make_mode()

        async def fake_stream(**kwargs):
            mode.session.watch_active = False
            yield "   "

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            _run(mode._watch_loop("track errors"))

        assert len(mode.session.messages) == 0

    def test_shared_history_passed_to_stream(self):
        mode = _make_mode(goal="watch goal")
        mode.session.messages.append({"role": "user", "content": "prior context"})

        captured_kwargs = {}

        async def fake_stream(**kwargs):
            captured_kwargs.update(kwargs)
            mode.session.watch_active = False
            yield "ok"

        with patch("modes.live_mode.prepare_image", _unique_image_factory()), \
             patch("modes.live_mode.process_task_stream", side_effect=fake_stream):
            _run(mode._watch_loop("watch goal"))

        msgs = captured_kwargs["messages"]
        assert msgs[0] == {"role": "user", "content": "prior context"}
        assert msgs[-1] == {"role": "user", "content": "watch goal"}
        # Pre-captured image passed through
        assert captured_kwargs["base64_image"].startswith("base64_image_")

    def test_three_consecutive_errors_stops_loop(self):
        mode = _make_mode()

        async def failing_prepare(**kwargs):
            raise RuntimeError("screenshot failed")

        async def noop_sleep(seconds):
            pass

        with patch("modes.live_mode.prepare_image", side_effect=failing_prepare), \
             patch("modes.live_mode.asyncio.sleep", side_effect=noop_sleep):
            _run(mode._watch_loop("track errors"))

        assert len(mode.session.messages) == 0


# ---------------------------------------------------------------------------
# Voice muting
# ---------------------------------------------------------------------------

class TestWatchVoiceMuting:
    def test_wh_missing_forces_mute(self):
        session = SessionState()
        session.watch_voice_muted = False

        with patch("shutil.which", return_value=None):
            mode = LiveMode(settings=_StubSettings(), session=session)

        assert mode.wh_available is False
        assert session.watch_voice_muted is True

    def test_wh_present_preserves_mute_setting(self):
        session = SessionState()
        session.watch_voice_muted = False

        with patch("shutil.which", return_value="/usr/local/bin/wh"):
            mode = LiveMode(settings=_StubSettings(), session=session)

        assert mode.wh_available is True
        assert session.watch_voice_muted is False
