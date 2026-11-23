'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const AskView = require('./ask-view')

const LONG_QUESTION_CHARS = 2001
const SAMPLE_ID = 'conv-1'
const DRAFT_TEXT = '不要把这段放进去'
const REPLACED_TEXT = '替换后的正文'

test('emits an event only after a split frame is complete', () => {
  const frame = 'event: delta\ndata: {"text":"火"}\n\n'
  const bytes = new TextEncoder().encode(frame)
  const mid = Math.floor(bytes.length / 2)
  const first = AskView.feedSse(AskView.emptyParser(), bytes.subarray(0, mid))
  assert.deepEqual(first.events, [])
  const second = AskView.feedSse(first.parser, bytes.subarray(mid))
  assert.equal(second.events.length, 1)
  assert.equal(second.events[0].event, 'delta')
  assert.deepEqual(second.events[0].data, {text: '火'})
})

test('fake SSE replaces the bubble and then offers continue', () => {
  const raw = [
    'event: delta\n',
    'data: {"text":"火"}\n\n',
    'event: delta\n',
    'data: {"text":"球"}\n\n',
    'event: replace\n',
    'data: {"text":"' + REPLACED_TEXT + '"}\n\n',
    'event: paused\n',
    'data: {"partial":"' + REPLACED_TEXT + '"}\n\n',
  ].join('')
  const bytes = new TextEncoder().encode(raw)
  const mid = Math.floor(bytes.length / 2)
  const first = AskView.feedSse(AskView.emptyParser(), bytes.subarray(0, mid))
  const second = AskView.feedSse(first.parser, bytes.subarray(mid))
  const events = first.events.concat(second.events)
  const idle = AskView.idleState()
  assert.equal(AskView.buttonState(idle, '   ').sendDisabled, true)
  const streaming = AskView.beginStream(idle)
  const pausing = AskView.buttonState(streaming, '')
  assert.equal(pausing.sendDisabled, false)
  assert.equal(pausing.sendName, '暂停')
  const view = events.reduce(AskView.applyEvent, streaming)
  assert.equal(view.bubble, REPLACED_TEXT)
  assert.equal(view.phase, 'paused')
  assert.equal(streaming.bubble, '')
  const pausedButtons = AskView.buttonState(view, '')
  assert.equal(pausedButtons.continueVisible, true)
  assert.equal(pausedButtons.continueLabel, 'Continue Working')
  const continued = AskView.continueRequest(SAMPLE_ID, DRAFT_TEXT)
  assert.equal(continued.method, 'POST')
  assert.equal(continued.path, '/api/ask/continue')
  assert.deepEqual(continued.body, {conversation_id: SAMPLE_ID})
  assert.equal(JSON.stringify(continued.body).includes(DRAFT_TEXT), false)
})

test('applyEvent appends delta and replaces without mutating input', () => {
  const start = AskView.beginStream(AskView.idleState())
  const appended = AskView.applyEvent(start, {event: 'delta', data: {text: '火'}})
  const grown = AskView.applyEvent(appended, {event: 'delta', data: {text: '球'}})
  assert.equal(grown.bubble, '火球')
  const replaced = AskView.applyEvent(grown, {
    event: 'replace',
    data: {text: REPLACED_TEXT},
  })
  assert.equal(replaced.bubble, REPLACED_TEXT)
  assert.equal(grown.bubble, '火球')
  assert.equal(start.bubble, '')
})

test('ignores legacy version and sentence frames', () => {
  const raw = [
    'event: version\n',
    'data: {"text":"版本"}\n\n',
    'event: sentence\n',
    'data: {"text":"第一句"}\n\n',
    'event: sentence\n',
    'data: {"text":"第二句"}\n\n',
  ].join('')
  const fed = AskView.feedSse(AskView.emptyParser(), raw)
  const view = fed.events.reduce(AskView.applyEvent, AskView.idleState())
  assert.equal(view.bubble, '')
  assert.equal(view.phase, 'idle')
})

test('paused shows Continue Working and done hides it', () => {
  const streaming = AskView.beginStream(AskView.idleState())
  const paused = AskView.applyEvent(streaming, {
    event: 'paused',
    data: {partial: '半截'},
  })
  assert.equal(paused.phase, 'paused')
  assert.equal(paused.bubble, '半截')
  assert.equal(AskView.buttonState(paused, '').continueVisible, true)
  const done = AskView.applyEvent(paused, {event: 'done', data: {text: '全文'}})
  assert.equal(done.phase, 'done')
  assert.equal(done.bubble, '全文')
  assert.equal(AskView.buttonState(done, '').continueVisible, false)
  assert.equal(paused.phase, 'paused')
})

test('error records detail and leaves the previous bubble', () => {
  const streaming = AskView.applyEvent(AskView.beginStream(AskView.idleState()), {
    event: 'delta',
    data: {text: '火'},
  })
  const failed = AskView.applyEvent(streaming, {
    event: 'error',
    data: {detail: '回答失败'},
  })
  assert.equal(failed.phase, 'error')
  assert.equal(failed.detail, '回答失败')
  assert.equal(failed.bubble, '火')
  assert.equal(streaming.phase, 'streaming')
})

test('empty input disables send unless a turn is streaming', () => {
  const idle = AskView.idleState()
  assert.equal(AskView.buttonState(idle, '').sendDisabled, true)
  assert.equal(AskView.buttonState(idle, '  \n').sendDisabled, true)
  assert.equal(AskView.buttonState(idle, '火球').sendName, '发送')
  assert.equal(AskView.buttonState(idle, '火球').sendDisabled, false)
  const streaming = AskView.beginStream(idle)
  assert.equal(idle.phase, 'idle')
  assert.equal(AskView.buttonState(streaming, '').sendName, '暂停')
  assert.equal(AskView.buttonState(streaming, '').continueVisible, false)
})

