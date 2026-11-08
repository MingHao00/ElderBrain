"""第一期审计记录的纯函数。不接提问接口，不落库，也不做定时删除。"""

import hashlib
import ipaddress
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

# 删对话不删审计，请求审计留 90 天供排查和抽样。
REQUEST_AUDIT_RETENTION_DAYS = 90
# 设计中的 1 年按 365 天计，不按日历年进位，避免闰日结果不同。
ADMIN_AUDIT_RETENTION_DAYS = 365
# 哈希仍可能和短期访问日志对照，所以比请求正文更短。
IP_HASH_RETENTION_DAYS = 7
KIND_REQUEST_AUDIT, KIND_ADMIN_AUDIT = "request_audit", "admin_audit"
KIND_IP_HASH = "ip_hash"
RETENTION_DAYS_BY_KIND = {
    KIND_REQUEST_AUDIT: REQUEST_AUDIT_RETENTION_DAYS,
    KIND_ADMIN_AUDIT: ADMIN_AUDIT_RETENTION_DAYS,
    KIND_IP_HASH: IP_HASH_RETENTION_DAYS,
}

RESULT_ANSWER, RESULT_REFUSAL = "回答", "拒答"
RESULT_TYPES = frozenset({RESULT_ANSWER, RESULT_REFUSAL})
CITATION_PASSED = "通过"
CITATION_PASSED_AFTER_RETRY, CITATION_FAILED = "重试后通过", "失败"
# 没跑引用校验的成功记录用这个，不能写成「通过」。
CITATION_UNCHECKED = "未校验"
CITATION_CHECKS = frozenset({
    CITATION_PASSED,
    CITATION_PASSED_AFTER_RETRY,
    CITATION_FAILED,
    CITATION_UNCHECKED,
})
ACTION_CONNECT_SOURCE, ACTION_DISABLE_SOURCE = "接入来源", "停用来源"
ACTION_PUBLISH, ACTION_ROLLBACK, ACTION_ADJUST_QUOTA = "发布", "回退", "调整配额"
ADMIN_ACTIONS = frozenset({
    ACTION_CONNECT_SOURCE, ACTION_DISABLE_SOURCE, ACTION_PUBLISH,
    ACTION_ROLLBACK, ACTION_ADJUST_QUOTA,
})
LATENCY_CHECK, LATENCY_RETRIEVAL = "检查", "检索"
LATENCY_FIRST_TOKEN, LATENCY_COMPLETE_ANSWER = "首字", "完整回答"
LATENCY_STAGES = (LATENCY_CHECK, LATENCY_RETRIEVAL, LATENCY_FIRST_TOKEN, LATENCY_COMPLETE_ANSWER)
RATING_UP, RATING_DOWN = "好评", "差评"
FEEDBACK_RATINGS = frozenset({RATING_UP, RATING_DOWN})
FIELD_REQUEST_ID, FIELD_USER_ID, FIELD_ACTOR_ID = "request_id", "user_id", "actor_id"
FIELD_CREATED_AT, FIELD_QUESTION, FIELD_LANG = "created_at", "question", "lang"
FIELD_DETECTED_VERSION, FIELD_RESULT_TYPE = "detected_version", "result_type"
FIELD_REFUSAL_CLASS, FIELD_RETRIEVED, FIELD_CITED = "refusal_class", "retrieved", "cited"
FIELD_CITATION_CHECK, FIELD_INDEX_VERSION = "citation_check", "index_version"
FIELD_PROMPT_VERSION, FIELD_MODEL_ID = "prompt_version", "model_id"
FIELD_GUARD_VERSION, FIELD_TOKENS_IN = "guard_version", "tokens_in"
FIELD_TOKENS_OUT, FIELD_COST_ESTIMATE = "tokens_out", "cost_estimate"
FIELD_LATENCY_MS, FIELD_FEEDBACK = "latency_ms", "feedback"
FIELD_ENTRY_ID, FIELD_SIMILARITY = "entry_id", "similarity"
FIELD_RATING, FIELD_NOTE, FIELD_ACTION = "rating", "note", "action"
RETRIEVED_ITEM_KEYS = frozenset({FIELD_ENTRY_ID, FIELD_SIMILARITY})
FEEDBACK_KEYS = frozenset({FIELD_RATING, FIELD_NOTE})
COUNT_MIN = 0
TEXT_ENCODING = "utf-8"
# NUL 不会出现在 IP 文本里，用来切断 salt 与 IP 的拼接边界。
HASH_SEPARATOR = b"\x00"
# 前缀后还要有一段，避免短编号被当成令牌；边界避免 mask- 里的 sk- 误伤。
_TOKEN_PREFIX = r"(?:bearer\s+|sk-|github_pat_|gh[pousr]_|glpat-|xox[baprs]-)"
TOKEN_PATTERN = re.compile(
    rf"(?:^|[^A-Za-z0-9]){_TOKEN_PREFIX}[A-Za-z0-9_\-]{{8,}}", re.IGNORECASE,
)
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
GITHUB_URL_PATTERN = re.compile(r"github\.com/[A-Za-z0-9][A-Za-z0-9\-]{0,38}", re.IGNORECASE)
_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4_PATTERN = re.compile(rf"(?<!\d)(?:{_IPV4_OCTET}\.){{3}}{_IPV4_OCTET}(?!\d)")
IPV6_CANDIDATE = re.compile(r"(?:[A-Fa-f0-9]{0,4}:){2,7}[A-Fa-f0-9]{0,4}")


