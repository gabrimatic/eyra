"""Weather tool — fetches current weather via wttr.in (no API key needed)."""

import asyncio
import logging

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class WeatherTool(BaseTool):
    name = "get_weather"
    description = (
        "Fetches the current weather conditions for a given location. "
        "Call this when the user asks about the weather, temperature, or forecast. "
        "Returns temperature, humidity, wind, and conditions.\n"
        "Example: {\"location\": \"Tokyo\"}\n"
        "Example (auto-detect): {}"
    )
    parameters = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place name. Examples: 'London', 'New York', 'Tehran'. Omit to auto-detect the user's location.",
            },
        },
        "required": [],
    }

    async def execute(self, location: str = "", **kwargs) -> ToolResult:
        from urllib.parse import quote

        query = quote(location.strip()) if location else ""
        url = f"https://wttr.in/{query}?format=%l:+%C,+%t,+feels+like+%f,+humidity+%h,+wind+%w"

        def _fetch() -> str:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.read().decode("utf-8").strip()
            except urllib.error.URLError as e:
                return f"Could not fetch weather: {e.reason}"
            except Exception as e:
                return f"Weather lookup failed: {e}"

        result = await asyncio.to_thread(_fetch)
        return ToolResult(content=result)
