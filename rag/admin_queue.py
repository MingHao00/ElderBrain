"""审核队列。只接收具名原因，问题原文留给管理员查看。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from admin_store import ensure_tables
from auth_db import AuthDb

REVIEW_REASON_CITATION_FAILED = "引用校验两次失败"
REVIEW_REASONS = frozenset({REVIEW_REASON_CITATION_FAILED})


def enqueue_review(
    db: AuthDb,
    reason: str,
    question: str,
    created_at: datetime,
) -> None:
    """写入审核队列。原因不在允许集合里就拒绝。"""
    if reason not in REVIEW_REASONS:
        raise ValueError("未知的审核原因")
    text = _question_text(question)
    moment = _created(created_at)
    with db.immediate() as conn:
        ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO review_queue (reason, created_at, question)
            VALUES (?, ?, ?)
            """,
            (reason, moment.isoformat(), text),
        )


def list_review_queue(db: AuthDb) -> list[dict[str, str]]:
    """按写入顺序列出原因、时间和问题。不含账号。"""
    with db.immediate() as conn:
        ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT reason, created_at, question
            FROM review_queue
            ORDER BY item_id
            """
        ).fetchall()
    return [_queue_item(row) for row in rows]


def _queue_item(row: sqlite3.Row) -> dict[str, str]:
    return {
        "reason": str(row["reason"]),
        "created_at": str(row["created_at"]),
        "question": str(row["question"]),
    }


def _question_text(value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError("问题不能为空")
    return value


def _created(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("时间无效")
    return value
