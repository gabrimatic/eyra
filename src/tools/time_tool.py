"""Time tool — returns the current date and time."""

from datetime import datetime

from tools.base import BaseTool, ToolResult


class TimeTool(BaseTool):
    name = "get_current_time"
    description = (
        "Returns the current local date and time. "
        "Call this when the user asks what time it is, what today's date is, or what day of the week it is. "
        "Takes no parameters."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> ToolResult:
        now = datetime.now()
        return ToolResult(content=now.strftime("%A, %B %d, %Y at %I:%M %p"))
