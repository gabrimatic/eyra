"""Deterministic low-data memory compaction helpers."""

from __future__ import annotations

import re

_SECTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("writing_style", ("writing", "tone", "style", "voice", "format")),
    ("workflows", ("workflow", "process", "always run", "when i ask", "default to")),
    ("devices_environment", ("mac", "machine", "device", "path", "folder", "homebrew", "ollama", "whisper")),
    ("eyra_project", ("eyra", "ira", "assistant", "menu bar", "voice")),
    ("long_term_tasks", ("project", "goal", "roadmap", "long term", "todo")),
    ("do_not_forget", ("never forget", "important", "remember that")),
    ("user_preferences", ("prefer", "preference", "like", "dislike", "want", "do not", "don't")),
    ("user_profile", ("i am", "my name", "i live", "i work", "based in")),
)


def compact_text(text: str, max_chars: int) -> str:
    """Normalize text and keep only a bounded useful sentence."""
    cleaned = re.sub(r"\s+", " ", text.strip())
    cleaned = re.sub(r"^(please\s+)?remember\s+(that\s+|to\s+)?", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" -:;,.")
    if max_chars <= 0:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    clipped = cleaned[: max(0, max_chars - 3)].rstrip(" ,.;:")
    return f"{clipped}..."


def key_for_text(text: str, max_chars: int = 48) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {
        "a", "an", "and", "are", "as", "be", "but", "for", "from", "i", "in", "is",
        "it", "me", "my", "of", "on", "or", "that", "the", "this", "to", "with", "you",
    }
    kept = [word for word in words if word not in stop][:6]
    key = "_".join(kept) or "memory"
    return key[:max_chars].strip("_") or "memory"


def section_for_text(text: str) -> str:
    lowered = text.lower()
    for section, needles in _SECTION_PATTERNS:
        if any(needle in lowered for needle in needles):
            return section
    return "user_preferences"
