"""对话存档和会话卡片。和账号库同一个文件。

以后换 PostgreSQL 时只换这一层。占位符用 ?。
删对话不碰审计表。
"""

from __future__ import annotations

import json
import secrets
import sqlite3

from auth_constants import CONVERSATION_ID_BYTES, CONVERSATION_ID_PREFIX
from auth_db import AuthDb
from config import RULE_VERSION
from session_card import CardEntry, SessionCard, empty_card, entry_payload

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"
_EMPTY_ENTRIES = "[]"

_CREATE_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""
_CREATE_CONVERSATION_INDEX = """
CREATE INDEX IF NOT EXISTS conversations_by_user
    ON conversations(user_id, updated_at)
"""
_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
"""
_CREATE_MESSAGE_INDEX = """
CREATE INDEX IF NOT EXISTS messages_by_conversation
    ON conversation_messages(conversation_id, position)
"""
_CREATE_CARDS = """
CREATE TABLE IF NOT EXISTS session_cards (
    conversation_id TEXT PRIMARY KEY,
    rule_version TEXT NOT NULL,
    entries_json TEXT NOT NULL
)
"""


def new_conversation_id() -> str:
    """服务端编号。客户端传来的字符串不会被当成新的一场。"""
    return CONVERSATION_ID_PREFIX + secrets.token_hex(CONVERSATION_ID_BYTES)


def conversation_owner(db: AuthDb, conversation_id: str) -> str | None:
    """没有这场，或已经删掉，都当作不属于任何人。"""
    with db.immediate() as conn:
        _ensure(conn)
        row = _owner_row(conn, conversation_id)
        if row is None:
            return None
        return str(row["user_id"])


def load_card(db: AuthDb, conversation_id: str) -> SessionCard:
    """追问沿用上一轮卡片。库里没有就按新对话的空卡片。"""
    with db.immediate() as conn:
        _ensure(conn)
        return _load_card(conn, conversation_id)


def list_conversations(db: AuthDb, user_id: str) -> list[dict[str, str]]:
    """最近更新的在前。侧栏不用再排一次。"""
    with db.immediate() as conn:
        _ensure(conn)
        rows = conn.execute(
            """
            SELECT conversation_id, title FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, conversation_id DESC
            """,
            (user_id,),
        ).fetchall()
    return [_summary(row) for row in rows]


def conversation_messages(
    db: AuthDb,
    user_id: str,
    conversation_id: str,
) -> list[dict[str, str]] | None:
    """不是本人的对话返回 None，调用方回应无权限。"""
    with db.immediate() as conn:
        _ensure(conn)
        if not _belongs(conn, user_id, conversation_id):
            return None
        rows = conn.execute(
            """
            SELECT role, body FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY position ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [_message(row) for row in rows]


def delete_conversation(db: AuthDb, user_id: str, conversation_id: str) -> bool:
    """删掉存档和卡片。审计记录留在另一张表里。"""
    with db.immediate() as conn:
        _ensure(conn)
        if not _belongs(conn, user_id, conversation_id):
            return False
        _delete_rows(conn, conversation_id)
        return True


def commit_turn(
    db: AuthDb,
    user_id: str,
    conversation_id: str | None,
    question: str,
    answer: str,
    card_entries: tuple[CardEntry, ...] | None,
    now: int,
) -> str:
    """回答成功之后才落盘。没有新引用就不改卡片。"""
    with db.immediate() as conn:
        _ensure(conn)
        stored_id = _open_conversation(
            conn,
            user_id,
            conversation_id,
            question,
            now,
        )
        _append_exchange(conn, stored_id, question, answer, now)
        if card_entries:
            _save_entries(conn, stored_id, card_entries)
        return stored_id


