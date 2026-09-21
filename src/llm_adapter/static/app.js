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
    elements.messageList.append(createMessage("Gemini", responseText, "model", turn.error, turn.status, modelCopy, turn.response_image_path, turn.response_text, turn.response_artifact_code, turn.response_markdown || turn.response_text, turn.response_artifacts || []));
  }
  elements.messageList.scrollTop = elements.messageList.scrollHeight;
}

function normalizeArtifactCode(rawText) {
  // [修改] 2026-09-20 18:35 原因: Gemini code artifact 常混入 NBSP 與視覺換行，直接拿來預覽容易壞掉。 說明: 先做保守正規化，盡量修復 HTML / URL 被切斷的情況。
  return rawText
    .replace(/\u00A0/g, " ")
    .replace(/,\s*\n\s+/g, ", ")
    .replace(/\.\s*\n\s*([A-Za-z])/g, ".$1")
    .replace(/\+\s*\n\s*\+/g, "+")
    .replace(/([A-Za-z0-9_\/-])\n\s+([A-Za-z0-9_\/-])/g, "$1$2")
    .trim();
}

function stripArtifactToolbarText(text) {
  return text
    .replace(/\n?(程式碼\s*\n\s*預覽(?:\s*\n\s*下載)?)/g, "")
    .replace(/\n?(Code\s*\n\s*Preview(?:\s*\n\s*Download)?)/gi, "")
    .replace(/\n?(程式碼預覽(?:\s*\n\s*下載)?)/g, "")
    .trim();
}

function normalizeSummaryText(rawText = "") {
  return rawText
    .replace(/\u00A0/g, " ")
    .replace(/\r\n/g, "\n")
    .trim();
}

function escapeHtml(text = "") {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sanitizeUrl(rawUrl = "") {
  const url = rawUrl.trim();
  if (!url) return "#";
  if (/^(https?:|mailto:|#|\/)/i.test(url)) {
    return escapeHtml(url);
  }
  return "#";
}

function renderInlineMarkdown(text = "") {
  let rendered = escapeHtml(text);
  rendered = rendered.replace(/`([^`]+)`/g, "<code>$1</code>");
  rendered = rendered.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  rendered = rendered.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  rendered = rendered.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  rendered = rendered.replace(/_([^_]+)_/g, "<em>$1</em>");
  rendered = rendered.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => `<a href="${sanitizeUrl(url)}" target="_blank" rel="noreferrer">${label}</a>`);
  return rendered;
}

function renderMarkdownToHtml(markdown = "") {
  const normalized = normalizeSummaryText(markdown);
  if (!normalized) return "";

  const lines = normalized.split("\n");
  const htmlParts = [];
  let paragraphLines = [];
  let listType = "";
  let listItems = [];
  let quoteLines = [];
  let inCodeBlock = false;
  let codeLanguage = "";
  let codeLines = [];

  function flushParagraph() {
    if (!paragraphLines.length) return;
    htmlParts.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n")).replace(/\n/g, "<br>")}</p>`);
    paragraphLines = [];
  }

  function flushList() {
    if (!listItems.length || !listType) return;
    const items = listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("");
    htmlParts.push(`<${listType}>${items}</${listType}>`);
    listType = "";
    listItems = [];
  }

  function flushQuote() {
    if (!quoteLines.length) return;
    htmlParts.push(`<blockquote>${quoteLines.map((line) => `<p>${renderInlineMarkdown(line)}</p>`).join("")}</blockquote>`);
    quoteLines = [];
  }

  function flushCodeBlock() {
    if (!codeLines.length && !codeLanguage) return;
    const languageClass = codeLanguage ? ` class="language-${escapeHtml(codeLanguage)}"` : "";
    htmlParts.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
    codeLanguage = "";
  }

  for (const line of lines) {
    const codeFenceMatch = line.match(/^```([a-zA-Z0-9_+-]+)?\s*$/);
    if (inCodeBlock) {
      if (codeFenceMatch) {
        flushCodeBlock();
        inCodeBlock = false;
      } else {
        codeLines.push(line);
      }
      continue;
    }

    if (codeFenceMatch) {
      flushParagraph();
      flushList();
      flushQuote();
      inCodeBlock = true;
      codeLanguage = (codeFenceMatch[1] || "").toLowerCase();
      codeLines = [];
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      flushQuote();
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      flushQuote();
      const level = headingMatch[1].length;
      htmlParts.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      continue;
    }

    const quoteMatch = line.match(/^>\s?(.*)$/);
    if (quoteMatch) {
      flushParagraph();
      flushList();
      quoteLines.push(quoteMatch[1]);
      continue;
    }
    flushQuote();

    const orderedMatch = line.match(/^\d+\.\s+(.+)$/);
    if (orderedMatch) {
      flushParagraph();
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listItems.push(orderedMatch[1]);
      continue;
    }

    const unorderedMatch = line.match(/^[-*+]\s+(.+)$/);
    if (unorderedMatch) {
      flushParagraph();
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listItems.push(unorderedMatch[1]);
      continue;
    }
    flushList();

    paragraphLines.push(line);
  }

  if (inCodeBlock) {
    flushCodeBlock();
  }
  flushParagraph();
  flushList();
  flushQuote();
  return htmlParts.join("");
}

function looksLikeMarkdown(text = "") {
  const normalized = normalizeSummaryText(text);
  if (!normalized) return false;
  return /(^|\n)(#{1,6}\s|>\s|[-*+]\s|\d+\.\s)|```|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\[[^\]]+\]\([^)]+\)/m.test(normalized);
}

