import asyncio
import json
import logging
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from clients.base_client import BaseAIClient
from tools.base import ToolResult
from tools.registry import ToolRegistry
from utils.settings import Settings

logger = logging.getLogger(__name__)

_SPECIAL_TOKENS = (
    "<|im_end|>",
    "<|im_start|>",
    "<|endoftext|>",
    "<|end|>",
)

_TEXT_TOOL_PATTERNS = [
    re.compile(r'<function=(\w+)>(.*?)</function>', re.DOTALL),
    re.compile(r'<tool_call>\s*\{.*?"name"\s*:\s*"(\w+)".*?"arguments"\s*:\s*(\{.*?\})\s*\}\s*</tool_call>', re.DOTALL),
]

# Sentinels embedded in streamed text to mark think-block boundaries.
# The renderer (live_session) converts these to ANSI styling; they never
# reach conversation history, speech, or non-streaming consumers.
THINK_START = "\x02"
THINK_END = "\x03"


def _strip_tokens(text: str) -> str:
    """Remove special tokens from a string."""
    for token in _SPECIAL_TOKENS:
        text = text.replace(token, "")
    return text


def _strip_sentinels(text: str) -> str:
    """Remove think sentinels and their enclosed content from accumulated text.
    Used by non-streaming consumers that should not see think content."""
    parts = text.split(THINK_START)
    result = [parts[0]]
    for part in parts[1:]:
        after = part.split(THINK_END, 1)
        if len(after) == 2:
            result.append(after[1])
    return "".join(result)


def _clean_for_history(content: str) -> str:
    """Strip think blocks and special tokens from raw content before storing
    in message history. Handles both closed and unclosed (truncated) blocks."""
    result = re.sub(r"<think>.*?(?:</think>|$)", "", content, flags=re.DOTALL)
    return _strip_tokens(result)


class StreamCleaner:
    """Filters streaming text, replacing <think>/<​/think> with sentinel
    markers and stripping special tokens that local servers sometimes leak.

    Think content is passed through (not suppressed) so the renderer can
    display it with distinct styling while excluding it from history."""

    # Tail buffer: must be >= the longest tag or special token that could be
    # split across chunks.  Longest is "<|endoftext|>" (13 chars).
    _TAIL = 13

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out_parts: list[str] = []

        while self._buf:
            if self._in_think:
                end = self._buf.find("</think>")
                if end == -1:
                    # Stream think content; hold tail for split </think>.
                    safe = max(0, len(self._buf) - self._TAIL)
                    out_parts.append(self._buf[:safe])
                    self._buf = self._buf[safe:]
                    break
                out_parts.append(self._buf[:end])
                out_parts.append(THINK_END)
                self._buf = self._buf[end + 8:]
                self._in_think = False
            else:
                start = self._buf.find("<think>")
                if start == -1:
                    safe = max(0, len(self._buf) - self._TAIL)
                    out_parts.append(self._buf[:safe])
                    self._buf = self._buf[safe:]
                    break
                out_parts.append(self._buf[:start])
                out_parts.append(THINK_START)
                self._buf = self._buf[start + 7:]
                self._in_think = True

        return _strip_tokens("".join(out_parts))

    def flush(self) -> str:
        """Drain remaining buffer at end of stream."""
        result = self._buf
        if self._in_think:
            result += THINK_END
            self._in_think = False
        self._buf = ""
        return _strip_tokens(result)


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

    async def _clean_stream(
        self, source: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """Wrap an async text generator, stripping think blocks and special tokens."""
        cleaner = StreamCleaner()
        async for chunk in source:
            cleaned = cleaner.feed(chunk)
            if cleaned:
                yield cleaned
        tail = cleaner.flush()
        if tail:
            yield tail

    async def generate_completion(
        self,
        messages: list[dict],
        model_name: str | None = None,
        **kwargs,
    ) -> dict:
        result = ""
        async for chunk in self.generate_completion_stream(messages, model_name=model_name):
            result += chunk
        return {"content": _strip_sentinels(result)}

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
        return {"content": _strip_sentinels(result)}

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

        async def _raw():
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta is not None and delta != "":
                    yield delta

        async for text in self._clean_stream(_raw()):
            yield text

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

        async def _raw():
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta is not None and delta != "":
                    yield delta

        async for text in self._clean_stream(_raw()):
            yield text

    async def stream_with_tools(
        self,
        messages: list[dict],
        model_name: str | None = None,
        tools: ToolRegistry | None = None,
        include_costly: bool = True,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        if not tools or not tools.to_openai_tools(include_costly=include_costly):
            # generate_completion_stream already cleans via _clean_stream
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
            _BUFFER_THRESHOLD = 50
            _cleaner = StreamCleaner()

            stream = await self.client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.7,
                stream=True,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    accumulated_content += delta.content

                    if not _buffer_flushed:
                        _content_buffer += delta.content
                        if len(_content_buffer) >= _BUFFER_THRESHOLD:
                            _buffer_flushed = True
                            if "<function=" in _content_buffer or "<tool_call>" in _content_buffer:
                                _suppress_content = True
                            else:
                                cleaned = _cleaner.feed(_content_buffer)
                                if cleaned:
                                    yield cleaned
                    elif not _suppress_content:
                        cleaned = _cleaner.feed(delta.content)
                        if cleaned:
                            yield cleaned

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
                if "<function=" in _content_buffer or "<tool_call>" in _content_buffer:
                    _suppress_content = True
                else:
                    if not _suppress_content:
                        cleaned = _cleaner.feed(_content_buffer)
                        if cleaned:
                            yield cleaned

            # Flush any remaining buffered content from the cleaner
            if not _suppress_content:
                tail = _cleaner.flush()
                if tail:
                    yield tail

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
                    cleaned = _clean_for_history(accumulated_content)
                    if cleaned:
                        yield cleaned
                    messages.append({"role": "assistant", "content": cleaned})
                    return

            if not tool_calls_raw:
                messages.append({"role": "assistant", "content": _clean_for_history(accumulated_content)})
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
            clean_content = _clean_for_history(accumulated_content) if accumulated_content else None
            assistant_msg = {
                "role": "assistant",
                "content": clean_content,
                "tool_calls": assistant_tool_calls,
            }
            messages.append(assistant_msg)
            if history is not None:
                history.append(assistant_msg)

            # Execute each tool and append results (tool messages first, then any images)
            pending_images: list[dict] = []
            for tc in tool_calls_raw.values():
                try:
                    result = await asyncio.wait_for(tools.execute(tc["name"], tc["arguments"]), timeout=30)
                except asyncio.TimeoutError:
                    logger.error("Tool '%s' timed out after 30s", tc["name"])
                    result = ToolResult(content=f"Tool '{tc['name']}' timed out after 30 seconds.")
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.content,
                }
                messages.append(tool_msg)
                if history is not None:
                    history.append(tool_msg)
                if result.image_base64:
                    pending_images.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Here is the captured screenshot:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{result.image_base64}"}},
                        ],
                    })
            # Append image messages after all tool results
            for img_msg in pending_images:
                messages.append(img_msg)
                if history is not None:
                    history.append(img_msg)

        # Safety: if 5 rounds exhausted without a final text response, yield a fallback
        logger.warning("Tool-calling loop exhausted after 5 rounds")
        yield "\n[Reached tool-call limit. Please try rephrasing your request.]"
