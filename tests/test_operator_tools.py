"""Tests for Eyra's local OS, agent, and MCP operator tools."""

import asyncio
import json
import os
import stat
import sys
from asyncio.subprocess import PIPE
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.approval import ApprovalManager
from tools.operator import (
    DiscoverCapabilitiesTool,
    FetchUrlTool,
    FileInfoTool,
    GetAgentSessionContentTool,
    GetAgentStatusTool,
    GetLaunchAgentStatusTool,
    GetSystemSnapshotTool,
    GetVoiceContextTool,
    ListAgentSessionsTool,
    ManageLaunchAgentTool,
    OpenAppTool,
    RunAgentTaskTool,
    RunCommandTool,
    SearchFilesTool,
    SetClipboardTool,
    ShowNotificationTool,
)
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class TestDiscoverCapabilitiesTool:
    def test_reports_optional_capability_state(self):
        settings = Settings(
            NETWORK_TOOLS_ENABLED=True,
            OS_TOOLS_ENABLED=True,
            AGENT_TOOLS_ENABLED=False,
            MCP_TOOLS_ENABLED=True,
            REALTIME_VOICE_ENABLED=True,
            WEB_UI_ENABLED=True,
        )

        result = _run(DiscoverCapabilitiesTool(settings).execute())
        data = json.loads(result.content)

        assert data["offlineByDefault"] is True
        assert data["tools"]["os"] is True
        assert data["tools"]["agents"] is False
        assert data["tools"]["mcp"] is True
        assert data["voice"]["localWhisper"] is True
        assert data["voice"]["realtime"] is True
        assert data["web"]["enabled"] is True

    def test_voice_context_returns_runtime_summary(self):
        result = _run(GetVoiceContextTool(Settings(WEB_UI_ENABLED=True)).execute())
        data = json.loads(result.content)

        assert data["assistant"] == "Eyra"
        assert data["web"]["enabled"] is True


class TestRunCommandTool:
    def test_runs_argv_inside_allowed_root(self, tmp_path):
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        result = _run(tool.execute(argv=["python3", "-c", "print('hello')"], cwd=str(tmp_path)))

        assert "exit_code=0" in result.content
        assert "hello" in result.content

    def test_rejects_cwd_outside_allowed_root(self, tmp_path):
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        result = _run(tool.execute(argv=["pwd"], cwd="/"))

        assert "Access denied" in result.content

    def test_shell_commands_require_confirmation(self, tmp_path):
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        result = _run(tool.execute(command="echo hello", cwd=str(tmp_path)))

        assert "Approval required" in result.content
        assert "/approve" in result.content

    def test_dangerous_argv_requires_confirmation(self, tmp_path):
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        result = _run(tool.execute(argv=["rm", "-rf", "x"], cwd=str(tmp_path)))

        assert "Approval required" in result.content

    def test_confirmed_true_does_not_bypass_command_approval(self, tmp_path):
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        result = _run(tool.execute(command="echo bypass", cwd=str(tmp_path), confirmed=True))

        assert "Approval required" in result.content
        assert "exit_code=0" not in result.content

    def test_approval_id_is_action_specific_and_consumed(self, tmp_path):
        manager = ApprovalManager(ttl_seconds=60)
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path, approval_manager=manager)

        pending = _run(tool.execute(command="echo approved", cwd=str(tmp_path)))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        result = _run(tool.execute(command="echo approved", cwd=str(tmp_path), approval_id=approval_id))
        reused = _run(tool.execute(command="echo approved", cwd=str(tmp_path), approval_id=approval_id))

        assert "exit_code=0" in result.content
        assert "approved" in result.content
        assert "Approval required" in reused.content

    def test_approval_expires(self, tmp_path):
        now = {"value": 1000.0}
        manager = ApprovalManager(ttl_seconds=1, clock=lambda: now["value"])
        tool = RunCommandTool(allowed_roots=(tmp_path,), default_path=tmp_path, approval_manager=manager)

        pending = _run(tool.execute(command="echo late", cwd=str(tmp_path)))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        now["value"] = 1002.0

        assert manager.approve(approval_id) is False
        result = _run(tool.execute(command="echo late", cwd=str(tmp_path), approval_id=approval_id))
        assert "Approval required" in result.content


