"""验证码和 API 密钥的存储。以后换 PostgreSQL 时和 auth_db.py 一起换。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from auth_constants import (
    API_KEY_ID_BYTES,
    API_KEY_PREFIX,
    API_KEY_RANDOM_BYTES,
    CODE_LENGTH,
    CODE_MAX_ATTEMPTS,
    CODE_SALT_BYTES,
    CODE_TTL_SECONDS,
    EMAIL_HOUR_SECONDS,
    EMAIL_HOURLY_CAP,
    EMAIL_RESEND_SECONDS,
    MAX_ACTIVE_API_KEYS,
)
from auth_db import AuthDb

_CODE_HASH_CHARS = 64


def new_login_code() -> tuple[str, str, str]:
    """生成验证码，返回明文、哈希和盐。明文只交给发信函数。"""
    code = str(secrets.randbelow(10**CODE_LENGTH)).zfill(CODE_LENGTH)
    salt = secrets.token_hex(CODE_SALT_BYTES)
    return code, hash_code(salt, code), salt


def hash_code(salt_hex: str, code: str) -> str:
    """验证码只存哈希。盐和哈希都不是验证码本身。"""
    raw = bytes.fromhex(salt_hex) + code.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def begin_email_code(
    db: AuthDb,
    email_hash: str,
    code_hash: str,
    salt: str,
    now: int,
) -> str | None:
    """记下一次发送。太密或这一小时已满时返回 soon 或 hourly。"""
    with db.immediate() as conn:
        if _hourly_full(conn, email_hash, now):
            return "hourly"
        if _too_soon(conn, email_hash, now):
            return "soon"
        _replace_code(conn, email_hash, code_hash, salt, now)
        conn.execute(
            "INSERT INTO email_sends (email_hash, sent_at) VALUES (?, ?)",
            (email_hash, now),
        )
        return None


def cancel_email_code(db: AuthDb, email_hash: str, sent_at: int) -> None:
    """发信失败就撤回这一次，避免占掉重发间隔。"""
    with db.immediate() as conn:
        conn.execute(
            "DELETE FROM email_codes WHERE email_hash = ? AND sent_at = ?",
            (email_hash, sent_at),
        )
        conn.execute(
            "DELETE FROM email_sends WHERE email_hash = ? AND sent_at = ?",
            (email_hash, sent_at),
        )


def take_email_code(db: AuthDb, email_hash: str, code: str, now: int) -> str:
    """对上就消费掉。错满次数、过期或不存在都返回 bad。"""
    with db.immediate() as conn:
        row = conn.execute(
            """
            SELECT code_hash, salt, expires_at, attempts
            FROM email_codes WHERE email_hash = ?
            """,
            (email_hash,),
        ).fetchone()
        if row is None:
            _dummy_compare()
            return "bad"
        return _judge_code(conn, email_hash, code, now, row)


def create_api_key(db: AuthDb, user_id: str, now: int) -> tuple[str, str] | None:
    """创建一把密钥。库里只写哈希；已有两把时返回 None。"""
    secret = API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_RANDOM_BYTES)
    key_id = secrets.token_hex(API_KEY_ID_BYTES)
    key_hash = _key_hash(secret)
    with db.immediate() as conn:
        count = _active_keys(conn, user_id)
        if count >= MAX_ACTIVE_API_KEYS:
            return None
        conn.execute(
            """
            INSERT INTO api_keys (key_id, user_id, key_hash, created_at, revoked_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (key_id, user_id, key_hash, now),
        )
    return secret, key_id


def revoke_api_key(db: AuthDb, user_id: str, key_id: str, now: int) -> bool:
    """吊销自己的一把密钥。立刻不能再用来提问。"""
    with db.immediate() as conn:
        cursor = conn.execute(
            """
            UPDATE api_keys SET revoked_at = ?
            WHERE key_id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (now, key_id, user_id),
        )
        return cursor.rowcount == 1


def user_for_api_key(db: AuthDb, secret: str) -> str | None:
    """用哈希找到仍有效的密钥主人。找不到就是无效密钥。"""
    with db.immediate() as conn:
        row = conn.execute(
            """
            SELECT user_id FROM api_keys
            WHERE key_hash = ? AND revoked_at IS NULL
            """,
            (_key_hash(secret),),
        ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _hourly_full(conn: sqlite3.Connection, email_hash: str, now: int) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM email_sends
        WHERE email_hash = ? AND sent_at > ?
        """,
        (email_hash, now - EMAIL_HOUR_SECONDS),
    ).fetchone()
    return int(row[0]) >= EMAIL_HOURLY_CAP


def _too_soon(conn: sqlite3.Connection, email_hash: str, now: int) -> bool:
    row = conn.execute(
        "SELECT MAX(sent_at) FROM email_sends WHERE email_hash = ?",
        (email_hash,),
    ).fetchone()
    if row[0] is None:
        return False
    return now - int(row[0]) < EMAIL_RESEND_SECONDS


def _replace_code(
    conn: sqlite3.Connection,
    email_hash: str,
    code_hash: str,
    salt: str,
    now: int,
) -> None:
    conn.execute("DELETE FROM email_codes WHERE email_hash = ?", (email_hash,))
    conn.execute(
        """
        INSERT INTO email_codes (
            email_hash, code_hash, salt, expires_at, attempts, sent_at
        ) VALUES (?, ?, ?, ?, 0, ?)
        """,
        (email_hash, code_hash, salt, now + CODE_TTL_SECONDS, now),
    )


def _judge_code(
    conn: sqlite3.Connection,
    email_hash: str,
    code: str,
    now: int,
    row: sqlite3.Row,
) -> str:
    if now >= int(row["expires_at"]) or int(row["attempts"]) >= CODE_MAX_ATTEMPTS:
        _delete_code(conn, email_hash)
        return "bad"
    actual = hash_code(str(row["salt"]), code)
    if not _same_hash(str(row["code_hash"]), actual):
        _note_miss(conn, email_hash, int(row["attempts"]))
        return "bad"
    _delete_code(conn, email_hash)
    return "ok"


def _note_miss(conn: sqlite3.Connection, email_hash: str, attempts: int) -> None:
    updated = attempts + 1
    if updated >= CODE_MAX_ATTEMPTS:
        _delete_code(conn, email_hash)
        return
    conn.execute(
        "UPDATE email_codes SET attempts = ? WHERE email_hash = ?",
        (updated, email_hash),
    )


def _delete_code(conn: sqlite3.Connection, email_hash: str) -> None:
    conn.execute("DELETE FROM email_codes WHERE email_hash = ?", (email_hash,))


def _dummy_compare() -> None:
    # 没有记录时也做一次比较，减少“这个邮箱没有验证码”的时间差。
    _same_hash(hash_code("00" * CODE_SALT_BYTES, "0" * CODE_LENGTH), "0" * _CODE_HASH_CHARS)


def _same_hash(expected: str, actual: str) -> bool:
    if len(expected) != len(actual):
        return False
    return hmac.compare_digest(expected, actual)


def _active_keys(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM api_keys
        WHERE user_id = ? AND revoked_at IS NULL
        """,
        (user_id,),
    ).fetchone()
    return int(row[0])


def _key_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
