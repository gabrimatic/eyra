"""Local operator tools for OS inspection, bounded commands, and agent bridges."""

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from chat.capture import capture_screenshot_in_memory
from runtime.capabilities import build_capability_snapshot
from runtime.external_agents import AgentAdapterRegistry, AgentJobSpec
from tools.approval import GLOBAL_APPROVAL_MANAGER, ApprovalManager, approval_required_message
from tools.base import BaseTool, ToolResult
from tools.filesystem import _resolve
from utils.settings import Settings

_MAX_OUTPUT = 12_000
_MAX_TIMEOUT = 120
_MAX_SESSION_BYTES = 64_000
_SHELL_METACHARS = re.compile(r"[|&;<>()$`\\\n]")
_DANGEROUS_TOKENS = {
    "rm",
    "rmdir",
    "mv",
    "dd",
    "mkfs",
    "chmod",
    "chown",
    "sudo",
    "su",
    "kill",
    "killall",
    "pkill",
    "launchctl",
    "shutdown",
    "reboot",
}


def _clip(text: str, limit: int = _MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated to {limit} chars]"


def _as_default_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _json(data: object) -> ToolResult:
    return ToolResult(content=json.dumps(data, indent=2, sort_keys=True))


def _redact(text: str) -> str:
    patterns = [
        r"sk-[A-Za-z0-9_\-]{20,}",
        r"(api[_-]?key|token|secret|password)([\"'\s:=]+)[A-Za-z0-9_\-./+=]{8,}",
        r"Bearer\s+[A-Za-z0-9_\-./+=]{16,}",
    ]
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, lambda m: m.group(1) + m.group(2) + "[redacted]" if m.lastindex else "[redacted]", redacted, flags=re.I)
    return redacted


def _redact_path_for_approval(path: Path) -> str:
    return path.name or "working-directory"


def _agent_approval_details(agent: str, task: str, workdir: Path) -> dict[str, object]:
    return {
        "agent": agent,
        "taskLength": len(task),
        "taskFingerprint": hashlib.sha256(task.encode()).hexdigest(),
        "cwdLabel": _redact_path_for_approval(workdir),
        "cwdFingerprint": hashlib.sha256(str(workdir).encode()).hexdigest(),
    }


def _approval_or_result(
    manager: ApprovalManager,
    *,
    tool_name: str,
    title: str,
    details: dict[str, object],
    approval_id: str = "",
) -> ToolResult | None:
    if approval_id and manager.consume(approval_id, tool_name, title, details):
        return None
    approval = manager.request(tool_name, title, details)
    return ToolResult(content=approval_required_message(approval))


class DiscoverCapabilitiesTool(BaseTool):
    name = "discover_capabilities"
    description = (
        "Return Eyra's active local, voice, web, MCP, agent, and network capability switches. "
        "Call this before broad OS-control or tool-discovery work."
    )
    parameters = {"type": "object", "properties": {}}
    costly = False

    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, **_) -> ToolResult:
        return _json(
            {
                "offlineByDefault": True,
                "capabilities": build_capability_snapshot(self.settings),
                "tools": {
                    "filesystem": True,
                    "screen": True,
                    "network": self.settings.NETWORK_TOOLS_ENABLED,
                    "os": self.settings.OS_TOOLS_ENABLED,
                    "agents": self.settings.AGENT_TOOLS_ENABLED or self.settings.EXTERNAL_AGENT_TOOLS_ENABLED,
                    "connectors": self.settings.CONNECTORS_ENABLED,
                    "mcp": self.settings.MCP_TOOLS_ENABLED,
                },
                "voice": {
                    "localWhisper": self.settings.LIVE_LISTENING_ENABLED or self.settings.LIVE_SPEECH_ENABLED,
                    "realtime": self.settings.REALTIME_VOICE_ENABLED,
                    "realtimeModel": self.settings.REALTIME_MODEL,
                },
                "web": {
                    "enabled": self.settings.WEB_UI_ENABLED,
                    "host": self.settings.WEB_UI_HOST,
                    "port": self.settings.WEB_UI_PORT,
                },
                "agents": {
                    "codex": bool(shutil.which("codex")),
                    "openclaw": bool(shutil.which("openclaw") or (Path.home() / ".openclaw").exists()),
                },
            }
        )


class GetVoiceContextTool(BaseTool):
    name = "get_voice_context"
    description = "Return Eyra runtime context for voice, web, model, and optional tool modes."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, **_) -> ToolResult:
        return _json(
            {
                "assistant": "Eyra",
                "offlineByDefault": True,
                "model": self.settings.MODEL,
                "voice": {
                    "localWhisper": self.settings.LIVE_LISTENING_ENABLED or self.settings.LIVE_SPEECH_ENABLED,
                    "realtime": self.settings.REALTIME_VOICE_ENABLED,
                    "realtimeModel": self.settings.REALTIME_MODEL,
                },
                "web": {
                    "enabled": self.settings.WEB_UI_ENABLED,
                    "host": self.settings.WEB_UI_HOST,
                    "port": self.settings.WEB_UI_PORT,
                },
                "tools": {
                    "network": self.settings.NETWORK_TOOLS_ENABLED,
                    "os": self.settings.OS_TOOLS_ENABLED,
                    "agents": self.settings.AGENT_TOOLS_ENABLED or self.settings.EXTERNAL_AGENT_TOOLS_ENABLED,
                    "connectors": self.settings.CONNECTORS_ENABLED,
                    "mcp": self.settings.MCP_TOOLS_ENABLED,
                },
            }
        )


