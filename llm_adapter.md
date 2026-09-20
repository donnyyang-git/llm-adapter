# 純 Python Gemini 本地對話保存器規劃

## 一、專案目標

建立一個只在本機執行的網站，由 Python 控制專用 Chrome 視窗。使用者可以：

1. 在網站上查看專用 Chrome 目前開啟的分頁。
2. 選擇一個已登入的 Gemini 分頁。
3. 像一般聊天介面一樣逐題傳送問題。
4. 等待並完整擷取 Gemini 的回答，再繼續傳送下一題。
5. 在網站上查看送出的問題、生成進度與收到的回答。
6. 將每次對話同時保存為本機 JSON 與 Markdown 檔案。

主要目的：

- 將雲端聊天內容完整留存在本機。
- 使用 Gemini 網頁版帳號提供的免費使用額度，不呼叫付費 API。

> 注意：網頁版無法提供可信的 token 使用數，因此本系統不計算、不顯示，也不保證可用 token 數量。

## 二、已確認的產品範圍

- 全程使用 Python，不使用 n8n。
- 不呼叫 Gemini API。
- 第一版只支援 Gemini。
- 單機、單使用者，只監聽 `127.0.0.1`。
- 同一時間，同一個 Gemini 分頁只執行一個問題。
- 採一般聊天模式：每次回答完成後，使用者再輸入下一題。
- 每個對話同時保存 JSON 與 Markdown，預設永久保留。
- 第一版會保存 Gemini 回覆的結構化文字、程式碼 artifact 與回覆附圖，但仍不保存使用者上傳附件、語音或完整互動式 Canvas 狀態。
- 不支援區域網路、公網部署、多帳號、多使用者或多工作並行。

## 三、核心技術決策

### 3.1 技術組合

| 層級 | 技術 | 用途 |
| --- | --- | --- |
| 後端 | Python、FastAPI | API、狀態管理及本機服務 |
| 瀏覽器控制 | Playwright for Python | 啟動 Chrome、列出分頁及操作 Gemini |
| 即時事件 | Server-Sent Events（SSE） | 將生成狀態和回答推送至前端 |
| 前端 | 原生 HTML、CSS、JavaScript | 本機聊天介面，無 Node.js 建置流程 |
| 資料驗證 | Pydantic | API 與檔案 schema 驗證 |
| 測試 | pytest、FastAPI TestClient | 單元與 API 測試 |

### 3.2 Chrome 控制策略

Chrome 136 起，`--remote-debugging-port` 不能用於預設使用者資料目錄。因此第一版不接管日常使用中的 Chrome，而是由 Python 啟動一個專用 Chrome：

- 使用獨立且固定的 `data/chrome-profile`。
- 第一次啟動時，由使用者手動登入 Gemini。
- 後續沿用專用 profile 的登入狀態。
- CDP 只綁定 loopback，不對區域網路或公網開放。
- 網站只列出此專用 Chrome 的分頁，不顯示日常 Chrome 分頁。
- Playwright 使用標準 `connect_over_cdp()` 連線；隔離由專用 `user-data-dir` 與 loopback CDP endpoint 保證。

不可複製或直接自動化日常 Chrome profile，避免 Cookie、憑證與瀏覽資料暴露。

## 四、系統架構

```text
Browser UI (localhost)
  |-- HTTP: 啟動 Chrome、刷新分頁、選擇分頁、送出問題
  |-- SSE: 連線狀態、生成狀態、部分回答、完成或錯誤
  v
FastAPI Application
  |-- ChromeManager
  |     |-- 啟動或重連專用 Chrome
  |     `-- 列出及追蹤 tabs
  |-- GeminiAdapter
  |     |-- 定位輸入框和訊息
  |     |-- 傳送單一問題
  |     `-- 判斷回答完成並擷取內容
  |-- ConversationService
  |     |-- 每個 tab 的執行鎖
  |     `-- session / turn 狀態機
  `-- ConversationStore
        |-- JSON 原子寫入
        `-- Markdown 原子寫入
