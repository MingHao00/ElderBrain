"""把提问结果收成审计。不调用引用校验，也不发告警。"""

from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timezone
from typing import Callable

from audit_record import (
    CITATION_CHECKS,
    CITATION_UNCHECKED,
    COUNT_MIN,
    FIELD_CREATED_AT,
    FIELD_REQUEST_ID,
    FIELD_USER_ID,
    LATENCY_STAGES,
    RESULT_ANSWER,
    RESULT_REFUSAL,
    TEXT_ENCODING,
    build_request_audit,
)
from audit_store import insert_audit
from auth_constants import (
    ERR_UNAUTHORIZED,
    HTTP_OK,
    HTTP_TOO_MANY,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
)
from auth_db import AuthDb
from auth_session import now_seconds
from config import RULE_VERSION, SOURCE_ID
from demo_answer import REFUSAL_LINES
from generation_call import QWEN_MODEL
from scope import INJECTION_REFUSAL, REFUSAL, VERSION_SENTENCE

# 认不出身份时用这个假名。不能写成邮箱或 GitHub 用户名。
UNKNOWN_USER_ID = "unknown"
# 演示页和规则库都是中文，这条路径没有语言检测。
ANSWER_LANG = "zh"
# 回答函数只交回页面文本，没有交出版本识别结果。
UNDETECTED_VERSION = ""
UNKNOWN_REFUSAL_CLASS = ""
# 128 位只做请求编号，不是会话令牌。
REQUEST_ID_BYTES = 16
AUDIT_WRITE_FAILED = "审计没有写入"
# present_answer 在未知拒答类别时用这句。
UNKNOWN_CATEGORY_REFUSAL = "不能回答这个问题。"
# 与生成模块的证据不足是同一句。这里不导入生成客户端。
NO_EVIDENCE_TEXT = VERSION_SENTENCE + "没有找到足够的原文。"
_EMPTY_REASON = ""


def citation_on(shown: str) -> str:
    """取出文本上的引用校验结果。普通字符串没跑过校验，保持未校验。"""
    check = getattr(shown, "citation_check", CITATION_UNCHECKED)
    if isinstance(check, str) and check in CITATION_CHECKS:
        return check
    return CITATION_UNCHECKED


def record_answer(
    db: AuthDb,
    user_id: str,
    question: str,
    shown: str,
    citation_check: str = CITATION_UNCHECKED,
) -> None:
    """成功提问写一条审计。检索和引用列表目前是空的。

    没跑引用校验时保持未校验，不能记成通过。
    """
    # 调用方没跑校验时用默认值，不能把缺省记成通过。
    index_version, prompt_version, model_id, guard_version = _versions()
    record = build_request_audit(
        _request_id(),
        user_id,
        _moment(),
        question,
        ANSWER_LANG,
        UNDETECTED_VERSION,
        _result_type(shown),
        UNKNOWN_REFUSAL_CLASS,
        [],
        [],
        citation_check,
        index_version,
        prompt_version,
        model_id,
        guard_version,
        # token 和费用现有回答不返回，先记 0。
        COUNT_MIN,
        COUNT_MIN,
        COUNT_MIN,
        _unmeasured_latency(),
    )
    _store_answer(db, record)


def record_rejection(
    db: AuthDb,
    user_id: str,
    http_status: int,
    reason: str,
    question_len: int | None,
) -> None:
    """拒绝记录只留编号、时间和原因。不写令牌，也不写成回答或拒答。"""
    insert_audit(
        db,
        _request_id(),
        user_id,
        _moment().isoformat(),
        http_status,
        reason,
        question_len,
        None,
    )


def record_unprocessable(db: AuthDb, body: bytes, reason: str) -> None:
    """422 在认身份之前。用户记为 unknown，只留原因和长度。"""
    record_rejection(
        db,
        UNKNOWN_USER_ID,
        HTTP_UNPROCESSABLE,
        reason,
        _question_length(body),
    )


def record_unauthorized(db: AuthDb) -> None:
    """401 还没有访客编号。不读取 Cookie 或密钥。"""
    record_rejection(db, UNKNOWN_USER_ID, HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED, None)


def record_limited(db: AuthDb, user_id: str, reason: str) -> None:
    """429 用访客假名。原因只复用已经返回给客户端的那句。"""
    record_rejection(db, user_id, HTTP_TOO_MANY, reason, None)


def error_text(raw: bytes) -> str:
    """从错误 JSON 取出原因。这不是问题原文。"""
    try:
        payload = json.loads(raw.decode(TEXT_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _EMPTY_REASON
    if not isinstance(payload, dict):
        return _EMPTY_REASON
    text = payload.get("error")
    if isinstance(text, str):
        return text
    return _EMPTY_REASON


def write_quietly(write: Callable[[], None]) -> None:
    """执行审计写入。失败只打一行，调用方仍返回原来的响应。"""
    try:
        write()
    except Exception as exc:
        _log_failure(exc)


def _store_answer(db: AuthDb, record: dict[str, object]) -> None:
    insert_audit(
        db,
        str(record[FIELD_REQUEST_ID]),
        str(record[FIELD_USER_ID]),
        str(record[FIELD_CREATED_AT]),
        HTTP_OK,
        _EMPTY_REASON,
        None,
        json.dumps(record, ensure_ascii=False),
    )


def _versions() -> tuple[str, str, str, str]:
    """索引、提示词、模型和拦截。没有独立版本号时用现成常量。"""
    # 来源编号是建库用的。提示词和拦截还没有版本常量，记规则版本。
    return SOURCE_ID, RULE_VERSION, QWEN_MODEL, RULE_VERSION


def _result_type(shown: str) -> str:
    if _refusal_tone(shown):
        return RESULT_REFUSAL
    return RESULT_ANSWER


def _refusal_tone(shown: str) -> bool:
    # 页面只有纯文本。这些句子来自现有拒答文案。
    marks = (
        *REFUSAL_LINES.values(),
        REFUSAL,
        INJECTION_REFUSAL,
        NO_EVIDENCE_TEXT,
        UNKNOWN_CATEGORY_REFUSAL,
    )
    return any(mark in shown for mark in marks)


def _unmeasured_latency() -> dict[str, int]:
    # 现有回答一次返回整段文本，没有检查、检索、首字、完整回答的分段计时。
    return {stage: COUNT_MIN for stage in LATENCY_STAGES}


def _question_length(body: bytes) -> int:
    # 只把长度交出去。原文留在这个函数里，不进审计行。
    question = _question_text(body)
    if question is None:
        return len(body)
    return len(question)


def _question_text(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode(TEXT_ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    question = payload.get("question")
    if isinstance(question, str):
        return question
    return None


def _request_id() -> str:
    return secrets.token_hex(REQUEST_ID_BYTES)


def _moment() -> datetime:
    return datetime.fromtimestamp(now_seconds(), timezone.utc)


def _log_failure(exc: Exception) -> None:
    # 异常参数可能含问题原文或令牌，所以只留类型名。
    sys.stderr.write(f"{AUDIT_WRITE_FAILED} {type(exc).__name__}\n")
