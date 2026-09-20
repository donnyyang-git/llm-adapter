# LLM Adapter 開發交接

| 欄位 | 值 |
| --- | --- |
| 交接 ID | `LLMA-HO-20260920-125729-03` |
| 建立時間 | `2026-09-20T12:21:30+08:00` |
| 最後更新時間 | `2026-09-20T13:43:56+08:00` |
| 工作區 | `llm_adapter` |
| 接續起點 | 驗證新 Gemini 聊天導頁與歷史 session 載入的 Windows 實機行為 |

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

## 目前狀態

- FastAPI 服務目前使用 `127.0.0.1:8000`，Chrome CDP 使用 `127.0.0.1:9222`。
- 專用 Chrome profile 位於 `data/chrome-profile`，已完成啟動、持久化與重連實機驗證。
- 目前服務已連接專用 Chrome；新程序啟動後仍需呼叫 `POST /api/chrome/start` 重新建立 Playwright CDP 連線。
- 完整測試結果：`47 passed`。另有 2 個來自 Starlette／Python 3.14 的上游棄用警告。
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
- 回答完成條件：停止生成、輸入框可編輯、內容持續穩定。
- 首字 timeout、總回答 timeout、partial 與 failed 保存。

### Session 與儲存

- session／turn Pydantic schema 與狀態驗證。
- JSON、Markdown 同目錄暫存及 `os.replace` 原子寫入。
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
- session ID 保存於瀏覽器 localStorage；頁面重新整理時會載入 persisted session 與全部 turns。
- 最後一輪為 pending 或 generating 時，頁面才建立 EventSource；SSE 斷線後先重新讀取 session，再以最大 15 秒的遞增延遲連線，不會重新送題。
- 回答完成、partial 或 failed 後關閉 SSE 並恢復輸入；可對 active turn 呼叫 recapture。

### 本機歷史與雙模式新對話

- 左側顯示 `Saved conversations` 本機歷史清單，可載入舊 session 查看全部 turns。
- `New local record` 只建立新的本機保存 session，保留目前 Gemini 分頁的上下文。
- `New Gemini chat` 將目前選取的 Gemini tab 導向 `/app` 新聊天入口後建立本機 session。
- 歷史 session 的 tab ID 與目前目標 tab 不一致時，輸入區改為唯讀，避免將舊紀錄續寫至錯誤的 Gemini 對話。

## 實機驗證紀錄

測試 session：`20260920-041729-3e58`

- 第一題要求只回覆 `LLM adapter test OK`，狀態成功轉為 `completed`。
- 第二題詢問上一輪的固定短句，Gemini 正確回覆相同內容。
- 兩輪皆保存至同一份 JSON 與 Markdown。
- 第一輪完成後第二輪可正常送出，證明 tab 鎖已釋放。
- 對話檔案位於 `data/conversations/`，該目錄已由 `.gitignore` 排除。

## 尚未完成

1. 驗證 `New Gemini chat` 的 Gemini 導頁、空白聊天 readiness 與歷史 session 載入的 Windows 實機行為。
2. 長回答、重新擷取與回答期間關閉 tab 的 Windows 實機驗收。

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