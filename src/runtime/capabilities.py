"""Runtime capability and privacy boundary reporting."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse

from runtime.connectors.registry import ConnectorRegistry
from runtime.external_agents import AgentAdapterRegistry
from runtime.models import LiveRuntimeState, PreflightResult
from runtime.privacy import evaluate_privacy_boundary, privacy_decision_dict
from utils.settings import Settings

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_local_base_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"", "http", "https"} and host in _LOCAL_HOSTS


def _model_capability(settings: Settings, preflight: PreflightResult | None, model: str, capability: str) -> str:
    if not model:
        return "unknown"
    if preflight is None:
        return "unknown"
    if capability == "tool":
        checked = set(preflight.tool_capability_checked_models)
        capable = set(preflight.tool_capable_models)
    else:
        checked = set(preflight.vision_capability_checked_models)
        capable = set(preflight.vision_capable_models)
    if model not in checked:
        return "unknown"
    return "yes" if model in capable else "no"


def build_capability_snapshot(
    settings: Settings,
    *,
    preflight: PreflightResult | None = None,
    state: LiveRuntimeState | None = None,
) -> dict:
    """Return current local capability and privacy state as structured data."""
    model = settings.MODEL
    worker_model = settings.WORKER_MODEL or settings.MODEL
    vision_model = settings.VISION_MODEL or settings.MODEL
    provider_local = _is_local_base_url(settings.API_BASE_URL)
    listening_ready = bool(
        state.listening_enabled
        if state is not None
        else (
            preflight.listening_available
            if preflight is not None and preflight.listening_available is not None
            else preflight.wh_available
            if preflight is not None
            else settings.LIVE_LISTENING_ENABLED
        )
    )
    speech_ready = bool(
        state.speech_enabled
        if state is not None
        else (
            preflight.speech_available
            if preflight is not None and preflight.speech_available is not None
            else preflight.wh_available
            if preflight is not None
            else settings.LIVE_SPEECH_ENABLED
        )
    )
    screen_ready = bool(
        preflight.screen_capture_available if preflight is not None else shutil.which("screencapture")
    )
    remote_paths: list[str] = []
    if not provider_local:
        remote_paths.append("model_provider")
    if settings.NETWORK_TOOLS_ENABLED:
        remote_paths.append("network_tools")
    if settings.REALTIME_VOICE_ENABLED:
        remote_paths.append("realtime_voice")
    if settings.REALTIME_TOOLS_ENABLED:
        remote_paths.append("realtime_tools")
    external_agent_registry = AgentAdapterRegistry.from_settings(
        settings,
        allowed_roots=tuple(Path(root.strip()).expanduser() for root in settings.FILESYSTEM_ALLOWED_PATHS.split(",") if root.strip()),
        default_path=Path(settings.FILESYSTEM_DEFAULT_PATH).expanduser(),
    )
    connector_registry = ConnectorRegistry.from_settings(settings)

    return {
        "localFirst": True,
        "models": {
            "main": model,
            "worker": worker_model,
            "vision": vision_model,
            "providerBaseUrl": settings.API_BASE_URL,
            "providerLocal": provider_local,
            "backendReady": bool(state.backend_ready if state is not None else preflight.backend_reachable if preflight else False),
            "mainToolCalling": _model_capability(settings, preflight, worker_model, "tool"),
            "visionImages": _model_capability(settings, preflight, vision_model, "vision"),
        },
        "voice": {
            "handsFreeMode": settings.HANDS_FREE_MODE,
            "localWhisper": {
                "enabled": settings.LIVE_LISTENING_ENABLED or settings.LIVE_SPEECH_ENABLED,
                "listeningReady": listening_ready,
                "speechReady": speech_ready,
                "ready": listening_ready or speech_ready,
                "binary": state.wh_bin if state is not None else preflight.wh_bin if preflight is not None else None,
            },
            "realtime": {
                "enabled": settings.REALTIME_VOICE_ENABLED,
                "model": settings.REALTIME_MODEL,
                "toolsEnabled": settings.REALTIME_TOOLS_ENABLED,
            },
        },
        "screen": {
            "captureReady": screen_ready,
            "visionModel": vision_model,
        },
        "tools": {
            "filesystem": {
                "enabled": True,
                "allowedRoots": settings.FILESYSTEM_ALLOWED_PATHS,
                "defaultPath": settings.FILESYSTEM_DEFAULT_PATH,
                "trashRestore": True,
                "permanentDeleteRequiresApproval": True,
            },
            "network": {"enabled": settings.NETWORK_TOOLS_ENABLED},
            "os": {"enabled": settings.OS_TOOLS_ENABLED},
            "browser": {"enabled": settings.NETWORK_TOOLS_ENABLED},
            "mcp": {"enabled": settings.MCP_TOOLS_ENABLED, "configPath": settings.MCP_CONFIG_PATH},
            "memory": {
                "enabled": settings.MEMORY_ENABLED,
                "provider": settings.MEMORY_PROVIDER,
                "path": settings.MEMORY_PATH,
                "contextMaxChars": settings.MEMORY_CONTEXT_MAX_CHARS,
            },
            "connectors": connector_registry.capability_snapshot(),
            "agents": {
                "enabled": settings.AGENT_TOOLS_ENABLED or settings.EXTERNAL_AGENT_TOOLS_ENABLED,
                "external": external_agent_registry.capability_snapshot(),
            },
            "web": {
                "enabled": settings.WEB_UI_ENABLED,
                "host": settings.WEB_UI_HOST,
                "port": settings.WEB_UI_PORT,
            },
        },
        "privacy": {
            "leavesMachineByDefault": bool(remote_paths),
            "remotePaths": remote_paths,
            "telemetry": False,
            "analytics": False,
            "localJobStore": settings.JOB_STORE_PATH,
            "localMemoryStore": settings.MEMORY_PATH,
            "boundaries": [
                privacy_decision_dict(
                    evaluate_privacy_boundary(
                        settings,
                        action="model.screen_summary",
                        data_classes=["prompt", "screenshot"],
                    )
                ),
                privacy_decision_dict(
                    evaluate_privacy_boundary(settings, action="network.web_search", data_classes=["search_query"])
                ),
                privacy_decision_dict(
                    evaluate_privacy_boundary(
                        settings,
                        action="realtime.voice_turn",
                        data_classes=["microphone_audio", "transcript"],
                    )
                ),
            ],
        },
    }


def format_capability_answer(snapshot: dict) -> str:
    """Compact human-readable capability summary for terminal and voice answers."""
    tools = snapshot["tools"]
    voice = snapshot["voice"]
    privacy = snapshot["privacy"]
    lines = [
        f"Local-first default: {'yes' if snapshot['localFirst'] else 'no'}",
        f"Model provider: {'local' if snapshot['models']['providerLocal'] else 'remote'} ({snapshot['models']['providerBaseUrl']})",
        f"Filesystem: {'on' if tools['filesystem']['enabled'] else 'off'} ({tools['filesystem']['allowedRoots']})",
        f"Trash/restore: {'on' if tools['filesystem']['trashRestore'] else 'off'}",
        "Permanent delete: approval required",
        f"Screen capture: {'on' if snapshot['screen']['captureReady'] else 'off'}",
        f"Local voice: {'on' if voice['localWhisper']['ready'] else 'off'}",
        f"Network tools: {'on' if tools['network']['enabled'] else 'off'}",
        f"OS tools: {'on' if tools['os']['enabled'] else 'off'}",
        f"Browser tools: {'on' if tools['browser']['enabled'] else 'off'}",
        f"MCP tools: {'on' if tools['mcp']['enabled'] else 'off'}",
        f"Connectors: {'on' if tools['connectors']['enabled'] else 'off'}",
        f"Agent tools: {'on' if tools['agents']['enabled'] else 'off'}",
        f"Realtime: {'on' if voice['realtime']['enabled'] else 'off'}",
        f"Leaves machine by default: {'yes' if privacy['leavesMachineByDefault'] else 'no'}",
    ]
    if privacy["remotePaths"]:
        lines.append("Remote paths: " + ", ".join(privacy["remotePaths"]))
    return "\n".join(lines)
