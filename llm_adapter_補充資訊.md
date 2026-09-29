依據您提供的簡報 HTML 原始碼，為您整理出的專案作品資訊如下：

### 1. 作品名稱

**純 Python Gemini 本地對話保存器**（GitHub Copilot 創意競賽作品）

### 2. 作品簡介

本作品是一套完全掌控個人 AI 對話紀錄的自動化擷取與本機結構化備份方案。系統採用全純 Python 技術棧（FastAPI + Playwright），透過自動化接管專用 Chrome 視窗調用 Gemini 網頁版，無需付費 API Key 即可免除高昂費用。對話過程經由 SSE 即時串流推送，並採用原子寫入機制將對話內容同步落盤為 JSON 與 Markdown 雙格式，完整保留程式碼片段（Artifacts）、表格與語法格式，打造高效且兼具資料私密性的個人 AI 知識庫。

### 3. 作品特色

* **輕量安全與雙格式備份**：採用全純 Python（FastAPI + Playwright）技術棧，免除複雜前端建置；僅監聽 `127.0.0.1` 確保單機安全，並透過「原子寫入」機制將對話同步保存為結構化 JSON（含完整元數據）與 Markdown 檔（方便筆記軟體如 Obsidian 調用）。
* **獨立 Chrome Profile 與資源隔離**：啟動專屬 Chrome 視窗與獨立 Profile，不存取或洩漏使用者日常瀏覽器的 Cookies/憑證；針對同一分頁設置互斥鎖與隊列機制，確保對話傳送與擷取的穩定性。
* **精準 DOM 擷取與 SSE 即時串流**：利用 ARIA Label/Role 選擇器精準監控 Gemini UI 狀態，能完整擷取 Monaco Editor 中的 Code Artifacts，並透過 SSE（Server-Sent Events）將生成內容即時推送至前端介面。

### 4. 如何應用

1. **啟動與登入**：執行服務啟動專用 Chrome 瀏覽器並載入獨立 Profile，由使用者手動登入 Gemini 帳號。
2. **選取分頁與對話**：透過原生 HTML Web UI 選取目標 Gemini 分頁並送出提問，系統會鎖定分頁並透過 SSE 即時接收動態回答。
3. **自動結構化歸檔**：回答生成完畢後，系統會自動將提問、回答與程式碼（Artifacts）完整備份至本機 `data/conversations/` 目錄下的 JSON 與 `.md` 檔案，方便後續搜尋、個人知識管理與匯入筆記系統。