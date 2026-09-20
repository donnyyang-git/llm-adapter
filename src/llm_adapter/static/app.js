const storageKey = "llm-adapter.session-id";
const activeStatuses = new Set(["pending", "generating"]);
const state = { session: null, tabs: [], history: [], eventSource: null, reconnectTimer: null, reconnectAttempts: 0, chrome: null };

const elements = {
  connectionDot: document.querySelector("#connection-dot"),
  connectionText: document.querySelector("#connection-text"),
  startChromeButton: document.querySelector("#start-chrome-button"),
  refreshTabsButton: document.querySelector("#refresh-tabs-button"),
  tabList: document.querySelector("#tab-list"),
  tabHelp: document.querySelector("#tab-help"),
  newLocalSessionButton: document.querySelector("#new-local-session-button"),
  newGeminiSessionButton: document.querySelector("#new-gemini-session-button"),
  sessionList: document.querySelector("#session-list"),
  sessionLabel: document.querySelector("#session-label"),
  conversationTitle: document.querySelector("#conversation-title"),
  rebindButton: document.querySelector("#rebind-button"),
  recaptureButton: document.querySelector("#recapture-button"),
  notice: document.querySelector("#notice"),
  messageList: document.querySelector("#message-list"),
  form: document.querySelector("#message-form"),
  questionInput: document.querySelector("#question-input"),
  composerStatus: document.querySelector("#composer-status"),
  sendButton: document.querySelector("#send-button"),
};

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Request failed.");
  }
  return response.json();
}

function isActive() {
  const turns = state.session?.turns || [];
  return turns.length > 0 && activeStatuses.has(turns.at(-1).status);
}

function selectedTab() {
  return state.tabs.find((tab) => tab.is_selected) || null;
}

function canContinueSession() {
  return !state.session || state.session.tab_id === selectedTab()?.id;
}

function canRebindSession() {
  return Boolean(state.session && selectedTab() && !canContinueSession() && !isActive());
}

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.hidden = !message;
  elements.notice.classList.toggle("notice--error", isError);
}

function setChromeStatus(status) {
  // [修改] 2026-09-20 16:40 原因: New Gemini chat 需要依賴 Chrome 是否已連線，而不只是目前是否選到 tab。 說明: 將 Chrome 狀態保存到前端 state，讓按鈕啟用條件可同時判斷連線與分頁狀況。
  state.chrome = status;
  elements.connectionText.textContent = status.message || (status.connected ? "Chrome connected" : "Chrome unavailable");
  elements.connectionDot.className = `status-dot ${status.connected ? "status-dot--connected" : status.state === "error" ? "status-dot--error" : "status-dot--muted"}`;
  elements.startChromeButton.textContent = status.connected ? "Reconnect Chrome" : "Connect Chrome";
}

function isChromeConnected() {
  return Boolean(state.chrome?.connected);
}

function renderTabs() {
  const tabs = state.tabs.filter((tab) => tab.is_gemini);
  elements.tabList.replaceChildren();
  if (!tabs.length) {
    // [修改] 2026-09-20 16:40 原因: 當 Gemini tab 被關掉時，單純顯示空清單不足以引導使用者恢復。 說明: 提示可直接用 New Gemini chat 重新開啟新分頁，縮短復原路徑。
    elements.tabHelp.textContent = isChromeConnected()
      ? state.session && !canContinueSession()
        ? "The Gemini tab for this conversation was closed. Click New Gemini chat to open a replacement tab, then click Rebind session."
        : "No Gemini tabs are open in the dedicated Chrome profile. Click New Gemini chat to open one."
      : "Connect Chrome, then choose an open Gemini tab.";
  } else {
    elements.tabHelp.textContent = state.session && !canContinueSession()
      ? "This conversation is attached to a different Gemini tab. Select the target tab, then click Rebind session to continue here."
      : "Choose the Gemini tab that should receive this conversation.";
  }
  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tab-option";
    button.setAttribute("aria-pressed", String(tab.is_selected));
    button.dataset.tabId = tab.id;
    const title = document.createElement("span");
    title.className = "tab-title";
    title.textContent = tab.title || "Untitled Gemini tab";
    const url = document.createElement("span");
    url.className = "tab-url";
    url.textContent = tab.url;
    button.append(title, url);
    elements.tabList.append(button);
  }
  elements.newLocalSessionButton.disabled = !selectedTab() || isActive();
  elements.newGeminiSessionButton.disabled = !selectedTab() || isActive();
}

