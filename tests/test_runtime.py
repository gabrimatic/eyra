"""Tests for the runtime layer: models, speech controller, live session commands."""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.models import LiveRuntimeState, PreflightResult, RuntimeStatus
from runtime.speech_controller import SpeechController


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# PreflightResult and LiveRuntimeState.from_preflight()
# ---------------------------------------------------------------------------

class TestPreflightResult:
    def test_defaults(self):
        r = PreflightResult()
        assert r.backend_reachable is False
        assert r.models_ready == []
        assert r.models_missing == []
        assert r.wh_available is False
        assert r.listening_available is None
        assert r.speech_available is None
        assert r.wh_bin is None
        assert r.screen_capture_available is False

    def test_from_preflight_backend_and_models_ready(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            models_missing=[],
            wh_available=True,
            wh_bin="/usr/local/bin/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.backend_ready is True
        assert state.listening_enabled is True
        assert state.speech_enabled is True
        assert state.wh_bin == "/usr/local/bin/wh"

    def test_from_preflight_missing_models_disables_backend(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            models_missing=["model-b"],
            wh_available=True,
            wh_bin="/usr/local/bin/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.backend_ready is False

    def test_from_preflight_no_wh_disables_listening_and_speech(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            wh_available=False,
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.listening_enabled is False
        assert state.speech_enabled is False
        assert state.wh_bin is None

    def test_from_preflight_wh_available_enables_both(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            wh_available=True,
            wh_bin="/opt/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.listening_enabled is True
        assert state.speech_enabled is True
        assert state.wh_bin == "/opt/wh"

    def test_from_preflight_speech_only_capability(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            wh_available=True,
            listening_available=False,
            speech_available=True,
            wh_bin="/opt/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.listening_enabled is False
        assert state.speech_enabled is True
        assert state.wh_bin == "/opt/wh"

    def test_from_preflight_listening_only_capability(self):
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["model-a"],
            wh_available=True,
            listening_available=True,
            speech_available=False,
            wh_bin="/opt/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.listening_enabled is True
        assert state.speech_enabled is False
        assert state.wh_bin == "/opt/wh"


# ---------------------------------------------------------------------------
# SpeechController mute / cooldown
# ---------------------------------------------------------------------------

class TestSpeechController:
    def _make_controller(self, cooldown_ms=3000, wh_bin="/opt/test/wh"):
        state = LiveRuntimeState()
        state.speech_enabled = True
        state.speech_muted = False
        state.wh_bin = wh_bin
        return SpeechController(state, cooldown_ms=cooldown_ms), state

    def test_speak_does_nothing_when_muted(self):
        ctrl, state = self._make_controller()
        state.speech_muted = True

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            _run(ctrl.speak("hello"))
            mock_exec.assert_not_called()

    def test_speak_does_nothing_when_speech_disabled(self):
        ctrl, state = self._make_controller()
        state.speech_enabled = False

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            _run(ctrl.speak("hello"))
            mock_exec.assert_not_called()

    def test_speak_skips_empty_text(self):
        ctrl, state = self._make_controller()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            _run(ctrl.speak("   "))
            mock_exec.assert_not_called()

    def test_cooldown_prevents_rapid_speech(self):
        ctrl, state = self._make_controller(cooldown_ms=60_000)
        state.last_spoken_output_at = time.time()  # just spoke

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            _run(ctrl.speak("too soon"))
            mock_exec.assert_not_called()

    def test_speak_allowed_after_cooldown(self):
        ctrl, state = self._make_controller(cooldown_ms=100)
        state.last_spoken_output_at = time.time() - 10.0  # 10s ago

        mock_proc = MagicMock()
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            _run(ctrl.speak("now it works"))
            assert state.last_spoken_output_at is not None
            # speak() is non-blocking: launches process and returns
            assert ctrl._speaking_proc is mock_proc
            # Verify the resolved wh_bin path is used, not bare "wh"
            mock_exec.assert_called_once()
            assert mock_exec.call_args[0][0] == "/opt/test/wh"

    def test_speak_is_non_blocking(self):
        """speak() launches wh whisper and returns without waiting."""
        ctrl, state = self._make_controller(cooldown_ms=0)

        mock_proc = MagicMock()
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            _run(ctrl.speak("hello"))
            # Process is stored but not awaited
            assert ctrl._speaking_proc is mock_proc
            assert ctrl.is_speaking is True

    def test_wait_for_speech_awaits_process(self):
        """wait_for_speech() blocks until the speech process finishes."""
        ctrl, state = self._make_controller()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        ctrl._speaking_proc = mock_proc

        _run(ctrl.wait_for_speech())
        mock_proc.wait.assert_called_once()
        assert ctrl._speaking_proc is None

    def test_listen_does_not_wait_for_speech_before_recording(self):
        """listen() keeps the microphone path active while TTS is speaking."""
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        mock_speech_proc = MagicMock()
        mock_speech_proc.returncode = None
        mock_speech_proc.wait = AsyncMock(return_value=0)
        ctrl._speaking_proc = mock_speech_proc

        async def fake_listen(on_speech_start=None):
            mock_speech_proc.wait.assert_not_called()
            return "hello world"

        voice_input = MagicMock()
        voice_input.listen = fake_listen
        with patch.object(ctrl, "_get_voice_input", return_value=voice_input):
            result = _run(ctrl.listen())
            assert result == "hello world"

    def test_listen_interrupts_speech_when_user_starts_talking(self):
        """Voice onset during TTS schedules an immediate speech interrupt."""
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        mock_speech_proc = MagicMock()
        mock_speech_proc.returncode = None
        mock_speech_proc.terminate = MagicMock()
        mock_speech_proc.wait = AsyncMock(return_value=0)
        ctrl._speaking_proc = mock_speech_proc

        async def fake_listen(on_speech_start=None):
            assert on_speech_start is not None
            on_speech_start()
            await asyncio.sleep(0)
            return "stop please"

        voice_input = MagicMock()
        voice_input.listen = fake_listen

        with patch.object(ctrl, "_get_voice_input", return_value=voice_input):
            result = _run(ctrl.listen())

        assert result == "stop please"
        mock_speech_proc.terminate.assert_called_once()
        assert ctrl._speaking_proc is None

    def test_listen_does_not_interrupt_speech_on_silence(self):
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        mock_speech_proc = MagicMock()
        mock_speech_proc.returncode = None
        mock_speech_proc.terminate = MagicMock()
        mock_speech_proc.wait = AsyncMock(return_value=0)
        ctrl._speaking_proc = mock_speech_proc

        async def fake_listen(on_speech_start=None):
            assert on_speech_start is not None
            return None

        voice_input = MagicMock()
        voice_input.listen = fake_listen

        with patch.object(ctrl, "_get_voice_input", return_value=voice_input):
            result = _run(ctrl.listen())

        assert result is None
        mock_speech_proc.terminate.assert_not_called()
        assert ctrl._speaking_proc is mock_speech_proc

    def test_interrupt_terminates_process(self):
        ctrl, state = self._make_controller()

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        ctrl._speaking_proc = mock_proc

        _run(ctrl.interrupt())
        mock_proc.terminate.assert_called_once()
        assert ctrl._speaking_proc is None

    def test_interrupt_kills_process_when_terminate_hangs(self):
        ctrl, state = self._make_controller()

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        ctrl._speaking_proc = mock_proc

        _run(ctrl.interrupt())

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert ctrl._speaking_proc is None

    def test_listen_returns_none_when_disabled(self):
        ctrl, state = self._make_controller()
        state.listening_enabled = False

        result = _run(ctrl.listen())
        assert result is None

    def test_listen_delegates_to_voice_input(self):
        """listen() delegates to VoiceInput and returns its result."""
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        voice_input = MagicMock()
        voice_input.listen = AsyncMock(return_value="test speech")
        with patch.object(ctrl, "_get_voice_input", return_value=voice_input):
            result = _run(ctrl.listen())
            assert result == "test speech"
            voice_input.listen.assert_called_once()

    def test_listen_returns_none_on_silence(self):
        """listen() returns None when VoiceInput detects no speech."""
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        voice_input = MagicMock()
        voice_input.listen = AsyncMock(return_value=None)
        with patch.object(ctrl, "_get_voice_input", return_value=voice_input):
            result = _run(ctrl.listen())
            assert result is None

    def test_cancel_listen_cancels_voice_input(self):
        """cancel_listen() forwards to VoiceInput.cancel()."""
        ctrl, _ = self._make_controller()
        voice_input = MagicMock()
        ctrl._voice_input = voice_input
        ctrl.cancel_listen()
        voice_input.cancel.assert_called_once()

    def test_disabled_controller_does_not_initialize_voice_input(self):
        ctrl, state = self._make_controller()
        state.listening_enabled = False

        with patch.object(ctrl, "_get_voice_input") as mock_get_voice:
            result = _run(ctrl.listen())

        assert result is None
        mock_get_voice.assert_not_called()

    def test_voice_init_failure_disables_listening(self):
        ctrl, state = self._make_controller()
        state.listening_enabled = True

        with patch("runtime.voice_input.load_silero_vad", side_effect=RuntimeError("vad unavailable")):
            result = _run(ctrl.listen())

        assert result is None
        assert state.listening_enabled is False


# ---------------------------------------------------------------------------
# LiveSession command handling
# ---------------------------------------------------------------------------

class TestLiveSessionCommands:
    def _make_session(self):
        from chat.complexity_scorer import ComplexityScorer
        from runtime.live_session import LiveSession
        from runtime.models import LiveRuntimeState, PreflightResult
        from utils.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.SPEECH_COOLDOWN_MS = 3000
        settings.VOICE_SILENCE_MS = 1500
        settings.VOICE_VAD_THRESHOLD = 0.6
        settings.FILESYSTEM_ALLOWED_PATHS = "~,/tmp"
        settings.COMPLEXITY_ROUTING_ENABLED = False
        settings.NETWORK_TOOLS_ENABLED = False

        preflight = PreflightResult(backend_reachable=True, models_ready=["m"])
        state = LiveRuntimeState.from_preflight(preflight)
        scorer = MagicMock(spec=ComplexityScorer)

        session = LiveSession(
            settings=settings,
            preflight=preflight,
            state=state,
            complexity_scorer=scorer,
        )
        return session

    def test_mute_command(self):
        session = self._make_session()
        _run(session._handle_command("/mute"))
        assert session.state.speech_muted is True

    def test_unmute_command(self):
        session = self._make_session()
        session.state.speech_muted = True
        _run(session._handle_command("/unmute"))
        assert session.state.speech_muted is False

    def test_voice_test_speaks_diagnostic_when_speech_enabled(self, capsys):
        session = self._make_session()
        session.state.speech_enabled = True
        session.speech = MagicMock()
        session.speech.speak = AsyncMock()

        assert _run(session._handle_command("/voice-test")) is True

        session.speech.speak.assert_called_once()
        assert "Voice interruption test started" in capsys.readouterr().out

    def test_goal_command_sets_goal(self):
        session = self._make_session()
        _run(session._handle_command("/goal track all errors"))
        assert session.state.current_goal == "track all errors"

    def test_goal_command_no_arg_prints_current(self, capsys):
        session = self._make_session()
        session.state.current_goal = "existing goal"
        _run(session._handle_command("/goal"))
        captured = capsys.readouterr()
        assert "existing goal" in captured.out

    def test_quit_command_sets_shutdown(self):
        session = self._make_session()
        _run(session._handle_command("/quit"))
        assert session._shutdown.is_set()

    def test_clear_command_resets_history(self):
        session = self._make_session()
        session.state.conversation_messages.append({"role": "user", "content": "hello"})
        _run(session._handle_command("/clear"))
        assert session.state.conversation_messages == []

    def test_unknown_command_returns_true(self):
        session = self._make_session()
        result = _run(session._handle_command("/notacommand"))
        assert result is True

    def test_network_tools_disabled_by_default(self):
        session = self._make_session()
        names = {
            tool["function"]["name"]
            for tool in session._tool_registry.to_openai_tools(include_costly=True)
        }
        assert "get_current_time" in names
        assert "take_screenshot" in names
        assert "get_weather" not in names
        assert "web_search" not in names

    def test_network_tools_enabled_registers_remote_tools(self):
        session = self._make_session()
        session.settings.NETWORK_TOOLS_ENABLED = True
        session._tool_registry = session._build_tool_registry()

        names = {
            tool["function"]["name"]
            for tool in session._tool_registry.to_openai_tools(include_costly=True)
        }
        assert "get_weather" in names
        assert "web_search" in names
        assert "open_url" in names

    def test_fast_mode_explains_when_routing_is_disabled(self, capsys):
        session = self._make_session()
        _run(session._handle_command("/mode fast"))
        out = capsys.readouterr().out
        assert "complexity routing" in out.lower()
        assert session.quality_mode.value == "balanced"


# ---------------------------------------------------------------------------
# Fix verification: config flags applied to runtime state
# ---------------------------------------------------------------------------

class TestConfigFlagsApplied:
    def test_flags_disabled_overrides_preflight(self):
        """LIVE_*_ENABLED=false should disable capabilities even if preflight detected them."""
        from utils.settings import Settings
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["m"],
            wh_available=True,
            wh_bin="/opt/wh",
            screen_capture_available=True,
        )
        settings = MagicMock(spec=Settings)
        settings.LIVE_LISTENING_ENABLED = False
        settings.LIVE_SPEECH_ENABLED = False

        state = LiveRuntimeState.from_preflight(r, settings=settings)
        assert state.listening_enabled is False
        assert state.speech_enabled is False
        # wh_bin is still set even when user disables voice via config
        assert state.wh_bin == "/opt/wh"

    def test_flags_enabled_respects_preflight(self):
        """LIVE_*_ENABLED=true should still respect hardware availability."""
        from utils.settings import Settings
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["m"],
            wh_available=False,
        )
        settings = MagicMock(spec=Settings)
        settings.LIVE_LISTENING_ENABLED = True
        settings.LIVE_SPEECH_ENABLED = True

        state = LiveRuntimeState.from_preflight(r, settings=settings)
        # wh not available, so these stay False regardless of config
        assert state.listening_enabled is False
        assert state.speech_enabled is False
        assert state.wh_bin is None

    def test_no_settings_backward_compatible(self):
        """Omitting settings should work the same as before."""
        r = PreflightResult(
            backend_reachable=True,
            models_ready=["m"],
            wh_available=True,
            wh_bin="/opt/wh",
        )
        state = LiveRuntimeState.from_preflight(r)
        assert state.listening_enabled is True
        assert state.speech_enabled is True
        assert state.wh_bin == "/opt/wh"


# ---------------------------------------------------------------------------
# Fix verification: screen context detection
# ---------------------------------------------------------------------------

class TestScreenContextDetection:
    def _make_session(self):
        from chat.complexity_scorer import ComplexityScorer
        from runtime.live_session import LiveSession
        from utils.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.SPEECH_COOLDOWN_MS = 3000
        settings.VOICE_SILENCE_MS = 1500
        settings.VOICE_VAD_THRESHOLD = 0.6
        settings.FILESYSTEM_ALLOWED_PATHS = "~,/tmp"
        settings.COMPLEXITY_ROUTING_ENABLED = False

        preflight = PreflightResult(backend_reachable=True, models_ready=["m"])
        state = LiveRuntimeState.from_preflight(preflight)
        scorer = MagicMock(spec=ComplexityScorer)

        return LiveSession(settings=settings, preflight=preflight, state=state, complexity_scorer=scorer)

    def test_screen_keywords_need_context(self):
        session = self._make_session()
        assert session._needs_screen_context("what is this error on screen?") is True
        assert session._needs_screen_context("look at this") is True
        assert session._needs_screen_context("what is that button?") is True
        assert session._needs_screen_context("read the text on the screen") is True
        assert session._needs_screen_context("what's on the screen?") is True
        assert session._needs_screen_context("show me the dialog") is True

    def test_knowledge_questions_skip_screen(self):
        session = self._make_session()
        assert session._needs_screen_context("What is a binary search tree?") is False
        assert session._needs_screen_context("How does Python garbage collection work?") is False
        assert session._needs_screen_context("Explain the difference between TCP and UDP") is False

    def test_trivial_input_skips_screen(self):
        session = self._make_session()
        assert session._needs_screen_context("hi") is False
        assert session._needs_screen_context("thanks") is False

    def test_bare_pronouns_skip_screen(self):
        """Bare pronouns like this/that/here/there should NOT trigger screen capture."""
        session = self._make_session()
        assert session._needs_screen_context("hello there") is False
        assert session._needs_screen_context("that makes sense") is False
        assert session._needs_screen_context("this is interesting") is False
        assert session._needs_screen_context("I agree with that") is False
        assert session._needs_screen_context("here is my question") is False

    def test_generic_error_mention_skips_screen(self):
        """Non-visual error mentions should not trigger screen capture."""
        session = self._make_session()
        assert session._needs_screen_context("I got an error in my code") is False
        assert session._needs_screen_context("there is a warning in the logs") is False

    def test_visual_error_reference_needs_screen(self):
        """Error in a visual context (dialog, popup) should trigger capture."""
        session = self._make_session()
        assert session._needs_screen_context("the error dialog appeared") is True
        assert session._needs_screen_context("see this popup") is True
        assert session._needs_screen_context("explain this code") is True

    def test_action_verbs_without_visual_object_skip_screen(self):
        """Phrases like 'show me how X works' should NOT trigger screen capture."""
        session = self._make_session()
        assert session._needs_screen_context("show me how binary search works") is False
        assert session._needs_screen_context("look at how this algorithm behaves") is False
        assert session._needs_screen_context("read the article to me") is False
        assert session._needs_screen_context("show me an example of recursion") is False
        assert session._needs_screen_context("produce a detailed page-by-page PDF summary") is False

    def test_action_verbs_with_visual_object_need_screen(self):
        """Phrases like 'show me the dialog' should trigger screen capture."""
        session = self._make_session()
        assert session._needs_screen_context("show me the dialog") is True
        assert session._needs_screen_context("look at this") is True
        assert session._needs_screen_context("read the text on the screen") is True
        assert session._needs_screen_context("show me that window") is True


# ---------------------------------------------------------------------------
# Fix verification: initial status and header text
# ---------------------------------------------------------------------------

class TestInitialStatusAndHeader:
    def _make_session(self, listening=False, speech=False):
        from chat.complexity_scorer import ComplexityScorer
        from runtime.live_session import LiveSession
        from utils.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.LIVE_LISTENING_ENABLED = listening
        settings.LIVE_SPEECH_ENABLED = speech
        settings.SPEECH_COOLDOWN_MS = 3000
        settings.VOICE_SILENCE_MS = 1500
        settings.VOICE_VAD_THRESHOLD = 0.6
        settings.FILESYSTEM_ALLOWED_PATHS = "~,/tmp"

        preflight = PreflightResult(
            backend_reachable=True, models_ready=["m"],
            wh_available=listening or speech,
            listening_available=listening,
            speech_available=speech,
            wh_bin="/opt/wh" if (listening or speech) else None,
        )
        state = LiveRuntimeState.from_preflight(preflight, settings=settings)
        scorer = MagicMock(spec=ComplexityScorer)
        session = LiveSession(settings=settings, preflight=preflight, state=state, complexity_scorer=scorer)
        return session, state

    def test_initial_status_is_idle(self):
        session, state = self._make_session()
        # LiveSession starts STARTING before run() is called
        assert state.current_status == RuntimeStatus.STARTING

    def test_header_says_type_only_when_listening_off(self, capsys):
        from runtime.status_presenter import render_header
        _, state = self._make_session(listening=False)
        render_header(state)
        out = capsys.readouterr().out
        assert "Type anything." in out

    def test_header_says_type_or_speak_when_listening_on(self, capsys):
        from runtime.status_presenter import render_header
        _, state = self._make_session(listening=True)
        render_header(state)
        out = capsys.readouterr().out
        assert "Type anything or speak." in out

    def test_voice_label_preserves_muted_speech_with_listening(self):
        from runtime.status_presenter import voice_status_label
        _, state = self._make_session(listening=True, speech=True)
        state.speech_muted = True
        assert voice_status_label(state) == "input + muted speech"


# ---------------------------------------------------------------------------
# /voice command
# ---------------------------------------------------------------------------

class TestVoiceCommand:
    def _make_session(self, wh_available=True, listening=True, speech=True):
        from chat.complexity_scorer import ComplexityScorer
        from runtime.live_session import LiveSession
        from utils.settings import Settings

        settings = MagicMock(spec=Settings)
        settings.LIVE_LISTENING_ENABLED = listening
        settings.LIVE_SPEECH_ENABLED = speech
        settings.SPEECH_COOLDOWN_MS = 3000
        settings.VOICE_SILENCE_MS = 1500
        settings.VOICE_VAD_THRESHOLD = 0.6
        settings.FILESYSTEM_ALLOWED_PATHS = "~,/tmp"
        settings.COMPLEXITY_ROUTING_ENABLED = False

        preflight = PreflightResult(
            backend_reachable=True, models_ready=["m"],
            wh_available=wh_available,
            listening_available=wh_available if listening else False,
            speech_available=wh_available if speech else False,
            wh_bin="/opt/wh" if wh_available else None,
        )
        state = LiveRuntimeState.from_preflight(preflight, settings=settings)
        scorer = MagicMock(spec=ComplexityScorer)
        session = LiveSession(settings=settings, preflight=preflight, state=state, complexity_scorer=scorer)
        return session, state

    def test_voice_off_disables_both(self):
        session, state = self._make_session()
        assert state.listening_enabled is True
        assert state.speech_enabled is True
        _run(session._handle_command("/voice off"))
        assert state.listening_enabled is False
        assert state.speech_enabled is False

    def test_voice_on_enables_both(self):
        session, state = self._make_session()
        _run(session._handle_command("/voice off"))
        _run(session._handle_command("/voice on"))
        assert state.listening_enabled is True
        assert state.speech_enabled is True

    def test_voice_on_recovers_when_startup_skipped_voice(self):
        async def run():
            session, state = self._make_session(wh_available=False, listening=False, speech=False)

            async def idle_voice_loop():
                return None

            session._voice_input_loop = idle_voice_loop

            async def check_local_whisper(_manager, result, **_kwargs):
                result.wh_bin = "/opt/wh"
                result.wh_available = True
                result.listening_available = True
                result.speech_available = True
                return True

            with patch("runtime.live_session.PreflightManager.check_local_whisper", check_local_whisper):
                await session._handle_command("/voice on")

            assert state.listening_enabled is True
            assert state.speech_enabled is True
            assert state.wh_bin == "/opt/wh"
            assert session.preflight.wh_available is True
            assert session.preflight.listening_available is True
            assert session.preflight.speech_available is True

        _run(run())

    def test_voice_on_enables_speech_only_when_asr_is_unavailable(self, capsys):
        session, state = self._make_session(wh_available=False, listening=False, speech=False)

        async def check_local_whisper(_manager, result, **_kwargs):
            result.wh_bin = "/opt/wh"
            result.wh_available = True
            result.listening_available = False
            result.speech_available = True
            return True

        with patch("runtime.live_session.PreflightManager.check_local_whisper", check_local_whisper):
            _run(session._handle_command("/voice on"))

        assert state.listening_enabled is False
        assert state.speech_enabled is True
        assert state.wh_bin == "/opt/wh"
        out = capsys.readouterr().out
        assert "speech only" in out.lower()

    def test_voice_off_cancels_owned_voice_task(self):
        async def run():
            session, state = self._make_session()

            async def idle_voice_loop():
                await asyncio.sleep(60)

            session._voice_input_loop = idle_voice_loop
            await session._handle_command("/voice off")
            await session._handle_command("/voice on")

            task = session._voice_task
            assert task is not None
            assert task.done() is False

            await session._handle_command("/voice off")

            assert state.listening_enabled is False
            assert state.speech_enabled is False
            assert task.done() is True

        _run(run())

    def test_voice_on_blocked_without_wh(self, capsys):
        session, state = self._make_session(wh_available=False, listening=False, speech=False)
        assert state.listening_enabled is False

        async def check_local_whisper(_manager, result, **_kwargs):
            result.wh_bin = None
            result.wh_available = False
            result.listening_available = False
            result.speech_available = False
            return False

        with patch("runtime.live_session.PreflightManager.check_local_whisper", check_local_whisper):
            _run(session._handle_command("/voice on"))

        assert state.listening_enabled is False
        assert state.speech_enabled is False
        out = capsys.readouterr().out
        assert "not available" in out.lower()

    def test_voice_no_arg_shows_status(self, capsys):
        session, state = self._make_session()
        _run(session._handle_command("/voice"))
        out = capsys.readouterr().out
        assert "on" in out
        assert "/voice on|off" in out
