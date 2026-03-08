import aiohttp
import json
import logging
from typing import List, Dict, Optional, Any, AsyncGenerator

from clients.base_client import BaseAIClient
from utils.settings import Settings


class OllamaClient(BaseAIClient):
    """Client for interacting with Ollama models (locally or via local API)."""

    def __init__(self, settings: Settings, model_name: Optional[str] = None):
        self.settings = settings
        self.default_model = model_name or settings.SIMPLE_TEXT_MODEL
        self.base_url = f"http://{settings.OLLAMA_HOST}:{settings.OLLAMA_PORT}"
        self.logger = logging.getLogger(self.__class__.__name__)

        # We open the session once (with a large timeout).
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300))

    async def close(self) -> None:
        """Close the aiohttp session if not already closed."""
        if not self.session.closed:
            await self.session.close()

    async def generate_completion(
        self,
        messages: List[Dict[str, Any]],
        model_name: Optional[str] = None,
        format: Optional[dict] = None,  # <-- ADDED: optional schema
        **kwargs
    ) -> Dict[str, Any]:
        """
        Non-streaming text completion.
        Calls generate_completion_stream, accumulates chunks.
        """
        result = ""
        async for chunk in self.generate_completion_stream(
            messages, model_name=model_name, format=format, **kwargs
        ):
            result += chunk
        return {"content": result}

    async def generate_completion_with_image(
        self,
        messages: List[Dict[str, Any]],
        image_base64: str,
        model_name: Optional[str] = None,
        format: Optional[dict] = None,  # <-- ADDED: optional schema
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Non-streaming image-based completion.
        Calls generate_completion_with_image_stream, accumulates chunks.
        """
        result = ""
        async for chunk in self.generate_completion_with_image_stream(
            messages, image_base64, model_name=model_name, format=format, **kwargs
        ):
            result += chunk
        return {"content": result}

    async def generate_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        model_name: Optional[str] = None,
        format: Optional[dict] = None,  # <-- ADDED: optional schema
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion for text only. Yields string chunks.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty.")

        chosen_model = model_name or self.default_model
        url = f"{self.base_url}/api/chat"

        # Format messages for Ollama
        payload = {
            "model": chosen_model,
            "messages": self._format_messages(messages),
            "stream": True,
        }
        # If a 'format' is provided (Pydantic schema), include it
        if format is not None:
            payload["format"] = format

        async with self.session.post(url, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise Exception(f"Ollama API error {resp.status}: {err_text}")

            # Stream response line by line
            async for line in resp.content:
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    if chunk.get("done"):
                        break
                    content = chunk.get("message", {}).get("content")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse JSON chunk: {e}")
                    continue

    async def generate_completion_with_image_stream(
        self,
        messages: List[Dict[str, Any]],
        image_base64: str,
        model_name: Optional[str] = None,
        format: Optional[dict] = None,  # <-- ADDED: optional schema
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion for text + an image. Yields string chunks.
        Uses the /api/generate endpoint for image processing.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty.")

        chosen_model = model_name or self.settings.SIMPLE_IMAGE_MODEL
        url = f"{self.base_url}/api/generate"

        # Format messages and extract the prompt from the last message
        formatted_messages = self._format_messages_with_image(messages, image_base64)
        last_message = formatted_messages[-1]
        prompt = last_message.get("content", "")

        if isinstance(prompt, list):
            # If it's multi-part content
            prompt = " ".join(
                part.get("text", "") for part in prompt if part.get("type") == "text"
            )

        payload = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": True,
            "images": [image_base64],
        }
        if format is not None:
            payload["format"] = format

        async with self.session.post(url, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise Exception(f"Ollama API error {resp.status}: {err_text}")

            async for line in resp.content:
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    if chunk.get("done"):
                        break
                    content = chunk.get("response", "")
                    if content:
                        yield content
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse JSON chunk: {e}")
                    continue

    def _format_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Cleanly handle textual messages.
        We skip or log any malformed ones.
        """
        formatted = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role and content:
                formatted.append({"role": role, "content": content})
            else:
                self.logger.warning(f"Skipping malformed message: {msg}")
        return formatted

    def _format_messages_with_image(
        self, messages: List[Dict[str, Any]], image_b64: str
    ) -> List[Dict[str, Any]]:
        """
        Format messages with image data.
        Handles both simple text messages and messages with image content.
        For image messages, ensures proper formatting according to Ollama API spec.
        """
        formatted = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if not (role and content):
                self.logger.warning(f"Skipping malformed message: {msg}")
                continue

            if isinstance(content, list):
                # Handle multi-part content (text + image)
                content_parts = []
                for part in content:
                    p_type = part.get("type")
                    if p_type == "text":
                        content_parts.append(
                            {"type": "text", "text": part.get("text", "")}
                        )
                    elif p_type == "image_url":
                        # For image parts, we'll handle them in the generate endpoint
                        continue
                formatted.append({"role": role, "content": content_parts})
            else:
                # Simple text message
                formatted.append({"role": role, "content": content})
        return formatted

    @property
    def is_local(self) -> bool:
        return True