"""Tests for Eyra's local OS, agent, and MCP operator tools."""

import asyncio
import json
import os
import stat
import sys
from asyncio.subprocess import PIPE
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.external_agents import AgentAdapterRegistry
from tools.approval import ApprovalManager
from tools.operator import (
    ActivateAppTool,
    DiscoverCapabilitiesTool,
    ExtractScreenTextTool,
    FetchUrlTool,
    FileInfoTool,
    GetAccessibilityTreeTool,
    GetAgentSessionContentTool,
    GetAgentStatusTool,
    GetLaunchAgentStatusTool,
    GetSystemSnapshotTool,
    GetVoiceContextTool,
    ListAgentSessionsTool,
    ListOpenAppsTool,
    ListWindowsTool,
    ManageLaunchAgentTool,
    OpenAppTool,
    PressHotkeyTool,
    QuitAppTool,
    RunAgentTaskTool,
    RunCommandTool,
    RunShortcutTool,
    SearchFilesTool,
    SetClipboardTool,
    ShowNotificationTool,
    UiClickTool,
    UiDragTool,
    UiScrollTool,
    UiTypeTextTool,
    WindowActionTool,
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
        assert data["capabilities"]["localFirst"] is True
        assert data["capabilities"]["privacy"]["leavesMachineByDefault"] is True
        assert data["tools"]["os"] is True
        assert data["tools"]["agents"] is False
        assert data["tools"]["mcp"] is True
        assert data["voice"]["localWhisper"] is True
        assert data["voice"]["realtime"] is True
        assert data["web"]["enabled"] is True

    def test_reports_external_agent_flag_as_agent_capability(self):
        result = _run(
            DiscoverCapabilitiesTool(Settings(AGENT_TOOLS_ENABLED=False, EXTERNAL_AGENT_TOOLS_ENABLED=True)).execute()
        )
        data = json.loads(result.content)

        assert data["tools"]["agents"] is True

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


class TestRunAgentTaskToolSchema:
    def test_configured_agent_name_is_not_restricted_to_builtin_enum(self, tmp_path):
        tool = RunAgentTaskTool(allowed_roots=(tmp_path,), default_path=tmp_path)

        schema = tool.to_openai_tool()["function"]["parameters"]["properties"]["agent"]

        assert schema["type"] == "string"
        assert "enum" not in schema


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
    def test_extract_screen_text_requires_configured_local_ocr_backend(self):
        result = _run(ExtractScreenTextTool().execute())

        assert "SCREEN_OCR_COMMAND" in result.content
        assert "local OCR" in result.content

    def test_extract_screen_text_pipes_in_memory_screenshot_to_local_ocr(self):
        async def fake_capture():
            return Image.new("RGB", (2, 2), "white")

        with patch("tools.operator.capture_screenshot_in_memory", fake_capture):
            with patch("tools.operator.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "Hello screen\n"
                run.return_value.stderr = ""

                result = _run(ExtractScreenTextTool(ocr_command="ocr-local --stdin").execute())

        assert "Hello screen" in result.content
        assert run.call_args.kwargs["input"].startswith(b"\x89PNG")
        assert run.call_args.kwargs["capture_output"] is True

    def test_extract_screen_text_reports_permission_blocker_without_traceback(self):
        async def fake_capture():
            raise RuntimeError("screen permission denied")

        with patch("tools.operator.capture_screenshot_in_memory", fake_capture):
            result = _run(ExtractScreenTextTool(ocr_command="ocr-local").execute())

        assert "Screen recording permission" in result.content
        assert "traceback" not in result.content.lower()

    def test_accessibility_tree_uses_system_events(self):
        stdout = (
            "app=Finder\n"
            "window=Downloads\n"
            "AXButton|Back|enabled=true\n"
            "AXTextField|Search|enabled=true\n"
        )
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""

            result = _run(GetAccessibilityTreeTool().execute(limit=10))

        data = json.loads(result.content)
        assert data["frontmostApp"] == "Finder"
        assert data["window"] == "Downloads"
        assert data["elements"][0]["role"] == "AXButton"
        assert data["elements"][0]["title"] == "Back"
        assert run.call_args.args[0][0] == "osascript"

    def test_accessibility_tree_reports_permission_blocker_without_traceback(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "System Events got an error: Not authorized to send Apple events to System Events."

            result = _run(GetAccessibilityTreeTool().execute())

        assert "accessibility permission" in result.content.lower()
        assert "traceback" not in result.content.lower()

    def test_list_open_apps_returns_visible_app_names(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "Finder\nTerminal\n"
            run.return_value.stderr = ""

            result = _run(ListOpenAppsTool().execute())

        data = json.loads(result.content)
        assert data["apps"] == ["Finder", "Terminal"]
        assert run.call_args.args[0][0] == "osascript"

    def test_list_windows_returns_app_window_titles(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "Downloads\nDocuments\n"
            run.return_value.stderr = ""

            result = _run(ListWindowsTool().execute(app="Finder"))

        data = json.loads(result.content)
        assert data["app"] == "Finder"
        assert data["windows"] == ["Downloads", "Documents"]

    def test_window_action_requires_action_specific_approval(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = WindowActionTool(approval_manager=manager)

        pending = _run(tool.execute(action="minimize", app="Finder", window="Downloads"))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""

            result = _run(
                tool.execute(action="minimize", app="Finder", window="Downloads", approval_id=approval_id)
            )

        assert result.content == "Window minimize applied: Finder / Downloads"
        script = run.call_args.args[0][-1]
        assert "set value of attribute \"AXMinimized\"" in script
        assert "Downloads" in script

    def test_window_action_refuses_unknown_action_before_approval(self):
        manager = ApprovalManager(ttl_seconds=60)

        result = _run(WindowActionTool(approval_manager=manager).execute(action="explode", app="Finder"))

        assert "Unsupported window action" in result.content
        assert manager.list_pending() == []

    def test_activate_app_uses_system_events_without_approval(self):
        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""

            result = _run(ActivateAppTool().execute(name="Finder"))

        assert result.content == "Activated app: Finder"
        assert "System Events" in run.call_args.args[0][-1]

    def test_quit_app_requires_action_specific_approval(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = QuitAppTool(approval_manager=manager)

        pending = _run(tool.execute(name="Preview"))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""

            result = _run(tool.execute(name="Preview", approval_id=approval_id))

        assert "Quit app: Preview" in result.content
        assert "Approval required" in _run(tool.execute(name="Preview", approval_id=approval_id)).content

    def test_ui_click_requires_confirmation(self):
        result = _run(UiClickTool().execute(x=100, y=200))

        assert "Approval required" in result.content

    def test_ui_click_uses_cliclick_after_confirmation(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = UiClickTool(approval_manager=manager)
        pending = _run(tool.execute(x=100, y=200))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.shutil.which", return_value="/usr/local/bin/cliclick"):
            with patch("tools.operator.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stderr = ""

                result = _run(tool.execute(x=100, y=200, approval_id=approval_id))

        assert "Clicked: 100,200" in result.content
        assert run.call_args.args[0] == ["/usr/local/bin/cliclick", "c:100,200"]

    def test_ui_scroll_requires_confirmation_and_uses_cliclick(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = UiScrollTool(approval_manager=manager)
        pending = _run(tool.execute(direction="down", amount=4))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.shutil.which", return_value="/usr/local/bin/cliclick"):
            with patch("tools.operator.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stderr = ""

                result = _run(tool.execute(direction="down", amount=4, approval_id=approval_id))

        assert "Scrolled down by 4" in result.content
        assert run.call_args.args[0] == ["/usr/local/bin/cliclick", "w:0,-4"]

    def test_ui_drag_requires_confirmation_and_uses_cliclick(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = UiDragTool(approval_manager=manager)
        pending = _run(tool.execute(start_x=10, start_y=20, end_x=30, end_y=40))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.shutil.which", return_value="/usr/local/bin/cliclick"):
            with patch("tools.operator.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stderr = ""

                result = _run(tool.execute(start_x=10, start_y=20, end_x=30, end_y=40, approval_id=approval_id))

        assert "Dragged: 10,20 -> 30,40" in result.content
        assert run.call_args.args[0] == [
            "/usr/local/bin/cliclick",
            "m:10,20",
            "dd:10,20",
            "m:30,40",
            "du:30,40",
        ]

    def test_ui_type_text_requires_confirmation(self):
        result = _run(UiTypeTextTool().execute(text="hello"))

        assert "Approval required" in result.content

    def test_press_hotkey_invokes_osascript_after_confirmation(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = PressHotkeyTool(approval_manager=manager)
        pending = _run(tool.execute(key="s", modifiers=["command"]))
        approval_id = pending.content.split("/approve ", 1)[1].split()[0]
        assert manager.approve(approval_id) is True

        with patch("tools.operator.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stderr = ""

            result = _run(tool.execute(key="s", modifiers=["command"], approval_id=approval_id))

        assert "Pressed hotkey: command+s" in result.content
        assert run.call_args.args[0][0] == "osascript"

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

    def test_run_shortcut_requires_approval_and_invokes_shortcuts_cli(self):
        manager = ApprovalManager(ttl_seconds=60)
        tool = RunShortcutTool(approval_manager=manager)

        with patch("tools.operator.shutil.which", return_value="/usr/bin/shortcuts"):
            pending = _run(tool.execute(name="Resize Image", input_text="hello"))
            approval_id = pending.content.split("/approve ", 1)[1].split()[0]
            assert manager.approve(approval_id) is True

            with patch("tools.operator.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "ok"
                run.return_value.stderr = ""

                result = _run(tool.execute(name="Resize Image", input_text="hello", approval_id=approval_id))

        assert "Shortcut ran: Resize Image" in result.content
        assert run.call_args.args[0] == ["/usr/bin/shortcuts", "run", "Resize Image", "--input-path", "-"]
        assert run.call_args.kwargs["input"] == "hello"

    def test_run_shortcut_reports_missing_shortcuts_cli_without_approval(self):
        with patch("tools.operator.shutil.which", return_value=None):
            result = _run(RunShortcutTool().execute(name="Resize Image"))

        assert "shortcuts command is not available" in result.content

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
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "codex",
                            "type": "cli",
                            "command": ["/usr/bin/codex", "exec"],
                            "cwdPolicy": "request",
                            "requiresApproval": True,
                            "timeoutSeconds": 5,
                        }
                    ]
                }
            )
        )
        settings = Settings(EXTERNAL_AGENT_TOOLS_ENABLED=True, EXTERNAL_AGENT_CONFIG_PATH=str(config_path))
        agent_registry = AgentAdapterRegistry.from_settings(settings, allowed_roots=(tmp_path,), default_path=tmp_path)
        created = {}

        class FakeProcess:
            returncode = 0

            async def communicate(self, *_args):
                return b"ok", b""

        async def fake_create_subprocess_exec(*argv, cwd=None, stdin=None, stdout=None, stderr=None):
            created["argv"] = argv
            created["cwd"] = cwd
            created["stdin"] = stdin
            created["stdout"] = stdout
            created["stderr"] = stderr
            return FakeProcess()

        with patch("runtime.external_agents.shutil.which", return_value="/usr/bin/codex"):
            with patch("runtime.external_agents.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                manager = ApprovalManager(ttl_seconds=60)
                tool = RunAgentTaskTool(
                    allowed_roots=(tmp_path,),
                    default_path=tmp_path,
                    approval_manager=manager,
                    agent_registry=agent_registry,
                )
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
        assert created["argv"] == ("/usr/bin/codex", "exec")
        assert created["stdout"] == PIPE

    def test_agent_task_kills_child_on_timeout(self, tmp_path):
        config_path = tmp_path / "agents.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "name": "codex",
                            "type": "cli",
                            "command": ["/usr/bin/codex", "exec"],
                            "cwdPolicy": "request",
                            "requiresApproval": True,
                            "timeoutSeconds": 1,
                        }
                    ]
                }
            )
        )
        settings = Settings(EXTERNAL_AGENT_TOOLS_ENABLED=True, EXTERNAL_AGENT_CONFIG_PATH=str(config_path))
        agent_registry = AgentAdapterRegistry.from_settings(settings, allowed_roots=(tmp_path,), default_path=tmp_path)
        agent_registry.get("codex")._timeout_seconds = 0.01
        killed = {"value": False}

        class SlowProcess:
            returncode = None

            async def communicate(self, *_args):
                await asyncio.sleep(10)
                return b"", b""

            def kill(self):
                killed["value"] = True

            async def wait(self):
                self.returncode = -9

        async def fake_create_subprocess_exec(*_argv, **_kwargs):
            return SlowProcess()

        with patch("runtime.external_agents.shutil.which", return_value="/usr/bin/codex"):
            with patch("runtime.external_agents.asyncio.create_subprocess_exec", fake_create_subprocess_exec):
                manager = ApprovalManager(ttl_seconds=60)
                tool = RunAgentTaskTool(
                    allowed_roots=(tmp_path,),
                    default_path=tmp_path,
                    approval_manager=manager,
                    agent_registry=agent_registry,
                )
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
