"""对话存档和会话卡片。和账号库同一个文件。

以后换 PostgreSQL 时只换这一层。占位符用 ?。
删对话不碰审计表。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass

from auth_constants import CONVERSATION_ID_BYTES, CONVERSATION_ID_PREFIX
from auth_db import AuthDb
from config import RULE_VERSION
from session_card import CardEntry, SessionCard, empty_card, entry_payload

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"
_EMPTY_ENTRIES = "[]"
# 0 表示还没折进卡片。助手回答也可以是 0，但扫描只看用户原话。
FOLDED_NO = 0
FOLDED_YES = 1

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
_CREATE_MESSAGES = f"""
CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    folded INTEGER NOT NULL DEFAULT {FOLDED_NO}
)
"""
# SQLite 的 ALTER TABLE 不能给默认值绑定 ?。0 与 FOLDED_NO 相同。
_ADD_FOLDED = (
    "ALTER TABLE conversation_messages ADD COLUMN folded INTEGER NOT NULL DEFAULT "
    + str(FOLDED_NO)
)
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


def session_card_exists(db: AuthDb, conversation_id: str) -> bool:
    """这场对话在 session_cards 里有没有一行。

    没有行时 load_card 仍返回空卡片，不能靠条目是否为空判断行在不在。

    Args:
        db: 账号库。
        conversation_id: 这场对话。

    Returns:
        有这一行时为 True。条目是空数组也算有。
    """
    with db.immediate() as conn:
        _ensure(conn)
        return _card_exists(conn, conversation_id)


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
    """回答成功之后才落盘。没有新引用就不改卡片。

    生成期间对话可能已被删掉。费用已经结算，这时不再写入，原编号照样返回。
    """
    with db.immediate() as conn:
        _ensure(conn)
        # 提问门闩只在生成前查过一次。此刻已不属于该用户时，再写就是无主消息。
        if conversation_id is not None and not _belongs(conn, user_id, conversation_id):
            return conversation_id
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
    _ensure_folded(conn)


def _ensure_folded(conn: sqlite3.Connection) -> None:
    # 已有库的建表语句没有这一列。缺了再补，已有的原话默认未折。
    if _has_column(conn, "conversation_messages", "folded"):
        return
    conn.execute(_ADD_FOLDED)


def _has_column(conn: sqlite3.Connection, table: str, name: str) -> bool:
    # PRAGMA 的表名不能用 ?。这里只传入本模块写死的表名。
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row["name"]) == name for row in rows)


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
            conversation_id, position, role, body, created_at, folded
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, position, role, body, now, FOLDED_NO),
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


@dataclass(frozen=True)
class DueTurn:
    """一次扫描读出的未折用户原话。

    latest_user_message_id 是读库时最后一条用户问题的编号。
    写回时用它识别扫描期间新来的问题。
    """

    conversation_id: str
    user_id: str
    message_ids: tuple[int, ...]
    utterances: tuple[str, ...]
    card: SessionCard
    latest_user_message_id: int


def load_due_turns(db: AuthDb, now: int, idle_seconds: int) -> tuple[DueTurn, ...]:
    """读出已闲置且仍有未折用户原话的对话。返回时事务已经结束。

    Args:
        db: 账号库。
        now: 这次扫描的时间戳。
        idle_seconds: 从最后一条用户问题起算的闲置时长。

    Returns:
        到点的对话。没有未折原话的不在里面。

    Raises:
        ValueError: idle_seconds 为负。
    """
    if idle_seconds < 0:
        raise ValueError("闲置时长不能为负")
    with db.immediate() as conn:
        _ensure(conn)
        return _collect_due(conn, now - idle_seconds)


def apply_idle_card(
    db: AuthDb,
    conversation_id: str,
    message_ids: tuple[int, ...],
    latest_user_message_id: int,
    entries: tuple[CardEntry, ...],
    rule_version: str,
) -> bool:
    """没有更新的用户问题才替换卡片，并只标记这一批用户原话。

    Args:
        db: 账号库。
        conversation_id: 这场对话。
        message_ids: 这次读到的未折用户原话。
        latest_user_message_id: 读库时最后一条用户问题。
        entries: 摘要返回的条目，替换整张卡片。
        rule_version: 写回的规则版本。

    Returns:
        是否写上了。已有更新的用户问题时为 False，卡片和未折标记都不变。
    """
    if not message_ids:
        return False
    with db.immediate() as conn:
        _ensure(conn)
        if not _can_fold(conn, conversation_id, message_ids, latest_user_message_id):
            return False
        _replace_card(conn, conversation_id, entries, rule_version)
        _mark_folded(conn, conversation_id, message_ids)
        return True


def _collect_due(conn: sqlite3.Connection, deadline: int) -> tuple[DueTurn, ...]:
    found: list[DueTurn] = []
    for row in _due_rows(conn, deadline):
        turn = _due_turn(conn, row)
        if turn is not None:
            found.append(turn)
    return tuple(found)


def _due_rows(conn: sqlite3.Connection, deadline: int) -> list[sqlite3.Row]:
    # 计时只看用户原话。助手回答更晚也不重新计时。
    return list(
        conn.execute(
            """
            SELECT c.conversation_id AS conversation_id,
                   c.user_id AS user_id,
                   MAX(m.created_at) AS latest_at,
                   MAX(m.message_id) AS latest_id
            FROM conversations c
            JOIN conversation_messages m
                ON m.conversation_id = c.conversation_id AND m.role = ?
            GROUP BY c.conversation_id, c.user_id
            HAVING latest_at <= ?
            """,
            (MESSAGE_ROLE_USER, deadline),
        ).fetchall()
    )


def _due_turn(conn: sqlite3.Connection, row: sqlite3.Row) -> DueTurn | None:
    conversation_id = str(row["conversation_id"])
    messages = _open_user_messages(conn, conversation_id)
    if not messages:
        return None
    return DueTurn(
        conversation_id,
        str(row["user_id"]),
        tuple(int(item["message_id"]) for item in messages),
        tuple(str(item["body"]) for item in messages),
        _load_card(conn, conversation_id),
        int(row["latest_id"]),
    )


def _open_user_messages(
    conn: sqlite3.Connection,
    conversation_id: str,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT message_id, body FROM conversation_messages
            WHERE conversation_id = ? AND role = ? AND folded = ?
            ORDER BY position ASC
            """,
            (conversation_id, MESSAGE_ROLE_USER, FOLDED_NO),
        ).fetchall()
    )


