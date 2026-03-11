"""Tool registry for managing and dispatching tool calls."""

import json
import logging

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def to_openai_tools(self, include_costly: bool = True) -> list[dict]:
        """Return tools in OpenAI function-calling format.
        If include_costly is False, only lightweight tools are returned."""
        return [
            tool.to_openai_tool() for tool in self._tools.values()
            if include_costly or not tool.costly
        ]

    async def execute(self, name: str, arguments: str) -> ToolResult:
        """Execute a tool by name with JSON-encoded arguments."""
        logger.info("Tool call: %s(%s)", name, arguments[:200] if arguments else "")
        tool = self._tools.get(name)
        if not tool:
            logger.warning("Unknown tool requested: %s", name)
            return ToolResult(content=f"Unknown tool: {name}")

        try:
            kwargs = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            kwargs = {}

        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e, exc_info=True)
            return ToolResult(content=f"Tool error: {e}")
