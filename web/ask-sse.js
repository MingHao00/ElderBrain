(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) {
    module.exports = api
  }
  if (root) {
    root.AskSse = api
  }
}(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict'
  const FIELD_EVENT = 'event'
  const FIELD_DATA = 'data'
  const BYTE_LF = 10
  const BYTE_CR = 13
  const FRAME_GAP = 2
  const CRLF_GAP = 4
  /** 还没收到字节的 SSE 缓冲。 */
  function emptyParser() {
    return {pending: new Uint8Array(0)}
  }
  // 帧边界是 ASCII。半帧先留在 pending，避免把 UTF-8 字符拆开解码。
  function feedSse(parser, chunk, flush) {
    const merged = concatBytes(bytesOf(parser), asBytes(chunk))
    const taken = takeFrames(merged)
    const flushed = flushRest(taken, flush)
    return {parser: {pending: flushed.rest}, events: flushed.events}
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
    emptyParser: emptyParser,
    feedSse: feedSse,
  }
}))
