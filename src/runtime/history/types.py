"""Typed semantic history records for safe runtime surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataClass(str, Enum):
    TEXT = "text"
    TOOL_NAME = "tool_name"
    ROUTE = "route"
    JOB_REFERENCE = "job_reference"
    CONNECTOR_REFERENCE = "connector_reference"
    REDACTED_SECRET = "redacted_secret"
    REDACTED_LOCAL_PATH = "redacted_local_path"
    REDACTED_TEMP_PATH = "redacted_temp_path"
    OMITTED_TOOL_ARGS = "omitted_tool_args"
    OMITTED_IMAGE = "omitted_image"
    OMITTED_CLIPBOARD = "omitted_clipboard"
    OMITTED_PDF_TEXT = "omitted_pdf_text"
    OMITTED_CONNECTOR_PAYLOAD = "omitted_connector_payload"


@dataclass(frozen=True)
class ToolUseSummary:
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name}


@dataclass(frozen=True)
class RouteSummary:
    execution_class: str = ""
    selected_model: str = ""
    risk_tier: str = ""
    privacy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "executionClass": self.execution_class,
                "selectedModel": self.selected_model,
                "riskTier": self.risk_tier,
                "privacy": self.privacy,
            }.items()
            if value
        }


@dataclass(frozen=True)
class JobReference:
    id: str
    title: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in {"id": self.id, "title": self.title, "status": self.status}.items() if value}


@dataclass(frozen=True)
class ConnectorReference:
    id: str
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in {"id": self.id, "status": self.status}.items() if value}


@dataclass(frozen=True)
class PrivacyBoundary:
    local_only: bool = True
    leaves_machine: bool = False
    data_classes: tuple[DataClass, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "localOnly": self.local_only,
            "leavesMachine": self.leaves_machine,
            "dataClasses": [item.value for item in self.data_classes],
        }


@dataclass(frozen=True)
class RedactionPolicy:
    redact_secrets: bool = True
    redact_local_paths: bool = True
    redact_temp_paths: bool = True
    omit_protocol_payloads: bool = True
    max_text_chars: int = 800


@dataclass(frozen=True)
class SemanticTurn:
    role: str
    content: str
    tool_calls: tuple[ToolUseSummary, ...] = ()
    route: RouteSummary | None = None
    jobs: tuple[JobReference, ...] = ()
    connectors: tuple[ConnectorReference, ...] = ()
    privacy: PrivacyBoundary = field(default_factory=PrivacyBoundary)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "privacy": self.privacy.to_dict(),
        }
        if self.tool_calls:
            payload["toolCalls"] = [item.to_dict() for item in self.tool_calls]
        if self.route is not None:
            payload["route"] = self.route.to_dict()
        if self.jobs:
            payload["jobs"] = [item.to_dict() for item in self.jobs]
        if self.connectors:
            payload["connectors"] = [item.to_dict() for item in self.connectors]
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload
