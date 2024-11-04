"""
Base class for application modes.
Defines the interface and common functionality for all modes.
"""

from abc import ABC, abstractmethod
from openai import OpenAI
from config.settings import Settings


class BaseMode(ABC):
    """
    Abstract base class for application modes.

    Attributes:
        client (OpenAI): OpenAI client instance for API communication
        settings (Settings): Application settings
        messages (list): Chat history storage
    """

    def __init__(self, client: OpenAI, settings: Settings, messages=None):
        """
        Initialize the base mode.

        Args:
            client (OpenAI): OpenAI client instance
            settings (Settings): Application settings
            messages (list, optional): Shared message history
        """
        self.client = client
        self.settings = settings
        self.messages = (
            messages
            if messages is not None
            else [
                {
                    "role": "system",
                    "content": "You are Eyra, a highly helpful AI assistant with the ability to understand and analyze both text and images. Your primary role is to assist users by interpreting the content of the screenshots or selfies they provide and answering any related questions they may have. Ensure your responses are clear, accurate, and supportive to enhance the user’s understanding and experience.",
                }
            ]
        )
        self.switch_requested = False

    @abstractmethod
    async def run(self):
        """Run the mode. Must be implemented by subclasses."""
        pass
