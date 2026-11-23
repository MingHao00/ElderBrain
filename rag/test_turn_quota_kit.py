"""一轮配额测试的临时账本和查询。"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_constants import SHANGHAI_TZ
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_quota import QuotaDeny, Reserved, release_quota, reserve_quota, settle_cost
from auth_testkit import TEST_IP, auth_env
from turn_quota import (
    QuotaPorts,
    begin_quota,
    make_quota_ports,
    write_turn_audit,
)
from turn_run import ModelStep, TurnRun, TurnState

USER_EMAIL = "turn-quota@example.com"
USER_TEXT = "用户原文：火球术的施法材料"
CONVERSATION_ID = "conv-quota"
MISSING_HOLD = "没有这次占用"
_ZONE = ZoneInfo(SHANGHAI_TZ)
_AUDIT_EXISTS = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'request_audits'"
)
_AUDIT_REASONS = "SELECT reason FROM request_audits"
_HOLD_ROW = "SELECT cost_micro, released FROM quota_uses WHERE id = ?"


class Calls:
    """记下注入函数被调用的次数和参数。"""

    def __init__(self) -> None:
        self.reserve = 0
        self.release = 0
        self.settle: list[tuple[int, int]] = []
        self.audit: list[tuple[str, int]] = []
        self.reserve_costs: list[int] = []


class _Store:
    """配额测试不写检查点。"""

    def load(self, thread_id: str) -> None:
        return None

    def save(self, checkpoint: object) -> None:
        return None

    def delete(self, thread_id: str) -> None:
        return None


class _SilentModel:
    """开始一轮时要有模型，但不产生正文。"""

    def __call__(self, state: TurnState) -> ModelStep:
        return ModelStep("", None)


class _NoTool:
    """配额路径不调用工具。"""

    def __call__(self, name: str) -> str:
        raise AssertionError(name)


@contextmanager
def ledger() -> Iterator[tuple[AuthDb, Account, int]]:
    """和配额测试一样的临时账号库。"""
    env = auth_env()
    env.__enter__()
    folder = tempfile.TemporaryDirectory()
    try:
        db = AuthDb(Path(folder.name) / "auth.sqlite")
        now = _moment()
        account = _account(db, now)
        yield db, account, now
    finally:
        folder.cleanup()
        env.__exit__(None, None, None)


def ports(db: AuthDb, account: Account, now: int, calls: Calls) -> QuotaPorts:
    """计数后仍走现有的占位、退回、结算和审计。"""

    def reserve(
        bound: AuthDb,
        user_id: str,
        created_at: int,
        cost_micro: int,
        asked_at: int,
    ) -> Reserved | QuotaDeny:
        calls.reserve += 1
        calls.reserve_costs.append(cost_micro)
        return reserve_quota(bound, user_id, created_at, cost_micro, asked_at)

    def release(bound: AuthDb, event_id: int) -> None:
        calls.release += 1
        release_quota(bound, event_id)

    def settle(bound: AuthDb, event_id: int, cost_micro: int) -> bool:
        calls.settle.append((event_id, cost_micro))
        return settle_cost(bound, event_id, cost_micro)

    def write_audit(
        bound: AuthDb,
        user_id: str,
        reason: str,
        cost_micro: int,
    ) -> None:
        calls.audit.append((reason, cost_micro))
        write_turn_audit(bound, user_id, reason, cost_micro)

    return make_quota_ports(
        db,
        account.user_id,
        account.created_at,
        now,
        reserve=reserve,
        release=release,
        settle=settle,
        write_audit=write_audit,
    )


def turn() -> TurnRun:
    return TurnRun(CONVERSATION_ID, _Store(), _SilentModel(), _NoTool())


def started(bound: QuotaPorts) -> TurnRun:
    opened = turn()
    opened.start(USER_TEXT)
    begin_quota(opened, bound)
    return opened


def reasons(db: AuthDb) -> list[str]:
    with db.immediate() as conn:
        found = conn.execute(_AUDIT_EXISTS).fetchone()
        if found is None:
            return []
        rows = conn.execute(_AUDIT_REASONS).fetchall()
    return [str(row["reason"]) for row in rows]


def cost(db: AuthDb, event_id: int) -> int:
    row = hold(db, event_id)
    return int(row["cost_micro"])


def released(db: AuthDb, event_id: int) -> int:
    row = hold(db, event_id)
    return int(row["released"])


def hold(db: AuthDb, event_id: int) -> object:
    with db.immediate() as conn:
        row = conn.execute(_HOLD_ROW, (event_id,)).fetchone()
    if row is None:
        raise AssertionError(MISSING_HOLD)
    return row


def _account(db: AuthDb, now: int) -> Account:
    salt = ip_salt()
    assert salt is not None
    account = db.login_email(USER_EMAIL, hash_ip(TEST_IP, salt), now)
    assert isinstance(account, Account)
    return account


def _moment() -> int:
    local = datetime(2026, 11, 23, 12, 0, tzinfo=_ZONE)
    return int(local.timestamp())


_Calls = Calls
_ledger = ledger
_ports = ports
_turn = turn
_started = started
_reasons = reasons
_cost = cost
_released = released
