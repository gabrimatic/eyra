"""Cheap screen fingerprinting and triggered full capture."""

import asyncio
import hashlib
import logging
import subprocess
from typing import Optional

import mss
from PIL import Image

from runtime.models import ObservationEvent, LiveRuntimeState
from chat.capture import capture_screenshot_and_encode

logger = logging.getLogger(__name__)


def _get_active_app() -> Optional[str]:
    """Get the name of the frontmost application via AppleScript."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _get_active_window() -> Optional[str]:
    """Get the title of the frontmost window."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of front window of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _cheap_screen_fingerprint() -> str:
    """Fast low-res screen hash without full capture overhead."""
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        raw = sct.grab(monitor)
        # Downsample to tiny thumbnail for fast hashing
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
        img = img.resize((64, 36), Image.Resampling.BOX)
        return hashlib.md5(img.tobytes()).hexdigest()


class ScreenObserver:
    """Runs cheap fingerprint loop and emits observation events."""

    def __init__(self, state: LiveRuntimeState, debounce_ms: int = 1500):
        self.state = state
        self.debounce_s = debounce_ms / 1000.0
        self._pending_change_at: Optional[float] = None

    async def check(self) -> Optional[ObservationEvent]:
        """Run one cheap observation cycle. Returns event if something meaningful happened."""
        if self.state.paused or not self.state.observing:
            return None

        event = ObservationEvent()

        # Check active app/window
        app = _get_active_app()
        window = _get_active_window()

        if app and app != self.state.active_app:
            event.active_app_changed = True
            event.reason = f"App changed: {self.state.active_app} → {app}"
            self.state.active_app = app

        if window and window != self.state.active_window:
            event.active_window_changed = True
            if not event.reason:
                event.reason = f"Window changed: {window}"
            self.state.active_window = window

        # Cheap screen fingerprint
        try:
            fp = await asyncio.to_thread(_cheap_screen_fingerprint)
        except Exception as e:
            logger.debug("Fingerprint failed: %s", e)
            return None

        if fp != self.state.last_screen_fingerprint:
            event.fingerprint_changed = True
            self.state.last_screen_fingerprint = fp

            if self._pending_change_at is None:
                self._pending_change_at = asyncio.get_event_loop().time()
                # First change, wait for debounce
                return None

            elapsed = asyncio.get_event_loop().time() - self._pending_change_at
            if elapsed < self.debounce_s:
                # Still debouncing
                return None

            # Debounce passed, this is a material change
            event.material_change = True
            if not event.reason:
                event.reason = "Screen content changed"
            self._pending_change_at = None
        else:
            # Screen stable, reset pending change
            if self._pending_change_at is not None:
                elapsed = asyncio.get_event_loop().time() - self._pending_change_at
                if elapsed >= self.debounce_s:
                    # Screen settled after change
                    event.material_change = True
                    event.fingerprint_changed = True
                    event.reason = "Screen settled after change"
                    self._pending_change_at = None
                    return event
            return None

        if not event.material_change and not event.active_app_changed:
            return None

        return event

    async def capture_full(self) -> Optional[str]:
        """Capture a full-resolution screenshot. Only call when justified."""
        try:
            return await capture_screenshot_and_encode()
        except Exception as e:
            logger.error("Full capture failed: %s", e)
            return None
