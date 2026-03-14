"""Shared enums for interaction quality and style."""

from enum import Enum


class QualityMode(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    BEST = "best"


class InteractionStyle(str, Enum):
    TEXT = "text"
    VOICE = "voice"
