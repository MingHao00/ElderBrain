"""核对正文后提交。不创建网络客户端，也不调用模型。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from admin_queue import REVIEW_REASON_CITATION_FAILED, enqueue_review
from ask_gate import INJECTION_REPLY
from audit_log import NO_EVIDENCE_TEXT
from auth_db import AuthDb
from citation_check import CitedSentence, RetrievedParent, citations_match
from conversation_store import commit_turn
from turn_run import CheckpointStore, TurnCheckpoint

OUTCOME_PASS = "通过"
OUTCOME_REPLACED = "替换后通过"
OUTCOME_NO_EVIDENCE = "证据不足"
NOT_FROM_DATABASE = "不是来自数据库"

HTTP_SCHEME = "http://"
HTTPS_SCHEME = "https://"
_SCHEMES = (HTTPS_SCHEME, HTTP_SCHEME)
_NOT_FOUND = -1
_TEXT_START = 0
_TRAILING_MARKS = "。，、；：！？,.;:!?)]》」'\"”"
_UTC = timezone.utc

Rewrite = Callable[
    [list[RetrievedParent], Sequence[str]],
    tuple[str, list[CitedSentence]],
]


@dataclass(frozen=True)
class TurnCommitResult:
    """一次提交留下的结论和正文。"""

    outcome: str
    stored_text: str


def commit_checked_turn(
    *,
    draft_text: str,
    sentences: list[CitedSentence],
    retrieved: list[RetrievedParent],
    stated_version: str,
    web_urls: Sequence[str],
    rewrite: Rewrite,
    store: CheckpointStore,
    checkpoint: TurnCheckpoint,
    db: AuthDb,
    user_id: str,
    question: str,
    now: int,
) -> TurnCommitResult:
    """核对草稿。不通过就用同一批父节和链接重写一次。

    两次都不通过时写入证据不足，并用原问题进入引用复核队列。
    失败草稿不写入对话，也不留在检查点正文里。
    检查点的 thread_id 必须是已经属于 user_id 的对话，否则现有
    commit_turn 不会落消息。

    Args:
        draft_text: 这一稿正文。通过时原样留下。
        sentences: 带 entry_id 的句子。None 且含链接的是联网句。
        retrieved: 本轮父节。重写时原对象传回去。
        stated_version: 回答标注的版本。
        web_urls: 本轮搜索结果里出现过的完整 URL。
        rewrite: 仅第一次不通过时调用一次。
        store: 检查点存储。
        checkpoint: 当前未提交的检查点。
        db: 账号库。
        user_id: 对话主人。
        question: 原问题。
        now: 写入时刻，单位秒。

    Returns:
        通过、替换后通过或证据不足，以及实际留下的正文。

    Raises:
        ValueError: 句子、父节或版本不符合 citations_match 的要求。
    """
    if _accepted(sentences, retrieved, stated_version, web_urls):
        return _keep(
            OUTCOME_PASS,
            draft_text,
            store,
            checkpoint,
            db,
            user_id,
            question,
            now,
        )
    return _retry(
        rewrite,
        retrieved,
        web_urls,
        stated_version,
        store,
        checkpoint,
        db,
        user_id,
        question,
        now,
    )


def commit_short_refusal(
    *,
    draft_text: str,
    store: CheckpointStore,
    checkpoint: TurnCheckpoint,
    db: AuthDb,
    user_id: str,
    question: str,
    now: int,
) -> TurnCommitResult:
    """循环前的短拒绝。不核对引用，不重写，不进复核队列。

    draft_text 不写入。thread_id 不是该用户的对话时，现有 commit_turn 不落消息。

    Args:
        draft_text: 调用方手里的草稿，不写入。
        store: 检查点存储。
        checkpoint: 当前未提交的检查点。
        db: 账号库。
        user_id: 对话主人。
        question: 原问题，和拒绝句一起写入对话。
        now: 写入时刻，单位秒。

    Returns:
        写入的短拒绝。outcome 与正文相同，不是三种核对结论。
    """
    # 草稿只占参数位，短拒绝固定写现成的拒绝句。
    del draft_text
    return _keep(
        INJECTION_REPLY,
        INJECTION_REPLY,
        store,
        checkpoint,
        db,
        user_id,
        question,
        now,
    )


def _retry(
    rewrite: Rewrite,
    retrieved: list[RetrievedParent],
    web_urls: Sequence[str],
    stated_version: str,
    store: CheckpointStore,
    checkpoint: TurnCheckpoint,
    db: AuthDb,
    user_id: str,
    question: str,
    now: int,
) -> TurnCommitResult:
    text, revised = rewrite(retrieved, web_urls)
    if _accepted(revised, retrieved, stated_version, web_urls):
        return _keep(
            OUTCOME_REPLACED,
            text,
            store,
            checkpoint,
            db,
            user_id,
            question,
            now,
        )
    _enqueue(db, question, now)
    return _keep(
        OUTCOME_NO_EVIDENCE,
        NO_EVIDENCE_TEXT,
        store,
        checkpoint,
        db,
        user_id,
        question,
        now,
    )


def _accepted(
    sentences: list[CitedSentence],
    retrieved: list[RetrievedParent],
    stated_version: str,
    web_urls: Sequence[str],
) -> bool:
    if not citations_match(sentences, retrieved, stated_version):
        return False
    return _web_notes_ok(sentences, web_urls)


def _web_notes_ok(
    sentences: list[CitedSentence],
    web_urls: Sequence[str],
) -> bool:
    return all(_web_sentence_ok(item, web_urls) for item in sentences)


def _web_sentence_ok(sentence: CitedSentence, web_urls: Sequence[str]) -> bool:
    links = _web_links(sentence)
    if not links:
        return True
    if NOT_FROM_DATABASE not in sentence.text:
        return False
    return all(_listed(link, web_urls) for link in links)


def _web_links(sentence: CitedSentence) -> tuple[str, ...]:
    # 有条目编号的父节句不是联网句，不要求写「不是来自数据库」。
    if sentence.entry_id is not None:
        return ()
    return _links_in(sentence.text)


def _listed(link: str, web_urls: Sequence[str]) -> bool:
    # 只认完整 URL。主机名子串会把 example.com.evil 当成 example.com。
    return any(link == url for url in web_urls)


def _links_in(text: str) -> tuple[str, ...]:
    found: list[str] = []
    start = _next_scheme(text, _TEXT_START)
    while start is not None:
        end = _link_end(text, start)
        found.append(_trim_link(text[start:end]))
        start = _next_scheme(text, end)
    return tuple(found)


def _next_scheme(text: str, start: int) -> int | None:
    positions = [text.find(scheme, start) for scheme in _SCHEMES]
    found = [item for item in positions if item != _NOT_FOUND]
    if not found:
        return None
    return min(found)


def _link_end(text: str, start: int) -> int:
    end = start
    length = len(text)
    while end < length and not text[end].isspace():
        end += 1
    return end


def _trim_link(link: str) -> str:
    return link.rstrip(_TRAILING_MARKS)


def _keep(
    outcome: str,
    stored_text: str,
    store: CheckpointStore,
    checkpoint: TurnCheckpoint,
    db: AuthDb,
    user_id: str,
    question: str,
    now: int,
) -> TurnCommitResult:
    store.save(_committed(checkpoint, stored_text))
    commit_turn(
        db,
        user_id,
        checkpoint.thread_id,
        question,
        stored_text,
        None,
        now,
    )
    return TurnCommitResult(outcome, stored_text)


def _committed(checkpoint: TurnCheckpoint, stored_text: str) -> TurnCheckpoint:
    return TurnCheckpoint(
        thread_id=checkpoint.thread_id,
        turn_id=checkpoint.turn_id,
        partial_text=stored_text,
        user_messages=checkpoint.user_messages,
        tool_results=checkpoint.tool_results,
        answer_committed=True,
    )


def _enqueue(db: AuthDb, question: str, now: int) -> None:
    enqueue_review(
        db,
        REVIEW_REASON_CITATION_FAILED,
        question,
        datetime.fromtimestamp(now, _UTC),
    )
