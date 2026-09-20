import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Browser

from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.config import Settings


class FakePage:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self._title = title
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return self._title


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages


class FakeBrowser:
    def __init__(self, pages: list[FakePage]) -> None:
        self.contexts = [FakeContext(pages)]

    def is_connected(self) -> bool:
        return True


def test_finds_explicit_chrome_executable(tmp_path: Path) -> None:
    executable = tmp_path / "chrome.exe"
    executable.touch()
    manager = ChromeManager(Settings(chrome_executable=executable))

    assert manager.find_chrome_executable() == executable


@pytest.mark.asyncio
async def test_tab_ids_remain_stable_and_closed_tabs_are_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini = FakePage("https://gemini.google.com/app/abc", "Gemini")
    other = FakePage("https://example.com", "Example")
    manager = ChromeManager(Settings())
    manager._browser = cast(Browser, FakeBrowser([gemini, other]))
    monkeypatch.setattr(manager, "_list_cdp_tabs", AsyncMock(return_value=None))

    first = await manager.list_tabs()
    other.closed = True
    second = await manager.list_tabs()

    assert len(first) == 2
    assert len(second) == 1
    assert first[0].id == second[0].id
    assert second[0].is_gemini is True


@pytest.mark.asyncio
async def test_select_tab_rejects_non_gemini_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage("https://example.com", "Example")
    manager = ChromeManager(Settings())
    manager._browser = cast(Browser, FakeBrowser([page]))
    monkeypatch.setattr(manager, "_list_cdp_tabs", AsyncMock(return_value=None))
    tab = (await manager.list_tabs())[0]

    with pytest.raises(ValueError, match="Only Gemini"):
        await manager.select_tab(tab.id)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gemini.google.com/app", True),
        ("https://gemini.google.com.evil.example/app", False),
        ("https://www.google.com/", False),
    ],
)
def test_identifies_only_gemini_hostname(url: str, expected: bool) -> None:
    assert ChromeManager.is_gemini_url(url) is expected


@pytest.mark.asyncio
async def test_cdp_target_ids_are_stable_tab_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ChromeManager(Settings())
    manager._browser = cast(Browser, FakeBrowser([]))
    targets = [
        {
            "id": "TARGET-123",
            "type": "page",
            "title": "Gemini",
            "url": "https://gemini.google.com/app/abc",
        },
        {
            "id": "INTERNAL",
            "type": "page",
            "title": "Popup",
            "url": "chrome://omnibox-popup.top-chrome/",
        },
    ]

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(targets).encode()

    monkeypatch.setattr("llm_adapter.chrome_manager.urlopen", lambda *_args, **_kwargs: FakeResponse())

    tabs = await manager.list_tabs()

    assert [tab.id for tab in tabs] == ["TARGET-123"]