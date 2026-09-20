# LLM Adapter 開發交接

| 欄位 | 值 |
| --- | --- |
| 交接 ID | `LLMA-HO-20260920-125729-03` |
| 建立時間 | `2026-09-20T12:21:30+08:00` |
| 最後更新時間 | `2026-09-20T22:43:00+08:00` |
| 工作區 | `llm_adapter` |
| 接續起點 | Gemini artifact 多片段 / 預覽切換整理完成，待 commit/push |

## 開發歷程

| 時間 | 階段 | 結果 |
| --- | --- | --- |
| 2026-09-20 | 專案初始化 | 建立 Python `src` layout、FastAPI 入口、設定與測試環境。 |
| 2026-09-20 | Chrome 原型 | 完成專用 profile、loopback CDP、tab 列舉、選取及重連。 |
| 2026-09-20 | 分頁實機修正 | 發現 Playwright 既有連線漏掉外部新增 target，改以 CDP `/json/list` 作為權威清單。 |
| 2026-09-20 | Gemini 探測 | 完成登入／輸入框 readiness、target 到 `Page` 解析及單次送題。 |
| 2026-09-20 | 本地儲存 | 完成 session／turn schema 與 JSON、Markdown 原子寫入。 |
| 2026-09-20 | 狀態機 | 完成 pending-first、每 tab 鎖、防重送與完成後釋放。 |
| 2026-09-20 | 回答監看 | 完成 DOM 擷取、Markdown 轉換、穩定判定、timeout、partial／failed 保存。 |
| 2026-09-20T12:17+08:00 | 兩輪實機驗收 | 同一 Gemini 對話兩輪皆完成，內容與 JSON／Markdown 一致。 |
| 2026-09-20T12:21+08:00 | 最終回歸 | `35 passed`，VS Code 診斷為零；服務與專用 Chrome 已連線。 |
| 2026-09-20T12:25:10+08:00 | Event broker、SSE、recapture | 完成 session 事件發布、SSE endpoint 與重啟後 active turn 重新擷取；`43 passed`，VS Code 診斷為零。 |
| 2026-09-20T12:36:58+08:00 | 原生聊天頁面與恢復 | 完成根路由、原生 HTML/CSS/JavaScript 聊天介面、session 恢復與 SSE 遞增延遲重連；`45 passed`，VS Code 診斷為零。 |
| 2026-09-20T12:57:29+08:00 | 本機歷史與雙模式新對話 | 完成 session 歷史清單、重新載入、`New local record` 與 `New Gemini chat`；新 Gemini 模式會導向 `/app` 後建立本機 session，歷史 session 與目前 tab 不符時採唯讀保護；`47 passed`。 |
| 2026-09-20T13:32:21+08:00 | README 教學整理 | 將安裝、首次登入、日常啟動、資料備份、設定與故障排除集中至 README，保留既有介面操作說明。 |
| 2026-09-20T13:43:56+08:00 | README 跨電腦指南 | 補充 Git clone/pull、虛擬環境重建、跨電腦對話搬遷與 Git 權限、埠號、專用 profile 等常見問題。 |
| 2026-09-20T14:00:00+08:00 | Chrome profile lock 清理 | 修正 `ChromeManager.start()` 啟動前清理專用 profile 的 stale lockfile；避免 `lockfile`／`SingletonLock` 殘留導致 Chrome 直接退出。 |
| 2026-09-20T14:06:29+08:00 | Chrome 啟動診斷 | 新增 JSONL trace 與 Chrome stdout/stderr log；timeout 與提前退出會顯示相對 log 路徑及 exit code，ChromeManager 聚焦測試 `9 passed`、完整測試 `48 passed`。 |
| 2026-09-20T15:10:00+08:00 | IPv4/IPv6 loopback fallback | 修正 CDP 連線邏輯，依序嘗試設定值、`127.0.0.1`、`localhost`、`[::1]`，兼容 Windows Chrome 同時綁定 IPv4/IPv6 的實際情況。 |
| 2026-09-20T15:11:00+08:00 | Copy 原文 UX 設計 | 明確新增「Copy 原文」按鈕需求：每則訊息可複製原始問答文字或 Markdown，方便將輸出貼回其他工具；UI 需保留單一訊息操作、不干擾整個 session 複製。 |
| 2026-09-20T15:40:00+08:00 | Copy 原文 UX 實作 | 在訊息卡片加入 `Copy` 按鈕，`Gemini` 卡片優先複製 `response_markdown`，`You` 卡片複製原始輸入文字；保留純文字顯示不改動 session 儲存格式。 |
| 2026-09-20T16:05:00+08:00 | 前端狀態卡死排查 | 實際排查到 `Connect Chrome` 點擊後 UI 停留在 `Checking Chrome...`，但 Chrome 已在 `9222` 監聽；查證顯示 backend 狀態與 start endpoint 實際已連接，問題集中在前端 stale state 與缺失的 `refreshChromeAndTabs` helper。 |
| 2026-09-20T16:18:00+08:00 | Markdown 時間戳補齊 | 在 `ConversationStore.render_markdown()` 補上 `Sent:` 與 `Completed:`，讓每個 turn 可直接追蹤發送與完成時間，不改變 JSON 儲存格式。 |
| 2026-09-20T16:40:00+08:00 | Gemini tab 復原入口 | `New Gemini chat` 新增無選取 tab 時的 fallback：若目前沒有可用 Gemini tab，會在專用 Chrome 內直接開啟新的 Gemini 分頁並重新選取；前端同步保留按鈕可用與空狀態引導。 |
| 2026-09-20T17:05:00+08:00 | session rebind 與空狀態強化 | 新增 `Rebind session`：當舊 session 被切到另一個 Gemini tab 時，可手動把它接回目前選取的 tab；前端空狀態與 composer 文案改成會提示「已關閉 tab / 可重新開啟」。 |
| 2026-09-20T17:20:00+08:00 | Chrome connected 但 browser 未重建 | 補上 `_ensure_browser_connected()`：當右上顯示 connected 但 Playwright browser 仍是空的時，`list_tabs` / `get_page` / `New Gemini chat` 會先自動重連再執行，避免使用者看到 connected 卻無法開新 tab。 |
| 2026-09-20T18:05:00+08:00 | Gemini 回覆附圖落地 | 新增回覆卡片 PNG 擷取、`/artifacts` 靜態掛載與 `data/conversations/_artifacts/<session>/<turn>.png` 儲存流程，讓 UI 可保留 Gemini 原始視覺畫面。 |
| 2026-09-20T18:35:00+08:00 | artifact 診斷腳本與前端切換器 | 新增 `scripts/probe_gemini_artifact.py` 與 `scripts/README.md`，並在前端加入 `程式碼 / 預覽` artifact 面板。 |
| 2026-09-20T18:55:00+08:00 | Monaco 完整程式碼擷取 | 確認 Gemini code artifact 來源是 Monaco editor model；改由 `window.monaco.editor.getModels()` 擷取完整 code，避免 HTML/JS 被截斷。 |
| 2026-09-20T19:20:00+08:00 | 多 artifact 結構化保存 | 新增 `ResponseArtifact`、`response_artifacts[]`，支援一則回覆對應多個程式片段，並保留 legacy `response_artifact_code` / `response_image_path` 相容層。 |
| 2026-09-20T19:35:00+08:00 | artifact ID 與後端防禦修正 | `turn_automation` 送出的 artifact payload 會補 `artifact_id`；service 端也會自動補齊缺失 ID，避免 runtime validation error。 |
| 2026-09-20T19:55:00+08:00 | 摘要與預覽 UX 收斂 | 保留 Gemini 原本 1/2/3 摘要、支援每個 artifact 的局部摘要、點左側 tab 自動對應 session，並限制只有真正 HTML artifact 才顯示預覽，移除空白/假 preview。 |

