"""Browser tools — web browsing via Playwright for the model's tool-calling interface."""

import asyncio
import base64
import logging
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from tools.approval import GLOBAL_APPROVAL_MANAGER, ApprovalManager, approval_required_message
from tools.base import BaseTool, ToolResult
from tools.filesystem import _DEFAULT_ALLOWED_ROOTS, _resolve

logger = logging.getLogger(__name__)

# Content selectors, ordered by specificity. First match wins.
_CONTENT_SELECTORS = ["article", "main", "[role='main']", "#content", ".content"]


def _get_playwright():
    try:
        from playwright.async_api import async_playwright
        return async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Run: uv add playwright && uv run playwright install chromium"
        )


async def _install_chromium() -> None:
    """Install Playwright's Chromium binary. Raises RuntimeError on failure."""
    logger.info("Playwright Chromium not found, installing automatically...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "playwright install", stderr=stderr)
        logger.info("Playwright Chromium installed successfully.")
    except (subprocess.CalledProcessError, asyncio.TimeoutError, OSError) as e:
        raise RuntimeError(
            f"Failed to install Playwright Chromium: {e}\n"
            "Run manually: uv run playwright install chromium"
        ) from e


class BrowserSession:
    """Persistent headless browser, lazily initialized, reused across tool calls."""

    def __init__(self):
        self._cm = None  # context manager from async_playwright()
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _launch_browser(self):
        """Launch Chromium, auto-installing the binary on first run if needed."""
        self._cm = _get_playwright()()
        pw = await self._cm.start()
        try:
            return await pw.chromium.launch(headless=True)
        except Exception as e:
            err_msg = str(e)
            if "Executable doesn't exist" not in err_msg and "playwright install" not in err_msg:
                raise
        # Binary missing — install and retry once.
        await self._cm.__aexit__(None, None, None)
        self._cm = None
        await _install_chromium()
        self._cm = _get_playwright()()
        pw = await self._cm.start()
        return await pw.chromium.launch(headless=True)

    async def page(self):
        async with self._lock:
            if self._page and not self._page.is_closed():
                return self._page
            # Close any existing browser before launching a new one
            if self._browser:
                await self.close()
            if self._cm:
                try:
                    await self._cm.__aexit__(None, None, None)
                except Exception:
                    pass
                self._cm = None
            self._browser = await self._launch_browser()
            ctx = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            self._context = ctx
            self._page = await self._context.new_page()
            return self._page

    async def close(self):
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        finally:
            self._page = None
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        finally:
            self._context = None
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None
        try:
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        except Exception:
            pass
        finally:
            self._cm = None


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
            try:
                await page.wait_for_selector("#results, .snippet, .result", timeout=5000)
            except Exception:
                pass
            text = await _extract_text(page)
            return ToolResult(content=f"Search: {query}\n{page.url}\n\n{text}")
        except Exception as e:
            logger.error("web_search failed: %s", e, exc_info=True)
            return ToolResult(content=f"Web search failed for '{query}'. The page may have timed out or been blocked.")


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
        # Strip protocol-relative prefix
        if url.startswith("//"):
            url = "https:" + url
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
            return ToolResult(content=f"Failed to open {url}. The page may have timed out or been unreachable.")


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

    async def _click(self, locator) -> None:
        """Click without folding slow navigation waits into element-click failure."""
        await locator.first.click(timeout=5000, no_wait_after=True)

    async def _page_content_after_click(self, page) -> ToolResult:
        for _ in range(10):
            try:
                title = await page.title()
                text = await _extract_text(page, max_chars=2000)
                return ToolResult(content=_page_header(page, title) + text)
            except Exception:
                await page.wait_for_timeout(500)
        title = await page.title()
        text = await _extract_text(page, max_chars=2000)
        return ToolResult(content=_page_header(page, title) + text)

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
                    await self._click(loc)
                    clicked = True
            except Exception:
                pass
            if not clicked:
                link = page.get_by_role("link", name=selector)
                if await link.count() > 0:
                    await self._click(link)
                else:
                    await self._click(page.get_by_text(selector, exact=False))
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(800)
            return await self._page_content_after_click(page)
        except Exception as e:
            logger.error("click_element failed: %s", e, exc_info=True)
            return ToolResult(content=f"Could not click '{selector}': element not found or not clickable.")


class FillFormFieldTool(BaseTool):
    name = "fill_form_field"
    description = (
        "Fill a text field, textarea, or editable element on the current browser page without submitting the form. "
        "Call this when the user asks to enter text but not submit."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector, label text, placeholder text, or accessible name for the field.",
            },
            "value": {"type": "string", "description": "Text to place in the field."},
        },
        "required": ["selector", "value"],
    }
    costly = True

    def __init__(self, session: BrowserSession | None = None):
        self._session = session or BrowserSession()

    async def execute(self, selector: str = "", value: str = "", **_) -> ToolResult:
        if not selector:
            return ToolResult(content="Missing 'selector'.")
        if value == "":
            return ToolResult(content="Missing 'value'.")
        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                locator = page.get_by_label(selector).first
            if await locator.count() == 0:
                locator = page.get_by_placeholder(selector).first
            if await locator.count() == 0:
                locator = page.get_by_role("textbox", name=selector).first
            if await locator.count() == 0:
                return ToolResult(content=f"Could not find form field: {selector}")
            await locator.fill(value, timeout=5000)
            return ToolResult(content=f"Filled {selector} ({len(value)} characters). No submit action was taken.")
        except Exception as e:
            logger.error("fill_form_field failed: %s", e, exc_info=True)
            return ToolResult(content=f"Could not fill '{selector}'. The field may not be editable.")


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
            return ToolResult(content="Failed to take page screenshot. The page may have crashed or timed out.")


