"""Tests for stdio MCP listing and tool calls."""

import asyncio
import json
import os
import stat
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.approval import ApprovalManager
from tools.mcp_stdio import CallMcpTool, ListMcpTools


def _run(coro):
    return asyncio.run(coro)


def _fake_mcp_server(tmp_path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(
        textwrap.dedent(
            r'''
            #!/usr/bin/env python3
            import json
            import sys


            def read_msg():
                headers = {}
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line in (b"\r\n", b"\n"):
                        break
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                body = sys.stdin.buffer.read(int(headers["content-length"]))
                return json.loads(body)


            def write_msg(payload):
                raw = json.dumps(payload).encode()
                sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
                sys.stdout.buffer.flush()


            while True:
                msg = read_msg()
                if msg is None:
                    break
                method = msg.get("method")
                if "id" not in msg:
                    continue
                if method == "initialize":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
                elif method == "tools/list":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}})
                elif method == "tools/call":
                    text = msg["params"]["arguments"]["text"]
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "echo: " + text}]}})
            '''
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _hanging_mcp_server(tmp_path):
    script = tmp_path / "hang_mcp.py"
    script.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import time
            time.sleep(30)
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _large_output_mcp_server(tmp_path):
    script = tmp_path / "large_mcp.py"
    script.write_text(
        textwrap.dedent(
            r'''
            #!/usr/bin/env python3
            import json
            import sys

            def read_msg():
                headers = {}
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line in (b"\r\n", b"\n"):
                        break
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))

            def write_msg(payload):
                raw = json.dumps(payload).encode()
                sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
                sys.stdout.buffer.flush()

            while True:
                msg = read_msg()
                if msg is None:
                    break
                if "id" not in msg:
                    continue
                if msg.get("method") == "initialize":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "large", "version": "1"}}})
                elif msg.get("method") == "tools/call":
                    write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "x" * 20000}]}})
            '''
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _config(tmp_path, script):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(script)]}}}))
    return path


class TestMcpStdioTools:
    def test_missing_config_fails_cleanly(self, tmp_path):
        result = _run(ListMcpTools(config_path=tmp_path / "missing.json").execute(server="fake"))

        assert "MCP server is not configured" in result.content

    def test_unknown_server_fails_cleanly(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))

        result = _run(ListMcpTools(config_path=config).execute(server="missing"))

        assert "MCP server is not configured" in result.content

    def test_lists_tools_from_configured_stdio_server(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))

        result = _run(ListMcpTools(config_path=config).execute(server="fake"))

        data = json.loads(result.content)
        assert data["server"] == "fake"
        assert data["tools"][0]["name"] == "echo"

    def test_call_requires_server_side_approval(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))
        manager = ApprovalManager()

        result = _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="echo",
                arguments={"text": "hi"},
                confirmed=True,
            )
        )

        assert "Approval required" in result.content
        assert "echo: hi" not in result.content

    def test_calls_tool_after_exact_approval(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))
        manager = ApprovalManager()
        first = _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="echo",
                arguments={"text": "hi"},
            )
        )
        approval_id = manager.list_pending()[0].id
        assert "Approval required" in first.content
        assert manager.approve(approval_id) is True

        result = _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="echo",
                arguments={"text": "hi"},
                approval_id=approval_id,
            )
        )

        assert "echo: hi" in result.content

    def test_approval_cannot_be_reused_for_different_mcp_arguments(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))
        manager = ApprovalManager()
        _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="echo",
                arguments={"text": "hi"},
            )
        )
        approval_id = manager.list_pending()[0].id
        assert manager.approve(approval_id) is True

        result = _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="echo",
                arguments={"text": "changed"},
                approval_id=approval_id,
            )
        )

        assert "Approval required" in result.content
        assert "echo: changed" not in result.content

    def test_server_timeout_is_clean(self, tmp_path):
        config = _config(tmp_path, _hanging_mcp_server(tmp_path))

        result = _run(ListMcpTools(config_path=config, timeout=0.01).execute(server="fake"))

        assert "timed out" in result.content.lower()

    def test_tool_output_is_capped(self, tmp_path):
        config = _config(tmp_path, _large_output_mcp_server(tmp_path))
        manager = ApprovalManager()
        _run(CallMcpTool(config_path=config, approval_manager=manager).execute(server="fake", tool="large", arguments={}))
        approval_id = manager.list_pending()[0].id
        manager.approve(approval_id)

        result = _run(
            CallMcpTool(config_path=config, approval_manager=manager).execute(
                server="fake",
                tool="large",
                arguments={},
                approval_id=approval_id,
            )
        )

        assert len(result.content) < 5000
        assert "truncated" in result.content.lower()
