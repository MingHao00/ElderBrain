"""提问门闩：先看长度，再看身份，再扣配额，最后才调用现有回答。"""

from __future__ import annotations

import sys
from typing import Callable

from alert_mail import send_alert_mail
from auth_app import AuthApp
from audit_log import (
    NO_EVIDENCE_TEXT,
    citation_on,
    error_text,
    record_answer,
    record_limited,
    record_rejection,
    record_unauthorized,
    record_unprocessable,
    usages_on,
    write_quietly,
)
from admin_store import source_is_enabled
from auth_constants import (
    ASK_COST_MICRO,
    COST_ALERT_BODY,
    COST_ALERT_SUBJECT,
    ERR_ANSWER_FAILED,
    ERR_FORBIDDEN,
    ERR_UNAUTHORIZED,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    MICRO_PER_YUAN,
    PATH_ASK,
    PATH_QUERY,
    UNPROCESSABLE_QUESTIONS,
)
from config import SOURCE_ID
from auth_http import Incoming, Outgoing, csrf_ok, json_error
from auth_quota import (
    QuotaDeny,
    Reserved,
    quota_message,
    release_quota,
    reserve_quota,
    settle_cost,
)
from auth_session import Visitor, bearer_visitor, now_seconds, session_visitor
from conversation_store import commit_turn, conversation_owner, load_card
from demo_answer import read_turn
from generation_call import qwen_yuan
from session_card import SessionCard, empty_card
from turn_render import card_update_of, render_ask, render_query

# 异常参数可能带邮局密码或收件地址，所以失败日志只留类型名。
ALERT_SEND_FAILED = "费用告警没有发出"

AnswerFn = Callable[[str, SessionCard], str]
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
        return _gated(app, request, answer, on_failure, _from_cookie, True, True)
    if request.path == PATH_QUERY:
        return _gated(app, request, answer, on_failure, _from_key, False, False)
    return None


def _gated(
    app: AuthApp,
    request: Incoming,
    answer: AnswerFn,
    on_failure: FailFn | None,
    identify: Callable[[AuthApp, Incoming], Visitor | None],
    need_csrf: bool,
    stream: bool,
) -> Outgoing:
    parsed = _turn(request.body)
    if isinstance(parsed, Outgoing):
        write_quietly(lambda: _audit_turn(app, request.body, parsed))
        return parsed
    question, conversation_id = parsed
    visitor = identify(app, request)
    if visitor is None:
        response = json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
        write_quietly(lambda: record_unauthorized(app.db))
        return response
    if need_csrf and not csrf_ok(request):
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    return _charge(app, visitor, question, conversation_id, answer, on_failure, stream)


def _charge(
    app: AuthApp,
    visitor: Visitor,
    question: str,
    conversation_id: str | None,
    answer: AnswerFn,
    on_failure: FailFn | None,
    stream: bool,
) -> Outgoing:
    denied = _not_owner(app, visitor, question, conversation_id)
    if denied is not None:
        return denied
    reserved = _reserve(app, visitor)
    if isinstance(reserved, Outgoing):
        return reserved
    try:
        shown = _shown(app, question, conversation_id, answer)
    except Exception as exc:
        release_quota(app.db, reserved.event_id)
        _report(on_failure, exc)
        return json_error(HTTP_SERVER_ERROR, ERR_ANSWER_FAILED)
    _book(app, reserved.event_id, visitor.user_id, question, shown)
    return _saved(app, visitor, question, conversation_id, shown, stream)


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


def _shown(
    app: AuthApp,
    question: str,
    conversation_id: str | None,
    answer: AnswerFn,
) -> str:
    """来源仍启用时走原来的检索和生成。停用时不再调用它们。"""
    if source_is_enabled(app.db, SOURCE_ID):
        return answer(question, _card(app, conversation_id))
    # 和没有父节时同一句，但引用结果保持未校验，避免每问都进审核队列。
    return NO_EVIDENCE_TEXT


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


def _not_owner(
    app: AuthApp,
    visitor: Visitor,
    question: str,
    conversation_id: str | None,
) -> Outgoing | None:
    if conversation_id is None:
        return None
    if conversation_owner(app.db, conversation_id) == visitor.user_id:
        return None
    response = json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    write_quietly(lambda: _audit_forbidden(app, visitor.user_id, question))
    return response


def _audit_forbidden(app: AuthApp, user_id: str, question: str) -> None:
    record_rejection(app.db, user_id, HTTP_FORBIDDEN, ERR_FORBIDDEN, len(question))


def _reserve(app: AuthApp, visitor: Visitor) -> Reserved | Outgoing:
    reserved = reserve_quota(
        app.db,
        visitor.user_id,
        visitor.created_at,
        ASK_COST_MICRO,
        now_seconds(),
    )
    if isinstance(reserved, QuotaDeny):
        return _limited(app, visitor, reserved)
    return reserved


def _limited(app: AuthApp, visitor: Visitor, reserved: QuotaDeny) -> Outgoing:
    message = quota_message(reserved.kind, reserved.resume_at)
    response = json_error(HTTP_TOO_MANY, message)
    write_quietly(lambda: record_limited(app.db, visitor.user_id, message))
    return response


def _card(app: AuthApp, conversation_id: str | None) -> SessionCard:
    if conversation_id is None:
        return empty_card()
    return load_card(app.db, conversation_id)


def _saved(
    app: AuthApp,
    visitor: Visitor,
    question: str,
    conversation_id: str | None,
    shown: str,
    stream: bool,
) -> Outgoing:
    stored_id = commit_turn(
        app.db,
        visitor.user_id,
        conversation_id,
        question,
        str(shown),
        card_update_of(shown),
        now_seconds(),
    )
    if stream:
        return render_ask(shown, stored_id)
    return render_query(shown, stored_id)


def _turn(body: bytes) -> Outgoing | tuple[str, str | None]:
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
