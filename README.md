# LLM Adapter

只在本機執行的 Gemini 對話保存器。它會啟動一個使用獨立設定檔的專用 Chrome，讓你在本機網頁介面中選擇 Gemini 分頁、逐題提問，並將每個對話保存為 JSON 與 Markdown。

服務只監聽 `127.0.0.1`，不會讀取或接管日常使用的 Chrome。完整產品範圍與技術規劃請參閱 [llm_adapter.md](llm_adapter.md)。

## 需求

- Windows 10 或更新版本。
- Python 3.11 以上。
- 已安裝 Google Chrome。
- 可登入的 Google/Gemini 帳號。

## 安裝

在專案根目錄執行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

若 PowerShell 拒絕執行啟用指令，請先在目前終端機執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

## 在另一台電腦安裝或更新

### 第一次下載

先安裝 Git、Python 3.11 以上版本與 Google Chrome，然後在要存放專案的資料夾執行：

```powershell
git clone https://github.com/donnyyang-git/llm-adapter.git
cd llm-adapter
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

接著依照「首次使用」啟動服務。每一台電腦都要在自己的專用 Chrome 視窗登入 Gemini；不要複製另一台電腦的 `data/chrome-profile`，其中可能包含登入資訊。

### 取得後續更新

先停止正在執行的服務，再進入專案目錄執行：

```powershell
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

若 `git pull` 顯示本機檔案有未提交變更，先使用 `git status` 確認內容。保留變更時應先自行提交，或暫存後再更新：

```powershell
git stash
git pull origin main
git stash pop
```

若不需要本機程式碼修改，請先手動檢查後移除或還原那些變更；不要直接刪除 `data/`。

## 首次使用

1. 啟動服務：

	```powershell
	.\.venv\Scripts\python.exe -m uvicorn llm_adapter.main:app --host 127.0.0.1 --port 8000
	```

2. 在瀏覽器開啟 `http://127.0.0.1:8000`。
3. 按 `Connect Chrome`。程式會啟動專用 Chrome 視窗，設定檔位於 `data/chrome-profile`。
4. 在該專用 Chrome 視窗登入 Google，並開啟 `https://gemini.google.com/`。請勿嘗試複製日常 Chrome 的設定檔或 Cookie。
5. 回到 LLM Adapter 網頁，按 `Refresh`，選擇 Gemini 分頁，再建立或選取本機對話紀錄。

首次登入後，登入狀態會保留在專用設定檔中；後續只要按 `Connect Chrome` 或 `Reconnect Chrome` 即可重新連線。

## 日常啟動、測試與關閉

```powershell
# 啟動網站
.\.venv\Scripts\python.exe -m uvicorn llm_adapter.main:app --host 127.0.0.1 --port 8000

# 或使用安裝的命令
.\.venv\Scripts\llm-adapter.exe

# 執行自動測試
.\.venv\Scripts\python.exe -m pytest -q
```

服務執行時，在終端機按 `Ctrl+C` 可正常停止服務並關閉其 CDP 連線。專用 Chrome 視窗可自行關閉；下次連線時會再次啟動或重新使用它。

## 資料位置與備份

每個本機 session 同時保存為：

```text
data/conversations/<session-id>.json
data/conversations/<session-id>.md
```

`.json` 保存完整結構與狀態，`.md` 方便閱讀、搜尋或備份。`data/` 已由 Git 排除，包含專用 Chrome 的登入狀態；不要提交、分享或刪除 `data/chrome-profile`，除非你要清除專用 Chrome 的登入狀態。若要備份對話，請只複製 `data/conversations/`。

要將歷史對話帶到另一台電腦時，在兩邊服務都停止的情況下，複製來源電腦的 `data/conversations/` 到目標電腦相同位置。目標電腦啟動後可在 `Saved conversations` 查看它們；若該筆紀錄所屬的 Gemini 分頁不在目前專用 Chrome 中，介面會唯讀顯示，這是避免接續到錯誤對話的保護機制。

## 可選設定

設定使用 `LLM_ADAPTER_` 前綴的環境變數。常見範例：

```powershell
$env:LLM_ADAPTER_PORT = "8001"
$env:LLM_ADAPTER_CHROME_EXECUTABLE = "C:\Program Files\Google\Chrome\Application\chrome.exe"
.\.venv\Scripts\llm-adapter.exe
```

可設定的項目包括 `HOST`、`PORT`、`CDP_HOST`、`CDP_PORT`、`CHROME_EXECUTABLE`、`DATA_DIR` 與回答 timeout 相關設定。預設服務埠為 `8000`，Chrome CDP 埠為 `9222`。

## Chrome 啟動診斷 Log

當畫面顯示 Chrome 無法連線或 CDP endpoint timeout 時，程式會在 `data/logs/` 建立下列檔案：

```text
data/logs/chrome-manager.jsonl
data/logs/chrome-stderr.log
```