class TestFileInspectionTools:
    def test_file_info_returns_metadata_without_content(self, tmp_path):
        file_path = tmp_path / "note.txt"
        file_path.write_text("secret-ish local content")

        result = _run(FileInfoTool(allowed_roots=(tmp_path,), default_path=tmp_path).execute(path=str(file_path)))
        data = json.loads(result.content)

        assert data["name"] == "note.txt"
        assert data["type"] == "file"
        assert data["sizeBytes"] == len("secret-ish local content")
        assert "content" not in data

    def test_search_files_uses_python_fallback_when_rg_missing(self, tmp_path):
        (tmp_path / "a.txt").write_text("alpha beta")
        (tmp_path / "b.txt").write_text("gamma")

        with patch("tools.operator.shutil.which", return_value=None):
            result = _run(
                SearchFilesTool(allowed_roots=(tmp_path,), default_path=tmp_path).execute(
                    root=str(tmp_path),
                    query="alpha",
                    limit=5,
                )
            )

        assert "a.txt:1: alpha beta" in result.content
        assert "b.txt" not in result.content


class TestMacOperatorTools:
    def test_set_clipboard_requires_confirmation(self):
        result = _run(SetClipboardTool().execute(text="hello"))

        assert "Approval required" in result.content

    def test_set_clipboard_writes_with_confirmation(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = SetClipboardTool(approval_manager=manager)
        pending = _run(tool.execute(text="hello"))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""

            result = _run(tool.execute(text="hello", approval_id=approval_id))

        assert "Clipboard updated" in result.content
        run.assert_called_once()

    def test_show_notification_invokes_osascript(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""

            result = _run(ShowNotificationTool().execute(title="Eyra", message="Ready"))

        assert "Notification shown" in result.content
        assert run.call_args.args[0][0] == "osascript"

    def test_open_app_requires_confirmation(self):
        result = _run(OpenAppTool().execute(name="Calculator"))

        assert "Approval required" in result.content

    def test_launch_agent_status_reports_matching_labels(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "123\t0\tcom.example.agent\n-\t0\tcom.other.agent\n"
            run.return_value.stderr = ""

            result = _run(GetLaunchAgentStatusTool().execute(label="com.example"))

        data = json.loads(result.content)
        assert data["matches"][0]["label"] == "com.example.agent"

    def test_manage_launch_agent_requires_confirmation(self):
        result = _run(ManageLaunchAgentTool().execute(label="com.example.agent", action="start"))

        assert "Approval required" in result.content

    def test_system_snapshot_includes_processes_and_system_info(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "PID PPID %CPU %MEM COMM\n1 0 1.0 1.0 launchd\n"
            run.return_value.stderr = ""

            result = _run(GetSystemSnapshotTool().execute())

        assert "processes" in result.content

    def test_fetch_url_requires_http_url(self):
        result = _run(FetchUrlTool().execute(url="file:///etc/passwd"))

        assert "Only http/https" in result.content


class TestAgentSessionTools:
    def test_agent_status_counts_local_session_files(self, tmp_path):
        codex_home = tmp_path / ".codex"
        openclaw_home = tmp_path / ".openclaw"
        (codex_home / "sessions" / "2026" / "05" / "11").mkdir(parents=True)
        (openclaw_home / "agents" / "main" / "sessions").mkdir(parents=True)
        (codex_home / "sessions" / "2026" / "05" / "11" / "rollout-a.jsonl").write_text("{}\n")
        (openclaw_home / "agents" / "main" / "sessions" / "session-a.jsonl").write_text("{}\n")

        with patch("tools.operator.shutil.which", return_value="/usr/bin/fake"):
            result = _run(GetAgentStatusTool(codex_home=codex_home, openclaw_home=openclaw_home).execute())

        data = json.loads(result.content)
        assert data["codex"]["available"] is True
        assert data["codex"]["sessionCount"] == 1
        assert data["openclaw"]["sessionCount"] == 1

    def test_list_agent_sessions_returns_newest_metadata(self, tmp_path):
        codex_home = tmp_path / ".codex"
        session_dir = codex_home / "sessions" / "2026" / "05" / "11"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "rollout-2026-05-11T10-00-00-abc.jsonl"
        session_file.write_text('{"type":"session_meta","payload":{"id":"abc-session"}}\n')

        result = _run(ListAgentSessionsTool(codex_home=codex_home, openclaw_home=tmp_path / ".openclaw").execute(agent="codex"))

        data = json.loads(result.content)
        assert data["sessions"][0]["id"] == "abc-session"
        assert data["sessions"][0]["index"] == 1

    def test_get_agent_session_content_redacts_secret_like_values(self, tmp_path):
        codex_home = tmp_path / ".codex"
        session_dir = codex_home / "sessions" / "2026" / "05" / "11"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "rollout-secret.jsonl"
        session_file.write_text(
            '{"type":"session_meta","payload":{"id":"secret-session"}}\n'
            '{"role":"user","content":"token sk-abcdefghijklmnopqrstuvwxyz1234567890"}\n'
        )

        result = _run(
            GetAgentSessionContentTool(codex_home=codex_home, openclaw_home=tmp_path / ".openclaw").execute(
                agent="codex",
                session="secret",
            )
        )

        assert "sk-" not in result.content
        assert "[redacted]" in result.content


class TestRunAgentTaskTool:
    def test_agent_task_uses_bounded_async_subprocess(self, tmp_path):
        created = {}

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"ok", b""

        async def fake_create_subprocess_exec(*argv, cwd=None, stdout=None, stderr=None):
            created["argv"] = argv
            created["cwd"] = cwd
            created["stdout"] = stdout
            created["stderr"] = stderr
            return FakeProcess()

        with patch("tools.operator.shutil.which", return_value="/usr/bin/codex"):
            with patch("tools.operator.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                manager = ApprovalManager(ttl_seconds=60)
                tool = RunAgentTaskTool(allowed_roots=(tmp_path,), default_path=tmp_path, approval_manager=manager)
                pending = _run(tool.execute(agent="codex", task="do work", cwd=str(tmp_path)))
                approval_id = pending.content.split("/approve ", 1)[1].split()[0]
                assert manager.approve(approval_id) is True
                result = _run(
                    tool.execute(
                        agent="codex",
                        task="do work",
                        cwd=str(tmp_path),
                        approval_id=approval_id,
                    )
                )

        assert "exit_code=0" in result.content
        assert created["argv"] == ("/usr/bin/codex", "exec", "do work")
        assert created["stdout"] == PIPE

    def test_agent_task_kills_child_on_timeout(self, tmp_path):
        killed = {"value": False}

        class SlowProcess:
            returncode = None

            async def communicate(self):
                await asyncio.sleep(10)
                return b"", b""

            def kill(self):
                killed["value"] = True

            async def wait(self):
                self.returncode = -9

        async def fake_create_subprocess_exec(*_argv, **_kwargs):
            return SlowProcess()

        with patch("tools.operator.shutil.which", return_value="/usr/bin/codex"):
            with patch("tools.operator.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                with patch("tools.operator._AGENT_TIMEOUT_SECONDS", 0.01):
                    manager = ApprovalManager(ttl_seconds=60)
                    tool = RunAgentTaskTool(allowed_roots=(tmp_path,), default_path=tmp_path, approval_manager=manager)
                    pending = _run(tool.execute(agent="codex", task="slow", cwd=str(tmp_path)))
                    approval_id = pending.content.split("/approve ", 1)[1].split()[0]
                    assert manager.approve(approval_id) is True
                    result = _run(
                        tool.execute(
                            agent="codex",
                            task="slow",
                            cwd=str(tmp_path),
                            approval_id=approval_id,
                        )
                    )

        assert killed["value"] is True
        assert "timed out" in result.content


class TestMcpSmokeScript:
    def test_fake_mcp_script_is_executable_fixture(self, tmp_path):
        script = tmp_path / "fake_mcp.py"
        script.write_text("#!/usr/bin/env python3\nprint('ok')\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        assert os.access(script, os.X_OK)
