"""把闲置对话里的用户原话折进会话卡片。

触发只看最后一条用户问题过去了多久，不看厂商 KV cache 是否还在。
摘要在库锁外面调用，避免挡住下一问，也避免回调里写新问题时死锁。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from openai import OpenAI

from auth_constants import MICRO_PER_YUAN, SITE_DAILY_CAP_MICRO
from auth_db import AuthDb
from auth_quota import record_idle_cost, site_spent_micro
from config import RULE_VERSION
from conversation_store import (
    DueTurn,
    apply_idle_card,
    load_due_turns,
    session_card_exists,
)
from generation_call import QWEN_MODEL, complete, qwen_body, qwen_client, qwen_yuan
from session_card import CardEntry, SessionCard, generation_context

# 从最后一条用户问题起算。测试传入更短的秒数，扫描本身不睡眠这么久。
IDLE_SUMMARY_SECONDS = 30 * 60
# 轮询比闲置窗口短，到点后最多再等一个间隔才被看见。
IDLE_SCAN_INTERVAL_SECONDS = 60
# 厂商没给用量时不估 token，费用记 0。
MISSING_IDLE_TOKENS = 0
IDLE_SUMMARY_PROMPT = """你只把已经讨论过的规则条目收成列表，不写规则结论。
只输出一个 JSON 对象，第一个字符是 {，最后一个字符是 }。
对象只能有 rule_version 和 entries。
entries 是数组，元素只能有 entry_id、concept_key、title。
这些字段逐字抄自会话卡片或用户原话里已经出现的条目，不要编造。
没有讨论过的条目就给空数组。不要输出解释、数值或裁定。
"""

Summarizer = Callable[[tuple[str, ...], SessionCard], "IdleBrief | None"]
AlertFn = Callable[[], None]
Clock = Callable[[], int]


@dataclass(frozen=True)
class IdleBrief:
    """一次摘要结果。规则版本缺省时调用方保留卡片上已有的版本。"""

    entries: tuple[CardEntry, ...]
    rule_version: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class IdleScan:
    """后台扫描。stop.set() 后线程在当前等待处退出，不挡住请求。"""

    stop: threading.Event
    thread: threading.Thread


def parse_idle_summary(text: str) -> IdleBrief | None:
    """只接受条目列表。规则结论或普通段落返回 None。

    Args:
        text: 模型原文。

    Returns:
        解析出的条目。正文里没有 rule_version 时该字段为 None。
        解析不成条目列表时返回 None。
    """
    payload = _json_value(text)
    if isinstance(payload, list):
        return _brief_from_entries(payload, None)
    if isinstance(payload, dict):
        return _brief_from_object(payload)
    return None


def scan_idle_summaries(
    db: AuthDb,
    summarize: Summarizer,
    now: int,
    idle_seconds: int = IDLE_SUMMARY_SECONDS,
    on_alert: AlertFn | None = None,
) -> int:
    """扫描到点的对话。有未折的用户原话才调用 summarize。

    Args:
        db: 账号库。
        summarize: 摘要函数。失败时返回 None 或抛出异常。
        now: 这次扫描的时间戳。
        idle_seconds: 从最后一条用户问题起算的闲置时长。
        on_alert: 费用跨过告警线时调用。缺省不发信。

    Returns:
        成功折进卡片的对话数。

    Raises:
        ValueError: idle_seconds 为负。
    """
    pending = load_due_turns(db, now, idle_seconds)
    folded = 0
    for turn in pending:
        if _fold_one(db, summarize, turn, now, on_alert):
            folded += 1
    return folded


def start_idle_scan(
    db: AuthDb,
    summarize: Summarizer,
    idle_seconds: int = IDLE_SUMMARY_SECONDS,
    interval_seconds: int = IDLE_SCAN_INTERVAL_SECONDS,
    clock: Clock | None = None,
    on_alert: AlertFn | None = None,
) -> IdleScan:
    """启动不阻塞请求的扫描线程。

    Args:
        db: 账号库。
        summarize: 摘要函数。
        idle_seconds: 闲置多久才到点。
        interval_seconds: 两轮之间的等待，不是闲置时长本身。
        clock: 当前时间。缺省用本机时钟。
        on_alert: 跨过告警线时调用。

    Returns:
        停止开关和守护线程。

    Raises:
        ValueError: 间隔为负。
    """
    if interval_seconds < 0:
        raise ValueError("扫描间隔不能为负")
    stop = threading.Event()
    tick = _now if clock is None else clock
    thread = threading.Thread(
        target=_scan_loop,
        name="idle-summary",
        daemon=True,
        args=(stop, db, summarize, idle_seconds, interval_seconds, tick, on_alert),
    )
    thread.start()
    return IdleScan(stop, thread)


def summarize_with_qwen(
    utterances: tuple[str, ...],
    card: SessionCard,
    client: object | None = None,
) -> IdleBrief | None:
    """用通义把未折原话收成条目。

    Args:
        utterances: 还没折进卡片的用户原话。
        card: 当前会话卡片。只作上下文，不把助手原文送进去。
        client: 对话客户端。缺省时才读取环境变量里的密钥。

    Returns:
        条目和用量。调用失败或正文不是条目列表时返回 None，调用方不记费。
    """
    try:
        text, usage = _complete_idle(utterances, card, client)
    except Exception:
        return None
    parsed = parse_idle_summary(text)
    if parsed is None:
        return None
    return IdleBrief(
        parsed.entries,
        parsed.rule_version,
        _usage_token(usage, "prompt_tokens"),
        _usage_token(usage, "completion_tokens"),
    )


def _fold_one(
    db: AuthDb,
    summarize: Summarizer,
    turn: DueTurn,
    now: int,
    on_alert: AlertFn | None,
) -> bool:
    # 先看费用再调模型。到了上限就不调用，也不把原话标成已折。
    if _at_cap(db, now):
        return False
    # 没有卡片行就写不进去，未折原话会一直到点。调用前跳过，才不会反复记费。
    if not session_card_exists(db, turn.conversation_id):
        return False
    brief = _invoke(summarize, turn)
    if brief is None:
        return False
    entries = _discussed_entries(brief.entries, turn)
    # 给了条目但一条都对不上。保持原状，下次再试，这次不记费。
    if entries is None:
        return False
    applied = apply_idle_card(
        db,
        turn.conversation_id,
        turn.message_ids,
        turn.latest_user_message_id,
        entries,
        _kept_version(brief, turn.card),
    )
    # 调用期间行被删掉。写失败不能记费，否则下一轮扫描还会再记一笔。
    if not applied and not session_card_exists(db, turn.conversation_id):
        return False
    # 模型已经花了钱。新问题只挡住覆盖卡片，费用仍进当日累计。
    _charge(db, turn.user_id, brief, now, on_alert)
    return applied


def _discussed_entries(
    entries: tuple[CardEntry, ...],
    turn: DueTurn,
) -> tuple[CardEntry, ...] | None:
    """空列表仍交给写入。讨论过的条目被滤光时返回 None，表示这次失败。

    Args:
        entries: 模型返回的条目。
        turn: 这次读到的未折原话和当时的卡片。

    Returns:
        可以整卡替换的条目。一条都不剩时返回 None。空输入返回空元组。
    """
    if not entries:
        return entries
    kept = tuple(_keep_discussed(entry, turn) for entry in entries)
    discussed = tuple(item for item in kept if item is not None)
    if not discussed:
        return None
    return discussed


def _keep_discussed(entry: CardEntry, turn: DueTurn) -> CardEntry | None:
    if not _entry_id_discussed(entry.entry_id, turn):
        return None
    return _ground_entry(entry, turn)


def _entry_id_discussed(entry_id: str, turn: DueTurn) -> bool:
    if not entry_id:
        return False
    if any(item.entry_id == entry_id for item in turn.card.entries):
        return True
    # 原话里的编号必须是空白隔开的整词，短碎片不能算讨论过。
    return _in_utterances(entry_id, turn.utterances)


def _ground_entry(entry: CardEntry, turn: DueTurn) -> CardEntry:
    current = _same_entry(turn.card, entry.entry_id)
    concept = None if current is None else current.concept_key
    title = None if current is None else current.title
    return CardEntry(
        entry.entry_id,
        _accepted_field(entry.concept_key, concept, turn.utterances),
        _accepted_field(entry.title, title, turn.utterances),
        entry.location,
    )


def _accepted_field(
    proposed: str | None,
    current: str | None,
    utterances: tuple[str, ...],
) -> str | None:
    """对不上卡片上的原字段、也不在原话里时，退回卡片已有的值。"""
    if proposed is not None and _field_attested(proposed, current, utterances):
        return proposed
    return current


def _field_attested(
    proposed: str,
    current: str | None,
    utterances: tuple[str, ...],
) -> bool:
    if not proposed:
        return False
    # 卡片字段要整段相等。原话里也只认空白隔开的整词。
    if current is not None and proposed == current:
        return True
    return _in_utterances(proposed, utterances)


def _same_entry(card: SessionCard, entry_id: str) -> CardEntry | None:
    for item in card.entries:
        if item.entry_id == entry_id:
            return item
    return None


def _in_utterances(needle: str, utterances: tuple[str, ...]) -> bool:
    if not needle:
        return False
    # 子串会把「术」「spell」从更长的词里抠出来。只认空白隔开的整词。
    return any(needle in text.split() for text in utterances)


def _at_cap(db: AuthDb, now: int) -> bool:
    return site_spent_micro(db, now) >= SITE_DAILY_CAP_MICRO


def _invoke(summarize: Summarizer, turn: DueTurn) -> IdleBrief | None:
    try:
        brief = summarize(turn.utterances, turn.card)
    except AssertionError:
        raise
    except Exception:
        # 模型超时或返回异常都当成这次失败，卡片留到下次扫描。
        return None
    if not isinstance(brief, IdleBrief):
        return None
    return brief


def _charge(
    db: AuthDb,
    user_id: str,
    brief: IdleBrief,
    now: int,
    on_alert: AlertFn | None,
) -> None:
    crossed = record_idle_cost(db, user_id, _cost_micro(brief), now)
    if crossed:
        _notify(on_alert)


def _notify(on_alert: AlertFn | None) -> None:
    if on_alert is None:
        return
    try:
        on_alert()
    except Exception as exc:
        # 发信失败不能回滚已经写下的费用和卡片。
        sys.stderr.write(f"费用告警没有发出 {type(exc).__name__}\n")


def _cost_micro(brief: IdleBrief) -> int:
    yuan = qwen_yuan(_tokens(brief.prompt_tokens), _tokens(brief.completion_tokens))
    return round(yuan * MICRO_PER_YUAN)


def _tokens(value: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return MISSING_IDLE_TOKENS
    return value


def _kept_version(brief: IdleBrief, card: SessionCard) -> str:
    returned = _clean(brief.rule_version)
    if returned is not None:
        return returned
    current = _clean(card.rule_version)
    if current is not None:
        return current
    return RULE_VERSION


def _scan_loop(
    stop: threading.Event,
    db: AuthDb,
    summarize: Summarizer,
    idle_seconds: int,
    interval_seconds: int,
    clock: Clock,
    on_alert: AlertFn | None,
) -> None:
    # 启动后先扫一遍，不必先睡完一个轮询间隔。
    while not stop.is_set():
        _scan_quietly(db, summarize, idle_seconds, clock, on_alert)
        if stop.wait(interval_seconds):
            return


def _scan_quietly(
    db: AuthDb,
    summarize: Summarizer,
    idle_seconds: int,
    clock: Clock,
    on_alert: AlertFn | None,
) -> None:
    try:
        scan_idle_summaries(db, summarize, clock(), idle_seconds, on_alert)
    except Exception as exc:
        # 异常文本可能夹着接口返回，线程只留类型名。
        sys.stderr.write(f"闲置摘要扫描失败 {type(exc).__name__}\n")


def _now() -> int:
    return int(time.time())


def _complete_idle(
    utterances: tuple[str, ...],
    card: SessionCard,
    client: object | None,
) -> tuple[str, dict[str, int]]:
    model = qwen_client() if client is None else cast(OpenAI, client)
    messages = [
        {"role": "system", "content": IDLE_SUMMARY_PROMPT},
        {"role": "user", "content": _idle_request(utterances, card)},
    ]
    return complete(model, QWEN_MODEL, messages, qwen_body())


def _idle_request(utterances: tuple[str, ...], card: SessionCard) -> str:
    lines = ["用户原话：", *utterances]
    context = generation_context(card)
    if context is None:
        return "\n".join(lines)
    return "\n".join([*lines, "当前会话卡片：", context])


def _usage_token(usage: dict[str, int], name: str) -> int | None:
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _json_value(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _brief_from_object(payload: dict[str, object]) -> IdleBrief | None:
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return None
    return _brief_from_entries(raw, _clean(payload.get("rule_version")))


def _brief_from_entries(items: list[object], version: str | None) -> IdleBrief | None:
    entries = _entry_list(items)
    if entries is None:
        return None
    return IdleBrief(entries, version)


def _entry_list(items: list[object]) -> tuple[CardEntry, ...] | None:
    found: list[CardEntry] = []
    for item in items:
        entry = _one_entry(item)
        if entry is None:
            return None
        found.append(entry)
    return tuple(found)


def _one_entry(item: object) -> CardEntry | None:
    if not isinstance(item, dict):
        return None
    entry_id = _clean(item.get("entry_id"))
    if entry_id is None:
        return None
    return CardEntry(entry_id, _clean(item.get("concept_key")), _clean(item.get("title")))


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped
