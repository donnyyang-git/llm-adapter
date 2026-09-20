const storageKey = "llm-adapter.session-id";
const activeStatuses = new Set(["pending", "generating"]);
const state = { session: null, tabs: [], history: [], eventSource: null, reconnectTimer: null, reconnectAttempts: 0 };

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

function showNotice(message, isError = false) {
  elements.notice.textContent = message;
  elements.notice.hidden = !message;
  elements.notice.classList.toggle("notice--error", isError);
}

function setChromeStatus(status) {
  elements.connectionText.textContent = status.message || (status.connected ? "Chrome connected" : "Chrome unavailable");
  elements.connectionDot.className = `status-dot ${status.connected ? "status-dot--connected" : status.state === "error" ? "status-dot--error" : "status-dot--muted"}`;
  elements.startChromeButton.textContent = status.connected ? "Reconnect Chrome" : "Connect Chrome";
}

function renderTabs() {
  const tabs = state.tabs.filter((tab) => tab.is_gemini);
  elements.tabList.replaceChildren();
  if (!tabs.length) {
    elements.tabHelp.textContent = "No Gemini tabs are open in the dedicated Chrome profile.";
  } else {
    elements.tabHelp.textContent = "Choose the Gemini tab that should receive this conversation.";
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

function renderSession() {
  const session = state.session;
  const active = isActive();
  elements.messageList.replaceChildren();
  elements.recaptureButton.disabled = !active;
  elements.questionInput.disabled = !selectedTab() || active || !canContinueSession();
  elements.sendButton.disabled = !selectedTab() || active || !canContinueSession();
  elements.newLocalSessionButton.disabled = !selectedTab() || active;
  elements.newGeminiSessionButton.disabled = !selectedTab() || active;

  if (!session) {
    elements.sessionLabel.textContent = "NO ACTIVE CONVERSATION";
    elements.conversationTitle.textContent = selectedTab() ? "Ready for a new conversation" : "Choose a Gemini tab";
    elements.composerStatus.textContent = selectedTab() ? "A new local session is created when you send." : "Waiting for a Gemini tab.";
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<p>Select a Gemini tab to start a saved local conversation.</p>";
    elements.messageList.append(empty);
    return;
  }

  elements.sessionLabel.textContent = `SESSION ${session.session_id}`;
  elements.conversationTitle.textContent = session.title;
  elements.composerStatus.textContent = active ? "Gemini is responding. A live connection is being maintained." : canContinueSession() ? "Saved locally. Ready for the next question." : "This record belongs to a different Gemini tab and is read-only here.";
  for (const turn of session.turns) {
    elements.messageList.append(createMessage("You", turn.question, "user", null, turn.turn_id));
    const text = turn.response_text || (activeStatuses.has(turn.status) ? "Waiting for Gemini response..." : "No response was captured.");
    elements.messageList.append(createMessage("Gemini", text, "model", turn.error, turn.status));
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function createMessage(author, text, kind, error, stateLabel) {
  const article = document.createElement("article");
  article.className = `message message--${kind}`;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("span");
  name.textContent = author;
  const status = document.createElement("span");
  status.textContent = stateLabel;
  meta.append(name, status);
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

async function refreshChromeAndTabs() {
  const [status, tabs, history] = await Promise.all([request("/api/chrome/status"), request("/api/tabs"), request("/api/sessions")]);
  state.tabs = tabs;
  state.history = history;
  setChromeStatus(status);
  renderTabs();
  renderHistory();
  renderSession();
}

async function loadStoredSession() {
  const sessionId = localStorage.getItem(storageKey);
  if (!sessionId) return;
  try {
    state.session = await request(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (isActive()) connectEvents();
  } catch (error) {
    localStorage.removeItem(storageKey);
    showNotice("The previously open session is no longer available.", true);
  }
}

async function createSession(path = "/api/sessions") {
  const session = await request(path, { method: "POST" });
  state.session = session;
  state.history = [session, ...state.history.filter((item) => item.session_id !== session.session_id)];
  localStorage.setItem(storageKey, session.session_id);
  renderHistory();
  renderSession();
  return session;
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
  await request("/api/tabs/select", { method: "POST", body: JSON.stringify({ tab_id: tabId }) });
  disconnectEvents();
  state.session = null;
  localStorage.removeItem(storageKey);
  showNotice("");
  await refreshChromeAndTabs();
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