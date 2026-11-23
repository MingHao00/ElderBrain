"""一轮的配额占用和审计收口。

这些函数接收一轮对象。它们不发 HTTP，也不连接账本以外的服务。
每日上限仍走 auth_quota，不在这里另写一套。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from audit_log import record_rejection
from auth_constants import HTTP_OK
from auth_db import AuthDb
from auth_quota import (
    QuotaDeny,
    Reserved,
    release_quota,
    reserve_quota,
    settle_cost,
)
from turn_run import TurnRun

REASON_SUBMIT = "提交"
REASON_NO_EVIDENCE = "证据不足"
REASON_DISCONNECT = "断开取消"
REASON_ABANDON = "新问题放弃暂停"
RESERVE_COST_MICRO = 0
NEGATIVE_COST = "费用不能为负"
UNKNOWN_REASON = "审计原因无效"
CLOSE_REASONS = frozenset({
    REASON_SUBMIT,
    REASON_NO_EVIDENCE,
    REASON_DISCONNECT,
    REASON_ABANDON,
})

ReserveFn = Callable[[AuthDb, str, int, int, int], Reserved | QuotaDeny]
ReleaseFn = Callable[[AuthDb, int], None]
SettleFn = Callable[[AuthDb, int, int], bool]
WriteAuditFn = Callable[[AuthDb, str, str, int], None]


@dataclass(frozen=True)
class QuotaPorts:
    """一轮配额要用的库，以及四个可替换动作。"""

    db: AuthDb
    user_id: str
    created_at: int
    now: int
    reserve: ReserveFn
    release: ReleaseFn
    settle: SettleFn
    write_audit: WriteAuditFn


def make_quota_ports(
    db: AuthDb,
    user_id: str,
    created_at: int,
    now: int,
    *,
    reserve: ReserveFn | None = None,
    release: ReleaseFn | None = None,
    settle: SettleFn | None = None,
    write_audit: WriteAuditFn | None = None,
) -> QuotaPorts:
    """组装配额端口。没传入的动作调用现有配额和审计。

    Args:
        db: 账号库。调用方打开，这里不另连外部服务。
        user_id: 访客编号。
        created_at: 账号创建时间，交给现有每日上限。
        now: 这次占用记在哪个时间戳。
        reserve: 占一次。默认 reserve_quota。
        release: 退回。默认 release_quota。
        settle: 把费用合计写进同一次占用。默认 settle_cost。
        write_audit: 写一行审计。默认 write_turn_audit。

    Returns:
        可注入的端口。本函数自己不占配额。
    """
    chosen_reserve = reserve_quota if reserve is None else reserve
    chosen_release = release_quota if release is None else release
    chosen_settle = settle_cost if settle is None else settle
    chosen_audit = write_turn_audit if write_audit is None else write_audit
    return QuotaPorts(
        db,
        user_id,
        created_at,
        now,
        chosen_reserve,
        chosen_release,
        chosen_settle,
        chosen_audit,
    )


def begin_quota(turn: TurnRun, ports: QuotaPorts) -> Reserved | QuotaDeny | None:
    """循环开始前占一次。同一轮已经占过就直接返回。

    Args:
        turn: 当前轮。
        ports: 配额端口。

    Returns:
        新占到的一次、被现有上限拒绝，或已经占过时的 None。
    """
    if turn.quota.reserved:
        return None
    held = ports.reserve(
        ports.db,
        ports.user_id,
        ports.created_at,
        RESERVE_COST_MICRO,
        ports.now,
    )
    if not isinstance(held, Reserved):
        return held
    turn.quota.reserved = True
    turn.quota.event_id = held.event_id
    return held


def note_body(turn: TurnRun) -> None:
    """记下已经出现正文。之后模型失败不再退回。"""
    turn.quota.has_body = True


def on_model_failure(turn: TurnRun, ports: QuotaPorts) -> None:
    """正文出现前模型失败则退回这一次。已有正文不退。

    Args:
        turn: 当前轮。
        ports: 用来调用 release。
    """
    if turn.quota.has_body or not turn.quota.reserved:
        return
    event_id = turn.quota.event_id
    if event_id is None:
        return
    ports.release(ports.db, event_id)


def on_tool_failure(turn: TurnRun, ports: QuotaPorts) -> None:
    """工具失败不退回。签名接收 release，这里故意不调用它。"""
    # 保留参数，好让测试证明工具失败没有走到 release。
    _ = (turn.quota.reserved, ports.release)


def on_pause(turn: TurnRun) -> None:
    """暂停不另占、不写审计、不收口。检查点仍由原来的暂停流程写。"""
    if turn.quota.audit_closed:
        return


def on_continue(turn: TurnRun, added_cost_micro: int) -> None:
    """继续不另占。费用先加进这一轮，收口时进入同一次占用和同一行。

    Args:
        turn: 当前轮。必须是已经占过配额的那一个对象。
        added_cost_micro: 继续这一段的微元。

    Raises:
        ValueError: 费用为负。
    """
    _add_cost(turn, added_cost_micro)


def close_quota(turn: TurnRun, ports: QuotaPorts, reason: str) -> None:
    """四种结局各写一行并收口。已经收口则不再写第二行。

    原因只用短词，不读用户消息，避免原文进审计。

    Args:
        turn: 当前轮。
        ports: 用来结算和写审计。
        reason: 提交、证据不足、断开取消或新问题放弃暂停。

    Raises:
        ValueError: 原因不是这四种短词，或累计费用为负。
    """
    if turn.quota.audit_closed:
        return
    _require_reason(reason)
    _require_cost(turn.quota.cost_micro)
    _settle_open(turn, ports)
    ports.write_audit(ports.db, ports.user_id, reason, turn.quota.cost_micro)
    turn.quota.audit_closed = True


def write_turn_audit(
    db: AuthDb,
    user_id: str,
    reason: str,
    cost_micro: int,
) -> None:
    """把短原因写入审计。费用合计不写进 reason。

    金额记在同一次占用上。reason 只接受四种短词，不接受用户原文。

    Args:
        db: 账号库。
        user_id: 访客编号。
        reason: 四种收口短原因之一。
        cost_micro: 费用合计。只校验非负。

    Raises:
        ValueError: 原因不在四种短词里，或费用为负。
    """
    _require_reason(reason)
    _require_cost(cost_micro)
    record_rejection(db, user_id, HTTP_OK, reason, None)


def _add_cost(turn: TurnRun, cost_micro: int) -> None:
    _require_cost(cost_micro)
    if turn.quota.audit_closed:
        return
    turn.quota.cost_micro += cost_micro


def _settle_open(turn: TurnRun, ports: QuotaPorts) -> None:
    event_id = turn.quota.event_id
    if event_id is None:
        return
    # 返回值表示要不要发信。发信会走出这一步，这里丢掉。
    ports.settle(ports.db, event_id, turn.quota.cost_micro)


def _require_reason(reason: str) -> None:
    if reason not in CLOSE_REASONS:
        raise ValueError(UNKNOWN_REASON)


def _require_cost(cost_micro: int) -> None:
    if cost_micro < 0:
        raise ValueError(NEGATIVE_COST)
