"""提问请求在打开流之前的校验。"""

from __future__ import annotations

import json

from ask_runtime import AskDenied, AskRuntime
from audit_log import (
    error_text,
    record_rejection,
    record_unauthorized,
    record_unprocessable,
    write_quietly,
)
from auth_app import AuthApp
from auth_constants import (
    ERR_CONVERSATION_REQUIRED,
    ERR_FORBIDDEN,
    ERR_JSON,
    ERR_TURN_RUNNING,
    ERR_UNAUTHORIZED,
    HTTP_BAD_REQUEST,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    PATH_CONVERSATIONS,
    QUESTION_TOO_LONG,
    UNPROCESSABLE_QUESTIONS,
)
from auth_http import Incoming, csrf_ok, json_error, read_json
from auth_session import Visitor, now_seconds, session_visitor
from conversation_store import conversation_owner
from demo_answer import MAX_BODY_BYTES, read_turn
from fastapi import Request

_EMPTY_HELD = ""


def _question(auth: AuthApp, body: bytes) -> tuple[str, str | None]:
    try:
        return read_turn(body)
    except ValueError as exc:
        text = str(exc)
        status = _question_status(text)
        if status == HTTP_UNPROCESSABLE:
            write_quietly(lambda: record_unprocessable(auth.db, body, text))
        raise AskDenied(status, text) from exc


def _question_status(text: str) -> int:
    if text in UNPROCESSABLE_QUESTIONS:
        return HTTP_UNPROCESSABLE
    return HTTP_BAD_REQUEST


def _conversation_id(body: bytes) -> str:
    payload = read_json(body)
    if payload is None:
        raise AskDenied(HTTP_BAD_REQUEST, ERR_JSON)
    raw = payload.get("conversation_id")
    if not isinstance(raw, str) or not raw.strip():
        raise AskDenied(HTTP_UNPROCESSABLE, ERR_CONVERSATION_REQUIRED)
    return raw.strip()


def _optional_id(body: bytes) -> str | None:
    try:
        _question_text, conversation_id = read_turn(body)
    except ValueError:
        return None
    return conversation_id


def _visitor(auth: AuthApp, incoming: Incoming) -> Visitor:
    visitor = session_visitor(auth, incoming, now_seconds())
    if visitor is None:
        write_quietly(lambda: record_unauthorized(auth.db))
        raise AskDenied(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
    if not csrf_ok(incoming):
        raise AskDenied(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    return visitor


def _owner(auth: AuthApp, visitor: Visitor, conversation_id: str) -> None:
    if conversation_owner(auth.db, conversation_id) == visitor.user_id:
        return
    write_quietly(
        lambda: record_rejection(
            auth.db,
            visitor.user_id,
            HTTP_FORBIDDEN,
            ERR_FORBIDDEN,
            0,
        ),
    )
    raise AskDenied(HTTP_FORBIDDEN, ERR_FORBIDDEN)


def _hold(runtime: AskRuntime, conversation_id: str | None) -> str:
    if not conversation_id:
        return _EMPTY_HELD
    if runtime.gate.begin(conversation_id):
        return conversation_id
    raise AskDenied(HTTP_CONFLICT, ERR_TURN_RUNNING)


def _release(runtime: AskRuntime, held: str) -> None:
    runtime.gate.end(held)


def _body(request: Request) -> bytes:
    _reject_length(request)
    raw = getattr(request.state, "body", b"")
    if isinstance(raw, bytes):
        return raw
    return bytes(raw)


def _reject_length(request: Request) -> None:
    problem = _length_problem(request.headers.get("content-length"))
    if problem is None:
        return
    status = HTTP_UNPROCESSABLE if problem == QUESTION_TOO_LONG else HTTP_BAD_REQUEST
    outgoing = json_error(status, problem)
    raise AskDenied(outgoing.status, error_text(outgoing.body))


def _length_problem(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        length = int(raw)
    except ValueError:
        return ERR_JSON
    if length < 0 or length > MAX_BODY_BYTES:
        return QUESTION_TOO_LONG
    return None


def _incoming(request: Request, body: bytes) -> Incoming:
    headers = {key.lower(): value for key, value in request.headers.items()}
    host = "" if request.client is None else request.client.host
    return Incoming(request.method, request.url.path, {}, headers, body, host)


def _item_id(path: str) -> str | None:
    prefix = PATH_CONVERSATIONS + "/"
    if not path.startswith(prefix):
        return None
    item = path[len(prefix) :]
    if not item or "/" in item:
        return None
    return item


def _object(raw: bytes) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None
