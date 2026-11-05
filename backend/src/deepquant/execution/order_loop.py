"""发单循环的决策层：给定「现在几点 + 计划 + 已有委托」，决定这一轮做什么。

它**不做**的事（边界必须清楚）
--------------------------
- 不调券商：只产出 :class:`LoopAction`，真正的 ``place_order`` 由 Phase 3 的
  执行器在 ``LIVE_ENABLED`` 打开后调用。**本项目当前 ``LIVE_ENABLED=false``，
  本模块的任何输出都不会变成真实委托。**
- 不算风控：能不能买由 :class:`~deepquant.execution.risk_gate.RiskGate` 判。
- 不算成交：成交与否以券商回报为准（:mod:`deepquant.execution.state_machine`）。

它**做**的事
------------
1. 到点才发单（09:35 后、避开午休与收盘竞价）——验收 2.2-c；
2. 10:30 / 14:30 两个时点最多改价 2 次，超出**硬失败**——验收 2.2-c / 2.2-d；
3. 收盘未成交转顺延，顺延以交易日计、10 天到顶放弃——验收 2.2-c；
4. 每次发单/改价先过限速器；被限速时**保留动作到下一轮**，不丢单。

三个刻意的设计
--------------
1. **一轮 tick 是纯函数**（除了就地推进 ``trackers``）。同样的输入永远得到同样的动作，
   这样"为什么这轮没发单"可以事后逐条复算——不需要看日志猜。
2. **限速被拒不是失败**。返回 ``HOLD`` 并附原因，下一轮重试。把"被限速"记成
   ``FAILED`` 会把一次正常的节流变成台账上的一次事故。
3. **重定价只朝有利方向**。买单只上移、卖单只下移；「让 0.3%」是相对最新价再让
   0.3%，且有涨跌停夹逼。反向改价等于自愿扩大损失，不给这个口子。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from deepquant.broker.models import Quote
from deepquant.execution.planner import PlannedOrder
from deepquant.execution.rate_limit import RateLimiter
from deepquant.execution.session import (
    DEFAULT_TIMES,
    CloseOutcome,
    OrderTracker,
    SessionTimes,
    is_trading_time,
)
from deepquant.market.rules import Side

__all__ = [
    "Action",
    "LoopAction",
    "LoopState",
    "PRICE_DEVIATION",
    "reprice_price",
    "tick",
]

#: 相对最新价的让步（验收 2.2-c：对手价最多让 0.3%）。
#: 没有盘口数据，用最新价近似"对手价"——这一点必须在台账里写明，不能默认读者知道。
PRICE_DEVIATION = 0.003


class Action(str, Enum):
    PLACE = "place"        #: 首次发单
    REPRICE = "reprice"    #: 撤旧单 + 报新价（同一 client_order_id）
    CANCEL = "cancel"      #: 撤单（顺延/放弃前清理挂单）
    ABANDON = "abandon"    #: 顺延期满，放弃并记终态
    HOLD = "hold"          #: 什么都不做（含"被限速，下轮再试"）


@dataclass(frozen=True)
class LoopAction:
    """一轮 tick 产出的一条动作。"""

    kind: Action
    client_order_id: str
    code: str = ""
    side: Side | None = None
    shares: int = 0
    limit_price: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind in (Action.PLACE, Action.REPRICE):
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError(f"{self.kind.value} 必须有正的 limit_price")
            if self.shares <= 0:
                raise ValueError(f"{self.kind.value} 必须有正的 shares")


@dataclass
class LoopState:
    """跨轮次的持久状态。``trackers`` 按 ``client_order_id`` 索引。"""

    trackers: dict[str, OrderTracker] = field(default_factory=dict)
    limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(rate_per_second=2.0, burst=2))
    #: 上一轮结束时的阶段，仅用于说明性日志（不参与判定）。
    last_phase: str | None = None

    def tracker(self, order: PlannedOrder) -> OrderTracker:
        """取或建 tracker。已存在则不重建（否则顺延计数会归零）。"""
        t = self.trackers.get(order.client_order_id)
        if t is None:
            t = OrderTracker(order.client_order_id, decision_day=_decision_of(order))
            self.trackers[order.client_order_id] = t
        return t

    def to_dict(self) -> dict:
        return {
            "trackers": {k: v.to_dict() for k, v in self.trackers.items()},
            "limiter": self.limiter.to_dict(),
            "last_phase": self.last_phase,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "LoopState":
        return cls(
            trackers={k: OrderTracker.from_dict(v)
                      for k, v in (d.get("trackers") or {}).items()},
            limiter=RateLimiter.from_dict(d.get("limiter") or
                                          {"rate_per_second": 2.0, "burst": 2}),
            last_phase=d.get("last_phase"),
        )


def _decision_of(order: PlannedOrder) -> str:
    """从 ``client_order_id`` 里取决策日。

    真实格式由 :func:`~deepquant.execution.planner.make_client_order_id` 决定：
    ``20261030-600990-B-200``（重定价后为 ``…-r1``）——**决策日在第 0 段**。

    ⚠️ 本函数第一版按想象的 ``DQ-YYYYMMDD-…`` 写成了 ``parts[1]``，
    对着真实 id 会取到股票代码并抛错。这类"凭想象定协议"的错误在
    本项目已出现过多次（`side.value=="buy"`、凭空写测试名），所以这里
    加了一条直接调用 ``make_client_order_id`` 的回归测试。
    """
    parts = order.client_order_id.split("-")
    head = parts[0] if parts else ""
    if not (head.isdigit() and len(head) == 8):
        raise ValueError(
            f"client_order_id {order.client_order_id!r} 首段不是 YYYYMMDD 决策日，"
            f"无法建立顺延账（planner.make_client_order_id 生成的 id 必以决策日开头）")
    return head


def reprice_price(order: PlannedOrder, quote: Quote | None,
                  price_limits: Mapping[str, tuple[float, float]] | None = None,
                  deviation: float = PRICE_DEVIATION) -> float | None:
    """算出重定价后的限价。

    规则（只朝有利方向）：
    - **买**：``max(原限价, min(最新价 × (1+0.003), 涨停价))`` —— 只会更高，不会更低；
    - **卖**：``min(原限价, max(最新价 × (1−0.003), 跌停价))`` —— 只会更低，不会更高。

    返回 ``None`` 表示**不该改价**（没有报价，或改了也不会改善）。
    返回 ``None`` 是正常结果，不是错误——调用方应据此返回 ``HOLD`` 并说明原因。
    """
    if quote is None or not (quote.last > 0):
        return None
    if quote.stale:
        # ⚠️ 陈旧报价（停牌/数据源滞后）不得用于改价：
        # 停牌股的 last 是停牌前价格，按它改价等于"用昨天的价追今天的市"。
        # 判定由数据源给出（models.Quote.stale），这里只服从，不重新猜。
        return None
    lo, hi = (price_limits or {}).get(order.code, (0.0, float("inf")))
    if order.side is Side.BUY:
        raw = quote.last * (1.0 + deviation)
        capped = min(raw, hi) if hi else raw
        new = max(order.limit_price, capped)
        # 涨跌停/最小变动会让 new 与旧价相同；相同就没有改价的意义
        improved = new > order.limit_price + 1e-12
    else:
        raw = quote.last * (1.0 - deviation)
        floored = max(raw, lo) if lo > 0 else raw
        new = min(order.limit_price, floored)
        improved = new < order.limit_price - 1e-12
    if not improved:
        return None
    if new <= 0:
        return None
    return round(new, 2)


def tick(
    *,
    now: dt.datetime,
    plan: Iterable[PlannedOrder],
    state: LoopState,
    quotes: Mapping[str, Quote] | None = None,
    price_limits: Mapping[str, tuple[float, float]] | None = None,
    times: SessionTimes = DEFAULT_TIMES,
    filled: Iterable[str] = (),
    at_close: bool = False,
    day: str | None = None,
    trading_days: "list[str] | tuple[str, ...] | None" = None,
    deviation: float = PRICE_DEVIATION,
) -> list[LoopAction]:
    """推进一轮。

    参数
    ----
    now
        本轮时刻（显式传入，不读系统时钟）。
    plan
        待执行计划；**顺序即优先级**（planner 已保证卖单在前）。
    filled
        本轮已确认（完全）成交的 ``client_order_id`` 集合。
    at_close
        ``True`` 表示这是当日收盘后的清账轮：未成交的转 ``CARRY``/``ABANDON``。
    trading_days
        ``at_close=True`` 且需要顺延时**必须给**；不给就只报 ``CARRY`` 不推进日期
        （失败关闭：不猜次一交易日）。

    返回按 ``plan`` 顺序排列的动作列表。

    ⚠️ **调用方必须在一个时点内多轮调用，直到没有 PLACE/REPRICE 动作为止。**
    原因：一轮能发出的笔数受限速器的 ``burst`` 与当前令牌数限制；本轮拿不到令牌的
    订单返回 ``HOLD``（"限速：下轮重试"），**不会自动在之后补发**。
    2026-11-05 的纸面演练实测：``burst=2`` 时一轮 4 笔只能发出 2 笔，
    而调用方若只在 09:35 调一次，剩下 2 笔会被拖到下一次 tick（可能是 10:30 甚至 14:40），
    于是"当天该建的仓"变成了"下午才建仓"——价格和成交率都变了，账面上却看不出异常。

    推荐调用形态（真实执行器应每 1–3 秒 tick 一次，直到 ``drained``）：

    >>> # 伪代码
    >>> for i in range(max_rounds):
    ...     acts = tick(now=t0 + i * 1s, ...)
    ...     if not any(a.kind in (Action.PLACE, Action.REPRICE) for a in acts):
    ...         break
    """
    orders = list(plan)
    filled_set = {str(x) for x in filled}
    acts: list[LoopAction] = []

    # ---- 收盘清账轮：只处理未成交的，不发单 ----
    if at_close:
        if day is None:
            raise ValueError("at_close=True 时必须给 day（YYYYMMDD）")
        for o in orders:
            tr = state.tracker(o)
            if o.client_order_id in filled_set:
                tr.on_close(day, filled=True)
                acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                       o.shares, reason="已成交，收盘清账"))
                continue
            if tr.placed_at is None:
                # 从未发出（例如全天都在限速/无报价）——不计顺延，如实说明
                acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                       o.shares, reason="全天未发出，不计顺延"))
                continue
            outcome = tr.on_close(day, filled=False)
            if outcome is CloseOutcome.ABANDONED:
                acts.append(LoopAction(Action.ABANDON, o.client_order_id, o.code, o.side,
                                       o.shares,
                                       reason=f"顺延 {tr.carry_days} 个交易日仍未成交，放弃"))
                continue
            if trading_days is not None:
                nxt = _next_day_safe(day, trading_days)
                if nxt is not None:
                    tr.carry_to(nxt, trading_days)
                    acts.append(LoopAction(Action.CANCEL, o.client_order_id, o.code, o.side,
                                           o.shares,
                                           reason=f"收盘未成交→撤单顺延至 {nxt}"
                                                  f"（第 {tr.carry_days} 天）"))
                    continue
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares,
                                   reason=f"收盘未成交（顺延第 {tr.carry_days} 天）"
                                          f"但未推进日期：{'无交易日历' if trading_days is None else '日历到头'}"))
        state.last_phase = "close"
        return acts

    # ---- 盘中的发单/改价轮 ----
    if not is_trading_time(now, times):
        for o in orders:
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares, reason=f"非交易时段（{now:%H:%M}）"))
        state.last_phase = "off_hours"
        return acts

    for o in orders:
        tr = state.tracker(o)
        if o.client_order_id in filled_set:
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares, reason="已成交"))
            continue
        if tr.exhausted:
            acts.append(LoopAction(Action.ABANDON, o.client_order_id, o.code, o.side,
                                   o.shares, reason="顺延期已满"))
            continue

        # 1) 未发过 → 发单
        if tr.placed_at is None:
            if not _take(state.limiter, now):
                acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                       o.shares, reason="限速：本轮未获令牌，下轮重试"))
                continue
            tr.place(now, times)
            acts.append(LoopAction(Action.PLACE, o.client_order_id, o.code, o.side,
                                   o.shares, o.limit_price, reason=o.reason or "首次发单"))
            continue

        # 2) 已发过 → 看是否到了可改价的时点
        due = tr.due_slots(now, times)
        if not due:
            if tr.reprice_remaining == 0:
                why = "重定价次数已用尽（2.2-d 上限 2），保持挂单等成交"
            else:
                why = f"未到重定价时点（已到达 {len(due)} 个可用 slot）"
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares, reason=why))
            continue

        newp = reprice_price(o, quotes.get(o.code) if quotes else None,
                             price_limits, deviation)
        if newp is None:
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares,
                                   reason="无报价或改价无改善（不朝不利方向改）"))
            continue
        if not _take(state.limiter, now):
            acts.append(LoopAction(Action.HOLD, o.client_order_id, o.code, o.side,
                                   o.shares, reason="限速：改价未获令牌，下轮重试"))
            continue
        tr.reprice(now, times)
        acts.append(LoopAction(Action.REPRICE, o.client_order_id, o.code, o.side,
                               o.shares, newp,
                               reason=f"第 {tr.reprice_count} 次改价 "
                                      f"{o.limit_price:.2f} → {newp:.2f}"))

    state.last_phase = "intraday"
    return acts


def _take(limiter: RateLimiter, now: dt.datetime) -> bool:
    """取一个令牌。用 epoch 秒作逻辑时间，便于与限速器的可持久化状态对齐。"""
    return limiter.try_acquire(now.timestamp())


def _next_day_safe(day: str, trading_days: "list[str] | tuple[str, ...]") -> str | None:
    """安全取次一交易日；取不到返回 ``None`` 而不是抛错（收盘清账不应因日历问题整轮失败）。"""
    try:
        from deepquant.execution.session import next_trading_day
        return next_trading_day(day, trading_days)
    except (KeyError, IndexError):
        return None