function looksLikeJson(text = "") {
  const normalized = text.trim();
  if (!normalized || !/^[\[{]/.test(normalized)) return false;
  try {
    JSON.parse(normalized);
    return true;
  } catch {
    return false;
  }
}

function createRichTextBody(text = "", preferMarkdown = false) {
  const body = document.createElement("div");
  body.className = "message-body";

  if (preferMarkdown && looksLikeMarkdown(text)) {
    body.classList.add("message-body--rich", "prose");
    body.innerHTML = renderMarkdownToHtml(text);
    return body;
  }

  body.textContent = text;
  return body;
}

function inferArtifactPreviewType(artifact = {}) {
  const explicitType = (artifact.preview_type || "").toLowerCase();
  const language = (artifact.language || "").toLowerCase();
  const code = (artifact.code || "").trim();
  const hasImage = Boolean((artifact.image_path || "").trim());

  if (explicitType === "html" && code) return "html";
  if (explicitType === "image" && hasImage) return "image";
  if (language === "html" || /^<!doctype html>|^<html[\s>]/i.test(code)) return "html";
  if (language === "markdown" || language === "md") return "markdown";
  if (language === "json" || looksLikeJson(code)) return "json";
  if (hasImage && !code) return "image";
  return explicitType === "code" ? "code" : "none";
}

function createArtifactPreviewNode(artifact, previewType) {
  if (previewType === "html") {
    const frame = document.createElement("iframe");
    frame.className = "message-artifact-frame";
    frame.loading = "lazy";
    frame.referrerPolicy = "no-referrer";
    frame.sandbox = "allow-scripts allow-same-origin";
    frame.srcdoc = artifact.code.trim();
    return frame;
  }

  if (previewType === "markdown") {
    const markdown = document.createElement("div");
    markdown.className = "message-artifact-rendered prose";
    markdown.innerHTML = renderMarkdownToHtml(artifact.code.trim());
    return markdown;
  }

  if (previewType === "json") {
    const jsonBlock = document.createElement("pre");
    jsonBlock.className = "message-artifact-json";
    try {
      jsonBlock.textContent = JSON.stringify(JSON.parse(artifact.code.trim()), null, 2);
    } catch {
      jsonBlock.textContent = artifact.code.trim();
    }
    return jsonBlock;
  }

  if (previewType === "image") {
    const wrapper = document.createElement("a");
    wrapper.className = "message-preview-link";
    wrapper.href = artifact.image_path;
    wrapper.target = "_blank";
    wrapper.rel = "noreferrer";

    const image = document.createElement("img");
    image.className = "message-preview-image";
    image.loading = "lazy";
    image.alt = artifact.title || "Artifact preview";
    image.src = artifact.image_path;
    wrapper.append(image);
    return wrapper;
  }

  return null;
}

function extractArtifactSummary(sourceText = "", artifacts = []) {
  // [修改] 2026-09-20 19:55 原因: Gemini 的摘要通常會和多個 code block 交錯出現，不能在第一個 code block 就截斷。 說明: 改為保留整段摘要，只移除 code fence 本體，讓 1/2/3 這類編號內容完整保留。
  let summary = normalizeSummaryText(sourceText);
  if (artifacts.length || summary.includes("```") || summary.includes("<html") || summary.includes("<!DOCTYPE html>")) {
    summary = summary.replace(/```[a-zA-Z0-9_+-]*\n[\s\S]*?```/g, "");
  }
  return stripArtifactToolbarText(summary)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeArtifacts(responseArtifacts = [], artifactCode = "", imagePath = "", sourceText = "") {
  const normalizedArtifacts = Array.isArray(responseArtifacts)
    ? responseArtifacts
      .filter((artifact) => artifact && (artifact.code || artifact.image_path))
      .map((artifact, index) => ({
        artifact_id: artifact.artifact_id || `artifact-${index + 1}`,
        title: artifact.title || `Code snippet ${index + 1}`,
        summary: artifact.summary || "",
        kind: artifact.kind || "code-block",
        language: artifact.language || "",
        code: artifact.code || "",
        preview_type: inferArtifactPreviewType(artifact) || ((artifact.language || "").toLowerCase() === "html" ? "html" : artifact.code ? "code" : artifact.image_path ? "image" : "none"),
        image_path: artifact.image_path || "",
      }))
    : [];

  if (normalizedArtifacts.length) {
    if (!normalizedArtifacts[0].image_path && imagePath) {
      normalizedArtifacts[0].image_path = imagePath;
    }
    return normalizedArtifacts;
  }

  if (artifactCode.trim()) {
    return [{
      artifact_id: "artifact-1",
      title: "Generated artifact",
      summary: "",
      kind: "gemini-ui",
      language: artifactCode.trim().match(/^<!DOCTYPE html>|^<html[\s>]/i) ? "html" : "",
      code: artifactCode.trim(),
      preview_type: artifactCode.trim().match(/^<!DOCTYPE html>|^<html[\s>]/i) ? "html" : "code",
      image_path: imagePath || "",
    }];
  }

  const normalized = normalizeArtifactCode(sourceText);
  const htmlStart = normalized.search(/<!DOCTYPE html>|<html[\s>]/i);
  if (htmlStart >= 0) {
    return [{
      artifact_id: "artifact-1",
      title: "Generated artifact",
      summary: "",
      kind: "gemini-ui",
      language: "html",
      code: normalized.slice(htmlStart).trim(),
      preview_type: "html",
      image_path: imagePath || "",
    }];
  }

  const fenceMatch = normalized.match(/```([a-zA-Z0-9_-]+)?\n([\s\S]*?)```/);
  if (fenceMatch) {
    return [{
      artifact_id: "artifact-1",
      title: "Code snippet 1",
      summary: "",
      kind: "code-block",
      language: (fenceMatch[1] || "").toLowerCase(),
      code: fenceMatch[2].trim(),
      preview_type: fenceMatch[1]?.toLowerCase() === "html" ? "html" : "code",
      image_path: imagePath || "",
    }];
  }

  return [];
}

function createArtifactPanel(artifact, copyText) {
  const container = document.createElement("section");
  container.className = "message-artifact";

  const hasCode = Boolean(artifact?.code?.trim());
  const previewType = inferArtifactPreviewType(artifact);
  const hasPreview = previewType !== "code" && previewType !== "none";
  // [修改] 2026-09-20 19:55 原因: 空白預覽或空白程式碼會誤導成擷取成功。 說明: 預覽只保留真正的 HTML artifact，避免把一般 response screenshot 當成有效內容。
  const activeTab = hasPreview && (!hasCode || previewType !== "code") ? "preview" : "code";

  if (!hasCode && !hasPreview) {
    return null;
  }

  const toolbar = document.createElement("div");
  toolbar.className = "message-artifact-toolbar";

  if (artifact?.summary) {
    const summary = createRichTextBody(artifact.summary, true);
    summary.classList.add("message-artifact-summary");
    container.append(summary);
  }

  const title = document.createElement("span");
  title.className = "message-artifact-title";
  title.textContent = artifact?.language ? `${artifact.title} · ${artifact.language}` : artifact.title;

  const tabs = document.createElement("div");
  tabs.className = "message-artifact-tabs";

  const codeButton = document.createElement("button");
  codeButton.type = "button";
  codeButton.className = "message-artifact-tab";
  codeButton.textContent = "程式碼";
  codeButton.disabled = !hasCode;

  const previewButton = document.createElement("button");
  previewButton.type = "button";
  previewButton.className = "message-artifact-tab";
  previewButton.textContent = "預覽";
  previewButton.disabled = !hasPreview;

  tabs.append(codeButton, previewButton);
  toolbar.append(title, tabs);

  if (hasCode) {
    const copyCodeButton = document.createElement("button");
    copyCodeButton.type = "button";
    copyCodeButton.className = "message-copy";
    copyCodeButton.textContent = "Copy code";
    copyCodeButton.title = "Copy generated code";
    copyCodeButton.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(artifact.code || copyText || "");
        const originalText = copyCodeButton.textContent;
        copyCodeButton.textContent = "Copied";
        setTimeout(() => {
          copyCodeButton.textContent = originalText;
        }, 1000);
      } catch {
        copyCodeButton.textContent = "Failed";
        setTimeout(() => {
          copyCodeButton.textContent = "Copy code";
        }, 1000);
      }
    });
    toolbar.append(copyCodeButton);
  }

  const codePanel = document.createElement("div");
  codePanel.className = "message-artifact-panel";
  if (hasCode) {
    const codeBlock = document.createElement("pre");
    codeBlock.className = "message-artifact-code";
    const codeNode = document.createElement("code");
    codeNode.textContent = artifact.code.trim();
    codeBlock.append(codeNode);
    codePanel.append(codeBlock);
  }

  const previewPanel = document.createElement("div");
  previewPanel.className = "message-artifact-panel";
  if (hasPreview) {
    const previewNode = createArtifactPreviewNode(artifact, previewType);
    if (previewNode) {
      previewPanel.append(previewNode);
    }
  }

  function setActiveTab(tabName) {
    codeButton.setAttribute("aria-pressed", String(tabName === "code"));
    previewButton.setAttribute("aria-pressed", String(tabName === "preview"));
    codePanel.hidden = tabName !== "code";
    previewPanel.hidden = tabName !== "preview";
  }

  codeButton.addEventListener("click", () => setActiveTab("code"));
  previewButton.addEventListener("click", () => setActiveTab("preview"));
  setActiveTab(activeTab);

  container.append(toolbar, codePanel, previewPanel);
  return container;
}

