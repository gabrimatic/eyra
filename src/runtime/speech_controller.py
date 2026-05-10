"""Speech output (TTS) and voice input (VAD + ASR) via Local Whisper."""

from __future__ import annotations

import asyncio
import logging
import time

from runtime.models import LiveRuntimeState

logger = logging.getLogger(__name__)


class SpeechController:
    def __init__(self, state: LiveRuntimeState, cooldown_ms: int = 3000, silence_duration_ms: int = 1500, vad_threshold: float = 0.6):
        self.state = state
        self.cooldown_s = cooldown_ms / 1000.0
        self._silence_duration_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
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

        # Wait for any ongoing speech to finish first
        await self.wait_for_speech()

        voice_input = self._get_voice_input()
        if voice_input is None:
            return None
        return await voice_input.listen()

    def cancel_listen(self):
        """Cancel an in-progress listen from another coroutine."""
        if self._voice_input is not None:
            self._voice_input.cancel()
