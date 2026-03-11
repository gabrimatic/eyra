"""Tests for the browser tools."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.browser import BrowserSession, ClickElementTool, OpenUrlTool, PageScreenshotTool, WebSearchTool


def _run(coro):
    return asyncio.run(coro)


def _make_session():
    return BrowserSession()


class TestToolSchema:
    def test_web_search_schema(self):
        tool = WebSearchTool()
        schema = tool.to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "web_search"
        assert "query" in schema["function"]["parameters"]["properties"]
        assert WebSearchTool.costly is True

    def test_open_url_schema(self):
        tool = OpenUrlTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "open_url"
        assert "url" in schema["function"]["parameters"]["properties"]
        assert OpenUrlTool.costly is True

    def test_click_element_schema(self):
        tool = ClickElementTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "click_element"
        assert "selector" in schema["function"]["parameters"]["properties"]
        assert ClickElementTool.costly is True

    def test_page_screenshot_schema(self):
        tool = PageScreenshotTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "page_screenshot"
        assert PageScreenshotTool.costly is True


class TestToolValidation:
    def test_web_search_missing_query(self):
        async def run():
            session = _make_session()
            tool = WebSearchTool(session=session)
            try:
                r = await tool.execute()
                assert "Missing" in r.content
            finally:
                await session.close()
        _run(run())

    def test_open_url_missing_url(self):
        async def run():
            session = _make_session()
            tool = OpenUrlTool(session=session)
            try:
                r = await tool.execute()
                assert "Missing" in r.content
            finally:
                await session.close()
        _run(run())

    def test_open_url_bad_scheme(self):
        async def run():
            session = _make_session()
            tool = OpenUrlTool(session=session)
            try:
                r = await tool.execute(url="ftp://example.com")
                assert "http" in r.content
            finally:
                await session.close()
        _run(run())

    def test_click_element_missing_selector(self):
        async def run():
            session = _make_session()
            tool = ClickElementTool(session=session)
            try:
                r = await tool.execute()
                assert "Missing" in r.content
            finally:
                await session.close()
        _run(run())

    def test_page_screenshot_blank(self):
        async def run():
            session = _make_session()
            tool = PageScreenshotTool(session=session)
            try:
                r = await tool.execute()
                assert "No page loaded" in r.content
            finally:
                await session.close()
        _run(run())


class TestToolNetwork:
    def test_open_url(self):
        async def run():
            session = _make_session()
            tool = OpenUrlTool(session=session)
            try:
                r = await tool.execute(url="https://example.com")
                assert "Example Domain" in r.content
                assert r.image_base64 is None
            finally:
                await session.close()
        _run(run())

    def test_open_url_bare_domain(self):
        async def run():
            session = _make_session()
            tool = OpenUrlTool(session=session)
            try:
                r = await tool.execute(url="example.com")
                assert "Example Domain" in r.content
            finally:
                await session.close()
        _run(run())

    def test_page_screenshot_after_load(self):
        async def run():
            session = _make_session()
            open_tool = OpenUrlTool(session=session)
            shot_tool = PageScreenshotTool(session=session)
            try:
                await open_tool.execute(url="https://example.com")
                r = await shot_tool.execute()
                assert r.image_base64 is not None
                assert len(r.image_base64) > 100
            finally:
                await session.close()
        _run(run())

    def test_click_by_text(self):
        async def run():
            session = _make_session()
            open_tool = OpenUrlTool(session=session)
            click_tool = ClickElementTool(session=session)
            try:
                await open_tool.execute(url="https://example.com")
                r = await click_tool.execute(selector="Learn more")
                assert "iana" in r.content.lower()
            finally:
                await session.close()
        _run(run())
