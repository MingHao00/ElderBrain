const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const messagesContainer = document.getElementById("messagesContainer");
const typingIndicator = document.getElementById("typingIndicator");
const chatTitle = document.querySelector(".chat-title");
const chatHistory = document.querySelector(".chat-history");
const greetingMessage = document.getElementById("greetingMessage");
const ASK_PATH = "/api/ask";
const TITLE_LIMIT = 24;
const STORAGE_KEY = "elderbrain.sessions";
const UNTITLED = "新对话";
// 与服务端历史上限一致。超限的请求会被整段拒绝，对话就再也发不出去。
const MAX_HISTORY_MESSAGES = 8;
const MAX_HISTORY_CHARS = 2000;
const MAX_QUESTION_CHARS = 2000;
const QUESTION_TOO_LONG = "问题太长";
const ANSWER_FAILED = "回答失败";
const OFFLINE_NOTICE = "连不上本机服务。";

let sessions = [];
let currentId = "";
let pendingSerial = 0;

messageInput.addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = this.scrollHeight + "px";
  sendButton.disabled = this.value.trim() === "";
});

messageInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!sendButton.disabled) {
      sendMessage();
    }
  }
});

sendButton.addEventListener("click", sendMessage);
document.querySelector(".new-chat-btn").addEventListener("click", startNewChat);
document.getElementById("clearChat").addEventListener("click", clearCurrent);
chatHistory.addEventListener("click", onHistoryClick);
loadSessions();

function loadSessions() {
  const stored = readStore();
  sessions = stored.sessions;
  currentId = stored.currentId;
  if (!findSession(currentId)) {
    sessions.unshift(blankSession());
    currentId = sessions[0].id;
  }
  showCurrent();
}

function readStore() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || "null";
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== "object") {
      return emptyStore();
    }
    const found = Array.isArray(payload.sessions) ? payload.sessions : [];
    const storedId = typeof payload.currentId === "string" ? payload.currentId : "";
    return {sessions: keptSessions(found), currentId: storedId};
  } catch {
    // 本地记录损坏时从空对话开始，避免整页打不开。
    return emptyStore();
  }
}

function emptyStore() {
  return {sessions: [], currentId: ""};
}

function keptSessions(found) {
  const normalized = [];
  found.forEach(function (item) {
    const session = normalizeSession(item);
    if (session) {
      normalized.push(session);
    }
  });
  return normalized;
}

function normalizeSession(item) {
  if (!item || typeof item.id !== "string" || item.id.trim() === "") {
    return null;
  }
  const title = typeof item.title === "string" ? item.title.trim() : "";
  const messages = Array.isArray(item.messages) ? item.messages : [];
  return {
    id: item.id,
    title: title || UNTITLED,
    messages: messages.filter(validMessage).map(storedMessage),
  };
}

function validMessage(message) {
  if (!message || typeof message.text !== "string") {
    return false;
  }
  const known = message.role === "user" || message.role === "assistant";
  return known && message.text.trim() !== "";
}

function storedMessage(message) {
  return {role: message.role, text: message.text.trim()};
}

function blankSession() {
  return {id: newId(), title: UNTITLED, messages: []};
}