```

## 五、實作階段

### Phase 1：專案骨架與 Chrome 生命週期

1. 建立 Python 專案、設定模型、FastAPI 入口及靜態頁面目錄。
2. 實作 `ChromeManager`：
   - 尋找 Windows 系統 Chrome 執行檔。
   - 使用專用 profile 與本機 CDP port 啟動 Chrome。
   - 偵測已啟動的專用 Chrome並重新連線。
   - 清楚區分 Chrome 未啟動、CDP 無法連線及 Gemini 尚未登入。
3. 取得所有 browser contexts 和 pages。
4. 為每個分頁建立穩定的 tab ID，回傳：
   - 標題。
   - URL。
   - 是否為 Gemini。
   - 是否被選取。
5. 刷新列表時移除已關閉分頁並加入新分頁。

### Phase 2：Gemini 自動化核心

1. 建立 `GeminiAdapter`，集中管理：
   - Gemini URL 判斷。
   - 輸入框定位器。
   - 傳送按鈕定位器。
   - 停止生成按鈕定位器。
   - 使用者與模型訊息定位器。
2. 選擇器優先使用 role、ARIA label 與可見文字，只保留少量已驗證的 CSS fallback。
3. 實作單輪對話狀態機：
   - 記錄送出前的訊息數量與最後訊息。
   - 填入問題並只傳送一次。
   - 確認新的使用者訊息已出現在 Gemini。
   - 等待新的模型訊息出現。
   - 生成期間持續擷取最新內容並發送 SSE 事件。
4. 回答完成必須同時參考：
   - 停止生成按鈕或生成指示器消失。
   - 輸入框恢復可操作。
   - 回答內容連續數秒沒有變動。
5. 設定首字 timeout 與總回答 timeout。
6. 若無法確定問題是否已送出，禁止自動重送，避免 Gemini 收到重複問題。
7. timeout 或錯誤時保存已取得的部分回答與錯誤狀態。
8. 前端提供重新擷取目前回答的操作；重新送題必須由使用者確認。
9. 將回答轉為規範化 Markdown，同時保存純文字，涵蓋：
   - 標題與段落。
   - 有序及無序清單。
   - 程式碼區塊。
   - 表格。
   - 連結與引用。
10. 若 Gemini 回覆包含 code artifact 或 HTML 預覽，需額外：
   - 擷取 Monaco editor model 中的完整程式碼。
   - 區分整體摘要與各 code block 前的局部摘要。
   - 僅對真正的 HTML artifact 提供預覽視圖，避免把一般截圖誤當成可互動預覽。

### Phase 3：對話狀態與本地保存

1. 建立 `ConversationStore` 與固定資料 schema。
2. 每個 session 記錄：
   - session ID。
   - Gemini conversation URL 與標題。
   - tab 資訊。
   - 建立與更新時間。
   - 所有對話輪次。
3. 每輪記錄：
   - turn ID。
   - question。
   - response text。
   - response Markdown。
   - response artifacts（可為多筆，含標題、摘要、語言、code、preview type、image path）。
   - response artifact code（legacy 主 artifact 相容欄位）。
   - response image path（回覆附圖）。
   - sent at。
   - completed at。
   - status：pending、generating、completed、partial 或 failed。
   - error。
4. 每個 session 保存為：

```text
data/conversations/<session-id>.json
data/conversations/<session-id>.md
data/conversations/_artifacts/<session-id>/<turn-id>.png
```

5. 保存時機：
   - 傳送問題前先寫入 pending turn。
   - 收到內容時更新 generating 與部分回答。
   - 完成或失敗時再次落盤。
6. 使用同目錄暫存檔加 `replace` 原子寫入，避免程式意外中止造成檔案損毀。
7. 檔案不得包含 Cookie、CDP endpoint、Google 憑證或其他登入資訊。

JSON 結構示例：

```json
{
  "session_id": "20260920-143022-a1b2",
  "title": "Gemini conversation",
  "conversation_url": "https://gemini.google.com/app/...",
  "created_at": "2026-09-20T14:30:22+08:00",
  "updated_at": "2026-09-20T14:32:05+08:00",
  "turns": [
    {
      "turn_id": "turn-1",
      "question": "第一個問題",
      "response_text": "完整回答",
      "response_markdown": "完整回答",
      "sent_at": "2026-09-20T14:30:30+08:00",
      "completed_at": "2026-09-20T14:31:15+08:00",
      "status": "completed",
      "error": null
    }
  ]
}
```

### Phase 4：FastAPI 服務

規劃下列 API：

| Method | Endpoint | 用途 |
| --- | --- | --- |
| POST | `/api/chrome/start` | 啟動或連接專用 Chrome |
| GET | `/api/chrome/status` | 取得 Chrome 連線狀態 |
| GET | `/api/tabs` | 列出專用 Chrome 分頁 |
| POST | `/api/tabs/select` | 選擇 Gemini 分頁 |
| POST | `/api/sessions` | 建立新對話 session |
| GET | `/api/sessions/{id}` | 載入本機對話 |
| POST | `/api/sessions/{id}/messages` | 傳送一個問題 |
| POST | `/api/sessions/{id}/recapture` | 重新擷取目前 Gemini 回答 |
| GET | `/api/sessions/{id}/events` | 建立 SSE 事件串流 |

API 原則：

- 所有輸入都使用 Pydantic 驗證。
- 有副作用的操作使用 HTTP POST。
- SSE 只負責推送狀態與內容。
- 事件必須包含 session ID 與 turn ID，避免訊息錯配。
- 頁面重新整理後可從本機 JSON 恢復狀態。
- 使用每個 tab 一把 `asyncio.Lock`，拒絕同一 tab 的重複工作。

SSE 事件類型：

- `chrome_status`
- `queued`
- `sent`
- `generating`
- `response_update`
- `completed`
- `partial`
- `failed`

### Phase 5：本機聊天介面

單頁介面包含：

1. Chrome 連線狀態及啟動／重新連線按鈕。
2. 可手動刷新的 tab 清單。
3. Gemini tab 選取狀態。
4. 對話歷史區。
5. 問題輸入框與送出按鈕。
6. 等待回答／停止等待控制。
7. 生成中、完成、部分完成與錯誤提示。
8. 保存成功提示與相對檔名。

只有符合以下條件才允許送出：

- Chrome 已連線。
- 已選擇可辨識的 Gemini tab。
- Gemini 已登入且輸入框可用。
- 該 tab 沒有正在執行的工作。

使用者問題送出後立即顯示於聊天區，模型回答則透過 SSE 持續更新。回答完成前停用再次送出；完成後恢復輸入，即可繼續同一 Gemini 對話。

## 六、錯誤處理

| 情況 | 處理方式 |
| --- | --- |
| 找不到 Chrome | 顯示安裝或設定 Chrome 路徑的提示 |
| CDP 無法連線 | 保留服務運作並提供重新連線 |
| Gemini 未登入 | 要求在專用 Chrome 手動登入 |
| 選取的 tab 已關閉 | 中止工作、保存錯誤並要求重新選取 |
| 找不到輸入框 | 判定 Gemini UI 可能改版，保存診斷資訊 |
| 問題送出狀態不明 | 不自動重送，由使用者確認 |
| 回答 timeout | 保存 partial 回答並提供重新擷取 |
| SSE 中斷 | 前端重新連線並從 session 狀態恢復 |
| 磁碟寫入失敗 | 顯示明確錯誤，不宣稱已保存 |

## 七、安全與限制

1. FastAPI 與 CDP 只能綁定 `127.0.0.1`。
2. 不提供外部網路存取或登入功能。
3. 專用 Chrome profile 不得提交至 Git。
4. `data/` 應加入 `.gitignore`。
5. API 回應與前端不得顯示本機絕對路徑、Cookie 或憑證。
6. Gemini DOM 可能改版，定位器必須集中並可測試。
7. Gemini 可能限制自動化行為；系統不保證永遠可用，也不應以隨機延遲規避平台偵測。
8. 使用者需自行確認相關 Google/Gemini 使用條款是否允許其使用方式。

## 八、預計專案結構

```text
llm_adapter/
|-- pyproject.toml
|-- README.md
|-- .gitignore
|-- llm_adapter.md
|-- src/
|   `-- llm_adapter/
|       |-- __init__.py
|       |-- main.py
|       |-- config.py
|       |-- models.py
|       |-- chrome_manager.py
|       |-- conversation_service.py
|       |-- storage.py
|       |-- routes.py
|       |-- adapters/
|       |   |-- __init__.py
|       |   `-- gemini.py
|       `-- static/
|           |-- index.html
|           |-- app.js
|           `-- styles.css
|-- tests/
|   |-- fixtures/
|   |-- test_chrome_manager.py
|   |-- test_gemini_adapter.py
|   |-- test_storage.py
|   |-- test_conversation_service.py
|   `-- test_api.py
`-- data/
    |-- chrome-profile/
    `-- conversations/
```

