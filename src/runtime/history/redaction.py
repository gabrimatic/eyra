"""Redaction rules for semantic runtime history."""

from __future__ import annotations

import json
import re
from typing import Any

from runtime.history.types import DataClass, PrivacyBoundary, RedactionPolicy, SemanticTurn, ToolUseSummary

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)([\"'\s:=]+)([^\s,}\"']+)")
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{12,}")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9_\-./+=]{12,}")
_HOME_RE = re.compile(r"/Users/[^/\s,}\"']+")
_TEMP_RE = re.compile(r"(?:/private)?/var/[^\s,}\"']+|/tmp/[^\s,}\"']+")
_OMIT_HINTS = {
    "clipboard": DataClass.OMITTED_CLIPBOARD,
    "pdf": DataClass.OMITTED_PDF_TEXT,
    "connector": DataClass.OMITTED_CONNECTOR_PAYLOAD,
}


def redact_semantic_text(text: str, policy: RedactionPolicy | None = None) -> str:
    """Redact secrets and local paths from user-visible history text."""
    policy = policy or RedactionPolicy()
    redacted = text
    if policy.redact_secrets:
        redacted = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted)
        redacted = _OPENAI_KEY_RE.sub("[REDACTED_KEY]", redacted)
        redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    if policy.redact_local_paths:
        redacted = _HOME_RE.sub("~/[user]", redacted)
    if policy.redact_temp_paths:
        redacted = _TEMP_RE.sub("~/[temp]", redacted)
    if len(redacted) > policy.max_text_chars:
        return redacted[: policy.max_text_chars - 20].rstrip() + "\n[summary clipped]"
    return redacted


def semantic_history_entry(
    message: dict[str, Any],
    policy: RedactionPolicy | None = None,
    tool_name_by_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a safe semantic summary of one protocol message."""
    return semantic_turn_from_protocol(message, policy=policy, tool_name_by_id=tool_name_by_id).to_dict()


def semantic_turn_from_protocol(
    message: dict[str, Any],
    policy: RedactionPolicy | None = None,
    tool_name_by_id: dict[str, str] | None = None,
) -> SemanticTurn:
    """Convert one raw model/tool protocol message into a typed semantic turn."""
    policy = policy or RedactionPolicy()
    role = str(message.get("role", "user"))
    data_classes: list[DataClass] = [DataClass.TEXT]
    content = _content_summary(message, policy=policy, data_classes=data_classes, tool_name_by_id=tool_name_by_id or {})
    tool_calls = tuple(_tool_summaries(message))
    if tool_calls:
        data_classes.append(DataClass.TOOL_NAME)
        data_classes.append(DataClass.OMITTED_TOOL_ARGS)
    privacy = PrivacyBoundary(data_classes=tuple(_dedupe_data_classes(data_classes)))
    metadata: dict[str, Any] = {}
    if role == "tool" and message.get("tool_call_id"):
        metadata["toolCallId"] = str(message.get("tool_call_id", ""))
    return SemanticTurn(role=role, content=content, tool_calls=tool_calls, privacy=privacy, metadata=metadata)


def _content_summary(
    message: dict[str, Any],
    *,
    policy: RedactionPolicy,
    data_classes: list[DataClass],
    tool_name_by_id: dict[str, str],
) -> str:
    role = str(message.get("role", "user"))
    if role == "tool" and policy.omit_protocol_payloads:
        tool_name = tool_name_by_id.get(str(message.get("tool_call_id", "")), "")
        data_classes.extend(_omitted_data_classes_for_tool(message, tool_name=tool_name))
        return f"[{tool_name or 'tool'} result omitted]"
    content = message.get("content", "")
    if isinstance(content, str):
        return redact_semantic_text(content, policy=policy)
    if isinstance(content, list):
        return _semantic_parts(content, policy=policy, data_classes=data_classes)
    if content is None:
        return ""
    return redact_semantic_text(str(content), policy=policy)


def _semantic_parts(parts: list[Any], *, policy: RedactionPolicy, data_classes: list[DataClass]) -> str:
    rendered: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            rendered.append(redact_semantic_text(str(part), policy=policy))
            continue
        kind = part.get("type")
        if kind == "text":
            rendered.append(redact_semantic_text(str(part.get("text", "")), policy=policy))
        elif kind in {"image_url", "input_image"}:
            data_classes.append(DataClass.OMITTED_IMAGE)
            rendered.append("[image omitted]")
        else:
            safe = {key: value for key, value in part.items() if key not in {"image_url", "data", "arguments"}}
            rendered.append(redact_semantic_text(json.dumps(safe, sort_keys=True), policy=policy))
    return "\n".join(item for item in rendered if item)


def _tool_summaries(message: dict[str, Any]) -> list[ToolUseSummary]:
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        return []
    summaries: list[ToolUseSummary] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function", {})
        name = function.get("name") if isinstance(function, dict) else ""
        summaries.append(ToolUseSummary(name=str(name or "unknown")))
    return summaries


def _omitted_data_classes_for_tool(message: dict[str, Any], *, tool_name: str = "") -> list[DataClass]:
    haystack = " ".join([tool_name, str(message.get("name", "")), str(message.get("tool_call_id", ""))]).lower()
    classes = [DataClass.OMITTED_TOOL_ARGS]
    for hint, data_class in _OMIT_HINTS.items():
        if hint in haystack:
            classes.append(data_class)
    return classes


def _dedupe_data_classes(items: list[DataClass]) -> list[DataClass]:
    seen: set[DataClass] = set()
    result: list[DataClass] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
