"""配额计数。以后换 PostgreSQL 时和 auth_db.py 一起换。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from auth_constants import (
    DAILY_LIMIT,
    MICRO_PER_YUAN,
    MINUTE_LIMIT,
    MINUTE_WINDOW_SECONDS,
    NEW_ACCOUNT_DAILY_LIMIT,
    NEW_ACCOUNT_WINDOW_SECONDS,
    QUOTA_DAILY,
    QUOTA_MINUTE,
    QUOTA_SITE,
    SHANGHAI_TZ,
    SITE_DAILY_CAP_MICRO,
)
from auth_db import AuthDb

_ZONE = ZoneInfo(SHANGHAI_TZ)


@dataclass(frozen=True)
class Reserved:
    """刚占上的一次。模型失败时用 event_id 退回。"""

    event_id: int


@dataclass(frozen=True)
class QuotaDeny:
    """哪一种上限，以及何时可以再问。"""

    kind: str
    resume_at: int


def daily_limit(created_at: int, now: int) -> int:
    """新账号的每日上限。"""
    # 用 24 小时而不是自然日，避免午夜注册立刻变成 100 次。
    if now - created_at < NEW_ACCOUNT_WINDOW_SECONDS:
        return NEW_ACCOUNT_DAILY_LIMIT
    return DAILY_LIMIT


def reserve_quota(
    db: AuthDb,
    user_id: str,
    created_at: int,
    cost_micro: int,
    now: int,
) -> Reserved | QuotaDeny:
    """占一次配额。计数和插入在同一个事务里，避免并发超限。"""
    if cost_micro < 0:
        raise ValueError("费用不能为负")
    with db.immediate() as conn:
        denial = _denial(conn, user_id, created_at, now)
        if denial is not None:
            return denial
        return Reserved(_insert_use(conn, user_id, now, cost_micro))


def release_quota(db: AuthDb, event_id: int) -> None:
    """模型调用失败时退回刚占的一次。成功的拒答不退。"""
    with db.immediate() as conn:
        conn.execute(
            "UPDATE quota_uses SET released = 1 WHERE id = ?",
            (event_id,),
        )


def open_uses(db: AuthDb, user_id: str) -> int:
    """还没退回的次数。测试用来确认空问题没有扣次。"""
    with db.immediate() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0
            """,
            (user_id,),
        ).fetchone()
        return int(row[0])


def quota_message(kind: str, resume_at: int) -> str:
    """说明是每日、每分钟还是全站上限，并给出北京时间的恢复点。"""
    if kind == QUOTA_DAILY:
        label = "每日次数已用完"
    elif kind == QUOTA_MINUTE:
        label = "每分钟次数已用完"
    else:
        label = "全站上限已到"
    return f"{label}，恢复时间：{format_shanghai(resume_at)}（北京时间）"


def format_shanghai(timestamp: int) -> str:
    """恢复时间用北京时间，和自然日的切分一致。"""
    local = datetime.fromtimestamp(timestamp, _ZONE)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def yuan_to_micro(yuan: int) -> int:
    """测试用整数元换算。运行路径目前只记 0 元。"""
    return yuan * MICRO_PER_YUAN


def _denial(
    conn: sqlite3.Connection,
    user_id: str,
    created_at: int,
    now: int,
) -> QuotaDeny | None:
    day_start, day_end = _day_bounds(now)
    daily = _daily_denial(conn, user_id, created_at, now, day_start, day_end)
    if daily is not None:
        return daily
    minute = _minute_denial(conn, user_id, now)
    if minute is not None:
        return minute
    return _site_denial(conn, day_start, day_end)


def _daily_denial(
    conn: sqlite3.Connection,
    user_id: str,
    created_at: int,
    now: int,
    day_start: int,
    day_end: int,
) -> QuotaDeny | None:
    used = _count_between(conn, user_id, day_start, day_end, inclusive=False)
    if used < daily_limit(created_at, now):
        return None
    return QuotaDeny(QUOTA_DAILY, _daily_resume(created_at, now, used, day_end))


def _minute_denial(conn: sqlite3.Connection, user_id: str, now: int) -> QuotaDeny | None:
    start = now - MINUTE_WINDOW_SECONDS
    used = _count_between(conn, user_id, start, now, inclusive=True)
    if used < MINUTE_LIMIT:
        return None
    rows = _minute_times(conn, user_id, start, now)
    if not rows:
        return QuotaDeny(QUOTA_MINUTE, now + MINUTE_WINDOW_SECONDS)
    index = min(used - MINUTE_LIMIT, len(rows) - 1)
    resume = int(rows[index][0]) + MINUTE_WINDOW_SECONDS
    return QuotaDeny(QUOTA_MINUTE, resume)


def _site_denial(conn: sqlite3.Connection, day_start: int, day_end: int) -> QuotaDeny | None:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_micro), 0) FROM quota_uses
        WHERE released = 0 AND asked_at >= ? AND asked_at < ?
        """,
        (day_start, day_end),
    ).fetchone()
    if int(row[0]) < SITE_DAILY_CAP_MICRO:
        return None
    return QuotaDeny(QUOTA_SITE, day_end)


def _daily_resume(created_at: int, now: int, used: int, next_midnight: int) -> int:
    upgrade_at = created_at + NEW_ACCOUNT_WINDOW_SECONDS
    opens_today = now < upgrade_at < next_midnight and used < DAILY_LIMIT
    if opens_today:
        return upgrade_at
    return next_midnight


def _count_between(
    conn: sqlite3.Connection,
    user_id: str,
    start: int,
    end: int,
    inclusive: bool,
) -> int:
    if inclusive:
        sql = """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0 AND asked_at > ? AND asked_at <= ?
        """
    else:
        sql = """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0 AND asked_at >= ? AND asked_at < ?
        """
    row = conn.execute(sql, (user_id, start, end)).fetchone()
    return int(row[0])


def _minute_times(
    conn: sqlite3.Connection,
    user_id: str,
    start: int,
    now: int,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT asked_at FROM quota_uses
            WHERE user_id = ? AND released = 0 AND asked_at > ? AND asked_at <= ?
            ORDER BY asked_at ASC
            """,
            (user_id, start, now),
        ).fetchall()
    )


def _insert_use(conn: sqlite3.Connection, user_id: str, now: int, cost_micro: int) -> int:
    cursor = conn.execute(
        """
        INSERT INTO quota_uses (user_id, asked_at, cost_micro, released)
        VALUES (?, ?, ?, 0)
        """,
        (user_id, now, cost_micro),
    )
    return int(cursor.lastrowid)


def _day_bounds(now: int) -> tuple[int, int]:
    local = datetime.fromtimestamp(now, _ZONE)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())
