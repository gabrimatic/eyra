# base_client.py

"""
Base client interface for AI model interactions.
Defines the contract that all AI clients must implement.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, AsyncGenerator, Any


class BaseAIClient(ABC):
    """Base class for AI client implementations."""

    @abstractmethod
    async def generate_completion(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a completion response (non-streaming).

        Args:
            messages (List[Dict]): List of message dictionaries
            model_name (Optional[str]): Name of the model to use
            **kwargs: Additional parameters for extended config

        Returns:
            Dict[str, Any]: Response containing the completion (e.g. {"content": "..."}).
        """
        pass

    @abstractmethod
    async def generate_completion_with_image(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a completion response for image-based input (non-streaming).

        Args:
            messages (List[Dict]): List of message dictionaries
            image_base64 (str): Base64-encoded image data
            model_name (Optional[str]): Name of the model to use
            **kwargs: Additional parameters for extended config

        Returns:
            Dict[str, Any]: Response containing the completion (e.g. {"content": "..."}).
        """
        pass

    @abstractmethod
    async def generate_completion_stream(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming completion response.

        Args:
            messages (List[Dict]): List of message dictionaries
            model_name (Optional[str]): Name of the model to use
            **kwargs: Additional parameters for extended config

        Yields:
            str: Chunks of the completion response
        """
        pass

    @abstractmethod
    async def generate_completion_with_image_stream(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming completion response for image-based input.

        Args:
            messages (List[Dict]): List of message dictionaries
            image_base64 (str): Base64-encoded image data
            model_name (Optional[str]): Name of the model to use
            **kwargs: Additional parameters for extended config

        Yields:
            str: Chunks of the completion response
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close any underlying sessions or connections if applicable.
        """
        pass
