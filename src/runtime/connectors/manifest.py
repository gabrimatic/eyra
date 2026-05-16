"""Connector manifest loading and validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from runtime.connectors.types import (
    ConnectorAcceptance,
    ConnectorConfigLoadResult,
    ConnectorCwdPolicy,
    ConnectorInputMode,
    ConnectorManifest,
    ConnectorOutputMode,
    ConnectorPrivacy,
    ConnectorRiskTier,
    ConnectorType,
)
from tools.filesystem import parse_allowed_roots
from utils.settings import Settings

_CONNECTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}
_FORBIDDEN_DATA_CLASSES = {"screenshot", "clipboard", "pdf", "pdf_text", "file_contents"}
_DYNAMIC_ARG_PATTERNS = ("{task}", "{{", "}}", "$(", "`")
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "osascript"}


def load_connector_config(settings: Settings) -> ConnectorConfigLoadResult:
    """Load and validate the configured connector manifest file."""
    path = Path(settings.CONNECTORS_CONFIG_PATH).expanduser()
    if not path.exists():
        return ConnectorConfigLoadResult(
            status="missing",
            reason=f"No connector config at {_redact_path(path)}.",
        )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return ConnectorConfigLoadResult(status="invalid", reason=f"Could not read connector config: {exc}")
    return parse_connector_config(payload, settings=settings)


def parse_connector_config(payload: Any, *, settings: Settings) -> ConnectorConfigLoadResult:
    """Validate a connector config payload and return normalized manifests."""
    if not isinstance(payload, dict):
        return ConnectorConfigLoadResult(status="invalid", reason="Connector config must be a JSON object.")
    raw_connectors = payload.get("connectors")
    if raw_connectors is None:
        return ConnectorConfigLoadResult(status="invalid", reason="Connector config needs a connectors list.")
    if not isinstance(raw_connectors, list):
        return ConnectorConfigLoadResult(status="invalid", reason="connectors must be a list.")

    roots = _connector_roots(settings)
    default_path = Path(settings.FILESYSTEM_DEFAULT_PATH).expanduser().resolve()
    errors: list[str] = []
    manifests: list[ConnectorManifest] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_connectors):
        try:
            manifest = _parse_one(raw, settings=settings, roots=roots, default_path=default_path)
        except ValueError as exc:
            errors.append(f"connectors[{index}]: {exc}")
            continue
        if manifest.id in seen_ids:
            errors.append(f"connectors[{index}]: duplicate connector id '{manifest.id}'")
            continue
        seen_ids.add(manifest.id)
        manifests.append(manifest)
    if errors:
        return ConnectorConfigLoadResult(status="invalid", reason=errors[0], errors=tuple(errors))
    return ConnectorConfigLoadResult(status="loaded", manifests=tuple(manifests))


def _parse_one(
    raw: Any,
    *,
    settings: Settings,
    roots: tuple[Path, ...],
    default_path: Path,
) -> ConnectorManifest:
    if not isinstance(raw, dict):
        raise ValueError("each connector must be a JSON object")
    connector_id = _required_str(raw, "id")
    if not _CONNECTOR_ID_RE.fullmatch(connector_id):
        raise ValueError("connector id must match ^[a-z][a-z0-9_-]{1,63}$")
    display_name = _required_str(raw, "displayName")
    connector_type = _enum(ConnectorType, _required_str(raw, "type"), "type")
    if connector_type == ConnectorType.PYTHON_MODULE and not bool(getattr(settings, "CONNECTORS_ALLOW_PYTHON_MODULE", False)):
        raise ValueError("python_module connectors require CONNECTORS_ALLOW_PYTHON_MODULE=true")
    enabled = bool(raw.get("enabled", False))
    cwd_policy = _enum(ConnectorCwdPolicy, str(raw.get("cwdPolicy", "filesystem_default_path")), "cwdPolicy")
    input_mode = _enum(ConnectorInputMode, str(raw.get("inputMode", "stdin_json")), "inputMode")
    output_mode = _enum(ConnectorOutputMode, str(raw.get("outputMode", "stdout_json")), "outputMode")
    privacy = _parse_privacy(raw.get("privacy"))
    risk_tier = _enum(ConnectorRiskTier, _required_str(raw, "riskTier"), "riskTier")
    if "requiresApproval" not in raw:
        raise ValueError("requiresApproval must be declared")
    timeout_seconds = _bounded_int(
        raw.get("timeoutSeconds", settings.CONNECTORS_TIMEOUT_SECONDS),
        "timeoutSeconds",
        minimum=1,
        maximum=max(1, settings.CONNECTORS_TIMEOUT_SECONDS),
    )
    output_cap_bytes = _bounded_int(
        raw.get("outputCapBytes", settings.CONNECTORS_OUTPUT_CAP_BYTES),
        "outputCapBytes",
        minimum=1024,
        maximum=max(1024, settings.CONNECTORS_OUTPUT_CAP_BYTES),
    )
    command = _static_argv(raw.get("command", ()), field="command") if connector_type in _command_types() else ()
    endpoint = str(raw.get("endpoint", "")).strip()
    module = str(raw.get("module", "")).strip()
    local = bool(raw.get("local", connector_type != ConnectorType.HTTP_REMOTE))
    can_use_network = bool(raw.get("canUseNetwork", connector_type == ConnectorType.HTTP_REMOTE))
    if connector_type == ConnectorType.HTTP_LOCAL:
        _validate_endpoint(endpoint, local_only=True)
    if connector_type == ConnectorType.HTTP_REMOTE:
        _validate_endpoint(endpoint, local_only=False)
        local = False
        can_use_network = True
    if connector_type == ConnectorType.PYTHON_MODULE and not module:
        raise ValueError("python_module connectors need module")
    _validate_privacy_data_classes(
        privacy,
        can_read_files=bool(raw.get("canReadFiles", False)),
        can_mutate_files=bool(raw.get("canMutateFiles", False)),
        can_control_ui=bool(raw.get("canControlUI", False)),
    )
    if (connector_type == ConnectorType.HTTP_REMOTE or not local or privacy.leaves_machine) and not settings.CONNECTORS_ALLOW_REMOTE:
        raise ValueError("remote connector refused because CONNECTORS_ALLOW_REMOTE=false")
    if can_use_network and not (settings.NETWORK_TOOLS_ENABLED or settings.CONNECTORS_ALLOW_REMOTE or connector_type == ConnectorType.HTTP_LOCAL):
        raise ValueError("network connector refused because network and remote connector opt-ins are disabled")
    cwd = str(raw.get("cwd", "")).strip()
    _validate_cwd(cwd_policy=cwd_policy, cwd=cwd, roots=roots, default_path=default_path)
    acceptance = _parse_acceptance(raw.get("acceptance", {}))
    return ConnectorManifest(
        id=connector_id,
        display_name=display_name,
        type=connector_type,
        enabled=enabled,
        command=command,
        endpoint=endpoint,
        module=module,
        cwd_policy=cwd_policy,
        cwd=cwd,
        input_mode=input_mode,
        output_mode=output_mode,
        local=local,
        can_use_network=can_use_network,
        can_read_files=bool(raw.get("canReadFiles", False)),
        can_mutate_files=bool(raw.get("canMutateFiles", False)),
        can_control_ui=bool(raw.get("canControlUI", False)),
        can_run_shell=bool(raw.get("canRunShell", False)),
        requires_approval=bool(raw.get("requiresApproval", True)),
        risk_tier=risk_tier,
        timeout_seconds=timeout_seconds,
        output_cap_bytes=output_cap_bytes,
        allowed_tools=tuple(str(item) for item in raw.get("allowedTools", []) if isinstance(item, str)),
        denied_tools=tuple(str(item) for item in raw.get("deniedTools", []) if isinstance(item, str)),
        privacy=privacy,
        acceptance=acceptance,
        allowed_roots=roots,
        default_path=default_path,
    )


def _connector_roots(settings: Settings) -> tuple[Path, ...]:
    raw = (settings.CONNECTORS_ALLOWED_ROOTS or "").strip() or settings.FILESYSTEM_ALLOWED_PATHS
    return parse_allowed_roots(raw)


def _command_types() -> set[ConnectorType]:
    return {ConnectorType.CLI, ConnectorType.MCP, ConnectorType.BROWSER_AGENT, ConnectorType.CODING_AGENT}


def _static_argv(value: Any, *, field: str) -> tuple[str, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, list) or not value or not all(isinstance(part, str) and part.strip() for part in value):
        raise ValueError(f"{field} must be a static argv list")
    argv = tuple(part.strip() for part in value)
    for part in argv:
        lowered = part.lower()
        if any(pattern in lowered for pattern in _DYNAMIC_ARG_PATTERNS):
            raise ValueError(f"{field} cannot contain model-filled placeholders or shell interpolation")
    executable = Path(argv[0]).name.lower()
    if executable in _SHELL_EXECUTABLES and any(part == "-c" for part in argv[1:]):
        raise ValueError(f"{field} cannot use a shell interpreter with -c")
    return argv


def _parse_privacy(value: Any) -> ConnectorPrivacy:
    if not isinstance(value, dict):
        raise ValueError("privacy declaration is required")
    data_sent = value.get("dataSent")
    destination = value.get("destination")
    leaves_machine = value.get("leavesMachine")
    if not isinstance(data_sent, list) or not all(isinstance(item, str) and item.strip() for item in data_sent):
        raise ValueError("privacy.dataSent must list data classes")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("privacy.destination is required")
    if not isinstance(leaves_machine, bool):
        raise ValueError("privacy.leavesMachine must be true or false")
    return ConnectorPrivacy(
        data_sent=tuple(item.strip() for item in data_sent),
        destination=destination.strip(),
        leaves_machine=leaves_machine,
    )


def _validate_privacy_data_classes(
    privacy: ConnectorPrivacy,
    *,
    can_read_files: bool,
    can_mutate_files: bool,
    can_control_ui: bool,
) -> None:
    sent = set(privacy.data_sent)
    unsupported = sorted(sent.intersection(_FORBIDDEN_DATA_CLASSES))
    if unsupported:
        raise ValueError(
            f"privacy.dataSent declares unsupported data class '{unsupported[0]}'; "
            "the current connector runner does not support sending it yet"
        )
    file_data = {"selected_files", "file_contents", "pdf", "pdf_text"}
    if sent.intersection(file_data) and not can_read_files:
        raise ValueError("privacy declares file data but canReadFiles=false")
    if "cwd" in sent and not (can_read_files or can_mutate_files):
        raise ValueError("privacy declares cwd but connector has no file capability")
    if "screenshot" in sent and not can_control_ui:
        raise ValueError("privacy declares screenshot but canControlUI=false")


def _parse_acceptance(value: Any) -> ConnectorAcceptance:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("acceptance must be a JSON object")
    return ConnectorAcceptance(
        health_command=_static_argv(value.get("healthCommand", ()), field="acceptance.healthCommand"),
        test_task=str(value.get("testTask", "")).strip(),
        expected_output_contains=str(value.get("expectedOutputContains", "")).strip(),
        requires_human_approval=bool(value.get("requiresHumanApproval", True)),
    )


def _validate_endpoint(endpoint: str, *, local_only: bool) -> None:
    if not endpoint:
        raise ValueError("HTTP connectors need endpoint")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("HTTP connector endpoint must use http or https")
    host = (parsed.hostname or "").lower()
    if local_only and host not in _LOCAL_HOSTS:
        raise ValueError("http_local endpoint must be localhost or loopback")


def _validate_cwd(
    *,
    cwd_policy: ConnectorCwdPolicy,
    cwd: str,
    roots: tuple[Path, ...],
    default_path: Path,
) -> None:
    base = default_path
    if cwd_policy == ConnectorCwdPolicy.MANIFEST:
        if not cwd:
            raise ValueError("manifest cwdPolicy requires cwd")
        base = Path(cwd).expanduser()
    resolved = base.resolve()
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("connector cwd is outside CONNECTORS_ALLOWED_ROOTS")


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _enum(enum_type, value: str, name: str):
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}")


def _bounded_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if number < minimum or number > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _redact_path(path: Path) -> str:
    return re.sub(r"/Users/[^/\s]+", "~/[user]", str(path))
