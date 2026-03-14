"""Tests for the smart voice input module (Silero VAD)."""

import asyncio
import os
import sys
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.voice_input import (
    CHANNELS,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    VoiceInput,
    _LOOKBACK_FRAMES,
)


def _run(coro):
    return asyncio.run(coro)


def _mock_stream():
    """Create a mock sounddevice InputStream."""
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    return stream


def _make_vi(**kwargs):
    """Create a VoiceInput with mocked Silero model loading."""
    with patch("runtime.voice_input.load_silero_vad") as mock_load:
        mock_model = MagicMock()
        mock_model.return_value = MagicMock()  # warmup call
        mock_load.return_value = mock_model
        return VoiceInput(**kwargs)


def _mock_vad_events(events_by_frame):
    """Create a mock VADIterator that fires events at specific call counts.

    events_by_frame: dict mapping 1-based frame number to event dict.
    e.g. {3: {"start": 1536}, 10: {"end": 5120}}
    """
    call_count = 0

    def vad_call(chunk, return_seconds=False):
        nonlocal call_count
        call_count += 1
        return events_by_frame.get(call_count)

    vad_call.reset_states = MagicMock()
    return vad_call


# ---------------------------------------------------------------------------
# VAD recording logic
# ---------------------------------------------------------------------------

class TestVoiceInputVAD:
    def test_cancel_stops_recording(self):
        """Setting cancel event should make _record return None."""
        vi = _make_vi()
        vi._cancel.set()

        stream = _mock_stream()
        stream.read = MagicMock(return_value=(np.zeros((FRAME_SAMPLES, 1), dtype=np.int16), False))

        mock_vad = _mock_vad_events({})
        with patch("runtime.voice_input.sd.InputStream", return_value=stream):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                assert vi._record() is None

    def test_no_speech_returns_none(self):
        """When VAD never detects speech, _record returns None."""
        vi = _make_vi(max_duration_s=0.3)

        stream = _mock_stream()
        stream.read = MagicMock(return_value=(np.zeros((FRAME_SAMPLES, 1), dtype=np.int16), False))

        mock_vad = _mock_vad_events({})  # never fires start
        with patch("runtime.voice_input.sd.InputStream", return_value=stream):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                assert vi._record() is None

    def test_speech_then_silence_returns_audio(self):
        """Speech start followed by speech end should return recorded audio."""
        vi = _make_vi(min_speech_ms=60, max_duration_s=5)

        speech_start_frame = 3
        # End must come after enough frames to meet min_speech_frames
        speech_end_frame = speech_start_frame + vi.min_speech_frames + 2

        stream = _mock_stream()
        stream.read = MagicMock(
            return_value=(np.full((FRAME_SAMPLES, 1), 1000, dtype=np.int16), False)
        )

        mock_vad = _mock_vad_events({
            speech_start_frame: {"start": speech_start_frame * FRAME_SAMPLES},
            speech_end_frame: {"end": speech_end_frame * FRAME_SAMPLES},
        })

        with patch("runtime.voice_input.sd.InputStream", return_value=stream):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                result = vi._record()
                assert result is not None
                assert len(result) > 0

    def test_only_speech_frames_captured(self):
        """Pre-speech silence should not be in the output (only lookback)."""
        vi = _make_vi(min_speech_ms=60, max_duration_s=5)

        waiting_frames = 20
        speech_start_frame = waiting_frames + 1
        speech_count = vi.min_speech_frames + 2
        speech_end_frame = speech_start_frame + speech_count

        stream = _mock_stream()
        stream.read = MagicMock(
            return_value=(np.full((FRAME_SAMPLES, 1), 500, dtype=np.int16), False)
        )

        mock_vad = _mock_vad_events({
            speech_start_frame: {"start": speech_start_frame * FRAME_SAMPLES},
            speech_end_frame: {"end": speech_end_frame * FRAME_SAMPLES},
        })

        with patch("runtime.voice_input.sd.InputStream", return_value=stream):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                result = vi._record()
                assert result is not None
                # Should have lookback + speech frames, NOT all the waiting frames
                max_expected = (_LOOKBACK_FRAMES + speech_count + 1) * FRAME_SAMPLES
                assert len(result) <= max_expected

    def test_false_trigger_resets(self):
        """A speech burst shorter than min_speech_ms is ignored."""
        vi = _make_vi(min_speech_ms=150, max_duration_s=5)

        # Start at frame 2, end at frame 3 (too short for 150ms min)
        call_count = 0

        def mock_vad(chunk, return_seconds=False):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return {"start": 2 * FRAME_SAMPLES}
            if call_count == 3:
                return {"end": 3 * FRAME_SAMPLES}
            if call_count > 30:
                vi._cancel.set()
            return None

        mock_vad.reset_states = MagicMock()

        stream = _mock_stream()
        stream.read = MagicMock(
            return_value=(np.full((FRAME_SAMPLES, 1), 500, dtype=np.int16), False)
        )

        with patch("runtime.voice_input.sd.InputStream", return_value=stream):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                assert vi._record() is None
                mock_vad.reset_states.assert_called()

    def test_mic_error_returns_none(self):
        """PortAudioError during recording returns None."""
        vi = _make_vi()

        mock_vad = _mock_vad_events({})
        with patch("runtime.voice_input.sd.InputStream", side_effect=sd.PortAudioError("No mic")):
            with patch.object(vi, "_new_vad_iterator", return_value=mock_vad):
                assert vi._record() is None


