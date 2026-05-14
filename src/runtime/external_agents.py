"""Optional external agent adapter registry.

External agents are workers under Eyra's local policy layer. They are disabled
by default, loaded only from static config, bounded by the filesystem sandbox,
and never allowed to choose their own command line at runtime.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.jobs import RiskLevel
from utils.settings import Settings

_DEFAULT_OUTPUT_CAP_BYTES = 16_384
_DEFAULT_TIMEOUT_SECONDS = 300
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)=([^\s]+)"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(token=)[^&\s]+"),
)


@dataclass(frozen=True)
class AgentJobSpec:
    """Input contract for one external-agent job."""

    agent_name: str
    task: str
    cwd: str = ""
    source: str = "terminal"
    approval_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentJobResult:
    """Bounded, redacted external-agent result."""

    agent_name: str
    status: str
    output: str
    exit_code: int | None = None
    artifacts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AgentAdapterCapabilities:
    """Static capability declaration for an external agent."""

    name: str
    kind: str
    available: bool
    local: bool
    can_mutate_files: bool
    can_use_network: bool
    requires_approval: bool
    risk_level: RiskLevel
    config_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfigLoadResult:
    """Config load status and configured adapters."""

    status: str
    reason: str = ""
    adapters: tuple["ExternalAgentAdapter", ...] = ()


class ExternalAgentAdapter:
    """Base adapter contract."""

    name: str

    @property
    def capabilities(self) -> AgentAdapterCapabilities:
        raise NotImplementedError

    async def run(self, spec: AgentJobSpec) -> AgentJobResult:
        raise NotImplementedError

    def cancel(self) -> None:
        """Request cancellation when the adapter supports it."""


class DetectionOnlyAgentAdapter(ExternalAgentAdapter):
    """Reports availability for known agents without pretending integration works."""

    def __init__(self, name: str, *, binaries: tuple[str, ...], local: bool = True):
        self.name = name
        self._binaries = binaries
        self._local = local

    @property
    def capabilities(self) -> AgentAdapterCapabilities:
        return AgentAdapterCapabilities(
            name=self.name,
            kind="detection",
            available=any(shutil.which(binary) for binary in self._binaries),
            local=self._local,
            can_mutate_files=False,
            can_use_network=False,
            requires_approval=True,
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE,
            config_requirements=("Configure this agent in EXTERNAL_AGENT_CONFIG_PATH before running jobs.",),
        )

    async def run(self, spec: AgentJobSpec) -> AgentJobResult:
        return AgentJobResult(
            agent_name=self.name,
            status="unavailable",
            output=f"{self.name} is detection-only until configured in EXTERNAL_AGENT_CONFIG_PATH.",
        )


class ConfiguredCliAgentAdapter(ExternalAgentAdapter):
    """Run a configured CLI agent with static argv and task text on stdin."""

    def __init__(
        self,
        *,
        name: str,
        command: tuple[str, ...],
        cwd_policy: str,
        allowed_roots: tuple[Path, ...],
        default_path: Path,
        network: bool,
        mutates_files: bool,
        requires_approval: bool,
        timeout_seconds: int,
        output_cap_bytes: int = _DEFAULT_OUTPUT_CAP_BYTES,
    ):
        self.name = name
        self.command = command
        self.cwd_policy = cwd_policy
        self._roots = tuple(root.expanduser().resolve() for root in allowed_roots)
        self._default_path = default_path.expanduser().resolve()
        self._network = network
        self._mutates_files = mutates_files
        self._requires_approval = requires_approval
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._output_cap_bytes = max(1_024, int(output_cap_bytes))
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def capabilities(self) -> AgentAdapterCapabilities:
        return AgentAdapterCapabilities(
            name=self.name,
            kind="cli",
            available=bool(self.command and shutil.which(self.command[0]) or Path(self.command[0]).exists()),
            local=not self._network,
            can_mutate_files=self._mutates_files,
            can_use_network=self._network,
            requires_approval=self._requires_approval or self._mutates_files or self._network,
            risk_level=RiskLevel.MEDIUM_RISK_CHANGE if self._mutates_files else RiskLevel.READ_ONLY,
            config_requirements=("static argv", "sandboxed cwd", "bounded timeout", "redacted capped output"),
        )

    async def run(self, spec: AgentJobSpec) -> AgentJobResult:
        try:
            cwd = self._resolve_cwd(spec.cwd)
        except (PermissionError, ValueError) as exc:
            return AgentJobResult(agent_name=self.name, status="blocked", output=str(exc))
        if not self.capabilities.available:
            return AgentJobResult(agent_name=self.name, status="unavailable", output=f"{self.name} command is not installed.")
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                self._proc.communicate(spec.task.encode()),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.cancel()
            return AgentJobResult(
                agent_name=self.name,
                status="timeout",
                output=f"{self.name} timed out after {self._timeout_seconds}s.",
            )
        raw = stdout + stderr
        clipped = raw[: self._output_cap_bytes]
        suffix = "\n[output clipped]" if len(raw) > len(clipped) else ""
        output = _redact(clipped.decode(errors="replace")) + suffix
        return AgentJobResult(
            agent_name=self.name,
            status="completed" if self._proc.returncode == 0 else "failed",
            output=output,
            exit_code=self._proc.returncode,
            artifacts=({"cwd": _redact_path(cwd), "outputBytes": len(raw)},),
        )

    def cancel(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()

    def _resolve_cwd(self, requested: str) -> Path:
        base = self._default_path
        if self.cwd_policy == "request" and requested:
            base = Path(requested).expanduser()
        elif self.cwd_policy not in {"request", "filesystem_default_path"}:
            raise ValueError(f"Unsupported cwdPolicy: {self.cwd_policy}")
        resolved = base.resolve()
        if not any(resolved == root or root in resolved.parents for root in self._roots):
            raise PermissionError(f"Access denied: {resolved} is outside configured filesystem roots.")
        return resolved


class AgentAdapterRegistry:
    """Container for enabled, configured, and detection-only external agents."""

    def __init__(self, *, enabled: bool, config: AgentConfigLoadResult, detections: tuple[ExternalAgentAdapter, ...]):
        self.enabled = enabled
        self.config = config
        self._detections = {adapter.name: adapter for adapter in detections}
        self._configured = {adapter.name: adapter for adapter in config.adapters}

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        allowed_roots: tuple[Path, ...],
        default_path: Path,
    ) -> "AgentAdapterRegistry":
        enabled = bool(getattr(settings, "EXTERNAL_AGENT_TOOLS_ENABLED", False))
        config_path = Path(getattr(settings, "EXTERNAL_AGENT_CONFIG_PATH", "~/.config/eyra/agents.json")).expanduser()
        config = load_agent_config(
            config_path,
            allowed_roots=allowed_roots,
            default_path=default_path,
        )
        detections = (
            DetectionOnlyAgentAdapter("codex", binaries=("codex",)),
            DetectionOnlyAgentAdapter("openhands", binaries=("openhands",)),
            DetectionOnlyAgentAdapter("openclaw", binaries=("openclaw",)),
            DetectionOnlyAgentAdapter("browser-use", binaries=("browser-use",)),
            DetectionOnlyAgentAdapter("mcp-backed-agent", binaries=("npx", "uvx")),
        )
        return cls(enabled=enabled, config=config, detections=detections)

    def get(self, name: str) -> ExternalAgentAdapter | None:
        if not self.enabled:
            return None
        return self._configured.get(name)

    async def run(self, spec: AgentJobSpec) -> AgentJobResult:
        adapter = self.get(spec.agent_name)
        if adapter is None:
            return AgentJobResult(
                agent_name=spec.agent_name,
                status="unknown",
                output=f"External agent '{spec.agent_name}' is not enabled or configured.",
            )
        return await adapter.run(spec)

    def capability_snapshot(self) -> dict[str, Any]:
        agents = {name: _capability_dict(adapter.capabilities) for name, adapter in self._detections.items()}
        agents.update({name: _capability_dict(adapter.capabilities) for name, adapter in self._configured.items()})
        return {
            "enabled": self.enabled,
            "config": {"status": self.config.status, "reason": self.config.reason},
            "agents": agents,
        }


def load_agent_config(
    path: Path,
    *,
    allowed_roots: tuple[Path, ...] = (),
    default_path: Path | None = None,
) -> AgentConfigLoadResult:
    path = path.expanduser()
    if not path.exists():
        return AgentConfigLoadResult(status="missing", reason=f"No external agent config at {_redact_path(path)}.")
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return AgentConfigLoadResult(status="invalid", reason=f"Could not read external agent config: {exc}")
    adapters: list[ExternalAgentAdapter] = []
    default_root = default_path or Path.cwd()
    roots = tuple(allowed_roots or (default_root,))
    for item in payload.get("agents", []):
        if item.get("type") != "cli":
            return AgentConfigLoadResult(status="invalid", reason=f"Unsupported agent type for {item.get('name')}.")
        command = item.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
            return AgentConfigLoadResult(status="invalid", reason="CLI agent command must be static argv list.")
        name = str(item.get("name") or "").strip()
        if not name:
            return AgentConfigLoadResult(status="invalid", reason="Each agent needs a name.")
        adapters.append(
            ConfiguredCliAgentAdapter(
                name=name,
                command=tuple(command),
                cwd_policy=str(item.get("cwdPolicy", "filesystem_default_path")),
                allowed_roots=roots,
                default_path=default_root,
                network=bool(item.get("network", False)),
                mutates_files=bool(item.get("mutatesFiles", False)),
                requires_approval=bool(item.get("requiresApproval", True)),
                timeout_seconds=int(item.get("timeoutSeconds", _DEFAULT_TIMEOUT_SECONDS)),
                output_cap_bytes=int(item.get("outputCapBytes", _DEFAULT_OUTPUT_CAP_BYTES)),
            )
        )
    return AgentConfigLoadResult(status="loaded", adapters=tuple(adapters))


def _capability_dict(capabilities: AgentAdapterCapabilities) -> dict[str, Any]:
    return {
        "name": capabilities.name,
        "kind": capabilities.kind,
        "available": capabilities.available,
        "local": capabilities.local,
        "canMutateFiles": capabilities.can_mutate_files,
        "canUseNetwork": capabilities.can_use_network,
        "requiresApproval": capabilities.requires_approval,
        "riskLevel": capabilities.risk_level.value,
        "configRequirements": list(capabilities.config_requirements),
    }


def _redact(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]" if match.groups() else "[REDACTED]", redacted)
    redacted = re.sub(r"/Users/[^/\s]+", "~/[user]", redacted)
    return redacted


def _redact_path(path: Path) -> str:
    return _redact(str(path.expanduser()))