## 九、測試與驗收

### 9.1 自動測試

1. tab ID 映射、分頁新增與關閉。
2. session 與 turn schema 驗證。
3. JSON 和 Markdown 原子寫入及重新載入。
4. pending、generating、completed、partial、failed 狀態轉換。
5. timeout 後保存部分回答。
6. 使用固定 Gemini HTML fixtures 測試定位器 fallback。
7. 標題、清單、程式碼、表格及引用的 Markdown 擷取。
8. FastAPI 正常與錯誤回應契約。
9. 同一 tab 重複送出時必須拒絕第二個請求。
10. 失敗或不確定狀態不得自動重送問題。

### 9.2 Windows 實機驗收

1. 第一次啟動專用 Chrome 並登入 Gemini。
2. 網頁能列出專用 Chrome 的多個 tabs。
3. 選擇 Gemini tab 並傳送第一題。
4. 第一題回答完整後才可傳送第二題。
5. 第二題沿用同一 Gemini 對話上下文。
6. 比對 Gemini 畫面、前端、JSON 與 Markdown，內容不得截斷或錯配。
7. 驗證長回答、程式碼、清單、表格及引用。
8. 回答期間關閉 tab，確認工作停止並保存錯誤。
9. 模擬回答 timeout，確認保存 partial 而且不重送。
10. 重啟服務後可恢復既有對話與本機檔案。
11. 確認服務只監聽 localhost，日常 Chrome tabs 不會出現在清單。

