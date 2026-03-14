"""Base tool interface and result type."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    """Result returned by a tool execution."""

    content: str
    image_base64: str | None = None


class BaseTool(ABC):
    """Abstract base for all tools."""

    name: str
    description: str
    parameters: dict  # JSON Schema for function parameters
    costly: bool = False  # If True, only available on Complex tier (e.g. screenshot sends an image)

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    def to_openai_tool(self) -> dict:
        """Convert to OpenAI function-calling tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
