function onPrimaryClick() {
  const buttons = AskView.buttonState(view, messageInput.value);
  if (buttons.sendName === AskView.PAUSE_NAME) {
    pauseTurn();
    return;
  }
  sendMessage();
}
function sendMessage() {
  const messageText = messageInput.value.trim();
  if (!AskView.questionGate(messageText).allowed) {
    return;
  }
  if (view.phase === AskView.PHASE_STREAMING) {
    return;
  }
  const token = viewToken;
  const originId = currentId;
  removeStream();
  view = AskView.beginStream(view);
  messages = messages.concat([{role: USER_ROLE, text: messageText}]);
  if (!originId) {
    chatTitle.textContent = clipTitle(messageText);
  }
  dom.appendMessage(USER_ROLE, messageText, true, false);
  messageInput.value = "";
  messageInput.style.height = "auto";
  syncButtons();
  const payload = {question: messageText};
  if (originId) {
    payload.conversation_id = originId;
  }
  postSse(ASK_PATH, payload, token);
}
function pauseTurn() {
  if (view.phase !== AskView.PHASE_STREAMING || !currentId) {
    return;
  }
  const request = AskView.pauseRequest(currentId, messageInput.value);
  fetch(request.path, authInit(request.method, request.body)).catch(function () {
    failTurn(viewToken, OFFLINE_NOTICE);
  });
}
function continueTurn() {
  if (view.phase !== AskView.PHASE_PAUSED || !currentId) {
    return;
  }
  const request = AskView.continueRequest(currentId, messageInput.value);
  view = AskView.resumeStream(view);
  syncButtons();
  postSse(request.path, request.body, viewToken);
}
function postSse(path, payload, token) {
  typingIndicator.style.display = "flex";
  dom.scrollDown();
  fetch(path, authInit("POST", payload)).then(function (response) {
    if (!response.ok) {
      return response.json().then(function (body) {
        failTurn(token, httpError(body));
      });
    }
    return readSse(response, token);
  }).catch(function () {
    failTurn(token, OFFLINE_NOTICE);
  });
}
function httpError(body) {
  if (body && typeof body.error === "string" && body.error) {
    return body.error;
  }
  return ANSWER_FAILED;
}
function readSse(response, token) {
  if (!response.body || typeof response.body.getReader !== "function") {
    return response.text().then(function (text) {
      deliver(AskView.emptyParser(), text, token, true);
      finishStream(token);
    });
  }
  return pump(response.body.getReader(), AskView.emptyParser(), token);
}
function pump(reader, parser, token) {
  return reader.read().then(function (result) {
    const chunk = result.done ? "" : result.value;
    const next = deliver(parser, chunk, token, result.done);
    if (result.done) {
      finishStream(token);
      return;
    }
    return pump(reader, next, token);
  });
}
function deliver(parser, chunk, token, flush) {
  const fed = AskView.feedSse(parser, chunk, flush);
  if (token !== viewToken) {
    return fed.parser;
  }
  fed.events.forEach(function (event) {
    takeEvent(event, token);
  });
  return fed.parser;
}
function takeEvent(event, token) {
  if (token !== viewToken) {
    return;
  }
  const previous = view;
  view = AskView.applyEvent(previous, event);
  paintEvent(previous);
  if (view.phase === AskView.PHASE_DONE && previous.phase !== view.phase) {
    rememberAnswer(view.bubble);
    refreshTitle();
  }
  if (view.phase !== AskView.PHASE_STREAMING) {
    typingIndicator.style.display = "none";
  }
  syncButtons();
}
function paintEvent(previous) {
  if (view.phase === AskView.PHASE_ERROR) {
    showText(view.detail || ANSWER_FAILED);
    return;
  }
  const paused = view.phase === AskView.PHASE_PAUSED;
  if (view.bubble !== previous.bubble || paused) {
    showText(view.bubble);
  }
}
function finishStream(token) {
  if (token === viewToken && view.phase === AskView.PHASE_STREAMING) {
    failTurn(token, ANSWER_FAILED);
  }
}
function failTurn(token, text) {
  if (token !== viewToken) {
    return;
  }
  view = AskView.applyEvent(view, {event: "error", data: {detail: text}});
  typingIndicator.style.display = "none";
  showText(text);
  syncButtons();
}
function showText(text) {
  if (!streamNode) {
    streamNode = dom.appendMessage(ASSISTANT_ROLE, text, true, true);
    return;
  }
  streamNode.textContent = text;
  dom.scrollDown();
}
function rememberAnswer(text) {
  messages = messages.concat([{role: ASSISTANT_ROLE, text: text}]);
  const row = streamNode && streamNode.closest(".message");
  if (row) {
    row.classList.remove("message-unsaved");
  }
  streamNode = null;
}
function removeStream() {
  const row = streamNode && streamNode.closest(".message");
  if (row) {
    row.remove();
  }
  streamNode = null;
}
function refreshTitle() {
  const token = viewToken;
  const originId = currentId;
  loadList().then(function () {
    if (token !== viewToken) {
      return;
    }
    if (!originId && conversations.length) {
      currentId = conversations[0].conversation_id;
    }
    dom.renderSidebar(conversations, currentId, clipTitle);
    if (currentId) {
      chatTitle.textContent = clipTitle(titleOf(currentId));
    }
  });
}

function syncButtons() {
  const buttons = AskView.buttonState(view, messageInput.value);
  sendButton.disabled = buttons.sendDisabled;
  sendButton.setAttribute("aria-label", buttons.sendName);
  sendButton.title = buttons.sendName;
  const icon = sendButton.querySelector("i");
  if (icon) {
    icon.className = buttons.sendName === AskView.PAUSE_NAME ? PAUSE_ICON : SEND_ICON;
  }
  continueButton.hidden = !buttons.continueVisible;
  continueButton.textContent = buttons.continueLabel;
}
