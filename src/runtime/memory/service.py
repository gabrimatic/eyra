"""First-class Eyra memory service backed by mcp-prose-memory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from runtime.memory.compression import compact_text, key_for_text, section_for_text
from runtime.memory.files import instruction_context_messages, load_instruction_files
from runtime.memory.mcp_client import MemoryMcpClient, parse_json_text
from runtime.memory.policy import is_safe_to_store, redact_for_memory
from utils.settings import Settings


class MemoryService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = MemoryMcpClient(settings)

    async def status(self) -> dict[str, Any]:
        availability = self.client.availability()
        files = load_instruction_files(self.settings)
        return {
            "enabled": bool(self.settings.MEMORY_ENABLED),
            "autoSaveEnabled": bool(self.settings.MEMORY_AUTO_SAVE_ENABLED),
            "provider": self.settings.MEMORY_PROVIDER,
            "ready": bool(self.settings.MEMORY_ENABLED and availability["available"]),
            "commandAvailable": bool(availability["available"]),
            "command": availability["command"],
            "commandArgs": availability["args"],
            "error": availability["error"],
            "path": str(Path(self.settings.MEMORY_PATH).expanduser()),
            "contextMaxChars": self.settings.MEMORY_CONTEXT_MAX_CHARS,
            "factMaxChars": self.settings.MEMORY_FACT_MAX_CHARS,
            "instructionFiles": [
                {
                    "label": item.label,
                    "path": str(item.path),
                    "exists": item.exists,
                    "chars": len(item.content),
                    "clipped": item.clipped,
                }
                for item in files
            ],
        }

    async def show(self, *, max_chars: int | None = None, format: str = "compact") -> str:
        if not self.settings.MEMORY_ENABLED:
            return "Memory is off."
        return await self.client.call_tool(
            "memory_context",
            {
                "format": format,
                "maxChars": max_chars or self.settings.MEMORY_CONTEXT_MAX_CHARS,
            },
        )

    async def remember(self, text: str, *, section: str | None = None) -> str:
        if not self.settings.MEMORY_ENABLED:
            return "Memory is off. Run `eyra memory on` or `/memory on` first."
        if not is_safe_to_store(text):
            return "I did not save that because it looks too large, raw, or sensitive."
        redacted = redact_for_memory(text)
        value = compact_text(redacted, max(40, int(self.settings.MEMORY_FACT_MAX_CHARS)))
        if not value:
            return "I did not save that because there was no durable fact to store."
        target_section = section or section_for_text(value)
        key = key_for_text(value)
        return await self.client.call_tool(
            "memory",
            {
                "command": "upsert",
                "section": target_section,
                "key": key,
                "value": value,
                "source": "eyra",
            },
        )

    async def forget(self, query: str) -> str:
        if not self.settings.MEMORY_ENABLED:
            return "Memory is off."
        query = query.strip()
        if not query:
            return "Tell me what memory to forget."
        try:
            raw = await self.client.call_tool("memory", {"command": "view", "format": "json"})
            data = parse_json_text(raw)
            match = _find_memory_match(data, query)
        except Exception as exc:
            return f"Could not inspect memory: {exc}"
        if match is None:
            return f"No matching memory found for: {query}"
        section, line, rendered = match
        result = await self.client.call_tool("memory", {"command": "remove", "section": section, "line": line})
        return f"{result}\nForgot: {rendered}"

    async def context_messages(self) -> list[dict[str, str]]:
        messages = instruction_context_messages(self.settings)
        messages.extend(await self.memory_context_messages())
        return messages

    async def memory_context_messages(self) -> list[dict[str, str]]:
        if not self.settings.MEMORY_ENABLED or self.settings.USE_MOCK_CLIENT:
            return []
        if not self.client.availability()["available"]:
            return []
        try:
            text = await self.show(max_chars=self.settings.MEMORY_CONTEXT_MAX_CHARS, format="compact")
        except Exception:
            return []
        if text and not text.startswith("No memories stored yet"):
            return [{
                "role": "system",
                "content": (
                    "Compact local memory facts from mcp-prose-memory. Use them as helpful background only; "
                    "do not reveal private facts unless the user asks, and do not store new memory unless it is compact and durable.\n"
                    f"{text}"
                ),
            }]
        return []

    async def handle_natural_memory_request(self, text: str) -> str | None:
        lowered = text.lower().strip()
        if re.fullmatch(r"(what do you remember|show memory|memory status|what is in memory)\??", lowered):
            status = await self.status()
            summary = await self.show()
            ready = "ready" if status["ready"] else "needs setup" if status["enabled"] else "off"
            return f"Memory is {ready}.\n\n{summary}"
        remember_match = re.match(r"^(?:please\s+)?remember(?: that| to)? (?P<fact>.+)$", text.strip(), re.I)
        if remember_match:
            return await self.remember(remember_match.group("fact"))
        forget_match = re.match(r"^(?:please\s+)?forget (?P<fact>.+)$", text.strip(), re.I)
        if forget_match:
            return await self.forget(forget_match.group("fact"))
        return None

    async def maybe_auto_remember(self, text: str) -> None:
        if (
            not self.settings.MEMORY_ENABLED
            or not self.settings.MEMORY_AUTO_SAVE_ENABLED
            or self.settings.USE_MOCK_CLIENT
        ):
            return
        stripped = text.strip()
        if not is_safe_to_store(stripped):
            return
        if re.match(r"^(?:please\s+)?remember(?: that| to)? ", stripped, re.I):
            return
        if not re.search(
            r"\b(i prefer|i like|i dislike|my preference|call me|my name is|i work|i live|always|never|do not|don't)\b",
            stripped,
            re.I,
        ):
            return
        try:
            await self.remember(stripped)
        except Exception:
            return


async def memory_context_messages(settings: Settings) -> list[dict[str, str]]:
    return await MemoryService(settings).context_messages()


def _find_memory_match(data: dict[str, Any], query: str) -> tuple[str, int, str] | None:
    needle = query.lower()
    sections = data.get("sections", {})
    for section, facts in sections.items():
        if not isinstance(facts, list):
            continue
        for index, fact in enumerate(facts, start=1):
            rendered = _render_fact(fact)
            if needle in rendered.lower():
                return str(section), index, rendered
    return None


def _render_fact(fact: Any) -> str:
    if isinstance(fact, str):
        return fact
    if isinstance(fact, dict):
        key = str(fact.get("key", "")).strip()
        value = str(fact.get("value", "")).strip()
        return f"{key}={value}" if key else value
    return str(fact)