function newId() {
  if (window.crypto && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return String(Date.now()) + "-" + String(Math.random()).slice(2);
}

function saveSessions() {
  const payload = {
    currentId: currentId,
    sessions: sessions.map(function (session) {
      return {id: session.id, title: session.title, messages: session.messages};
    }),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function findSession(id) {
  for (let index = 0; index < sessions.length; index += 1) {
    if (sessions[index].id === id) {
      return sessions[index];
    }
  }
  return null;
}

function currentSession() {
  return findSession(currentId) || sessions[0];
}

function showCurrent() {
  const session = currentSession();
  currentId = session.id;
  chatTitle.textContent = session.title;
  typingIndicator.style.display = session.pending ? "flex" : "none";
  renderSidebar();
  renderMessages(session);
  saveSessions();
}

function renderSidebar() {
  chatHistory.textContent = "";
  sessions.forEach(function (session) {
    const item = document.createElement("div");
    const active = session.id === currentId ? " active" : "";
    item.className = "chat-item" + active;
    item.dataset.id = session.id;
    item.textContent = session.title;
    chatHistory.append(item);
  });
}

function renderMessages(session) {
  const nodes = messagesContainer.querySelectorAll(".message");
  nodes.forEach(function (node) {
    if (node !== greetingMessage) {
      node.remove();
    }
  });
  session.messages.forEach(function (message) {
    appendMessage(message.role, message.text, false);
  });
  scrollDown();
}

function onHistoryClick(event) {
  const item = event.target.closest(".chat-item");
  if (!item || item.dataset.id === currentId) {
    return;
  }
  currentId = item.dataset.id;
  showCurrent();
}

function startNewChat() {
  sessions.unshift(blankSession());
  currentId = sessions[0].id;
  messageInput.value = "";
  messageInput.style.height = "auto";
  sendButton.disabled = true;
  showCurrent();
}

function clearCurrent() {
  const session = currentSession();
  session.messages = [];
  session.pending = null;
  typingIndicator.style.display = "none";
  saveSessions();
  renderMessages(session);
}

function sendMessage() {
  const messageText = messageInput.value.trim();
  const session = currentSession();
  if (messageText === "" || session.pending) {
    return;
  }
  if (messageText.length > MAX_QUESTION_CHARS) {
    showUnsaved(QUESTION_TOO_LONG);
    return;
  }
  const history = requestHistory(session.messages);
  session.messages.push({role: "user", text: messageText});
  if (session.title === UNTITLED) {
    session.title = clipTitle(messageText);
  }
  pendingSerial += 1;
  session.pending = pendingSerial;
  saveSessions();
  appendMessage("user", messageText, true);
  chatTitle.textContent = session.title;
  renderSidebar();
  messageInput.value = "";
  messageInput.style.height = "auto";
  sendButton.disabled = true;
  ask(session, messageText, history, session.pending);
}

function requestHistory(messages) {
  const start = Math.max(0, messages.length - MAX_HISTORY_MESSAGES);
  return messages.slice(start).map(function (message) {
    return {role: message.role, text: clipHistoryText(message.text)};
  });
}

function clipHistoryText(text) {
  const cleaned = text.trim();
  if (cleaned.length <= MAX_HISTORY_CHARS) {
    return cleaned;
  }
  return cleaned.slice(0, MAX_HISTORY_CHARS);
}

function showUnsaved(text) {
  const stale = messagesContainer.querySelectorAll(".message-unsaved");
  stale.forEach(function (node) {
    node.remove();
  });
  appendMessage("assistant", text, true, true);
}

function clipTitle(text) {
  if (text.length <= TITLE_LIMIT) {
    return text;
  }
  return text.slice(0, TITLE_LIMIT) + "…";
}

function csrfToken() {
  if (typeof window.elderbrainCsrf === "function") {
    return window.elderbrainCsrf();
  }
  return "";
}

function ask(session, messageText, history, serial) {
  typingIndicator.style.display = "flex";
  scrollDown();
  fetch(ASK_PATH, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken(),
    },
    body: JSON.stringify({question: messageText, history: history}),
  }).then(function (response) {
    return response.json().then(function (payload) {
      return {ok: response.ok, payload: payload};
    });
  }).then(function (result) {
    const payload = result.payload || {};
    if (result.ok && payload.answer) {
      finishAsk(session, serial, payload.answer, true);
      return;
    }
    // 拒绝和断线只留在页面上。写进历史后，下一句会一直带着失败文案。
    finishAsk(session, serial, payload.error || ANSWER_FAILED, false);
  }).catch(function () {
    finishAsk(session, serial, OFFLINE_NOTICE, false);
  });
}

function finishAsk(session, serial, text, saved) {
  if (session.pending !== serial) {
    return;
  }
  session.pending = null;
  if (saved) {
    session.messages.push({role: "assistant", text: text});
    saveSessions();
  }
  if (session.id !== currentId) {
    return;
  }
  typingIndicator.style.display = "none";
  appendMessage("assistant", text, true, !saved);
  sendButton.disabled = messageInput.value.trim() === "";
}

function appendMessage(role, text, stamp, unsaved) {
  const messageDiv = document.createElement("div");
  const extra = unsaved ? " message-unsaved" : "";
  messageDiv.className = "message" + extra;
  const isUser = role === "user";
  const avatar = document.createElement("div");
  avatar.className = "avatar " + (isUser ? "user-avatar" : "ai-avatar");
  avatar.textContent = isUser ? "你" : "规";
  const content = document.createElement("div");
  content.className = "message-content";
  const header = document.createElement("div");
  header.className = "message-header";
  const name = document.createElement("div");
  name.className = "sender-name";
  name.textContent = isUser ? "你" : "ElderBrain";
  const time = document.createElement("div");
  time.className = "message-time";
  time.textContent = stamp ? currentTime() : "";
  const body = document.createElement("div");
  body.className = "message-text " + (isUser ? "user-message" : "ai-message");
  body.textContent = text;
  header.append(name, time);
  content.append(header, body);
  messageDiv.append(avatar, content);
  messagesContainer.insertBefore(messageDiv, typingIndicator);
  scrollDown();
}

function scrollDown() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function currentTime() {
  const now = new Date();
  const hours = String(now.getHours()).padStart(2, "0");
  const minutes = String(now.getMinutes()).padStart(2, "0");
  return hours + ":" + minutes;
}
