"""Tests for optional external agent adapter architecture."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.external_agents import AgentAdapterRegistry, AgentJobSpec, load_agent_config
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
