"""System info tool — reports battery, disk, and memory status on macOS."""

import asyncio
import subprocess

from tools.base import BaseTool, ToolResult

_SYSTEM_INFO_PREFIXES = {
    "macos": "macOS:",
    "disk": "Disk:",
    "memory": "Memory:",
    "uptime": "Uptime:",
    "battery": "Battery:",
}


def _run_cmd(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def format_system_info_for_query(content: str, query: str) -> str:
    """Return the narrow system-info line a direct local intent asked for."""

    lowered = query.lower()
    wanted: list[str] = []
    if "macos" in lowered or "mac os" in lowered:
        wanted.append(_SYSTEM_INFO_PREFIXES["macos"])
    if "disk" in lowered or "storage" in lowered:
        wanted.append(_SYSTEM_INFO_PREFIXES["disk"])
    if "memory" in lowered or "ram" in lowered:
        wanted.append(_SYSTEM_INFO_PREFIXES["memory"])
    if "uptime" in lowered or "running" in lowered:
        wanted.append(_SYSTEM_INFO_PREFIXES["uptime"])
    if "battery" in lowered:
        wanted.append(_SYSTEM_INFO_PREFIXES["battery"])

    if not wanted:
        return content

    lines = [line for line in content.splitlines() if any(line.startswith(prefix) for prefix in wanted)]
    if lines:
        return "\n".join(lines)
    if wanted == [_SYSTEM_INFO_PREFIXES["battery"]]:
        return "Battery information is unavailable on this Mac."
    return content


class SystemInfoTool(BaseTool):
    name = "get_system_info"
    description = (
        "Returns system status: macOS version, battery level, disk space, memory usage, and uptime. "
        "Call this when the user asks about their computer's macOS version, battery, storage, RAM, "
        "or how long it's been running. "
        "Takes no parameters."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> ToolResult:
        def _collect() -> str:
            parts = []

            # OS version
            macos = _run_cmd(["sw_vers", "-productVersion"])
            build = _run_cmd(["sw_vers", "-buildVersion"])
            if macos:
                suffix = f" ({build})" if build else ""
                parts.append(f"macOS: {macos}{suffix}")

            # Battery
            battery = _run_cmd(["pmset", "-g", "batt"])
            if battery:
                for line in battery.splitlines():
                    if "%" in line:
                        parts.append(f"Battery: {line.strip()}")
                        break

            # Disk
            disk = _run_cmd(["df", "-h", "/"])
            if disk:
                lines = disk.splitlines()
                if len(lines) >= 2:
                    fields = lines[1].split()
                    if len(fields) >= 4:
                        parts.append(f"Disk: {fields[3]} available of {fields[1]} total ({fields[4]} used)")

            # Memory pressure
            pressure = _run_cmd(["memory_pressure"])
            if pressure:
                for line in pressure.splitlines():
                    if "System-wide memory free percentage" in line:
                        parts.append(f"Memory: {line.strip()}")
                        break

            # Uptime
            uptime = _run_cmd(["uptime"])
            if uptime:
                parts.append(f"Uptime: {uptime.strip()}")

            return "\n".join(parts) if parts else "Could not collect system information."

        info = await asyncio.to_thread(_collect)
        return ToolResult(content=info)
