# google_client.py

"""
Google AI client implementation for Gemini (or other) model interactions.
"""

import logging
from typing import List, Dict, Optional, Any, AsyncGenerator
import asyncio

import google.generativeai as genai
from clients.base_client import BaseAIClient
from utils.settings import Settings


class GoogleAIClient(BaseAIClient):
    """Client for interacting with Google's Gemini (or similar) models."""

    def __init__(self, settings: Settings, model_name: Optional[str] = None):
        self.settings = settings
        self.default_model = model_name or settings.COMPLEX_MODEL
        self.logger = logging.getLogger(self.__class__.__name__)

        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is required for Google AI client")

        # Initialize generative AI config once.
        genai.configure(api_key=settings.GOOGLE_API_KEY)

        # Default generation config (can be extended/overridden via **kwargs).
        self.generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 8192,
        }

    async def close(self) -> None:
        """
        Google generative AI doesn't require an explicit close, but we keep the
        method for interface consistency.
        """
        pass

    async def generate_completion(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Non-streaming completion. We internally call generate_completion_stream and accumulate chunks.
        """
        full_response = ""
        async for chunk in self.generate_completion_stream(
            messages, model_name, **kwargs
        ):
            full_response += chunk
        return {"content": full_response}

    async def generate_completion_with_image(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Non-streaming image-based completion. We call the streaming version and accumulate chunks.
        """
        full_response = ""
        async for chunk in self.generate_completion_with_image_stream(
            messages, image_base64, model_name, **kwargs
        ):
            full_response += chunk
        return {"content": full_response}

    async def generate_completion_stream(
        self, messages: List[Dict], model_name: Optional[str] = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion with textual input only.
        Yields text chunks from the response.
        """
        chosen_model = model_name or self.default_model
        final_config = {**self.generation_config, **kwargs}

        # Build prompt from messages
        prompt = self._build_prompt_from_messages(messages)

        try:
            model = genai.GenerativeModel(
                model_name=chosen_model, generation_config=final_config
            )
            # stream=True => generator that yields partial results
            response_stream = model.generate_content(prompt, stream=True)
            async for chunk in self._process_stream(response_stream):
                yield chunk
        except Exception as e:
            self.logger.error(f"GoogleAIClient generate_completion_stream error: {e}")
            raise

    async def generate_completion_with_image_stream(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion that includes an image in the input.
        """
        chosen_model = model_name or self.default_model
        final_config = {**self.generation_config, **kwargs}

        prompt = self._build_prompt_from_messages(messages)
        image_content = [{"mime_type": "image/jpeg", "data": image_base64}]

        try:
            model = genai.GenerativeModel(
                model_name=chosen_model, generation_config=final_config
            )
            # Provide image + text as a list of contents
            response_stream = model.generate_content(
                contents=[*image_content, prompt], stream=True
            )
            async for chunk in self._process_stream(response_stream):
                yield chunk
        except Exception as e:
            self.logger.error(
                f"GoogleAIClient generate_completion_with_image_stream error: {e}"
            )
            raise

    async def _process_stream(self, response_stream) -> AsyncGenerator[str, None]:
        """
        Process the streaming response from Gemini in an async manner.
        Because Google's streaming might be synchronous, we wrap in run_in_executor if needed.
        """
        loop = asyncio.get_running_loop()
        for chunk in response_stream:
            # chunk.text might be blocking, so we do run_in_executor
            text = await loop.run_in_executor(None, lambda: chunk.text)
            if text:
                yield text

    def _build_prompt_from_messages(self, messages: List[Dict]) -> str:
        """Helper to unify text from all messages into a single prompt."""
        prompt = ""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                prompt += content + "\n"
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        prompt += part.get("text", "") + "\n"
        return prompt.strip()
