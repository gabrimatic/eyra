"""User-facing settings metadata and safe .env editing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from utils.settings import Settings


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    description: str
    category: str
    default: str
    simple: bool
    privacy: str
    restart_required: bool = True
    secret: bool = False
    allowed_values: tuple[str, ...] = ()
    value_type: str = "string"


_BOOL_VALUES = {"true": True, "1": True, "yes": True, "on": True, "false": False, "0": False, "no": False, "off": False}


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("MODEL", "Main model", "The local or configured model Eyra uses for most replies.", "Models", "gemma4:e4b", True, "Model requests stay local when this points at a local provider."),
    SettingSpec("VISION_MODEL", "Vision model", "Optional model for screen and image understanding. Empty means use the main model.", "Models", "", True, "Screen requests go to this configured model."),
    SettingSpec("API_BASE_URL", "AI provider URL", "The OpenAI-compatible endpoint Eyra talks to.", "Privacy", "http://localhost:11434/v1", True, "Localhost keeps model requests on this Mac. Cloud URLs send model requests to that provider."),
    SettingSpec("API_KEY", "AI provider key", "Whether a provider key is configured. The value is never shown by status or settings.", "Privacy", "ollama", True, "A cloud provider key can allow prompts to leave this Mac.", secret=True),
    SettingSpec("LIVE_LISTENING_ENABLED", "Voice input", "Listen through the microphone when voice is ready.", "Voice", "true", True, "Microphone audio is processed locally by default.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("LIVE_SPEECH_ENABLED", "Speech output", "Speak Eyra's answers aloud when Local Whisper is ready.", "Voice", "true", True, "Text-to-speech is local by default.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("VOICE_INPUT_DEVICE", "Microphone", "Optional microphone device index or name. Empty means the system default.", "Voice", "", True, "Only affects local microphone capture."),
    SettingSpec("VOICE_VAD_THRESHOLD", "Voice sensitivity", "Speech detection threshold from 0.0 to 1.0. Higher is stricter.", "Voice", "0.15", True, "Only affects local speech detection.", value_type="float"),
    SettingSpec("FILESYSTEM_ALLOWED_PATHS", "Allowed folders", "Folders Eyra may read or write when file tools are used.", "Folders", "~/Documents,~/Desktop,~/Downloads,/tmp", True, "Controls local filesystem access."),
    SettingSpec("FILESYSTEM_DEFAULT_PATH", "Default folder", "Where relative file requests start.", "Folders", "~/Documents", True, "Controls local filesystem access."),
    SettingSpec("WEB_UI_ENABLED", "Web UI", "Start the local browser interface when Eyra starts.", "Web UI", "false", True, "The Web UI binds locally by default and keeps token auth on.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("NETWORK_TOOLS_ENABLED", "Network tools", "Allow weather, browser, and URL tools.", "Advanced Tools", "false", True, "When on, web requests can leave this Mac.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("OS_TOOLS_ENABLED", "Mac control tools", "Allow local app/window/UI/shell-adjacent tools behind policy and approvals.", "Advanced Tools", "false", True, "Stays local, but can control this Mac when enabled.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("CONNECTORS_ENABLED", "Connectors", "Allow configured connector workers.", "Connectors", "false", True, "Connector privacy depends on each manifest and Eyra policy.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("MEMORY_ENABLED", "Memory", "Use compact local durable memory.", "Memory", "true", True, "Memory is stored locally through mcp-prose-memory and injected with a small context budget.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("MEMORY_AUTO_SAVE_ENABLED", "Memory auto-save", "Allow Eyra to save compact durable facts from explicit memory requests.", "Memory", "true", True, "Only short local key/value facts are stored; raw conversations and secrets are rejected.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("AGENTS_FILE", "Instructions file", "User-editable rules loaded into each model turn with a compact budget.", "Memory", "~/.config/eyra/AGENTS.md", True, "Local file only. Keep it short because it is injected into context."),
    SettingSpec("PERSONALITY_FILE", "Personality file", "User-editable personality notes loaded into each model turn with a compact budget.", "Memory", "~/.config/eyra/personality.md", True, "Local file only. Keep it short because it is injected into context."),
    SettingSpec("REALTIME_VOICE_ENABLED", "Realtime voice", "Enable online browser Realtime voice.", "Realtime", "false", True, "Online Realtime voice sends audio/model data to the configured provider.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("MCP_TOOLS_ENABLED", "MCP tools", "Allow configured stdio MCP tools.", "Developer", "false", False, "Depends on configured MCP servers.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("AGENT_TOOLS_ENABLED", "Agent delegation", "Allow local terminal-agent delegation.", "Developer", "false", False, "Depends on configured local agents and approvals.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("EXTERNAL_AGENT_TOOLS_ENABLED", "External agents", "Allow configured external agent bridges.", "Developer", "false", False, "Depends on configured agents and approvals.", value_type="bool", allowed_values=("true", "false")),
    SettingSpec("COMPLEXITY_ROUTING_ENABLED", "Model routing", "Use simple/moderate/main model tiers.", "Developer", "false", False, "Only changes model selection.", value_type="bool", allowed_values=("true", "false")),
)

_SPEC_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}


def setting_specs() -> list[dict[str, Any]]:
    return [asdict(spec) for spec in SETTING_SPECS]


def normalize_setting_value(key: str, raw_value: str) -> str:
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"Unknown setting: {key}")
    value = raw_value.strip()
    if spec.secret:
        raise ValueError(f"{key} is secret. Use `eyra setup` to configure provider keys safely.")
    if spec.value_type == "bool":
        lowered = value.lower()
        if lowered not in _BOOL_VALUES:
            raise ValueError(f"{key} must be true or false.")
        return "true" if _BOOL_VALUES[lowered] else "false"
    if spec.value_type == "float":
        try:
            numeric = float(value)
        except ValueError:
            raise ValueError(f"{key} must be a number.")
        if key == "VOICE_VAD_THRESHOLD" and not 0.0 <= numeric <= 1.0:
            raise ValueError("VOICE_VAD_THRESHOLD must be between 0.0 and 1.0.")
        return str(numeric)
    if spec.allowed_values and value not in spec.allowed_values:
        allowed = ", ".join(spec.allowed_values)
        raise ValueError(f"{key} must be one of: {allowed}.")
    return value


def settings_snapshot(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SETTING_SPECS:
        current = getattr(settings, spec.key, spec.default)
        if isinstance(current, bool):
            rendered = "true" if current else "false"
        else:
            rendered = str(current)
        if spec.secret:
            rendered = "configured" if rendered and rendered not in {"ollama", "none"} else "not needed for local default"
        rows.append({**asdict(spec), "value": rendered})
    return rows


def get_setting(settings: Settings, key: str) -> dict[str, Any]:
    normalized = key.upper()
    spec = _SPEC_BY_KEY.get(normalized)
    if spec is None:
        raise KeyError(f"Unknown setting: {key}")
    return next(row for row in settings_snapshot(settings) if row["key"] == normalized)


def write_setting(env_path: Path, key: str, raw_value: str) -> str:
    normalized = key.upper()
    value = normalize_setting_value(normalized, raw_value)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated = False
    output: list[str] = []
    for line in lines:
        if line.startswith(f"{normalized}="):
            output.append(f"{normalized}={value}")
            updated = True
        else:
            output.append(line)
    if not updated:
        if output and output[-1].strip():
            output.append("")
        output.extend(["# Simple settings edited by eyra settings", f"{normalized}={value}"])
    env_path.write_text("\n".join(output).rstrip() + "\n")
    env_path.chmod(0o600)
    return value
