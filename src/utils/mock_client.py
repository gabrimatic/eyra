"""
Mock AI client for development and testing.
Simulates streaming behavior without connecting to any backend.
"""

from typing import AsyncGenerator, List, Dict, Optional
from clients.base_client import BaseAIClient


class MockAIClient(BaseAIClient):
    """Mock client for testing without a live AI backend."""

    async def generate_completion(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> Dict:
        return {"content": "This is a mock response."}

    async def generate_completion_with_image(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        return {"content": "This is a mock image response."}

    async def generate_completion_stream(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        yield "This is a mock streaming response."

    async def generate_completion_with_image_stream(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        yield "This is a mock streaming image response."

    async def close(self) -> None:
        pass
