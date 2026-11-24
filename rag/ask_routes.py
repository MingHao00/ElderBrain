"""提问、暂停和继续的 HTTP。流开始前的失败仍是普通响应。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

import fastapi.sse
from ask_guard import (
    _body,
    _conversation_id,
    _hold,
    _incoming,
    _item_id,
    _object,
    _optional_id,
    _owner,
    _question,
    _release,
    _visitor,
)
from ask_runtime import AskDenied, AskReady, AskRuntime, thread_of
from ask_turn import _done_id, iter_legacy, iter_turn
from audit_log import error_text
from auth_app import AuthApp
from auth_constants import (
    CONVERSATION_HEADER,
    ERR_ANSWER_FAILED,
    ERR_TURN_IDLE,
    HTTP_OK,
    HTTP_SERVER_ERROR,
    HTTP_UNPROCESSABLE,
)
from auth_routes import route_api
from auth_session import Visitor, now_seconds
from conversation_store import (
    conversation_owner,
    ensure_conversation,
    new_conversation_id,
)
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from turn_run import MISSING_CHECKPOINT

_NO_LEGACY = None


def open_ask(request: Request, response: Response) -> AskReady:
    """POST /api/ask 在推流前先做完拒绝。"""
    runtime: AskRuntime = request.app.state.runtime
    auth: AuthApp = request.app.state.auth
    body = _body(request)
    if runtime.model is None or runtime.tool is None:
        ready = _legacy(request, auth, runtime, body)
    else:
        ready = _live(request, auth, runtime, body)
    _stamp_conversation(response, ready.conversation_id)
    return ready


def _stamp_conversation(response: Response, conversation_id: str) -> None:
    """正文流出前让页面拿到这轮对话号，暂停才发得出去。"""
    if conversation_id:
        response.headers[CONVERSATION_HEADER] = conversation_id


def open_continue(request: Request) -> AskReady:
    """继续只认对话号，不把正文当成新问题。"""
    runtime: AskRuntime = request.app.state.runtime
    auth: AuthApp = request.app.state.auth
    body = _body(request)
    conversation_id = _conversation_id(body)
    visitor = _visitor(auth, _incoming(request, body))
    _owner(auth, visitor, conversation_id)
    return _resumed(auth, runtime, visitor, conversation_id)


def post_ask(
    ready: Annotated[AskReady, Depends(open_ask)],
) -> Iterator[fastapi.sse.ServerSentEvent]:
    """POST /api/ask。成功时才是 SSE。"""
    yield from _guarded(ready, True)


def post_continue(
    ready: Annotated[AskReady, Depends(open_continue)],
) -> Iterator[fastapi.sse.ServerSentEvent]:
    """POST /api/ask/continue。接回同一轮。"""
    yield from _guarded(ready, False)


def post_pause(request: Request) -> JSONResponse:
    """POST /api/ask/pause。只收对话号，不打开新的流。"""
    runtime: AskRuntime = request.app.state.runtime
    auth: AuthApp = request.app.state.auth
    body = _body(request)
    conversation_id = _conversation_id(body)
    visitor = _visitor(auth, _incoming(request, body))
    _owner(auth, visitor, conversation_id)
    if not runtime.gate.request_pause(conversation_id):
        raise AskDenied(HTTP_UNPROCESSABLE, ERR_TURN_IDLE)
    return JSONResponse(status_code=HTTP_OK, content={"paused": True})


def attach_pause(request: Request, response: Response) -> Response:
    """未提交的暂停检查点另附 paused 和 partial，不改消息列表。"""
    if response.status_code != HTTP_OK or request.method != "GET":
        return response
    conversation_id = _item_id(request.url.path)
    if conversation_id is None:
        return response
    runtime: AskRuntime = request.app.state.runtime
    # 继续后工具还在跑时，旧检查点不能把未提交正文又画回来。
    if runtime.gate.pause_event(conversation_id) is not None:
        return response
    loaded = runtime.store.load(thread_of(conversation_id))
    if loaded is None or loaded.answer_committed:
        return response
    payload = _object(response.body)
    if payload is None:
        return response
    payload["paused"] = True
    payload["partial"] = loaded.partial_text
    return JSONResponse(status_code=HTTP_OK, content=payload)


def _guarded(
    ready: AskReady,
    allow_legacy: bool,
) -> Iterator[fastapi.sse.ServerSentEvent]:
    try:
        if allow_legacy and ready.legacy_body is not None:
            yield from iter_legacy(ready)
            return
        yield from iter_turn(ready)
    finally:
        ready.runtime.gate.end(ready.held_id)


def _legacy(
    request: Request,
    auth: AuthApp,
    runtime: AskRuntime,
    body: bytes,
) -> AskReady:
    _question(auth, body)
    conversation_id = _optional_id(body)
    held = _hold(runtime, conversation_id)
    try:
        outgoing = route_api(
            auth,
            _incoming(request, body),
            request.app.state.answer,
            request.app.state.on_failure,
        )
    except Exception as exc:
        _release(runtime, held)
        request.app.state.on_failure(exc)
        raise AskDenied(HTTP_SERVER_ERROR, ERR_ANSWER_FAILED) from exc
    if outgoing.status != HTTP_OK:
        _release(runtime, held)
        raise AskDenied(outgoing.status, error_text(outgoing.body))
    return _legacy_ready(auth, runtime, held, outgoing.body)


def _live(
    request: Request,
    auth: AuthApp,
    runtime: AskRuntime,
    body: bytes,
) -> AskReady:
    question, supplied = _question(auth, body)
    visitor = _visitor(auth, _incoming(request, body))
    conversation_id = supplied or new_conversation_id()
    if supplied is not None:
        _owner(auth, visitor, supplied)
    else:
        # 暂停和继续只认已经属于本人的编号，所以公布前先建空对话。
        ensure_conversation(
            auth.db,
            visitor.user_id,
            conversation_id,
            question,
            now_seconds(),
        )
    held = _hold(runtime, conversation_id)
    runtime.store.delete(thread_of(conversation_id))
    return _fresh(auth, runtime, visitor, held, conversation_id, question)


def _resumed(
    auth: AuthApp,
    runtime: AskRuntime,
    visitor: Visitor,
    conversation_id: str,
) -> AskReady:
    if runtime.model is None or runtime.tool is None:
        raise AskDenied(HTTP_UNPROCESSABLE, MISSING_CHECKPOINT)
    loaded = runtime.store.load(thread_of(conversation_id))
    if loaded is None or loaded.answer_committed:
        raise AskDenied(HTTP_UNPROCESSABLE, MISSING_CHECKPOINT)
    held = _hold(runtime, conversation_id)
    # 继续已经把检查点读进内存。留下它的话，刷新会把未提交正文又画出来。
    runtime.store.delete(thread_of(conversation_id))
    return AskReady(
        held_id=held,
        conversation_id=conversation_id,
        user_id=visitor.user_id,
        legacy_body=_NO_LEGACY,
        visible=loaded.partial_text,
        user_messages=loaded.user_messages,
        tool_results=loaded.tool_results,
        turn_id=loaded.turn_id,
        auth=auth,
        runtime=runtime,
    )


def _fresh(
    auth: AuthApp,
    runtime: AskRuntime,
    visitor: Visitor,
    held: str,
    conversation_id: str,
    question: str,
) -> AskReady:
    return AskReady(
        held_id=held,
        conversation_id=conversation_id,
        user_id=visitor.user_id,
        legacy_body=_NO_LEGACY,
        visible="",
        user_messages=(question,),
        tool_results=(),
        turn_id=uuid.uuid4().hex,
        auth=auth,
        runtime=runtime,
    )


def _legacy_ready(
    auth: AuthApp,
    runtime: AskRuntime,
    held: str,
    body: bytes,
) -> AskReady:
    conversation_id = _done_id(body) or held
    owner = conversation_owner(auth.db, conversation_id) or ""
    return AskReady(
        held_id=held,
        conversation_id=conversation_id,
        user_id=owner,
        legacy_body=body,
        visible="",
        user_messages=(),
        tool_results=(),
        turn_id="",
        auth=auth,
        runtime=runtime,
    )


