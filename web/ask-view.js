(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) {
    module.exports = api
  }
  if (root) {
    root.AskView = api
  }
}(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict'
  const PHASE_IDLE = 'idle'
  const PHASE_STREAMING = 'streaming'
  const PHASE_PAUSED = 'paused'
  const PHASE_DONE = 'done'
  const PHASE_ERROR = 'error'
  const SEND_NAME = '发送'
  const PAUSE_NAME = '暂停'
  const CONTINUE_LABEL = 'Continue Working'
  const PAUSE_PATH = '/api/ask/pause'
  const CONTINUE_PATH = '/api/ask/continue'
  const POST_METHOD = 'POST'
  const EVENT_DELTA = 'delta'
  const EVENT_REPLACE = 'replace'
  const EVENT_PAUSED = 'paused'
  const EVENT_DONE = 'done'
  const EVENT_ERROR = 'error'
  const FIELD_TEXT = 'text'
  const FIELD_PARTIAL = 'partial'
  const FIELD_DETAIL = 'detail'
  const FIELD_EVENT = 'event'
  const FIELD_DATA = 'data'
  const BYTE_LF = 10
  const BYTE_CR = 13
  const FRAME_GAP = 2
  const CRLF_GAP = 4
  /** 还没开始提问时的视图。 */
  function idleState() {
    return {phase: PHASE_IDLE, bubble: '', detail: '', messages: []}
  }
  /** 还没收到字节的 SSE 缓冲。 */
  function emptyParser() {
    return {pending: new Uint8Array(0)}
  }
  /** 新的一轮开始，清掉上一轮气泡。 */
  function beginStream(state) {
    return copyState(state, {phase: PHASE_STREAMING, bubble: '', detail: ''})
  }
  /** 继续同一轮，保留已经显示的半截正文。 */
  function resumeStream(state) {
    return copyState(state, {phase: PHASE_STREAMING, detail: ''})
  }
  // 帧边界是 ASCII。半帧先留在 pending，避免把 UTF-8 字符拆开解码。
  function feedSse(parser, chunk, flush) {
    const merged = concatBytes(bytesOf(parser), asBytes(chunk))
    const taken = takeFrames(merged)
    const flushed = flushRest(taken, flush)
    return {parser: {pending: flushed.rest}, events: flushed.events}
  }
  /** 返回新视图，不改入参。不再把 version 或 sentence 拼进气泡。 */
  function applyEvent(state, event) {
    const name = eventName(event)
    const data = eventData(event)
    if (name === EVENT_DELTA) {
      return copyState(state, {bubble: textOf(state) + fieldText(data, FIELD_TEXT)})
    }
    if (name === EVENT_REPLACE) {
      return copyState(state, {bubble: fieldText(data, FIELD_TEXT)})
    }
    if (name === EVENT_PAUSED) {
      return copyState(state, {
        phase: PHASE_PAUSED,
        bubble: fieldText(data, FIELD_PARTIAL),
      })
    }
    if (name === EVENT_DONE) {
      return copyState(state, {
        phase: PHASE_DONE,
        bubble: fieldText(data, FIELD_TEXT),
        detail: '',
      })
    }
    if (name === EVENT_ERROR) {
      return copyState(state, {
        phase: PHASE_ERROR,
        detail: fieldText(data, FIELD_DETAIL),
      })
    }
    return copyState(state, {})
  }
  /** 发送、暂停和 Continue Working 是否可见。 */
  function buttonState(state, inputText) {
    const streaming = Boolean(state && state.phase === PHASE_STREAMING)
    const hasText = trimmedText(inputText).length > 0
    return {
      sendDisabled: !streaming && !hasText,
      sendName: streaming ? PAUSE_NAME : SEND_NAME,
      continueVisible: Boolean(state && state.phase === PHASE_PAUSED),
      continueLabel: CONTINUE_LABEL,
    }
  }
  /** 去掉空白后为空才拒绝。不再因为字数拒绝。 */
  function questionGate(text) {
    return {allowed: trimmedText(text).length > 0, notice: ''}
  }
  /** 暂停只提交对话号。 */
  function pauseRequest(conversationId) {
    return turnRequest(PAUSE_PATH, conversationId)
  }
  /** 继续只提交对话号，输入框文字不会变成新问题。 */
  function continueRequest(conversationId) {
    return turnRequest(CONTINUE_PATH, conversationId)
  }
  /** 暂停检查点的 partial 只进气泡，不追加进 messages。 */
  function viewFromConversation(payload) {
    const source = payload && Array.isArray(payload.messages) ? payload.messages : []
    const paused = Boolean(payload && payload.paused === true)
    const partial = payload && typeof payload.partial === 'string' ? payload.partial : ''
    return {
      phase: paused ? PHASE_PAUSED : PHASE_IDLE,
      bubble: paused ? partial : '',
      detail: '',
      messages: source.map(copyMessage),
    }
  }
  function turnRequest(path, conversationId) {
    const id = typeof conversationId === 'string' ? conversationId : ''
    return {method: POST_METHOD, path: path, body: {conversation_id: id}}
  }
  function copyMessage(item) {
    return {
      role: item && typeof item.role === 'string' ? item.role : '',
      text: item && typeof item.text === 'string' ? item.text : '',
    }
  }
  function copyState(state, patch) {
    const current = state || idleState()
    return {
      phase: valueOr(patch.phase, current.phase),
      bubble: valueOr(patch.bubble, current.bubble),
      detail: valueOr(patch.detail, current.detail),
      messages: valueOr(patch.messages, current.messages),
    }
  }
  function valueOr(next, current) {
    return next === undefined ? current : next
  }
  function textOf(state) {
    return state && typeof state.bubble === 'string' ? state.bubble : ''
  }
  function eventName(event) {
    return event && typeof event.event === 'string' ? event.event : ''
  }
  function eventData(event) {
    const data = event && event.data
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return {}
    }
    return data
  }
  function fieldText(data, key) {
    return typeof data[key] === 'string' ? data[key] : ''
  }
  function trimmedText(value) {
    return typeof value === 'string' ? value.trim() : ''
  }
  function bytesOf(parser) {
    if (parser && parser.pending instanceof Uint8Array) {
      return parser.pending
    }
    return new Uint8Array(0)
  }
  function asBytes(chunk) {
    if (chunk instanceof Uint8Array) {
      return chunk
    }
    if (typeof chunk === 'string') {
      return new TextEncoder().encode(chunk)
    }
    return new Uint8Array(0)
  }
  function concatBytes(left, right) {
    const merged = new Uint8Array(left.length + right.length)
    merged.set(left, 0)
    merged.set(right, left.length)
    return merged
  }
  function takeFrames(bytes) {
    const events = []
    let rest = bytes
    let found = frameEnd(rest)
    while (found) {
      const event = parseFrame(rest.subarray(0, found.at))
      rest = rest.subarray(found.at + found.length)
      if (event) {
        events.push(event)
      }
      found = frameEnd(rest)
    }
    return {events: events, rest: copyBytes(rest)}
  }
  function flushRest(taken, flush) {
    if (!flush || !taken.rest.length) {
      return taken
    }
    const last = parseFrame(taken.rest)
    const events = last ? taken.events.concat([last]) : taken.events
    return {events: events, rest: new Uint8Array(0)}
  }
  function frameEnd(bytes) {
    for (let index = 0; index < bytes.length - 1; index += 1) {
      const crlf = crlfEnd(bytes, index)
      if (crlf) {
        return crlf
      }
      if (bytes[index] === BYTE_LF && bytes[index + 1] === BYTE_LF) {
        return {at: index, length: FRAME_GAP}
      }
    }
    return null
  }
  function crlfEnd(bytes, index) {
    if (bytes[index] !== BYTE_CR || bytes[index + 1] !== BYTE_LF) {
      return null
    }
    const next = index + FRAME_GAP
    if (next + 1 >= bytes.length) {
      return null
    }
    if (bytes[next] !== BYTE_CR || bytes[next + 1] !== BYTE_LF) {
      return null
    }
    return {at: index, length: CRLF_GAP}
  }
  function parseFrame(bytes) {
    const text = new TextDecoder('utf-8').decode(bytes)
    const fields = text.split(/\r\n|\n|\r/).reduce(addField, emptyFields())
    const data = fields.name ? parseData(fields.dataLines.join('\n')) : null
    if (!data) {
      return null
    }
    return {event: fields.name, data: data}
  }
  function emptyFields() {
    return {name: '', dataLines: []}
  }
  function addField(fields, line) {
    if (!line || line.charAt(0) === ':') {
      return fields
    }
    const split = splitField(line)
    if (split.name === FIELD_EVENT) {
      return {name: split.value, dataLines: fields.dataLines}
    }
    if (split.name !== FIELD_DATA) {
      return fields
    }
    return {name: fields.name, dataLines: fields.dataLines.concat([split.value])}
  }
  function splitField(line) {
    const colon = line.indexOf(':')
    if (colon < 0) {
      return {name: line, value: ''}
    }
    const raw = line.slice(colon + 1)
    const value = raw.charAt(0) === ' ' ? raw.slice(1) : raw
    return {name: line.slice(0, colon), value: value}
  }
  function parseData(raw) {
    if (!raw) {
      return null
    }
    try {
      const data = JSON.parse(raw)
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        return null
      }
      return data
    } catch (_parseError) {
      return null
    }
  }
  function copyBytes(bytes) {
    const copied = new Uint8Array(bytes.length)
    copied.set(bytes, 0)
    return copied
  }
  return {
    PHASE_IDLE: PHASE_IDLE,
    PHASE_STREAMING: PHASE_STREAMING,
    PHASE_PAUSED: PHASE_PAUSED,
    PHASE_DONE: PHASE_DONE,
    PHASE_ERROR: PHASE_ERROR,
    SEND_NAME: SEND_NAME,
    PAUSE_NAME: PAUSE_NAME,
    CONTINUE_LABEL: CONTINUE_LABEL,
    idleState: idleState,
    emptyParser: emptyParser,
    beginStream: beginStream,
    resumeStream: resumeStream,
    feedSse: feedSse,
    applyEvent: applyEvent,
    buttonState: buttonState,
    questionGate: questionGate,
    pauseRequest: pauseRequest,
    continueRequest: continueRequest,
    viewFromConversation: viewFromConversation,
  }
}))
