"""Tests for stdio MCP listing and tool calls."""

import asyncio
import json
import os
import stat
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


def _config(tmp_path, script):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(script)]}}}))
    return path


class TestMcpStdioTools:
    def test_lists_tools_from_configured_stdio_server(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))

        result = _run(ListMcpTools(config_path=config).execute(server="fake"))

        data = json.loads(result.content)
        assert data["server"] == "fake"
        assert data["tools"][0]["name"] == "echo"

    def test_calls_tool_on_configured_stdio_server(self, tmp_path):
        config = _config(tmp_path, _fake_mcp_server(tmp_path))

        result = _run(CallMcpTool(config_path=config).execute(server="fake", tool="echo", arguments={"text": "hi"}))

        assert "echo: hi" in result.content
