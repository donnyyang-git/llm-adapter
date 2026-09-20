from urllib.parse import urlparse

from markdownify import markdownify
from playwright.async_api import Locator, Page
from pydantic import BaseModel


class GeminiPageStatus(BaseModel):
    is_gemini: bool
    ready: bool
    message: str | None = None


class GeminiResponse(BaseModel):
    text: str
    markdown: str
    generating: bool
    input_editable: bool


class GeminiAdapterError(RuntimeError):
    pass


class GeminiAdapter:
    LOGIN_SELECTORS = (
        'button[aria-label="登入"]',
        'button[aria-label="Sign in"]',
        'a[aria-label="登入"]',
        'a[aria-label="Sign in"]',
    )
    INPUT_SELECTORS = (
        '[role="textbox"][contenteditable="true"][aria-label]',
        'rich-textarea [contenteditable="true"]',
    )
    MODEL_MESSAGE_SELECTORS = (
        "model-response",
        '[data-test-id="model-response"]',
    )
    RESPONSE_CONTENT_SELECTORS = (
        "message-content",
        ".markdown-main-panel",
        ".model-response-text",
    )
    STOP_SELECTORS = (
        'button[aria-label="停止回覆"]',
        'button[aria-label="停止生成"]',
        'button[aria-label="Stop response"]',
        'button[data-test-id="stop-button"]',
    )

    @staticmethod
    def supports_url(url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == "gemini.google.com"

    async def inspect(self, page: Page) -> GeminiPageStatus:
        if not self.supports_url(page.url):
            return GeminiPageStatus(
                is_gemini=False,
                ready=False,
                message="The selected tab is not a Gemini page.",
            )

        if await self._first_visible(page, self.LOGIN_SELECTORS) is not None:
            return GeminiPageStatus(
                is_gemini=True,
                ready=False,
                message="Sign in to Gemini in the dedicated Chrome window.",
            )

        input_locator = await self.find_input(page)
        if input_locator is None:
            return GeminiPageStatus(
                is_gemini=True,
                ready=False,
                message="Gemini input was not found; sign in or refresh the page.",
            )
        if not await input_locator.is_editable():
            return GeminiPageStatus(
                is_gemini=True,
                ready=False,
                message="Gemini input is present but not editable.",
            )
        return GeminiPageStatus(is_gemini=True, ready=True)

    async def require_input(self, page: Page) -> Locator:
        input_locator = await self.find_input(page)
        if input_locator is None or not await input_locator.is_editable():
            raise GeminiAdapterError("Gemini input is not available.")
        return input_locator

    async def send_question(self, page: Page, question: str) -> None:
        input_locator = await self.require_input(page)
        await input_locator.fill(question)
        await input_locator.press("Enter")

    async def count_model_messages(self, page: Page) -> int:
        for selector in self.MODEL_MESSAGE_SELECTORS:
            count = await page.locator(selector).count()
            if count:
                return count
        return 0

    async def capture_latest_response(
        self, page: Page, baseline_count: int
    ) -> GeminiResponse | None:
        responses = None
        response_count = 0
        for selector in self.MODEL_MESSAGE_SELECTORS:
            candidate = page.locator(selector)
            count = await candidate.count()
            if count:
                responses = candidate
                response_count = count
                break
        if responses is None or response_count <= baseline_count:
            return None

        response = responses.nth(response_count - 1)
        content = response
        for selector in self.RESPONSE_CONTENT_SELECTORS:
            candidate = response.locator(selector).first
            if await candidate.count():
                content = candidate
                break

        text = (await content.inner_text()).strip()
        html = await content.inner_html()
        input_locator = await self.find_input(page)
        return GeminiResponse(
            text=text,
            markdown=self.html_to_markdown(html),
            generating=await self.is_generating(page),
            input_editable=(
                input_locator is not None and await input_locator.is_editable()
            ),
        )

    async def is_generating(self, page: Page) -> bool:
        return await self._first_visible(page, self.STOP_SELECTORS) is not None

    @staticmethod
    def html_to_markdown(html: str) -> str:
        return markdownify(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["button"],
        ).strip()

    async def find_input(self, page: Page) -> Locator | None:
        return await self._first_visible(page, self.INPUT_SELECTORS)

    @staticmethod
    async def _first_visible(page: Page, selectors: tuple[str, ...]) -> Locator | None:
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return locator
        return None