"""Runtime state and event models for the live session."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class RuntimeStatus(str, Enum):
    STARTING = "starting"
    PREFLIGHT = "preflight"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    IDLE = "idle"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    PERMISSION_REQUIRED = "permission_required"
    ERROR = "error"


@dataclass
class PreflightResult:
    backend_reachable: bool = False
    models_ready: list[str] = field(default_factory=list)
    models_missing: list[str] = field(default_factory=list)
    wh_available: bool = False
    screen_capture_available: bool = False
    microphone_available: bool = False


@dataclass
class LiveRuntimeState:
    listening_enabled: bool = False
    speech_enabled: bool = False
    speech_muted: bool = False
    backend_ready: bool = False
    current_goal: str | None = None
    current_status: RuntimeStatus = RuntimeStatus.STARTING
    last_user_input_at: float | None = None
    last_spoken_output_at: float | None = None
    conversation_messages: list[dict] = field(default_factory=list)
    recent_events: deque = field(default_factory=lambda: deque(maxlen=50))

    @classmethod
    def from_preflight(cls, result: PreflightResult, settings=None) -> "LiveRuntimeState":
        state = cls(
            backend_ready=result.backend_reachable and len(result.models_missing) == 0,
            listening_enabled=result.wh_available and result.microphone_available,
            speech_enabled=result.wh_available,
        )
        # Apply user config flags (disable capabilities the user turned off)
        if settings is not None:
            if not settings.LIVE_LISTENING_ENABLED:
                state.listening_enabled = False
            if not settings.LIVE_SPEECH_ENABLED:
                state.speech_enabled = False
        return state
