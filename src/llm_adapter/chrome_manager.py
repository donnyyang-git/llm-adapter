import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright, async_playwright

from llm_adapter.config import Settings
from llm_adapter.models import ChromeStatus, TabInfo


class ChromeManager:
    NEW_GEMINI_CONVERSATION_URL = "https://gemini.google.com/app"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._state: ChromeStatus = ChromeStatus(
            state="stopped",
            connected=False,
            message="Dedicated Chrome has not been started.",
        )
        self._page_ids: dict[int, str] = {}
        self._selected_tab_id: str | None = None
        self._start_lock = asyncio.Lock()

    @property
    def status(self) -> ChromeStatus:
        if self._browser is not None and self._browser.is_connected():
            return ChromeStatus(state="connected", connected=True)
        return self._state

    @property
    def endpoint(self) -> str:
        return f"http://{self.settings.cdp_host}:{self.settings.cdp_port}"

    @staticmethod
    def is_gemini_url(url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower()
        return hostname == "gemini.google.com"

    def find_chrome_executable(self) -> Path | None:
        if self.settings.chrome_executable is not None:
            configured = self.settings.chrome_executable.expanduser()
            return configured if configured.is_file() else None

        roots = (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        for root in filter(None, roots):
            candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return candidate
        return None

    async def start(self) -> ChromeStatus:
        async with self._start_lock:
            if self.status.connected:
                return self.status

            self._state = ChromeStatus(state="starting", connected=False)
            if await self._connect():
                return self.status

            executable = self.find_chrome_executable()
            if executable is None:
                self._state = ChromeStatus(
                    state="error",
                    connected=False,
                    message="Google Chrome executable was not found.",
                )
                return self.status

            self.settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
            self._process = await asyncio.create_subprocess_exec(
                str(executable),
                f"--remote-debugging-address={self.settings.cdp_host}",
                f"--remote-debugging-port={self.settings.cdp_port}",
                f"--user-data-dir={self.settings.chrome_profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://gemini.google.com/app",
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.settings.chrome_startup_timeout_seconds
            while loop.time() < deadline:
                if await self._connect():
                    return self.status
                await asyncio.sleep(0.25)

            self._state = ChromeStatus(
                state="error",
                connected=False,
                message="Chrome started, but its local debugging endpoint did not respond.",
            )
            return self.status

    async def list_tabs(self) -> list[TabInfo]:
        if self._browser is None or not self._browser.is_connected():
            return []

        cdp_tabs = await self._list_cdp_tabs()
        if cdp_tabs is not None:
            return cdp_tabs

        tabs: list[TabInfo] = []
        active_page_keys: set[int] = set()
        for context in self._browser.contexts:
            for page in context.pages:
                if page.is_closed():
                    continue
                page_key = id(page)
                active_page_keys.add(page_key)
                tab_id = self._page_ids.setdefault(page_key, uuid4().hex)
                try:
                    title = await page.title()
                except PlaywrightError:
                    continue
                tabs.append(
                    TabInfo(
                        id=tab_id,
                        title=title or "Untitled tab",
                        url=page.url,
                        is_gemini=self.is_gemini_url(page.url),
                        is_selected=tab_id == self._selected_tab_id,
                    )
                )

        self._page_ids = {
            page_key: tab_id
            for page_key, tab_id in self._page_ids.items()
            if page_key in active_page_keys
        }
        return tabs

    async def _list_cdp_tabs(self) -> list[TabInfo] | None:
        def fetch_targets() -> list[dict[str, Any]]:
            with urlopen(f"{self.endpoint}/json/list", timeout=2) as response:
                return json.load(response)

        try:
            targets = await asyncio.to_thread(fetch_targets)
        except (OSError, ValueError):
            return None

        tabs: list[TabInfo] = []
        active_tab_ids: set[str] = set()
        for target in targets:
            tab_id = target.get("id")
            url = target.get("url", "")
            if (
                target.get("type") != "page"
                or not isinstance(tab_id, str)
                or not isinstance(url, str)
                or url.startswith("chrome://omnibox-popup")
            ):
                continue
            active_tab_ids.add(tab_id)
            tabs.append(
                TabInfo(
                    id=tab_id,
                    title=target.get("title") or "Untitled tab",
                    url=url,
                    is_gemini=self.is_gemini_url(url),
                    is_selected=tab_id == self._selected_tab_id,
                )
            )

        if self._selected_tab_id not in active_tab_ids:
            self._selected_tab_id = None
        return tabs

    async def select_tab(self, tab_id: str) -> TabInfo:
        tabs = await self.list_tabs()
        tab = next((candidate for candidate in tabs if candidate.id == tab_id), None)
        if tab is None:
            raise KeyError(tab_id)
        if not tab.is_gemini:
            raise ValueError("Only Gemini tabs can be selected.")
        self._selected_tab_id = tab_id
        return tab.model_copy(update={"is_selected": True})

    async def get_selected_tab(self) -> TabInfo:
        tabs = await self.list_tabs()
        tab = next((candidate for candidate in tabs if candidate.is_selected), None)
        if tab is None:
            raise LookupError("No Gemini tab is selected.")
        return tab

    async def start_new_gemini_conversation(self) -> TabInfo:
        tab = await self.get_selected_tab()
        page = await self.get_page(tab.id)
        await page.goto(self.NEW_GEMINI_CONVERSATION_URL, wait_until="domcontentloaded")
        return await self.get_selected_tab()

    async def get_page(self, tab_id: str) -> Page:
        if self._browser is None or not self._browser.is_connected():
            raise LookupError("Chrome is not connected.")
        for context in self._browser.contexts:
            for page in context.pages:
                if page.is_closed():
                    continue
                if self._page_ids.get(id(page)) == tab_id:
                    return page
                if await self._target_id_for_page(page) == tab_id:
                    return page
        raise KeyError(tab_id)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._state = ChromeStatus(state="stopped", connected=False)

    async def _connect(self) -> bool:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(self.endpoint)
        except PlaywrightError:
            return False
        self._state = ChromeStatus(state="connected", connected=True)
        return True

    @staticmethod
    async def _target_id_for_page(page: Page) -> str | None:
        session = None
        try:
            session = await page.context.new_cdp_session(page)
            target_info = await session.send("Target.getTargetInfo")
            return target_info.get("targetInfo", {}).get("targetId")
        except PlaywrightError:
            return None
        finally:
            if session is not None:
                await session.detach()