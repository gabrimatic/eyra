import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    USE_MOCK_CLIENT: bool = False
    API_BASE_URL: str = "http://localhost:11434/v1"
    API_KEY: str = "ollama"
    # Default model for all requests (used when complexity routing is off)
    MODEL: str = "gemma3:4b"
    # Tier models (only used when COMPLEXITY_ROUTING_ENABLED=true)
    SIMPLE_MODEL: str = "qwen3.5:2b"
    MODERATE_MODEL: str = "gemma3:4b"
    # Live runtime settings
    AUTO_PULL_MODELS: bool = True
    LIVE_LISTENING_ENABLED: bool = True
    LIVE_SPEECH_ENABLED: bool = True
    SPEECH_COOLDOWN_MS: int = 3000
    # Voice input: silence duration (ms) after speech to stop recording
    VOICE_SILENCE_MS: int = 1500
    # Voice input: Silero VAD threshold (0.0-1.0). Higher = stricter.
    VOICE_VAD_THRESHOLD: float = 0.6
    # Filesystem tool: comma-separated list of allowed root paths (~ expanded)
    FILESYSTEM_ALLOWED_PATHS: str = "~/Documents,/tmp"
    # Filesystem tool: default working directory for the model (~ expanded)
    FILESYSTEM_DEFAULT_PATH: str = "~/Documents"
    # Network-backed tools (weather and browser) are opt-in so the default runtime stays local.
    NETWORK_TOOLS_ENABLED: bool = False
    # OS/operator tools are powerful and therefore opt-in. They stay local.
    OS_TOOLS_ENABLED: bool = False
    # External agent bridges are opt-in and disabled by default.
    AGENT_TOOLS_ENABLED: bool = False
    # MCP bridges are opt-in and disabled by default.
    MCP_TOOLS_ENABLED: bool = False
    MCP_CONFIG_PATH: str = "~/.config/eyra/mcp.json"
    # Built-in Web UI. Disabled by default so terminal-only local use stays quiet.
    WEB_UI_ENABLED: bool = False
    WEB_UI_HOST: str = "127.0.0.1"
    WEB_UI_PORT: int = 8765
    # Realtime voice is online and explicit opt-in. Local Whisper remains the local voice path.
    REALTIME_VOICE_ENABLED: bool = False
    REALTIME_MODEL: str = "gpt-realtime-2"
    REALTIME_VOICE: str = "marin"
    OPENAI_API_KEY: str = ""
    # Experimental: complexity-based routing. When disabled, all requests use MODEL.
    COMPLEXITY_ROUTING_ENABLED: bool = False

    @classmethod
    def load_from_env(cls):
        load_dotenv()

        def _bool(key: str, default: str = "true") -> bool:
            raw = os.getenv(key, default).strip().lower()
            if raw in {"true", "1", "yes", "on"}:
                return True
            if raw in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"Invalid boolean for {key}: '{raw}'. Use true or false.")

        def _int(key: str, default: str) -> int:
            raw = os.getenv(key, default)
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"Invalid integer for {key}: '{raw}'. Check your .env file.")

        def _float_range(key: str, default: str, lo: float, hi: float) -> float:
            raw = os.getenv(key, default)
            try:
                val = float(raw)
            except ValueError:
                raise ValueError(f"Invalid number for {key}: '{raw}'. Check your .env file.")
            if not lo <= val <= hi:
                raise ValueError(f"{key}={val} is out of range [{lo}, {hi}]. Check your .env file.")
            return val

        return cls(
            USE_MOCK_CLIENT=_bool("USE_MOCK_CLIENT", "false"),
            API_BASE_URL=os.getenv("API_BASE_URL", "http://localhost:11434/v1"),
            API_KEY=os.getenv("API_KEY", "ollama"),
            MODEL=os.getenv("MODEL", "gemma3:4b"),
            SIMPLE_MODEL=os.getenv("SIMPLE_MODEL", "qwen3.5:2b"),
            MODERATE_MODEL=os.getenv("MODERATE_MODEL", "gemma3:4b"),
            AUTO_PULL_MODELS=_bool("AUTO_PULL_MODELS"),
            LIVE_LISTENING_ENABLED=_bool("LIVE_LISTENING_ENABLED"),
            LIVE_SPEECH_ENABLED=_bool("LIVE_SPEECH_ENABLED"),
            SPEECH_COOLDOWN_MS=_int("SPEECH_COOLDOWN_MS", "3000"),
            VOICE_SILENCE_MS=_int("VOICE_SILENCE_MS", "1500"),
            VOICE_VAD_THRESHOLD=_float_range("VOICE_VAD_THRESHOLD", "0.6", 0.0, 1.0),
            FILESYSTEM_ALLOWED_PATHS=os.getenv("FILESYSTEM_ALLOWED_PATHS", "~/Documents,/tmp"),
            FILESYSTEM_DEFAULT_PATH=os.getenv("FILESYSTEM_DEFAULT_PATH", "~/Documents"),
            NETWORK_TOOLS_ENABLED=_bool("NETWORK_TOOLS_ENABLED", "false"),
            OS_TOOLS_ENABLED=_bool("OS_TOOLS_ENABLED", "false"),
            AGENT_TOOLS_ENABLED=_bool("AGENT_TOOLS_ENABLED", "false"),
            MCP_TOOLS_ENABLED=_bool("MCP_TOOLS_ENABLED", "false"),
            MCP_CONFIG_PATH=os.getenv("MCP_CONFIG_PATH", "~/.config/eyra/mcp.json"),
            WEB_UI_ENABLED=_bool("WEB_UI_ENABLED", "false"),
            WEB_UI_HOST=os.getenv("WEB_UI_HOST", "127.0.0.1"),
            WEB_UI_PORT=_int("WEB_UI_PORT", "8765"),
            REALTIME_VOICE_ENABLED=_bool("REALTIME_VOICE_ENABLED", "false"),
            REALTIME_MODEL=os.getenv("REALTIME_MODEL", "gpt-realtime-2"),
            REALTIME_VOICE=os.getenv("REALTIME_VOICE", "marin"),
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
            COMPLEXITY_ROUTING_ENABLED=_bool("COMPLEXITY_ROUTING_ENABLED", "false"),
        )

    @property
    def all_model_names(self) -> list[str]:
        """Models that need to be available. Depends on whether routing is enabled."""
        if not self.COMPLEXITY_ROUTING_ENABLED:
            return [self.MODEL]
        seen = []
        for name in [self.SIMPLE_MODEL, self.MODERATE_MODEL, self.MODEL]:
            if name not in seen:
                seen.append(name)
        return seen
