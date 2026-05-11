"""Tests for the optional weather tool."""

import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.weather import WeatherTool


def _run(coro):
    return asyncio.run(coro)


class TestWeatherTool:
    def test_location_is_required_to_avoid_ip_geolocation(self):
        async def run():
            with patch("urllib.request.urlopen") as urlopen:
                result = await WeatherTool().execute()
            assert "location" in result.content.lower()
            urlopen.assert_not_called()

        _run(run())