class DownloadFileTool(BaseTool):
    name = "download_file"
    description = (
        "Click a download link or button on the current browser page and save the downloaded file to an allowed "
        "local filesystem path. Requires server-side user approval for the exact selector and destination."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector or visible text for the download link/button.",
            },
            "destination": {
                "type": "string",
                "description": "Allowed local destination file path. Relative paths resolve under FILESYSTEM_DEFAULT_PATH.",
            },
            "approval_id": {"type": "string", "description": "Server-issued approval id for this exact download."},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve downloads."},
        },
        "required": ["selector", "destination"],
    }
    costly = True

    def __init__(
        self,
        session: BrowserSession | None = None,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
    ):
        self._session = session or BrowserSession()
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        selector: str = "",
        destination: str = "",
        approval_id: str = "",
        confirmed: bool = False,
        **_,
    ) -> ToolResult:
        if not selector:
            return ToolResult(content="Missing 'selector'.")
        if not destination:
            return ToolResult(content="Missing 'destination'.")
        try:
            dest = _resolve(destination, self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))

        details = {"selector": selector, "destination": str(dest)}
        if not approval_id or not self._approvals.consume(approval_id, self.name, "browser download", details):
            approval = self._approvals.request(self.name, "browser download", details)
            return ToolResult(content=approval_required_message(approval))

        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                locator = page.get_by_role("link", name=selector).first
            if await locator.count() == 0:
                locator = page.get_by_text(selector, exact=False).first
            if await locator.count() == 0:
                return ToolResult(content=f"Could not find download target: {selector}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with page.expect_download(timeout=15000) as download_info:
                await locator.click(timeout=5000, no_wait_after=True)
            download_value = download_info.value
            download = await download_value if hasattr(download_value, "__await__") else download_value
            await download.save_as(str(dest))
            suggested = getattr(download, "suggested_filename", "")
            suffix = f" (suggested name: {suggested})" if suggested else ""
            return ToolResult(content=f"Downloaded: {dest}{suffix}")
        except Exception as e:
            logger.error("download_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Could not download from '{selector}'. The page may not have started a download.")


class UploadFileTool(BaseTool):
    name = "upload_file"
    description = (
        "Attach a local file to a file input on the current browser page. Requires server-side user approval "
        "for the exact selector and sandboxed file path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the file input."},
            "path": {
                "type": "string",
                "description": "Allowed local file path to upload. Relative paths resolve under FILESYSTEM_DEFAULT_PATH.",
            },
            "approval_id": {"type": "string", "description": "Server-issued approval id for this exact upload."},
            "confirmed": {"type": "boolean", "description": "Ignored. Models cannot approve uploads."},
        },
        "required": ["selector", "path"],
    }
    costly = True

    def __init__(
        self,
        session: BrowserSession | None = None,
        allowed_roots: tuple[Path, ...] = (),
        default_path: Path | None = None,
        approval_manager: ApprovalManager | None = None,
    ):
        self._session = session or BrowserSession()
        self._roots = allowed_roots or _DEFAULT_ALLOWED_ROOTS
        self._default_path = default_path.expanduser().resolve() if default_path else None
        self._approvals = approval_manager or GLOBAL_APPROVAL_MANAGER

    async def execute(
        self,
        selector: str = "",
        path: str = "",
        approval_id: str = "",
        confirmed: bool = False,
        **_,
    ) -> ToolResult:
        if not selector:
            return ToolResult(content="Missing 'selector'.")
        if not path:
            return ToolResult(content="Missing 'path'.")
        try:
            source = _resolve(path, self._roots, self._default_path)
        except (PermissionError, ValueError) as e:
            return ToolResult(content=str(e))
        if not source.is_file():
            return ToolResult(content=f"Not a file: {source}")

        details = {"selector": selector, "path": str(source)}
        if not approval_id or not self._approvals.consume(approval_id, self.name, "browser file upload", details):
            approval = self._approvals.request(self.name, "browser file upload", details)
            return ToolResult(content=approval_required_message(approval))

        try:
            page = await self._session.page()
        except RuntimeError as e:
            return ToolResult(content=str(e))
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                return ToolResult(content=f"Could not find upload field: {selector}")
            await locator.set_input_files(str(source), timeout=5000)
            return ToolResult(content=f"Uploaded: {source} into {selector}. No submit action was taken.")
        except Exception as e:
            logger.error("upload_file failed: %s", e, exc_info=True)
            return ToolResult(content=f"Could not upload '{source}'. The field may not accept files.")
