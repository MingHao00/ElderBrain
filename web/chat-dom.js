(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) {
    module.exports = api
  }
  if (root) {
    root.ChatDom = api
  }
}(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict'

  const USER_ROLE = 'user'
  const CLOCK_PAD = 2
  const ACTIVE_CLASS = ' active'
  const UNSAVED_CLASS = ' message-unsaved'

  // 规则正文来自服务端，只用 textContent，避免把正文当成 HTML。
  function bind(els) {
    return {
      renderSidebar: function (items, currentId, labelOf) {
        renderSidebar(els.chatHistory, items, currentId, labelOf)
      },
      renderMessages: function (items) {
        renderMessages(els, items)
      },
      appendMessage: function (role, text, stamp, unsaved) {
        return appendMessage(els, role, text, stamp, unsaved)
      },
      scrollDown: function () {
        els.messagesContainer.scrollTop = els.messagesContainer.scrollHeight
      },
      removeUnsaved: function () {
        removeUnsaved(els.messagesContainer)
      },
    }
  }

  function renderSidebar(history, items, currentId, labelOf) {
    history.textContent = ''
    items.forEach(function (item) {
      const row = el('div', 'chat-item' + (item.conversation_id === currentId ? ACTIVE_CLASS : ''))
      row.dataset.id = item.conversation_id
      row.textContent = labelOf(item.title || '')
      history.append(row)
    })
  }

  function renderMessages(els, items) {
    els.messagesContainer.querySelectorAll('.message').forEach(function (node) {
      if (node !== els.greetingMessage) {
        node.remove()
      }
    })
    items.forEach(function (message) {
      if (message && typeof message.text === 'string') {
        appendMessage(els, message.role, message.text, false, false)
      }
    })
    els.messagesContainer.scrollTop = els.messagesContainer.scrollHeight
  }

  function removeUnsaved(container) {
    container.querySelectorAll('.message-unsaved').forEach(function (node) {
      node.remove()
    })
  }

  function appendMessage(els, role, text, stamp, unsaved) {
    const isUser = role === USER_ROLE
    const row = el('div', 'message' + (unsaved ? UNSAVED_CLASS : ''))
    row.append(el('div', avatarClass(isUser), isUser ? '你' : '规'), messageBody(isUser, text, stamp))
    els.messagesContainer.insertBefore(row, els.typingIndicator)
    els.messagesContainer.scrollTop = els.messagesContainer.scrollHeight
    return row.querySelector('.message-text')
  }

  function avatarClass(isUser) {
    return 'avatar ' + (isUser ? 'user-avatar' : 'ai-avatar')
  }

  function messageBody(isUser, text, stamp) {
    const header = el('div', 'message-header')
    header.append(el('div', 'sender-name', isUser ? '你' : 'ElderBrain'))
    header.append(el('div', 'message-time', stamp ? currentTime() : ''))
    const content = el('div', 'message-content')
    const kind = isUser ? 'user-message' : 'ai-message'
    content.append(header, el('div', 'message-text ' + kind, text))
    return content
  }

  function el(tag, className, text) {
    const node = document.createElement(tag)
    node.className = className
    if (text) {
      node.textContent = text
    }
    return node
  }

  function currentTime() {
    const now = new Date()
    const hours = String(now.getHours()).padStart(CLOCK_PAD, '0')
    const minutes = String(now.getMinutes()).padStart(CLOCK_PAD, '0')
    return hours + ':' + minutes
  }

  return {bind: bind}
}))
