"""Tests for Eyra's universal connector contract."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from chat.session_state import InteractionStyle, QualityMode
from runtime.connectors.manifest import parse_connector_config
from runtime.connectors.registry import ConnectorRegistry
from runtime.connectors.types import AcceptanceState, ConnectorJobSpec
from runtime.models import PreflightResult
from runtime.routing.router import RuntimeRouter
from runtime.routing.trace import format_route_trace, trace_to_dict
from runtime.routing.types import Capability, ExecutionClass, RequestEnvelope, RequestSource
from runtime.tooling import build_tool_registry
from tools.approval import ApprovalManager
from utils.settings import Settings


def _settings(tmp_path, **overrides):
    return Settings(
        CONNECTORS_ENABLED=True,
        CONNECTORS_CONFIG_PATH=str(tmp_path / "connectors.json"),
        CONNECTORS_ALLOWED_ROOTS=str(tmp_path),
        FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
        FILESYSTEM_DEFAULT_PATH=str(tmp_path),
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
        **overrides,
    )


def _manifest(command, **overrides):
    data = {
        "id": "openclawnew",
        "displayName": "OpenClawNew",
        "type": "cli",
        "enabled": True,
        "command": command,
        "cwdPolicy": "filesystem_default_path",
        "inputMode": "stdin_json",
        "outputMode": "stdout_json",
        "local": True,
        "canUseNetwork": False,
        "canReadFiles": False,
        "canMutateFiles": False,
        "canControlUI": False,
        "canRunShell": False,
        "requiresApproval": False,
        "riskTier": "read_only",
        "timeoutSeconds": 5,
        "outputCapBytes": 4096,
        "allowedTools": [],
        "deniedTools": ["delete_permanently", "run_command"],
        "privacy": {"dataSent": ["task"], "destination": "local_process", "leavesMachine": False},
        "acceptance": {
            "healthCommand": [sys.executable, "-c", "print('status health')"],
            "testTask": "Print status.",
            "expectedOutputContains": "status",
            "requiresHumanApproval": False,
        },
    }
    data.update(overrides)
    return {"connectors": [data]}


def _run(coro):
    return asyncio.run(coro)


def test_manifest_parses_static_cli_connector(tmp_path):
    payload = _manifest([sys.executable, "-c", "print('{\"status\":\"ok\"}')"])

    result = parse_connector_config(payload, settings=_settings(tmp_path))

    assert result.status == "loaded"
    manifest = result.manifests[0]
    assert manifest.id == "openclawnew"
    assert manifest.command[0] == sys.executable
    assert manifest.privacy.data_sent == ("task",)


def test_manifest_rejects_dynamic_command_string(tmp_path):
    payload = _manifest("openclawnew run {task}")

    result = parse_connector_config(payload, settings=_settings(tmp_path))

    assert result.status == "invalid"
    assert "static argv" in result.reason


def test_manifest_rejects_shell_interpolation(tmp_path):
    payload = _manifest(["bash", "-c", "echo $TASK"])

    result = parse_connector_config(payload, settings=_settings(tmp_path))

    assert result.status == "invalid"
    assert "shell interpreter" in result.reason


def test_remote_connector_disabled_by_default(tmp_path):
    payload = _manifest(
        [],
        type="http_remote",
        endpoint="https://example.com/connector",
        local=False,
        canUseNetwork=True,
        privacy={"dataSent": ["task"], "destination": "https://example.com", "leavesMachine": True},
    )

    result = parse_connector_config(payload, settings=_settings(tmp_path, CONNECTORS_ALLOW_REMOTE=False))

    assert result.status == "invalid"
    assert "CONNECTORS_ALLOW_REMOTE=false" in result.reason


def test_remote_connector_requires_explicit_opt_in(tmp_path):
    payload = _manifest(
        [],
        type="http_remote",
        endpoint="https://example.com/connector",
        local=False,
        canUseNetwork=True,
        privacy={"dataSent": ["task"], "destination": "https://example.com", "leavesMachine": True},
    )

    result = parse_connector_config(
        payload,
        settings=_settings(tmp_path, CONNECTORS_ALLOW_REMOTE=True),
    )

    assert result.status == "loaded"


def test_remote_connector_routes_as_remote_with_network_capability(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [],
                type="http_remote",
                endpoint="https://example.com/connector",
                local=False,
                canUseNetwork=True,
                privacy={"dataSent": ["task"], "destination": "https://example.com", "leavesMachine": True},
            )
        )
    )
    settings = _settings(tmp_path, CONNECTORS_ALLOW_REMOTE=True, NETWORK_TOOLS_ENABLED=True)
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=settings.all_model_names,
        tool_capability_checked_models=settings.all_model_names,
        tool_capable_models=settings.all_model_names,
    )
    envelope = RequestEnvelope(
        text="ask openclawnew to check status",
        source=RequestSource.TEST,
        interaction_style=InteractionStyle.TEXT,
        quality_mode=QualityMode.BALANCED,
        messages=[],
        current_goal=None,
        is_worker=True,
        settings=settings,
        preflight=preflight,
    )
    registry = ConnectorRegistry.from_settings(settings, approvals=ApprovalManager())

    decision = _run(
        RuntimeRouter(ComplexityScorer()).route(
            envelope,
            tool_registry=build_tool_registry(settings),
            connector_registry=registry,
        )
    )

    assert decision.execution_class == ExecutionClass.CONNECTOR_REMOTE
    assert Capability.CONNECTOR_REMOTE in decision.required_capabilities
    assert Capability.CONNECTOR_NETWORK in decision.required_capabilities


def test_manifest_rejects_privacy_file_mismatch(tmp_path):
    payload = _manifest(
        [sys.executable, "-c", "print('{\"status\":\"ok\"}')"],
        privacy={"dataSent": ["task", "selected_files"], "destination": "local_process", "leavesMachine": False},
        canReadFiles=False,
    )

    result = parse_connector_config(payload, settings=_settings(tmp_path))

    assert result.status == "invalid"
    assert "file data" in result.reason


def test_registry_acceptance_then_local_cli_run(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [
                    sys.executable,
                    "-c",
                    "import json,sys; p=json.load(sys.stdin); print(json.dumps({'status':'ok','task':p['task']}))",
                ]
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())

    blocked = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))
    accepted = _run(registry.test("openclawnew"))
    result = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))

    assert blocked.status == "blocked"
    assert accepted.state == AcceptanceState.ACCEPTED
    assert result.status == "completed"
    assert '"task": "hello"' in result.output


def test_registry_redacts_connector_destination(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('{\"status\":\"ok\"}')"],
                privacy={
                    "dataSent": ["task"],
                    "destination": "https://example.com/hook?api_key=secret-token",
                    "leavesMachine": False,
                },
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())

    rendered = json.dumps(registry.snapshot_for("openclawnew"))

    assert "secret-token" not in rendered
    assert "[REDACTED]" in rendered


def test_missing_executable_is_reported(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(json.dumps(_manifest(["definitely-missing-eyra-connector"])))
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())

    accepted = _run(registry.test("openclawnew"))

    assert accepted.state == AcceptanceState.ACCEPTANCE_FAILED
    assert "transport" in accepted.reason


def test_runner_enforces_timeout(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeoutSeconds=1,
                outputMode="stdout_text",
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    result = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))

    assert result.status == "timeout"


def test_runner_caps_and_redacts_output(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('token=secret-token ' + 'x' * 5000)"],
                outputMode="stdout_text",
                outputCapBytes=1024,
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    result = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))

    assert result.status == "completed"
    assert "secret-token" not in result.output
    assert "[output clipped]" in result.output


def test_sandbox_refuses_cwd_outside_allowed_roots(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('{\"status\":\"ok\"}')"],
                cwdPolicy="request",
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    result = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd="/")))

    assert result.status == "blocked"
    assert "outside connector sandbox" in result.output


def test_file_write_connector_requires_approval(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('{\"status\":\"ok\"}')"],
                canReadFiles=True,
                canMutateFiles=True,
                requiresApproval=True,
                riskTier="low_risk_change",
                privacy={"dataSent": ["task", "cwd"], "destination": "local_process", "leavesMachine": False},
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    approvals = ApprovalManager()
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=approvals)
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    result = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))

    assert result.status == "approval_required"
    assert approvals.list_pending()[0].tool_name == "run_connector_task"


def test_cancel_running_connector_job(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                outputMode="stdout_text",
                timeoutSeconds=5,
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    async def run_and_cancel():
        task = asyncio.create_task(
            registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path), job_id="job-1"))
        )
        await asyncio.sleep(0.1)
        cancelled = registry.cancel("job-1")
        result = await task
        return cancelled, result

    cancelled, result = _run(run_and_cancel())

    assert cancelled is True
    assert result.status == "cancelled"


def test_connector_route_trace_is_redacted_and_policy_owned(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('{\"status\":\"ok\"}')"],
                canReadFiles=True,
                canMutateFiles=True,
                requiresApproval=True,
                riskTier="delegated_agent",
                privacy={"dataSent": ["task", "cwd"], "destination": "local_process", "leavesMachine": False},
            )
        )
    )
    settings = _settings(tmp_path)
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=settings.all_model_names,
        tool_capability_checked_models=settings.all_model_names,
        tool_capable_models=settings.all_model_names,
    )
    envelope = RequestEnvelope(
        text="ask openclawnew to inspect /Users/soroush/private token=secret",
        source=RequestSource.TEST,
        interaction_style=InteractionStyle.TEXT,
        quality_mode=QualityMode.BALANCED,
        messages=[],
        current_goal=None,
        is_worker=True,
        settings=settings,
        preflight=preflight,
    )
    registry = ConnectorRegistry.from_settings(settings, approvals=ApprovalManager())

    decision = _run(
        RuntimeRouter(ComplexityScorer()).route(
            envelope,
            tool_registry=build_tool_registry(settings),
            connector_registry=registry,
        )
    )
    rendered = format_route_trace(decision.trace)
    payload = json.dumps(trace_to_dict(decision.trace))

    assert decision.execution_class == ExecutionClass.CONNECTOR_FILE_WRITE
    assert Capability.CONNECTOR_FILE_WRITE in decision.required_capabilities
    assert decision.tool_policy.allowed_tool_names == frozenset()
    assert "secret" not in rendered
    assert "/Users/soroush" not in payload
    assert decision.trace.connector["connectorId"] == "openclawnew"
