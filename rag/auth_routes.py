"""把登录和提问接到同一个入口。演示服务只做 HTTP 适配。"""

from __future__ import annotations

from auth_app import AuthApp
from auth_constants import ERR_NOT_FOUND
from auth_gate import AnswerFn, FailFn, handle_question
from auth_http import Incoming, Outgoing, json_error
from auth_login import handle_login


def route_api(
    app: AuthApp,
    request: Incoming,
    answer: AnswerFn,
    on_failure: FailFn | None,
) -> Outgoing:
    """先看登录路径，再看提问。都不是就 404。"""
    login = handle_login(app, request)
    if login is not None:
        return login
    asked = handle_question(app, request, answer, on_failure)
    if asked is not None:
        return asked
    return json_error(404, ERR_NOT_FOUND)
