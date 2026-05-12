"""Tests for the browser tools."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.approval import ApprovalManager
from tools.browser import (
    BrowserSession,
    ClickElementTool,
    DownloadFileTool,
    FillFormFieldTool,
    OpenUrlTool,
    PageScreenshotTool,
    UploadFileTool,
    WebSearchTool,
)


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

    def test_fill_form_field_schema(self):
        tool = FillFormFieldTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "fill_form_field"
        assert "selector" in schema["function"]["parameters"]["properties"]
        assert "value" in schema["function"]["parameters"]["properties"]
        assert FillFormFieldTool.costly is True

    def test_page_screenshot_schema(self):
        tool = PageScreenshotTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "page_screenshot"
        assert PageScreenshotTool.costly is True

    def test_download_file_schema(self):
        tool = DownloadFileTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "download_file"
        props = schema["function"]["parameters"]["properties"]
        assert "selector" in props
        assert "destination" in props
        assert DownloadFileTool.costly is True

    def test_upload_file_schema(self):
        tool = UploadFileTool()
        schema = tool.to_openai_tool()
        assert schema["function"]["name"] == "upload_file"
        props = schema["function"]["parameters"]["properties"]
        assert "selector" in props
        assert "path" in props
        assert UploadFileTool.costly is True


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

    def test_fill_form_field_missing_selector_or_value(self):
        async def run():
            session = _make_session()
            tool = FillFormFieldTool(session=session)
            try:
                assert "Missing" in (await tool.execute(value="hello")).content
                assert "Missing" in (await tool.execute(selector="#name")).content
            finally:
                await session.close()
        _run(run())

    def test_download_file_missing_selector_or_destination(self):
        async def run():
            session = _make_session()
            tool = DownloadFileTool(session=session)
            try:
                assert "Missing" in (await tool.execute(destination="~/Downloads/file.txt")).content
                assert "Missing" in (await tool.execute(selector="Download")).content
            finally:
                await session.close()
        _run(run())

    def test_upload_file_missing_selector_or_path(self):
        async def run():
            session = _make_session()
            tool = UploadFileTool(session=session)
            try:
                assert "Missing" in (await tool.execute(path="~/Documents/file.txt")).content
                assert "Missing" in (await tool.execute(selector="#file")).content
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

    def test_fill_form_field_without_submit(self):
        async def run():
            session = _make_session()
            tool = FillFormFieldTool(session=session)
            try:
                page = await session.page()
                await page.set_content(
                    "<html><body><form><input id='name' name='name'><button type='submit'>Send</button></form></body></html>"
                )
                r = await tool.execute(selector="#name", value="Soroush")
                value = await page.locator("#name").input_value()
                assert "Filled #name" in r.content
                assert value == "Soroush"
                assert "submitted" not in r.content.lower()
            finally:
                await session.close()
        _run(run())


class TestDownloadFileTool:
    def test_download_requires_server_side_approval(self, tmp_path):
        async def run():
            manager = ApprovalManager(ttl_seconds=60)
            tool = DownloadFileTool(
                session=FakeDownloadSession(),
                allowed_roots=(tmp_path,),
                default_path=tmp_path,
                approval_manager=manager,
            )
            pending = await tool.execute(selector="Download", destination=str(tmp_path / "file.txt"))
            approval_id = pending.content.split("/approve ", 1)[1].split()[0]
            assert manager.approve(approval_id) is True
            result = await tool.execute(
                selector="Download",
                destination=str(tmp_path / "file.txt"),
                approval_id=approval_id,
            )
            return result

        result = _run(run())

        assert "Downloaded:" in result.content
        assert (tmp_path / "file.txt").read_text() == "downloaded"

    def test_download_refuses_destination_outside_sandbox(self, tmp_path):
        async def run():
            tool = DownloadFileTool(
                session=FakeDownloadSession(),
                allowed_roots=(tmp_path,),
                default_path=tmp_path,
            )
            return await tool.execute(selector="Download", destination="/etc/file.txt")

        result = _run(run())

        assert "Access denied" in result.content


class TestUploadFileTool:
    def test_upload_requires_server_side_approval(self, tmp_path):
        async def run():
            source = tmp_path / "upload.txt"
            source.write_text("upload me")
            manager = ApprovalManager(ttl_seconds=60)
            page = FakeUploadPage()
            tool = UploadFileTool(
                session=FakeUploadSession(page),
                allowed_roots=(tmp_path,),
                default_path=tmp_path,
                approval_manager=manager,
            )
            pending = await tool.execute(selector="#file", path=str(source))
            approval_id = pending.content.split("/approve ", 1)[1].split()[0]
            assert manager.approve(approval_id) is True
            result = await tool.execute(selector="#file", path=str(source), approval_id=approval_id)
            return result, page

        result, page = _run(run())

        assert "Uploaded:" in result.content
        assert page.uploaded_path.endswith("upload.txt")

    def test_upload_refuses_path_outside_sandbox(self, tmp_path):
        async def run():
            tool = UploadFileTool(
                session=FakeUploadSession(FakeUploadPage()),
                allowed_roots=(tmp_path,),
                default_path=tmp_path,
            )
            return await tool.execute(selector="#file", path="/etc/passwd")

        result = _run(run())

        assert "Access denied" in result.content


class FakeLocator:
    async def count(self):
        return 1

    @property
    def first(self):
        return self

    async def click(self, **_):
        return None


class FakeDownload:
    suggested_filename = "file.txt"

    async def save_as(self, path):
        with open(path, "w") as handle:
            handle.write("downloaded")


class FakeDownloadContext:
    async def __aenter__(self):
        class Holder:
            value = FakeDownload()

        return Holder()

    async def __aexit__(self, *_):
        return False


class FakeDownloadPage:
    url = "https://example.test/download"

    def locator(self, _selector):
        return FakeLocator()

    def expect_download(self, **_):
        return FakeDownloadContext()


class FakeDownloadSession:
    async def page(self):
        return FakeDownloadPage()


class FakeUploadLocator:
    def __init__(self, page):
        self._page = page

    async def count(self):
        return 1

    @property
    def first(self):
        return self

    async def set_input_files(self, path, **_):
        self._page.uploaded_path = path


class FakeUploadPage:
    def __init__(self):
        self.uploaded_path = ""

    def locator(self, _selector):
        return FakeUploadLocator(self)


class FakeUploadSession:
    def __init__(self, page):
        self._page = page

    async def page(self):
        return self._page