def _can_fold(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_ids: tuple[int, ...],
    latest_user_message_id: int,
) -> bool:
    if _newer_user_message(conn, conversation_id, latest_user_message_id):
        return False
    if not _batch_still_open(conn, conversation_id, message_ids):
        return False
    return _card_exists(conn, conversation_id)


def _newer_user_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    latest_user_message_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM conversation_messages
        WHERE conversation_id = ? AND role = ? AND message_id > ?
        LIMIT 1
        """,
        (conversation_id, MESSAGE_ROLE_USER, latest_user_message_id),
    ).fetchone()
    return row is not None


def _batch_still_open(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_ids: tuple[int, ...],
) -> bool:
    # 条数不固定，只能拼出同样多个 ?。编号本身仍然绑定，不拼进 SQL。
    marks = ",".join("?" for _item in message_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM conversation_messages
        WHERE conversation_id = ? AND role = ? AND folded = ?
            AND message_id IN ({marks})
        """,
        (conversation_id, MESSAGE_ROLE_USER, FOLDED_NO, *message_ids),
    ).fetchone()
    return int(row[0]) == len(message_ids)


def _card_exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM session_cards WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return row is not None


def _replace_card(
    conn: sqlite3.Connection,
    conversation_id: str,
    entries: tuple[CardEntry, ...],
    rule_version: str,
) -> None:
    payload = json.dumps(
        [entry_payload(item) for item in entries],
        ensure_ascii=False,
    )
    conn.execute(
        """
        UPDATE session_cards
        SET entries_json = ?, rule_version = ?
        WHERE conversation_id = ?
        """,
        (payload, rule_version, conversation_id),
    )


def _mark_folded(
    conn: sqlite3.Connection,
    conversation_id: str,
    message_ids: tuple[int, ...],
) -> None:
    for message_id in message_ids:
        conn.execute(
            """
            UPDATE conversation_messages
            SET folded = ?
            WHERE conversation_id = ? AND message_id = ? AND role = ? AND folded = ?
            """,
            (
                FOLDED_YES,
                conversation_id,
                message_id,
                MESSAGE_ROLE_USER,
                FOLDED_NO,
            ),
        )
