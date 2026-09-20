# llm-adapter

只在本機執行的 Gemini 對話保存器。詳細範圍與里程碑請參閱 [llm_adapter.md](llm_adapter.md)。

## 開發

```powershell
python -m pip install -e ".[test]"
llm-adapter
```

服務預設監聽 `http://127.0.0.1:8000`。

## 介面導覽與操作流程

開啟本機網址後，依下列步驟操作：

1. 確認右上角顯示綠點與 `Chrome connected`。這代表服務已連上由本專案管理的專用 Chrome 視窗，可以操作其中的 Gemini 分頁。
2. 在左側 `Gemini tabs` 選取要接收提問的 Gemini 分頁。卡片中的標題與網址可用來確認目標；按 `Refresh` 可重新掃描專用 Chrome 目前開啟的 Gemini 分頁。
3. 依需求選擇 `New local record` 或 `New Gemini chat`；也可直接在下方輸入問題並按 `Send question`，系統會建立本機對話紀錄。
4. 等待 Gemini 產生回覆。產生期間請不要再送出下一題；完成後才可繼續輸入下一題。

### Chrome connected / Reconnect Chrome

`Chrome connected` 與綠點表示連線正常。`Reconnect Chrome` 用來要求服務重新連線專用 Chrome；若 Chrome 尚未啟動，按鈕會顯示為 `Connect Chrome` 並啟動它。

本專案不會接管日常使用的 Chrome，而是使用 `data/chrome-profile` 的專用設定檔。第一次使用時，請在此專用 Chrome 視窗內登入 Gemini；登入狀態會保留在該設定檔。

### Gemini tabs

左側高亮的卡片是目前選定的目標分頁。切換分頁會清除網頁上目前選取的本機 session，避免將下一個問題傳送到錯誤的 Gemini 頁面。

### New local record、New Gemini chat 與 SESSION

- `New local record` 只會建立新的本機 session，並繼續使用目前 Gemini 分頁的對話上下文。適合要分開保存不同工作階段、但仍希望 Gemini 記得前文時使用。
- `New Gemini chat` 會將目前選取的分頁導向 Gemini 的新聊天頁，再建立新的本機 session。適合要同時切開 Gemini 上下文與本機保存紀錄時使用。

畫面上的 `SESSION 20260920-...` 是本機保存紀錄的 ID，不是 Gemini 帳號或 Gemini 對話 ID。

每個 session 會保存為以下兩個檔案：

```text
data/conversations/<session-id>.json
data/conversations/<session-id>.md
```

`.json` 保存結構化資料與狀態；`.md` 便於直接閱讀、搜尋或備份。重新整理控制台時，瀏覽器會嘗試載入上次開啟的本機 session。

### Saved conversations

左側 `Saved conversations` 會列出所有保存在本機的 session，依最後更新時間由新到舊排列。點選項目可重新查看舊對話，不會刪除或覆寫原始紀錄。

若開啟的歷史紀錄不是目前選取 Gemini 分頁所屬，畫面會以唯讀方式呈現，不能直接送出下一題。請先選回原本的 Gemini 分頁，或建立新的本機紀錄，避免把舊紀錄接到錯誤的 Gemini 對話。

### TURN 與回覆狀態

每一個 `TURN` 是一組「你的問題」與「Gemini 回覆」。例如 `TURN-1` 是本機 session 的第一輪，後續問題會依序成為 `TURN-2`、`TURN-3`。

每輪可能有下列狀態：

- `pending`：問題已建立，正準備送至 Gemini。
- `generating`：Gemini 正在產生回答，畫面會持續更新並保存已擷取的內容。
- `completed`：回答已完整擷取並保存，可以送出下一題。
- `partial`：等待逾時或發生可恢復問題，已保存目前擷取到的部分回答。
- `failed`：此輪失敗，畫面會顯示錯誤訊息。

### Recapture response

`Recapture response` 用於問題已送到 Gemini、但控制台中斷或未完整擷取回答時，重新從目前 Gemini 頁面擷取最新回答。它只會在最新一輪仍處於 `pending` 或 `generating` 時啟用；當狀態為 `completed` 時按鈕會停用。

此功能不會再次送出問題，因此可避免 Gemini 收到重複提問。