## 十、實作順序與完成標準

### Milestone 1：Chrome 連線原型

- 能啟動或重新連接專用 Chrome。
- 能從 Python 列出 tabs。
- 能辨識 Gemini tab。

### Milestone 2：單輪 Gemini POC

- 能安全送出一題。
- 能等待完成並擷取完整文字。
- 失敗時不會重複送題。

### Milestone 3：本地保存

- 每輪狀態即時寫入 JSON 與 Markdown。
- 中途停止後仍可讀取 pending 或 partial 資料。

### Milestone 4：聊天網站

- 能選 tab、逐題聊天並即時查看回答。
- 頁面重新整理後可恢復對話。

### Milestone 5：可靠性與文件

- 自動測試通過。
- Windows 實機驗收通過。
- README 包含安裝、首次登入、操作、關閉、資料位置及故障排除。

只有在上述五個里程碑全部達成後，第一版 MVP 才算完成。

## 十一、目前執行狀態

### 已完成

- 建立 `src` layout、Python 套件設定、FastAPI 入口與測試環境。
- 建立集中式 localhost、CDP port、Chrome profile 與資料目錄設定。
- 實作 Windows Chrome 路徑偵測、專用 profile 啟動參數及 CDP 重連。
- 實作 tab 列舉、穩定 tab ID、Gemini 網域辨識與 Gemini tab 選取。
- 提供 `/api/chrome/status`、`/api/chrome/start`、`/api/tabs` 與 `/api/tabs/select`。
- 完成 ChromeManager 與 API 的隔離測試；測試不會啟動真實 Chrome。
- 完成 Windows 實機啟動與重連，確認 CDP 僅監聽 `127.0.0.1` 且專用 profile 已落盤。
- 以 CDP target 清單作為 tab 列舉權威來源，修正既有 Playwright 連線漏掉外部新增分頁的情況。
- 建立唯讀 `GeminiAdapter`，並在實際 Gemini 頁確認輸入框存在、已登入且可編輯。
- 建立 session／turn schema，以及 JSON、Markdown 同目錄暫存與原子替換儲存。
- 提供 session 建立、載入與 message API；未選 Gemini tab、session 不存在及 tab busy 均有明確錯誤碼。
- 實作每個 tab 的 `asyncio.Lock`，從送題前持續鎖定到 turn 完成或失敗。
- 實作 pending-first 狀態機：磁碟確認保存 `pending` 後才呼叫 sender，失敗不自動重送。
- 服務重啟後若最後一輪仍為 `pending` 或 `generating`，會拒絕新題，等待後續重新擷取流程處理。
- 建立固定 Gemini 回答 fixture，驗證標題、清單、程式碼、表格、引用與連結的 Markdown 擷取。
- 實作背景回答監看，以停止生成、輸入框可用及內容穩定時間共同判定完成。
- 實作首字與總回答 timeout；已有內容時保存 partial，其他錯誤也保留已擷取內容。
- 回答完成、partial 或 failed 後會更新 JSON／Markdown 並釋放 tab 鎖。
- 完成兩輪 Gemini 實機驗收，確認同一對話上下文、完整擷取、雙格式保存及輪次間鎖釋放。

### 下一個執行切片

