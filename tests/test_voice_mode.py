"""Tests for VoiceMode — session state, quit handling, last_task tracking.

All tests mock subprocess calls and process_task_stream to avoid real I/O.
The production VoiceMode class is exercised directly.
"""

import sys
import os
import asyncio
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from chat.session_state import SessionState, InteractionStyle, LastTaskMeta
from modes.voice.voice_mode import VoiceMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubSettings:
    SCREENSHOT_INTERVAL = 1


def _make_mode():
    return VoiceMode(settings=_StubSettings(), session=SessionState())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

class TestStartup:
    def test_no_wh_returns_to_text(self):
        mode = _make_mode()
        with patch("shutil.which", return_value=None):
            result = _run(mode.run())
        assert result == "text"

    def test_sets_interaction_style(self):
        mode = _make_mode()

        async def quit_immediately(self):
            return "quit"

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", quit_immediately):
            result = _run(mode.run())

        assert mode.session.interaction_style == InteractionStyle.VOICE
        assert result == "text"


# ---------------------------------------------------------------------------
# Conversation flow
# ---------------------------------------------------------------------------

class TestConversationFlow:
    def test_user_message_stored_and_last_task_set(self):
        mode = _make_mode()
        call_count = 0

        async def listen(self):
            nonlocal call_count
            call_count += 1
            return "hello there" if call_count == 1 else "quit"

        async def fake_stream(**kwargs):
            yield "hi back"

        async def noop_speak(self, text):
            pass

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream), \
             patch.object(VoiceMode, "_speak", noop_speak):
            _run(mode.run())

        assert mode.session.messages[0] == {"role": "user", "content": "hello there"}
        assert mode.session.messages[1] == {"role": "assistant", "content": "hi back"}
        assert mode.session.last_task == LastTaskMeta("text", "hello there", False)

    def test_multiple_turns_accumulate(self):
        mode = _make_mode()
        turn = 0

        async def listen(self):
            nonlocal turn
            turn += 1
            if turn == 1:
                return "first question"
            elif turn == 2:
                return "second question"
            return "quit"

        async def fake_stream(**kwargs):
            yield f"answer to {kwargs['text_content']}"

        async def noop_speak(self, text):
            pass

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream), \
             patch.object(VoiceMode, "_speak", noop_speak):
            _run(mode.run())

        assert len(mode.session.messages) == 4
        assert mode.session.last_task.text_content == "second question"

    def test_empty_listen_skipped(self):
        mode = _make_mode()
        turn = 0

        async def listen(self):
            nonlocal turn
            turn += 1
            if turn == 1:
                return ""
            elif turn == 2:
                return "real input"
            return "quit"

        async def fake_stream(**kwargs):
            yield "response"

        async def noop_speak(self, text):
            pass

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream), \
             patch.object(VoiceMode, "_speak", noop_speak):
            _run(mode.run())

        assert len(mode.session.messages) == 2
        assert mode.session.messages[0]["content"] == "real input"


# ---------------------------------------------------------------------------
# Quit words
# ---------------------------------------------------------------------------

class TestQuitWords:
    @pytest.mark.parametrize("quit_word", ["/quit", "/exit", "quit", "exit", "stop"])
    def test_quit_words_exit_loop(self, quit_word):
        mode = _make_mode()

        async def listen(self):
            return quit_word

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen):
            result = _run(mode.run())

        assert result == "text"
        assert len(mode.session.messages) == 0


# ---------------------------------------------------------------------------
# Speech output
# ---------------------------------------------------------------------------

class TestSpeech:
    def test_speak_called_with_response(self):
        mode = _make_mode()
        call_count = 0
        speak_calls = []

        async def listen(self):
            nonlocal call_count
            call_count += 1
            return "hello" if call_count == 1 else "quit"

        async def fake_stream(**kwargs):
            yield "world"

        async def track_speak(self, text):
            speak_calls.append(text)

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream), \
             patch.object(VoiceMode, "_speak", track_speak):
            _run(mode.run())

        assert speak_calls == ["world"]

    def test_empty_response_not_stored(self):
        mode = _make_mode()
        call_count = 0

        async def listen(self):
            nonlocal call_count
            call_count += 1
            return "hello" if call_count == 1 else "quit"

        async def fake_stream(**kwargs):
            return
            yield  # noqa: unreachable

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", listen), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream):
            _run(mode.run())

        # user msg stored, but no assistant msg since response was empty
        assert len(mode.session.messages) == 1


# ---------------------------------------------------------------------------
# Error recovery
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    def test_consecutive_listen_failures_exit_loop(self):
        """5 consecutive empty returns from _listen should exit to text mode."""
        mode = _make_mode()

        async def always_fail(self):
            return ""

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", always_fail):
            result = _run(mode.run())

        assert result == "text"
        assert len(mode.session.messages) == 0

    def test_successful_listen_resets_error_count(self):
        """A successful listen between failures should reset the counter."""
        mode = _make_mode()
        call_count = 0

        async def intermittent_fail(self):
            nonlocal call_count
            call_count += 1
            # Fail 4 times, succeed once, fail 4 more, succeed, then quit
            if call_count <= 4:
                return ""
            elif call_count == 5:
                return "real input"
            elif call_count <= 9:
                return ""
            elif call_count == 10:
                return "more input"
            return "quit"

        async def fake_stream(**kwargs):
            yield "response"

        async def noop_speak(self, text):
            pass

        with patch("shutil.which", return_value="/usr/local/bin/wh"), \
             patch.object(VoiceMode, "_listen", intermittent_fail), \
             patch("modes.voice.voice_mode.process_task_stream", side_effect=fake_stream), \
             patch.object(VoiceMode, "_speak", noop_speak):
            _run(mode.run())

        # Should have processed both "real input" and "more input"
        assert len(mode.session.messages) == 4  # 2 user + 2 assistant
