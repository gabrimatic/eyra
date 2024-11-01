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
    
    def __init__(self, client: OpenAI, settings: Settings):
        """
        Initialize the base mode.
        
        Args:
            client (OpenAI): OpenAI client instance
            settings (Settings): Application settings
        """
        self.client = client
        self.settings = settings
        self.messages = [
            {"role": "system", "content": "You are Eyra, an AI assistant created by Soroush. You can understand both text and images."}
        ]

    @abstractmethod
    async def run(self):
        """Run the mode. Must be implemented by subclasses."""
        pass
