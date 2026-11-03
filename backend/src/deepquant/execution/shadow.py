"""SHADOW 模式：算出"本应下的单"，但**绝不发送**。

2.9 的两条要求，各自怎么落地
-------------------------
**2.9-a「不发送」**：不是"我记得不调 place_order"，而是**签名里没有券商对象**。
``build_shadow_plan`` 的参数只有台账记录、账户快照、行情——没有 adapter 就
不可能下单。比运行时判断 ``if mode != SHADOW`` 硬得多：那种写法一旦有人把
mode 传错，单就发出去了。

**2.9-a「差异必须可解释」**：解释规则**事前**写成枚举 ``DiffReason``，
不是事后逐条编理由。归不进任何一条的差异一律记 ``UNEXPLAINED``——
G2 的判据「连续 20 个交易日无未解释差异」靠的就是这个计数器，
所以它必须**宁可漏解释不可硬凑**：把一条真差异塞进"资金不足"里，
等于自己把闸门关了。

⭐ 为什么解释规则要包含价格上限/板块/ST
------------------------------------
前瞻主口径（``target_holds``）**没有**做 D5 价格上限、D4 板块与 ST 过滤；
实盘按授权范围必须做。所以"实盘少买了 3 只"大概率**不是 bug**，是口径差。
把这三类写成显式原因，才能让"每日差异"变成"每日可核对"，而不是每周
重新讨论一次"这算不算正常"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from deepquant.broker.models import AccountSnapshot, Quote
from deepquant.execution.planner import PlanInput, PlannedOrder, SkippedItem, plan_orders
from deepquant.market.rules import Side

__all__ = [
    "DiffReason",
    "ExplanationContext",
    "ShadowPlan",
    "ShadowDiff",
    "build_shadow_plan",
    "compare_with_ledger",
    "shadow_report",
]


class DiffReason(str, Enum):
    """差异原因。**事前枚举**——不允许事后临时加一条把差异说圆。"""

    MATCH = "match"
    LEDGER_TARGET_NOT_PLANNED = "ledger_target_not_planned"
    PLANNED_NOT_IN_LEDGER = "planned_not_in_ledger"
    SHARES_DIFFER = "shares_differ"

    # ---- 可解释的口径差（实盘按授权范围过滤，前瞻没过滤）----
    EXPLAINED_PRICE_CAP = "explained_price_cap"
    EXPLAINED_BOARD = "explained_board"
    EXPLAINED_ST = "explained_st"
    EXPLAINED_NO_QUOTE = "explained_no_quote"
    EXPLAINED_CASH = "explained_cash"
    EXPLAINED_LOT = "explained_lot"

    #: 归不进任何一条。**必须告警**，且计入 G2 的"未解释差异"。
    UNEXPLAINED = "unexplained"

    @property
    def is_explained(self) -> bool:
        return self in (DiffReason.MATCH,) or self.value.startswith("explained_")


@dataclass(frozen=True)
class ExplanationContext:
    """解释差异所需的事实。全部**显式传入**，不在这里读配置或行情。"""

    price_cap: float | None = None
    allowed_boards: frozenset[str] | None = None
    board_of: Mapping[str, str] = field(default_factory=dict)
    last_price: Mapping[str, float] = field(default_factory=dict)
    st_codes: frozenset[str] = frozenset()
    #: 资金不足导致没买成的代码（由 planner 的 skipped 提供，这里只做分类）。
    cash_short_codes: frozenset[str] = frozenset()
    lot_short_codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ShadowPlan:
    """SHADOW 的产出。**只是计划**，字段里没有任何"已发送"的概念。"""

    decision_date: int
    target_holds: tuple[str, ...]
    would_be_orders: tuple[PlannedOrder, ...]
    skipped: tuple[SkippedItem, ...]
    equity: float = 0.0

    @property
    def buy_codes(self) -> tuple[str, ...]:
        """⚠️ 用**枚举**比较，不比较 ``.value`` 字符串。

        ``Side.BUY.value`` 是 ``"BUY"``（大写），写成 ``== "buy"`` 永远为假——
        后果是"所有买单都被判成没买"，于是每条差异都落到"没有行情"这个
        已解释原因上，报告全绿而实际什么都没对上。本轮真实踩过。
        """
        return tuple(o.code for o in self.would_be_orders if o.side is Side.BUY)

    @property
    def sell_codes(self) -> tuple[str, ...]:
        return tuple(o.code for o in self.would_be_orders if o.side is Side.SELL)


@dataclass(frozen=True)
class ShadowDiff:
    code: str
    reason: DiffReason
    detail: str = ""

    @property
    def blocks_g2(self) -> bool:
        """未解释的差异会挡 G2 的「连续 20 日无未解释差异」。"""
        return not self.reason.is_explained


def build_shadow_plan(ledger_record: Mapping[str, Any],
                      snapshot: AccountSnapshot,
                      quotes: Mapping[str, Quote],
                      *, target_weight: float,
                      price_limits: Mapping[str, tuple[float, float]] | None = None,
                      lot_size: int = 100) -> ShadowPlan:
    """由前瞻台账记录算出"本应下的单"。

    ⚠️ 参数里**没有** broker/adapter。这不是巧合：SHADOW 的"不发送"如果靠
    运行时分支保证，一次参数传错就会真的下单；靠类型与签名保证，则不可能。
    """
    targets = tuple(ledger_record.get("target_holds") or ())
    day = int(ledger_record.get("decision_date") or ledger_record.get("asof") or 0)
    if not targets:
        raise ValueError(
            "台账记录里没有 target_holds——SHADOW 没有目标就无从比对，"
            "不得用空目标当作『今天不用下单』")
    if day <= 0:
        raise ValueError(f"台账记录缺少有效决策日：{day}")

    result = plan_orders(PlanInput(
        decision=str(day), targets=targets, snapshot=snapshot, quotes=quotes,
        target_weight=target_weight, lot_size=lot_size,
        price_limits=price_limits or {},
    ))
    return ShadowPlan(decision_date=day, target_holds=targets,
                      would_be_orders=result.orders, skipped=result.skipped,
                      equity=result.equity)


def _explain_missing(code: str, ctx: ExplanationContext) -> tuple[DiffReason, str]:
    """某只台账目标没出现在计划里，归因。**顺序即优先级**。"""
    if code in ctx.st_codes:
        return DiffReason.EXPLAINED_ST, "ST/*ST：实盘剔除，前瞻主口径未剔"
    if ctx.allowed_boards is not None:
        board = ctx.board_of.get(code)
        if board is not None and board not in ctx.allowed_boards:
            return (DiffReason.EXPLAINED_BOARD,
                    f"板块 {board} 不在授权范围 {sorted(ctx.allowed_boards)}")
    px = ctx.last_price.get(code)
    if ctx.price_cap is not None and px is not None and px > ctx.price_cap:
        return (DiffReason.EXPLAINED_PRICE_CAP,
                f"最新价 {px} 超过价格上限 {ctx.price_cap}")
    if code in ctx.cash_short_codes:
        return DiffReason.EXPLAINED_CASH, "资金不足，排在前面的买单已用完现金"
    if code in ctx.lot_short_codes:
        return DiffReason.EXPLAINED_LOT, "不足一手，整手取整后为 0"
    if px is None:
        return DiffReason.EXPLAINED_NO_QUOTE, "没有可用行情，规划阶段被跳过"
    return (DiffReason.UNEXPLAINED,
            "台账要持有、规划却没有，且不属于任何已知口径差——必须人工核对")


def compare_with_ledger(plan: ShadowPlan, ledger_record: Mapping[str, Any],
                        ctx: ExplanationContext | None = None) -> tuple[ShadowDiff, ...]:
    """比对"台账目标"与"本应下的单"，给每条差异一个**事前定义好的**原因。"""
    ctx = ctx or ExplanationContext()
    targets = set(plan.target_holds)
    planned_buy = set(plan.buy_codes)

    out: list[ShadowDiff] = []

    # ---- 方向一：台账要买、计划里没有 ----
    for code in sorted(targets - planned_buy):
        reason, why = _explain_missing(code, ctx)
        out.append(ShadowDiff(code, reason, why))

    # ---- 方向二：计划要买、台账没有 ----
    for code in sorted(planned_buy - targets):
        out.append(ShadowDiff(code, DiffReason.PLANNED_NOT_IN_LEDGER,
                              "计划买入但台账目标里没有：可能是持仓差额导致的重建仓"))

    # ---- 股数口径：台账没记股数，只能核对"是否都在计划里" ----
    for o in plan.would_be_orders:
        if o.code in targets and o.shares <= 0:
            out.append(ShadowDiff(o.code, DiffReason.SHARES_DIFFER,
                                  f"规划股数 {o.shares} 非正"))

    if not out:
        out.append(ShadowDiff("", DiffReason.MATCH, "台账目标与计划完全一致"))
    return tuple(out)


@dataclass(frozen=True)
class ShadowReport:
    decision_date: int
    diffs: tuple[ShadowDiff, ...]
    plan: ShadowPlan

    @property
    def unexplained(self) -> tuple[ShadowDiff, ...]:
        return tuple(d for d in self.diffs if d.blocks_g2)

    @property
    def ok(self) -> bool:
        return not self.unexplained

    def summary(self) -> str:
        n = len(self.diffs)
        u = len(self.unexplained)
        base = (f"SHADOW {self.decision_date}：{n} 条差异，"
                f"未解释 {u} 条；本应下单 {len(self.plan.would_be_orders)} 笔（未发送）")
        if u:
            base += "；" + "；".join(f"{d.code}:{d.reason.value}" for d in self.unexplained)
        return base


def shadow_report(ledger_record: Mapping[str, Any], snapshot: AccountSnapshot,
                  quotes: Mapping[str, Quote], *,
                  target_weight: float,
                  ctx: ExplanationContext | None = None,
                  price_limits: Mapping[str, tuple[float, float]] | None = None
                  ) -> ShadowReport:
    """一次完整的 SHADOW：算计划 → 比对 → 报告。**不发送任何委托**。"""
    plan = build_shadow_plan(ledger_record, snapshot, quotes,
                             target_weight=target_weight,
                             price_limits=price_limits)
    return ShadowReport(decision_date=plan.decision_date,
                        diffs=compare_with_ledger(plan, ledger_record, ctx),
                        plan=plan)