class RunCommandTool(BaseTool):
    name = "run_command"
    description = (
        "Run a bounded local command inside an allowed filesystem root. Prefer argv. "
        "Command strings are parsed safely and risky commands require server-side user approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"}, "description": "Command argv, e.g. ['pwd']."},
            "command": {"type": "string", "description": "Command string. Shell syntax is not supported. Requires approval."},
            "cwd": {"type": "string", "description": "Working directory under an allowed root."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": _MAX_TIMEOUT},
            "approval_id": {"type": "string", "description": "Server-issued approval id for this exact action."},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve risky actions."},
        },
    }
    costly = True

    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        default_path: Path,
        approval_manager: ApprovalManager | None = None,
    ):
        self._roots = tuple(_as_default_path(root) for root in allowed_roots)
        self._default_path = _as_default_path(default_path)
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        argv: list[str] | None = None,
        command: str = "",
        cwd: str = "",
        timeout_seconds: int = 30,
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        try:
            workdir = _resolve(cwd or str(self._default_path), self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))
        if not workdir.is_dir():
            return ToolResult(content=f"Not a directory: {workdir}")

        timeout = max(1, min(int(timeout_seconds or 30), _MAX_TIMEOUT))
        if command:
            approval = _approval_or_result(
                self._approvals,
                tool_name=self.name,
                title="command string",
                details={"command": command, "cwd": str(workdir), "timeout_seconds": timeout},
                approval_id=approval_id,
            )
            if approval is not None:
                return approval
            return await asyncio.to_thread(self._run_command_string, command, workdir, timeout)

        if not argv:
            return ToolResult(content="Missing argv or command.")
        argv = [str(part) for part in argv]
        if self._requires_confirmation(argv):
            approval = _approval_or_result(
                self._approvals,
                tool_name=self.name,
                title="risky command",
                details={"argv": argv, "cwd": str(workdir), "timeout_seconds": timeout},
                approval_id=approval_id,
            )
            if approval is not None:
                return approval
        return await asyncio.to_thread(self._run_argv, argv, workdir, timeout)

    def _requires_confirmation(self, argv: list[str]) -> bool:
        if not argv:
            return True
        head = Path(argv[0]).name.lower()
        if head in _DANGEROUS_TOKENS:
            return True
        joined = " ".join(argv).lower()
        return " -rf" in joined or "--force" in joined or ">" in joined or "|" in joined

    def _run_argv(self, argv: list[str], cwd: Path, timeout: int) -> ToolResult:
        try:
            completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError:
            return ToolResult(content=f"Command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(content=f"Command timed out after {timeout}s: {shlex.join(argv)}")
        return self._format_result(completed.returncode, shlex.join(argv), completed.stdout, completed.stderr)

    def _run_command_string(self, command: str, cwd: Path, timeout: int) -> ToolResult:
        try:
            argv = self._parse_command_string(command)
        except ValueError as exc:
            return ToolResult(content=str(exc))
        return self._run_argv(argv, cwd, timeout)

    def _parse_command_string(self, command: str) -> list[str]:
        if _SHELL_METACHARS.search(command):
            raise ValueError("Shell syntax is not supported. Use argv for exact command arguments.")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Invalid command string: {exc}. Use argv for exact command arguments.") from exc
        if not argv:
            raise ValueError("Missing command. Use argv for exact command arguments.")
        if self._requires_confirmation(argv):
            raise ValueError("Approved command string still looks risky. Use argv and request approval for the exact command.")
        return argv

    def _format_result(self, code: int, command: str, stdout: str, stderr: str) -> ToolResult:
        parts = [f"command={command}", f"exit_code={code}"]
        if stdout:
            parts.append("stdout:\n" + _clip(stdout.rstrip()))
        if stderr:
            parts.append("stderr:\n" + _clip(stderr.rstrip()))
        return ToolResult(content="\n\n".join(parts))


class FileInfoTool(BaseTool):
    name = "get_file_info"
    description = "Return file or directory metadata without reading file content."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File or directory path."}},
        "required": ["path"],
    }

    def __init__(self, allowed_roots: tuple[Path, ...], default_path: Path):
        self._roots = tuple(_as_default_path(root) for root in allowed_roots)
        self._default_path = _as_default_path(default_path)

    async def execute(self, path: str = "", **_) -> ToolResult:
        try:
            p = _resolve(path, self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))
        try:
            stat = p.stat()
        except FileNotFoundError:
            return ToolResult(content=f"Not found: {p}")
        return _json(
            {
                "path": str(p),
                "name": p.name,
                "type": "directory" if p.is_dir() else "file" if p.is_file() else "other",
                "sizeBytes": stat.st_size,
                "modified": stat.st_mtime,
                "readable": os.access(p, os.R_OK),
                "writable": os.access(p, os.W_OK),
            }
        )