test('a 2001 character question is allowed and is not too long', () => {
  const longQuestion = '问'.repeat(LONG_QUESTION_CHARS)
  const gate = AskView.questionGate(longQuestion)
  assert.equal(gate.allowed, true)
  assert.equal(JSON.stringify(gate).includes('问题太长'), false)
  assert.equal(AskView.questionGate('   ').allowed, false)
  const buttons = AskView.buttonState(AskView.idleState(), longQuestion)
  assert.equal(buttons.sendDisabled, false)
  assert.equal(buttons.sendName, '发送')
})

test('pause and continue bodies carry only the conversation id', () => {
  const paused = AskView.pauseRequest(SAMPLE_ID, DRAFT_TEXT)
  assert.equal(paused.method, 'POST')
  assert.equal(paused.path, '/api/ask/pause')
  assert.deepEqual(paused.body, {conversation_id: SAMPLE_ID})
  assert.equal(Object.keys(paused.body).length, 1)
  const continued = AskView.continueRequest(SAMPLE_ID, DRAFT_TEXT)
  assert.deepEqual(continued.body, {conversation_id: SAMPLE_ID})
  assert.equal(JSON.stringify(continued).includes(DRAFT_TEXT), false)
})

test('paused conversation keeps partial off the message list', () => {
  const payload = {
    messages: [{role: 'user', text: '火球'}],
    paused: true,
    partial: '未写完',
  }
  const view = AskView.viewFromConversation(payload)
  assert.equal(view.messages.length, 1)
  assert.equal(view.messages[0].text, '火球')
  assert.equal(view.bubble, '未写完')
  assert.equal(view.phase, 'paused')
  assert.equal(AskView.buttonState(view, '').continueVisible, true)
  payload.messages.push({role: 'assistant', text: '未写完'})
  assert.equal(view.messages.length, 1)
  const plain = AskView.viewFromConversation({
    messages: [{role: 'user', text: '火球'}],
  })
  assert.equal(plain.bubble, '')
  assert.equal(AskView.buttonState(plain, '').continueVisible, false)
})

test('reads two frames, comments, and a final flush', () => {
  const pair = [
    'event: delta\n',
    'data: {"text":"火"}\n\n',
    'event: replace\n',
    'data: {"text":"球"}\n\n',
  ].join('')
  const fed = AskView.feedSse(AskView.emptyParser(), pair)
  assert.equal(fed.events.length, 2)
  assert.equal(fed.events[1].data.text, '球')
  const commented = AskView.feedSse(
    AskView.emptyParser(),
    ': keep-alive\nevent: delta\ndata: {"text":"火"}\n\n',
  )
  assert.equal(commented.events.length, 1)
  const held = AskView.feedSse(
    AskView.emptyParser(),
    'event: done\ndata: {"text":"全文"}',
  )
  assert.deepEqual(held.events, [])
  const flushed = AskView.feedSse(held.parser, new Uint8Array(0), true)
  assert.equal(flushed.events[0].data.text, '全文')
  assert.equal(flushed.parser.pending.length, 0)
})

test('reads a CRLF frame and drops non-object data', () => {
  const crlf = 'event: delta\r\ndata: {"text":"火"}\r\n\r\n'
  const fed = AskView.feedSse(AskView.emptyParser(), crlf)
  assert.equal(fed.events[0].data.text, '火')
  const dropped = AskView.feedSse(AskView.emptyParser(), 'event: delta\ndata: "火"\n\n')
  assert.deepEqual(dropped.events, [])
  const ignored = AskView.feedSse(AskView.emptyParser(), null)
  assert.deepEqual(ignored.events, [])
})

test('paused send stays a send button beside Continue Working', () => {
  const paused = AskView.applyEvent(AskView.beginStream(AskView.idleState()), {
    event: 'paused',
    data: {partial: '半截'},
  })
  const empty = AskView.buttonState(paused, '')
  assert.equal(empty.sendDisabled, true)
  assert.equal(empty.sendName, '发送')
  assert.equal(empty.continueVisible, true)
  const typed = AskView.buttonState(paused, '再问')
  assert.equal(typed.sendDisabled, false)
  assert.equal(typed.sendName, '发送')
  const opened = AskView.viewFromConversation({messages: [], paused: true})
  assert.equal(opened.bubble, '')
  assert.equal(opened.phase, 'paused')
  const blank = AskView.viewFromConversation(null)
  assert.deepEqual(blank.messages, [])
  assert.equal(AskView.questionGate(null).allowed, false)
  const ignored = AskView.applyEvent(AskView.idleState(), null)
  assert.equal(ignored.bubble, '')
  assert.equal(ignored.phase, 'idle')
})

test('resume keeps the partial bubble and returns to streaming', () => {
  const paused = AskView.applyEvent(AskView.beginStream(AskView.idleState()), {
    event: 'paused',
    data: {partial: '半截'},
  })
  const resumed = AskView.resumeStream(paused)
  assert.equal(resumed.phase, 'streaming')
  assert.equal(resumed.bubble, '半截')
  assert.equal(paused.phase, 'paused')
  assert.equal(AskView.buttonState(resumed, '').sendName, '暂停')
  assert.equal(AskView.buttonState(resumed, '').continueVisible, false)
})
