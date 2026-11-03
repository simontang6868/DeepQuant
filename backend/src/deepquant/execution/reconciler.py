"""日终对账：持仓、资金、成交、委托四类对象逐字段比对。

对账是本阶段唯一一个"错了就会一直错下去"的环节
---------------------------------------------
闸门写错，最坏是拒掉一笔该发的单；对账写错，是**账实不符却报通过**，
然后这个错误状态被当成下一轮的基线，一直错到期末。所以这里的原则是：
**宁可多报一条差异（人工看一眼），不可漏报一条**。

三条硬规矩
----------
1. ⭐⭐ **两个方向都要查**（2.5-b）。只遍历本地去券商侧找，就永远看不到
   「券商有、本地无」——手工交易、漏录回报都长这样（缺陷预登记 P3）。
   实现上每个对象都有 ``*_MISSING_LOCAL`` 与 ``*_MISSING_BROKER`` 两个方向。
2. ⭐ **差异落到字段与差值**（2.5-c）。``Difference.field`` 不允许为空，
   构造函数里断言。只报"不一致"的对账结果等于没报——人看到也没法处理。
3. ⭐ **两种容差方向不同**（2.5-d）：股数**必须完全相等**（一股都不能差，
   差一股就意味着有成交没记）；金额 ≤ 0.01 元视为舍入。
   金额比较用**整数分**而不是 ``abs(a-b) <= 0.01``：浮点下
   ``0.010000000000000009`` 会被判成"超出容差"，而它明明就是一分钱。

一个容易写反的地方：成交去重
--------------------------
``BrokerAdapter.get_fills`` **不去重**（回报可能重复到达，去重是调用方的职责，
见 2.1-c）。对账必须先按 ``fill_id`` 去重再比，否则一笔重复回报会被当成两笔
成交，反过来制造一条假差异。但去重**不能静默**：重复本身是异常，要单独报出来
（``duplicate_fill_ids``），否则"回报重复"这个故障就永远没人知道。

本模块**不做任何写入**，也不改任何状态。``reconcile`` 是纯函数，便于重放与
单测；把结论写进闸门的动作在 ``apply_reconcile_to_gate_state``，且它只改
``last_reconciliation_ok`` 一个字段——并且**不会自动改回 True**。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from deepquant.broker.models import (
    BrokerOrder,
    Cash,
    Fill,
    Position,
)
from deepquant.execution.state_machine import ReadOnlyBroker

__all__ = [
    "DiffKind",
    "Difference",
    "ReconcileResult",
    "MONEY_TOL_CENTS",
    "reconcile",
    "reconcile_with_broker",
    "apply_reconcile_to_gate_state",
]


#: 金额容差（单位：分）。「≤ 0.01 元视为舍入」⇔ 相差不超过 1 分。
MONEY_TOL_CENTS = 1


class DiffKind(str, Enum):
    """差异类型。**每个对象都有 MISSING_LOCAL / MISSING_BROKER 两侧。**"""

    POSITION_MISSING_LOCAL = "position_missing_local"
    POSITION_MISSING_BROKER = "position_missing_broker"
    POSITION_QUANTITY = "position_quantity"
    POSITION_AVAILABLE = "position_available"

    CASH_AVAILABLE = "cash_available"
    CASH_FROZEN = "cash_frozen"
    CASH_TOTAL = "cash_total"

    ORDER_MISSING_LOCAL = "order_missing_local"
    ORDER_MISSING_BROKER = "order_missing_broker"
    ORDER_STATUS = "order_status"
    ORDER_FILLED_SHARES = "order_filled_shares"

    FILL_MISSING_LOCAL = "fill_missing_local"
    FILL_MISSING_BROKER = "fill_missing_broker"
    FILL_SHARES = "fill_shares"
    FILL_PRICE = "fill_price"


@dataclass(frozen=True)
class Difference:
    """一条差异。**必须能定位到字段与数值**（2.5-c）。"""

    object: str                     # positions / cash / orders / fills
    kind: DiffKind
    code: str                       # 标的代码；资金类为 ""
    field: str                      # 精确字段名，不得为空
    local: float | int | str | None
    broker: float | int | str | None
    delta: float | int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError(
                "Difference.field 不得为空——只报「不一致」等于没报（2.5-c）")
        if not isinstance(self.kind, DiffKind):
            raise TypeError(f"kind 必须是 DiffKind，收到 {type(self.kind).__name__}")

    def __str__(self) -> str:
        d = "" if self.delta is None else f" Δ={self.delta}"
        tail = f"（{self.detail}）" if self.detail else ""
        return (f"[{self.object}] {self.kind.value} {self.code or '-'}."
                f"{self.field}: 本地={self.local!r} 券商={self.broker!r}{d}{tail}")


@dataclass(frozen=True)
class ReconcileResult:
    """对账结论。``ok`` 为真当且仅当**一条差异都没有**。"""

    diffs: tuple[Difference, ...] = ()
    duplicate_fill_ids: tuple[str, ...] = ()
    ts: int = 0

    @property
    def ok(self) -> bool:
        return not self.diffs

    def by_object(self) -> dict[str, tuple[Difference, ...]]:
        out: dict[str, list[Difference]] = {}
        for d in self.diffs:
            out.setdefault(d.object, []).append(d)
        return {k: tuple(v) for k, v in out.items()}

    def summary(self) -> str:
        if self.ok:
            base = "对账通过（四类对象全部一致）"
        else:
            counts = ", ".join(f"{k}={len(v)}" for k, v in sorted(self.by_object().items()))
            base = f"对账发现 {len(self.diffs)} 条差异（{counts}）"
        if self.duplicate_fill_ids:
            base += f"；另有重复回报 {len(self.duplicate_fill_ids)} 个 fill_id"
        return base


# --------------------------------------------------------------------------- 工具


def _cents(x: float) -> int:
    """金额转整数分。

    用整数分而不是 ``abs(a-b) <= 0.01``：后者在浮点下会把 1 分钱判成超差
    （``0.1 + 0.2 - 0.3 = 5.55e-17``，而 ``100.01 - 100.00 = 0.010000000000000009``，
    严格大于 0.01）。金额本身是分位量，先量化再比是唯一稳妥的做法。
    """
    return int(round(float(x) * 100))


def _money_diff(obj: str, kind: DiffKind, field: str,
                local: float, broker: float) -> Difference | None:
    dl, db = _cents(local), _cents(broker)
    if abs(db - dl) <= MONEY_TOL_CENTS:
        return None
    return Difference(
        object=obj, kind=kind, code="", field=field,
        local=local, broker=broker, delta=(db - dl) / 100.0,
        detail=f"相差 {abs(db - dl)} 分，超出舍入容差")


# --------------------------------------------------------------------------- 四类对象


def _diff_positions(local: Iterable[Position],
                    broker: Iterable[Position]) -> list[Difference]:
    lm: dict[str, Position] = {p.code: p for p in local}
    bm: dict[str, Position] = {p.code: p for p in broker}
    out: list[Difference] = []

    for code in sorted(set(bm) - set(lm)):
        b = bm[code]
        out.append(Difference(
            "positions", DiffKind.POSITION_MISSING_LOCAL, code, "quantity",
            None, b.quantity, b.quantity,
            "券商有、本地无：可能是手工交易或漏录成交（P3 方向）"))
    for code in sorted(set(lm) - set(bm)):
        a = lm[code]
        out.append(Difference(
            "positions", DiffKind.POSITION_MISSING_BROKER, code, "quantity",
            a.quantity, None, -a.quantity,
            "本地有、券商无：不得自动删除，须人工确认"))

    for code in sorted(set(lm) & set(bm)):
        a, b = lm[code], bm[code]
        # ⚠️ 股数**不允许容差**：差一股就是有一笔成交没记上。
        if a.quantity != b.quantity:
            out.append(Difference(
                "positions", DiffKind.POSITION_QUANTITY, code, "quantity",
                a.quantity, b.quantity, b.quantity - a.quantity))
        if a.available != b.available:
            out.append(Difference(
                "positions", DiffKind.POSITION_AVAILABLE, code, "available",
                a.available, b.available, b.available - a.available,
                "可卖数不一致：T+1 解冻判断与券商不同"))
    return out


def _diff_cash(local: Cash, broker: Cash) -> list[Difference]:
    out: list[Difference] = []
    # ⚠️ 三个字段**都要比**。只比 total 会漏掉"冻结没释放"这类：总钱数对、
    #    可用不对，实盘就是买不了——而 total 相等让它看起来一切正常。
    for field, kind in (("available", DiffKind.CASH_AVAILABLE),
                        ("frozen", DiffKind.CASH_FROZEN),
                        ("total", DiffKind.CASH_TOTAL)):
        d = _money_diff("cash", kind, field,
                        float(getattr(local, field)), float(getattr(broker, field)))
        if d is not None:
            out.append(d)
    return out


def _diff_orders(local: Iterable[BrokerOrder],
                 broker: Iterable[BrokerOrder]) -> list[Difference]:
    lm = {o.client_order_id: o for o in local}
    bm = {o.client_order_id: o for o in broker}
    out: list[Difference] = []

    for coid in sorted(set(bm) - set(lm)):
        b = bm[coid]
        out.append(Difference(
            "orders", DiffKind.ORDER_MISSING_LOCAL, b.code, "status",
            None, b.status.value, None,
            f"券商有、本地无（{coid}）：可能是手工/其他终端的委托"))
    for coid in sorted(set(lm) - set(bm)):
        a = lm[coid]
        out.append(Difference(
            "orders", DiffKind.ORDER_MISSING_BROKER, a.code, "status",
            a.status.value, None, None,
            f"本地有、券商无（{coid}）：不得自动删，须人工确认"))

    for coid in sorted(set(lm) & set(bm)):
        a, b = lm[coid], bm[coid]
        if a.status is not b.status:
            out.append(Difference(
                "orders", DiffKind.ORDER_STATUS, a.code, "status",
                a.status.value, b.status.value, None, coid))
        if a.filled_shares != b.filled_shares:
            out.append(Difference(
                "orders", DiffKind.ORDER_FILLED_SHARES, a.code, "filled_shares",
                a.filled_shares, b.filled_shares,
                b.filled_shares - a.filled_shares, coid))
    return out


def _dedup_fills(fills: Sequence[Fill]) -> tuple[dict[str, Fill], tuple[str, ...]]:
    """按 ``fill_id`` 去重。返回 ``(唯一成交, 重复出现的 id)``。

    ⚠️ 顺序敏感：同一 ``fill_id`` 若两份内容不同，说明**不是重复回报而是
    撞 id**，那种情况必须留证据——这里保留第一份，差异由调用方从重复列表察觉。
    """
    seen: dict[str, Fill] = {}
    dups: list[str] = []
    for f in fills:
        if f.fill_id in seen:
            if f.fill_id not in dups:
                dups.append(f.fill_id)
            continue
        seen[f.fill_id] = f
    return seen, tuple(sorted(dups))


def _diff_fills(local: Sequence[Fill], broker: Sequence[Fill]
                ) -> tuple[list[Difference], tuple[str, ...]]:
    lm, ldup = _dedup_fills(local)
    bm, bdup = _dedup_fills(broker)
    out: list[Difference] = []

    for fid in sorted(set(bm) - set(lm)):
        b = bm[fid]
        out.append(Difference(
            "fills", DiffKind.FILL_MISSING_LOCAL, b.code, "shares",
            None, b.shares, b.shares,
            f"券商有、本地无（fill_id={fid}）：漏录回报"))
    for fid in sorted(set(lm) - set(bm)):
        a = lm[fid]
        out.append(Difference(
            "fills", DiffKind.FILL_MISSING_BROKER, a.code, "shares",
            a.shares, None, -a.shares,
            f"本地有、券商无（fill_id={fid}）：本地可能记了不存在的成交"))

    for fid in sorted(set(lm) & set(bm)):
        a, b = lm[fid], bm[fid]
        if a.shares != b.shares:
            out.append(Difference(
                "fills", DiffKind.FILL_SHARES, a.code, "shares",
                a.shares, b.shares, b.shares - a.shares, fid))
        # 成交价用金额容差：券商可能给 6 位小数，差 1 分以内是舍入。
        if abs(_cents(a.price) - _cents(b.price)) > MONEY_TOL_CENTS:
            out.append(Difference(
                "fills", DiffKind.FILL_PRICE, a.code, "price",
                a.price, b.price, round(b.price - a.price, 6), fid))
    return out, tuple(sorted(set(ldup) | set(bdup)))


# --------------------------------------------------------------------------- 主入口


def reconcile(*, local_positions: Sequence[Position],
              broker_positions: Sequence[Position],
              local_cash: Cash, broker_cash: Cash,
              local_orders: Sequence[BrokerOrder] = (),
              broker_orders: Sequence[BrokerOrder] = (),
              local_fills: Sequence[Fill] = (),
              broker_fills: Sequence[Fill] = (),
              ts: int = 0) -> ReconcileResult:
    """四类对象全量比对。**纯函数**：不读文件、不调券商、不改状态。"""
    diffs: list[Difference] = []
    diffs += _diff_positions(local_positions, broker_positions)
    diffs += _diff_cash(local_cash, broker_cash)
    diffs += _diff_orders(local_orders, broker_orders)
    fill_diffs, dups = _diff_fills(local_fills, broker_fills)
    diffs += fill_diffs
    return ReconcileResult(diffs=tuple(diffs), duplicate_fill_ids=dups, ts=ts)


def reconcile_with_broker(adapter, *, local_positions: Sequence[Position],
                          local_cash: Cash,
                          local_orders: Sequence[BrokerOrder] = (),
                          local_fills: Sequence[Fill] = (),
                          ts: int = 0) -> ReconcileResult:
    """从券商拉现状再比对。

    ⭐ 复用 ``ReadOnlyBroker``：对账**也**不该下单。对账跑在下单之前，如果它
    顺手补了一笔单，那就是"对账改变了被对账的对象"——这个错一旦发生，
    多轮之后没人说得清账是怎么平的。
    """
    ro = ReadOnlyBroker(adapter)
    positions = tuple(ro.get_positions())
    cash = ro.get_cash()
    orders = tuple(ro.get_orders())
    fills = tuple(ro.get_fills())
    return reconcile(
        local_positions=local_positions, broker_positions=positions,
        local_cash=local_cash, broker_cash=cash,
        local_orders=local_orders, broker_orders=orders,
        local_fills=local_fills, broker_fills=fills, ts=ts,
    )


def apply_reconcile_to_gate_state(result: ReconcileResult, state,
                                  *, clear: bool = False):
    """把对账结论写进 ``GateState``。

    ⭐⭐ 差异 ⇒ ``last_reconciliation_ok = False``（触发拒单）。
    但**成功不会自动改回 True**：一条差异被"下一次对账恰好没复现"而自动清除，
    就等于系统自己宣布自己没问题。必须人工核对后显式传 ``clear=True``。

    只改这一个字段：峰值、熔断、日笔数都属于当日事实，对账无权重置它们
    （与 2.4 的 ``apply_rebuild_to_gate_state`` 同一原则）。
    """
    if not result.ok:
        state.last_reconciliation_ok = False
    elif clear:
        state.last_reconciliation_ok = True
    return state
