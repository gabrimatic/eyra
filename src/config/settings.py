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
        API_KEY (str): OpenAI API key
        MODEL_NAME (str): Name of the AI model to use
        MAX_TOKENS (int): Maximum tokens for API responses
        IMAGE_PATH (str): Path for saving captured images
    """

    API_KEY: str
    MODEL_NAME: str
    MAX_TOKENS: int
    IMAGE_PATH: str

    @classmethod
    def load_from_env(cls):
        """Load settings from environment variables."""
        return cls(
            API_KEY=os.getenv("OPENAI_API_KEY"),
            MODEL_NAME=os.getenv("MODEL_NAME"),
            MAX_TOKENS=int(os.getenv("MAX_TOKENS")),
            IMAGE_PATH=os.getenv("IMAGE_PATH"),
        )