class AuditRecordRejected(ValueError):
    """字段不合法，或含有不能落盘的敏感内容。"""


def build_request_audit(
    request_id: str, user_id: str, created_at: datetime, question: str,
    lang: str, detected_version: str, result_type: str, refusal_class: str,
    retrieved: Sequence[Mapping[str, object]], cited: Sequence[str],
    citation_check: str, index_version: str, prompt_version: str,
    model_id: str, guard_version: str, tokens_in: int, tokens_out: int,
    cost_estimate: int | float, latency_ms: Mapping[str, object],
    feedback: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """组成一条请求审计。非法取值或敏感字段抛出 AuditRecordRejected。"""
    record: dict[str, object] = {
        FIELD_REQUEST_ID: _text(request_id, FIELD_REQUEST_ID, False),
        FIELD_USER_ID: _pseudonym(user_id, FIELD_USER_ID),
        FIELD_CREATED_AT: _moment(created_at).isoformat(),
        FIELD_QUESTION: _text(question, FIELD_QUESTION, True),
        FIELD_LANG: _text(lang, FIELD_LANG, False),
        FIELD_DETECTED_VERSION: _text(detected_version, FIELD_DETECTED_VERSION, True),
        FIELD_RESULT_TYPE: _choice(result_type, RESULT_TYPES, FIELD_RESULT_TYPE),
        # 页面只有纯文本，拒答类别可能还没有。空字符串表示没拿到。
        FIELD_REFUSAL_CLASS: _text(refusal_class, FIELD_REFUSAL_CLASS, True),
        FIELD_RETRIEVED: _retrieved(retrieved),
        FIELD_CITED: [
            _text(item, FIELD_ENTRY_ID, False)
            for item in _as_list(cited, FIELD_CITED)
        ],
        FIELD_CITATION_CHECK: _choice(
            citation_check, CITATION_CHECKS, FIELD_CITATION_CHECK,
        ),
        FIELD_INDEX_VERSION: _text(index_version, FIELD_INDEX_VERSION, False),
        FIELD_PROMPT_VERSION: _text(prompt_version, FIELD_PROMPT_VERSION, False),
        FIELD_MODEL_ID: _text(model_id, FIELD_MODEL_ID, False),
        FIELD_GUARD_VERSION: _text(guard_version, FIELD_GUARD_VERSION, False),
        FIELD_TOKENS_IN: _count(tokens_in, FIELD_TOKENS_IN),
        FIELD_TOKENS_OUT: _count(tokens_out, FIELD_TOKENS_OUT),
        FIELD_COST_ESTIMATE: _number(cost_estimate, FIELD_COST_ESTIMATE, COUNT_MIN),
        FIELD_LATENCY_MS: _latency(latency_ms),
        FIELD_FEEDBACK: _feedback(feedback),
    }
    _scan_record(record)
    return record


def build_admin_audit(
    actor_id: str, created_at: datetime, action: str,
) -> dict[str, object]:
    """组成一条管理审计。动作不在允许集合内时拒绝。"""
    record = {
        FIELD_ACTOR_ID: _pseudonym(actor_id, FIELD_ACTOR_ID),
        FIELD_CREATED_AT: _moment(created_at).isoformat(),
        FIELD_ACTION: _choice(action, ADMIN_ACTIONS, FIELD_ACTION),
    }
    _scan_record(record)
    return record


def attach_feedback(
    record: Mapping[str, object], feedback: Mapping[str, object] | None,
) -> dict[str, object]:
    """事后补写反馈并返回新记录。备注里的敏感内容会被拒绝。"""
    if not isinstance(record, Mapping) or FIELD_FEEDBACK not in record:
        raise AuditRecordRejected("只能给请求审计补写反馈")
    updated = dict(record)
    updated[FIELD_FEEDBACK] = _feedback(feedback)
    _scan_record(updated)
    return updated


def hash_client_ip(client_ip: str, ip_salt: str) -> str:
    """用调用方 salt 计算限流用的 sha256。调用方单独保存，不写入请求审计。"""
    # 请求审计保留 90 天，而这枚哈希只用于限流、保留 7 天，所以不能写进请求记录。
    # 盐由调用方传入。写死就等于没有盐，摘要里也不能出现 IP 或 salt。
    normalized_ip = _ip(client_ip)
    if not isinstance(ip_salt, str) or ip_salt.strip() == "":
        raise AuditRecordRejected("计算 IP 哈希需要调用方提供 salt")
    try:
        payload = ip_salt.encode(TEXT_ENCODING) + HASH_SEPARATOR
        payload += normalized_ip.encode(TEXT_ENCODING)
    except UnicodeError as exc:
        raise AuditRecordRejected("文本无法编码") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if normalized_ip in digest or ip_salt in digest:
        raise AuditRecordRejected("哈希与原文重叠，拒绝写入")
    return digest


def audit_expires_at(created_at: datetime, record_kind: str) -> datetime:
    """按创建时间计算到期时刻。只返回时间，不删除记录。"""
    stamped = _moment(created_at)
    if record_kind not in RETENTION_DAYS_BY_KIND:
        raise AuditRecordRejected("未知的保留类别")
    return stamped + timedelta(days=RETENTION_DAYS_BY_KIND[record_kind])


def _text(value: object, field_name: str, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise AuditRecordRejected(f"{field_name} 必须是字符串")
    if not allow_empty and value.strip() == "":
        raise AuditRecordRejected(f"{field_name} 不能为空")
    return value


def _pseudonym(value: object, field_name: str) -> str:
    text = _text(value, field_name, False)
    # 假名由调用方生成。带 @ 的值会被当成邮箱或 GitHub 用户名。
    if _secret(text):
        raise AuditRecordRejected(f"{field_name} 不能是邮箱、GitHub 用户名或其他敏感值")
    return text


def _choice(value: object, allowed: frozenset[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise AuditRecordRejected(f"{field_name} 取值不在允许范围内")
    return value


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < COUNT_MIN:
        raise AuditRecordRejected(f"{field_name} 必须是非负整数")
    return value


def _number(value: object, field_name: str, minimum: float | None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditRecordRejected(f"{field_name} 必须是有限数值")
    below = minimum is not None and value < minimum
    if below or not math.isfinite(value):
        raise AuditRecordRejected(f"{field_name} 必须是有限数值")
    return float(value)


def _moment(value: object) -> datetime:
    # 不用 date。只有日期时，保留期限会从零点悄悄起算。
    if not isinstance(value, datetime):
        raise AuditRecordRejected(f"{FIELD_CREATED_AT} 必须是日期时间")
    return value


def _as_list(value: object, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AuditRecordRejected(f"{field_name} 必须是列表")
    return value


def _retrieved(retrieved: object) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for item in _as_list(retrieved, FIELD_RETRIEVED):
        if not isinstance(item, Mapping) or set(item) != RETRIEVED_ITEM_KEYS:
            raise AuditRecordRejected("retrieved 只能包含 entry_id 和 similarity")
        hits.append({
            FIELD_ENTRY_ID: _text(item[FIELD_ENTRY_ID], FIELD_ENTRY_ID, False),
            FIELD_SIMILARITY: _number(item[FIELD_SIMILARITY], FIELD_SIMILARITY, None),
        })
    return hits


def _latency(latency_ms: object) -> dict[str, int]:
    if not isinstance(latency_ms, Mapping):
        raise AuditRecordRejected(f"{FIELD_LATENCY_MS} 必须是分阶段耗时")
    if set(latency_ms) != set(LATENCY_STAGES):
        joined = "、".join(LATENCY_STAGES)
        raise AuditRecordRejected(f"{FIELD_LATENCY_MS} 必须包含{joined}")
    return {stage: _count(latency_ms[stage], stage) for stage in LATENCY_STAGES}


def _feedback(feedback: Mapping[str, object] | None) -> dict[str, str] | None:
    if feedback is None:
        return None
    if not isinstance(feedback, Mapping) or set(feedback) - FEEDBACK_KEYS:
        raise AuditRecordRejected(f"{FIELD_FEEDBACK} 格式不正确")
    rating = _text(feedback.get(FIELD_RATING, ""), FIELD_RATING, True)
    note = _text(feedback.get(FIELD_NOTE, ""), FIELD_NOTE, True)
    if rating != "" and rating not in FEEDBACK_RATINGS:
        raise AuditRecordRejected(f"{FIELD_FEEDBACK} 评分只能是好评或差评")
    if rating == "" and note == "":
        return None
    return {FIELD_RATING: rating, FIELD_NOTE: note}


def _ip(client_ip: object) -> str:
    if not isinstance(client_ip, str):
        raise AuditRecordRejected("IP 必须是字符串")
    normalized = client_ip.strip()
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        raise AuditRecordRejected("IP 不是合法地址") from None
    return normalized


def _scan_record(record: Mapping[str, object]) -> None:
    for key, value in record.items():
        # 问题原文按设计保留，供审核和评估。敏感内容只允许出现在这一处。
        if key == FIELD_QUESTION:
            continue
        if any(_secret(text) for text in _strings(value)):
            raise AuditRecordRejected(f"{key} 含不能写入审计的内容")


def _secret(text: str) -> bool:
    # @ 同时覆盖邮箱和 GitHub 的 @用户名，其余审计字段没有合法用途。
    if "@" in text or TOKEN_PATTERN.search(text) or JWT_PATTERN.search(text):
        return True
    return bool(GITHUB_URL_PATTERN.search(text)) or _contains_ip(text)


def _contains_ip(text: str) -> bool:
    pieces = [text.strip()]
    matched = IPV4_PATTERN.search(text)
    if matched:
        pieces.append(matched.group(0))
    pieces.extend(item.group(0) for item in IPV6_CANDIDATE.finditer(text))
    return any(_valid_ip(piece) for piece in pieces)


def _valid_ip(text: str) -> bool:
    if text == "":
        return False
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        nested = [_strings(key) + _strings(item) for key, item in value.items()]
        return [text for group in nested for text in group]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [text for item in value for text in _strings(item)]
    return []
