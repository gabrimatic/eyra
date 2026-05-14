"""Shared helpers for local coding job requests."""

from __future__ import annotations

import re


def parse_coding_job_request(text: str) -> tuple[str, str] | None:
    """Return the requested terminal agent and instruction when text asks for a coding job."""
    stripped = " ".join(text.strip().split())
    patterns = [
        r"(?:start|create|run)\s+(?:a\s+)?coding job(?:\s+with\s+(?P<agent>[a-z0-9_.-]+))?\s+to\s+(?P<task>.+)",
        r"(?:ask|tell)\s+(?P<agent>[a-z0-9_.-]+)\s+to\s+(?P<task>.+)",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, stripped, re.I)
        if match:
            agent = (match.groupdict().get("agent") or "codex").lower()
            instruction = match.group("task").strip().strip("'\"").rstrip(".")
            if instruction:
                return agent, instruction
    return None


def approval_id_from_text(text: str) -> str | None:
    """Extract a local approval id from a user-facing approval-required message."""
    match = re.search(r"/approve\s+(?P<id>\S+)", text)
    return match.group("id") if match else None
