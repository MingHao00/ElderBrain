const messageInput = document.getElementById("messageInput");
const sendButton = document.getElementById("sendButton");
const continueButton = document.getElementById("continueButton");
const messagesContainer = document.getElementById("messagesContainer");
const typingIndicator = document.getElementById("typingIndicator");
const chatTitle = document.querySelector(".chat-title");
const chatHistory = document.querySelector(".chat-history");
const greetingMessage = document.getElementById("greetingMessage");
const dom = ChatDom.bind({
  messagesContainer: messagesContainer,
  typingIndicator: typingIndicator,
  greetingMessage: greetingMessage,
  chatHistory: chatHistory,
});
const ASK_PATH = "/api/ask";
const CONVERSATIONS_PATH = "/api/conversations";
const TITLE_LIMIT = 24;
const UNTITLED = "新对话";
const ANSWER_FAILED = "回答失败";
const OFFLINE_NOTICE = "连不上本机服务。";
const DELETE_FAILED = "没有删除这场对话。";
const USER_ROLE = "user";
const ASSISTANT_ROLE = "assistant";
const PAUSE_ICON = "fas fa-pause";
const SEND_ICON = "fas fa-arrow-up";
let conversations = [];
let messages = [];
let currentId = "";
let viewToken = 0;
let view = AskView.idleState();
let streamNode = null;
messageInput.addEventListener("input", onInput);
messageInput.addEventListener("keydown", onKeyDown);
sendButton.addEventListener("click", onPrimaryClick);
continueButton.addEventListener("click", continueTurn);
document.querySelector(".new-chat-btn").addEventListener("click", startNewChat);
document.getElementById("clearChat").addEventListener("click", clearCurrent);
chatHistory.addEventListener("click", onHistoryClick);
syncButtons();
boot();
function onInput() {
  messageInput.style.height = "auto";
  messageInput.style.height = messageInput.scrollHeight + "px";
  syncButtons();
}
function onKeyDown(event) {
  if (event.key !== "Enter" || event.shiftKey) {
    return;
  }
  event.preventDefault();
  if (!sendButton.disabled) {
    onPrimaryClick();
  }
}
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
  return readJson(CONVERSATIONS_PATH).then(function (payload) {
    const found = payload && payload.conversations;
    conversations = Array.isArray(found) ? found : [];
  }).catch(function () {
    conversations = [];
  });
}
function readJson(path) {
  return fetch(path, authInit("GET")).then(function (response) {
    if (!response.ok) {
      return null;
    }
    return response.json();
  });
}
function authInit(method, payload) {
  const headers = {"X-CSRF-Token": csrfToken()};
  const init = {method: method, credentials: "same-origin", headers: headers};
  if (!payload) {
    return init;
  }
  headers["Content-Type"] = "application/json";
  init.body = JSON.stringify(payload);
  return init;
}
function openConversation(id) {
  currentId = id;
  chatTitle.textContent = clipTitle(titleOf(id));
  dom.renderSidebar(conversations, currentId, clipTitle);
  const path = CONVERSATIONS_PATH + "/" + encodeURIComponent(id);
  return readJson(path).then(function (payload) {
    if (currentId === id && payload) {
      showOpened(payload);
    }
  }).catch(function () {
    if (currentId === id) {
      showUnsaved(OFFLINE_NOTICE);
    }
  });
}
function showOpened(payload) {
  const opened = AskView.viewFromConversation(payload);
  messages = opened.messages;
  view = opened;
  streamNode = null;
  dom.renderMessages(messages);
  if (opened.phase === AskView.PHASE_PAUSED && opened.bubble) {
    streamNode = dom.appendMessage(ASSISTANT_ROLE, opened.bubble, true, true);
  }
  typingIndicator.style.display = "none";
  syncButtons();
}
function titleOf(id) {
  const found = conversations.find(function (item) {
    return item.conversation_id === id;
  });
  return found && found.title ? found.title : UNTITLED;
}
function showBlank() {
  currentId = "";
  messages = [];
  view = AskView.idleState();
  streamNode = null;
  chatTitle.textContent = UNTITLED;
  typingIndicator.style.display = "none";
  dom.renderSidebar(conversations, currentId, clipTitle);
  dom.renderMessages(messages);
  syncButtons();
}
function onHistoryClick(event) {
  const item = event.target.closest(".chat-item");
  if (!item || item.dataset.id === currentId) {
    return;
  }
  leaveView();
  openConversation(item.dataset.id);
}
function startNewChat() {
  leaveView();
  messageInput.value = "";
  messageInput.style.height = "auto";
  showBlank();
}
function clearCurrent() {
  if (!currentId) {
    showBlank();
    return;
  }
  const id = currentId;
  viewToken += 1;
  stopStreamingView();
  const path = CONVERSATIONS_PATH + "/" + encodeURIComponent(id);
  fetch(path, authInit("DELETE")).then(function (response) {
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
function stopStreamingView() {
  if (view.phase !== AskView.PHASE_STREAMING) {
    return;
  }
  view = AskView.idleState();
  streamNode = null;
  typingIndicator.style.display = "none";
  syncButtons();
}
function leaveView() {
  viewToken += 1;
  view = AskView.idleState();
  streamNode = null;
  typingIndicator.style.display = "none";
  syncButtons();
}

function showUnsaved(text) {
  dom.removeUnsaved();
  streamNode = dom.appendMessage(ASSISTANT_ROLE, text, true, true);
}
function clipTitle(text) {
  const value = text || UNTITLED;
  if (value.length <= TITLE_LIMIT) {
    return value;
  }
  return value.slice(0, TITLE_LIMIT) + "…";
}
function csrfToken() {
  if (typeof window.elderbrainCsrf === "function") {
    return window.elderbrainCsrf();
  }
  return "";
}
