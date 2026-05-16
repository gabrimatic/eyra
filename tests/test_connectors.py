"""Tests for Eyra's universal connector contract."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from chat.session_state import InteractionStyle, QualityMode
from runtime.connectors.cli import main as connectors_cli
from runtime.connectors.manifest import parse_connector_config
from runtime.connectors.registry import ConnectorRegistry
from runtime.connectors.types import AcceptanceState, ConnectorJobSpec
from runtime.jobs import DurableJobStore, JobStatus
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


def test_connectors_validate_missing_config_is_successful_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONNECTORS_CONFIG_PATH", str(tmp_path / "missing.json"))

    exit_code = connectors_cli(["validate", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["config"]["status"] == "missing"


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


def test_runner_payload_omits_undeclared_cwd_and_selected_files(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "import json,sys; print(json.dumps(json.load(sys.stdin)))"],
                privacy={"dataSent": ["task"], "destination": "local_process", "leavesMachine": False},
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

    result = _run(
        registry.run(
            ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path), job_id="job-task-only")
        )
    )
    payload = json.loads(result.output)

    assert result.status == "completed"
    assert set(payload) == {"connectorId", "jobId", "task"}
    assert payload["connectorId"] == "openclawnew"
    assert payload["jobId"] == "job-task-only"
    assert payload["task"] == "hello"
    assert "cwd" not in payload
    assert "selectedFiles" not in payload
    assert "source" not in payload


def test_runner_payload_includes_declared_sandboxed_cwd(tmp_path):
    capture = tmp_path / "payload.json"
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [
                    sys.executable,
                    "-c",
                    f"import json,sys,pathlib; p=json.load(sys.stdin); pathlib.Path({str(capture)!r}).write_text(json.dumps(p)); print('{{\"status\":\"ok\"}}')",
                ],
                canReadFiles=True,
                privacy={"dataSent": ["task", "cwd"], "destination": "local_process", "leavesMachine": False},
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
    payload = json.loads(capture.read_text())

    assert result.status == "completed"
    assert payload["cwd"] == str(tmp_path.resolve())


def test_runner_payload_sends_selected_files_only_when_declared_and_sandbox_valid(tmp_path):
    selected = tmp_path / "note.txt"
    selected.write_text("local")
    capture = tmp_path / "payload.json"
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [
                    sys.executable,
                    "-c",
                    f"import json,sys,pathlib; p=json.load(sys.stdin); pathlib.Path({str(capture)!r}).write_text(json.dumps(p)); print('{{\"status\":\"ok\"}}')",
                ],
                canReadFiles=True,
                privacy={"dataSent": ["task", "selected_files"], "destination": "local_process", "leavesMachine": False},
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

    result = _run(
        registry.run(
            ConnectorJobSpec(
                connector_id="openclawnew",
                task="hello",
                cwd=str(tmp_path),
                selected_files=(str(selected),),
            )
        )
    )
    outside = _run(
        registry.run(
            ConnectorJobSpec(
                connector_id="openclawnew",
                task="hello",
                cwd=str(tmp_path),
                selected_files=("/etc/hosts",),
            )
        )
    )

    payload = json.loads(capture.read_text())
    assert result.status == "completed"
    assert payload["selectedFiles"] == [str(selected.resolve())]
    assert outside.status == "blocked"
    assert "outside connector sandbox" in outside.output


def test_runner_refuses_task_when_privacy_omits_task(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "import json,sys; print(json.dumps(json.load(sys.stdin)))"],
                privacy={"dataSent": [], "destination": "local_process", "leavesMachine": False},
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

    assert result.status == "blocked"
    assert "task is not declared" in result.output


def test_runner_refuses_unsupported_declared_privacy_payloads(tmp_path):
    data_classes = [
        ("file_contents", {"canReadFiles": True}),
        ("pdf", {"canReadFiles": True}),
        ("pdf_text", {"canReadFiles": True}),
        ("screenshot", {"canControlUI": True}),
        ("clipboard", {}),
    ]

    for data_class, capabilities in data_classes:
        config_path = tmp_path / "connectors.json"
        config_path.write_text(
            json.dumps(
                _manifest(
                    [sys.executable, "-c", "import json,sys; print(json.dumps(json.load(sys.stdin)))"],
                    privacy={
                        "dataSent": ["task", data_class],
                        "destination": "local_process",
                        "leavesMachine": False,
                    },
                    acceptance={"requiresHumanApproval": False},
                    **capabilities,
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
        if result.status == "approval_required":
            approvals.approve(result.approval_id)
            result = _run(
                registry.run(
                    ConnectorJobSpec(
                        connector_id="openclawnew",
                        task="hello",
                        cwd=str(tmp_path),
                        approval_id=result.approval_id,
                    )
                )
            )

        assert result.status == "blocked"
        assert data_class in result.output


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


def test_remote_connector_payload_omits_undeclared_fields(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received["payload"] = json.loads(body.decode())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [],
                type="http_remote",
                endpoint=f"http://127.0.0.1:{server.server_port}/connector",
                local=False,
                canUseNetwork=True,
                privacy={"dataSent": ["task"], "destination": "http://127.0.0.1/connector", "leavesMachine": True},
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    try:
        approvals = ApprovalManager()
        registry = ConnectorRegistry.from_settings(
            _settings(tmp_path, CONNECTORS_ALLOW_REMOTE=True, NETWORK_TOOLS_ENABLED=True),
            approvals=approvals,
        )
        registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
            "openclawnew",
            AcceptanceState.ACCEPTED,
            "accepted",
        )

        first = _run(registry.run(ConnectorJobSpec(connector_id="openclawnew", task="hello", cwd=str(tmp_path))))
        assert first.status == "approval_required"
        approvals.approve(first.approval_id)
        result = _run(
            registry.run(
                ConnectorJobSpec(
                    connector_id="openclawnew",
                    task="hello",
                    cwd=str(tmp_path),
                    approval_id=first.approval_id,
                )
            )
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.status == "completed"
    assert received["payload"]["task"] == "hello"
    assert "cwd" not in received["payload"]
    assert "selectedFiles" not in received["payload"]


def test_missing_executable_is_reported(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(json.dumps(_manifest(["definitely-missing-eyra-connector"])))
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())

    accepted = _run(registry.test("openclawnew"))

    assert accepted.state == AcceptanceState.ACCEPTANCE_FAILED
    assert "transport" in accepted.reason


def test_acceptance_approval_required_is_not_accepted_until_approved(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [
                    sys.executable,
                    "-c",
                    "import json,sys; p=json.load(sys.stdin); print(json.dumps({'status':'ok','task':p['task']}))",
                ],
                requiresApproval=True,
                riskTier="delegated_agent",
                acceptance={
                    "testTask": "status",
                    "expectedOutputContains": "status",
                    "requiresHumanApproval": True,
                },
            )
        )
    )
    approvals = ApprovalManager()
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=approvals)

    first = _run(registry.test("openclawnew"))
    pending = approvals.list_pending()
    assert first.state == AcceptanceState.AVAILABLE
    assert first.state != AcceptanceState.ACCEPTED
    assert "approval required" in first.reason.lower()
    assert pending and pending[0].tool_name == "run_connector_task"

    approvals.approve(pending[0].id)
    second = _run(registry.test("openclawnew", approval_id=pending[0].id))

    assert second.state == AcceptanceState.ACCEPTED


def test_acceptance_output_is_capped_and_redacted(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "print('token=secret-token ' + 'x' * 5000)"],
                outputMode="stdout_text",
                outputCapBytes=1024,
                acceptance={"testTask": "status", "requiresHumanApproval": False},
            )
        )
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager())

    result = _run(registry.test("openclawnew"))
    rendered = json.dumps({"reason": result.reason, "checks": result.checks})

    assert result.state == AcceptanceState.ACCEPTED
    assert "secret-token" not in rendered
    assert "output clipped" in rendered


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


def test_connector_job_records_logs_artifacts_and_operation_ledger(tmp_path):
    config_path = tmp_path / "connectors.json"
    config_path.write_text(
        json.dumps(
            _manifest(
                [sys.executable, "-c", "import json,sys; p=json.load(sys.stdin); print(json.dumps({'status':'ok','task':p['task']}))"],
                acceptance={"requiresHumanApproval": False},
            )
        )
    )
    store = DurableJobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(
        title="Connector job",
        original_user_input="ask openclawnew to report status",
        source_frontend="test",
        id="job-openclawnew",
    )
    registry = ConnectorRegistry.from_settings(_settings(tmp_path), approvals=ApprovalManager(), job_store=store)
    registry._acceptance["openclawnew"] = registry._acceptance["openclawnew"].__class__(
        "openclawnew",
        AcceptanceState.ACCEPTED,
        "accepted",
    )

    result = _run(
        registry.run(
            ConnectorJobSpec(
                connector_id="openclawnew",
                task="hello",
                cwd=str(tmp_path),
                job_id=job.id,
            )
        )
    )
    stored = store.get_job(job.id)
    logs = store.list_logs(job.id)
    operations = store.list_operations(job.id)

    assert result.status == "completed"
    assert stored is not None
    assert stored.status == JobStatus.COMPLETED
    assert stored.artifacts and stored.artifacts[0]["connectorId"] == "openclawnew"
    assert "hello" in (stored.final_result or "")
    assert [log.message for log in logs] == ["Connector job started.", "Connector process exited."]
    assert operations and operations[0].target == "openclawnew"
    assert operations[0].success is True


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
        text="ask openclawnew to inspect private_file.txt and say token=secret",
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
    assert "private_file.txt" not in rendered
    assert "private_file.txt" not in payload
    assert "inspect private_file" not in rendered
    assert decision.trace.connector["connectorId"] == "openclawnew"