`chrome-manager.jsonl` 是每行一筆 JSON 的 trace，會記錄啟動請求、Chrome 啟動、CDP 連線失敗、連線成功、Chrome 提前退出與 timeout。`chrome-stderr.log` 是 Chrome 原生的 stdout/stderr，適合檢查 profile 鎖定、參數或瀏覽器啟動問題。

發生問題後，在專案根目錄執行：

```powershell
Get-Content data\logs\chrome-manager.jsonl -Tail 100
Get-Content data\logs\chrome-stderr.log -Tail 200
Test-NetConnection 127.0.0.1 -Port 9222
```

若 trace 顯示 `chrome_exited`，請查看其中的 `returncode` 和 Chrome log；若持續是 `cdp_connect_failed` 或 `cdp_endpoint_timeout`，確認預設 CDP 埠 `9222` 未被其他程式占用。這些 log 可能含有本機路徑或 Chrome 的診斷資訊，分享前請先檢查內容。

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

## 故障排除

| 情況 | 處理方式 |
| --- | --- |
| 網頁無法開啟 | 確認啟動命令仍在執行，並開啟 `http://127.0.0.1:8000`。若出現埠號已被使用，關閉舊服務，或將 `LLM_ADAPTER_PORT` 設為其他埠號後重新啟動。 |
| 顯示 `Connect Chrome` 或 `Reconnect Chrome` | 按該按鈕建立與專用 Chrome 的連線；重新啟動服務後需要重新連線，但不需要重新登入。 |
| 清單沒有 Gemini 分頁 | 在專用 Chrome 開啟 `https://gemini.google.com/`，完成登入後回到網站按 `Refresh`。日常 Chrome 的分頁不會出現在清單中。 |
| 無法送出問題 | 確認已選取 Gemini 分頁、輸入框已可用，且上一輪不是 `pending` 或 `generating`。Gemini 正在產生回答時，系統會禁止送出下一題。 |
| 回答停在 `pending` 或 `generating` | 不要直接重送相同問題。先在同一 Gemini 分頁確認回答是否存在，再按 `Recapture response` 擷取現有回答。 |
| 找不到 Chrome | 安裝 Google Chrome，或設定 `LLM_ADAPTER_CHROME_EXECUTABLE` 指向 `chrome.exe` 的完整路徑。 |
| 登入狀態異常 | 關閉專用 Chrome，確認沒有其他程式占用 `data/chrome-profile`，再按 `Connect Chrome` 並重新登入。若要完全重設登入狀態，先備份對話資料，再刪除 `data/chrome-profile`。 |
| `py` 或 `python` 找不到、版本太舊 | 從 Python 官方安裝程式安裝 Python 3.11 以上版本，安裝時啟用加入 PATH 的選項；以 `py --version` 確認。若系統沒有 `py`，改用完整的 `python.exe` 路徑建立虛擬環境。 |
| `.venv` 壞掉或套件匯入失敗 | 停止服務後刪除 `.venv`，重新執行「安裝」段落的建立與安裝指令。`.venv` 不包含對話資料。 |
| `git clone` 或 `git pull` 要求登入或遭拒 | 確認已取得 GitHub 倉庫存取權，並依 GitHub 的提示以瀏覽器、Git Credential Manager 或 Personal Access Token 完成驗證。私人倉庫無法匿名下載。 |
| `git pull` 被本機變更阻擋 | 先執行 `git status` 確認差異；需要保留時先 commit 或 `git stash`，再拉取更新。不要用 Git 指令強制覆寫不確定的檔案。 |
| 啟動時顯示埠號被占用 | 先關閉舊的 LLM Adapter 程序；或設定未使用的 `LLM_ADAPTER_PORT`。若 Chrome 無法連線，也確認沒有另一個專用 Chrome 或程式占用預設 CDP 埠 `9222`，必要時另設 `LLM_ADAPTER_CDP_PORT`。 |
| 專用 Chrome 一開就關閉或無法連線 | 確認 `data/chrome-profile` 沒有被其他 Chrome 程序使用，完全關閉該專用 Chrome 後再按 `Connect Chrome`。不要對日常使用的 Chrome 加入這個專案的 CDP 參數。 |
| `Chrome started, but its local debugging endpoint did not respond` | 依「Chrome 啟動診斷 Log」讀取兩個 log，並執行 `Test-NetConnection 127.0.0.1 -Port 9222`。trace 若有 `chrome_exited`，優先檢查 Chrome stderr 與 exit code；若只有重複 `cdp_connect_failed`，確認 CDP 埠未被占用。 |
| 跨電腦看不到舊對話 | Git 不會同步 `data/`。請只複製 `data/conversations/`，不要傳送或同步 `data/chrome-profile`。 |

Gemini 的網頁結構可能變動。若 Gemini 已登入但仍持續找不到輸入框，請保留 `data/conversations/` 的對話檔案與錯誤訊息，以便檢查選擇器相容性。
