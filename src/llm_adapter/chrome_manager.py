import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener, urlopen
from uuid import uuid4

from playwright.async_api import Browser, Error as PlaywrightError, Page, Playwright, async_playwright

from llm_adapter.config import Settings
from llm_adapter.models import ChromeStatus, TabInfo


class ChromeManager:
    NEW_GEMINI_CONVERSATION_URL = "https://gemini.google.com/app"
    TRACE_LOG_NAME = "chrome-manager.jsonl"
    CHROME_LOG_NAME = "chrome-stderr.log"
    LOOPBACK_NO_PROXY_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._connected_endpoint: str | None = None
        self._state: ChromeStatus = ChromeStatus(
            state="stopped",
            connected=False,
            message="Dedicated Chrome has not been started.",
        )
        self._page_ids: dict[int, str] = {}
        self._selected_tab_id: str | None = None
        self._start_lock = asyncio.Lock()

    @property
    def _logs_dir(self) -> Path:
        return self.settings.data_dir / "logs"

    @property
    def _trace_log_path(self) -> Path:
        return self._logs_dir / self.TRACE_LOG_NAME

    @property
    def _chrome_log_path(self) -> Path:
        return self._logs_dir / self.CHROME_LOG_NAME

    @property
    def status(self) -> ChromeStatus:
        # // [修改] 2026-09-20 15:35 原因: Windows 下 Chrome 可能已在 loopback 上開啟 CDP，但程式內部 _browser 尚未初始化，導致狀態 API 誤判為 stopped。 說明: 只在 App 已進入啟動/連線流程時才做 live endpoint fallback，避免 fresh app 被其他電腦 Chrome 誤判為已連線。
        if self._browser is not None and self._browser.is_connected():
            self._state = ChromeStatus(state="connected", connected=True)
            return self._state

        if self._connected_endpoint is not None:
            try:
                with self._open_loopback_url(f"{self._connected_endpoint}/json/version", timeout=1):
                    self._state = ChromeStatus(state="connected", connected=True)
                    return self._state
            except OSError:
                self._connected_endpoint = None

        if self._process is None and self._state.state == "stopped":
            return self._state

        for endpoint in self._candidate_endpoints:
            try:
                with self._open_loopback_url(f"{endpoint}/json/version", timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if isinstance(payload, dict) and ("Browser" in payload or "webSocketDebuggerUrl" in payload):
                        self._connected_endpoint = endpoint
                        self._state = ChromeStatus(state="connected", connected=True)
                        return self._state
            except (OSError, ValueError, UnicodeDecodeError):
                continue

        return self._state

    @property
    def endpoint(self) -> str:
        if self._connected_endpoint is not None:
            return self._connected_endpoint
        host = self.settings.cdp_host or "127.0.0.1"
        return f"http://{host}:{self.settings.cdp_port}"

    @property
    def _candidate_endpoints(self) -> list[str]:
        # // [修改] 2026-09-20 15:10 原因: Chrome 在 Windows 上可能在 IPv4/IPv6 loopback 雙棧中綁定 9222，程式只用單一設定會誤判失敗。 說明: 依序嘗試設定值、IPv4 loopback、IPv6 loopback，讓 CDP 連線能自動 fallback。
        configured_host = (self.settings.cdp_host or "127.0.0.1").strip()
        hosts: list[str] = []
        seen: set[str] = set()

        def add_host(host: str) -> None:
            if not host:
                return
            normalized = host.strip().lower()
            if normalized in {"[::1]", "::1"}:
                host = "[::1]"
            elif normalized == "localhost":
                host = "localhost"
            key = host.lower()
            if key in seen:
                return
            seen.add(key)
            hosts.append(host)

        add_host(configured_host)
        add_host("127.0.0.1")
        add_host("[::1]")
        add_host("::1")

        return [f"http://{host}:{self.settings.cdp_port}" for host in hosts]

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

    def _profile_is_in_use(self) -> bool:
        # // [修改] 2026-09-20 14:15 原因: 需要在啟動前檢查專用 profile 是否已被另一個 Chrome 進程持有，避免重啟時直接因為鎖定退出。 說明: 以 Windows Chrome command line 檢查 --user-data-dir 是否指向同一個 profile，若已被佔用則直接返回錯誤，不清除鎖定檔。
        if not self.settings.chrome_profile_dir.exists():
            return False

        profile_dir = str(self.settings.chrome_profile_dir.resolve()).lower()
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'chrome' -or $_.Name -match 'msedge' } | Select-Object -ExpandProperty CommandLine) | Out-String -Width 4096",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        for line in result.stdout.splitlines():
            normalized = line.lower()
            if "--user-data-dir=" in normalized and profile_dir in normalized:
                return True
        return False

    def _clear_stale_profile_locks(self) -> None:
        # // [修改] 2026-09-20 14:00 原因: Chrome 專用 profile 會保留 lockfile；上一輪崩潰時會卡住新啟動並導致退出。 說明: 啟動前清理 profile 中的舊鎖定檔，避免專用 Chrome 因重用殘留鎖定而直接退出。
        if not self.settings.chrome_profile_dir.exists():
            return
        for lock_path in (
            self.settings.chrome_profile_dir / "lockfile",
            self.settings.chrome_profile_dir / "SingletonLock",
            self.settings.chrome_profile_dir / "SingletonSocket",
        ):
            if lock_path.exists():
                try:
                    lock_path.unlink()
                except OSError:
                    pass

    @classmethod
    def _ensure_loopback_no_proxy_env(cls) -> None:
        # // [修改] 2026-09-21 17:05 原因: 公司代理會攔截本機 loopback 的 CDP 請求，而使用者環境又把 NO_PROXY 誤設為 NO_PROXYx，導致 Chrome 明明已啟動卻被代理頁誤判成 403/504。 說明: 啟動前統一補齊標準 NO_PROXY/no_proxy，並保留既有清單與錯字變體內容，確保 127.0.0.1/localhost/::1 直接連線不走代理。
        configured_hosts: list[str] = []
        seen: set[str] = set()

        for key in ("NO_PROXY", "no_proxy", "NO_PROXYx", "no_proxyx"):
            raw_value = os.environ.get(key, "")
            if not raw_value:
                continue
            for item in raw_value.split(","):
                host = item.strip()
                if not host:
                    continue
                normalized = host.lower()
                if normalized in seen:
                    continue
                seen.add(normalized)
                configured_hosts.append(host)

        for host in cls.LOOPBACK_NO_PROXY_HOSTS:
            normalized = host.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            configured_hosts.append(host)

        merged_value = ",".join(configured_hosts)
        os.environ["NO_PROXY"] = merged_value
        os.environ["no_proxy"] = merged_value

    @staticmethod
    def _open_loopback_url(url: str, timeout: float):
        # // [修改] 2026-09-21 17:05 原因: urllib 預設會吃到系統 HTTP_PROXY/HTTPS_PROXY，讓本機 CDP health check 被公司代理攔截。 說明: 對 loopback 檢查改用不帶 proxy 的 opener，直接打本機端點。
        return build_opener(ProxyHandler({})).open(url, timeout=timeout)

    async def start(self) -> ChromeStatus:
        async with self._start_lock:
            self._write_trace("start_requested", endpoint=self.endpoint)
            if self.status.connected:
                self._write_trace("already_connected")
                return self.status

            self._state = ChromeStatus(state="starting", connected=False)
            if await self._connect():
                return self.status

            executable = self.find_chrome_executable()
            if executable is None:
                self._write_trace("chrome_executable_not_found")
                self._state = ChromeStatus(
                    state="error",
                    connected=False,
                    message="Google Chrome executable was not found.",
                )
                return self.status

            self.settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            if self._profile_is_in_use():
                self._write_trace(
                    "chrome_profile_in_use",
                    profile_dir=str(self.settings.chrome_profile_dir),
                )
                self._state = ChromeStatus(
                    state="error",
                    connected=False,
                    message=(
                        "Chrome is already using this profile. Please close the existing Chrome window for this project and try again."
                    ),
                )
                return self.status
            self._clear_stale_profile_locks()
            command = (
                str(executable),
                f"--remote-debugging-address={self.settings.cdp_host}",
                f"--remote-debugging-port={self.settings.cdp_port}",
                f"--user-data-dir={self.settings.chrome_profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--enable-logging=stderr",
                "--v=1",
                "https://gemini.google.com/app",
            )
            self._write_trace("chrome_launching", command=command)
            try:
                with self._chrome_log_path.open("ab") as chrome_log:
                    self._process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=chrome_log,
                        stderr=chrome_log,
                    )
            except OSError as error:
                self._write_trace("chrome_launch_failed", error=str(error))
                self._state = ChromeStatus(
                    state="error",
                    connected=False,
                    message="Chrome could not be started. See data/logs/chrome-manager.jsonl.",
                )
                return self.status

            self._write_trace("chrome_started", process_id=self._process.pid)
            if self._process.returncode is not None:
                self._set_process_exit_error(self._process.returncode)
                return self.status

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.settings.chrome_startup_timeout_seconds
            while loop.time() < deadline:
                if self._process.returncode is not None:
                    self._set_process_exit_error(self._process.returncode)
                    return self.status
                if await self._connect():
                    return self.status
                await asyncio.sleep(0.25)

            self._write_trace("cdp_endpoint_timeout")
            self._state = ChromeStatus(
                state="error",
                connected=False,
                message=(
                    "Chrome started, but its local debugging endpoint did not respond. "
                    "See data/logs/chrome-manager.jsonl and data/logs/chrome-stderr.log."
                ),
            )
            return self.status

    async def list_tabs(self) -> list[TabInfo]:
        if not await self._ensure_browser_connected():
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
        try:
            tab = await self.get_selected_tab()
        except LookupError:
            return await self._open_new_gemini_tab()

        try:
            page = await self.get_page(tab.id)
        except (KeyError, LookupError):
            return await self._open_new_gemini_tab()

        await page.goto(self.NEW_GEMINI_CONVERSATION_URL, wait_until="domcontentloaded")
        return await self.get_selected_tab()

    async def _open_new_gemini_tab(self) -> TabInfo:
        if not await self._ensure_browser_connected():
            raise LookupError("Chrome is not connected.")
        if not self._browser.contexts:
            raise LookupError("Chrome has no available browser context.")

        # // [修改] 2026-09-20 16:40 原因: 當 Gemini tab 已關閉而目前又沒有可選分頁時，原本流程會直接失敗。 說明: 改為主動建立新的 Gemini tab 並重新標記選取狀態，讓 New Gemini chat 可用於復原。
        page = await self._browser.contexts[0].new_page()
        await page.goto(self.NEW_GEMINI_CONVERSATION_URL, wait_until="domcontentloaded")

        tab_id = await self._target_id_for_page(page)
        if tab_id is None:
            raise LookupError("Unable to identify the new Gemini tab.")

        self._selected_tab_id = tab_id
        tabs = await self.list_tabs()
        tab = next((candidate for candidate in tabs if candidate.id == tab_id), None)
        if tab is None:
            raise LookupError(tab_id)
        return tab

    async def get_page(self, tab_id: str) -> Page:
        if not await self._ensure_browser_connected():
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

    async def _ensure_browser_connected(self) -> bool:
        if self._browser is not None and self._browser.is_connected():
            return True

        # // [修改] 2026-09-20 17:20 原因: Chrome 狀態可能已經從 CDP endpoint 判定為 connected，但 Playwright browser 還沒重建。 說明: 這裡主動嘗試重新 connect，讓 list_tabs / get_page / new Gemini chat 能從僅有 endpoint 的狀態恢復。
        return await self._connect()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._connected_endpoint = None
        self._state = ChromeStatus(state="stopped", connected=False)

    async def _connect(self) -> bool:
        self._ensure_loopback_no_proxy_env()
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        last_error: Exception | None = None
        for endpoint in self._candidate_endpoints:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
            except Exception as error:
                last_error = error
                self._write_trace("cdp_connect_failed", endpoint=endpoint, error=str(error))
                continue
            self._connected_endpoint = endpoint
            self._write_trace("cdp_connected", endpoint=endpoint)
            self._state = ChromeStatus(state="connected", connected=True)
            return True

        if last_error is not None:
            self._write_trace("cdp_connect_failed", endpoint=self.endpoint, error=str(last_error))
        return False

    def _set_process_exit_error(self, returncode: int) -> None:
        self._write_trace("chrome_exited", returncode=returncode)
        self._state = ChromeStatus(
            state="error",
            connected=False,
            message=(
                "Chrome exited before its local debugging endpoint responded "
                f"(exit code {returncode}). See data/logs/chrome-manager.jsonl "
                "and data/logs/chrome-stderr.log."
            ),
        )

    def _write_trace(self, event: str, **details: object) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **details,
        }
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            with self._trace_log_path.open("a", encoding="utf-8") as trace_log:
                trace_log.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass

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