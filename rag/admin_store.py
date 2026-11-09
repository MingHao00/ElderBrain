"""来源状态、索引指针、审核队列和管理审计。

和账号库同一个 sqlite 文件。占位符用 ?。
这一步不换 PostgreSQL，也不读取或重建 FAISS。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from audit_record import (
    ACTION_DISABLE_SOURCE,
    ACTION_ENABLE_SOURCE,
    ACTION_PUBLISH,
    ACTION_REBUILD_INDEX,
    ACTION_ROLLBACK,
    FIELD_ACTION,
    FIELD_ACTOR_ID,
    FIELD_CREATED_AT,
    build_admin_audit,
)
from auth_constants import (
    ERR_INDEX_EXISTS,
    ERR_INDEX_MISSING,
    ERR_INDEX_UNCONFIGURED,
    ERR_SOURCE_MISSING,
)
from auth_db import AuthDb
from config import SOURCE_ID

# 停用行不存在时仍参与检索，避免旧库一上来就拒答。
SOURCE_ENABLED = 1
SOURCE_DISABLED = 0
# 指针表只留一行。SQLite 的 CHECK 不能绑定 ?，数字与这里相同。
POINTER_ROW_ID = 1
# 至少比 idx- 再长一截，避免把单个词当成版本号。
INDEX_VERSION_MIN_CHARS = 8
# 上限挡住贴进来的长文本。请求审计也不收像地址的版本号。
INDEX_VERSION_MAX_CHARS = 64
_VERSION_MARKS = frozenset("._-")

_CREATE_SOURCES = """
CREATE TABLE IF NOT EXISTS source_states (
    source_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_BUILDS = """
CREATE TABLE IF NOT EXISTS index_builds (
    index_version TEXT PRIMARY KEY,
    built_at TEXT NOT NULL
)
"""
_CREATE_POINTER = (
    "CREATE TABLE IF NOT EXISTS published_pointer ("
    "row_id INTEGER PRIMARY KEY CHECK (row_id = "
    + str(POINTER_ROW_ID)
    + "), index_version TEXT NOT NULL, previous_version TEXT)"
)
_CREATE_QUEUE = """
CREATE TABLE IF NOT EXISTS review_queue (
    item_id INTEGER PRIMARY KEY,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    question TEXT NOT NULL
)
"""
_CREATE_ADMIN = """
CREATE TABLE IF NOT EXISTS admin_audits (
    audit_id INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL
)
"""
_SCHEMA = (
    _CREATE_SOURCES,
    _CREATE_BUILDS,
    _CREATE_POINTER,
    _CREATE_QUEUE,
    _CREATE_ADMIN,
)


class AdminRejected(Exception):
    """写操作没有完成。消息是固定文案，不带来源编号或版本号。"""


def source_is_enabled(db: AuthDb, source_id: str) -> bool:
    """没有停用记录时仍参与检索。第一期只判断这一个来源。"""
    with db.immediate() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT enabled FROM source_states WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    if row is None:
        return True
    return int(row["enabled"]) == SOURCE_ENABLED


def set_source_enabled(
    db: AuthDb,
    actor_id: str,
    source_id: str,
    enabled: bool,
    now: datetime,
) -> None:
    """停用或重新启用来源，并记一条管理审计。"""
    if source_id != SOURCE_ID:
        raise AdminRejected(ERR_SOURCE_MISSING)
    action = ACTION_ENABLE_SOURCE if enabled else ACTION_DISABLE_SOURCE
    record = build_admin_audit(actor_id, now, action)
    flag = SOURCE_ENABLED if enabled else SOURCE_DISABLED
    with db.immediate() as conn:
        _ensure(conn)
        _upsert_source(conn, source_id, flag, now)
        _insert_admin(conn, record)


def rebuild_index(
    db: AuthDb,
    actor_id: str,
    builder: Callable[[], str] | None,
    now: datetime,
) -> str:
    """写入构建函数返回的新版本。不改已发布指针，也不调用嵌入。"""
    version = _from_builder(builder)
    record = build_admin_audit(actor_id, now, ACTION_REBUILD_INDEX)
    with db.immediate() as conn:
        _ensure(conn)
        if _has_build(conn, version):
            raise AdminRejected(ERR_INDEX_EXISTS)
        _insert_build(conn, version, now)
        _insert_admin(conn, record)
    return version


def publish_index(
    db: AuthDb,
    actor_id: str,
    index_version: str,
    now: datetime,
) -> None:
    """指针切到已构建版本。目标不存在时整笔事务回滚。"""
    version = _require_version(index_version)
    record = build_admin_audit(actor_id, now, ACTION_PUBLISH)
    with db.immediate() as conn:
        _ensure(conn)
        if not _has_build(conn, version):
            raise AdminRejected(ERR_INDEX_MISSING)
        _point_at(conn, version)
        _insert_admin(conn, record)


def rollback_index(db: AuthDb, actor_id: str, now: datetime) -> None:
    """只把指针切回上一版。没有上一版就不改，构建记录仍保留。"""
    record = build_admin_audit(actor_id, now, ACTION_ROLLBACK)
    with db.immediate() as conn:
        _ensure(conn)
        previous = _rollback_target(conn)
        _write_pointer(conn, previous, None)
        _insert_admin(conn, record)


def published_index_version(db: AuthDb) -> str | None:
    """当前已发布版本。还没有发布记录时返回 None。"""
    with db.immediate() as conn:
        _ensure(conn)
        row = _pointer_row(conn)
    if row is None:
        return None
    return str(row["index_version"])


def ensure_tables(conn: sqlite3.Connection) -> None:
    """建管理表。调用方已经处在独占事务里。"""
    _ensure(conn)


def _from_builder(builder: Callable[[], str] | None) -> str:
    if builder is None:
        raise AdminRejected(ERR_INDEX_UNCONFIGURED)
    version = _version_text(builder())
    if version is None:
        raise AdminRejected(ERR_INDEX_MISSING)
    return version


def _require_version(value: object) -> str:
    version = _version_text(value)
    if version is None:
        raise AdminRejected(ERR_INDEX_MISSING)
    return version


def _version_text(value: object) -> str | None:
    if not isinstance(value, str) or not _version_shape(value):
        return None
    return value


def _version_shape(value: str) -> bool:
    length = len(value)
    if length < INDEX_VERSION_MIN_CHARS or length > INDEX_VERSION_MAX_CHARS:
        return False
    # 以字母开头，避免把 IPv4 记成版本号后请求审计拒绝落盘。
    if not value[0].isascii() or not value[0].isalpha():
        return False
    return all(_version_char(ch) for ch in value)


def _version_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in _VERSION_MARKS)