## 目前狀態

- FastAPI 服務目前使用 `127.0.0.1:8000`；Chrome CDP 連線會依序嘗試設定 host、`127.0.0.1`、`localhost`、`[::1]`，兼容 IPv4/IPv6 loopback binding。
- 專用 Chrome profile 位於 `data/chrome-profile`，已完成啟動、持久化與重連實機驗證，並會在啟動前清理 stale profile lock。
- 目前服務已連接專用 Chrome；新程序啟動後仍需呼叫 `POST /api/chrome/start` 重新建立 Playwright CDP 連線。
- 先前完整測試結果：`57 passed`。本波 artifact 相關回歸測試結果：`36 passed, 2 warnings`。
- VS Code 對 `src/` 與 `tests/` 無診斷錯誤。

## 已完成

### Chrome 與分頁

- Windows Chrome 執行檔偵測。
- 使用獨立 profile 與 loopback CDP 啟動或重連。
- 透過 `/json/list` 列舉頁面，以 CDP target ID 作為穩定 tab ID。
- Gemini 網域辨識、tab 選取，以及 target ID 到 Playwright `Page` 的解析。
- 已實機驗證新增、刷新、關閉分頁與服務重啟後重連。

### Gemini 自動化

- 登入／輸入框 readiness 探測。
- 送題只執行一次 `fill()` 與一次 `Enter`，不自動重送。
- 送題前記錄模型訊息 baseline，只擷取新回答。
- 使用 `model-response > message-content` 擷取純文字與 HTML。
- 使用 `markdownify` 轉換標題、清單、程式碼、表格、引用與連結。
- 會先切換 Gemini 的 `預覽` / `程式碼` 視圖抓摘要，再從 Monaco model 擷取完整 artifact code。
- 支援從單一回答中拆出多個 code block artifact，並保存個別摘要。
- 回答完成條件：停止生成、輸入框可編輯、內容持續穩定。
- 首字 timeout、總回答 timeout、partial 與 failed 保存。

