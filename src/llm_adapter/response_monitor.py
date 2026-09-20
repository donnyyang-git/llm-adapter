import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from llm_adapter.adapters.gemini import GeminiResponse


ResponseSampler = Callable[[], Awaitable[GeminiResponse | None]]
ResponseUpdateHandler = Callable[[GeminiResponse], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


class ResponseTimeoutError(TimeoutError):
    def __init__(
        self,
        phase: Literal["first_response", "total"],
        partial: GeminiResponse | None,
    ) -> None:
        self.phase = phase
        self.partial = partial
        super().__init__(
            "Timed out waiting for the first Gemini response."
            if phase == "first_response"
            else "Timed out waiting for Gemini to finish responding."
        )


class ResponseMonitor:
    def __init__(
        self,
        first_response_timeout: float,
        total_response_timeout: float,
        stable_seconds: float,
        poll_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.first_response_timeout = first_response_timeout
        self.total_response_timeout = total_response_timeout
        self.stable_seconds = stable_seconds
        self.poll_interval = poll_interval
        self.monotonic = monotonic
        self.sleep = sleep

    async def wait(
        self,
        sample: ResponseSampler,
        on_update: ResponseUpdateHandler | None = None,
    ) -> GeminiResponse:
        started_at = self.monotonic()
        last_change_at = started_at
        latest: GeminiResponse | None = None
        latest_content: tuple[str, str] | None = None

        while True:
            now = self.monotonic()
            if now - started_at >= self.total_response_timeout:
                raise ResponseTimeoutError("total", latest)

            current = await sample()
            if current is not None and current.text:
                content = (current.text, current.markdown)
                if content != latest_content:
                    latest = current
                    latest_content = content
                    last_change_at = now
                    if on_update is not None:
                        await on_update(current)
                else:
                    latest = current

                # [修改] 2026-09-20 10:35 原因: Gemini 的 HTML / 程式碼回覆常會在短暫停頓後繼續長內容。 說明: 對 code-like 回覆使用更長的穩定窗口，避免只抓到前段就提早結束。
                stable_seconds = self._stable_seconds_for(current)
                if (
                    not current.generating
                    and current.input_editable
                    and now - last_change_at >= stable_seconds
                ):
                    return current
            elif latest is None and now - started_at >= self.first_response_timeout:
                raise ResponseTimeoutError("first_response", None)

            await self.sleep(self.poll_interval)

    def _stable_seconds_for(self, current: GeminiResponse) -> float:
        if self._looks_like_code_response(current):
            return max(self.stable_seconds, 6.0)
        return self.stable_seconds

    @staticmethod
    def _looks_like_code_response(current: GeminiResponse) -> bool:
        text = f"{current.text}\n{current.markdown}"
        return any(
            marker in text
            for marker in (
                "<!DOCTYPE html>",
                "<html",
                "<script",
                "<style",
                "```",
                "function ",
                "class ",
                "import ",
            )
        )