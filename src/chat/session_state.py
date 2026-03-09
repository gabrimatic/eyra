"""
Shared in-memory session state for the current Eyra run.

One instance lives for the lifetime of the process. All modes
read and write through it so context survives mode switches.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional


class QualityMode(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    BEST = "best"


class InteractionStyle(str, Enum):
    TEXT = "text"
    WATCH = "watch"
    VOICE = "voice"


@dataclass
class LastTaskMeta:
    """Metadata about the most recent user task, used by /retry."""
    task_type: str = "text"       # "text" or "image"
    text_content: str = ""        # the prompt that was sent
    use_selfie: bool = False      # True if webcam, False if screenshot


class SessionState:
    """Lightweight state bag for the current run. No persistence."""

    def __init__(self):
        self.messages: List[Dict] = []
        self.quality_mode: QualityMode = QualityMode.BALANCED
        self.interaction_style: InteractionStyle = InteractionStyle.TEXT
        self.watch_active: bool = False
        self.watch_goal: Optional[str] = None
        self.watch_voice_muted: bool = False
        self.last_task: Optional[LastTaskMeta] = None

    def clear(self):
        """Reset to startup defaults."""
        self.messages.clear()
        self.quality_mode = QualityMode.BALANCED
        self.watch_active = False
        self.watch_goal = None
        self.watch_voice_muted = False
        self.last_task = None

    def status_summary(self) -> str:
        """Human-readable one-liner for /status."""
        parts = [f"quality: {self.quality_mode.value}"]
        if self.watch_active:
            goal = self.watch_goal or "(no goal)"
            parts.append(f"watching: {goal}")
            parts.append(f"watch narration: {'muted' if self.watch_voice_muted else 'on'}")
        parts.append(f"messages: {len(self.messages)}")
        parts.append(f"style: {self.interaction_style.value}")
        return " | ".join(parts)