1. 實作重新擷取 API，處理服務重啟後遺留的 `pending` 或 `generating` turn。
2. 建立 session 事件 broker 與 SSE endpoint，推送 queued、sent、generating、response update 與完成狀態。
3. 建立原生 HTML、CSS、JavaScript 聊天介面，串接 Chrome、tab、session 與 SSE。
4. 驗證頁面重新整理後恢復 session，以及 SSE 中斷後重新連線。
5. 補齊長回答、tab 關閉及服務重啟的 Windows 實機驗收。

Created memory file /memories/session/plan.md

## 計畫：純 Python Gemini 本地對話保存器

建立本機 FastAPI 網站，由 Playwright 控制一個**專用 Chrome 視窗**。網頁列出該視窗的分頁，選擇 Gemini 後即可像一般聊天介面逐題對話；問題與完整回答同步顯示，並保存成 JSON 與 Markdown。

**架構**
- 後端：Python、FastAPI、Playwright
- 前端：原生 HTML、CSS、JavaScript，不需要 Node.js
- 瀏覽器：獨立 Chrome profile，首次手動登入 Gemini
- 即時更新：HTTP 發送指令，SSE 推送回答與狀態
- 儲存：每個對話一份 JSON 與 Markdown
- 服務範圍：只監聽 `127.0.0.1`

**實作步驟**
1. 建立 Python 專案、設定、FastAPI 啟動入口與靜態頁面。
2. 實作 `ChromeManager`：
   - 用非預設 `user-data-dir` 啟動 Chrome。
   - 保留 Gemini 登入狀態。
   - 列出此專用 Chrome 的 tabs。
   - 處理 Chrome 未啟動、連線中斷與 tab 關閉。
3. 實作 `GeminiAdapter`：
   - 辨識 Gemini tab。
   - 定位輸入框、送出按鈕、停止生成按鈕和最新回答。
   - 所有 Gemini DOM 選擇器集中管理，方便日後修復。
4. 建立單輪對話狀態機：
   - 記錄送出前的訊息數量。
   - 問題只送出一次。
   - 等待新的 Gemini 回答。
   - 使用「停止按鈕消失、輸入框恢復、內容穩定數秒」共同判斷完成。
   - 完成後才允許使用者送下一題。
5. 擷取回答：
   - 保存純文字與 Markdown。
   - 支援標題、清單、表格、程式碼與引用連結。
   - timeout 時保存部分回答，不自動重送問題。
6. 建立本地儲存：
   - 每次送出前先寫入 `pending` 狀態。
   - 生成過程更新內容。
   - 完成或失敗後再次寫入。
   - 使用原子寫入，避免程式中止造成檔案損毀。
7. 建立聊天介面：
   - Chrome 連線狀態。
   - tab 列表與重新整理。
   - 對話歷史與輸入框。
   - 生成中、完成、失敗和保存狀態。
   - 頁面重新整理後恢復目前對話。
8. 補上 Windows 啟動入口、README 與自動化測試。

**預計檔案**
- `pyproject.toml`
- `src/llm_adapter/main.py`
- `src/llm_adapter/chrome_manager.py`
- `src/llm_adapter/adapters/gemini.py`
- `src/llm_adapter/storage.py`
- `src/llm_adapter/models.py`
- `src/llm_adapter/routes.py`
- `src/llm_adapter/static/index.html`
- `src/llm_adapter/static/app.js`
- `src/llm_adapter/static/styles.css`
- `tests/`
- `README.md`

**驗證**
1. 測試 Chrome 啟動、重連與 tab 列表。
2. 連續手動送出兩題，確認維持同一 Gemini 對話。
3. 比對 Gemini 畫面、網頁、JSON、Markdown，確認長回答與程式碼沒有截斷。
4. 測試 timeout、關閉 tab、Chrome 中斷及服務重啟。
5. 確認失敗時不會重複送出問題。

**已確認範圍**
- 不使用 n8n，也不呼叫 Gemini API。
- 第一版只支援 Gemini、單機、單使用者和單一執行工作。
- 不控制日常 Chrome，只控制專用 Chrome 內的分頁。
- 「免費 token」實際上是 Gemini 網頁版帳號額度，程式無法可靠取得或顯示 token 數。
- 第一版不保存圖片、附件、語音或完整網頁 HTML。

由於目前沒有開啟 VS Code workspace，系統無法把計畫寫入 `/memories/session/plan.md`；以上是完整可交付的實作計畫。