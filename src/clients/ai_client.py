import logging
from typing import List, Dict, Optional, AsyncGenerator

from openai import AsyncOpenAI

from clients.base_client import BaseAIClient
from utils.settings import Settings


class AIClient(BaseAIClient):
    """OpenAI-compatible client. Works with any provider that speaks /v1/chat/completions."""

    def __init__(self, settings: Settings, model_name: Optional[str] = None):
        self.model_name = model_name or settings.SIMPLE_TEXT_MODEL
        self.client = AsyncOpenAI(
            base_url=settings.API_BASE_URL,
            api_key=settings.API_KEY,
        )
        self.logger = logging.getLogger(self.__class__.__name__)

    async def close(self) -> None:
        await self.client.close()

    async def generate_completion(
        self,
        messages: List[Dict],
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        result = ""
        async for chunk in self.generate_completion_stream(messages, model_name=model_name):
            result += chunk
        return {"content": result}

    async def generate_completion_with_image(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        result = ""
        async for chunk in self.generate_completion_with_image_stream(
            messages, image_base64, model_name=model_name
        ):
            result += chunk
        return {"content": result}

    async def generate_completion_stream(
        self,
        messages: List[Dict],
        model_name: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        chosen_model = model_name or self.model_name
        stream = await self.client.chat.completions.create(
            model=chosen_model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is not None and delta != "":
                yield delta

    async def generate_completion_with_image_stream(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        chosen_model = model_name or self.model_name

        # Extract text from last message (handles both str and list content)
        last = messages[-1]
        content = last.get("content", "")
        if isinstance(content, list):
            text = next((p.get("text", "") for p in content if p.get("type") == "text"), "")
        else:
            text = content

        # Rebuild last message with proper OpenAI-compatible image format
        messages_with_image = list(messages[:-1]) + [
            {
                "role": last.get("role", "user"),
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                ],
            }
        ]

        stream = await self.client.chat.completions.create(
            model=chosen_model,
            messages=messages_with_image,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta is not None and delta != "":
                yield delta
