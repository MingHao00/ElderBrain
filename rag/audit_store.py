"""请求审计的本机存储。

以后换 PostgreSQL 时只换这一层。提问门闩不写 SQL。
占位符用 ?；换库时改成该驱动的占位符。
和账号库同一个文件，但是单独的表。不做定时删除，也不提供查询接口。
"""

from __future__ import annotations

import sqlite3

from auth_db import AuthDb

# 认不出的用户记 unknown，不是账号行，所以 user_id 不做外键。
_CREATE_AUDITS = """
CREATE TABLE IF NOT EXISTS request_audits (
    request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    reason TEXT NOT NULL,
    question_len INTEGER,
    record_json TEXT
)
"""
_INSERT_AUDIT = """
INSERT INTO request_audits (
    request_id, user_id, created_at, http_status, reason,
    question_len, record_json
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def insert_audit(
    db: AuthDb,
    request_id: str,
    user_id: str,
    created_at: str,
    http_status: int,
    reason: str,
    question_len: int | None,
    record_json: str | None,
) -> None:
    """写入一条审计。失败由调用方接住，不能改已经决定的响应。"""
    with db.immediate() as conn:
        _insert(
            conn,
            request_id,
            user_id,
            created_at,
            http_status,
            reason,
            question_len,
            record_json,
        )


def _insert(
    conn: sqlite3.Connection,
    request_id: str,
    user_id: str,
    created_at: str,
    http_status: int,
    reason: str,
    question_len: int | None,
    record_json: str | None,
) -> None:
    # executescript 会先提交，把这次独占事务提前结束。
    conn.execute(_CREATE_AUDITS)
    conn.execute(
        _INSERT_AUDIT,
        (
            request_id,
            user_id,
            created_at,
            http_status,
            reason,
            question_len,
            record_json,
        ),
    )
