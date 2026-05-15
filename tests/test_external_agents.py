"""Tests for optional external agent adapter architecture."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.external_agents import AgentAdapterRegistry, AgentJobSpec, load_agent_config
from runtime.tooling import build_tool_registry
from tools.approval import ApprovalManager
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class TestExternalAgentAdapters:
    def test_registry_disabled_by_default_reports_builtin_agents_only(self, tmp_path):
        registry = AgentAdapterRegistry.from_settings(
            Settings(EXTERNAL_AGENT_TOOLS_ENABLED=False),
            allowed_roots=(tmp_path,),
            default_path=tmp_path,
        )

        snapshot = registry.capability_snapshot()

        assert snapshot["enabled"] is False
        assert {"codex", "openhands", "openclaw", "browser-use"}.issubset(snapshot["agents"])
        assert registry.get("codex") is None

    def test_missing_config_is_reported_without_fake_integration(self, tmp_path):
        registry = AgentAdapterRegistry.from_settings(
            Settings(EXTERNAL_AGENT_TOOLS_ENABLED=True, EXTERNAL_AGENT_CONFIG_PATH=str(tmp_path / "missing.json")),
            allowed_roots=(tmp_path,),
            default_path=tmp_path,
        )

        snapshot = registry.capability_snapshot()

        assert snapshot["enabled"] is True
        assert snapshot["config"]["status"] == "missing"

    def test_configured_cli_agent_uses_static_argv_and_sandboxed_cwd(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "echo-agent",
                            "type": "cli",
                            "command": [
                                sys.executable,
                                "-c",
                                "import sys; print('configured:' + sys.argv[1])",
                                "ok",
                            ],
                            "cwdPolicy": "filesystem_default_path",
                            "network": False,
                            "mutatesFiles": False,
                            "requiresApproval": False,
                            "timeoutSeconds": 5,
                        }
                    ]
                }
            )
        )
        registry = AgentAdapterRegistry.from_settings(
            Settings(EXTERNAL_AGENT_TOOLS_ENABLED=True, EXTERNAL_AGENT_CONFIG_PATH=str(config_path)),
            allowed_roots=(tmp_path,),
            default_path=tmp_path,
        )

        result = _run(registry.run(AgentJobSpec(agent_name="echo-agent", task="ignored", cwd=str(tmp_path))))

        assert result.status == "completed"
        assert "configured:ok" in result.output

    def test_configured_cli_agent_refuses_cwd_outside_sandbox(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "echo-agent",
                            "type": "cli",
                            "command": [sys.executable, "-c", "print('ok')"],
                            "cwdPolicy": "request",
                            "network": False,
                            "mutatesFiles": False,
                            "requiresApproval": False,
                            "timeoutSeconds": 5,
                        }
                    ]
                }
            )
        )
        registry = AgentAdapterRegistry.from_settings(
            Settings(EXTERNAL_AGENT_TOOLS_ENABLED=True, EXTERNAL_AGENT_CONFIG_PATH=str(config_path)),
            allowed_roots=(tmp_path,),
            default_path=tmp_path,
        )

        result = _run(registry.run(AgentJobSpec(agent_name="echo-agent", task="ignored", cwd="/")))

        assert result.status == "blocked"
        assert "Access denied" in result.output

    def test_load_agent_config_rejects_dynamic_shell_command_strings(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(json.dumps({"agents": [{"name": "bad", "type": "cli", "command": "echo nope"}]}))

        config = load_agent_config(config_path)

        assert config.status == "invalid"
        assert "static argv" in config.reason

    def test_load_agent_config_rejects_non_object_payload(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(json.dumps([]))

        config = load_agent_config(config_path)

        assert config.status == "invalid"
        assert "JSON object" in config.reason

    def test_load_agent_config_rejects_invalid_timeout(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "bad",
                            "type": "cli",
                            "command": [sys.executable, "-c", "print('ok')"],
                            "timeoutSeconds": "soon",
                        }
                    ]
                }
            )
        )

        config = load_agent_config(config_path)

        assert config.status == "invalid"
        assert "timeout" in config.reason

    def test_agent_task_tool_requires_configured_static_adapter_after_approval(self, tmp_path):
        approvals = ApprovalManager()
        registry = build_tool_registry(
            Settings(
                AGENT_TOOLS_ENABLED=True,
                EXTERNAL_AGENT_TOOLS_ENABLED=False,
                FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
                FILESYSTEM_DEFAULT_PATH=str(tmp_path),
            ),
            approval_manager=approvals,
        )

        first = _run(
            registry.execute(
                "run_codex_task",
                json.dumps({"task": "inspect files", "cwd": str(tmp_path)}),
            )
        )
        pending = approvals.list_pending()
        assert "Approval required" in first.content
        assert len(pending) == 1
        approvals.approve(pending[0].id)

        second = _run(
            registry.execute(
                "run_codex_task",
                json.dumps({"task": "inspect files", "cwd": str(tmp_path), "approval_id": pending[0].id}),
            )
        )

        assert "not configured for execution" in second.content
        assert "EXTERNAL_AGENT_CONFIG_PATH" in second.content

    def test_agent_task_approval_details_do_not_store_raw_task_or_home_path(self, tmp_path):
        approvals = ApprovalManager()
        registry = build_tool_registry(
            Settings(
                AGENT_TOOLS_ENABLED=True,
                EXTERNAL_AGENT_TOOLS_ENABLED=False,
                FILESYSTEM_ALLOWED_PATHS=str(tmp_path),
                FILESYSTEM_DEFAULT_PATH=str(tmp_path),
            ),
            approval_manager=approvals,
        )

        first = _run(
            registry.execute(
                "run_codex_task",
                json.dumps({"task": "inspect token=secret-token private.txt", "cwd": str(tmp_path)}),
            )
        )
        pending = approvals.list_pending()[0]
        rendered = json.dumps(pending.details)

        assert "Approval required" in first.content
        assert "inspect token" not in rendered
        assert "secret-token" not in rendered
        assert str(tmp_path) not in rendered
        assert pending.details["taskLength"] > 0
        assert pending.details["taskFingerprint"]
