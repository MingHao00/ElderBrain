"""账号、拒绝名单和 OAuth state 的存储。

以后换 PostgreSQL 时只换这一层，以及同样写 SQL 的 auth_credentials.py、
auth_quota.py、audit_store.py。上面的登录和令牌代码不写 SQL。
占位符用 ?；换库时改成该驱动的占位符。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from auth_constants import (
    AUTH_DB_NAME,
    IP_HOUR_SECONDS,
    IP_HOURLY_SIGNUPS,
    PROVIDER_EMAIL,
    PROVIDER_GITHUB,
    SQLITE_TIMEOUT_SECONDS,
)
from auth_identity import new_user_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id TEXT PRIMARY KEY,
    auth_provider TEXT NOT NULL,
    email TEXT,
    github_id TEXT,
    github_login TEXT,
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS accounts_email
    ON accounts(email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS accounts_github
    ON accounts(github_id) WHERE github_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS signups (
    id INTEGER PRIMARY KEY,
    ip_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_jti (
    jti TEXT PRIMARY KEY,
    exp INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS email_codes (
    email_hash TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    sent_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS email_sends (
    id INTEGER PRIMARY KEY,
    email_hash TEXT NOT NULL,
    sent_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS quota_uses (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    asked_at INTEGER NOT NULL,
    cost_micro INTEGER NOT NULL,
    released INTEGER NOT NULL
);
-- 一天一行。和费用写在同一事务里，挡住同一天的第二封告警。
CREATE TABLE IF NOT EXISTS site_cost_alerts (
    day_start INTEGER PRIMARY KEY
);
"""


@dataclass(frozen=True)
class Account:
    """账号。github_login 不放进这个对象，避免流进日志和令牌。"""

    user_id: str
    provider: str
    email: str | None
    github_id: str | None
    created_at: int


class AuthDb:
    """一个 sqlite 文件。每个方法自己开事务，不要嵌套调用。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def account_by_id(self, user_id: str) -> Account | None:
        """按假名编号取账号。"""
        with self.immediate() as conn:
            return _by_id(conn, user_id)

    def login_email(self, email: str, ip_hash: str, now: int) -> Account | str:
        """已有账号直接返回。新账号占一个 IP 名额，满了返回 ip。"""
        with self.immediate() as conn:
            found = _by_email(conn, email)
            if found is not None:
                return found
            if not _signup_room(conn, ip_hash, now):
                return "ip"
            account = _insert(conn, PROVIDER_EMAIL, email, None, None, now)
            _add_signup(conn, ip_hash, now)
            return account

    def login_github(
        self,
        github_id: str,
        github_login: str,
        ip_hash: str | None,
        now: int,
    ) -> Account | str:
        """只按数字 id 认人。没有 IP 哈希时不新建账号。"""
        with self.immediate() as conn:
            found = _by_github(conn, github_id)
            if found is not None:
                return found
            if ip_hash is None or not _signup_room(conn, ip_hash, now):
                return "ip"
            account = _insert(conn, PROVIDER_GITHUB, None, github_id, github_login, now)
            _add_signup(conn, ip_hash, now)
            return account

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """独占写事务。并发扣次数靠这一层串行。"""
        self._lock.acquire()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            # close 或连接失败不能把锁留住，否则后面的登录会一直等。
            self._lock.release()

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None 才不会在 BEGIN IMMEDIATE 外面再套一层事务。
        conn = sqlite3.connect(
            self.path,
            timeout=SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def default_db_path() -> Path:
    """本机演示库。线上换成 PostgreSQL 时不要再写这个文件。"""
    return Path(__file__).resolve().parent / AUTH_DB_NAME


def _insert(
    conn: sqlite3.Connection,
    provider: str,
    email: str | None,
    github_id: str | None,
    github_login: str | None,
    now: int,
) -> Account:
    user_id = new_user_id()
    conn.execute(
        """
        INSERT INTO accounts (
            user_id, auth_provider, email, github_id, github_login, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, provider, email, github_id, github_login, now),
    )
    return Account(user_id, provider, email, github_id, now)


def _by_id(conn: sqlite3.Connection, user_id: str) -> Account | None:
    row = conn.execute(
        """
        SELECT user_id, auth_provider, email, github_id, created_at
        FROM accounts WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    return _account(row)


def _by_email(conn: sqlite3.Connection, email: str) -> Account | None:
    row = conn.execute(
        """
        SELECT user_id, auth_provider, email, github_id, created_at
        FROM accounts WHERE email = ?
        """,
        (email,),
    ).fetchone()
    return _account(row)


def _by_github(conn: sqlite3.Connection, github_id: str) -> Account | None:
    row = conn.execute(
        """
        SELECT user_id, auth_provider, email, github_id, created_at
        FROM accounts WHERE github_id = ?
        """,
        (github_id,),
    ).fetchone()
    return _account(row)


def _account(row: sqlite3.Row | None) -> Account | None:
    if row is None:
        return None
    email = None if row["email"] is None else str(row["email"])
    github_id = None if row["github_id"] is None else str(row["github_id"])
    return Account(
        str(row["user_id"]),
        str(row["auth_provider"]),
        email,
        github_id,
        int(row["created_at"]),
    )


def _signup_room(conn: sqlite3.Connection, ip_hash: str, now: int) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM signups
        WHERE ip_hash = ? AND created_at > ?
        """,
        (ip_hash, now - IP_HOUR_SECONDS),
    ).fetchone()
    return int(row[0]) < IP_HOURLY_SIGNUPS


def _add_signup(conn: sqlite3.Connection, ip_hash: str, now: int) -> None:
    conn.execute(
        "INSERT INTO signups (ip_hash, created_at) VALUES (?, ?)",
        (ip_hash, now),
    )
