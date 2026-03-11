import json
import logging
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from clients.base_client import BaseAIClient
from tools.registry import ToolRegistry
from utils.settings import Settings

logger = logging.getLogger(__name__)

_TEXT_TOOL_PATTERNS = [
    re.compile(r'<function=(\w+)>(.*?)</function>', re.DOTALL),
    re.compile(r'<tool_call>\s*\{[^}]*"name"\s*:\s*"(\w+)"[^}]*"arguments"\s*:\s*(\{.*?\})[^}]*\}\s*</tool_call>', re.DOTALL),
]


def _parse_text_tool_calls(content: str) -> list[dict] | None:
    """Try to parse text-format tool calls from model output.
    Returns list of {"id": str, "name": str, "arguments": str} or None."""
    results = []
    for pattern in _TEXT_TOOL_PATTERNS:
        for i, match in enumerate(pattern.finditer(content)):
            name = match.group(1)
            arguments = match.group(2).strip()
            try:
                json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                continue
            results.append({"id": f"text_{i}", "name": name, "arguments": arguments})
    return results if results else None


class AIClient(BaseAIClient):
    """OpenAI-compatible client. Works with any provider that speaks /v1/chat/completions."""

    def __init__(self, settings: Settings, model_name: str | None = None):
        self.model_name = model_name or settings.MODEL
        self.client = AsyncOpenAI(
            base_url=settings.API_BASE_URL,
            api_key=settings.API_KEY,
        )
        self.logger = logging.getLogger(self.__class__.__name__)

    async def close(self) -> None:
        await self.client.close()

    async def generate_completion(
        self,
        messages: list[dict],
        model_name: str | None = None,
        **kwargs,
    ) -> dict:
        result = ""
        async for chunk in self.generate_completion_stream(messages, model_name=model_name):
            result += chunk
        return {"content": result}

    async def generate_completion_with_image(
        self,
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
        **kwargs,
    ) -> dict:
        result = ""
        async for chunk in self.generate_completion_with_image_stream(
            messages, image_base64, model_name=model_name
        ):
            result += chunk
        return {"content": result}

    async def generate_completion_stream(
        self,
        messages: list[dict],
        model_name: str | None = None,
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
        messages: list[dict],
        image_base64: str,
        model_name: str | None = None,
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

    async def stream_with_tools(
        self,
        messages: list[dict],
        model_name: str | None = None,
        tools: ToolRegistry | None = None,
        include_costly: bool = True,
    ) -> AsyncGenerator[str, None]:
        if not tools or not tools.to_openai_tools(include_costly=include_costly):
            async for chunk in self.generate_completion_stream(messages, model_name=model_name):
                yield chunk
            return

        chosen_model = model_name or self.model_name
        openai_tools = tools.to_openai_tools(include_costly=include_costly)

        for _ in range(5):
            accumulated_content = ""
            tool_calls_raw: dict[int, dict] = {}
            _content_buffer = ""
            _suppress_content = False
            _buffer_flushed = False
            _BUFFER_THRESHOLD = 30

            stream = await self.client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.7,
                stream=True,
            )

            async for chunk in stream:
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    accumulated_content += delta.content

                    if not _buffer_flushed:
                        _content_buffer += delta.content
                        if len(_content_buffer) >= _BUFFER_THRESHOLD:
                            _buffer_flushed = True
                            if _content_buffer.lstrip().startswith(("<function=", "<tool_call>")):
                                _suppress_content = True
                            else:
                                yield _content_buffer
                    elif not _suppress_content:
                        yield delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_raw:
                            tool_calls_raw[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_raw[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls_raw[idx]["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_raw[idx]["arguments"] += tc.function.arguments

            # If we never hit the threshold, decide now
            if not _buffer_flushed and _content_buffer:
                _buffer_flushed = True
                if _content_buffer.lstrip().startswith(("<function=", "<tool_call>")):
                    _suppress_content = True
                else:
                    if not _suppress_content:
                        yield _content_buffer

            # Text-format tool call recovery
            if not tool_calls_raw and _suppress_content:
                recovered = _parse_text_tool_calls(accumulated_content)
                if recovered:
                    self.logger.info(
                        "Recovered text-format tool call: %s",
                        [r["name"] for r in recovered],
                    )
                    for i, r in enumerate(recovered):
                        tool_calls_raw[i] = r
                else:
                    self.logger.warning("Text tool-call suppressed but parse failed; yielding raw content")
                    yield accumulated_content
                    messages.append({"role": "assistant", "content": accumulated_content})
                    return

            if not tool_calls_raw:
                messages.append({"role": "assistant", "content": accumulated_content})
                self.logger.debug("No tool calls in response (text-only)")
                return

            self.logger.info("Tool calls: %s", [tc["name"] for tc in tool_calls_raw.values()])

            # Build the assistant message with tool_calls for context
            assistant_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls_raw.values()
            ]
            messages.append({
                "role": "assistant",
                "content": accumulated_content or None,
                "tool_calls": assistant_tool_calls,
            })

            # Execute each tool and append results
            for tc in tool_calls_raw.values():
                result = await tools.execute(tc["name"], tc["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.content,
                })
                if result.image_base64:
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Here is the captured screenshot:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{result.image_base64}"}},
                        ],
                    })

        # Safety: if 5 rounds exhausted without a final text response, yield a fallback
        logger.warning("Tool-calling loop exhausted after 5 rounds")
        yield "\n[Reached tool-call limit. Please try rephrasing your request.]"
