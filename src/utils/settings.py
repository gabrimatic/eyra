"""
Application settings and configuration management.
Handles loading and storing application configuration from environment variables.
"""

import os
from dataclasses import dataclass
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

    @classmethod
    def load_from_env(cls):
        """Load settings from environment variables."""
        return cls(
            GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY"),
        )
