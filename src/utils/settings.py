import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    USE_MOCK_CLIENT: bool = False
    API_BASE_URL: str = "http://localhost:11434/v1"
    API_KEY: str = "ollama"
    # Default model for all requests (used when complexity routing is off)
    MODEL: str = "qwen3.5:4b"
    # Tier models (only used when COMPLEXITY_ROUTING_ENABLED=true)
    SIMPLE_MODEL: str = "qwen3.5:2b"
    MODERATE_MODEL: str = "qwen3.5:4b"
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
    FILESYSTEM_ALLOWED_PATHS: str = "~,/tmp"
    # Filesystem tool: default working directory for the model (~ expanded)
    FILESYSTEM_DEFAULT_PATH: str = "~/Documents"
    # Experimental: complexity-based routing. When disabled, all requests use MODEL.
    COMPLEXITY_ROUTING_ENABLED: bool = False

    @classmethod
    def load_from_env(cls):
        def _bool(key: str, default: str = "true") -> bool:
            return os.getenv(key, default).lower() == "true"

        return cls(
            USE_MOCK_CLIENT=_bool("USE_MOCK_CLIENT", "false"),
            API_BASE_URL=os.getenv("API_BASE_URL", "http://localhost:11434/v1"),
            API_KEY=os.getenv("API_KEY", "ollama"),
            MODEL=os.getenv("MODEL", "qwen3.5:4b"),
            SIMPLE_MODEL=os.getenv("SIMPLE_MODEL", "qwen3.5:2b"),
            MODERATE_MODEL=os.getenv("MODERATE_MODEL", "qwen3.5:4b"),
            AUTO_PULL_MODELS=_bool("AUTO_PULL_MODELS"),
            LIVE_LISTENING_ENABLED=_bool("LIVE_LISTENING_ENABLED"),
            LIVE_SPEECH_ENABLED=_bool("LIVE_SPEECH_ENABLED"),
            SPEECH_COOLDOWN_MS=int(os.getenv("SPEECH_COOLDOWN_MS", "3000")),
            VOICE_SILENCE_MS=int(os.getenv("VOICE_SILENCE_MS", "1500")),
            VOICE_VAD_THRESHOLD=float(os.getenv("VOICE_VAD_THRESHOLD", "0.6")),
            FILESYSTEM_ALLOWED_PATHS=os.getenv("FILESYSTEM_ALLOWED_PATHS", "~,/tmp"),
            FILESYSTEM_DEFAULT_PATH=os.getenv("FILESYSTEM_DEFAULT_PATH", "~/Documents"),
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