class SearchFilesTool(BaseTool):
    name = "search_files"
    description = "Search text files under an allowed root. Uses rg when available, with a Python fallback."
    parameters = {
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "Directory to search."},
            "query": {"type": "string", "description": "Literal text to search for."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["query"],
    }

    def __init__(self, allowed_roots: tuple[Path, ...], default_path: Path):
        self._roots = tuple(_as_default_path(root) for root in allowed_roots)
        self._default_path = _as_default_path(default_path)

    async def execute(self, root: str = "", query: str = "", limit: int = 50, **_) -> ToolResult:
        if not query:
            return ToolResult(content="Missing query.")
        try:
            search_root = _resolve(root or str(self._default_path), self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))
        if not search_root.is_dir():
            return ToolResult(content=f"Not a directory: {search_root}")
        limit = max(1, min(int(limit or 50), 200))
        if shutil.which("rg"):
            return await asyncio.to_thread(self._rg, search_root, query, limit)
        return await asyncio.to_thread(self._python_search, search_root, query, limit)

    def _rg(self, root: Path, query: str, limit: int) -> ToolResult:
        completed = subprocess.run(
            ["rg", "--fixed-strings", "--line-number", "--max-count", str(limit), query, str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode not in (0, 1):
            return ToolResult(content=f"Search failed: {completed.stderr.strip()}")
        return ToolResult(content=_clip(completed.stdout.strip() or "No matches."))

    def _python_search(self, root: Path, query: str, limit: int) -> ToolResult:
        matches: list[str] = []
        for path in root.rglob("*"):
            if len(matches) >= limit:
                break
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), 1):
                if query in line:
                    matches.append(f"{path}:{idx}: {line}")
                    if len(matches) >= limit:
                        break
        return ToolResult(content="\n".join(matches) if matches else "No matches.")


class ExtractScreenTextTool(BaseTool):
    name = "extract_screen_text"
    description = (
        "Extract visible text from the current screen using a configured local OCR command. "
        "Use this when exact screen text is needed and no network OCR should be used."
    )
    parameters = {"type": "object", "properties": {}}
    costly = True

    def __init__(self, ocr_command: str = ""):
        self.ocr_command = ocr_command.strip()

    async def execute(self, **_) -> ToolResult:
        if not self.ocr_command:
            return ToolResult(
                content=(
                    "SCREEN_OCR_COMMAND is not set. Configure a local OCR command that reads PNG bytes "
                    "from stdin to enable local OCR screen text extraction."
                )
            )

        try:
            image = await capture_screenshot_in_memory()
        except Exception:
            return ToolResult(
                content=(
                    "Screen recording permission may be required before local OCR can read the screen. "
                    "Allow Eyra or your terminal in System Settings > Privacy & Security > Screen Recording, then try again."
                )
            )

        png = BytesIO()
        image.save(png, format="PNG")
        return await asyncio.to_thread(self._run_ocr, png.getvalue())

    def _run_ocr(self, png_bytes: bytes) -> ToolResult:
        argv = shlex.split(self.ocr_command)
        if not argv:
            return ToolResult(content="SCREEN_OCR_COMMAND is empty. Configure a local OCR command.")
        try:
            completed = subprocess.run(
                argv,
                input=png_bytes,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except FileNotFoundError:
            return ToolResult(content=f"Local OCR command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(content="Local OCR timed out after 20s. Try a faster OCR command or a smaller screen region.")

        stdout = completed.stdout.decode("utf-8", errors="replace") if isinstance(completed.stdout, bytes) else completed.stdout
        stderr = completed.stderr.decode("utf-8", errors="replace") if isinstance(completed.stderr, bytes) else completed.stderr
        if completed.returncode != 0:
            detail = _clip((stderr or stdout or "No error details.").strip(), 1000)
            return ToolResult(content=f"Local OCR failed with exit code {completed.returncode}: {detail}")
        return _json(
            {
                "ocr": "local",
                "offlineByDefault": True,
                "source": "screen",
                "text": stdout.strip(),
            }
        )


class ListProcessesTool(BaseTool):
    name = "list_processes"
    description = "List local processes by CPU usage for OS-status questions."
    parameters = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
    }

    async def execute(self, limit: int = 15, **_) -> ToolResult:
        limit = max(1, min(int(limit or 15), 50))
        completed = await asyncio.to_thread(
            subprocess.run,
            ["ps", "-axo", "pid,ppid,%cpu,%mem,comm", "-r"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not list processes: {completed.stderr.strip()}")
        lines = completed.stdout.splitlines()
        return ToolResult(content="\n".join(lines[: limit + 1]))


class GetAccessibilityTreeTool(BaseTool):
    name = "get_accessibility_tree"
    description = (
        "Read a bounded accessibility snapshot for the frontmost macOS app using local System Events. "
        "Use this to ground UI actions before clicking or typing. Requires macOS Accessibility/Automation permission."
    )
    parameters = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
    }
    costly = True

    async def execute(self, limit: int = 50, **_) -> ToolResult:
        limit = max(1, min(int(limit or 50), 100))
        script = f"""
set maxItems to {limit}
tell application "System Events"
  set frontProc to first application process whose frontmost is true
  set appName to name of frontProc
  set windowName to ""
  try
    set windowName to name of front window of frontProc
  end try
  set output to "app=" & appName & linefeed & "window=" & windowName
  set itemCount to 0
  try
    repeat with itemRef in entire contents of front window of frontProc
      if itemCount is greater than or equal to maxItems then exit repeat
      set itemRole to ""
      set itemTitle to ""
      set enabledText to ""
      try
        set itemRole to role of itemRef
      end try
      try
        set itemTitle to name of itemRef
      end try
      try
        set enabledText to "enabled=" & ((enabled of itemRef) as text)
      end try
      if itemRole is not "" then
        set output to output & linefeed & itemRole & "|" & itemTitle & "|" & enabledText
        set itemCount to itemCount + 1
      end if
    end repeat
  end try
  return output
end tell
"""
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return self._error_result(completed.stderr)
        return _json(self._parse_output(completed.stdout, limit))

    def _error_result(self, stderr: str) -> ToolResult:
        lowered = stderr.lower()
        if "not authorized" in lowered or "not allowed" in lowered or "accessibility" in lowered:
            return ToolResult(
                content=(
                    "Accessibility permission is required to inspect the frontmost app. "
                    "Enable it for the terminal running Eyra in System Settings > Privacy & Security > Accessibility."
                )
            )
        return ToolResult(content=f"Could not read accessibility tree: {_clip(stderr.strip() or 'unknown error', 800)}")

    def _parse_output(self, stdout: str, limit: int) -> dict:
        app = ""
        window = ""
        elements: list[dict[str, object]] = []
        for line in stdout.splitlines():
            if line.startswith("app="):
                app = line.split("=", 1)[1]
            elif line.startswith("window="):
                window = line.split("=", 1)[1]
            elif "|" in line and len(elements) < limit:
                role, title, enabled = (line.split("|", 2) + ["", "", ""])[:3]
                elements.append(
                    {
                        "role": role,
                        "title": title,
                        "enabled": enabled.split("=", 1)[1].lower() == "true" if enabled.startswith("enabled=") else None,
                    }
                )
        return {
            "frontmostApp": app,
            "window": window,
            "elementCount": len(elements),
            "elements": elements,
        }


class GetLaunchAgentStatusTool(BaseTool):
    name = "get_launch_agent_status"
    description = "Inspect macOS LaunchAgent status by label substring. Local read-only OS tool."
    parameters = {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "LaunchAgent label or substring."}},
    }

    async def execute(self, label: str = "", **_) -> ToolResult:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not inspect LaunchAgents: {completed.stderr.strip()}")
        needle = label.strip().lower()
        matches = []
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            entry_label = parts[2]
            if needle and needle not in entry_label.lower():
                continue
            matches.append({"pid": parts[0], "status": parts[1], "label": entry_label})
        return _json({"query": label, "matches": matches[:50]})


