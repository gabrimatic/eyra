"""Controller-owned dictation state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DictationState:
    """In-memory dictation buffer for hands-free text capture."""

    active: bool = False
    lines: list[str] = field(default_factory=list)
    target_path: str | None = None

    def start(self, target_path: str | None = None) -> None:
        self.active = True
        self.lines = []
        self.target_path = target_path

    def append(self, text: str) -> None:
        cleaned = normalize_literal_text(text.strip())
        if cleaned:
            self.lines.append(cleaned)

    def text(self) -> str:
        return "\n".join(self.lines)

    def clear(self) -> None:
        self.active = False
        self.lines = []
        self.target_path = None


def dictation_command(text: str) -> str | None:
    """Return start/end/cancel when text is a dictation control phrase."""
    lowered = text.strip().lower().rstrip(".!?")
    if lowered == "start dictation" or lowered.startswith("start dictation to "):
        return "start"
    if lowered == "end dictation":
        return "end"
    if lowered == "cancel dictation":
        return "cancel"
    return None


def parse_dictation_target(text: str, path_resolver) -> str | None:
    """Parse 'Start dictation to a file named X in my Documents' style targets."""
    stripped = " ".join(text.strip().split())
    match = re.fullmatch(
        r"start dictation to (?:a\s+)?file\s+(?:named|called)?\s*(?P<name>.+?)"
        r"\s+in\s+(?:my\s+)?(?P<folder>desktop|documents|downloads|tmp|/tmp)\.?",
        stripped,
        re.I,
    )
    if not match:
        return None
    name = match.group("name").strip().strip("'\"")
    return path_resolver(match.group("folder"), name)


def normalize_literal_text(text: str) -> str:
    """Convert simple spoken spelling after 'literal' into literal characters."""
    stripped = text.strip()
    if not stripped.lower().startswith("literal "):
        return stripped
    tokens = stripped.split()[1:]
    words = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "dash": "-",
        "hyphen": "-",
        "dot": ".",
        "period": ".",
        "underscore": "_",
        "slash": "/",
        "space": " ",
    }
    pieces: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if len(token) == 1 and token.isalpha():
            pieces.append(token.upper())
        elif lowered in words:
            pieces.append(words[lowered])
        else:
            pieces.append(token)
    return "".join(pieces)
