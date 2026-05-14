import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass
class Settings:
    USE_MOCK_CLIENT: bool = False
    API_BASE_URL: str = "http://localhost:11434/v1"
    API_KEY: str = "ollama"
    # Default model for all requests (used when complexity routing is off)
    MODEL: str = "gemma4:e4b"
    # Vision model for deterministic screen/image understanding. Empty means MODEL.
    VISION_MODEL: str = ""
    # Tier models (only used when COMPLEXITY_ROUTING_ENABLED=true)
    SIMPLE_MODEL: str = "qwen3.5:2b"
    MODERATE_MODEL: str = "gemma4:e4b"
    # Live runtime settings
    AUTO_PULL_MODELS: bool = True
    LIVE_LISTENING_ENABLED: bool = True
    LIVE_SPEECH_ENABLED: bool = True
    SPEECH_COOLDOWN_MS: int = 3000
    # Voice input: optional sounddevice input device index or name.
    VOICE_INPUT_DEVICE: str = ""
    # Voice input: sample rate used by Silero VAD and Local Whisper WAV export.
    VOICE_SAMPLE_RATE: int = 16000
    # Voice diagnostics: bounded capture length for local microphone probes.
    VOICE_DEBUG_RECORD_SECONDS: int = 3
    # Voice diagnostics: save captured probe audio under a local diagnostics folder.
    VOICE_DIAGNOSTIC_SAVE_AUDIO: bool = False
    # Voice input: silence duration (ms) after speech to stop recording
    VOICE_SILENCE_MS: int = 1500
    # Voice input: Silero VAD threshold (0.0-1.0). Higher = stricter.
    VOICE_VAD_THRESHOLD: float = 0.15
    # Filesystem tool: comma-separated list of allowed root paths (~ expanded)
    FILESYSTEM_ALLOWED_PATHS: str = "~/Documents,~/Desktop,~/Downloads,/tmp"
    # Filesystem tool: default working directory for the model (~ expanded)
    FILESYSTEM_DEFAULT_PATH: str = "~/Documents"
    # Network-backed tools (weather and browser) are opt-in so the default runtime stays local.
    NETWORK_TOOLS_ENABLED: bool = False
    # Background task coordinator.
    BACKGROUND_TASKS_ENABLED: bool = True
    MAX_BACKGROUND_TASKS: int = 2
    WORKER_MODEL: str = ""
    TASK_TIMEOUT_SECONDS: int = 300
    MAX_WORKER_TOOL_STEPS: int = 8
    TOOL_TIMEOUT_SECONDS: int = 30
    MODEL_CONCURRENCY: int = 1
    TASK_STATUS_UPDATES: bool = True
    JOB_STORE_PATH: str = "~/.local/share/eyra/jobs.sqlite3"
    TRIGGER_STORE_PATH: str = "~/.local/share/eyra/triggers.sqlite3"
    TRIGGER_CHECK_INTERVAL_SECONDS: float = 0.5
    TRIGGER_TIMEOUT_SECONDS: int = 300
    # OS/operator tools are powerful and therefore opt-in. They stay local.
    OS_TOOLS_ENABLED: bool = False
    # Optional local OCR command for screen text extraction. It must read PNG bytes from stdin.
    SCREEN_OCR_COMMAND: str = ""
    # External agent bridges are opt-in and disabled by default.
    AGENT_TOOLS_ENABLED: bool = False
    EXTERNAL_AGENT_TOOLS_ENABLED: bool = False
    EXTERNAL_AGENT_CONFIG_PATH: str = "~/.config/eyra/agents.json"
    # MCP bridges are opt-in and disabled by default.
    MCP_TOOLS_ENABLED: bool = False
    MCP_CONFIG_PATH: str = "~/.config/eyra/mcp.json"
    # Built-in Web UI. Disabled by default so terminal-only local use stays quiet.
    WEB_UI_ENABLED: bool = False
    WEB_UI_HOST: str = "127.0.0.1"
    WEB_UI_PORT: int = 8765
    WEB_UI_TOKEN: str = ""
    WEB_UI_REQUIRE_TOKEN: str = "auto"
    WEB_UI_MAX_REQUEST_BYTES: int = 1_000_000
    # Realtime voice is online and explicit opt-in. Local Whisper remains the local voice path.
    REALTIME_VOICE_ENABLED: bool = False
    REALTIME_MODEL: str = "gpt-realtime"
    REALTIME_VOICE: str = "marin"
    OPENAI_API_KEY: str = ""
    REALTIME_TOOLS_ENABLED: bool = False
    REALTIME_ALLOWED_TOOLS: str = ""
    # Complexity-based model tiers. When disabled, all requests use MODEL after policy routing.
    COMPLEXITY_ROUTING_ENABLED: bool = False
    ROUTING_DEBUG: bool = False
    HANDS_FREE_MODE: bool = False

    @classmethod
    def load_from_env(cls):
        user_env = Path.home() / ".config" / "eyra" / ".env"
        file_values = {}
        if user_env.exists():
            file_values.update({key: value for key, value in dotenv_values(user_env).items() if value is not None})
        cwd_env = Path.cwd() / ".env"
        if cwd_env.exists():
            file_values.update({key: value for key, value in dotenv_values(cwd_env).items() if value is not None})
        values = {**file_values, **os.environ}

        def _getenv(key: str, default: str = "") -> str:
            return str(values.get(key, default))

        def _bool(key: str, default: str = "true") -> bool:
            raw = _getenv(key, default).strip().lower()
            if raw in {"true", "1", "yes", "on"}:
                return True
            if raw in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"Invalid boolean for {key}: '{raw}'. Use true or false.")

        def _int(key: str, default: str) -> int:
            raw = _getenv(key, default)
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"Invalid integer for {key}: '{raw}'. Check your .env file.")

        def _float_range(key: str, default: str, lo: float, hi: float) -> float:
            raw = _getenv(key, default)
            try:
                val = float(raw)
            except ValueError:
                raise ValueError(f"Invalid number for {key}: '{raw}'. Check your .env file.")
            if not lo <= val <= hi:
                raise ValueError(f"{key}={val} is out of range [{lo}, {hi}]. Check your .env file.")
            return val

        return cls(
            USE_MOCK_CLIENT=_bool("USE_MOCK_CLIENT", "false"),
            API_BASE_URL=_getenv("API_BASE_URL", "http://localhost:11434/v1"),
            API_KEY=_getenv("API_KEY", "ollama"),
            MODEL=_getenv("MODEL", "gemma4:e4b"),
            VISION_MODEL=_getenv("VISION_MODEL", ""),
            SIMPLE_MODEL=_getenv("SIMPLE_MODEL", "qwen3.5:2b"),
            MODERATE_MODEL=_getenv("MODERATE_MODEL", "gemma4:e4b"),
            AUTO_PULL_MODELS=_bool("AUTO_PULL_MODELS"),
            LIVE_LISTENING_ENABLED=_bool("LIVE_LISTENING_ENABLED"),
            LIVE_SPEECH_ENABLED=_bool("LIVE_SPEECH_ENABLED"),
            SPEECH_COOLDOWN_MS=_int("SPEECH_COOLDOWN_MS", "3000"),
            VOICE_INPUT_DEVICE=_getenv("VOICE_INPUT_DEVICE", ""),
            VOICE_SAMPLE_RATE=_int("VOICE_SAMPLE_RATE", "16000"),
            VOICE_DEBUG_RECORD_SECONDS=_int("VOICE_DEBUG_RECORD_SECONDS", "3"),
            VOICE_DIAGNOSTIC_SAVE_AUDIO=_bool("VOICE_DIAGNOSTIC_SAVE_AUDIO", "false"),
            VOICE_SILENCE_MS=_int("VOICE_SILENCE_MS", "1500"),
            VOICE_VAD_THRESHOLD=_float_range("VOICE_VAD_THRESHOLD", "0.15", 0.0, 1.0),
            FILESYSTEM_ALLOWED_PATHS=_getenv("FILESYSTEM_ALLOWED_PATHS", "~/Documents,~/Desktop,~/Downloads,/tmp"),
            FILESYSTEM_DEFAULT_PATH=_getenv("FILESYSTEM_DEFAULT_PATH", "~/Documents"),
            NETWORK_TOOLS_ENABLED=_bool("NETWORK_TOOLS_ENABLED", "false"),
            BACKGROUND_TASKS_ENABLED=_bool("BACKGROUND_TASKS_ENABLED", "true"),
            MAX_BACKGROUND_TASKS=_int("MAX_BACKGROUND_TASKS", "2"),
            WORKER_MODEL=_getenv("WORKER_MODEL", ""),
            TASK_TIMEOUT_SECONDS=_int("TASK_TIMEOUT_SECONDS", "300"),
            MAX_WORKER_TOOL_STEPS=_int("MAX_WORKER_TOOL_STEPS", "8"),
            TOOL_TIMEOUT_SECONDS=_int("TOOL_TIMEOUT_SECONDS", "30"),
            MODEL_CONCURRENCY=_int("MODEL_CONCURRENCY", "1"),
            TASK_STATUS_UPDATES=_bool("TASK_STATUS_UPDATES", "true"),
            JOB_STORE_PATH=_getenv("JOB_STORE_PATH", "~/.local/share/eyra/jobs.sqlite3"),
            TRIGGER_STORE_PATH=_getenv("TRIGGER_STORE_PATH", "~/.local/share/eyra/triggers.sqlite3"),
            TRIGGER_CHECK_INTERVAL_SECONDS=_float_range("TRIGGER_CHECK_INTERVAL_SECONDS", "0.5", 0.01, 60.0),
            TRIGGER_TIMEOUT_SECONDS=_int("TRIGGER_TIMEOUT_SECONDS", "300"),
            OS_TOOLS_ENABLED=_bool("OS_TOOLS_ENABLED", "false"),
            SCREEN_OCR_COMMAND=_getenv("SCREEN_OCR_COMMAND", ""),
            AGENT_TOOLS_ENABLED=_bool("AGENT_TOOLS_ENABLED", "false"),
            EXTERNAL_AGENT_TOOLS_ENABLED=_bool(
                "EXTERNAL_AGENT_TOOLS_ENABLED",
                _getenv("AGENT_TOOLS_ENABLED", "false"),
            ),
            EXTERNAL_AGENT_CONFIG_PATH=_getenv("EXTERNAL_AGENT_CONFIG_PATH", "~/.config/eyra/agents.json"),
            MCP_TOOLS_ENABLED=_bool("MCP_TOOLS_ENABLED", "false"),
            MCP_CONFIG_PATH=_getenv("MCP_CONFIG_PATH", "~/.config/eyra/mcp.json"),
            WEB_UI_ENABLED=_bool("WEB_UI_ENABLED", "false"),
            WEB_UI_HOST=_getenv("WEB_UI_HOST", "127.0.0.1"),
            WEB_UI_PORT=_int("WEB_UI_PORT", "8765"),
            WEB_UI_TOKEN=_getenv("WEB_UI_TOKEN", ""),
            WEB_UI_REQUIRE_TOKEN=_getenv("WEB_UI_REQUIRE_TOKEN", "auto").strip().lower(),
            WEB_UI_MAX_REQUEST_BYTES=_int("WEB_UI_MAX_REQUEST_BYTES", "1000000"),
            REALTIME_VOICE_ENABLED=_bool("REALTIME_VOICE_ENABLED", "false"),
            REALTIME_MODEL=_getenv("REALTIME_MODEL", "gpt-realtime"),
            REALTIME_VOICE=_getenv("REALTIME_VOICE", "marin"),
            OPENAI_API_KEY=_getenv("OPENAI_API_KEY", ""),
            REALTIME_TOOLS_ENABLED=_bool("REALTIME_TOOLS_ENABLED", "false"),
            REALTIME_ALLOWED_TOOLS=_getenv("REALTIME_ALLOWED_TOOLS", ""),
            COMPLEXITY_ROUTING_ENABLED=_bool("COMPLEXITY_ROUTING_ENABLED", "false"),
            ROUTING_DEBUG=_bool("ROUTING_DEBUG", "false"),
            HANDS_FREE_MODE=_bool("HANDS_FREE_MODE", "false"),
        )

    @property
    def all_model_names(self) -> list[str]:
        """Models that need to be available. Depends on whether routing is enabled."""
        if not self.COMPLEXITY_ROUTING_ENABLED:
            names = [self.MODEL]
            if self.WORKER_MODEL and self.WORKER_MODEL not in names:
                names.append(self.WORKER_MODEL)
            vision_model = self.VISION_MODEL or self.MODEL
            if vision_model and vision_model not in names:
                names.append(vision_model)
            return names
        seen = []
        for name in [self.SIMPLE_MODEL, self.MODERATE_MODEL, self.MODEL, self.WORKER_MODEL, self.VISION_MODEL]:
            if name and name not in seen:
                seen.append(name)
        return seen
