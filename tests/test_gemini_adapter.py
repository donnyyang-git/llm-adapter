from pathlib import Path
from typing import cast
from collections.abc import Callable

import pytest
from playwright.async_api import Page

from llm_adapter.adapters.gemini import GeminiAdapter


class FakeLocator:
    def __init__(
        self,
        count: int,
        visible: bool = True,
        editable: bool = True,
        text: str = "",
        html: str = "",
        screenshot_bytes: bytes | None = None,
        children: dict[str, "FakeLocator"] | None = None,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self._count = count
        self._visible = visible
        self._editable = editable
        self._text = text
        self._html = html
        self._screenshot_bytes = screenshot_bytes
        self._children = children or {}
        self._on_click = on_click
        self.first = self
        self.filled: list[str] = []
        self.pressed: list[str] = []

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def is_editable(self) -> bool:
        return self._editable

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def press(self, key: str) -> None:
        self.pressed.append(key)

    async def click(self) -> None:
        if self._on_click is not None:
            self._on_click()

    def nth(self, _: int) -> "FakeLocator":
        return self

    def locator(self, selector: str) -> "FakeLocator":
        return self._children.get(selector, FakeLocator(0))

    async def inner_text(self) -> str:
        return self._text

    async def inner_html(self) -> str:
        return self._html

    async def text_content(self) -> str:
        return self._text

    async def screenshot(self, **_: object) -> bytes:
        if self._screenshot_bytes is None:
            raise RuntimeError("No screenshot available")
        return self._screenshot_bytes


class FakePage:
    def __init__(self, url: str, locators: dict[str, FakeLocator], *, evaluations: dict[str, object] | None = None) -> None:
        self.url = url
        self.locators = locators
        self.evaluations = evaluations or {}

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.get(selector, FakeLocator(0))

    async def wait_for_timeout(self, _: int) -> None:
        return None

    async def evaluate(self, script: str):
        if "monaco?.editor" in script:
            return self.evaluations.get("monaco", [])
        return self.evaluations.get(script, None)


@pytest.mark.asyncio
async def test_inspect_reports_ready_for_editable_gemini_input() -> None:
    adapter = GeminiAdapter()
    page = FakePage(
        "https://gemini.google.com/app/abc",
        {adapter.INPUT_SELECTORS[0]: FakeLocator(1)},
    )

    status = await adapter.inspect(cast(Page, page))

    assert status.is_gemini is True
    assert status.ready is True


@pytest.mark.asyncio
async def test_inspect_rejects_non_gemini_page_without_querying_dom() -> None:
    adapter = GeminiAdapter()
    page = FakePage("https://example.com", {})

    status = await adapter.inspect(cast(Page, page))

    assert status.is_gemini is False
    assert status.ready is False


@pytest.mark.asyncio
async def test_inspect_reports_missing_input() -> None:
    adapter = GeminiAdapter()
    page = FakePage("https://gemini.google.com/app", {})

    status = await adapter.inspect(cast(Page, page))

    assert status.is_gemini is True
    assert status.ready is False
    assert "sign in" in (status.message or "")


@pytest.mark.asyncio
async def test_inspect_rejects_editable_anonymous_page() -> None:
    adapter = GeminiAdapter()
    page = FakePage(
        "https://gemini.google.com/app",
        {
            adapter.LOGIN_SELECTORS[0]: FakeLocator(1),
            adapter.INPUT_SELECTORS[0]: FakeLocator(1),
        },
    )

    status = await adapter.inspect(cast(Page, page))

    assert status.is_gemini is True
    assert status.ready is False
    assert "Sign in" in (status.message or "")


@pytest.mark.asyncio
async def test_send_question_fills_and_submits_exactly_once() -> None:
    adapter = GeminiAdapter()
    input_locator = FakeLocator(1)
    page = FakePage(
        "https://gemini.google.com/app",
        {adapter.INPUT_SELECTORS[0]: input_locator},
    )

    await adapter.send_question(cast(Page, page), "Only once")

    assert input_locator.filled == ["Only once"]
    assert input_locator.pressed == ["Enter"]


@pytest.mark.asyncio
async def test_extracts_new_response_as_text_and_markdown() -> None:
    adapter = GeminiAdapter()
    html = (Path(__file__).parent / "fixtures" / "gemini_response.html").read_text(
        encoding="utf-8"
    )
    content = FakeLocator(
        1,
        text='Result title\nA paragraph with documentation.\nFirst item\nprint("hello")',
        html=html,
    )
    response = FakeLocator(
        1,
        screenshot_bytes=b"png-bytes",
        children={adapter.RESPONSE_CONTENT_SELECTORS[0]: content},
    )
    page = FakePage(
        "https://gemini.google.com/app",
        {
            adapter.MODEL_MESSAGE_SELECTORS[0]: response,
            adapter.INPUT_SELECTORS[0]: FakeLocator(1),
        },
        evaluations={"monaco": []},
    )

    captured = await adapter.capture_latest_response(cast(Page, page), baseline_count=0)

    assert captured is not None
    assert captured.text.startswith("Result title")
    assert "## Result title" in captured.markdown
    assert "- First item" in captured.markdown
    assert '```\nprint("hello")\n```' in captured.markdown
    assert "| Name | Value |" in captured.markdown
    assert "> Quoted text" in captured.markdown
    assert "[documentation](https://example.com/docs)" in captured.markdown
    assert captured.screenshot_png == b"png-bytes"
    assert captured.artifact_code == ""
    assert len(captured.artifacts) == 1
    assert captured.artifacts[0].code == 'print("hello")'


@pytest.mark.asyncio
async def test_prefers_code_view_when_available() -> None:
    adapter = GeminiAdapter()
    code_block = FakeLocator(
        1,
        text="<html>\n<body>\n  <h1>Snake</h1>\n</body>\n</html>",
        html="<pre><code class=\"language-html\">&lt;html&gt;\n&lt;body&gt;\n  &lt;h1&gt;Snake&lt;/h1&gt;\n&lt;/body&gt;\n&lt;/html&gt;</code></pre>",
    )
    summary = FakeLocator(1, text="摘要", html="<p>摘要</p>")

    response = FakeLocator(
        1,
        children={
            'button:has-text("程式碼")': FakeLocator(1, on_click=lambda: response._children.__setitem__(adapter.RESPONSE_CONTENT_SELECTORS[0], code_block)),
            adapter.RESPONSE_CONTENT_SELECTORS[0]: summary,
        },
    )
    page = FakePage(
        "https://gemini.google.com/app",
        {
            adapter.MODEL_MESSAGE_SELECTORS[0]: response,
            adapter.INPUT_SELECTORS[0]: FakeLocator(1),
        },
        evaluations={
            "monaco": [
                {
                    "language": "html",
                    "value": "<!DOCTYPE html>\n<html><body><h1>Snake</h1><script>console.log('ok')</script></body></html>",
                }
            ]
        },
    )

    captured = await adapter.capture_latest_response(cast(Page, page), baseline_count=0)

    assert captured is not None
    assert "摘要" in captured.text
    assert captured.artifact_code.startswith("<!DOCTYPE html>")
    assert "console.log('ok')" in captured.artifact_code
    assert len(captured.artifacts) == 1
    assert captured.artifacts[0].preview_type == "html"


@pytest.mark.asyncio
async def test_collects_multiple_markdown_code_blocks_as_artifacts() -> None:
    adapter = GeminiAdapter()
    content = FakeLocator(
        1,
        text="Summary",
        html="<p>Summary</p><pre><code class=\"language-python\">print('a')</code></pre><pre><code class=\"language-sql\">select 1;</code></pre>",
    )
    response = FakeLocator(
        1,
        children={adapter.RESPONSE_CONTENT_SELECTORS[0]: content},
    )
    page = FakePage(
        "https://gemini.google.com/app",
        {
            adapter.MODEL_MESSAGE_SELECTORS[0]: response,
            adapter.INPUT_SELECTORS[0]: FakeLocator(1),
        },
        evaluations={"monaco": []},
    )

    captured = await adapter.capture_latest_response(cast(Page, page), baseline_count=0)

    assert captured is not None
    assert len(captured.artifacts) == 2
    assert captured.artifacts[0].language == "python"
    assert captured.artifacts[1].language == "sql"
    assert "Summary" in captured.artifacts[0].summary


@pytest.mark.asyncio
async def test_does_not_capture_preexisting_response() -> None:
    adapter = GeminiAdapter()
    page = FakePage(
        "https://gemini.google.com/app",
        {adapter.MODEL_MESSAGE_SELECTORS[0]: FakeLocator(1)},
    )

    assert await adapter.capture_latest_response(cast(Page, page), baseline_count=1) is None