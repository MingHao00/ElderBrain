"""引用核对提交：通过、替换后通过、证据不足，以及短拒绝。"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from admin_queue import REVIEW_REASON_CITATION_FAILED, list_review_queue
from ask_gate import INJECTION_REPLY
from audit_log import NO_EVIDENCE_TEXT
from auth_db import AuthDb
from citation_check import CitedSentence, RetrievedParent
from test_turn_commit_kit import (
    ASSISTANT_PAIR,
    QUESTION,
    REWRITE_ONCE,
    SEED_ANSWER,
    TOOL_CONTENT,
    TOOL_NAME,
    TURN_ID,
    USER_ID,
    VERSION_51,
    MemoryStore,
    RecordingRewrite,
    assistant_texts,
    ledger,
    message_texts,
    run_checked,
    run_refusal,
)
from turn_commit import NOT_FROM_DATABASE, OUTCOME_NO_EVIDENCE, OUTCOME_PASS, OUTCOME_REPLACED
from turn_run import ToolObservation, TurnCheckpoint

FIREBALL = "srd51:spell:fireball"
PARENT_EIGHT = "射程 150 尺。造成 8d6 火焰伤害。"
PARENT_SIX = "射程 150 尺。造成 6d6 火焰伤害。"
DISTANCE = "射程 150 尺。"
DICE_EIGHT = "造成 8d6 火焰伤害。"
DICE_SIX = "造成 6d6 火焰伤害。"
DICE_NINE = "造成 9d6 火焰伤害。"
PAGE = "https://example.com/srd/fireball"
FOREIGN = "https://example.com.evil/phish"
CITED_WITH_URL = "射程 150 尺。参见 https://example.com/srd/fireball"
REFUSAL_DRAFT = "不该入库的草稿。"
QUEUE_ROW = 1


def test_outcome_labels() -> None:
    assert OUTCOME_PASS == "通过"
    assert OUTCOME_REPLACED == "替换后通过"
    assert OUTCOME_NO_EVIDENCE == "证据不足"
    assert NOT_FROM_DATABASE == "不是来自数据库"
    assert REVIEW_REASON_CITATION_FAILED == "引用校验两次失败"


def test_matching_parent_passes_without_rewrite() -> None:
    retrieved = [_parent()]
    pages: list[str] = []
    draft = DISTANCE
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            draft,
            [_cited(draft)],
            retrieved,
            pages,
            _forbid_rewrite,
        )
        assert result.outcome == "通过"
        assert result.stored_text == draft
        _assert_kept(db, user_id, conversation_id, store, draft)
        assert list_review_queue(db) == []


def test_dice_mismatch_replaces_with_same_batch() -> None:
    retrieved = [_parent(PARENT_SIX)]
    pages = [PAGE]
    rewrite = RecordingRewrite(DICE_SIX, [_cited(DICE_SIX)])
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            DICE_EIGHT,
            [_cited(DICE_EIGHT)],
            retrieved,
            pages,
            rewrite,
        )
        assert result.outcome == "替换后通过"
        assert result.stored_text == DICE_SIX
        _assert_same_batch(rewrite, retrieved, pages)
        _assert_kept(db, user_id, conversation_id, store, DICE_SIX)
        _assert_absent(db, user_id, conversation_id, store, DICE_EIGHT)
        assert list_review_queue(db) == []


def test_web_sentence_with_listed_url_passes() -> None:
    text = _linked(PAGE)
    retrieved = [_parent()]
    pages = [PAGE]
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            text,
            [_web(text)],
            retrieved,
            pages,
            _forbid_rewrite,
        )
        assert result.outcome == "通过"
        assert result.stored_text == text
        assert PAGE in result.stored_text
        assert NOT_FROM_DATABASE in result.stored_text
        _assert_kept(db, user_id, conversation_id, store, text)
        assert list_review_queue(db) == []


def test_web_sentence_missing_phrase_rewrites() -> None:
    draft = f"公开页 {PAGE} 。"
    fixed = _linked(PAGE)
    retrieved = [_parent()]
    pages = [PAGE]
    rewrite = RecordingRewrite(fixed, [_web(fixed)])
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            draft,
            [_web(draft)],
            retrieved,
            pages,
            rewrite,
        )
        assert result.outcome == "替换后通过"
        assert result.stored_text == fixed
        _assert_same_batch(rewrite, retrieved, pages)
        _assert_kept(db, user_id, conversation_id, store, fixed)
        _assert_absent(db, user_id, conversation_id, store, draft)


def test_web_link_outside_round_rewrites() -> None:
    draft = f"公开页 {PAGE} 以及 {FOREIGN} ，{NOT_FROM_DATABASE}。"
    fixed = _linked(PAGE)
    retrieved = [_parent()]
    pages = [PAGE]
    rewrite = RecordingRewrite(fixed, [_web(fixed)])
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            draft,
            [_web(draft)],
            retrieved,
            pages,
            rewrite,
        )
        assert result.outcome == "替换后通过"
        assert FOREIGN not in result.stored_text
        _assert_same_batch(rewrite, retrieved, pages)
        _assert_kept(db, user_id, conversation_id, store, fixed)
        _assert_absent(db, user_id, conversation_id, store, draft, FOREIGN)


def test_cited_url_does_not_need_database_phrase() -> None:
    retrieved = [_parent()]
    pages = [PAGE]
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            CITED_WITH_URL,
            [_cited(CITED_WITH_URL)],
            retrieved,
            pages,
            _forbid_rewrite,
        )
        assert result.outcome == "通过"
        assert NOT_FROM_DATABASE not in result.stored_text
        _assert_kept(db, user_id, conversation_id, store, CITED_WITH_URL)


def test_two_failures_become_no_evidence() -> None:
    retrieved = [_parent(PARENT_SIX)]
    pages = [PAGE]
    rewrite = RecordingRewrite(DICE_NINE, [_cited(DICE_NINE)])
    with ledger() as (db, user_id, conversation_id):
        result, store = run_checked(
            db,
            user_id,
            conversation_id,
            DICE_EIGHT,
            [_cited(DICE_EIGHT)],
            retrieved,
            pages,
            rewrite,
        )
        assert result.outcome == "证据不足"
        assert result.stored_text == NO_EVIDENCE_TEXT
        _assert_same_batch(rewrite, retrieved, pages)
        _assert_kept(db, user_id, conversation_id, store, NO_EVIDENCE_TEXT)
        _assert_absent(db, user_id, conversation_id, store, DICE_EIGHT, DICE_NINE)
        rows = list_review_queue(db)
        assert len(rows) == QUEUE_ROW
        assert rows[0]["reason"] == "引用校验两次失败"
        assert rows[0]["question"] == QUESTION


def test_short_refusal_keeps_reply_not_draft() -> None:
    with ledger() as (db, user_id, conversation_id):
        result, store = run_refusal(db, user_id, conversation_id, REFUSAL_DRAFT)
        assert result.stored_text == INJECTION_REPLY
        assert result.stored_text == "这个问题不能回答。"
        _assert_kept(db, user_id, conversation_id, store, INJECTION_REPLY)
        _assert_absent(db, user_id, conversation_id, store, REFUSAL_DRAFT)
        assert list_review_queue(db) == []


def _parent(text: str = PARENT_EIGHT) -> RetrievedParent:
    return RetrievedParent(FIREBALL, text, VERSION_51)


def _cited(text: str) -> CitedSentence:
    return CitedSentence(text, FIREBALL)


def _web(text: str) -> CitedSentence:
    return CitedSentence(text, None)


def _linked(url: str) -> str:
    return f"公开页 {url} ，{NOT_FROM_DATABASE}。"


def _forbid_rewrite(
    retrieved: list[RetrievedParent],
    web_urls: Sequence[str],
) -> tuple[str, list[CitedSentence]]:
    """第一次已经通过时，重写不该被调用。"""
    raise AssertionError((retrieved, web_urls))


def _assert_same_batch(
    rewrite: RecordingRewrite,
    retrieved: list[RetrievedParent],
    web_urls: Sequence[str],
) -> None:
    assert len(rewrite.seen) == REWRITE_ONCE
    assert rewrite.seen[0][0] is retrieved
    assert rewrite.seen[0][1] is web_urls


def _assert_kept(
    db: AuthDb,
    user_id: str,
    conversation_id: str,
    store: MemoryStore,
    stored: str,
) -> None:
    assert user_id == USER_ID
    saved = store.load(conversation_id)
    assert saved is not None
    assert saved.answer_committed is True
    assert saved.partial_text == stored
    assert saved.turn_id == TURN_ID
    assert saved.user_messages == (QUESTION,)
    assert saved.tool_results == (ToolObservation(TOOL_NAME, TOOL_CONTENT),)
    texts = assistant_texts(db, user_id, conversation_id)
    assert texts == [SEED_ANSWER, stored]
    assert len(texts) == ASSISTANT_PAIR


def _assert_absent(
    db: AuthDb,
    user_id: str,
    conversation_id: str,
    store: MemoryStore,
    *drafts: str,
) -> None:
    saved = store.load(conversation_id)
    assert isinstance(saved, TurnCheckpoint)
    blob = "\n".join(message_texts(db, user_id, conversation_id))
    for draft in drafts:
        assert draft not in blob
        assert draft not in saved.partial_text
