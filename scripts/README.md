# scripts

這個目錄放可重用的分析與診斷腳本。

- `probe_gemini_artifact.py`: 連到目前已啟動的 Gemini Chrome/CDP，檢查最後一則 Gemini artifact 在「程式碼 / 預覽」視圖下的 DOM 與可擷取文字情況，輸出到 `data/logs/gemini-artifact-probe.json`。
- `generate_mcp_2026_slides.py`: 根據外部 Markdown 文章整理好的 10 頁大綱，輸出單檔 `mcp_2026_slides.html`，支援按鈕與鍵盤換頁瀏覽。