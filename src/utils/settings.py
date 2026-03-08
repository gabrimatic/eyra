"""
Application settings and configuration management.
Handles loading and storing application configuration from environment variables.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class Settings:
    """
    Application settings container.

    Attributes:
        USE_MOCK_CLIENT (bool): Flag to use mock client
        OLLAMA_HOST (str): Host for Ollama client
        OLLAMA_PORT (int): Port for Ollama client
        SIMPLE_TEXT_MODEL (str): Name of the simple text model to use
        MODERATE_TEXT_MODEL (str): Name of the moderate text model to use
        SIMPLE_IMAGE_MODEL (str): Name of the simple image model to use
        MODERATE_IMAGE_MODEL (str): Name of the moderate image model to use
        COMPLEX_MODEL (str): Name of the complex model to use
        GOOGLE_API_KEY (str): Google API key for Google AI
        SCREENSHOT_INTERVAL (int): Interval between screenshots in seconds
        VOICE_MODEL_PATH (str): Path to the voice recognition model file
        VOICE_LANG (str): Language code for voice recognition
        VOICE_MESSAGES (dict): Messages for voice mode
        VOICE_CONVERSATION (dict): Conversation settings for voice mode
        VOICE_TTS_FALLBACK (bool): Fall back to pyttsx3 if TTS fails
    """

    USE_MOCK_CLIENT: bool = False
    OLLAMA_HOST: str = "localhost"
    OLLAMA_PORT: int = 11434
    SIMPLE_TEXT_MODEL: str = "phi3"
    MODERATE_TEXT_MODEL: str = "gemini-1.5-flash"
    SIMPLE_IMAGE_MODEL: str = "gemini-1.5-flash"
    MODERATE_IMAGE_MODEL: str = "gemini-1.5-flash"
    COMPLEX_MODEL: str = "gemini-1.5-flash"
    GOOGLE_API_KEY: str = ""
    SCREENSHOT_INTERVAL: int = 1
    VOICE_MODEL_PATH: str = os.path.join(
        "src", "modes", "voice", "models", "tiny.en.pt"
    )
    VOICE_LANG: str = "en"
    VOICE_TTS_FALLBACK: bool = True  # Fall back to pyttsx3 if TTS fails
    VOICE_MESSAGES: dict = field(
        default_factory=lambda: {
            "pressSpace": "Press and hold space to talk, release to stop.",
            "loadingModel": "Loading model...",
            "noAudioInput": "Error: No audio input detected",
        }
    )
    VOICE_CONVERSATION: dict = field(
        default_factory=lambda: {
            "context": "This conversation is entirely in English.",
            "greeting": "I'm listening.",
            "recognitionWaitMsg": "Yes.",
            "llmWaitMsg": "Let me think about that.",
        }
    )

    @classmethod
    def load_from_env(cls):
        """Load settings from environment variables."""
        return cls(
            GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY"),
            VOICE_MODEL_PATH=os.getenv(
                "VOICE_MODEL_PATH",
                os.path.join("src", "modes", "voice", "models", "tiny.en.pt"),
            ),
            VOICE_LANG=os.getenv("VOICE_LANG", cls.VOICE_LANG),
            VOICE_TTS_FALLBACK=os.getenv("VOICE_TTS_FALLBACK", "true").lower()
            == "true",
        )
