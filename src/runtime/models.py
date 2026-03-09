"""Runtime state and event models for the live session."""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional


class RuntimeStatus(str, Enum):
    STARTING = "starting"
    PREFLIGHT = "preflight"
    OBSERVING = "observing"
    LISTENING = "listening"
    CHANGE_DETECTED = "change_detected"
    ANALYZING = "analyzing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    PAUSED = "paused"
    IDLE = "idle"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    PERMISSION_REQUIRED = "permission_required"
    ERROR = "error"


@dataclass
class ObservationEvent:
    fingerprint_changed: bool = False
    material_change: bool = False
    active_app_changed: bool = False
    active_window_changed: bool = False
    reason: str = ""
    captured_image_base64: Optional[str] = None


@dataclass
class UserIntent:
    kind: Literal["control", "question", "goal_update", "screen_request", "chat"] = "chat"
    text: str = ""
    requires_screen: bool = False


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
    observing: bool = True
    paused: bool = False
    listening_enabled: bool = False
    speech_enabled: bool = False
    speech_muted: bool = False
    backend_ready: bool = False
    current_goal: Optional[str] = None
    current_status: RuntimeStatus = RuntimeStatus.STARTING
    active_app: Optional[str] = None
    active_window: Optional[str] = None
    last_screen_fingerprint: Optional[str] = None
    last_response_hash: Optional[str] = None
    last_screen_summary: Optional[str] = None
    last_user_input_at: Optional[float] = None
    last_observation_at: Optional[float] = None
    last_spoken_output_at: Optional[float] = None
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
            if not settings.LIVE_OBSERVATION_ENABLED:
                state.observing = False
            if not settings.LIVE_LISTENING_ENABLED:
                state.listening_enabled = False
            if not settings.LIVE_SPEECH_ENABLED:
                state.speech_enabled = False
        return state
