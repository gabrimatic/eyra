"""
Mock AI client for development and testing.
Simulates streaming behavior without connecting to any backend.
"""

from typing import AsyncGenerator

from clients.base_client import BaseAIClient
from tools.registry import ToolRegistry


class MockAIClient(BaseAIClient):
    """Mock client for testing without a live AI backend."""

    async def generate_completion(
        self, messages: list[dict], model_name: str | None = None, **kwargs
    ) -> dict:
        return {"content": "This is a mock response."}

    async def generate_completion_with_image(
        self,
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
        **kwargs,
    ) -> dict:
        return {"content": "This is a mock image response."}

    async def generate_completion_stream(
        self, messages: list[dict], model_name: str | None = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        yield "This is a mock streaming response."

    async def generate_completion_with_image_stream(
        self,
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        yield "This is a mock streaming image response."

    async def stream_with_tools(
        self,
        messages: list[dict],
        model_name: str | None = None,
        tools: ToolRegistry | None = None,
        include_costly: bool = True,
    ) -> AsyncGenerator[str, None]:
        content = "This is a mock response."
        yield content
        messages.append({"role": "assistant", "content": content})

    async def close(self) -> None:
        pass