def _rollback_target(conn: sqlite3.Connection) -> str:
    row = _pointer_row(conn)
    if row is None or row["previous_version"] is None:
        raise AdminRejected(ERR_INDEX_MISSING)
    previous = str(row["previous_version"])
    # 上一版必须还在构建记录里。找不到就保持当前指针。
    if not _has_build(conn, previous):
        raise AdminRejected(ERR_INDEX_MISSING)
    return previous


def _point_at(conn: sqlite3.Connection, version: str) -> None:
    current = _pointer_row(conn)
    if current is None:
        _write_pointer(conn, version, None)
        return
    published = str(current["index_version"])
    if published == version:
        return
    _write_pointer(conn, version, published)


def _write_pointer(
    conn: sqlite3.Connection,
    version: str,
    previous: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO published_pointer (row_id, index_version, previous_version)
        VALUES (?, ?, ?)
        ON CONFLICT(row_id) DO UPDATE SET
            index_version = excluded.index_version,
            previous_version = excluded.previous_version
        """,
        (POINTER_ROW_ID, version, previous),
    )


def _pointer_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT index_version, previous_version
        FROM published_pointer
        WHERE row_id = ?
        """,
        (POINTER_ROW_ID,),
    ).fetchone()


def _has_build(conn: sqlite3.Connection, version: str) -> bool:
    row = conn.execute(
        "SELECT index_version FROM index_builds WHERE index_version = ?",
        (version,),
    ).fetchone()
    return row is not None


def _insert_build(conn: sqlite3.Connection, version: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO index_builds (index_version, built_at) VALUES (?, ?)",
        (version, now.isoformat()),
    )


def _upsert_source(
    conn: sqlite3.Connection,
    source_id: str,
    enabled: int,
    now: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO source_states (source_id, enabled, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
        """,
        (source_id, enabled, now.isoformat()),
    )


def _insert_admin(conn: sqlite3.Connection, record: dict[str, object]) -> None:
    conn.execute(
        """
        INSERT INTO admin_audits (actor_id, created_at, action)
        VALUES (?, ?, ?)
        """,
        (
            str(record[FIELD_ACTOR_ID]),
            str(record[FIELD_CREATED_AT]),
            str(record[FIELD_ACTION]),
        ),
    )


def _ensure(conn: sqlite3.Connection) -> None:
    # executescript 会提交，把独占事务提前结束。所以逐条执行。
    for statement in _SCHEMA:
        conn.execute(statement)