class ManageLaunchAgentTool(BaseTool):
    name = "manage_launch_agent"
    description = "Start, stop, or restart a macOS LaunchAgent by label. Requires server-side user approval."
    parameters = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "stop", "restart"]},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["label", "action"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        label: str = "",
        action: str = "",
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        if not label.strip() or action not in {"start", "stop", "restart"}:
            return ToolResult(content="Provide label and action=start|stop|restart.")
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="LaunchAgent change",
            details={"label": label, "action": action},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        actions = ["stop", "start"] if action == "restart" else [action]
        outputs = []
        for item in actions:
            completed = await asyncio.to_thread(
                subprocess.run,
                ["launchctl", item, label],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            outputs.append(f"{item}: exit_code={completed.returncode} {completed.stderr.strip()}")
        return ToolResult(content="\n".join(outputs))


class GetSystemSnapshotTool(BaseTool):
    name = "get_system_snapshot"
    description = "Return a compact local system snapshot with system info and top processes."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_) -> ToolResult:
        processes = await ListProcessesTool().execute(limit=10)
        return _json({"processes": processes.content})


class FetchUrlTool(BaseTool):
    name = "fetch_url"
    description = "Fetch a plain HTTP/HTTPS URL without launching the browser. Network tool, opt-in only."
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "minimum": 100, "maximum": 12000}},
        "required": ["url"],
    }
    costly = True

    async def execute(self, url: str = "", max_chars: int = 4000, **_) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult(content="Only http/https URLs are allowed.")
        limit = max(100, min(int(max_chars or 4000), 12000))
        return await asyncio.to_thread(self._fetch, url, limit)

    def _fetch(self, url: str, limit: int) -> ToolResult:
        try:
            request = Request(url, headers={"User-Agent": "Eyra/1"})
            with urlopen(request, timeout=15) as response:
                raw = response.read(limit + 1)
        except Exception as e:
            return ToolResult(content=f"Could not fetch URL: {e}")
        text = raw[:limit].decode("utf-8", errors="replace")
        if len(raw) > limit:
            text += f"\n...[truncated to {limit} chars]"
        return ToolResult(content=text)


class ListOpenAppsTool(BaseTool):
    name = "list_open_apps"
    description = "List visible macOS applications. Local-only and read-only."
    parameters = {"type": "object", "properties": {}}
    costly = False

    async def execute(self, **_) -> ToolResult:
        script = """
tell application "System Events"
  set output to ""
  repeat with proc in application processes whose visible is true
    set output to output & name of proc & linefeed
  end repeat
  return output
end tell
"""
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not list open apps: {completed.stderr.strip() or 'macOS permission denied.'}")
        apps = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return _json({"apps": apps})


