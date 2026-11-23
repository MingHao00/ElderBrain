"""把一轮模型步骤收成 SSE。工具名和工具结果不进事件。"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator

import fastapi.sse
from ask_runtime import (
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_PAUSED,
    EVENT_REPLACE,
    LEGACY_DONE,
    LEGACY_SENTENCE,
    LEGACY_VERSION,
    POLL_SECONDS,
    AskReady,
    thread_of,
)
from auth_constants import ERR_ANSWER_FAILED
from auth_session import now_seconds
from conversation_store import commit_turn, conversation_messages, conversation_owner
from turn_run import TOOL_CONTENT_TYPE, ModelStep, ToolObservation, TurnCheckpoint, TurnState

_TEXT = "text"
_PARTIAL = "partial"
_DETAIL = "detail"


def iter_legacy(ready: AskReady) -> Iterator[fastapi.sse.ServerSentEvent]:
    """旧的逐句结果改成 delta，结束时带上已经提交的正文。"""
    for item in _rows(ready.legacy_body or b""):
        if item.get("event") not in {LEGACY_VERSION, LEGACY_SENTENCE}:
            continue
        text = item.get(_TEXT)
        if isinstance(text, str):
            yield _sse(EVENT_DELTA, {_TEXT: text})
    yield _sse(EVENT_DONE, {_TEXT: _committed(ready)})


def iter_turn(ready: AskReady) -> Iterator[fastapi.sse.ServerSentEvent]:
    """按模型步骤推送。暂停发生在工具返回之前。"""
    try:
        yield from _steps(ready)
    except Exception:
        ready.runtime.gate.end(ready.held_id)
        yield _sse(EVENT_ERROR, {_DETAIL: ERR_ANSWER_FAILED})


def _steps(ready: AskReady) -> Iterator[fastapi.sse.ServerSentEvent]:
    model = ready.runtime.model
    tool = ready.runtime.tool
    if model is None or tool is None:
        raise RuntimeError(ERR_ANSWER_FAILED)
    visible = ready.visible
    messages = ready.user_messages
    tools = ready.tool_results
    while True:
        if _paused(ready):
            yield from _emit_pause(ready, visible, messages, tools)
            return
        step = model(_state(messages, visible, tools))
        if not step.tool_name:
            yield from _finish(ready, visible, step.partial_text, messages)
            return
        previous = visible
        visible = _shown(previous, step)
        yield from _tool_events(previous, visible)
        if _paused(ready):
            yield from _emit_pause(ready, visible, messages, tools)
            return
        found = _await_tool(ready, step.tool_name)
        if found is None:
            yield from _emit_pause(ready, visible, messages, tools)
            return
        tools = tools + (ToolObservation(step.tool_name, found),)


def _shown(visible: str, step: ModelStep) -> str:
    fragment = step.partial_text
    if not fragment:
        return visible
    if not visible or fragment.startswith(visible):
        return fragment
    return visible + fragment


def _tool_events(
    previous: str,
    visible: str,
) -> Iterator[fastapi.sse.ServerSentEvent]:
    extra = _added(previous, visible)
    if extra:
        yield _sse(EVENT_DELTA, {_TEXT: extra})
    yield _sse(EVENT_REPLACE, {_TEXT: visible})


def _finish(
    ready: AskReady,
    visible: str,
    final: str,
    messages: tuple[str, ...],
) -> Iterator[fastapi.sse.ServerSentEvent]:
    extra = _added(visible, final)
    if extra:
        yield _sse(EVENT_DELTA, {_TEXT: extra})
    _commit(ready, messages, final)
    ready.runtime.store.delete(thread_of(ready.conversation_id))
    ready.runtime.gate.end(ready.held_id)
    yield _sse(EVENT_DONE, {_TEXT: final})


def _added(visible: str, final: str) -> str:
    if not final or final == visible:
        return ""
    if visible and final.startswith(visible):
        return final[len(visible) :]
    return final


def _await_tool(ready: AskReady, name: str) -> str | None:
    # 流要在工具返回前发出暂停，所以工具放在旁边的线程里。
    box: dict[str, object] = {}
    worker = threading.Thread(target=_run_tool, args=(ready, name, box))
    worker.start()
    while worker.is_alive():
        if _paused(ready):
            return None
        worker.join(POLL_SECONDS)
    return _tool_text(box)


def _run_tool(ready: AskReady, name: str, box: dict[str, object]) -> None:
    tool = ready.runtime.tool
    if tool is None:
        box["error"] = RuntimeError(ERR_ANSWER_FAILED)
        return
    try:
        box["content"] = tool(name)
    except Exception as exc:
        box["error"] = exc


def _tool_text(box: dict[str, object]) -> str:
    if "error" in box:
        raise RuntimeError(ERR_ANSWER_FAILED)
    content = box.get("content")
    if not isinstance(content, str):
        raise TypeError(TOOL_CONTENT_TYPE)
    return content


def _emit_pause(
    ready: AskReady,
    visible: str,
    messages: tuple[str, ...],
    tools: tuple[ToolObservation, ...],
) -> Iterator[fastapi.sse.ServerSentEvent]:
    ready.runtime.store.save(_checkpoint(ready, visible, messages, tools))
    ready.runtime.gate.end(ready.held_id)
    yield _sse(EVENT_PAUSED, {_PARTIAL: visible})


def _checkpoint(
    ready: AskReady,
    visible: str,
    messages: tuple[str, ...],
    tools: tuple[ToolObservation, ...],
) -> TurnCheckpoint:
    return TurnCheckpoint(
        thread_id=thread_of(ready.conversation_id),
        turn_id=ready.turn_id or uuid.uuid4().hex,
        partial_text=visible,
        user_messages=messages,
        tool_results=tools,
        answer_committed=False,
    )


def _commit(ready: AskReady, messages: tuple[str, ...], final: str) -> None:
    question = messages[0] if messages else ""
    owner = conversation_owner(ready.auth.db, ready.conversation_id)
    stored_id = ready.conversation_id if owner == ready.user_id else None
    commit_turn(
        ready.auth.db,
        ready.user_id,
        stored_id,
        question,
        final,
        None,
        now_seconds(),
    )


def _committed(ready: AskReady) -> str:
    owner = conversation_owner(ready.auth.db, ready.conversation_id)
    if owner is None:
        return ""
    messages = conversation_messages(ready.auth.db, owner, ready.conversation_id)
    if not messages:
        return ""
    return str(messages[-1]["text"])


def _paused(ready: AskReady) -> bool:
    event = ready.runtime.gate.pause_event(ready.held_id)
    if event is None:
        return False
    return event.is_set()


def _state(
    messages: tuple[str, ...],
    visible: str,
    tools: tuple[ToolObservation, ...],
) -> TurnState:
    return TurnState(user_messages=messages, partial_text=visible, tool_results=tools)


def _rows(body: bytes) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for line in body.decode("utf-8").splitlines():
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            found.append(item)
    return found


def _done_id(body: bytes) -> str:
    for item in _rows(body):
        if item.get("event") != LEGACY_DONE:
            continue
        raw = item.get("conversation_id")
        if isinstance(raw, str):
            return raw
    return ""


def _sse(event: str, data: dict[str, str]) -> fastapi.sse.ServerSentEvent:
    return fastapi.sse.ServerSentEvent(event=event, data=data)
