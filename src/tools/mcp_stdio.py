"""Minimal stdio MCP bridge for opt-in local tool servers."""

import asyncio
import json
from pathlib import Path
from typing import Any

from tools.approval import GLOBAL_APPROVAL_MANAGER, ApprovalManager, approval_required_message
from tools.base import BaseTool, ToolResult

_DEFAULT_TIMEOUT = 20
_OUTPUT_LIMIT = 4000


def _clip(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated to {limit} chars]"


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"servers": {}}
    data = json.loads(path.read_text())
    if "mcpServers" in data and "servers" not in data:
        data["servers"] = data["mcpServers"]
    return data


class _McpSession:
    def __init__(self, server: dict[str, Any], timeout: int = _DEFAULT_TIMEOUT):
        self.server = server
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self):
        command = self.server.get("command")
        args = self.server.get("args", [])
        if not command:
            raise ValueError("MCP server command is missing.")
        env = None
        if self.server.get("env"):
            import os

            env = {**os.environ, **{str(k): str(v) for k, v in self.server["env"].items()}}
        self.proc = await asyncio.create_subprocess_exec(
            str(command),
            *[str(arg) for arg in args],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "eyra", "version": "1"},
            },
        )
        await self.notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *_):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        await self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        while True:
            response = await asyncio.wait_for(self._read(), timeout=self.timeout)
            if response.get("id") != msg_id:
                continue
            if "error" in response:
                raise RuntimeError(json.dumps(response["error"]))
            return response.get("result")

    async def _write(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP process is not running.")
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
        await self.proc.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("MCP process is not running.")
        headers: dict[str, str] = {}
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                stderr = ""
                if self.proc.stderr:
                    try:
                        stderr = (await asyncio.wait_for(self.proc.stderr.read(), timeout=0.1)).decode(errors="replace")
                    except asyncio.TimeoutError:
                        pass
                raise RuntimeError(f"MCP server exited before responding. {stderr}".strip())
            if line in (b"\r\n", b"\n"):
                break
            key, value = line.decode().split(":", 1)
            headers[key.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = await self.proc.stdout.readexactly(length)
        return json.loads(body)


class ListMcpTools(BaseTool):
    name = "list_mcp_tools"
    description = "List tools exposed by an opt-in stdio MCP server from the Eyra MCP config."
    parameters = {
        "type": "object",
        "properties": {"server": {"type": "string", "description": "Configured MCP server name."}},
        "required": ["server"],
    }
    costly = True

    def __init__(self, config_path: str | Path, timeout: int | float = _DEFAULT_TIMEOUT):
        self.config_path = Path(config_path).expanduser()
        self.timeout = timeout

    async def execute(self, server: str = "", **_) -> ToolResult:
        try:
            server_config = self._server_config(server)
            async with _McpSession(server_config, timeout=self.timeout) as session:
                result = await session.request("tools/list")
        except asyncio.TimeoutError:
            return ToolResult(content="MCP error: server timed out.")
        except Exception as e:
            return ToolResult(content=f"MCP error: {e}")
        return ToolResult(content=_clip(json.dumps({"server": server, "tools": result.get("tools", [])}, indent=2, sort_keys=True)))

    def _server_config(self, server: str) -> dict[str, Any]:
        config = _load_config(self.config_path)
        servers = config.get("servers", {})
        if server not in servers:
            raise ValueError(f"MCP server is not configured: {server}")
        return servers[server]


class CallMcpTool(ListMcpTools):
    name = "call_mcp_tool"
    description = "Call a tool on an opt-in stdio MCP server."
    parameters = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve MCP calls."},
        },
        "required": ["server", "tool"],
    }
    costly = True

    def __init__(
        self,
        config_path: str | Path,
        timeout: int | float = _DEFAULT_TIMEOUT,
        approval_manager: ApprovalManager | None = None,
    ):
        super().__init__(config_path=config_path, timeout=timeout)
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        server: str = "",
        tool: str = "",
        arguments: dict[str, Any] | None = None,
        approval_id: str = "",
        confirmed: bool = False,
        **_,
    ) -> ToolResult:
        details = {"server": server, "tool": tool, "arguments": arguments or {}}
        if not approval_id or not self._approvals.consume(approval_id, self.name, "MCP tool call", details):
            approval = self._approvals.request(self.name, "MCP tool call", details)
            return ToolResult(content=approval_required_message(approval))
        try:
            server_config = self._server_config(server)
            async with _McpSession(server_config, timeout=self.timeout) as session:
                result = await session.request("tools/call", {"name": tool, "arguments": arguments or {}})
        except asyncio.TimeoutError:
            return ToolResult(content="MCP error: server timed out.")
        except Exception as e:
            return ToolResult(content=f"MCP error: {e}")
        text_parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        content = "\n".join(text_parts) if text_parts else json.dumps(result, indent=2, sort_keys=True)
        return ToolResult(content=_clip(content))
