"""Base tool interface and result type."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from runtime.routing.types import RiskTier, ToolMetadata


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
    tool_metadata: ToolMetadata | None = None

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given arguments."""
        ...

    @property
    def metadata(self) -> ToolMetadata:
        """Structured policy metadata. Subclasses may override with tool_metadata."""
        if self.tool_metadata is not None:
            return self.tool_metadata
        return ToolMetadata(
            name=self.name,
            capabilities=frozenset(),
            risk_tier=RiskTier.PRIVATE_READ if self.costly else RiskTier.LOW_READ_ONLY,
            latency_cost="high" if self.costly else "low",
            reads_private_data=self.costly,
        )

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
