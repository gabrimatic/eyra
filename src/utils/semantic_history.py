"""Redacted semantic history for visible and durable session summaries."""

from __future__ import annotations

import json
import re
from typing import Any

_MAX_TEXT_CHARS = 800
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)([\"'\s:=]+)([^\s,}\"']+)")
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{12,}")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9_\-./+=]{12,}")
_HOME_RE = re.compile(r"/Users/[^/\s,}\"']+")
_TEMP_RE = re.compile(r"(?:/private)?/var/folders/[^\s,}\"']+|/tmp/[^\s,}\"']+")


def redact_semantic_text(text: str) -> str:
    """Redact secrets and local paths from user-visible history text."""
    redacted = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    redacted = _OPENAI_KEY_RE.sub("[REDACTED_KEY]", redacted)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _HOME_RE.sub("~/[user]", redacted)
    redacted = _TEMP_RE.sub("~/[temp]", redacted)
    if len(redacted) > _MAX_TEXT_CHARS:
        return redacted[: _MAX_TEXT_CHARS - 20].rstrip() + "\n[summary clipped]"
    return redacted


def semantic_history_entry(message: dict[str, Any]) -> dict[str, Any]:
    """Return a safe semantic summary of one protocol message.

    Raw tool arguments, image payloads, connector payloads, clipboard values,
    secrets, and local paths are deliberately collapsed before this entry can be
    shown in a UI, stored durably, or reused as route context.
    """
    role = str(message.get("role", "user"))
    entry: dict[str, Any] = {"role": role}
    content = message.get("content", "")
    if isinstance(content, str):
        entry["content"] = redact_semantic_text(content)
    elif isinstance(content, list):
        entry["content"] = _semantic_parts(content)
    else:
        entry["content"] = redact_semantic_text(str(content))
    if role == "assistant" and message.get("tool_calls"):
        entry["toolCalls"] = [
            {"name": str(call.get("function", {}).get("name", "unknown"))}
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        ]
    if role == "tool":
        entry["toolCallId"] = str(message.get("tool_call_id", ""))
    return entry


def build_semantic_history(messages: list[dict[str, Any]], *, max_messages: int = 10) -> list[dict[str, Any]]:
    """Build a bounded redacted transcript from raw model/tool protocol messages."""
    return [semantic_history_entry(message) for message in messages[-max(1, max_messages) :]]


def _semantic_parts(parts: list[Any]) -> str:
    rendered: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            rendered.append(redact_semantic_text(str(part)))
            continue
        kind = part.get("type")
        if kind == "text":
            rendered.append(redact_semantic_text(str(part.get("text", ""))))
        elif kind in {"image_url", "input_image"}:
            rendered.append("[image omitted]")
        else:
            safe = {key: value for key, value in part.items() if key not in {"image_url", "data", "arguments"}}
            rendered.append(redact_semantic_text(json.dumps(safe, sort_keys=True)))
    return "\n".join(item for item in rendered if item)
