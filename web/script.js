const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const messagesContainer = document.getElementById("messagesContainer");
const typingIndicator = document.getElementById("typingIndicator");
const chatTitle = document.querySelector(".chat-title");
const chatHistory = document.querySelector(".chat-history");
const greetingMessage = document.getElementById("greetingMessage");
const ASK_PATH = "/api/ask";
const CONVERSATIONS_PATH = "/api/conversations";
const TITLE_LIMIT = 24;
const UNTITLED = "新对话";
const MAX_QUESTION_CHARS = 2000;
const QUESTION_TOO_LONG = "问题太长";
const ANSWER_FAILED = "回答失败";
const OFFLINE_NOTICE = "连不上本机服务。";
const DELETE_FAILED = "没有删除这场对话。";

let conversations = [];
let messages = [];
let currentId = "";
let viewToken = 0;
let inflightToken = 0;
let streamBody = null;
let streamParts = [];

messageInput.addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = this.scrollHeight + "px";
  sendButton.disabled = this.value.trim() === "" || inflightToken === viewToken;
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
boot();

function boot() {
  loadList().then(function () {
    if (!conversations.length) {
      showBlank();
      return;
    }
    openConversation(conversations[0].conversation_id);
  });
}

function loadList() {
  return fetch(CONVERSATIONS_PATH, {
    method: "GET",
    credentials: "same-origin",
    headers: {"X-CSRF-Token": csrfToken()},
  }).then(function (response) {
    if (!response.ok) {
      conversations = [];
      return;
    }
    return response.json().then(function (payload) {
      const found = payload && payload.conversations;
      conversations = Array.isArray(found) ? found : [];
    });
  }).catch(function () {
    conversations = [];
  });
}

function openConversation(id) {
  currentId = id;
  chatTitle.textContent = clipTitle(titleOf(id));
  renderSidebar();
  return fetch(CONVERSATIONS_PATH + "/" + encodeURIComponent(id), {
    method: "GET",
    credentials: "same-origin",
    headers: {"X-CSRF-Token": csrfToken()},
  }).then(function (response) {
    if (!response.ok || currentId !== id) {
      return;
    }
    return response.json().then(function (payload) {
      if (currentId !== id) {
        return;
      }
      const found = payload && payload.messages;
      messages = Array.isArray(found) ? found : [];
      renderMessages();
    });
  }).catch(function () {
    if (currentId === id) {
      showUnsaved(OFFLINE_NOTICE);
    }
  });
}

function titleOf(id) {
  for (let index = 0; index < conversations.length; index += 1) {
    if (conversations[index].conversation_id === id) {
      return conversations[index].title || UNTITLED;
    }
  }
  return UNTITLED;
}

function showBlank() {
  currentId = "";
  messages = [];
  chatTitle.textContent = UNTITLED;
  typingIndicator.style.display = "none";
  renderSidebar();
  renderMessages();
}

function renderSidebar() {
  chatHistory.textContent = "";
  conversations.forEach(function (item) {
    const row = document.createElement("div");
    const active = item.conversation_id === currentId ? " active" : "";
    row.className = "chat-item" + active;
    row.dataset.id = item.conversation_id;
    row.textContent = clipTitle(item.title || UNTITLED);
    chatHistory.append(row);
  });
}

function renderMessages() {
  const nodes = messagesContainer.querySelectorAll(".message");
  nodes.forEach(function (node) {
    if (node !== greetingMessage) {
      node.remove();
    }
  });
  messages.forEach(function (message) {
    if (message && typeof message.text === "string") {
      appendMessage(message.role, message.text, false);
    }
  });
  scrollDown();
}

function onHistoryClick(event) {
  const item = event.target.closest(".chat-item");
  if (!item || item.dataset.id === currentId) {
    return;
  }
  viewToken += 1;
  streamBody = null;
  streamParts = [];
  openConversation(item.dataset.id);
}

function startNewChat() {
  viewToken += 1;
  streamBody = null;
  streamParts = [];
  messageInput.value = "";
  messageInput.style.height = "auto";
  sendButton.disabled = true;
  showBlank();
}

