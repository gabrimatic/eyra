"""Tool registry for managing and dispatching tool calls."""

import json
import logging

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


def _summarize_arguments_for_log(arguments: str) -> str:
    """Summarize tool arguments without logging user-provided values."""
    if not arguments.strip():
        return "no arguments"
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return "invalid JSON arguments"
    if isinstance(parsed, dict):
        keys = ", ".join(str(key) for key in parsed)
        return f"argument keys: {keys}" if keys else "no arguments"
    return f"{type(parsed).__name__} arguments"


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return a registered tool by name."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return registered tool names."""
        return list(self._tools)

    def metadata_by_name(self) -> dict:
        """Return structured metadata for each registered tool."""
        from runtime.routing.tool_policy import metadata_for_tool

        return {name: metadata_for_tool(tool) for name, tool in self._tools.items()}

    def to_openai_tools(
        self,
        include_costly: bool = True,
        allowed_names: set[str] | frozenset[str] | None = None,
    ) -> list[dict]:
        """Return tools in OpenAI function-calling format.
        If include_costly is False, only lightweight tools are returned."""
        allowed_set = set(allowed_names) if allowed_names is not None else None
        return [
            tool.to_openai_tool() for tool in self._tools.values()
            if (allowed_set is None or tool.name in allowed_set) and (include_costly or not tool.costly)
        ]

    async def execute(self, name: str, arguments: str) -> ToolResult:
        """Execute a tool by name with JSON-encoded arguments."""
        logger.info("Tool call: %s (%s)", name, _summarize_arguments_for_log(arguments or ""))
        tool = self._tools.get(name)
        if not tool:
            logger.warning("Unknown tool requested: %s", name)
            return ToolResult(content=f"Unknown tool: {name}")

        try:
            kwargs = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(content=f"Invalid JSON arguments for tool '{name}'.")

        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e, exc_info=True)
            return ToolResult(content=f"Tool error: {e}")
