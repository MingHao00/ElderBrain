"""会话拒绝名单和 OAuth state。以后换 PostgreSQL 时和 auth_db.py 一起换。"""

from __future__ import annotations

import sqlite3

from auth_constants import CLOCK_SKEW_SECONDS
from auth_db import AuthDb


def revoke_jti(db: AuthDb, jti: str, exp: int, now: int) -> None:
    """拒绝名单只存 jti 和 exp，不存令牌原文。"""
    with db.immediate() as conn:
        conn.execute(
            """
            INSERT INTO revoked_jti (jti, exp) VALUES (?, ?)
            ON CONFLICT(jti) DO UPDATE SET exp = excluded.exp
            """,
            (jti, exp),
        )
        _purge_jti(conn, now)


def jti_revoked(db: AuthDb, jti: str, now: int) -> bool:
    """已拒绝的 jti 不能再当登录。过期项顺手清掉。"""
    with db.immediate() as conn:
        _purge_jti(conn, now)
        row = conn.execute(
            "SELECT 1 FROM revoked_jti WHERE jti = ?",
            (jti,),
        ).fetchone()
        return row is not None


def save_state(db: AuthDb, state_hash: str, expires_at: int) -> None:
    """保存 OAuth state 的哈希。原文只放在跳转地址里。"""
    with db.immediate() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state_hash, expires_at) VALUES (?, ?)",
            (state_hash, expires_at),
        )


def consume_state(db: AuthDb, state_hash: str, now: int) -> bool:
    """state 只用一次。过期或不存在都算失败。"""
    with db.immediate() as conn:
        row = conn.execute(
            "SELECT expires_at FROM oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        conn.execute(
            "DELETE FROM oauth_states WHERE state_hash = ? OR expires_at <= ?",
            (state_hash, now),
        )
        return row is not None and int(row[0]) > now


def _purge_jti(conn: sqlite3.Connection, now: int) -> None:
    # 留到 exp 之后再加时钟偏差，避免偏差窗口内的已登出令牌复活。
    conn.execute(
        "DELETE FROM revoked_jti WHERE exp < ?",
        (now - CLOCK_SKEW_SECONDS,),
    )