function clearCurrent() {
  if (!currentId) {
    showBlank();
    return;
  }
  const id = currentId;
  viewToken += 1;
  fetch(CONVERSATIONS_PATH + "/" + encodeURIComponent(id), {
    method: "DELETE",
    credentials: "same-origin",
    headers: {"X-CSRF-Token": csrfToken()},
  }).then(function (response) {
    if (!response.ok) {
      showUnsaved(DELETE_FAILED);
      return;
    }
    conversations = conversations.filter(function (item) {
      return item.conversation_id !== id;
    });
    showBlank();
  }).catch(function () {
    showUnsaved(OFFLINE_NOTICE);
  });
}

function sendMessage() {
  const messageText = messageInput.value.trim();
  if (messageText === "" || inflightToken === viewToken) {
    return;
  }
  if (messageText.length > MAX_QUESTION_CHARS) {
    showUnsaved(QUESTION_TOO_LONG);
    return;
  }
  const token = viewToken;
  const originId = currentId;
  inflightToken = token;
  streamBody = null;
  streamParts = [];
  messages.push({role: "user", text: messageText});
  if (!originId) {
    chatTitle.textContent = clipTitle(messageText);
  }
  appendMessage("user", messageText, true);
  messageInput.value = "";
  messageInput.style.height = "auto";
  sendButton.disabled = true;
  ask(originId, messageText, token);
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

function ask(originId, messageText, token) {
  const payload = {question: messageText};
  if (originId) {
    payload.conversation_id = originId;
  }
  typingIndicator.style.display = "flex";
  scrollDown();
  fetch(ASK_PATH, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken(),
    },
    body: JSON.stringify(payload),
  }).then(function (response) {
    if (!response.ok) {
      return response.json().then(function (body) {
        const message = body && body.error ? body.error : ANSWER_FAILED;
        finishError(token, message);
      });
    }
    return readLines(response, token);
  }).catch(function () {
    finishError(token, OFFLINE_NOTICE);
  });
}

function readLines(response, token) {
  if (!response.body || typeof response.body.getReader !== "function") {
    return response.text().then(function (text) {
      text.split("\n").forEach(function (line) {
        applyLine(line, token);
      });
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  function pump() {
    return reader.read().then(function (result) {
      if (result.done) {
        buffer += decoder.decode();
        applyLine(buffer, token);
        return;
      }
      buffer += decoder.decode(result.value, {stream: true});
      const lines = buffer.split("\n");
      buffer = lines.pop();
      lines.forEach(function (line) {
        applyLine(line, token);
      });
      return pump();
    });
  }
  return pump();
}

function applyLine(line, token) {
  const text = line.trim();
  if (!text) {
    return;
  }
  let event = null;
  try {
    event = JSON.parse(text);
  } catch (error) {
    finishError(token, ANSWER_FAILED);
    return;
  }
  if (!event || token !== viewToken && event.event !== "done") {
    return;
  }
  if (event.event === "version" || event.event === "sentence") {
    showSentence(event.text, token);
    return;
  }
  if (event.event === "done") {
    finishDone(token, event.conversation_id);
  }
}

function showSentence(text, token) {
  if (token !== viewToken || typeof text !== "string") {
    return;
  }
  streamParts.push(text);
  const joined = streamParts.join("\n\n");
  if (!streamBody) {
    streamBody = appendMessage("assistant", joined, true);
    return;
  }
  streamBody.textContent = joined;
  scrollDown();
}

function finishDone(token, conversationId) {
  if (token === viewToken && conversationId) {
    currentId = conversationId;
    messages.push({role: "assistant", text: streamParts.join("\n\n")});
  }
  loadList().then(function () {
    renderSidebar();
    if (token === viewToken && currentId) {
      chatTitle.textContent = clipTitle(titleOf(currentId));
    }
    endSend(token);
  });
}

function finishError(token, text) {
  if (token === viewToken) {
    typingIndicator.style.display = "none";
    showUnsaved(text);
  }
  endSend(token);
}

function endSend(token) {
  if (inflightToken === token) {
    inflightToken = 0;
  }
  if (token !== viewToken) {
    return;
  }
  typingIndicator.style.display = "none";
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
  return body;
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
