"""Speech output (TTS) and voice input (VAD + ASR) via Local Whisper."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time

from runtime.models import LiveRuntimeState

logger = logging.getLogger(__name__)


class SpeechController:
    def __init__(
        self,
        state: LiveRuntimeState,
        cooldown_ms: int = 3000,
        silence_duration_ms: int = 1500,
        vad_threshold: float = 0.6,
        input_device: str | int | None = None,
        sample_rate: int = 16000,
    ):
        self.state = state
        self.cooldown_s = cooldown_ms / 1000.0
        self._silence_duration_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
        self._input_device = input_device
        self._sample_rate = sample_rate
        self._speaking_proc: asyncio.subprocess.Process | None = None
        self._voice_input = None

    def _get_voice_input(self):
        """Create the microphone/VAD pipeline only when voice input is actually used."""
        if self._voice_input is not None:
            return self._voice_input
        try:
            from runtime.voice_input import VoiceInput

            self._voice_input = VoiceInput(
                silence_duration_ms=self._silence_duration_ms,
                threshold=self._vad_threshold,
                wh_bin=self.state.wh_bin,
                input_device=self._input_device,
                sample_rate=self._sample_rate,
            )
        except Exception as e:
            logger.debug("Voice input initialization failed: %s", e)
            self.state.listening_enabled = False
            return None
        return self._voice_input

    @property
    def is_speaking(self) -> bool:
        return self._speaking_proc is not None and self._speaking_proc.returncode is None

    async def speak(self, text: str):
        """Speak text via wh whisper. Non-blocking: launches process and returns.
        Use wait_for_speech() or interrupt() to manage the process lifecycle."""
        if not self.state.speech_enabled or self.state.speech_muted:
            return
        if not text.strip():
            return

        # Cooldown
        now = time.time()
        if self.state.last_spoken_output_at:
            if now - self.state.last_spoken_output_at < self.cooldown_s:
                return

        await self.interrupt()

        wh = self.state.wh_bin or "wh"
        try:
            self._speaking_proc = await asyncio.create_subprocess_exec(
                wh, "whisper", text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.state.last_spoken_output_at = time.time()
        except Exception as e:
            logger.debug("Speech launch failed: %s", e)
            self._speaking_proc = None

    async def wait_for_speech(self):
        """Wait for ongoing speech to finish. Safe to call when nothing is playing."""
        if self._speaking_proc is not None:
            try:
                await self._speaking_proc.wait()
            except Exception:
                pass
            self._speaking_proc = None

    async def wait_for_speech_or_barge_in(self, spoken_text: str) -> str | None:
        """Wait for TTS while listening for human barge-in.

        Returns a transcript when speech starts over TTS and does not look like
        echo of the spoken output. Returns None when TTS finishes normally or
        the captured audio looks self-generated.
        """
        if not self.is_speaking:
            await self.wait_for_speech()
            return None
        if not self.state.listening_enabled:
            await self.wait_for_speech()
            return None

        listen_task = asyncio.create_task(self.listen(), name="tts-barge-in-listen")
        speech_task = asyncio.create_task(self.wait_for_speech(), name="tts-complete")
        done, pending = await asyncio.wait({listen_task, speech_task}, return_when=asyncio.FIRST_COMPLETED)
        if speech_task in done:
            self.cancel_listen()
            listen_task.cancel()
            await asyncio.gather(listen_task, return_exceptions=True)
            return None

        speech_task.cancel()
        await asyncio.gather(speech_task, return_exceptions=True)
        transcript = await listen_task
        if not transcript:
            if self.is_speaking:
                await self.wait_for_speech()
            return None
        if _looks_like_echo(transcript, spoken_text):
            return None
        return transcript

    async def interrupt(self):
        """Stop any ongoing speech immediately."""
        if self._speaking_proc and self._speaking_proc.returncode is None:
            try:
                self._speaking_proc.terminate()
                await asyncio.wait_for(self._speaking_proc.wait(), timeout=1)
            except Exception:
                try:
                    self._speaking_proc.kill()
                except Exception:
                    pass
        self._speaking_proc = None

    async def listen(self) -> str | None:
        """Listen via smart VAD recording + Local Whisper transcription.
        Returns transcribed text, or None on silence/cancel."""
        if not self.state.listening_enabled:
            return None

        voice_input = self._get_voice_input()
        if voice_input is None:
            return None

        if not self.is_speaking:
            return await voice_input.listen()

        loop = asyncio.get_running_loop()
        interrupt_requested = False
        interrupt_future = None

        def interrupt_on_user_speech() -> None:
            nonlocal interrupt_future, interrupt_requested
            if interrupt_requested:
                return
            interrupt_requested = True
            try:
                interrupt_future = asyncio.run_coroutine_threadsafe(self.interrupt(), loop)
            except RuntimeError:
                logger.debug("Could not schedule speech interruption; event loop is closed")

        text = await voice_input.listen(on_speech_start=interrupt_on_user_speech)
        if interrupt_future is not None:
            try:
                await asyncio.wrap_future(interrupt_future)
            except Exception:
                logger.debug("Scheduled speech interruption failed", exc_info=True)
        elif text and self.is_speaking:
            await self.interrupt()
        return text

    def cancel_listen(self):
        """Cancel an in-progress listen from another coroutine."""
        if self._voice_input is not None:
            self._voice_input.cancel()


def _looks_like_echo(transcript: str, spoken_text: str) -> bool:
    """Return True when ASR text is likely Eyra's own TTS."""
    heard = _normalize_for_echo(transcript)
    spoken = _normalize_for_echo(spoken_text)
    if not heard or not spoken:
        return False
    if len(heard) >= 12 and heard in spoken:
        return True
    return difflib.SequenceMatcher(None, heard, spoken).ratio() >= 0.78


def _normalize_for_echo(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()
