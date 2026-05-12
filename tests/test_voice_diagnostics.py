"""Tests for voice diagnostics and microphone configuration."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.voice_input import FRAME_SAMPLES, VoiceInput
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


def _mock_stream(frames: list[np.ndarray], overflow: bool = False):
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.read = MagicMock(side_effect=[(frame.reshape(-1, 1), overflow) for frame in frames])
    return stream


def _make_vi(**kwargs):
    with patch("runtime.voice_input.load_silero_vad") as mock_load:
        mock_model = MagicMock()
        mock_model.return_value = MagicMock()
        mock_load.return_value = mock_model
        return VoiceInput(**kwargs)


def test_settings_loads_voice_diagnostic_configuration(monkeypatch):
    monkeypatch.setenv("VOICE_INPUT_DEVICE", "Mac mini Microphone")
    monkeypatch.setenv("VOICE_SAMPLE_RATE", "16000")
    monkeypatch.setenv("VOICE_DEBUG_RECORD_SECONDS", "2")
    monkeypatch.setenv("VOICE_DIAGNOSTIC_SAVE_AUDIO", "true")

    settings = Settings.load_from_env()

    assert settings.VOICE_INPUT_DEVICE == "Mac mini Microphone"
    assert settings.VOICE_SAMPLE_RATE == 16000
    assert settings.VOICE_DEBUG_RECORD_SECONDS == 2
    assert settings.VOICE_DIAGNOSTIC_SAVE_AUDIO is True


def test_voice_input_passes_selected_device_to_sounddevice():
    vi = _make_vi(input_device="External Mic")
    vi._cancel.set()
    stream = _mock_stream([np.zeros(FRAME_SAMPLES, dtype=np.int16)])

    with patch("runtime.voice_input.sd.InputStream", return_value=stream) as mock_stream:
        vi._record()

    assert mock_stream.call_args.kwargs["device"] == "External Mic"


def test_resolve_input_device_by_index_and_name():
    from runtime.voice_diagnostics import resolve_input_device

    devices = [
        {"index": 0, "name": "Speaker", "max_input_channels": 0},
        {"index": 1, "name": "Studio Mic", "max_input_channels": 1},
        {"index": 2, "name": "USB Headset", "max_input_channels": 2},
    ]

    assert resolve_input_device("2", devices) == 2
    assert resolve_input_device("studio", devices) == 1
    assert resolve_input_device("", devices) is None


def test_resolve_input_device_raises_for_missing_device():
    from runtime.voice_diagnostics import resolve_input_device

    devices = [{"index": 1, "name": "Studio Mic", "max_input_channels": 1}]

    try:
        resolve_input_device("missing", devices)
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("missing device should fail clearly")


def test_microphone_diagnostic_reports_all_zero_audio():
    from runtime.voice_diagnostics import VoiceDiagnostics

    settings = Settings(VOICE_DEBUG_RECORD_SECONDS=1)
    frames = [np.zeros(FRAME_SAMPLES, dtype=np.int16) for _ in range(2)]
    stream = _mock_stream(frames)

    with patch("runtime.voice_diagnostics.sd.query_devices", return_value=[]):
        with patch("runtime.voice_diagnostics.sd.check_input_settings"):
            with patch("runtime.voice_diagnostics.sd.InputStream", return_value=stream):
                report = VoiceDiagnostics(settings=settings, wh_bin="/opt/wh").run_microphone_checks()

    assert report.check("captured_audio").status == "failed"
    assert "silent/all-zero" in report.check("captured_audio").reason


def test_microphone_diagnostic_reports_nonzero_audio_and_overflow():
    from runtime.voice_diagnostics import VoiceDiagnostics

    settings = Settings(VOICE_DEBUG_RECORD_SECONDS=1)
    frames = [np.full(FRAME_SAMPLES, 1200, dtype=np.int16) for _ in range(2)]
    stream = _mock_stream(frames, overflow=True)

    with patch("runtime.voice_diagnostics.sd.query_devices", return_value=[]):
        with patch("runtime.voice_diagnostics.sd.check_input_settings"):
            with patch("runtime.voice_diagnostics.sd.InputStream", return_value=stream):
                report = VoiceDiagnostics(settings=settings, wh_bin="/opt/wh").run_microphone_checks()

    assert report.check("captured_audio").status == "passed"
    assert report.check("stream_overflow").status == "failed"


def test_microphone_diagnostic_skips_vad_when_capture_has_no_speech():
    from runtime.voice_diagnostics import VoiceDiagnostics

    settings = Settings(VOICE_DEBUG_RECORD_SECONDS=1)
    frames = [np.full(FRAME_SAMPLES, 1200, dtype=np.int16) for _ in range(2)]
    stream = _mock_stream(frames)

    class FakeVoiceInput:
        def __init__(self, **_kwargs):
            pass

        def _new_vad_iterator(self):
            return lambda *_args, **_kwargs: None

    with patch("runtime.voice_diagnostics.sd.query_devices", return_value=[]):
        with patch("runtime.voice_diagnostics.sd.check_input_settings"):
            with patch("runtime.voice_diagnostics.sd.InputStream", return_value=stream):
                with patch("runtime.voice_diagnostics.VoiceInput", FakeVoiceInput):
                    report = VoiceDiagnostics(settings=settings, wh_bin="/opt/wh").run_microphone_checks()

    assert report.check("captured_audio").status == "passed"
    assert report.check("vad_detected_speech").status == "skipped"
    assert "needs live speech" in report.check("vad_detected_speech").reason


def test_local_whisper_diagnostic_skips_transcription_when_vad_skipped():
    from runtime.voice_diagnostics import VoiceDiagnostics

    settings = Settings(VOICE_DEBUG_RECORD_SECONDS=1)
    frames = [np.full(FRAME_SAMPLES, 1200, dtype=np.int16) for _ in range(2)]
    stream = _mock_stream(frames)

    class FakeVoiceInput:
        def __init__(self, **_kwargs):
            pass

        def _new_vad_iterator(self):
            return lambda *_args, **_kwargs: None

    def fake_run(argv, **_kwargs):
        if argv[1] == "status":
            return SimpleNamespace(returncode=0, stdout="running", stderr="")
        if argv[1] == "transcribe":
            return SimpleNamespace(returncode=0, stdout="", stderr="Empty transcription")
        raise AssertionError(f"unexpected command: {argv}")

    diagnostics = VoiceDiagnostics(settings=settings, wh_bin="/opt/wh")
    with patch("runtime.voice_diagnostics.sd.query_devices", return_value=[]):
        with patch("runtime.voice_diagnostics.sd.check_input_settings"):
            with patch("runtime.voice_diagnostics.sd.InputStream", return_value=stream):
                with patch("runtime.voice_diagnostics.VoiceInput", FakeVoiceInput):
                    diagnostics.run_microphone_checks()

    with patch("runtime.voice_diagnostics.subprocess.run", side_effect=fake_run):
        report = diagnostics.run_local_whisper_checks()

    assert report.check("transcription_returns_text").status == "skipped"
    assert "No detected speech" in report.check("transcription_returns_text").reason


def test_voice_diagnose_command_prints_structured_report(capsys):
    from chat.complexity_scorer import ComplexityScorer
    from runtime.live_session import LiveSession
    from runtime.models import LiveRuntimeState, PreflightResult
    from runtime.voice_diagnostics import DiagnosticCheck, DiagnosticReport

    settings = MagicMock(spec=Settings)
    settings.SPEECH_COOLDOWN_MS = 3000
    settings.VOICE_SILENCE_MS = 1500
    settings.VOICE_VAD_THRESHOLD = 0.6
    settings.VOICE_INPUT_DEVICE = ""
    settings.VOICE_SAMPLE_RATE = 16000
    settings.VOICE_DEBUG_RECORD_SECONDS = 1
    settings.VOICE_DIAGNOSTIC_SAVE_AUDIO = False
    settings.FILESYSTEM_ALLOWED_PATHS = "~,/tmp"
    settings.COMPLEXITY_ROUTING_ENABLED = False
    settings.NETWORK_TOOLS_ENABLED = False

    preflight = PreflightResult(backend_reachable=True, models_ready=["m"], wh_bin="/opt/wh")
    state = LiveRuntimeState.from_preflight(preflight)
    session = LiveSession(settings=settings, preflight=preflight, state=state, complexity_scorer=MagicMock(spec=ComplexityScorer))

    report = DiagnosticReport(
        title="Voice diagnostics",
        checks=[DiagnosticCheck("input_devices", "passed", "1 input device found")],
    )
    with patch("runtime.live_session.VoiceDiagnostics") as diagnostics:
        diagnostics.return_value.run = AsyncMock(return_value=report)
        assert _run(session._handle_command("/voice-diagnose")) is True

    out = capsys.readouterr().out
    assert "Voice diagnostics" in out
    assert "input_devices" in out
    assert "passed" in out
