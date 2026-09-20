from pathlib import Path
from typing import cast

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
        children: dict[str, "FakeLocator"] | None = None,
    ) -> None:
        self._count = count
        self._visible = visible
        self._editable = editable
        self._text = text
        self._html = html
        self._children = children or {}
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

    def nth(self, _: int) -> "FakeLocator":
        return self

    def locator(self, selector: str) -> "FakeLocator":
        return self._children.get(selector, FakeLocator(0))

    async def inner_text(self) -> str:
        return self._text

    async def inner_html(self) -> str:
        return self._html


class FakePage:
    def __init__(self, url: str, locators: dict[str, FakeLocator]) -> None:
        self.url = url
        self.locators = locators

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.get(selector, FakeLocator(0))


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
        children={adapter.RESPONSE_CONTENT_SELECTORS[0]: content},
    )
    page = FakePage(
        "https://gemini.google.com/app",
        {
            adapter.MODEL_MESSAGE_SELECTORS[0]: response,
            adapter.INPUT_SELECTORS[0]: FakeLocator(1),
        },
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


@pytest.mark.asyncio
async def test_does_not_capture_preexisting_response() -> None:
    adapter = GeminiAdapter()
    page = FakePage(
        "https://gemini.google.com/app",
        {adapter.MODEL_MESSAGE_SELECTORS[0]: FakeLocator(1)},
    )

    assert await adapter.capture_latest_response(cast(Page, page), baseline_count=1) is None