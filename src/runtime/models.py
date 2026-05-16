"""Runtime state and event models for the live session."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from runtime.history import ProtocolHistory, SemanticHistory


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
    # Legacy coarse flag: true when at least one Local Whisper-backed feature is usable.
    wh_available: bool = False
    # Split capabilities: None means an older caller only populated wh_available.
    listening_available: bool | None = None
    speech_available: bool | None = None
    wh_bin: str | None = None
    screen_capture_available: bool = False
    tool_capable_models: list[str] = field(default_factory=list)
    tool_capability_checked_models: list[str] = field(default_factory=list)
    vision_capable_models: list[str] = field(default_factory=list)
    vision_capability_checked_models: list[str] = field(default_factory=list)


@dataclass
class LiveRuntimeState:
    listening_enabled: bool = False
    speech_enabled: bool = False
    speech_muted: bool = False
    backend_ready: bool = False
    wh_bin: str | None = None
    current_goal: str | None = None
    current_status: RuntimeStatus = RuntimeStatus.STARTING
    last_user_input_at: float | None = None
    last_spoken_output_at: float | None = None
    protocol_history: ProtocolHistory = field(default_factory=ProtocolHistory)
    semantic_history: SemanticHistory = field(default_factory=SemanticHistory)
    recent_events: deque = field(default_factory=lambda: deque(maxlen=50))
    last_route_trace: object | None = None

    @property
    def conversation_messages(self) -> list[dict]:
        """Deprecated raw protocol alias retained for model execution paths."""
        return self.protocol_history.messages

    def append_protocol_message(self, message: dict) -> None:
        self.protocol_history.append(message)
        self.semantic_history.append_from_protocol(message)

    def insert_protocol_message(self, index: int, message: dict) -> None:
        self.protocol_history.insert(index, message)
        self.semantic_history.rebuild_from_protocol(self.protocol_history.messages)

    def clear_history(self) -> None:
        self.protocol_history.clear()
        self.semantic_history.clear()

    @classmethod
    def from_preflight(cls, result: PreflightResult, settings=None) -> "LiveRuntimeState":
        listening_available = (
            result.listening_available
            if result.listening_available is not None
            else result.wh_available
        )
        speech_available = (
            result.speech_available
            if result.speech_available is not None
            else result.wh_available
        )
        state = cls(
            backend_ready=result.backend_reachable and len(result.models_missing) == 0,
            listening_enabled=bool(listening_available),
            speech_enabled=bool(speech_available),
            wh_bin=result.wh_bin,
        )
        # Apply user config flags (disable capabilities the user turned off)
        if settings is not None:
            if not settings.LIVE_LISTENING_ENABLED:
                state.listening_enabled = False
            if not settings.LIVE_SPEECH_ENABLED:
                state.speech_enabled = False
        return state
