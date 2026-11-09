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
    QUOTA_SETTLED,
    QUOTA_SITE,
    QUOTA_UNSETTLED,
    SHANGHAI_TZ,
    SITE_DAILY_ALERT_MICRO,
    SITE_DAILY_CAP_MICRO,
    USE_KIND_ASK,
    USE_KIND_IDLE,
    USER_ID_PREFIX,
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


def settle_cost(db: AuthDb, event_id: int, cost_micro: int) -> bool:
    """把这次占位的费用改成实际微元，并标成已结算。费用为负时抛出 ValueError。

    告警只比较已结算费用。从告警线以下跨到线上时，同一事务记下今天已告警。
    返回值表示事务提交后要不要发信。调用方发信失败不能回滚这笔费用。
    """
    if cost_micro < 0:
        raise ValueError("费用不能为负")
    with db.immediate() as conn:
        asked_at = _open_asked_at(conn, event_id)
        if asked_at is None:
            return False
        _set_cost(conn, event_id, cost_micro)
        return _mark_if_crossed(conn, asked_at, cost_micro)


def open_uses(db: AuthDb, user_id: str) -> int:
    """还没退回的提问次数。闲置摘要不占次数。"""
    with db.immediate() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0 AND use_kind = ?
            """,
            (user_id, USE_KIND_ASK),
        ).fetchone()
        return int(row[0])


def site_spent_micro(db: AuthDb, now: int) -> int:
    """这一北京时间日未退回的费用微元，含闲置摘要。

    Args:
        db: 账号库。
        now: 用来判断属于哪一天的时间戳。

    Returns:
        当日 cost_micro 合计。没有记录时为 0。
    """
    with db.immediate() as conn:
        day_start, day_end = _day_bounds(now)
        return _spent_between(conn, day_start, day_end)


def record_idle_cost(db: AuthDb, user_id: str, cost_micro: int, now: int) -> bool:
    """把闲置摘要记入全站当日费用。这一行不占该用户的次数。

    Args:
        db: 账号库。
        user_id: 这场对话的主人，必须是账号表里的编号。
        cost_micro: 这次调用的微元。缺用量时调用方传入 0。
        now: 记入哪一天的时间戳。

    Returns:
        已结算费用是否从告警线以下跨到线上。未结算提问预占不参与比较。调用方再发信。

    Raises:
        ValueError: 费用为负，或 user_id 不是已有账号。
    """
    if cost_micro < 0:
        raise ValueError("费用不能为负")
    with db.immediate() as conn:
        if not _billable_user(conn, user_id):
            raise ValueError("费用必须记在真实用户上")
        _insert_use(
            conn,
            user_id,
            now,
            cost_micro,
            USE_KIND_IDLE,
            QUOTA_SETTLED,
        )
        return _mark_if_crossed(conn, now, cost_micro)


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
    """测试用整数元换算。分以下的生成费用要四舍五入，不能用这个函数。"""
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
    if _spent_between(conn, day_start, day_end) < SITE_DAILY_CAP_MICRO:
        return None
    return QuotaDeny(QUOTA_SITE, day_end)


def _open_asked_at(conn: sqlite3.Connection, event_id: int) -> int | None:
    row = conn.execute(
        "SELECT asked_at, released FROM quota_uses WHERE id = ?",
        (event_id,),
    ).fetchone()
    # 已经退回的一次不能再记费用，否则失败调用仍会抬高当日累计。
    if row is None or int(row["released"]) != 0:
        return None
    return int(row["asked_at"])


def _set_cost(conn: sqlite3.Connection, event_id: int, cost_micro: int) -> None:
    # 写成实际费用后才进入告警累计。预占留在行上时不能把跨线前金额抬过告警线。
    conn.execute(
        "UPDATE quota_uses SET cost_micro = ?, settled = ? WHERE id = ?",
        (cost_micro, QUOTA_SETTLED, event_id),
    )


def _mark_if_crossed(
    conn: sqlite3.Connection,
    asked_at: int,
    cost_micro: int,
) -> bool:
    day_start, day_end = _day_bounds(asked_at)
    # 预占还在库里时闲置摘要可能先入账。把预占算进跨线前，会错过这一天仅有的一次告警。
    total = _settled_between(conn, day_start, day_end)
    # 这一行已经按实际费用写入已结算合计。减去它才是跨线前的累计。
    before = total - cost_micro
    crossed = before < SITE_DAILY_ALERT_MICRO <= total
    if not crossed:
        return False
    return _insert_alert(conn, day_start)


def _spent_between(conn: sqlite3.Connection, day_start: int, day_end: int) -> int:
    # 上限要算上未结算预占，否则并发的第二笔提问会在结算前通过。
    return _sum_cost(conn, day_start, day_end, settled_only=False)


def _settled_between(conn: sqlite3.Connection, day_start: int, day_end: int) -> int:
    return _sum_cost(conn, day_start, day_end, settled_only=True)


def _sum_cost(
    conn: sqlite3.Connection,
    day_start: int,
    day_end: int,
    settled_only: bool,
) -> int:
    if settled_only:
        sql = """
            SELECT COALESCE(SUM(cost_micro), 0) FROM quota_uses
            WHERE released = 0 AND settled = ?
                AND asked_at >= ? AND asked_at < ?
        """
        params: tuple[int, ...] = (QUOTA_SETTLED, day_start, day_end)
    else:
        sql = """
            SELECT COALESCE(SUM(cost_micro), 0) FROM quota_uses
            WHERE released = 0 AND asked_at >= ? AND asked_at < ?
        """
        params = (day_start, day_end)
    row = conn.execute(sql, params).fetchone()
    return int(row[0])


def _insert_alert(conn: sqlite3.Connection, day_start: int) -> bool:
    # 主键是这一北京时间日的起点。并发时后写入的一方插不进去，就不再发信。
    try:
        conn.execute(
            "INSERT INTO site_cost_alerts (day_start) VALUES (?)",
            (day_start,),
        )
    except sqlite3.IntegrityError:
        return False
    return True


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
    # 闲置摘要的 use_kind 不是提问，每日和每分钟都不计这一行。
    if inclusive:
        sql = """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0 AND use_kind = ?
                AND asked_at > ? AND asked_at <= ?
        """
    else:
        sql = """
            SELECT COUNT(*) FROM quota_uses
            WHERE user_id = ? AND released = 0 AND use_kind = ?
                AND asked_at >= ? AND asked_at < ?
        """
    row = conn.execute(sql, (user_id, USE_KIND_ASK, start, end)).fetchone()
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
            WHERE user_id = ? AND released = 0 AND use_kind = ?
                AND asked_at > ? AND asked_at <= ?
            ORDER BY asked_at ASC
            """,
            (user_id, USE_KIND_ASK, start, now),
        ).fetchall()
    )


def _insert_use(
    conn: sqlite3.Connection,
    user_id: str,
    now: int,
    cost_micro: int,
    kind: str = USE_KIND_ASK,
    settled: int = QUOTA_UNSETTLED,
) -> int:
    # 提问插入时还是预占。闲置摘要没有预占，调用方传入已结算的实际费用。
    cursor = conn.execute(
        """
        INSERT INTO quota_uses (
            user_id, asked_at, cost_micro, released, use_kind, settled
        ) VALUES (?, ?, ?, 0, ?, ?)
        """,
        (user_id, now, cost_micro, kind, settled),
    )
    return int(cursor.lastrowid)


def _billable_user(conn: sqlite3.Connection, user_id: str) -> bool:
    # 拒绝 site 这类假编号。费用只能挂在账号表里的那个人身上。
    if not user_id.startswith(USER_ID_PREFIX):
        return False
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row is not None


def _day_bounds(now: int) -> tuple[int, int]:
    local = datetime.fromtimestamp(now, _ZONE)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())
