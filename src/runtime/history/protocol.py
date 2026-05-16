"""Raw model/tool protocol history.

Protocol history is for model execution only.
Semantic history is for user-visible, durable, Web, route, job, connector, and diagnostic surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProtocolHistory:
    """In-memory raw protocol messages for model/tool loops."""

    messages: list[dict[str, Any]] = field(default_factory=list)

    def append(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def insert(self, index: int, message: dict[str, Any]) -> None:
        self.messages.insert(index, message)

    def clear(self) -> None:
        self.messages.clear()

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return list(self.messages[-max(1, limit) :])

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.messages)

    def __len__(self) -> int:
        return len(self.messages)
