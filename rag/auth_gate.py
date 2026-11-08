"""提问门闩：先看长度，再看身份，再扣配额，最后才调用现有回答。"""

from __future__ import annotations

import sys
from typing import Callable

from alert_mail import send_alert_mail
from auth_app import AuthApp
from audit_log import (
    citation_on,
    error_text,
    record_answer,
    record_limited,
    record_unauthorized,
    record_unprocessable,
    usages_on,
    write_quietly,
)
from auth_constants import (
    ASK_COST_MICRO,
    COST_ALERT_BODY,
    COST_ALERT_SUBJECT,
    ERR_ANSWER_FAILED,
    ERR_FORBIDDEN,
    ERR_UNAUTHORIZED,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    MICRO_PER_YUAN,
    PATH_ASK,
    PATH_QUERY,
    UNPROCESSABLE_QUESTIONS,
)
from auth_http import Incoming, Outgoing, csrf_ok, json_error, json_response
from auth_quota import (
    QuotaDeny,
    quota_message,
    release_quota,
    reserve_quota,
    settle_cost,
)
from auth_session import Visitor, bearer_visitor, now_seconds, session_visitor
from demo_answer import read_turn
from generation_call import qwen_yuan

# 异常参数可能带邮局密码或收件地址，所以失败日志只留类型名。
ALERT_SEND_FAILED = "费用告警没有发出"

AnswerFn = Callable[[str, list[dict[str, str]]], str]
FailFn = Callable[[Exception], None]


def handle_question(
    app: AuthApp,
    request: Incoming,
    answer: AnswerFn,
    on_failure: FailFn | None,
) -> Outgoing | None:
    """处理网页提问和查询 API。别的路径返回 None。"""
    if request.method != "POST":
        return None
    if request.path == PATH_ASK:
        return _gated(app, request, answer, on_failure, _from_cookie, True)
    if request.path == PATH_QUERY:
        return _gated(app, request, answer, on_failure, _from_key, False)
    return None


def _gated(
    app: AuthApp,
    request: Incoming,
    answer: AnswerFn,
    on_failure: FailFn | None,
    identify: Callable[[AuthApp, Incoming], Visitor | None],
    need_csrf: bool,
) -> Outgoing:
    parsed = _turn(request.body)
    if isinstance(parsed, Outgoing):
        write_quietly(lambda: _audit_turn(app, request.body, parsed))
        return parsed
    question, history = parsed
    visitor = identify(app, request)
    if visitor is None:
        response = json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
        write_quietly(lambda: record_unauthorized(app.db))
        return response
    if need_csrf and not csrf_ok(request):
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    return _charge(app, visitor, question, history, answer, on_failure)


def _charge(
    app: AuthApp,
    visitor: Visitor,
    question: str,
    history: list[dict[str, str]],
    answer: AnswerFn,
    on_failure: FailFn | None,
) -> Outgoing:
    reserved = reserve_quota(
        app.db,
        visitor.user_id,
        visitor.created_at,
        ASK_COST_MICRO,
        now_seconds(),
    )
    if isinstance(reserved, QuotaDeny):
        message = quota_message(reserved.kind, reserved.resume_at)
        response = json_error(HTTP_TOO_MANY, message)
        write_quietly(lambda: record_limited(app.db, visitor.user_id, message))
        return response
    try:
        shown = answer(question, history)
    except Exception as exc:
        release_quota(app.db, reserved.event_id)
        _report(on_failure, exc)
        return json_error(HTTP_SERVER_ERROR, ERR_ANSWER_FAILED)
    _book(app, reserved.event_id, visitor.user_id, question, shown)
    return json_response(HTTP_OK, {"answer": shown})


def _book(
    app: AuthApp,
    event_id: int,
    user_id: str,
    question: str,
    shown: str,
) -> None:
    prompt, completion, yuan, micro = _priced(shown)
    # 费用先提交。发信失败不能把已经给出的回答改成 500，也不能再发一封。
    if settle_cost(app.db, event_id, micro):
        _send_cost_alert(app)
    check = citation_on(shown)
    write_quietly(
        lambda: record_answer(
            app.db,
            user_id,
            question,
            shown,
            check,
            prompt,
            completion,
            yuan,
        ),
    )


def _priced(shown: str) -> tuple[int, int, float, int]:
    """按每次生成的输入长度分档计价，再把费用相加。"""
    prompt = 0
    completion = 0
    yuan = 0.0
    micro = 0
    for call_prompt, call_completion in usages_on(shown):
        call_yuan = qwen_yuan(call_prompt, call_completion)
        prompt += call_prompt
        completion += call_completion
        yuan += call_yuan
        micro += round(call_yuan * MICRO_PER_YUAN)
    return prompt, completion, yuan, micro


def _send_cost_alert(app: AuthApp) -> None:
    try:
        send_alert_mail(
            COST_ALERT_SUBJECT,
            COST_ALERT_BODY,
            deliver=app.alert_deliver,
        )
    except Exception as exc:
        sys.stderr.write(f"{ALERT_SEND_FAILED} {type(exc).__name__}\n")


def _turn(body: bytes) -> Outgoing | tuple[str, list[dict[str, str]]]:
    try:
        return read_turn(body)
    except ValueError as exc:
        text = str(exc)
        if text in UNPROCESSABLE_QUESTIONS:
            return json_error(HTTP_UNPROCESSABLE, text)
        return json_error(HTTP_BAD_REQUEST, text)


def _audit_turn(app: AuthApp, body: bytes, outgoing: Outgoing) -> None:
    if outgoing.status != HTTP_UNPROCESSABLE:
        return
    record_unprocessable(app.db, body, error_text(outgoing.body))


def _from_cookie(app: AuthApp, request: Incoming) -> Visitor | None:
    return session_visitor(app, request, now_seconds())


def _from_key(app: AuthApp, request: Incoming) -> Visitor | None:
    return bearer_visitor(app, request)


def _report(on_failure: FailFn | None, exc: Exception) -> None:
    # 记录失败也不能把已经决定的 500 换成别的状态。
    if on_failure is None:
        return
    try:
        on_failure(exc)
    except Exception:
        return
