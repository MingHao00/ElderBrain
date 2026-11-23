"""一轮配额和审计。账本用临时 sqlite，不发 HTTP。"""

from __future__ import annotations

import sys
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_quota import Reserved, open_uses
from test_turn_quota_kit import (
    USER_TEXT,
    _Calls,
    _cost,
    _ledger,
    _ports,
    _reasons,
    _released,
    _started,
    _turn,
)
from turn_quota import (
    REASON_ABANDON,
    REASON_DISCONNECT,
    REASON_NO_EVIDENCE,
    REASON_SUBMIT,
    RESERVE_COST_MICRO,
    begin_quota,
    close_quota,
    make_quota_ports,
    note_body,
    on_continue,
    on_model_failure,
    on_pause,
    on_tool_failure,
)

FIRST_COST_MICRO = 30
CONTINUE_COST_MICRO = 12


def test_begin_reserves_once() -> None:
    """同一轮开始两次只占一次。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        first = begin_quota(turn, ports)
        second = begin_quota(turn, ports)

        assert first is not None
        assert second is None
        assert calls.reserve == 1
        assert calls.reserve_costs == [RESERVE_COST_MICRO]
        assert turn.quota.reserved is True
        assert turn.quota.event_id == first.event_id
        assert open_uses(db, account.user_id) == 1


def test_model_failure_before_body_releases() -> None:
    """任何正文出现前，模型失败退回这一次。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        held = begin_quota(turn, ports)
        assert held is not None
        on_model_failure(turn, ports)

        assert calls.release == 1
        assert open_uses(db, account.user_id) == 0
        assert _released(db, held.event_id) == 1


def test_model_failure_after_body_keeps_quota() -> None:
    """已经有正文后再失败，不退回。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        begin_quota(turn, ports)
        note_body(turn)
        on_model_failure(turn, ports)

        assert calls.release == 0
        assert turn.quota.has_body is True
        assert open_uses(db, account.user_id) == 1


def test_tool_failure_does_not_release() -> None:
    """工具失败不退回，哪怕正文还没出现。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        held = begin_quota(turn, ports)
        assert held is not None
        on_tool_failure(turn, ports)

        assert calls.release == 0
        assert calls.reserve == 1
        assert open_uses(db, account.user_id) == 1
        assert _released(db, held.event_id) == 0


def test_pause_does_not_close() -> None:
    """暂停不另占，不写审计，也不收口。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        begin_quota(turn, ports)
        on_pause(turn)

        assert calls.reserve == 1
        assert calls.audit == []
        assert calls.settle == []
        assert turn.quota.audit_closed is False
        assert _reasons(db) == []


def test_continue_merges_into_same_audit_row() -> None:
    """继续的费用加进同一次占用，收口时只写一行。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _turn()
        turn.start(USER_TEXT)
        held = begin_quota(turn, ports)
        assert isinstance(held, Reserved)
        on_pause(turn)
        on_continue(turn, FIRST_COST_MICRO)
        on_continue(turn, CONTINUE_COST_MICRO)
        total = FIRST_COST_MICRO + CONTINUE_COST_MICRO
        close_quota(turn, ports, REASON_SUBMIT)
        close_quota(turn, ports, REASON_SUBMIT)

        assert calls.reserve == 1
        assert calls.release == 0
        assert calls.settle == [(held.event_id, total)]
        assert calls.audit == [(REASON_SUBMIT, total)]
        assert turn.quota.cost_micro == total
        assert turn.quota.event_id == held.event_id
        assert turn.quota.audit_closed is True
        assert _reasons(db) == [REASON_SUBMIT]
        assert USER_TEXT not in _reasons(db)[0]
        assert _cost(db, held.event_id) == total


def test_disconnect_and_abandon_each_write_one_row() -> None:
    """断开取消和新问题放弃暂停各写一行，暂停之前没有审计行。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        first = _started(ports)
        second = _started(ports)
        on_pause(first)
        on_pause(second)
        assert calls.audit == []
        assert _reasons(db) == []

        close_quota(first, ports, REASON_DISCONNECT)
        assert calls.audit == [(REASON_DISCONNECT, 0)]
        assert _reasons(db) == [REASON_DISCONNECT]
        on_pause(second)
        assert _reasons(db) == [REASON_DISCONNECT]

        close_quota(second, ports, REASON_ABANDON)
        assert calls.reserve == 2
        assert [reason for reason, _cost_micro in calls.audit] == [
            REASON_DISCONNECT,
            REASON_ABANDON,
        ]
        assert _reasons(db) == [REASON_DISCONNECT, REASON_ABANDON]
        for reason in _reasons(db):
            assert USER_TEXT not in reason


def test_no_evidence_closes_once_without_user_text() -> None:
    """证据不足写一行短原因。再收口不会写第二行。"""
    with _ledger() as (db, account, now):
        ports = make_quota_ports(db, account.user_id, account.created_at, now)
        turn = _started(ports)
        close_quota(turn, ports, REASON_NO_EVIDENCE)
        close_quota(turn, ports, REASON_NO_EVIDENCE)

        assert turn.quota.audit_closed is True
        assert _reasons(db) == [REASON_NO_EVIDENCE]
        assert USER_TEXT not in _reasons(db)[0]


def test_close_rejects_user_text_as_reason() -> None:
    """原因只能是短词，不能塞进用户原文。"""
    with _ledger() as (db, account, now):
        calls = _Calls()
        ports = _ports(db, account, now, calls)
        turn = _started(ports)
        try:
            close_quota(turn, ports, USER_TEXT)
        except ValueError:
            rejected = True
        else:
            rejected = False

        assert rejected is True
        assert calls.audit == []
        assert _reasons(db) == []
        assert turn.quota.audit_closed is False
