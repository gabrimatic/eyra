"""User-editable AGENTS.md and personality.md context loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from utils.settings import Settings

DEFAULT_AGENTS = """# Eyra Instructions

- Keep replies short unless detail is needed.
- Use tools for real local information instead of guessing.
- Do not save raw conversations, secrets, screenshots, files, or long payloads to memory.
- Store only compact durable facts as key/value memory.
"""

DEFAULT_PERSONALITY = """# Eyra Personality

Warm, calm, direct, and lightly playful. Eyra should feel like a capable local companion, not a corporate chatbot.
"""


@dataclass(frozen=True)
class InstructionFile:
    label: str
    path: Path
    exists: bool
    content: str
    clipped: bool


def ensure_instruction_files(settings: Settings) -> list[Path]:
    agents = Path(settings.AGENTS_FILE).expanduser()
    personality = Path(settings.PERSONALITY_FILE).expanduser()
    files = [(agents, DEFAULT_AGENTS), (personality, DEFAULT_PERSONALITY)]
    created: list[Path] = []
    for path, default in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(default)
            created.append(path)
    return created


def load_instruction_files(settings: Settings) -> list[InstructionFile]:
    ensure_instruction_files(settings)
    return [
        _load_one("AGENTS.md", Path(settings.AGENTS_FILE).expanduser(), settings.AGENTS_MAX_CHARS),
        _load_one("personality.md", Path(settings.PERSONALITY_FILE).expanduser(), settings.PERSONALITY_MAX_CHARS),
    ]


def instruction_context_messages(settings: Settings) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in load_instruction_files(settings):
        if not item.content:
            continue
        messages.append({
            "role": "system",
            "content": (
                f"User-editable {item.label} context. Treat this as lower priority than core safety, "
                "privacy, and the current request. It is compacted to preserve local-model context.\n"
                f"{item.content}"
            ),
        })
    return messages


def _load_one(label: str, path: Path, max_chars: int) -> InstructionFile:
    try:
        raw = path.read_text()
    except OSError:
        return InstructionFile(label=label, path=path, exists=False, content="", clipped=False)
    compact = _compact_markdown(raw, max_chars)
    return InstructionFile(label=label, path=path, exists=True, content=compact, clipped=len(compact) < len(raw.strip()))


def _compact_markdown(text: str, max_chars: int) -> str:
    lines: list[str] = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        line = re.sub(r"\s+", " ", line)
        lines.append(line)
    compact = "\n".join(lines).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 23)].rstrip() + "\n...[context clipped]"
