"""引用核对提交的临时账本。不连 PostgreSQL，不读 DATABASE_URL。"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_db import AuthDb
from citation_check import CitedSentence, RetrievedParent
from conversation_store import (
    MESSAGE_ROLE_ASSISTANT,
    commit_turn,
    conversation_messages,
)
from turn_commit import TurnCommitResult, commit_checked_turn, commit_short_refusal
from turn_run import ToolObservation, TurnCheckpoint

VERSION_51 = "5.1"
USER_ID = "user-turn-commit"
QUESTION = "火球术怎么施放？"
SEED_QUESTION = "种子问题"
SEED_ANSWER = "种子回答"
TURN_ID = "turn-commit"
TOOL_NAME = "web_search"
TOOL_CONTENT = "本轮搜索结果留在检查点。"
COMMITTED_AT = 1_763_800_000
DB_NAME = "auth.sqlite"
ASSISTANT_PAIR = 2
REWRITE_ONCE = 1
OBSERVATION = ToolObservation(TOOL_NAME, TOOL_CONTENT)
Rewrite = Callable[
    [list[RetrievedParent], Sequence[str]],
    tuple[str, list[CitedSentence]],
]


class MemoryStore:
    """进程内检查点。同一线程号覆盖上一份。"""

    def __init__(self) -> None:
        self._saved: dict[str, TurnCheckpoint] = {}

    def load(self, thread_id: str) -> TurnCheckpoint | None:
        return self._saved.get(thread_id)

    def save(self, checkpoint: TurnCheckpoint) -> None:
        self._saved[checkpoint.thread_id] = checkpoint

    def delete(self, thread_id: str) -> None:
        if thread_id in self._saved:
            del self._saved[thread_id]


class RecordingRewrite:
    """记下重写收到的父节和链接，并返回预设的下一稿。"""

    def __init__(self, text: str, sentences: list[CitedSentence]) -> None:
        self._text = text
        self._sentences = sentences
        self.seen: list[tuple[list[RetrievedParent], Sequence[str]]] = []

    def __call__(
        self,
        retrieved: list[RetrievedParent],
        web_urls: Sequence[str],
    ) -> tuple[str, list[CitedSentence]]:
        self.seen.append((retrieved, web_urls))
        return self._text, self._sentences


@contextmanager
def ledger() -> Iterator[tuple[AuthDb, str, str]]:
    """临时 sqlite。先有一场属于该用户的对话，提交才能写入消息。"""
    folder = tempfile.TemporaryDirectory()
    try:
        db = AuthDb(Path(folder.name) / DB_NAME)
        conversation_id = commit_turn(
            db,
            USER_ID,
            None,
            SEED_QUESTION,
            SEED_ANSWER,
            None,
            COMMITTED_AT,
        )
        yield db, USER_ID, conversation_id
    finally:
        folder.cleanup()


def message_texts(db: AuthDb, user_id: str, conversation_id: str) -> list[str]:
    """这场对话里全部消息正文。"""
    rows = conversation_messages(db, user_id, conversation_id)
    if rows is None:
        raise AssertionError(conversation_id)
    return [row["text"] for row in rows]


def assistant_texts(db: AuthDb, user_id: str, conversation_id: str) -> list[str]:
    """助手正文。种子回答在前，这一次提交在后。"""
    rows = conversation_messages(db, user_id, conversation_id)
    if rows is None:
        raise AssertionError(conversation_id)
    return [row["text"] for row in rows if row["role"] == MESSAGE_ROLE_ASSISTANT]


def run_checked(
    db: AuthDb,
    user_id: str,
    conversation_id: str,
    draft_text: str,
    sentences: list[CitedSentence],
    retrieved: list[RetrievedParent],
    web_urls: Sequence[str],
    rewrite: Rewrite,
) -> tuple[TurnCommitResult, MemoryStore]:
    """先放下未提交检查点，再跑核对提交。"""
    store, checkpoint = _checkpoint(conversation_id, draft_text)
    result = commit_checked_turn(
        draft_text=draft_text,
        sentences=sentences,
        retrieved=retrieved,
        stated_version=VERSION_51,
        web_urls=web_urls,
        rewrite=rewrite,
        store=store,
        checkpoint=checkpoint,
        db=db,
        user_id=user_id,
        question=QUESTION,
        now=COMMITTED_AT,
    )
    return result, store


def run_refusal(
    db: AuthDb,
    user_id: str,
    conversation_id: str,
    draft_text: str,
) -> tuple[TurnCommitResult, MemoryStore]:
    """短拒绝。草稿只传进去，不应当被写成答案。"""
    store, checkpoint = _checkpoint(conversation_id, draft_text)
    result = commit_short_refusal(
        draft_text=draft_text,
        store=store,
        checkpoint=checkpoint,
        db=db,
        user_id=user_id,
        question=QUESTION,
        now=COMMITTED_AT,
    )
    return result, store


def _checkpoint(
    conversation_id: str,
    partial_text: str,
) -> tuple[MemoryStore, TurnCheckpoint]:
    store = MemoryStore()
    checkpoint = TurnCheckpoint(
        thread_id=conversation_id,
        turn_id=TURN_ID,
        partial_text=partial_text,
        user_messages=(QUESTION,),
        tool_results=(OBSERVATION,),
        answer_committed=False,
    )
    store.save(checkpoint)
    return store, checkpoint
