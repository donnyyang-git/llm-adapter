from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "logs" / "gemini-artifact-probe.json"
CDP_ENDPOINTS = [
    "http://localhost:9222",
    "http://[::1]:9222",
    "http://127.0.0.1:9222",
]

TAB_SELECTORS = {
    "code": [
        'button:has-text("程式碼")',
        'button:has-text("Code")',
        '[role="tab"]:has-text("程式碼")',
        '[role="tab"]:has-text("Code")',
    ],
    "preview": [
        'button:has-text("預覽")',
        'button:has-text("Preview")',
        '[role="tab"]:has-text("預覽")',
        '[role="tab"]:has-text("Preview")',
    ],
}

CANDIDATE_SELECTORS = [
    "message-content",
    ".markdown-main-panel",
    ".model-response-text",
    "pre",
    "code",
    "textarea",
    "iframe",
    "[role='tabpanel']",
    ".cm-content",
    ".view-lines",
    ".monaco-editor",
]


async def click_first_visible(response, selectors: list[str]) -> str | None:
    for selector in selectors:
        locator = response.locator(selector).first
        if await locator.count() and await locator.is_visible():
            await locator.click()
            await response.page.wait_for_timeout(250)
            return selector
    return None


async def inspect_candidate(response, selector: str) -> dict[str, object]:
    locator = response.locator(selector).first
    info: dict[str, object] = {"selector": selector, "count": await locator.count()}
    if not info["count"]:
        return info
    info["visible"] = await locator.is_visible()
    try:
        text_content = await locator.text_content()
    except Exception as error:  # pragma: no cover - probe script only
        text_content = f"<text_content error: {error}>"
    try:
        inner_text = await locator.inner_text()
    except Exception as error:  # pragma: no cover - probe script only
        inner_text = f"<inner_text error: {error}>"
    try:
        inner_html = await locator.inner_html()
    except Exception as error:  # pragma: no cover - probe script only
        inner_html = f"<inner_html error: {error}>"
    info["text_content_length"] = len(text_content or "")
    info["inner_text_length"] = len(inner_text or "")
    info["text_content_sample"] = (text_content or "")[:1200]
    info["inner_text_sample"] = (inner_text or "")[:1200]
    info["inner_html_sample"] = (inner_html or "")[:1200]
    return info


async def inspect_view(response, view_name: str) -> dict[str, object]:
    clicked_selector = await click_first_visible(response, TAB_SELECTORS[view_name])
    candidates = []
    for selector in CANDIDATE_SELECTORS:
        candidates.append(await inspect_candidate(response, selector))
        monaco_model_text = await response.page.evaluate(
                """() => {
                    const editorApi = globalThis.monaco?.editor;
                    if (!editorApi?.getModels) return null;
                    const models = editorApi.getModels();
                    if (!models.length) return null;
                    return models.map((model, index) => ({
                        index,
                        language: typeof model.getLanguageId === 'function' ? model.getLanguageId() : null,
                        value: typeof model.getValue === 'function' ? model.getValue() : null,
                    }));
                }"""
        )
        iframe_payload = await response.page.evaluate(
                """() => {
                    const iframe = document.querySelector('model-response iframe');
                    if (!iframe) return null;
                    const doc = iframe.contentDocument;
                    return {
                        src: iframe.getAttribute('src'),
                        srcdocLength: (iframe.getAttribute('srcdoc') || '').length,
                        docOuterHtmlLength: doc?.documentElement?.outerHTML?.length || 0,
                        docOuterHtmlSample: doc?.documentElement?.outerHTML?.slice(0, 1200) || '',
                    };
                }"""
        )
    response_text = await response.text_content()
    response_html = await response.inner_html()
    return {
        "view": view_name,
        "clicked_selector": clicked_selector,
        "response_text_length": len(response_text or ""),
        "response_text_sample": (response_text or "")[:2000],
        "response_html_sample": (response_html or "")[:2000],
        "monaco_models": monaco_model_text,
        "iframe_payload": iframe_payload,
        "candidates": candidates,
    }


async def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # [修改] 2026-09-20 18:45 原因: Windows 上 Chrome 的 CDP endpoint 可能只綁 localhost 或 IPv6。 說明: 先探測可用 endpoint，再連線，避免腳本綁死單一 host。
    cdp_endpoint = None
    for endpoint in CDP_ENDPOINTS:
        try:
            with urlopen(f"{endpoint}/json/version", timeout=2):
                cdp_endpoint = endpoint
                break
        except OSError:
            continue
    if cdp_endpoint is None:
        raise RuntimeError("No reachable CDP endpoint found.")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_endpoint)
        pages = [page for context in browser.contexts for page in context.pages if not page.is_closed()]
        gemini_pages = [page for page in pages if "gemini.google.com" in page.url]
        if not gemini_pages:
            raise RuntimeError("No Gemini page found.")

        page = gemini_pages[-1]
        responses = page.locator("model-response")
        if not await responses.count():
            raise RuntimeError("No model-response found on the current Gemini page.")
        response = responses.nth(await responses.count() - 1)

        payload = {
            "cdp_endpoint": cdp_endpoint,
            "page_url": page.url,
            "response_count": await responses.count(),
            "views": [
                await inspect_view(response, "code"),
                await inspect_view(response, "preview"),
            ],
        }
        OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote probe results to {OUTPUT_PATH}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())