### Session 與儲存

- session／turn Pydantic schema 與狀態驗證。
- JSON、Markdown 同目錄暫存及 `os.replace` 原子寫入。
- 每輪可保存 `response_artifacts[]`、legacy artifact code 與回覆附圖路徑。
- 附圖實體檔落在 `data/conversations/_artifacts/<session>/<turn>.png`。
- pending-first：確認 `pending` 已落盤後才呼叫 Gemini sender。
- 每個 tab 使用 `asyncio.Lock`，完成、partial 或 failed 後才釋放。
- 服務重啟後若最後一輪仍為 `pending`／`generating`，禁止直接重送。
- 可列出所有本機保存 session，並依最後更新時間由新到舊排序。

### API

- `POST /api/chrome/start`
- `GET /api/chrome/status`
- `GET /api/tabs`
- `POST /api/tabs/select`
- `POST /api/sessions`
- `GET /api/sessions`
- `POST /api/sessions/new-gemini`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/messages`
- `POST /api/sessions/{session_id}/recapture`
- `GET /api/sessions/{session_id}/events`

### Event broker、SSE 與重新擷取

- session-scoped event broker，訂閱結束會清理 queue，慢速訂閱者滿載時丟棄最舊事件。
- queued、sent、generating、response_update、completed、partial、failed 均在對應狀態保存後發布。
- SSE 使用標準 `event`／`data` frame，資料包含 session ID、turn ID、狀態、回答內容與錯誤。
- SSE 每 15 秒發送 keep-alive，並停用代理緩衝與快取。
- recapture 會重新取得 persisted active turn 的 tab 鎖，以目前最後一則 Gemini 模型訊息作為擷取目標。
- recapture 不呼叫 sender、不重新送題，沿用既有完整、partial、failed 保存與鎖釋放流程。

### 原生聊天頁面、恢復與重連

- 根路由提供原生 HTML/CSS/JavaScript 單頁聊天介面，靜態資源掛載於 `/static`。
- 頁面顯示 Chrome 連線狀態、Gemini tab 清單、選取狀態、對話歷史、生成中與錯誤訊息。
- `Gemini` 訊息可顯示多個 artifact 卡片，包含個別摘要與 `程式碼` / `預覽` 切換。
- 左側 Gemini tab 點選後，若能對應到同一 `tab_id` 的本機 session，右側會自動切換載入該 session。
- session ID 保存於瀏覽器 localStorage；頁面重新整理時會載入 persisted session 與全部 turns。
- 最後一輪為 pending 或 generating 時，頁面才建立 EventSource；SSE 斷線後先重新讀取 session，再以最大 15 秒的遞增延遲連線，不會重新送題。
- 回答完成、partial 或 failed 後關閉 SSE 並恢復輸入；可對 active turn 呼叫 recapture。

### 本機歷史與雙模式新對話

- 左側顯示 `Saved conversations` 本機歷史清單，可載入舊 session 查看全部 turns。
- `New local record` 只建立新的本機保存 session，保留目前 Gemini 分頁的上下文。
- `New Gemini chat` 將目前選取的 Gemini tab 導向 `/app` 新聊天入口後建立本機 session；若當下沒有可用 Gemini tab，會直接在專用 Chrome 裡開啟新的 Gemini 分頁，再建立本機 session。
- `Rebind session` 讓已載入但屬於舊 Gemini tab 的 session，能在使用者選到新的 Gemini tab 後明確重新綁定。
- 若右上顯示 `Chrome connected`，但 `New Gemini chat` / `Rebind session` 暫時提示 `Chrome is not connected`，系統會先嘗試重建 Playwright browser 再繼續；若仍失敗，使用者可按 `Refresh` 或 `Reconnect Chrome`。
- 歷史 session 的 tab ID 與目前目標 tab 不一致時，輸入區改為唯讀，避免將舊紀錄續寫至錯誤的 Gemini 對話。

## 實機驗證紀錄

測試 session：`20260920-041729-3e58`

- 第一題要求只回覆 `LLM adapter test OK`，狀態成功轉為 `completed`。
- 第二題詢問上一輪的固定短句，Gemini 正確回覆相同內容。
- 兩輪皆保存至同一份 JSON 與 Markdown。
- 第一輪完成後第二輪可正常送出，證明 tab 鎖已釋放。
- 對話檔案位於 `data/conversations/`，該目錄已由 `.gitignore` 排除。

## 狀態總覽

### 已完成

- Chrome profile stale lock 清理與重設修正已落地。
- Chrome CDP 連線已修正 IPv4/IPv6 loopback fallback，兼容 `[::1]` / `127.0.0.1` 雙綁定情況。
- 訊息卡片單則 `Copy` 按鈕已完成，`Gemini` 回覆優先複製 `response_markdown`，`You` 訊息複製原始輸入文字。
- `Connect Chrome` 卡在 `Checking Chrome...` 的前端狀態已定位並修正：透過實際端點驗證確認 Chrome 已啟動，問題根因為 stale UI 狀態與缺失的 `refreshChromeAndTabs` helper；重新載入前端後可正常刷新狀態。
- 每個 turn 的 Markdown 輸出已補上 `Sent:` 與 `Completed:` 時間戳，便於分析與交接。
- Gemini artifact 現在會保存完整程式碼、個別摘要與多段 code block 結構，並可在前端逐一切換。
- Preview UI 已限制為真正 HTML artifact 才顯示，避免一般程式碼回答出現空白或誤導性的預覽框。
- 專案測試已驗證：`57 passed`，0 failed；相關 UI 改動未造成回歸。

### 待驗證

1. `New Gemini chat` 的 Gemini 導頁、空白聊天 readiness 與歷史 session 載入的 Windows 實機行為。
2. 多 artifact 回覆在真實 Gemini 畫面下的長流程驗收，包含切換 tab、自動載入 session 與 HTML preview。
3. 讀取/寫入 `data/chrome-profile` 在不同 Windows 環境下的穩定性，確認不受日常 Chrome 影響。

### 下一步

1. 在 Windows 實機上執行 `New Gemini chat` / history reload / readonly 防護的驗收測試。
2. 進行長回答、recapture、tab 關閉與重新整理的端到端驗收。
3. 若有必要，再補齊 README 的進階故障排除與使用者日誌收集說明。

## 已完成：Copy 原文

### 目的

- 讓使用者可直接複製單則訊息，不必開啟 `.md` 檔或整個 session。
- 針對長回覆、程式碼塊、表格與清單內容特別方便，符合本專案「保存原始內容」的設計目標。
- 讓訊息區兩側的 `You` / `Gemini` 卡片都具備可複製性，提升工作流快速貼回其他工具的效率。

### 設計方向

- 每則訊息卡片右上角放置 `Copy` 按鈕。
- `You` 卡片複製原始輸入文字。
- `Gemini` 卡片優先複製 `response_markdown`，若空白則 fallback 到 `response_text`。
- 點擊後顯示暫時狀態，例如 `Copied`，1 秒後恢復原始文案。
- 不在訊息卡片上直接以 HTML 渲染 Markdown，而是保留純文字/Markdown 原文輸出，避免行為與保存格式不一致。
- 若之後需要可選擇「Copy all session」，可在整個 session header 額外補一個次級操作。

### 實際交付

- 前端 UI：`Copy` 按鈕、複製成功狀態、單則訊息複製已完成。
- 資料：使用既有 `turn.question`、`turn.response_markdown`、`turn.response_text` 等欄位，未新增新存檔格式。
- 實作方式：保留既有 `body.textContent` 文字顯示，僅在訊息元件上新增 copy action，未破壞目前的 session 渲染流程。
- 驗證：專案測試 `51 passed`，0 failed；本次 UI 變更未帶來回歸。

## 下一步建議順序

1. 實機驗證新 Gemini 聊天導頁、歷史 session 載入與唯讀保護。
2. 完成 recapture、tab 關閉、timeout、重新整理與長回答實機驗收。
3. 補齊 README 的首次登入、資料位置、關閉與故障排除說明。

## 常用命令

```powershell
# 安裝
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 測試
.\.venv\Scripts\python.exe -m pytest -q

# 啟動
.\.venv\Scripts\python.exe -m uvicorn llm_adapter.main:app --host 127.0.0.1 --port 8000
```

API 文件：`http://127.0.0.1:8000/docs`

## 主要程式位置

- `src/llm_adapter/main.py`：FastAPI 組裝、路由與背景 task。
- `src/llm_adapter/chrome_manager.py`：Chrome/CDP/tab 管理。
- `src/llm_adapter/adapters/gemini.py`：Gemini selectors、送題與回答擷取。
- `src/llm_adapter/response_monitor.py`：穩定判定與 timeout。
- `src/llm_adapter/turn_automation.py`：送題與背景監看的協調。
- `src/llm_adapter/conversation_service.py`：狀態機、鎖與持久化流程。
- `src/llm_adapter/event_broker.py`：session 事件發布與訂閱生命週期。
- `src/llm_adapter/storage.py`：JSON／Markdown 原子儲存。
- `src/llm_adapter/static/`：原生聊天頁面、樣式、session 恢復與 SSE 重連。
- `llm_adapter.md`：完整產品規劃與驗收標準。