"""Runtime history boundaries."""

from runtime.history.protocol import ProtocolHistory
from runtime.history.redaction import redact_semantic_text, semantic_history_entry
from runtime.history.store import (
    SemanticHistory,
    build_semantic_history,
    sanitize_semantic_entries,
    semantic_history_to_protocol_context,
)
from runtime.history.types import (
    ConnectorReference,
    DataClass,
    JobReference,
    PrivacyBoundary,
    RedactionPolicy,
    RouteSummary,
    SemanticTurn,
    ToolUseSummary,
)

__all__ = [
    "ConnectorReference",
    "DataClass",
    "JobReference",
    "PrivacyBoundary",
    "ProtocolHistory",
    "RedactionPolicy",
    "RouteSummary",
    "SemanticHistory",
    "SemanticTurn",
    "ToolUseSummary",
    "build_semantic_history",
    "redact_semantic_text",
    "sanitize_semantic_entries",
    "semantic_history_entry",
    "semantic_history_to_protocol_context",
]
