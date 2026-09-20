import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Browser, Error as PlaywrightError

from llm_adapter.chrome_manager import ChromeManager
from llm_adapter.config import Settings
from llm_adapter.models import TabInfo


class FakePage:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self._title = title
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        # [修改] 2026-09-20 16:40 原因: 測試新開 Gemini tab 時需要模擬 Playwright 的導航行為。 說明: 讓假頁面在 goto 後同步更新 URL，方便驗證新 tab 會被導向 Gemini。
        self.url = url


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    async def new_page(self) -> FakePage:
        # [修改] 2026-09-20 16:40 原因: ChromeManager 會在沒有可選 tab 時主動建立新頁。 說明: 模擬 Playwright context.new_page() 的回傳值與頁面登錄。
        page = FakePage("about:blank", "Untitled tab")
        self.pages.append(page)
        return page


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


def test_status_recognizes_live_chrome_listener_without_playwright_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ChromeManager(Settings())
    manager._state = manager._state.model_copy(update={"state": "starting"})

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"Browser": "Chrome"}'

    monkeypatch.setattr("llm_adapter.chrome_manager.urlopen", lambda *_args, **_kwargs: FakeResponse())

    status = manager.status

    assert status.connected is True
    assert status.state == "connected"


@pytest.mark.asyncio
async def test_reports_chrome_exit_and_writes_trace_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "chrome.exe"
    executable.touch()
    manager = ChromeManager(
        Settings(data_dir=tmp_path / "data", chrome_executable=executable)
    )
    manager._connect = AsyncMock(return_value=False)
    monkeypatch.setattr("llm_adapter.chrome_manager.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not listening")))

    class ExitedProcess:
        pid = 1234
        returncode = 19

    monkeypatch.setattr(
        "llm_adapter.chrome_manager.asyncio.create_subprocess_exec",
        AsyncMock(return_value=ExitedProcess()),
    )

    status = await manager.start()

    assert status.state == "error"
    assert "exit code 19" in (status.message or "")
    trace_events = [
        json.loads(line)["event"]
        for line in (tmp_path / "data" / "logs" / "chrome-manager.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert trace_events == ["start_requested", "chrome_launching", "chrome_started", "chrome_exited"]


@pytest.mark.asyncio
async def test_cdp_timeout_points_to_diagnostic_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "chrome.exe"
    executable.touch()
    manager = ChromeManager(
        Settings(
            data_dir=tmp_path / "data",
            chrome_executable=executable,
            chrome_startup_timeout_seconds=0,
        )
    )
    manager._connect = AsyncMock(return_value=False)
    monkeypatch.setattr("llm_adapter.chrome_manager.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not listening")))

    class RunningProcess:
        pid = 1234
        returncode = None

    monkeypatch.setattr(
        "llm_adapter.chrome_manager.asyncio.create_subprocess_exec",
        AsyncMock(return_value=RunningProcess()),
    )

    status = await manager.start()

    assert status.state == "error"
    assert "data/logs/chrome-manager.jsonl" in (status.message or "")
    assert "data/logs/chrome-stderr.log" in (status.message or "")
    assert '"event": "cdp_endpoint_timeout"' in (
        tmp_path / "data" / "logs" / "chrome-manager.jsonl"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_connects_with_ipv4_then_ipv6_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ChromeManager(Settings())
    calls: list[str] = []

    class FakePlaywright:
        class Chromium:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def connect_over_cdp(self, endpoint: str):
                calls.append(endpoint)
                if endpoint == "http://127.0.0.1:9222":
                    raise PlaywrightError("ipv4 fail")
                return object()

        chromium = Chromium()

    class FakeStarter:
        def __init__(self) -> None:
            self._playwright = FakePlaywright()

        async def start(self):
            return self._playwright

    monkeypatch.setattr("llm_adapter.chrome_manager.async_playwright", lambda: FakeStarter())

    connected = await manager._connect()

    assert connected is True
    assert calls == ["http://127.0.0.1:9222", "http://[::1]:9222"]
    assert manager.endpoint == "http://[::1]:9222"


@pytest.mark.asyncio
async def test_clears_stale_profile_lock_before_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "chrome.exe"
    executable.touch()
    profile_dir = tmp_path / "data" / "chrome-profile"
    profile_dir.mkdir(parents=True)
    stale_lock = profile_dir / "lockfile"
    stale_lock.write_text("stale", encoding="utf-8")

    manager = ChromeManager(Settings(data_dir=tmp_path / "data", chrome_executable=executable))
    manager._connect = AsyncMock(return_value=False)
    monkeypatch.setattr("llm_adapter.chrome_manager.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not listening")))

    class RunningProcess:
        pid = 9876
        returncode = None

    monkeypatch.setattr(
        "llm_adapter.chrome_manager.asyncio.create_subprocess_exec",
        AsyncMock(return_value=RunningProcess()),
    )

    await manager.start()

    assert not stale_lock.exists()


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


@pytest.mark.asyncio
async def test_start_new_gemini_conversation_opens_new_tab_when_no_tab_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ChromeManager(Settings())
    manager._browser = cast(Browser, FakeBrowser([FakePage("https://example.com", "Example")]))
    monkeypatch.setattr(
        manager,
        "_list_cdp_tabs",
        AsyncMock(
            side_effect=[
                [
                    TabInfo(
                        id="TARGET-OLD",
                        title="Example",
                        url="https://example.com",
                        is_gemini=False,
                        is_selected=False,
                    )
                ],
                [
                    TabInfo(
                        id="TARGET-NEW",
                        title="Gemini",
                        url=ChromeManager.NEW_GEMINI_CONVERSATION_URL,
                        is_gemini=True,
                        is_selected=True,
                    )
                ],
            ]
        ),
    )
    monkeypatch.setattr(manager, "_target_id_for_page", AsyncMock(return_value="TARGET-NEW"))

    tab = await manager.start_new_gemini_conversation()

    assert tab.id == "TARGET-NEW"
    assert tab.is_gemini is True
    assert manager._selected_tab_id == "TARGET-NEW"
    assert manager._browser.contexts[0].pages[-1].url == ChromeManager.NEW_GEMINI_CONVERSATION_URL


@pytest.mark.asyncio
async def test_start_new_gemini_conversation_reconnects_browser_when_endpoint_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ChromeManager(Settings())
    reconnect_browser = FakeBrowser([FakePage("https://example.com", "Example")])

    async def fake_connect() -> bool:
        manager._browser = cast(Browser, reconnect_browser)
        return True

    manager._browser = None
    monkeypatch.setattr(manager, "_connect", fake_connect)
    monkeypatch.setattr(
        manager,
        "_list_cdp_tabs",
        AsyncMock(
            side_effect=[
                [],
                [
                    TabInfo(
                        id="TARGET-NEW",
                        title="Gemini",
                        url=ChromeManager.NEW_GEMINI_CONVERSATION_URL,
                        is_gemini=True,
                        is_selected=True,
                    )
                ],
            ]
        ),
    )
    monkeypatch.setattr(manager, "_target_id_for_page", AsyncMock(return_value="TARGET-NEW"))

    tab = await manager.start_new_gemini_conversation()

    assert tab.id == "TARGET-NEW"
    assert manager._browser is reconnect_browser


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