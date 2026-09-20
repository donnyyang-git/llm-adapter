import re
from html import unescape
from urllib.parse import urlparse

from markdownify import markdownify
from playwright.async_api import Locator, Page
from pydantic import BaseModel, Field


class GeminiPageStatus(BaseModel):
    is_gemini: bool
    ready: bool
    message: str | None = None


class GeminiResponse(BaseModel):
    text: str
    markdown: str
    generating: bool
    input_editable: bool
    artifacts: list["GeminiArtifactCapture"] = Field(default_factory=list)
    # [修改] 2026-09-20 18:55 原因: 需要把 Gemini artifact 的完整程式碼與摘要分開保存。 說明: 由 Monaco model 直接擷取完整 code，避免從視覺文字拼湊造成缺段。
    artifact_code: str = ""
    # [修改] 2026-09-20 18:05 原因: 需要把 Gemini 回覆在畫面上的樣子一併保留。 說明: 擷取最新回覆區塊的 PNG bytes，交由 service 存檔並回傳給前端顯示。
    screenshot_png: bytes | None = None


class GeminiAdapterError(RuntimeError):
    pass


class GeminiArtifactCapture(BaseModel):
    title: str
    summary: str = ""
    kind: str
    language: str = ""
    code: str = ""
    preview_type: str = "none"


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
    CODE_VIEW_SELECTORS = (
        'button:has-text("程式碼")',
        'button:has-text("Code")',
        '[role="tab"]:has-text("程式碼")',
        '[role="tab"]:has-text("Code")',
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
        summary_content = await self._capture_summary_content(page, response)

        text = await self._read_locator_text(summary_content)
        html = await summary_content.inner_html()
        markdown = self.html_to_markdown(html)
        artifacts = await self._capture_artifacts(page, response, html, markdown)
        input_locator = await self.find_input(page)
        return GeminiResponse(
            text=text,
            markdown=markdown,
            generating=await self.is_generating(page),
            input_editable=(
                input_locator is not None and await input_locator.is_editable()
            ),
            artifacts=artifacts,
            # [修改] 2026-09-20 19:35 原因: legacy response_artifact_code 只應對應 Gemini UI 主 artifact。 說明: 一般 markdown code block 改由 response_artifacts 提供，避免舊欄位語義混淆。
            artifact_code=next(
                (artifact.code for artifact in artifacts if artifact.kind == "gemini-ui"),
                "",
            ),
            screenshot_png=await self._capture_response_screenshot(response, summary_content),
        )

    async def is_generating(self, page: Page) -> bool:
        return await self._first_visible(page, self.STOP_SELECTORS) is not None

    async def _capture_summary_content(self, page: Page, response: Locator) -> Locator:
        # [修改] 2026-09-20 18:55 原因: artifact 回覆的摘要與完整程式碼分別存在不同視圖。 說明: 先切到預覽視圖抓摘要，再另外切到程式碼視圖抓完整 code。
        await self._open_preview_view_if_available(page, response)
        return await self._select_response_content(response)

    async def _open_code_view_if_available(self, page: Page, response: Locator) -> bool:
        for selector in self.CODE_VIEW_SELECTORS:
            button = response.locator(selector).first
            if not await button.count() or not await button.is_visible():
                continue
            try:
                await button.click()
                await page.wait_for_timeout(100)
                return True
            except Exception:
                continue
        return False

    async def _open_preview_view_if_available(self, page: Page, response: Locator) -> bool:
        preview_selectors = (
            'button:has-text("預覽")',
            'button:has-text("Preview")',
            '[role="tab"]:has-text("預覽")',
            '[role="tab"]:has-text("Preview")',
        )
        for selector in preview_selectors:
            button = response.locator(selector).first
            if not await button.count() or not await button.is_visible():
                continue
            try:
                await button.click()
                await page.wait_for_timeout(100)
                return True
            except Exception:
                continue
        return False

    async def _select_response_content(self, response: Locator) -> Locator:
        candidates: list[Locator] = [response]
        for selector in self.RESPONSE_CONTENT_SELECTORS + ("pre", "code", "textarea"):
            candidate = response.locator(selector).first
            if await candidate.count() and await candidate.is_visible():
                candidates.append(candidate)

        best_candidate = response
        best_length = -1
        for candidate in candidates:
            try:
                text = await self._read_locator_text(candidate)
            except Exception:
                continue
            if len(text) > best_length:
                best_candidate = candidate
                best_length = len(text)
        return best_candidate

    async def _read_locator_text(self, locator: Locator) -> str:
        # [修改] 2026-09-20 18:35 原因: code artifact 文字需要盡量保留原始內容，而不是只拿到畫面上換行後的可見片段。 說明: 先用 `text_content()` 取原始節點文字，再退回 `inner_text()` 兼容一般訊息。
        text_content = await locator.text_content()
        if text_content is not None and text_content.strip():
            return text_content.strip()
        return (await locator.inner_text()).strip()

    async def _capture_artifact_code(self, page: Page, response: Locator) -> str | None:
        if not await self._open_code_view_if_available(page, response):
            return None

        artifact_models = await page.evaluate(
            """() => {
                const editorApi = globalThis.monaco?.editor;
                if (!editorApi?.getModels) return [];
                return editorApi.getModels().map((model) => ({
                    language: typeof model.getLanguageId === 'function' ? model.getLanguageId() : null,
                    value: typeof model.getValue === 'function' ? model.getValue() : '',
                }));
            }"""
        )
        if artifact_models:
            best_model = max(
                artifact_models,
                key=lambda model: len(model.get("value") or ""),
            )
            best_value = (best_model.get("value") or "").strip()
            if best_value:
                return best_value

        code_locator = response.locator(".view-lines").first
        if await code_locator.count() and await code_locator.is_visible():
            return await self._read_locator_text(code_locator)
        return None

    async def _capture_artifacts(
        self, page: Page, response: Locator, html: str, markdown: str
    ) -> list[GeminiArtifactCapture]:
        artifacts: list[GeminiArtifactCapture] = []

        # [修改] 2026-09-20 19:20 原因: Gemini 可能同時提供互動式 artifact 與多個普通 code block。 說明: 先抓 Gemini UI 主 artifact，再補齊 markdown 中的其他程式片段，避免遺漏多段程式碼。
        primary_artifact = await self._capture_primary_artifact(page, response)
        if primary_artifact is not None:
            artifacts.append(primary_artifact)

        for artifact in self._extract_html_code_artifacts(html):
            if any(existing.code.strip() == artifact.code.strip() for existing in artifacts):
                continue
            artifacts.append(artifact)

        for artifact in self._extract_markdown_code_artifacts(markdown):
            if any(existing.code.strip() == artifact.code.strip() for existing in artifacts):
                continue
            artifacts.append(artifact)

        for index, artifact in enumerate(artifacts, start=1):
            if not artifact.title:
                artifact.title = f"Code snippet {index}"
        return artifacts

    async def _capture_primary_artifact(
        self, page: Page, response: Locator
    ) -> GeminiArtifactCapture | None:
        if not await self._open_code_view_if_available(page, response):
            return None

        artifact_models = await page.evaluate(
            """() => {
                const editorApi = globalThis.monaco?.editor;
                if (!editorApi?.getModels) return [];
                return editorApi.getModels().map((model) => ({
                    language: typeof model.getLanguageId === 'function' ? model.getLanguageId() : null,
                    value: typeof model.getValue === 'function' ? model.getValue() : '',
                }));
            }"""
        )
        if artifact_models:
            best_model = max(
                artifact_models,
                key=lambda model: len(model.get("value") or ""),
            )
            best_value = (best_model.get("value") or "").strip()
            if best_value:
                language = (best_model.get("language") or "").strip()
                return GeminiArtifactCapture(
                    title="Generated artifact",
                    summary="",
                    kind="gemini-ui",
                    language=language,
                    code=best_value,
                    preview_type=self._preview_type_for(best_value, language),
                )

        code_locator = response.locator(".view-lines").first
        if await code_locator.count() and await code_locator.is_visible():
            code_text = await self._read_locator_text(code_locator)
            if code_text.strip():
                return GeminiArtifactCapture(
                    title="Generated artifact",
                    summary="",
                    kind="gemini-ui",
                    language="",
                    code=code_text,
                    preview_type=self._preview_type_for(code_text, ""),
                )
        return None

    @staticmethod
    def _extract_markdown_code_artifacts(markdown: str) -> list[GeminiArtifactCapture]:
        pattern = re.compile(r"```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```")
        artifacts: list[GeminiArtifactCapture] = []
        cursor = 0
        for index, match in enumerate(pattern.finditer(markdown), start=1):
            language = (match.group(1) or "").strip().lower()
            code = match.group(2).strip()
            summary = GeminiAdapter._extract_section_summary(markdown[cursor:match.start()])
            if not code:
                cursor = match.end()
                continue
            artifacts.append(
                GeminiArtifactCapture(
                    title=f"Code snippet {index}",
                    summary=summary,
                    kind="code-block",
                    language=language,
                    code=code,
                    preview_type=GeminiAdapter._preview_type_for(code, language),
                )
            )
            cursor = match.end()
        return artifacts

    @staticmethod
    def _extract_html_code_artifacts(html: str) -> list[GeminiArtifactCapture]:
        pattern = re.compile(
            r"<pre>\s*<code(?:[^>]*class=\"[^\"]*language-([a-zA-Z0-9_+-]+)[^\"]*\")?[^>]*>([\s\S]*?)</code>\s*</pre>",
            re.IGNORECASE,
        )
        artifacts: list[GeminiArtifactCapture] = []
        cursor = 0
        for index, match in enumerate(pattern.finditer(html), start=1):
            language = (match.group(1) or "").strip().lower()
            code = unescape(match.group(2)).strip()
            summary = GeminiAdapter._extract_section_summary(html[cursor:match.start()])
            if not code:
                cursor = match.end()
                continue
            artifacts.append(
                GeminiArtifactCapture(
                    title=f"Code snippet {index}",
                    summary=summary,
                    kind="code-block",
                    language=language,
                    code=code,
                    preview_type=GeminiAdapter._preview_type_for(code, language),
                )
            )
            cursor = match.end()
        return artifacts

    @staticmethod
    def _extract_section_summary(source: str) -> str:
        normalized = source.replace("\r\n", "\n")
        lines = [line.strip() for line in normalized.split("\n")]
        cleaned: list[str] = []
        for line in lines:
            if not line:
                cleaned.append("")
                continue
            if line.startswith("```"):
                continue
            if line in {"Python", "python", "HTML", "html", "JavaScript", "javascript"}:
                continue
            cleaned.append(line)

        while cleaned and not cleaned[-1]:
            cleaned.pop()

        summary_lines: list[str] = []
        for line in reversed(cleaned):
            if not line:
                if summary_lines:
                    break
                continue
            summary_lines.append(line)
        return "\n".join(reversed(summary_lines)).strip()

    @staticmethod
    def _preview_type_for(code: str, language: str) -> str:
        normalized_code = code.lstrip().lower()
        if language == "html" or normalized_code.startswith("<!doctype html>") or normalized_code.startswith("<html"):
            return "html"
        if code.strip():
            return "code"
        return "none"

    async def _capture_response_screenshot(
        self, response: Locator, content: Locator
    ) -> bytes | None:
        # [修改] 2026-09-20 18:05 原因: 單純文字無法表達 Gemini 的 artifact / 預覽版面。 說明: 優先截整個 response 卡片，失敗時退回內容區塊，讓前端能附圖顯示。
        for candidate in (response, content):
            try:
                return await candidate.screenshot(type="png")
            except Exception:
                continue
        return None

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