function renderHistory() {
  elements.sessionList.replaceChildren();
  if (!state.history.length) {
    const empty = document.createElement("p");
    empty.className = "muted-copy";
    empty.textContent = "No saved conversations yet.";
    elements.sessionList.append(empty);
    return;
  }
  for (const session of state.history) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-option";
    button.dataset.sessionId = session.session_id;
    button.setAttribute("aria-pressed", String(session.session_id === state.session?.session_id));
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title;
    const detail = document.createElement("span");
    detail.className = "session-detail";
    detail.textContent = `${session.turns.length} turns | ${new Date(session.updated_at).toLocaleString()}`;
    button.append(title, detail);
    elements.sessionList.append(button);
  }
}

function copyTextToClipboard(text) {
  // // [修改] 2026-09-20 15:40 原因: 使用者需要直接複製單則訊息內容，特別是長回覆與 Markdown 原文。 說明: 透過 navigator.clipboard 先複製，若不可用則退回 textarea fallback，保持相容。
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }

  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.append(helper);
  helper.select();
  const didCopy = document.execCommand("copy");
  helper.remove();
  if (!didCopy) {
    return Promise.reject(new Error("Clipboard copy failed."));
  }
  return Promise.resolve();
}

function renderSession() {
  const session = state.session;
  const active = isActive();
  elements.messageList.replaceChildren();
  elements.rebindButton.disabled = !canRebindSession();
  elements.recaptureButton.disabled = !active;
  elements.questionInput.disabled = !selectedTab() || active || !canContinueSession();
  elements.sendButton.disabled = !selectedTab() || active || !canContinueSession();
  elements.newLocalSessionButton.disabled = !selectedTab() || active;
  // [修改] 2026-09-20 16:40 原因: 沒有 Gemini tab 時仍要能透過 New Gemini chat 開啟新頁面。 說明: 只有在 Chrome 未連線或目前有進行中的 turn 時才禁用此操作。
  elements.newGeminiSessionButton.disabled = !isChromeConnected() || active;

  if (!session) {
    elements.sessionLabel.textContent = "NO ACTIVE CONVERSATION";
    elements.conversationTitle.textContent = selectedTab() ? "Ready for a new conversation" : isChromeConnected() ? "Open a Gemini tab" : "Choose a Gemini tab";
    elements.composerStatus.textContent = selectedTab()
      ? "A new local session is created when you send."
      : isChromeConnected()
        ? "Click New Gemini chat to open a Gemini tab, or load a saved conversation to rebind it later."
        : "Waiting for a Gemini tab.";
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = isChromeConnected()
      ? "<p>Select a Gemini tab or click New Gemini chat to start a saved local conversation.</p>"
      : "<p>Select a Gemini tab to start a saved local conversation.</p>";
    elements.messageList.append(empty);
    return;
  }

  elements.sessionLabel.textContent = `SESSION ${session.session_id}`;
  elements.conversationTitle.textContent = session.title;
  elements.composerStatus.textContent = active
    ? "Gemini is responding. A live connection is being maintained."
    : canContinueSession()
      ? "Saved locally. Ready for the next question."
      : selectedTab()
        ? "This record belongs to a different Gemini tab. Click Rebind session to continue here."
        : isChromeConnected()
          ? "The Gemini tab for this record was closed. Open a new Gemini tab, then click Rebind session."
          : "This record is read-only here.";
  for (const turn of session.turns) {
    const userCopy = turn.question;
    elements.messageList.append(createMessage("You", turn.question, "user", null, turn.turn_id, userCopy));
    const responseText = turn.response_text || (activeStatuses.has(turn.status) ? "Waiting for Gemini response..." : "No response was captured.");
    const modelCopy = turn.response_markdown || responseText;
    elements.messageList.append(createMessage("Gemini", responseText, "model", turn.error, turn.status, modelCopy));
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function createMessage(author, text, kind, error, stateLabel, copyText) {
  const article = document.createElement("article");
  article.className = `message message--${kind}`;

  const meta = document.createElement("div");
  meta.className = "message-meta";

  const name = document.createElement("span");
  name.textContent = author;

  const status = document.createElement("span");
  status.textContent = stateLabel;

  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "message-copy";
  copyButton.textContent = "Copy";
  copyButton.title = "Copy original text";
  copyButton.addEventListener("click", async () => {
    try {
      await copyTextToClipboard(copyText || text);
      const originalText = copyButton.textContent;
      copyButton.textContent = "Copied";
      setTimeout(() => {
        copyButton.textContent = originalText;
      }, 1000);
    } catch (error) {
      copyButton.textContent = "Failed";
      setTimeout(() => {
        copyButton.textContent = "Copy";
      }, 1000);
    }
  });

  meta.append(name, status, copyButton);

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;

  article.append(meta, body);

  if (error) {
    const errorNode = document.createElement("p");
    errorNode.className = "message-error";
    errorNode.textContent = error;
    article.append(errorNode);
  }

  return article;
}

// [修改] 2026-09-20 15:50 原因: 前端缺少 Chrome 狀態與 session 載入的 helper，導致點擊 Connect Chrome 會在 refreshChromeAndTabs 時噴錯。 說明: 補回狀態刷新、儲存 session 載入、建立新會話等函式，讓 UI 正常往後執行。
async function loadStoredSession() {
  const storedSessionId = localStorage.getItem(storageKey);
  if (!storedSessionId) {
    state.session = null;
    return;
  }

  try {
    state.session = await request(`/api/sessions/${encodeURIComponent(storedSessionId)}`);
    state.history = await request("/api/sessions");
  } catch (error) {
    localStorage.removeItem(storageKey);
    state.session = null;
    state.history = [];
    showNotice(error.message, true);
  }
}

async function createSession(path = "/api/sessions") {
  const session = await request(path, { method: "POST" });
  state.session = session;
  localStorage.setItem(storageKey, session.session_id);
  state.history = await request("/api/sessions");
  renderHistory();
  renderSession();
  connectEvents();
  return session;
}

async function refreshChromeAndTabs() {
  const status = await request("/api/chrome/status");
  setChromeStatus(status);
  state.tabs = await request("/api/tabs");
  state.history = await request("/api/sessions");
  renderTabs();
  renderHistory();

  const activeSessionId = state.session?.session_id;
  if (activeSessionId && !state.history.some((session) => session.session_id === activeSessionId)) {
    state.session = null;
    localStorage.removeItem(storageKey);
  }

  renderSession();
}

async function loadSession(sessionId) {
  disconnectEvents();
  state.session = await request(`/api/sessions/${encodeURIComponent(sessionId)}`);
  localStorage.setItem(storageKey, sessionId);
  showNotice("");
  renderHistory();
  renderSession();
}

async function selectTab(tabId) {
  // [修改] 2026-09-20 17:05 原因: 需要讓舊 session 先保留在畫面上，才能在選到新 Gemini tab 後執行 rebind。 說明: 只更新目前選取的 tab，不主動清除已載入的 session。
  await request("/api/tabs/select", { method: "POST", body: JSON.stringify({ tab_id: tabId }) });
  showNotice("");
  await refreshChromeAndTabs();
}

async function rebindSession() {
  if (!state.session) return;
  // [修改] 2026-09-20 17:05 原因: 舊對話要明確接回新 Gemini tab，需先保留 session，再由使用者手動觸發重綁。 說明: 透過專用 API 更新 session.tab_id，避免自動接管造成誤送。
  try {
    state.session = await request(`/api/sessions/${encodeURIComponent(state.session.session_id)}/rebind`, { method: "POST" });
    localStorage.setItem(storageKey, state.session.session_id);
    showNotice("");
    await refreshChromeAndTabs();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function disconnectEvents() {
  window.clearTimeout(state.reconnectTimer);
  state.reconnectTimer = null;
  if (state.eventSource) state.eventSource.close();
  state.eventSource = null;
}

function connectEvents() {
  if (!state.session || !isActive() || state.eventSource) return;
  const sessionId = state.session.session_id;
  const source = new EventSource(`/api/sessions/${encodeURIComponent(sessionId)}/events`);
  state.eventSource = source;
  source.onopen = () => { state.reconnectAttempts = 0; showNotice(""); };
  for (const eventName of ["queued", "sent", "generating", "response_update", "completed", "partial", "failed"]) {
    source.addEventListener(eventName, (event) => applyEvent(eventName, event));
  }
  source.onerror = () => {
    source.close();
    if (state.eventSource === source) state.eventSource = null;
    scheduleReconnect();
  };
}

async function applyEvent(eventName, event) {
  const payload = JSON.parse(event.data);
  if (payload.session_id !== state.session?.session_id) return;
  const turn = state.session.turns.find((item) => item.turn_id === payload.turn_id);
  if (!turn) return reloadSession();
  Object.assign(turn, payload.data);
  turn.status = payload.data.status || eventName;
  renderSession();
  if (!isActive()) disconnectEvents();
}

function scheduleReconnect() {
  if (!state.session || !isActive() || state.reconnectTimer) return;
  const delay = Math.min(1000 * 2 ** state.reconnectAttempts, 15000);
  state.reconnectAttempts += 1;
  showNotice(`Live updates disconnected. Reconnecting in ${Math.ceil(delay / 1000)} seconds...`, true);
  state.reconnectTimer = window.setTimeout(async () => {
    state.reconnectTimer = null;
    await reloadSession();
    connectEvents();
  }, delay);
}

async function reloadSession() {
  if (!state.session) return;
  try {
    state.session = await request(`/api/sessions/${encodeURIComponent(state.session.session_id)}`);
    renderSession();
  } catch (error) {
    showNotice(error.message, true);
  }
}

elements.startChromeButton.addEventListener("click", async () => {
  try {
    setChromeStatus({ state: "starting", connected: false, message: "Connecting to dedicated Chrome..." });
    await request("/api/chrome/start", { method: "POST" });
    await refreshChromeAndTabs();
  } catch (error) { showNotice(error.message, true); }
});
elements.refreshTabsButton.addEventListener("click", () => refreshChromeAndTabs().catch((error) => showNotice(error.message, true)));
elements.tabList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-tab-id]");
  if (button) selectTab(button.dataset.tabId).catch((error) => showNotice(error.message, true));
});
elements.sessionList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-session-id]");
  if (button) loadSession(button.dataset.sessionId).catch((error) => showNotice(error.message, true));
});
elements.newLocalSessionButton.addEventListener("click", () => createSession().catch((error) => showNotice(error.message, true)));
elements.newGeminiSessionButton.addEventListener("click", async () => {
  try {
    await createSession("/api/sessions/new-gemini");
    await refreshChromeAndTabs();
  } catch (error) { showNotice(error.message, true); }
});
elements.rebindButton.addEventListener("click", () => rebindSession().catch((error) => showNotice(error.message, true)));
elements.recaptureButton.addEventListener("click", async () => {
  try {
    state.session = await request(`/api/sessions/${encodeURIComponent(state.session.session_id)}/recapture`, { method: "POST" });
    renderSession();
    connectEvents();
  } catch (error) { showNotice(error.message, true); }
});
elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = elements.questionInput.value.trim();
  if (!question) return;
  try {
    if (!state.session) await createSession();
    state.session = await request(`/api/sessions/${encodeURIComponent(state.session.session_id)}/messages`, { method: "POST", body: JSON.stringify({ question }) });
    elements.questionInput.value = "";
    renderSession();
    connectEvents();
  } catch (error) { showNotice(error.message, true); }
});

async function initialize() {
  try {
    await loadStoredSession();
    await refreshChromeAndTabs();
    renderSession();
  } catch (error) {
    showNotice(error.message, true);
    renderSession();
  }
}

initialize();