class ListWindowsTool(BaseTool):
    name = "list_windows"
    description = "List window titles for a visible macOS application. Local-only and read-only."
    parameters = {
        "type": "object",
        "properties": {"app": {"type": "string", "description": "Application name. Defaults to the frontmost app."}},
    }
    costly = False

    async def execute(self, app: str = "", **_) -> ToolResult:
        if not app.strip():
            app = await self._frontmost_app()
            if not app:
                return ToolResult(content="Could not determine the frontmost app.")
        script = f"""
tell application "System Events"
  if not (exists application process {json.dumps(app)}) then return ""
  tell application process {json.dumps(app)}
    set output to ""
    repeat with win in windows
      set output to output & name of win & linefeed
    end repeat
    return output
  end tell
end tell
"""
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not list windows for {app}: {completed.stderr.strip()}")
        windows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return _json({"app": app, "windows": windows})

    async def _frontmost_app(self) -> str:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""


class WindowActionTool(BaseTool):
    name = "window_action"
    description = (
        "Close, minimize, zoom, fullscreen, move, or resize a macOS application window through System Events. "
        "Requires server-side approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["close", "minimize", "zoom", "fullscreen", "move", "resize"],
                "description": "Window action to perform.",
            },
            "app": {"type": "string", "description": "Application name. Defaults to the frontmost app."},
            "window": {"type": "string", "description": "Window title. Defaults to the front window."},
            "x": {"type": "integer", "description": "New window x position for move."},
            "y": {"type": "integer", "description": "New window y position for move."},
            "width": {"type": "integer", "description": "New window width for resize."},
            "height": {"type": "integer", "description": "New window height for resize."},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["action"],
    }
    costly = True
    _ACTIONS = {"close", "minimize", "zoom", "fullscreen", "move", "resize"}

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        action: str = "",
        app: str = "",
        window: str = "",
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        action = action.strip().lower()
        app = app.strip()
        window = window.strip()
        if action not in self._ACTIONS:
            return ToolResult(content=f"Unsupported window action: {action or '(empty)'}")
        if action == "move" and (x < 0 or y < 0):
            return ToolResult(content="Window move needs non-negative x and y coordinates.")
        if action == "resize" and (width <= 0 or height <= 0):
            return ToolResult(content="Window resize needs positive width and height.")
        details = {"action": action, "app": app or "frontmost", "window": window or "front", "x": x, "y": y}
        if action == "resize":
            details.update({"width": width, "height": height})
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="window action",
            details=details,
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        target_app = app or await self._frontmost_app()
        if not target_app:
            return ToolResult(content="Could not determine the frontmost app.")
        script = self._script(action, target_app, window, x, y, width, height)
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not {action} window: {completed.stderr.strip()}")
        return ToolResult(content=f"Window {action} applied: {target_app} / {window or 'front window'}")

    async def _frontmost_app(self) -> str:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _script(self, action: str, app: str, window: str, x: int, y: int, width: int, height: int) -> str:
        win_ref = f'window {json.dumps(window)}' if window else "front window"
        if action == "close":
            command = "perform action \"AXClose\""
        elif action == "minimize":
            command = 'set value of attribute "AXMinimized" to true'
        elif action == "zoom":
            command = 'set value of attribute "AXZoomButton" to true'
        elif action == "fullscreen":
            command = "perform action \"AXRaise\"\n    keystroke \"f\" using {control down, command down}"
        elif action == "move":
            command = f"set position to {{{int(x)}, {int(y)}}}"
        else:
            command = f"set size to {{{int(width)}, {int(height)}}}"
        return f"""
tell application "System Events"
  tell application process {json.dumps(app)}
    tell {win_ref}
      {command}
    end tell
  end tell
end tell
"""


class ActivateAppTool(BaseTool):
    name = "activate_app"
    description = "Bring a running macOS application to the foreground. Local-only."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Application name, e.g. Finder."}},
        "required": ["name"],
    }
    costly = True

    async def execute(self, name: str = "", **_) -> ToolResult:
        if not name.strip():
            return ToolResult(content="Missing application name.")
        script = f'tell application "System Events" to set frontmost of application process {json.dumps(name)} to true'
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not activate {name}: {completed.stderr.strip()}")
        return ToolResult(content=f"Activated app: {name}")


