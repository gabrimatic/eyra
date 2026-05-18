"""Thin client for the local mcp-prose-memory stdio server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from utils.settings import Settings


class MemoryMcpClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def server_config(self) -> dict[str, Any]:
        command = self.settings.MEMORY_MCP_COMMAND.strip() or "mcp-prose-memory"
        args = shlex.split(self.settings.MEMORY_MCP_ARGS)
        resolved = _resolve_command(command, args)
        env = {"MEMORY_PATH": str(Path(self.settings.MEMORY_PATH).expanduser())}
        return {"command": resolved[0], "args": resolved[1], "env": env}

    def availability(self) -> dict[str, Any]:
        try:
            command, args = _resolve_command(
                self.settings.MEMORY_MCP_COMMAND.strip() or "mcp-prose-memory",
                shlex.split(self.settings.MEMORY_MCP_ARGS),
            )
        except Exception as exc:
            return {"available": False, "command": self.settings.MEMORY_MCP_COMMAND, "args": [], "error": str(exc)}
        return {"available": True, "command": command, "args": args, "error": ""}

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        async with _LineMcpSession(self.server_config(), timeout=max(5, int(self.settings.TOOL_TIMEOUT_SECONDS))) as session:
            result = await session.request("tools/call", {"name": name, "arguments": arguments or {}})
        return _result_text(result)


class _LineMcpSession:
    """MCP stdio client for the current line-delimited SDK transport."""

    def __init__(self, server: dict[str, Any], timeout: int = 20):
        self.server = server
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self):
        command = self.server.get("command")
        args = self.server.get("args", [])
        if not command:
            raise ValueError("Memory MCP command is missing.")
        env = None
        if self.server.get("env"):
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
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "eyra", "version": "1"},
            },
        )
        await self.notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *_):
        await self._close_process()

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
            raise RuntimeError("Memory MCP process is not running.")
        self.proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        await self.proc.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("Memory MCP process is not running.")
        line = await self.proc.stdout.readline()
        if not line:
            stderr = ""
            if self.proc.stderr:
                with contextlib.suppress(asyncio.TimeoutError):
                    stderr = (await asyncio.wait_for(self.proc.stderr.read(), timeout=0.1)).decode(errors="replace")
            raise RuntimeError(f"Memory MCP server exited before responding. {stderr}".strip())
        return json.loads(line.decode())

    async def _close_process(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        self.proc = None


def _resolve_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    expanded = Path(command).expanduser()
    if "/" in command:
        if expanded.exists():
            return str(expanded), args
        raise FileNotFoundError(f"Memory MCP command not found: {command}")
    found = shutil.which(command)
    if found:
        return found, args
    if command == "mcp-prose-memory":
        local_server = _local_source_server()
        node = shutil.which("node")
        if local_server and node:
            return node, [str(local_server), *args]
    raise FileNotFoundError("mcp-prose-memory is not installed. Install it with `npm install -g mcp-prose-memory`.")


def _local_source_server() -> Path | None:
    candidates: list[Path] = []
    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        candidates.append(base / "mcp-prose-memory" / "dist" / "index.js")
        candidates.append(base.parent / "mcp-prose-memory" / "dist" / "index.js")
    here = Path(__file__).resolve()
    for base in here.parents:
        candidates.append(base / "mcp-prose-memory" / "dist" / "index.js")
        candidates.append(base.parent / "mcp-prose-memory" / "dist" / "index.js")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _result_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part).strip()


def parse_json_text(text: str) -> dict[str, Any]:
    return json.loads(text)
