import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    USE_MOCK_CLIENT: bool = False
    OLLAMA_HOST: str = "localhost"
    OLLAMA_PORT: int = 11434
    SIMPLE_TEXT_MODEL: str = "qwen3.5:2b-q4_K_M"
    MODERATE_TEXT_MODEL: str = "qwen3.5:4b-q4_K_M"
    SIMPLE_IMAGE_MODEL: str = "qwen3.5:2b-q4_K_M"
    MODERATE_IMAGE_MODEL: str = "qwen3.5:4b-q4_K_M"
    COMPLEX_MODEL: str = "qwen3.5:9b-q4_K_M"
    SCREENSHOT_INTERVAL: int = 1

    @classmethod
    def load_from_env(cls):
        return cls(
            USE_MOCK_CLIENT=os.getenv("USE_MOCK_CLIENT", "false").lower() == "true",
            OLLAMA_HOST=os.getenv("OLLAMA_HOST", "localhost"),
            OLLAMA_PORT=int(os.getenv("OLLAMA_PORT", "11434")),
            SIMPLE_TEXT_MODEL=os.getenv("SIMPLE_TEXT_MODEL", "qwen3.5:2b-q4_K_M"),
            MODERATE_TEXT_MODEL=os.getenv("MODERATE_TEXT_MODEL", "qwen3.5:4b-q4_K_M"),
            SIMPLE_IMAGE_MODEL=os.getenv("SIMPLE_IMAGE_MODEL", "qwen3.5:2b-q4_K_M"),
            MODERATE_IMAGE_MODEL=os.getenv("MODERATE_IMAGE_MODEL", "qwen3.5:4b-q4_K_M"),
            COMPLEX_MODEL=os.getenv("COMPLEX_MODEL", "qwen3.5:9b-q4_K_M"),
            SCREENSHOT_INTERVAL=int(os.getenv("SCREENSHOT_INTERVAL", "1")),
        )