function createMessage(author, text, kind, error, stateLabel, copyText, imagePath = "", artifactSource = "", artifactCode = "", summarySource = "", responseArtifacts = []) {
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

  const artifacts = kind === "model"
    ? normalizeArtifacts(responseArtifacts, artifactCode || "", imagePath || "", summarySource || artifactSource || text)
    : [];
  const displayText = artifacts.length
    ? extractArtifactSummary(summarySource || artifactSource || text, artifacts)
    : text;
  const body = createRichTextBody(displayText, kind === "model");

  article.append(meta);
  if (displayText) {
    article.append(body);
  }

  if (kind === "model" && artifacts.length) {
    // [修改] 2026-09-20 19:20 原因: 一則 Gemini 回覆可能同時包含多個程式片段。 說明: 改為逐一渲染 artifact 清單，而不是只顯示單一 code/preview 面板。
    for (const artifact of artifacts) {
      const panel = createArtifactPanel(artifact, copyText);
      if (panel) {
        article.append(panel);
      }
    }
  }

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

function findSessionForTab(tabId) {
  return state.history.find((session) => session.tab_id === tabId) || null;
}

async function selectTab(tabId) {
  // [修改] 2026-09-20 17:05 原因: 需要讓舊 session 先保留在畫面上，才能在選到新 Gemini tab 後執行 rebind。 說明: 只更新目前選取的 tab，不主動清除已載入的 session。
  await request("/api/tabs/select", { method: "POST", body: JSON.stringify({ tab_id: tabId }) });
  showNotice("");
  await refreshChromeAndTabs();
  const matchingSession = findSessionForTab(tabId);
  if (matchingSession && matchingSession.session_id !== state.session?.session_id) {
    await loadSession(matchingSession.session_id);
  }
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
  source.onopen = async () => {
    state.reconnectAttempts = 0;
    showNotice("");
    // [修改] 2026-09-21 17:25 原因: 若 Gemini 很快完成，前端可能在 SSE 訂閱建立前就錯過最後的 completed/failed 事件，導致畫面永遠停在 generating 並把按鈕鎖住。 說明: SSE 一連上就主動重新抓一次 session snapshot，收斂快速完成時的 race condition。
    await reloadSession();
    if (!isActive()) {
      disconnectEvents();
    }
  };
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