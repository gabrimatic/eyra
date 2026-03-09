"""Speech output and input via local-whisper (wh)."""

import asyncio
import logging
import time
from typing import Optional

from runtime.models import LiveRuntimeState

logger = logging.getLogger(__name__)


class ServiceBusyError(Exception):
    """Raised when the Local Whisper service is temporarily busy."""
    pass


class SpeechController:
    def __init__(self, state: LiveRuntimeState, cooldown_ms: int = 3000):
        self.state = state
        self.cooldown_s = cooldown_ms / 1000.0
        self._speaking_proc: Optional[asyncio.subprocess.Process] = None

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

        try:
            self._speaking_proc = await asyncio.create_subprocess_exec(
                "wh", "whisper", text,
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

    async def listen(self, timeout_seconds: int = 10) -> Optional[str]:
        """Listen via wh listen. Returns transcribed text, None on silence,
        raises ServiceBusyError or RuntimeError on real failures."""
        if not self.state.listening_enabled:
            return None

        # Wait for any ongoing speech to finish first
        await self.wait_for_speech()

        try:
            proc = await asyncio.create_subprocess_exec(
                "wh", "listen", str(timeout_seconds), "--raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds + 5
            )

            out = stdout.decode().strip() if stdout else ""
            err = stderr.decode().strip() if stderr else ""

            # "No speech detected" is normal silence, regardless of exit code
            if "no speech" in out.lower() or "no speech" in err.lower():
                return None

            if proc.returncode != 0:
                msg = err or out or f"wh listen exited {proc.returncode}"
                if "busy" in msg.lower():
                    raise ServiceBusyError(msg)
                raise RuntimeError(msg)

            # With --raw, strip any "Recording..." prefix lines
            lines = out.splitlines()
            text_lines = [
                l for l in lines
                if not l.startswith("Recording") and l.strip()
            ]
            text = " ".join(text_lines).strip()
            return text if text else None

        except asyncio.TimeoutError:
            return None
