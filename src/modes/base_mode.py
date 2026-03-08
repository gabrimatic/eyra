# base_mode.py

"""
BaseMode is an abstract class that each mode (ManualMode, LiveMode, etc.) extends.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from clients.base_client import BaseAIClient
from clients.ollama_client import OllamaClient
from utils.settings import Settings


class BaseMode(ABC):
    """
    BaseMode provides shared attributes & initialization logic for each mode.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Optional[BaseAIClient] = self._initialize_client()
        self.logger = logging.getLogger(self.__class__.__name__)

    def _initialize_client(self) -> BaseAIClient:
        """
        By default, use an OllamaClient for any mode.
        If you want a different client, override in a subclass or adjust as needed.
        """
        return OllamaClient(self.settings)

    @abstractmethod
    async def run(self) -> None:
        """
        Each mode must implement a run() method for its main logic.
        """
        pass
