"""Structured connector contracts for Eyra-controlled workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ConnectorType(str, Enum):
    CLI = "cli"
    MCP = "mcp"
    HTTP_LOCAL = "http_local"
    HTTP_REMOTE = "http_remote"
    PYTHON_MODULE = "python_module"
    BROWSER_AGENT = "browser_agent"
    CODING_AGENT = "coding_agent"


class ConnectorRiskTier(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_CHANGE = "low_risk_change"
    MEDIUM_RISK_CHANGE = "medium_risk_change"
    HIGH_RISK_CHANGE = "high_risk_change"
    IRREVERSIBLE_OR_EXTERNAL = "irreversible_or_external"
    DELEGATED_AGENT = "delegated_agent"


class AcceptanceState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    VALIDATION_FAILED = "validation_failed"
    AVAILABLE = "available"
    ACCEPTANCE_FAILED = "acceptance_failed"
    ACCEPTED = "accepted"
    DISABLED = "disabled"


class ConnectorInputMode(str, Enum):
    STDIN_JSON = "stdin_json"
    STDIN_TEXT = "stdin_text"
    NONE = "none"


class ConnectorOutputMode(str, Enum):
    STDOUT_JSON = "stdout_json"
    STDOUT_TEXT = "stdout_text"


class ConnectorCwdPolicy(str, Enum):
    FILESYSTEM_DEFAULT_PATH = "filesystem_default_path"
    REQUEST = "request"
    MANIFEST = "manifest"


@dataclass(frozen=True)
class ConnectorPrivacy:
    data_sent: tuple[str, ...]
    destination: str
    leaves_machine: bool


@dataclass(frozen=True)
class ConnectorAcceptance:
    health_command: tuple[str, ...] = ()
    test_task: str = ""
    expected_output_contains: str = ""
    requires_human_approval: bool = True


@dataclass(frozen=True)
class ConnectorManifest:
    id: str
    display_name: str
    type: ConnectorType
    enabled: bool
    command: tuple[str, ...] = ()
    endpoint: str = ""
    module: str = ""
    cwd_policy: ConnectorCwdPolicy = ConnectorCwdPolicy.FILESYSTEM_DEFAULT_PATH
    cwd: str = ""
    input_mode: ConnectorInputMode = ConnectorInputMode.STDIN_JSON
    output_mode: ConnectorOutputMode = ConnectorOutputMode.STDOUT_JSON
    local: bool = True
    can_use_network: bool = False
    can_read_files: bool = False
    can_mutate_files: bool = False
    can_control_ui: bool = False
    can_run_shell: bool = False
    requires_approval: bool = True
    risk_tier: ConnectorRiskTier = ConnectorRiskTier.DELEGATED_AGENT
    timeout_seconds: int = 600
    output_cap_bytes: int = 32768
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    privacy: ConnectorPrivacy | None = None
    acceptance: ConnectorAcceptance = field(default_factory=ConnectorAcceptance)
    allowed_roots: tuple[Path, ...] = ()
    default_path: Path = Path.home()

    @property
    def remote(self) -> bool:
        return self.type == ConnectorType.HTTP_REMOTE or not self.local or bool(self.privacy and self.privacy.leaves_machine)

    @property
    def needs_approval(self) -> bool:
        return (
            self.requires_approval
            or self.can_mutate_files
            or self.can_control_ui
            or self.can_run_shell
            or self.remote
            or self.risk_tier
            in {
                ConnectorRiskTier.MEDIUM_RISK_CHANGE,
                ConnectorRiskTier.HIGH_RISK_CHANGE,
                ConnectorRiskTier.IRREVERSIBLE_OR_EXTERNAL,
                ConnectorRiskTier.DELEGATED_AGENT,
            }
        )

    @property
    def capabilities(self) -> tuple[str, ...]:
        caps: list[str] = []
        if self.can_read_files:
            caps.append("file_read")
        if self.can_mutate_files:
            caps.append("file_write")
        if self.can_use_network:
            caps.append("network")
        if self.can_control_ui:
            caps.append("ui_control")
        if self.can_run_shell:
            caps.append("shell")
        if self.type in {ConnectorType.MCP, ConnectorType.CODING_AGENT, ConnectorType.BROWSER_AGENT}:
            caps.append(self.type.value)
        return tuple(caps)


@dataclass(frozen=True)
class ConnectorConfigLoadResult:
    status: str
    reason: str = ""
    manifests: tuple[ConnectorManifest, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorJobSpec:
    connector_id: str
    task: str
    cwd: str = ""
    source: str = "terminal"
    approval_id: str = ""
    job_id: str = ""
    selected_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorJobResult:
    connector_id: str
    status: str
    output: str
    job_id: str = ""
    exit_code: int | None = None
    approval_id: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    logs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ConnectorAcceptanceResult:
    connector_id: str
    state: AcceptanceState
    reason: str
    checks: tuple[dict[str, Any], ...] = ()

