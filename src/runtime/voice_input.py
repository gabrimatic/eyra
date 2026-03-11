"""Smart voice input with Silero VAD and Local Whisper transcription.

Records from the microphone using sounddevice. Each 32ms frame (512 samples
at 16 kHz) is scored by Silero's neural voice activity detector running as
an ONNX model. The VADIterator uses hysteresis-based thresholds to emit
discrete speech_start and speech_end events, avoiding the per-frame boolean
noise of older detectors.

Recording starts silently, waits for a speech_start event, captures until
speech_end, then sends the audio to Local Whisper for transcription.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from silero_vad import VADIterator, load_silero_vad

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
FRAME_SAMPLES = 512  # Silero requires exactly 512 samples at 16 kHz
FRAME_MS = 32  # 512 / 16000 * 1000

# Frames of audio to keep before speech onset (captures the attack)
_LOOKBACK_FRAMES = 5

SOCKET_PATH = Path.home() / ".whisper" / "cmd.sock"

# Limit torch to one thread — we do real-time inference on the audio thread
# and don't want contention with sounddevice or the event loop.
torch.set_num_threads(1)


def _int16_to_float32(frame: np.ndarray) -> torch.Tensor:
    """Convert int16 PCM to float32 tensor normalized to [-1, 1]."""
    return torch.from_numpy(frame.astype(np.float32) / 32768.0)


class VoiceInput:
    """Records from microphone with Silero VAD, transcribes via Local Whisper.

    Only captures audio that matters: a small lookback buffer before speech
    onset, the speech itself, and a short tail of trailing silence. Pre-speech
    silence is discarded, keeping memory usage proportional to speech duration.
    """

    def __init__(
        self,
        silence_duration_ms: int = 1500,
        min_speech_ms: int = 250,
        max_duration_s: float = 300,
        threshold: float = 0.6,
    ):
        """Initialize voice input.

        Args:
            silence_duration_ms: Silence after speech before stopping (ms).
            min_speech_ms: Minimum speech duration to accept (ms).
            max_duration_s: Absolute recording cap (seconds).
            threshold: Silero VAD speech probability threshold (0.0-1.0).
                Higher = stricter. End-of-speech uses threshold - 0.15
                (hysteresis built into Silero).
        """
        self.min_speech_frames = int(min_speech_ms / FRAME_MS)
        self.max_frames = int(max_duration_s * 1000 / FRAME_MS)
        self._model = load_silero_vad(onnx=True)
        self._threshold = threshold
        self._silence_duration_ms = silence_duration_ms
        self._cancel = threading.Event()

        # Warm up ONNX model to avoid first-inference latency
        self._model(torch.zeros(FRAME_SAMPLES), SAMPLE_RATE)

    def _new_vad_iterator(self) -> VADIterator:
        """Create a fresh VADIterator for a recording session."""
        return VADIterator(
            self._model,
            threshold=self._threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=self._silence_duration_ms,
            speech_pad_ms=50,
        )

    def cancel(self):
        """Cancel an in-progress recording. Thread-safe."""
        self._cancel.set()

    async def listen(self) -> str | None:
        """Record until speech ends, transcribe, return text. None on silence/cancel."""
        audio = await asyncio.to_thread(self._record)
        if audio is None or len(audio) == 0:
            return None

        wav_path = await asyncio.to_thread(self._save_wav, audio)
        if not wav_path:
            return None

        try:
            return await self._transcribe(wav_path)
        finally:
            try:
                Path(wav_path).unlink()
            except Exception:
                pass

    # ── Recording with Silero VAD ─────────────────────────────────────────

    def _record(self) -> np.ndarray | None:
        """Synchronous mic recording with Silero neural voice activity detection.

        Driven by VADIterator events:
          WAITING (collecting lookback) → speech_start → RECORDING → speech_end → done

        Silero's VADIterator uses hysteresis (threshold - 0.15 for end detection)
        and configurable min_silence_duration_ms, producing stable end-of-speech
        detection without manual silence counting.
        """
        self._cancel.clear()
        vad = self._new_vad_iterator()

        lookback: collections.deque[np.ndarray] = collections.deque(maxlen=_LOOKBACK_FRAMES)
        speech_frames: list[np.ndarray] = []

        speech_started = False
        speech_frame_count = 0

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_SAMPLES,
            ) as stream:
                for _ in range(self.max_frames):
                    if self._cancel.is_set():
                        return None

                    data, _overflowed = stream.read(FRAME_SAMPLES)
                    frame = data[:, 0] if data.ndim > 1 else data.flatten()

                    chunk = _int16_to_float32(frame)
                    event = vad(chunk, return_seconds=False)

                    if event is not None and "start" in event:
                        if not speech_started:
                            speech_started = True
                            speech_frames.extend(lookback)
                            lookback.clear()
                            logger.debug("Speech onset detected")

                    if speech_started:
                        speech_frames.append(frame.copy())
                        speech_frame_count += 1

                        if event is not None and "end" in event:
                            if speech_frame_count >= self.min_speech_frames:
                                logger.debug("Speech ended: %d frames", speech_frame_count)
                                break
                            # Too short, false trigger — reset
                            speech_started = False
                            speech_frames.clear()
                            speech_frame_count = 0
                            vad.reset_states()
                    else:
                        lookback.append(frame.copy())

        except sd.PortAudioError as e:
            logger.error("Microphone error: %s", e)
            return None
        except Exception as e:
            logger.error("Recording error: %s", e)
            return None

        if not speech_started or speech_frame_count < self.min_speech_frames:
            return None

        return np.concatenate(speech_frames)

    # ── WAV export ────────────────────────────────────────────────────────

    @staticmethod
    def _save_wav(audio: np.ndarray) -> str | None:
        try:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # int16 = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
            return path
        except Exception as e:
            logger.error("Failed to save WAV: %s", e)
            return None

    # ── Transcription via Local Whisper ───────────────────────────────────

    async def _transcribe(self, wav_path: str) -> str | None:
        """Transcribe audio file via Local Whisper. Socket first, CLI fallback."""
        try:
            return await self._transcribe_socket(wav_path)
        except Exception as e:
            logger.debug("Socket transcription failed (%s), falling back to CLI", e)
            return await self._transcribe_cli(wav_path)

    async def _transcribe_socket(self, wav_path: str) -> str | None:
        """Send audio to Local Whisper via its Unix command socket."""
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
        try:
            request = {"type": "transcribe", "path": wav_path, "raw": False}
            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()

            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=30)
                if not line:
                    return None
                msg = json.loads(line.decode())
                if msg["type"] == "done":
                    text = msg.get("text", "").strip()
                    return text if text else None
                elif msg["type"] == "error":
                    raise RuntimeError(msg.get("message", "Transcription error"))
                # "started" → keep waiting
        finally:
            writer.close()
            await writer.wait_closed()

    async def _transcribe_cli(self, wav_path: str) -> str | None:
        """Fallback: transcribe via wh CLI subprocess."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wh", "transcribe", wav_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                err = stderr.decode().strip() if stderr else ""
                logger.debug("wh transcribe failed: %s", err)
                return None
            text = stdout.decode().strip()
            return text if text else None
        except asyncio.TimeoutError:
            logger.debug("wh transcribe timed out")
            return None
        except Exception as e:
            logger.debug("wh transcribe error: %s", e)
            return None
