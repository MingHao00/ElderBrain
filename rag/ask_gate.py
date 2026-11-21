"""网页提问进模型循环之前的闸。不调用模型，也不占配额。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from audit_log import record_rejection, record_unprocessable
from audit_record import TEXT_ENCODING
from auth_constants import HTTP_OK, HTTP_UNPROCESSABLE
from auth_db import AuthDb
from scope import is_prompt_injection

# 页面看到的短拒绝。不带版本句，旧路径的 INJECTION_REFUSAL 保持原样。
INJECTION_REPLY = "这个问题不能回答。"
# 审计原因只用短码。用户原文和命中的词不进 reason。
INJECTION_REASON = "injection"
BLANK_REASON = "blank"
# 注入仍把正文交给页面，不占用 422。审计靠短码和成功状态分开。
INJECTION_LOG_STATUS = HTTP_OK

ModelCall = Callable[..., object]
QuotaCall = Callable[..., object]


@dataclass(frozen=True)
class AskGateResult:
    """空白、注入拒绝或放行。放行只表示可以进入后面的循环。"""

    proceed: bool
    http_status: int | None
    reply: str | None
    page_record: tuple[str, ...]
    model_context: tuple[str, ...]


def gate_ask(
    question: str,
    *,
    model: ModelCall,
    quota: QuotaCall,
    db: AuthDb,
    user_id: str,
    page_record: tuple[str, ...] = (),
    model_context: tuple[str, ...] = (),
) -> AskGateResult:
    """在调用模型之前挡住空白和注入。

    模型和配额只接收，不调用。配额要等循环开始才占。
    版本词不在这里拒绝，所以不调用 refusal_for，也不调用 is_out_of_scope。

    Args:
        question: 用户原文。只含空白字符时视为空白。
        model: 后面循环用的模型。这里不调用。
        quota: 后面循环用的配额。这里不调用。
        db: 拒绝日志写入的账号库。
        user_id: 注入拒绝日志上的访客编号。空白仍由既有函数记为 unknown。
        page_record: 已经显示在页面上的文本。
        model_context: 下一轮可以交给模型的文本。

    Returns:
        空白带 HTTP 422，注入带固定回复，其余 proceed 为 True。

    Raises:
        审计写入失败时原样抛出。本函数不吞掉该错误。
    """
    _accept_later(model, quota)
    shown = tuple(page_record)
    context = tuple(model_context)
    # 空白先于词表。空问题没有可命中的注入词。
    if _is_blank(question):
        return _reject_blank(db, question, shown, context)
    # 只查注入词表。同时带版本词时仍按注入拒绝。
    if is_prompt_injection(question):
        return _reject_injection(db, user_id, question, shown, context)
    return _allow(shown, context)


def _accept_later(model: ModelCall, quota: QuotaCall) -> None:
    """模型和配额留到后面的循环。本步收到即可，不能调用。"""
    del model, quota


def _is_blank(question: str) -> bool:
    return question.strip() == ""


def _reject_blank(
    db: AuthDb,
    question: str,
    page_record: tuple[str, ...],
    model_context: tuple[str, ...],
) -> AskGateResult:
    record_unprocessable(db, _blank_body(question), BLANK_REASON)
    return AskGateResult(
        False,
        HTTP_UNPROCESSABLE,
        None,
        page_record,
        model_context,
    )


def _reject_injection(
    db: AuthDb,
    user_id: str,
    question: str,
    page_record: tuple[str, ...],
    model_context: tuple[str, ...],
) -> AskGateResult:
    # 页面留下原文和回复。模型上下文不追加这句原文。
    record_rejection(
        db,
        user_id,
        INJECTION_LOG_STATUS,
        INJECTION_REASON,
        len(question),
    )
    shown = page_record + (question, INJECTION_REPLY)
    return AskGateResult(
        False,
        None,
        INJECTION_REPLY,
        shown,
        model_context,
    )


def _allow(
    page_record: tuple[str, ...],
    model_context: tuple[str, ...],
) -> AskGateResult:
    return AskGateResult(True, None, None, page_record, model_context)


def _blank_body(question: str) -> bytes:
    """交给既有长度计算。原因另用短码，正文不会写进审计原因。"""
    payload = json.dumps({"question": question}, ensure_ascii=False)
    return payload.encode(TEXT_ENCODING)