# ---------------------------------------------------------------------------
# WAV export
# ---------------------------------------------------------------------------

class TestVoiceInputWAV:
    def test_save_wav_creates_valid_file(self):
        vi = _make_vi()
        audio = np.random.randint(-1000, 1000, size=SAMPLE_RATE, dtype=np.int16)
        path = vi._save_wav(audio)
        assert path is not None

        with wave.open(path, "rb") as wf:
            assert wf.getnchannels() == CHANNELS
            assert wf.getframerate() == SAMPLE_RATE
            assert wf.getsampwidth() == 2
            assert wf.getnframes() == SAMPLE_RATE

        os.unlink(path)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

class TestVoiceInputTranscription:
    def test_transcribe_socket_success(self):
        vi = _make_vi()

        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_reader.readline = AsyncMock(side_effect=[
            b'{"type": "started", "action": "transcribe"}\n',
            b'{"type": "done", "text": "hello world", "success": true}\n',
        ])

        with patch("runtime.voice_input.asyncio.open_unix_connection", new_callable=AsyncMock, return_value=(mock_reader, mock_writer)):
            assert _run(vi._transcribe_socket("/tmp/test.wav")) == "hello world"

    def test_transcribe_socket_error_falls_back_to_cli(self):
        vi = _make_vi()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello from cli", b""))

        with patch("runtime.voice_input.asyncio.open_unix_connection", side_effect=ConnectionRefusedError):
            with patch("runtime.voice_input.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                assert _run(vi._transcribe("/tmp/test.wav")) == "hello from cli"

    def test_cli_fallback_uses_resolved_wh_bin(self):
        """CLI transcription must use the resolved wh binary path, not bare 'wh'."""
        vi = _make_vi(wh_bin="/opt/custom/wh")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"resolved path works", b""))

        with patch("runtime.voice_input.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            assert _run(vi._transcribe_cli("/tmp/test.wav")) == "resolved path works"
            mock_exec.assert_called_once()
            assert mock_exec.call_args[0][0] == "/opt/custom/wh"

    def test_listen_returns_none_on_no_speech(self):
        vi = _make_vi()
        with patch.object(vi, "_record", return_value=None):
            assert _run(vi.listen()) is None

    def test_listen_full_pipeline(self):
        vi = _make_vi()
        fake_audio = np.random.randint(-1000, 1000, size=SAMPLE_RATE, dtype=np.int16)

        with patch.object(vi, "_record", return_value=fake_audio):
            with patch.object(vi, "_transcribe", new_callable=AsyncMock, return_value="test result"):
                assert _run(vi.listen()) == "test result"


# Need sd imported for PortAudioError in test
import sounddevice as sd  # noqa: E402
