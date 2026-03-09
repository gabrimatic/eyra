"""
BaseMode is an abstract class that each mode extends.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from utils.settings import Settings


class BaseMode(ABC):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self) -> Optional[str]:
        """
        Run the mode. Returns the next interaction style to switch to
        ('text', 'watch', 'voice'), or None to exit the app.
        """
        pass
