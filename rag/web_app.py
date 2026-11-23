"""FastAPI 入口。登录、对话、管理和查询仍交给原来的处理函数。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response

from audit_log import citation_on, usages_on
from auth_app import AuthApp
from auth_constants import (
    ERR_ANSWER_FAILED,
    ERR_JSON,
    HTTP_SERVER_ERROR,
    HTTP_UNPROCESSABLE,
    QUESTION_TOO_LONG,
    TEXT_TYPE,
)
from auth_http import Incoming, Outgoing, json_error
from auth_routes import route_api
from demo_answer import MAX_BODY_BYTES, present_answer
from demo_server import (
    CONTENT_TYPES,
    WEB_DIR,
    _body_error,
    _carry,
    _record_oversize,
    _report_failure,
    web_file,
    write_quietly,
)
from session_card import SessionCard
from system import CitedReply, ElderBrainRag

API_PREFIX = "/api/"
GET_METHOD = "GET"
POST_METHOD = "POST"
DELETE_METHOD = "DELETE"
FILE_NOT_FOUND = b"not found"
OCTET_STREAM = "application/octet-stream"
ROOT_PATH = "/"
TAIL_PATH = "/{path:path}"


def build_web_app(rag: ElderBrainRag, auth: AuthApp) -> FastAPI:
    """装上检索和账号库。文档页关掉，避免盖住原来的 404。"""
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.rag = rag
    app.state.auth = auth
    app.middleware("http")(_capture_body)
    _mount(app)
    return app


def answer_with(
    rag: ElderBrainRag,
) -> Callable[[str, SessionCard], str]:
    """和旧页面一样，把检索回答收成页面文本。"""

    def answer(question: str, card: SessionCard) -> str:
        raw = rag.ask(question, card)
        shown = CitedReply(present_answer(raw), citation_on(raw), usages_on(raw))
        _carry(shown, raw)
        return shown

    return answer


def _mount(app: FastAPI) -> None:
    app.add_api_route(ROOT_PATH, _read, methods=[GET_METHOD])
    app.add_api_route(TAIL_PATH, _read, methods=[GET_METHOD])
    app.add_api_route(ROOT_PATH, _post, methods=[POST_METHOD])
    app.add_api_route(TAIL_PATH, _post, methods=[POST_METHOD])
    app.add_api_route(ROOT_PATH, _delete, methods=[DELETE_METHOD])
    app.add_api_route(TAIL_PATH, _delete, methods=[DELETE_METHOD])


async def _capture_body(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    raw = request.headers.get("content-length")
    if request.method == POST_METHOD and _length_problem(raw) is None:
        request.state.body = await request.body()
    else:
        request.state.body = b""
    return await call_next(request)


def _read(request: Request) -> Response:
    if request.url.path.startswith(API_PREFIX):
        return _api(request, GET_METHOD)
    return _page(request)


def _post(request: Request) -> Response:
    return _api(request, POST_METHOD)


def _delete(request: Request) -> Response:
    return _api(request, DELETE_METHOD)


def _page(request: Request) -> Response:
    found = web_file(WEB_DIR, request.url.path)
    if found is None:
        return _bytes(404, FILE_NOT_FOUND, TEXT_TYPE)
    kind = CONTENT_TYPES.get(found.suffix, OCTET_STREAM)
    return _bytes(200, found.read_bytes(), kind)


def _api(request: Request, method: str) -> Response:
    auth: AuthApp = request.app.state.auth
    rag: ElderBrainRag = request.app.state.rag
    loaded = _post_body(request, auth) if method == POST_METHOD else b""
    if isinstance(loaded, Outgoing):
        return _to_response(loaded)
    return _routed(auth, rag, _incoming(request, method, loaded))


def _routed(auth: AuthApp, rag: ElderBrainRag, incoming: Incoming) -> Response:
    try:
        outgoing = route_api(auth, incoming, answer_with(rag), _report_failure)
    except Exception as exc:
        _report_failure(exc)
        outgoing = json_error(HTTP_SERVER_ERROR, ERR_ANSWER_FAILED)
    return _to_response(outgoing)


def _post_body(request: Request, auth: AuthApp) -> bytes | Outgoing:
    raw = request.headers.get("content-length")
    problem = _length_problem(raw)
    if problem is not None:
        return _reject_body(request.url.path, problem, auth, raw)
    length = 0 if raw is None else int(raw)
    return bytes(request.state.body[:length])


def _reject_body(path: str, text: str, auth: AuthApp, raw: str | None) -> Outgoing:
    outgoing = _body_error(path, text)
    if outgoing.status == HTTP_UNPROCESSABLE:
        write_quietly(lambda: _record_oversize(auth.db, text, outgoing.status, raw))
    return outgoing


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


def _incoming(request: Request, method: str, body: bytes) -> Incoming:
    headers = {key.lower(): value for key, value in request.headers.items()}
    host = "" if request.client is None else request.client.host
    return Incoming(method, request.url.path, _first_query(request), headers, body, host)


def _first_query(request: Request) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in found or value == "":
            continue
        found[key] = value
    return found


def _to_response(outgoing: Outgoing) -> Response:
    response = _bytes(outgoing.status, outgoing.body, outgoing.content_type)
    for key, value in outgoing.headers:
        response.headers.append(key, value)
    return response


def _bytes(status: int, body: bytes, content_type: str) -> Response:
    response = Response(content=body, status_code=status)
    response.headers["content-type"] = content_type
    return response
