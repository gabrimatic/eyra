"""Semantic runtime history store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.history.redaction import redact_semantic_text, semantic_turn_from_protocol
from runtime.history.types import RedactionPolicy, SemanticTurn

_VALID_PROTOCOL_CONTEXT_ROLES = {"user", "assistant"}
_SEMANTIC_ONLY_KEYS = {"privacy", "toolCalls", "route", "jobs", "connectors", "metadata"}


@dataclass
class SemanticHistory:
    """Typed redacted turns for user-visible and durable runtime surfaces."""

    turns: list[SemanticTurn] = field(default_factory=list)
    policy: RedactionPolicy = field(default_factory=RedactionPolicy)
    tool_name_by_id: dict[str, str] = field(default_factory=dict)

    def append_from_protocol(self, message: dict[str, Any]) -> None:
        self._remember_tool_calls(message)
        self.turns.append(
            semantic_turn_from_protocol(message, policy=self.policy, tool_name_by_id=self.tool_name_by_id)
        )

    def rebuild_from_protocol(self, messages: list[dict[str, Any]]) -> None:
        self.turns = []
        self.tool_name_by_id = {}
        for message in messages:
            self.append_from_protocol(message)

    def clear(self) -> None:
        self.turns.clear()
        self.tool_name_by_id.clear()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return [turn.to_dict() for turn in self.turns[-max(1, limit) :]]

    def to_list(self) -> list[dict[str, Any]]:
        return [turn.to_dict() for turn in self.turns]

    def _remember_tool_calls(self, message: dict[str, Any]) -> None:
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            return
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id", ""))
            function = call.get("function", {})
            name = str(function.get("name", "")) if isinstance(function, dict) else ""
            if call_id and name:
                self.tool_name_by_id[call_id] = name


def build_semantic_history(messages: list[dict[str, Any]], *, max_messages: int = 10) -> list[dict[str, Any]]:
    """Build a bounded redacted transcript from raw model/tool protocol messages."""
    history = SemanticHistory()
    history.rebuild_from_protocol(messages[-max(1, max_messages) :])
    return history.to_list()


def sanitize_semantic_entries(entries: list[dict[str, Any]], *, max_messages: int = 10) -> list[dict[str, Any]]:
    """Return bounded semantic entries with only known safe fields."""
    semantic_entries = entries if _looks_like_semantic_history(entries) else build_semantic_history(entries, max_messages=max_messages)
    return [_sanitize_semantic_entry(entry) for entry in semantic_entries[-max(1, max_messages) :]]


def semantic_history_to_protocol_context(
    semantic_entries: list[dict[str, Any]],
    *,
    max_messages: int = 6,
) -> list[dict[str, str]]:
    """Adapt safe semantic history into valid chat-protocol context messages."""
    messages: list[dict[str, str]] = []
    for entry in sanitize_semantic_entries(semantic_entries, max_messages=max_messages):
        role = str(entry.get("role", "assistant")).lower()
        if role == "tool":
            role = "assistant"
        if role not in _VALID_PROTOCOL_CONTEXT_ROLES:
            role = "assistant"
        content = redact_semantic_text(str(entry.get("content", ""))).strip()
        tool_names = _tool_names(entry.get("toolCalls"))
        if tool_names:
            tool_summary = f"[tools: {', '.join(tool_names)}]"
            content = f"{content}\n{tool_summary}".strip() if content else tool_summary
        if not content and str(entry.get("role", "")).lower() == "tool":
            content = "[tool result omitted]"
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _looks_like_semantic_history(entries: list[dict[str, Any]]) -> bool:
    return any(isinstance(entry, dict) and bool(_SEMANTIC_ONLY_KEYS.intersection(entry)) for entry in entries)


def _sanitize_semantic_entry(entry: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {
        "role": str(entry.get("role", "assistant")),
        "content": redact_semantic_text(str(entry.get("content", ""))),
    }
    privacy = entry.get("privacy")
    if isinstance(privacy, dict):
        safe["privacy"] = {
            "localOnly": bool(privacy.get("localOnly", True)),
            "leavesMachine": bool(privacy.get("leavesMachine", False)),
            "dataClasses": [redact_semantic_text(str(item)) for item in privacy.get("dataClasses", []) if item],
        }
    tool_names = _tool_names(entry.get("toolCalls"))
    if tool_names:
        safe["toolCalls"] = [{"name": name} for name in tool_names]
    route = _redacted_mapping(entry.get("route"), allowed={"executionClass", "selectedModel", "riskTier", "privacy"})
    if route:
        safe["route"] = route
    jobs = _redacted_references(entry.get("jobs"), allowed={"id", "title", "status"})
    if jobs:
        safe["jobs"] = jobs
    connectors = _redacted_references(entry.get("connectors"), allowed={"id", "status"})
    if connectors:
        safe["connectors"] = connectors
    return safe


def _tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = redact_semantic_text(str(item.get("name", ""))).strip()
        if name:
            names.append(name)
    return names


def _redacted_mapping(value: Any, *, allowed: set[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: redact_semantic_text(str(value[key])) for key in allowed if key in value and value[key]}


def _redacted_references(value: Any, *, allowed: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    references: list[dict[str, str]] = []
    for item in value:
        safe = _redacted_mapping(item, allowed=allowed)
        if safe:
            references.append(safe)
    return references
