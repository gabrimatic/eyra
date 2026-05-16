"""Semantic runtime history store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.history.redaction import semantic_turn_from_protocol
from runtime.history.types import RedactionPolicy, SemanticTurn


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
