# base_client.py

"""
Base client interface for AI model interactions.
Defines the contract that all AI clients must implement.
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

from tools.registry import ToolRegistry


class BaseAIClient(ABC):
    """Base class for AI client implementations."""

    @abstractmethod
    async def generate_completion(
        self, messages: list[dict], model_name: str | None = None, **kwargs
    ) -> dict[str, Any]:
        """
        Generate a completion response (non-streaming).

        Args:
            messages (list[dict]): List of message dictionaries
            model_name (str | None): Name of the model to use
            **kwargs: Additional parameters for extended config

        Returns:
            dict[str, Any]: Response containing the completion (e.g. {"content": "..."}).
        """
        pass

    @abstractmethod
    async def generate_completion_with_image(
        self,
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
        **kwargs
    ) -> dict[str, Any]:
        """
        Generate a completion response for image-based input (non-streaming).

        Args:
            messages (list[dict]): List of message dictionaries
            image_base64 (str): Base64-encoded image data
            model_name (str | None): Name of the model to use
            **kwargs: Additional parameters for extended config

        Returns:
            dict[str, Any]: Response containing the completion (e.g. {"content": "..."}).
        """
        pass

    @abstractmethod
    async def generate_completion_stream(
        self, messages: list[dict], model_name: str | None = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming completion response.

        Args:
            messages (list[dict]): List of message dictionaries
            model_name (str | None): Name of the model to use
            **kwargs: Additional parameters for extended config

        Yields:
            str: Chunks of the completion response
        """
        pass

    @abstractmethod
    async def generate_completion_with_image_stream(
        self,
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming completion response for image-based input.

        Args:
            messages (list[dict]): List of message dictionaries
            image_base64 (str): Base64-encoded image data
            model_name (str | None): Name of the model to use
            **kwargs: Additional parameters for extended config

        Yields:
            str: Chunks of the completion response
        """
        pass

    @abstractmethod
    async def stream_with_tools(
        self,
        messages: list[dict],
        model_name: str | None = None,
        tools: ToolRegistry | None = None,
        include_costly: bool = True,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream a completion with optional tool-calling support.

        If tools are provided, executes any tool calls returned by the model and
        loops until the model produces a final answer (max 5 rounds).

        Args:
            messages: Conversation messages (mutated in-place with tool results).
            model_name: Model to use; falls back to instance default.
            tools: Registry of available tools.
            include_costly: If False, only lightweight tools are sent to the model.

        Yields:
            str: Chunks of the final completion response.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close any underlying sessions or connections if applicable.
        """
        pass