class OpenAppTool(BaseTool):
    name = "open_app"
    description = "Open a macOS application by name. Requires server-side user approval."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Application name, e.g. Calculator."},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["name"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, name: str = "", confirmed: bool = False, approval_id: str = "", **_) -> ToolResult:
        if not name.strip():
            return ToolResult(content="Missing application name.")
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="open application",
            details={"name": name},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        completed = await asyncio.to_thread(
            subprocess.run,
            ["open", "-a", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not open {name}: {completed.stderr.strip()}")
        return ToolResult(content=f"Opened app: {name}")


class QuitAppTool(BaseTool):
    name = "quit_app"
    description = "Quit a macOS application. Requires server-side user approval."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Application name, e.g. Preview."},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["name"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, name: str = "", confirmed: bool = False, approval_id: str = "", **_) -> ToolResult:
        if not name.strip():
            return ToolResult(content="Missing application name.")
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="quit application",
            details={"name": name},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        script = f'tell application {json.dumps(name)} to quit'
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not quit {name}: {completed.stderr.strip()}")
        return ToolResult(content=f"Quit app: {name}")


class UiClickTool(BaseTool):
    name = "ui_click"
    description = (
        "Click a screen coordinate using local macOS automation. Requires user approval because the target app "
        "may perform actions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "minimum": 0},
            "y": {"type": "integer", "minimum": 0},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["x", "y"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, x: int = 0, y: int = 0, confirmed: bool = False, approval_id: str = "", **_) -> ToolResult:
        x = max(0, int(x))
        y = max(0, int(y))
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="UI click",
            details={"x": x, "y": y},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        cliclick = shutil.which("cliclick")
        if not cliclick:
            return ToolResult(content="UI click needs cliclick. Install with: brew install cliclick")
        completed = await asyncio.to_thread(
            subprocess.run,
            [cliclick, f"c:{x},{y}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not click {x},{y}: {completed.stderr.strip()}")
        return ToolResult(content=f"Clicked: {x},{y}")


class UiScrollTool(BaseTool):
    name = "ui_scroll"
    description = "Scroll the current UI using local macOS automation. Requires user approval."
    parameters = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 20},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["direction"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        direction: str = "down",
        amount: int = 3,
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        direction = direction.lower().strip()
        if direction not in {"up", "down", "left", "right"}:
            return ToolResult(content="direction must be one of: up, down, left, right.")
        amount = max(1, min(int(amount or 3), 20))
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="UI scroll",
            details={"direction": direction, "amount": amount},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        cliclick = shutil.which("cliclick")
        if not cliclick:
            return ToolResult(content="UI scroll needs cliclick. Install with: brew install cliclick")
        dx, dy = {
            "up": (0, amount),
            "down": (0, -amount),
            "left": (amount, 0),
            "right": (-amount, 0),
        }[direction]
        completed = await asyncio.to_thread(
            subprocess.run,
            [cliclick, f"w:{dx},{dy}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not scroll {direction}: {completed.stderr.strip()}")
        return ToolResult(content=f"Scrolled {direction} by {amount}.")


class UiDragTool(BaseTool):
    name = "ui_drag"
    description = "Drag from one screen coordinate to another using local macOS automation. Requires user approval."
    parameters = {
        "type": "object",
        "properties": {
            "start_x": {"type": "integer", "minimum": 0},
            "start_y": {"type": "integer", "minimum": 0},
            "end_x": {"type": "integer", "minimum": 0},
            "end_y": {"type": "integer", "minimum": 0},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["start_x", "start_y", "end_x", "end_y"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        start_x: int = 0,
        start_y: int = 0,
        end_x: int = 0,
        end_y: int = 0,
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        start_x = max(0, int(start_x))
        start_y = max(0, int(start_y))
        end_x = max(0, int(end_x))
        end_y = max(0, int(end_y))
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="UI drag",
            details={"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        cliclick = shutil.which("cliclick")
        if not cliclick:
            return ToolResult(content="UI drag needs cliclick. Install with: brew install cliclick")
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                cliclick,
                f"m:{start_x},{start_y}",
                f"dd:{start_x},{start_y}",
                f"m:{end_x},{end_y}",
                f"du:{end_x},{end_y}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not drag: {completed.stderr.strip()}")
        return ToolResult(content=f"Dragged: {start_x},{start_y} -> {end_x},{end_y}")


class UiTypeTextTool(BaseTool):
    name = "ui_type_text"
    description = "Type text into the focused UI element using local macOS automation. Requires user approval."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["text"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, text: str = "", confirmed: bool = False, approval_id: str = "", **_) -> ToolResult:
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="UI text entry",
            details={"text_length": len(text), "text_preview": text[:80]},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        script = 'tell application "System Events" to keystroke ' + json.dumps(text)
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not type text: {completed.stderr.strip()}")
        return ToolResult(content=f"Typed text ({len(text)} characters).")


class PressHotkeyTool(BaseTool):
    name = "press_hotkey"
    description = "Press a keyboard shortcut using local macOS automation. Requires user approval."
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Single key such as s, return, escape, or tab."},
            "modifiers": {
                "type": "array",
                "items": {"type": "string", "enum": ["command", "option", "control", "shift"]},
            },
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["key"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        key: str = "",
        modifiers: list[str] | None = None,
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        key = str(key).strip().lower()
        modifiers = [str(mod).strip().lower() for mod in (modifiers or []) if str(mod).strip()]
        if not key:
            return ToolResult(content="Missing hotkey key.")
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="hotkey",
            details={"key": key, "modifiers": modifiers},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        using = ""
        if modifiers:
            using = " using {" + ", ".join(f"{mod} down" for mod in modifiers) + "}"
        script = f'tell application "System Events" to keystroke {json.dumps(key)}{using}'
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not press hotkey: {completed.stderr.strip()}")
        combo = "+".join([*modifiers, key])
        return ToolResult(content=f"Pressed hotkey: {combo}")


class ShowNotificationTool(BaseTool):
    name = "show_notification"
    description = "Show a local macOS notification for the user."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["message"],
    }

    async def execute(self, message: str = "", title: str = "Eyra", **_) -> ToolResult:
        if not message.strip():
            return ToolResult(content="Missing notification message.")
        script = f'display notification {json.dumps(message)} with title {json.dumps(title or "Eyra")}'
        completed = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not show notification: {completed.stderr.strip()}")
        return ToolResult(content="Notification shown.")


class RunShortcutTool(BaseTool):
    name = "run_shortcut"
    description = "Run a local macOS Shortcut by name. Requires server-side approval."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Shortcut name."},
            "input_text": {"type": "string", "description": "Optional text piped to the shortcut through stdin."},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["name"],
    }
    costly = True

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        name: str = "",
        input_text: str = "",
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        name = name.strip()
        if not name:
            return ToolResult(content="Missing shortcut name.")
        shortcuts = shutil.which("shortcuts")
        if not shortcuts:
            return ToolResult(content="macOS shortcuts command is not available on this system.")
        details = {"name": name, "input_length": len(input_text)}
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="run shortcut",
            details=details,
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        argv = [shortcuts, "run", name]
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 30,
            "check": False,
        }
        if input_text:
            argv.extend(["--input-path", "-"])
            run_kwargs["input"] = input_text
        completed = await asyncio.to_thread(subprocess.run, argv, **run_kwargs)
        if completed.returncode != 0:
            return ToolResult(content=f"Could not run shortcut {name}: {completed.stderr.strip()}")
        output = completed.stdout.strip()
        return ToolResult(content=f"Shortcut ran: {name}" + (f"\n{output}" if output else ""))


class SetClipboardTool(BaseTool):
    name = "set_clipboard_text"
    description = "Replace the macOS clipboard text. Requires server-side user approval."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["text"],
    }

    def __init__(self, approval_manager: ApprovalManager | None = None):
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(self, text: str = "", confirmed: bool = False, approval_id: str = "", **_) -> ToolResult:
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="clipboard change",
            details={"text_length": len(text), "text_preview": text[:80]},
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        completed = await asyncio.to_thread(
            subprocess.run,
            ["pbcopy"],
            input=text,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return ToolResult(content=f"Could not update clipboard: {completed.stderr.strip()}")
        return ToolResult(content=f"Clipboard updated ({len(text)} characters).")


def _session_files(agent: str, codex_home: Path, openclaw_home: Path) -> list[Path]:
    if agent == "codex":
        roots = [codex_home / "sessions"]
        patterns = ("*.jsonl",)
    elif agent == "openclaw":
        roots = [openclaw_home / "agents"]
        patterns = ("*.jsonl", "*.trajectory.jsonl")
    else:
        return []
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            files.extend(path for path in root.rglob(pattern) if path.is_file())
    return sorted(set(files), key=lambda path: path.stat().st_mtime, reverse=True)


def _session_id(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = data.get("payload") if isinstance(data, dict) else None
                if isinstance(payload, dict) and payload.get("id"):
                    return str(payload["id"])
                if isinstance(data, dict) and data.get("id"):
                    return str(data["id"])
    except OSError:
        pass
    return path.stem


def _session_summary(path: Path, index: int) -> dict[str, object]:
    stat = path.stat()
    return {
        "index": index,
        "id": _session_id(path),
        "path": str(path),
        "modified": stat.st_mtime,
        "sizeBytes": stat.st_size,
    }


def _resolve_session(agent: str, session: str, codex_home: Path, openclaw_home: Path) -> Path | None:
    files = _session_files(agent, codex_home, openclaw_home)
    needle = str(session or "").strip()
    if not needle:
        return files[0] if files else None
    if needle.isdigit():
        idx = int(needle) - 1
        return files[idx] if 0 <= idx < len(files) else None
    lowered = needle.lower()
    for path in files:
        sid = _session_id(path).lower()
        if sid.startswith(lowered) or lowered in path.name.lower():
            return path
    return None


class GetAgentStatusTool(BaseTool):
    name = "get_agent_status"
    description = "Report installed terminal-agent bridges and local session counts for Codex and OpenClaw."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, codex_home: Path | None = None, openclaw_home: Path | None = None):
        self.codex_home = (codex_home or Path.home() / ".codex").expanduser()
        self.openclaw_home = (openclaw_home or Path.home() / ".openclaw").expanduser()

    async def execute(self, **_) -> ToolResult:
        return _json(
            {
                "codex": {
                    "available": bool(shutil.which("codex")),
                    "home": str(self.codex_home),
                    "sessionCount": len(_session_files("codex", self.codex_home, self.openclaw_home)),
                },
                "openclaw": {
                    "available": bool(shutil.which("openclaw") or self.openclaw_home.exists()),
                    "home": str(self.openclaw_home),
                    "sessionCount": len(_session_files("openclaw", self.codex_home, self.openclaw_home)),
                },
            }
        )


class ListAgentSessionsTool(BaseTool):
    name = "list_agent_sessions"
    description = "List recent local Codex or OpenClaw session files so Eyra can answer progress/history questions."
    parameters = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }

    def __init__(self, codex_home: Path | None = None, openclaw_home: Path | None = None):
        self.codex_home = (codex_home or Path.home() / ".codex").expanduser()
        self.openclaw_home = (openclaw_home or Path.home() / ".openclaw").expanduser()

    async def execute(self, agent: str = "codex", limit: int = 10, **_) -> ToolResult:
        if agent not in {"codex", "openclaw"}:
            return ToolResult(content="Unknown agent. Use codex or openclaw.")
        limit = max(1, min(int(limit or 10), 50))
        sessions = [_session_summary(path, index + 1) for index, path in enumerate(_session_files(agent, self.codex_home, self.openclaw_home)[:limit])]
        return _json({"agent": agent, "sessions": sessions})


class GetAgentSessionContentTool(ListAgentSessionsTool):
    name = "get_agent_session_content"
    description = "Read a bounded, redacted local Codex or OpenClaw session by index, id prefix, or filename fragment."
    parameters = {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": ["codex", "openclaw"]},
            "session": {"type": "string", "description": "Recent index, session id prefix, or filename fragment."},
            "max_bytes": {"type": "integer", "minimum": 1000, "maximum": _MAX_SESSION_BYTES},
        },
        "required": ["session"],
    }

    async def execute(self, agent: str = "codex", session: str = "", max_bytes: int = _MAX_SESSION_BYTES, **_) -> ToolResult:
        if agent not in {"codex", "openclaw"}:
            return ToolResult(content="Unknown agent. Use codex or openclaw.")
        path = _resolve_session(agent, session, self.codex_home, self.openclaw_home)
        if path is None:
            return ToolResult(content=f"No {agent} session matched: {session}")
        limit = max(1000, min(int(max_bytes or _MAX_SESSION_BYTES), _MAX_SESSION_BYTES))
        try:
            with path.open("rb") as handle:
                data = handle.read(limit + 1)
        except OSError as e:
            return ToolResult(content=f"Could not read session: {e}")
        truncated = len(data) > limit
        text = data[:limit].decode("utf-8", errors="replace")
        header = f"agent={agent}\nsession={_session_id(path)}\npath={path}"
        if truncated:
            header += f"\nshowing first {limit} bytes"
        return ToolResult(content=header + "\n\n" + _redact(text))


class ListCodexSessionsTool(ListAgentSessionsTool):
    name = "list_codex_sessions"
    description = "List recent local Codex sessions."

    async def execute(self, limit: int = 10, **_) -> ToolResult:
        return await super().execute(agent="codex", limit=limit)


class ListOpenClawSessionsTool(ListAgentSessionsTool):
    name = "list_openclaw_sessions"
    description = "List recent local OpenClaw sessions."

    async def execute(self, limit: int = 10, **_) -> ToolResult:
        return await super().execute(agent="openclaw", limit=limit)


class GetCodexSessionContentTool(GetAgentSessionContentTool):
    name = "get_codex_session_content"
    description = "Read a bounded, redacted local Codex session by index, id prefix, or filename fragment."

    async def execute(self, session: str = "", max_bytes: int = _MAX_SESSION_BYTES, **_) -> ToolResult:
        return await super().execute(agent="codex", session=session, max_bytes=max_bytes)


class GetOpenClawStatusTool(GetAgentStatusTool):
    name = "get_openclaw_status"
    description = "Report OpenClaw availability and local session count."

    async def execute(self, **_) -> ToolResult:
        data = json.loads((await super().execute()).content)
        return _json(data["openclaw"])


class RunAgentTaskTool(BaseTool):
    name = "run_agent_task"
    description = (
        "Hand a complex task to a configured terminal agent such as Codex or OpenClaw. "
        "This bridge is opt-in and requires server-side user approval for execution."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Configured agent name, e.g. codex, openclaw, or a static name from EXTERNAL_AGENT_CONFIG_PATH.",
            },
            "task": {"type": "string"},
            "cwd": {"type": "string"},
            "approval_id": {"type": "string"},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve this action."},
        },
        "required": ["agent", "task"],
    }
    costly = True

    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        default_path: Path,
        approval_manager: ApprovalManager | None = None,
        agent_registry: AgentAdapterRegistry | None = None,
    ):
        self._roots = tuple(_as_default_path(root) for root in allowed_roots)
        self._default_path = _as_default_path(default_path)
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER
        self._agent_registry = agent_registry

    async def execute(
        self,
        agent: str = "codex",
        task: str = "",
        cwd: str = "",
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        if not task.strip():
            return ToolResult(content="Missing task.")
        try:
            workdir = _resolve(cwd or str(self._default_path), self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))
        approval = _approval_or_result(
            self._approvals,
            tool_name=self.name,
            title="agent delegation",
            details=_agent_approval_details(agent, task, workdir),
            approval_id=approval_id,
        )
        if approval is not None:
            return approval
        if self._agent_registry is not None:
            adapter = self._agent_registry.get(agent)
            if adapter is not None:
                result = await self._agent_registry.run(AgentJobSpec(agent_name=agent, task=task, cwd=str(workdir)))
                return ToolResult(
                    content=(
                        f"agent={result.agent_name}\n"
                        f"status={result.status}\n"
                        f"exit_code={result.exit_code}\n\n"
                        f"{result.output}"
                    )
                )
        return ToolResult(
            content=(
                f"{agent} is not configured for execution. Add a static CLI adapter in "
                "EXTERNAL_AGENT_CONFIG_PATH and enable EXTERNAL_AGENT_TOOLS_ENABLED=true."
            )
        )


class RunCodexTaskTool(RunAgentTaskTool):
    name = "run_codex_task"
    description = "Delegate a complex task to Codex. Requires server-side user approval."

    async def execute(
        self,
        task: str = "",
        cwd: str = "",
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        return await super().execute(agent="codex", task=task, cwd=cwd, confirmed=confirmed, approval_id=approval_id)


class RunOpenClawAgentTool(RunAgentTaskTool):
    name = "run_openclaw_agent"
    description = "Delegate a complex task to OpenClaw. Requires server-side user approval."

    async def execute(
        self,
        task: str = "",
        cwd: str = "",
        confirmed: bool = False,
        approval_id: str = "",
        **_,
    ) -> ToolResult:
        return await super().execute(agent="openclaw", task=task, cwd=cwd, confirmed=confirmed, approval_id=approval_id)
