# base_mode.py

"""
BaseMode is an abstract class that each mode (ManualMode, LiveMode, etc.) extends.
"""

import logging
from abc import ABC, abstractmethod

from utils.settings import Settings


class BaseMode(ABC):
    """
    BaseMode provides shared attributes & initialization logic for each mode.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self) -> None:
        """
        Each mode must implement a run() method for its main logic.
        """
        pass
