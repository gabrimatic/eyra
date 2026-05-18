"""Safety policy for what Eyra may store in durable local memory."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(api[_-]?key|secret|password|token|credential)\b", re.I),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)
_RAW_PAYLOAD_PATTERNS = (
    re.compile(r"```"),
    re.compile(r"data:image/"),
    re.compile(r"base64", re.I),
    re.compile(r"\b(traceback|stack trace|exception group)\b", re.I),
)


def is_safe_to_store(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 4000:
        return False
    if any(pattern.search(stripped) for pattern in _SECRET_PATTERNS):
        return False
    if any(pattern.search(stripped) for pattern in _RAW_PAYLOAD_PATTERNS):
        return False
    return True


def redact_for_memory(text: str) -> str:
    redacted = re.sub(r"/Users/[^/\s]+", "~/[user]", text)
    redacted = re.sub(r"(?:/private)?/var/folders/[^\s,}\"']+|/tmp/[^\s,}\"']+", "~/[temp]", redacted)
    redacted = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[ip]", redacted)
    return redacted
