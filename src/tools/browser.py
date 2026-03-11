"""Browser tools — web browsing via Playwright for the model's tool-calling interface."""

import asyncio
import base64
import logging
import re
from urllib.parse import quote_plus, urlparse

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Content selectors, ordered by specificity. First match wins.
_CONTENT_SELECTORS = ["article", "main", "[role='main']", "#content", ".content"]


def _get_playwright():
    try:
        from playwright.async_api import async_playwright
        return async_playwright
    except ImportError:
        raise RuntimeError("Playwright is not installed. Run: uv add playwright && uv run playwright install chromium")


class BrowserSession:
    """Persistent headless browser, lazily initialized, reused across tool calls."""

    def __init__(self):
        self._cm = None  # context manager from async_playwright()
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()

    async def page(self):
        async with self._lock:
            if self._page and not self._page.is_closed():
                return self._page
            self._cm = _get_playwright()()
            pw = await self._cm.start()
            self._browser = await pw.chromium.launch(headless=True)
            ctx = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            self._page = await ctx.new_page()
            return self._page

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._cm:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
        self._page = None


async def _extract_text(page, max_chars: int = 4000) -> str:
    """Extract page text, preferring main content area over full body."""
    for sel in _CONTENT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                raw = await loc.inner_text(timeout=2000)
                if len(raw.strip()) > 100:
                    return _clean(raw, max_chars)
        except Exception:
            continue
    raw = await page.inner_text("body")
    return _clean(raw, max_chars)


def _clean(raw: str, max_chars: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", raw.strip())
    text = re.sub(r"[ \t]{2,}", " ", text)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated, {len(text)} chars total)"
    return text


def _page_header(page, title: str | None = None) -> str:
    t = title or ""
    return f"Page: {t}\nURL: {page.url}\n\n" if t else f"URL: {page.url}\n\n"


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web using Brave Search and return the results as text. "
        "Call this when the user asks to look something up, find information online, or research a topic. "
        'Example: {"query": "weather in Tokyo"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search terms to look up. Example: 'best restaurants in Paris'.",
            },
        },
        "required": ["query"],
    }
    costly = True

    def __init__(self, session: BrowserSession | None = None):
        self._session = session or BrowserSession()

    async def execute(self, query: str = "", **_) -> ToolResult:
        if not query:
            return ToolResult(content="Missing 'query'.")
        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            await page.goto(
                f"https://search.brave.com/search?q={quote_plus(query)}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_timeout(1200)
            text = await _extract_text(page)
            return ToolResult(content=f"Search: {query}\n{page.url}\n\n{text}")
        except Exception as e:
            logger.error("web_search failed: %s", e, exc_info=True)
            return ToolResult(content=f"Browser error: {e}")


class OpenUrlTool(BaseTool):
    name = "open_url"
    description = (
        "Open a URL in the browser and return the page content as text. "
        "Call this when the user provides a specific URL to visit or when you need to read a known page. "
        'Example: {"url": "https://example.com"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to navigate to. Example: 'https://example.com'. Bare domains like 'example.com' are also accepted.",
            },
        },
        "required": ["url"],
    }
    costly = True

    def __init__(self, session: BrowserSession | None = None):
        self._session = session or BrowserSession()

    async def execute(self, url: str = "", **_) -> ToolResult:
        if not url:
            return ToolResult(content="Missing 'url'.")
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https", ""):
            return ToolResult(content=f"Only http/https URLs are allowed, got: {scheme}")
        if not scheme:
            url = "https://" + url
        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(800)
            title = await page.title()
            text = await _extract_text(page)
            return ToolResult(content=_page_header(page, title) + text)
        except Exception as e:
            logger.error("open_url failed: %s", e, exc_info=True)
            return ToolResult(content=f"Browser error: {e}")


class ClickElementTool(BaseTool):
    name = "click_element"
    description = (
        "Click a link or button on the current browser page and return the resulting page content. "
        "Call this when you need to follow a link or press a button after loading a page. "
        'Example: {"selector": "Learn more"}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Link text or CSS selector of the element to click. Use visible link text (e.g. 'Learn more') or a CSS selector (e.g. 'button.submit').",
            },
        },
        "required": ["selector"],
    }
    costly = True

    def __init__(self, session: BrowserSession | None = None):
        self._session = session or BrowserSession()

    async def execute(self, selector: str = "", **_) -> ToolResult:
        if not selector:
            return ToolResult(content="Missing 'selector'.")
        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            # CSS selector first, then link by name, then any text match
            clicked = False
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5000)
                    clicked = True
            except Exception:
                pass
            if not clicked:
                link = page.get_by_role("link", name=selector)
                if await link.count() > 0:
                    await link.first.click(timeout=5000)
                else:
                    await page.get_by_text(selector, exact=False).first.click(timeout=5000)
            await page.wait_for_timeout(800)
            title = await page.title()
            text = await _extract_text(page, max_chars=2000)
            return ToolResult(content=_page_header(page, title) + text)
        except Exception as e:
            logger.error("click_element failed: %s", e, exc_info=True)
            return ToolResult(content=f"Browser error: {e}")


class PageScreenshotTool(BaseTool):
    name = "page_screenshot"
    description = (
        "Take a screenshot of the current browser page and return it as an image. "
        "Call this when the user wants to see the page visually or when text extraction is insufficient. "
        "No parameters required."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    costly = True

    def __init__(self, session: BrowserSession | None = None):
        self._session = session or BrowserSession()

    async def execute(self, **_) -> ToolResult:
        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            if page.url == "about:blank":
                return ToolResult(content="No page loaded. Use open_url or web_search first.")
            img = await page.screenshot(full_page=False)
            title = await page.title()
            return ToolResult(
                content=f"Screenshot of: {title} ({page.url})",
                image_base64=base64.b64encode(img).decode("ascii"),
            )
        except Exception as e:
            logger.error("page_screenshot failed: %s", e, exc_info=True)
            return ToolResult(content=f"Browser error: {e}")
