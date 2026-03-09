import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    USE_MOCK_CLIENT: bool = False
    API_BASE_URL: str = "http://localhost:11434/v1"
    API_KEY: str = "ollama"
    SIMPLE_TEXT_MODEL: str = "qwen3.5:2b"
    MODERATE_TEXT_MODEL: str = "qwen3.5:4b"
    SIMPLE_IMAGE_MODEL: str = "qwen3.5:2b"
    MODERATE_IMAGE_MODEL: str = "qwen3.5:4b"
    COMPLEX_MODEL: str = "qwen3.5:9b"
    SCREENSHOT_INTERVAL: int = 1

    # Live runtime settings
    AUTO_PULL_MODELS: bool = True
    LIVE_LISTENING_ENABLED: bool = True
    LIVE_SPEECH_ENABLED: bool = True
    LIVE_OBSERVATION_ENABLED: bool = True
    OBSERVATION_DEBOUNCE_MS: int = 1500
    OBSERVATION_COOLDOWN_MS: int = 5000
    SPEECH_COOLDOWN_MS: int = 3000

    @classmethod
    def load_from_env(cls):
        def _bool(key: str, default: str = "true") -> bool:
            return os.getenv(key, default).lower() == "true"

        return cls(
            USE_MOCK_CLIENT=_bool("USE_MOCK_CLIENT", "false"),
            API_BASE_URL=os.getenv("API_BASE_URL", "http://localhost:11434/v1"),
            API_KEY=os.getenv("API_KEY", "ollama"),
            SIMPLE_TEXT_MODEL=os.getenv("SIMPLE_TEXT_MODEL", "qwen3.5:2b"),
            MODERATE_TEXT_MODEL=os.getenv("MODERATE_TEXT_MODEL", "qwen3.5:4b"),
            SIMPLE_IMAGE_MODEL=os.getenv("SIMPLE_IMAGE_MODEL", "qwen3.5:2b"),
            MODERATE_IMAGE_MODEL=os.getenv("MODERATE_IMAGE_MODEL", "qwen3.5:4b"),
            COMPLEX_MODEL=os.getenv("COMPLEX_MODEL", "qwen3.5:9b"),
            SCREENSHOT_INTERVAL=int(os.getenv("SCREENSHOT_INTERVAL", "1")),
            AUTO_PULL_MODELS=_bool("AUTO_PULL_MODELS"),
            LIVE_LISTENING_ENABLED=_bool("LIVE_LISTENING_ENABLED"),
            LIVE_SPEECH_ENABLED=_bool("LIVE_SPEECH_ENABLED"),
            LIVE_OBSERVATION_ENABLED=_bool("LIVE_OBSERVATION_ENABLED"),
            OBSERVATION_DEBOUNCE_MS=int(os.getenv("OBSERVATION_DEBOUNCE_MS", "1500")),
            OBSERVATION_COOLDOWN_MS=int(os.getenv("OBSERVATION_COOLDOWN_MS", "5000")),
            SPEECH_COOLDOWN_MS=int(os.getenv("SPEECH_COOLDOWN_MS", "3000")),
        )

    @property
    def all_model_names(self) -> list[str]:
        seen = []
        for name in [
            self.SIMPLE_TEXT_MODEL, self.MODERATE_TEXT_MODEL,
            self.SIMPLE_IMAGE_MODEL, self.MODERATE_IMAGE_MODEL,
            self.COMPLEX_MODEL,
        ]:
            if name not in seen:
                seen.append(name)
        return seen
