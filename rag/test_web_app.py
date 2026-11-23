"""新入口上的旧路由。状态和正文要与直接调用处理函数相同。"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_app import AuthApp
from auth_constants import (
    PATH_ADMIN_REVIEW_QUEUE,
    PATH_ASK,
    PATH_CONVERSATIONS,
    PATH_QUERY,
    PATH_SESSION,
)
from auth_db import Account, AuthDb
from auth_http import Outgoing
from auth_identity import hash_ip, ip_salt
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import auth_env, cookie_header, json_bytes, make_request
from demo_answer import MAX_BODY_BYTES
from demo_server import _report_failure
from fastapi.testclient import TestClient
from session_card import SessionCard
from web_app import answer_with, build_web_app

CLIENT_HOST = "testclient"
CSRF_TOKEN = "csrf-token-for-tests-0001"
USER_EMAIL = "web-app@example.com"
QUESTION = "火球术射程"
PAGE_MARK = b"<!DOCTYPE html>"
MISSING_PAGE = "/missing-page.html"
OUTSIDE_PAGE = "/../demo_server.py"
NOT_FOUND_PAGE = b"not found"


class _Rag:
    """不连模型。被调用时只回一句固定原文。"""

    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask(self, question: str, card: SessionCard) -> str:
        del card
        self.questions.append(question)
        return "射程见原文。"


@contextmanager
def _opened() -> Iterator[AuthApp]:
    env = auth_env()
    env.__enter__()
    folder = tempfile.TemporaryDirectory()
    try:
        db = AuthDb(Path(folder.name) / "auth.sqlite")
        yield AuthApp(db, lambda _recipient, _code: None, None)
    finally:
        folder.cleanup()
        env.__exit__(None, None, None)


def test_session_conversation_admin_and_query_match() -> None:
    """登录、对话、管理和查询的响应与旧处理函数一致。"""
    with _opened() as auth:
        rag = _Rag()
        headers = _session_headers(auth)
        body = json_bytes({"question": QUESTION})
        app = build_web_app(rag, auth)
        with TestClient(app) as client:
            _same(client, auth, rag, "GET", PATH_SESSION, b"", headers)
            _same(client, auth, rag, "GET", PATH_CONVERSATIONS, b"", headers)
            _same(client, auth, rag, "GET", PATH_ADMIN_REVIEW_QUEUE, b"", {})
            _same_text(client, auth, rag, "POST", PATH_ASK, body, headers)
            _same(client, auth, rag, "POST", PATH_QUERY, body, {})
        assert rag.questions == [QUESTION, QUESTION]


def test_unknown_api_matches_old_not_found() -> None:
    with _opened() as auth:
        rag = _Rag()
        app = build_web_app(rag, auth)
        with TestClient(app) as client:
            _same(client, auth, rag, "GET", "/api/missing", b"", {})


def test_index_is_served_and_outside_files_are_not() -> None:
    with _opened() as auth:
        app = build_web_app(_Rag(), auth)
        with TestClient(app) as client:
            page = client.get("/")
            missing = client.get(MISSING_PAGE)
            outside = client.get(OUTSIDE_PAGE)
    assert page.status_code == 200
    assert PAGE_MARK in page.content
    assert missing.status_code == 404
    assert missing.content == NOT_FOUND_PAGE
    assert outside.status_code == 404
    assert outside.content == NOT_FOUND_PAGE


def test_oversize_query_stays_unprocessable() -> None:
    with _opened() as auth:
        app = build_web_app(_Rag(), auth)
        payload = b"x" * (MAX_BODY_BYTES + 1)
        with TestClient(app) as client:
            response = client.post(PATH_QUERY, content=payload)
    assert response.status_code == 422
    assert "问题太长" in response.text


def _same_text(
    client: TestClient,
    auth: AuthApp,
    rag: _Rag,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """新入口的提问是 SSE，不再和旧 NDJSON 正文逐条比较。"""
    response = client.request(method, path, content=body, headers=headers)
    direct = _direct(auth, rag, method, path, body, headers)
    assert response.status_code == direct.status
    assert "text/event-stream" in response.headers["content-type"]
    assert "ndjson" in direct.content_type



def _same(
    client: TestClient,
    auth: AuthApp,
    rag: _Rag,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> None:
    response = client.request(method, path, content=body, headers=headers)
    direct = _direct(auth, rag, method, path, body, headers)
    assert response.status_code == direct.status
    assert response.content == direct.body


def _direct(
    auth: AuthApp,
    rag: _Rag,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
) -> Outgoing:
    request = make_request(method, path, body, headers, client_ip=CLIENT_HOST)
    return route_api(auth, request, answer_with(rag), _report_failure)


def _session_headers(auth: AuthApp) -> dict[str, str]:
    salt = ip_salt()
    assert salt is not None
    account = auth.db.login_email(USER_EMAIL, hash_ip(CLIENT_HOST, salt), now_seconds())
    assert isinstance(account, Account)
    issued = issue_for(account, now_seconds())
    assert issued is not None
    token, _role = issued
    return {"cookie": cookie_header(token, CSRF_TOKEN), "x-csrf-token": CSRF_TOKEN}