def _ensure(conn: sqlite3.Connection) -> None:
    # executescript 会提交当前事务，所以建表逐条执行。
    conn.execute(_CREATE_CONVERSATIONS)
    conn.execute(_CREATE_CONVERSATION_INDEX)
    conn.execute(_CREATE_MESSAGES)
    conn.execute(_CREATE_MESSAGE_INDEX)
    conn.execute(_CREATE_CARDS)


def _open_conversation(
    conn: sqlite3.Connection,
    user_id: str,
    conversation_id: str | None,
    question: str,
    now: int,
) -> str:
    if conversation_id is not None:
        return conversation_id
    stored_id = new_conversation_id()
    conn.execute(
        """
        INSERT INTO conversations (
            conversation_id, user_id, title, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (stored_id, user_id, question, now, now),
    )
    _insert_empty_card(conn, stored_id)
    return stored_id


def _append_exchange(
    conn: sqlite3.Connection,
    conversation_id: str,
    question: str,
    answer: str,
    now: int,
) -> None:
    position = _next_position(conn, conversation_id)
    _insert_message(conn, conversation_id, position, MESSAGE_ROLE_USER, question, now)
    _insert_message(
        conn,
        conversation_id,
        position + 1,
        MESSAGE_ROLE_ASSISTANT,
        answer,
        now,
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
        (now, conversation_id),
    )


def _save_entries(
    conn: sqlite3.Connection,
    conversation_id: str,
    entries: tuple[CardEntry, ...],
) -> None:
    # 这一轮的引用替换上一轮。拒答不走到这里，所以不会把错句写进去。
    payload = json.dumps([entry_payload(item) for item in entries], ensure_ascii=False)
    conn.execute(
        """
        UPDATE session_cards
        SET entries_json = ?, rule_version = ?
        WHERE conversation_id = ?
        """,
        (payload, RULE_VERSION, conversation_id),
    )


def _insert_empty_card(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        """
        INSERT INTO session_cards (conversation_id, rule_version, entries_json)
        VALUES (?, ?, ?)
        """,
        (conversation_id, RULE_VERSION, _EMPTY_ENTRIES),
    )


def _insert_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    position: int,
    role: str,
    body: str,
    now: int,
) -> None:
    conn.execute(
        """
        INSERT INTO conversation_messages (
            conversation_id, position, role, body, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, position, role, body, now),
    )


def _next_position(conn: sqlite3.Connection, conversation_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(position), -1) + 1
        FROM conversation_messages
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    ).fetchone()
    return int(row[0])


def _load_card(conn: sqlite3.Connection, conversation_id: str) -> SessionCard:
    row = conn.execute(
        """
        SELECT rule_version, entries_json FROM session_cards
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        return empty_card()
    version = str(row["rule_version"]) or RULE_VERSION
    return SessionCard(version, _parse_entries(str(row["entries_json"])))


def _parse_entries(raw: str) -> tuple[CardEntry, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    entries = [_parse_entry(item) for item in payload if isinstance(item, dict)]
    return tuple(item for item in entries if item.entry_id)


def _parse_entry(item: dict[str, object]) -> CardEntry:
    entry_id = item.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        return CardEntry("")
    return CardEntry(
        entry_id,
        _optional(item, "concept_key"),
        _optional(item, "title"),
        _optional(item, "location"),
    )


def _optional(item: dict[str, object], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _belongs(conn: sqlite3.Connection, user_id: str, conversation_id: str) -> bool:
    row = _owner_row(conn, conversation_id)
    return row is not None and str(row["user_id"]) == user_id


def _owner_row(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT user_id FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()


def _delete_rows(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        "DELETE FROM conversation_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute(
        "DELETE FROM session_cards WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute(
        "DELETE FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    )


def _summary(row: sqlite3.Row) -> dict[str, str]:
    return {
        "conversation_id": str(row["conversation_id"]),
        "title": str(row["title"]),
    }


def _message(row: sqlite3.Row) -> dict[str, str]:
    return {"role": str(row["role"]), "text": str(row["body"])}
