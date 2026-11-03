"""现金占比归因（L3q）：把「未投资比例」拆成互不相同的机制。

为什么必须拆
------------
L3p 报告里只有一句 ``avg_cash_ratio_median = 11.71%``，随后就把这个数当作「稀释
程度」的代理写进对照臂注解。但同一个数字至少混了六个来源，处置方式完全不同：

==================  ==========================================  ======================
来源                含义                                        该怎么办
==================  ==========================================  ======================
``cap``             目标权重和 < 1（单标的上限压住等权）         调模型参数
``rounding``        Δ金额折算股数后的取整残差                   资金门槛 / 股价
``dead``            行情已终止的标的仍被记为持有                 修状态重建（数据缺陷）
``not_listed``      信号日早于该标的行情起点                     修标的池（时点成分）
``suspended``       当日停牌（区间内缺行）                       现实约束，无法避免
``insufficient``    先卖后买后资金仍不够（部分成交）             现实约束 + 换手
``fees``            交易费用                                    成本口径
==================  ==========================================  ======================

把「死票占权重」当成「信号没铺满仓位」引用，会得出反向结论：前者是缺陷，后者是
设计。所以本模块只做一件事——**把现金归因到可命名的机制**，且各分量之和必须与
引擎记的现金逐笔对得上（不是"差不多"）。

恒等式（本模块全部正确性都压在这条上）
--------------------------------------
调仓日 ``t``，``q_pre`` 为调仓前持仓、``px`` 为执行参考价：

    cash_after = cash_before + Σ卖出gross − Σ买入gross − fees
               = equity_open − MV_after − fees
               = equity_open × (1 − Σw)                        ← 结构缺口
                 + Σ_s (equity_open×w_s − q_after,s×px_s)      ← 逐标的缺口
                 − fees

第二项再按「为什么没买够」分类。实现里对每次调仓核两个残差：

- ``residual_identity``：``cash_after`` 与分量之和的差。**不为 0 说明漏了标的或
  漏了分量**（例如只遍历 target 而漏掉"有持仓但无目标"的清仓腿）。
- ``residual_ledger``：``cash_after`` 与成交回放现金的差。**不为 0 说明本模块对
  执行价/权益的理解与引擎不同**（例如缺价时的 fallback 口径不一致）。

两个都超容差就抛错，不出数。

逐日分摊
--------
调仓日之间现金只会因「顺延卖出回款」变化。逐日归因的口径是：分量取最近一次调仓
日的**实测值原样保留**，区间内的现金变动单独计入 ``other``。这样
``Σ分量 × 天数`` 逐日等于 ``cash/equity``，可加性不被分摊方式破坏。

不做的事
--------
- 不改 :mod:`deepquant.backtest.portfolio`（引擎是口径的唯一来源，本模块只读它的
  成交与状态）；
- 不判断策略好坏，不产生放行结论；
- 不把「诊断臂修复后收益更高」当作策略更强的证据——修复的是数据缺陷，不是模型。
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from deepquant.backtest.costs import round_money
from deepquant.backtest.portfolio import (
    PortfolioBacktestResult,
    TargetPortfolio,
    _valid_price,
)
from deepquant.market.rules import Side

# --------------------------------------------------------------------------- #
# 一、分量命名
# --------------------------------------------------------------------------- #
#: 目标权重和 < 1（只有单标的上限会压住它）
CASH_CAP = "cap"
#: 取整残差（``lots`` 口径按手、``weights`` 口径按 1 股）
CASH_ROUNDING = "rounding"
#: 行情已终止，仍被记为持有
CASH_DEAD = "dead"
#: 信号日早于该标的行情起点（尚未上市 / 未进池）
CASH_NOT_LISTED = "not_listed"
#: 当日停牌（标的行情区间内缺这一行）
CASH_SUSPENDED = "suspended"
#: 先卖后买之后资金仍不够（部分成交），且没有被记为拒单
CASH_INSUFFICIENT = "insufficient"
#: 费用
CASH_FEES = "fees"
#: 卖不出导致的超配（持仓高于目标，是**负**缺口，会把现金压向反方向）
CASH_OVERWEIGHT = "overweight"
#: 窗口首日 → 首个建仓日之间的空仓（折设计的固有成本，一天约 1/126）
CASH_PRESTART = "prestart"
#: 期间现金变动（顺延卖出回款）与未能归因的残余
CASH_OTHER = "other"

#: 无价标的的三种成因（都表现为「当日没有可用开盘价」）
UNPRICED_COMPONENTS: tuple[str, ...] = (CASH_DEAD, CASH_NOT_LISTED, CASH_SUSPENDED)

BASE_COMPONENTS: tuple[str, ...] = (
    CASH_CAP,
    CASH_ROUNDING,
    CASH_DEAD,
    CASH_NOT_LISTED,
    CASH_SUSPENDED,
    CASH_INSUFFICIENT,
    CASH_FEES,
    CASH_OVERWEIGHT,
    CASH_PRESTART,
    CASH_OTHER,
)

COMPONENT_LABELS: dict[str, str] = {
    CASH_CAP: "结构缺口：目标权重和 < 1（单标的上限）",
    CASH_ROUNDING: "取整残差：折算股数后的零头",
    CASH_DEAD: "行情已终止的标的仍被记为持有",
    CASH_NOT_LISTED: "尚未上市（信号日早于行情起点）",
    CASH_SUSPENDED: "当日停牌",
    CASH_INSUFFICIENT: "先卖后买后资金不足（部分成交）",
    CASH_FEES: "交易费用",
    CASH_OVERWEIGHT: "超配返还（卖不出导致持仓高于目标）",
    CASH_PRESTART: "窗口首日到建仓日之间的空仓（折设计的固有成本）",
    CASH_OTHER: "期间现金变动与其他",
}

#: 金额容差：半分钱。低于它的差额是浮点噪声，不是机制。
CENT = 0.005

#: 代数恒等式残差容差（元）
IDENTITY_TOLERANCE = 0.01
#: 与成交回放现金的差额容差（元）：允许逐笔 round_money 的累积误差
LEDGER_TOLERANCE = 0.50


class CashAuditError(RuntimeError):
    """归因结果不自洽（分量加不回现金、或与成交回放对不上）。"""


# --------------------------------------------------------------------------- #
# 二、无价标的的成因分类
# --------------------------------------------------------------------------- #
def classify_unpriced(index: pd.DatetimeIndex, day: pd.Timestamp) -> str:
    """按行情区间判断「当日无价」的成因。

    三者必须分开：「停牌」是现实约束，「行情已终止」是数据缺陷，「未上市」是标的
    池用了当日快照（时点成分错误）。混成一个 ``no_price`` 会把两个缺陷说成现实约束。

    Args:
        index: 该标的的行情日期（升序）。
        day: 判定日。

    Returns:
        :data:`CASH_DEAD` / :data:`CASH_NOT_LISTED` / :data:`CASH_SUSPENDED`。

    Raises:
        CashAuditError: ``index`` 为空——空行情不该走到这里，静默归类会掩盖取数错误。
    """
    if len(index) == 0:
        raise CashAuditError("标的行情为空，无法判断无价成因（取数或池文件有问题）")
    if day < index[0]:
        return CASH_NOT_LISTED
    if day > index[-1]:
        return CASH_DEAD
    return CASH_SUSPENDED


# --------------------------------------------------------------------------- #
# 三、持仓回放
# --------------------------------------------------------------------------- #
@dataclass
class Ledger:
    """按成交记录重建的逐日持仓与现金。"""

    #: 逐日收盘后现金
    cash_daily: dict[pd.Timestamp, float]
    #: 逐日收盘后持仓
    qty_daily: dict[pd.Timestamp, dict[str, int]]
    #: 回放现金与引擎记账的最大绝对差（应为 0）
    max_cash_gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "replayed_days": len(self.cash_daily),
            "max_cash_gap_vs_engine": self.max_cash_gap,
        }


def replay_ledger(
    result: PortfolioBacktestResult,
    *,
    symbols: Sequence[str],
    initial_cash: float,
) -> Ledger:
    """只用成交记录重建逐日持仓与现金，并与引擎状态逐日核对。

    为什么要重建：``PortfolioState`` 只有 ``cash`` 与 ``market_value``，没有持股数；
    没有持股数就算不出「调仓前的持仓市值」，也就无法把缺口归因到具体标的。

    重建后必须与引擎记的现金**逐日逐分**一致，否则重建本身错了，后面的归因全部
    作废——所以这里宁可报错也不降级。
    """
    fills_by_date: dict[pd.Timestamp, list[Any]] = defaultdict(list)
    for fill in result.fills:
        fills_by_date[fill.date].append(fill)

    qty: dict[str, int] = {s: 0 for s in symbols}
    cash = float(initial_cash)
    cash_daily: dict[pd.Timestamp, float] = {}
    qty_daily: dict[pd.Timestamp, dict[str, int]] = {}
    max_gap = 0.0
    engine_cash = {state.date: state.cash for state in result.states}

    for day in [state.date for state in result.states]:
        for fill in sorted(fills_by_date.get(day, []), key=lambda f: f.fill_id):
            if fill.side == Side.BUY:
                cash = round_money(cash - fill.gross - fill.fees_total)
                qty[fill.symbol] = qty.get(fill.symbol, 0) + fill.quantity
            else:
                cash = round_money(cash + fill.gross - fill.fees_total)
                qty[fill.symbol] = qty.get(fill.symbol, 0) - fill.quantity
        cash_daily[day] = cash
        qty_daily[day] = dict(qty)
        expected = engine_cash.get(day)
        if expected is not None:
            max_gap = max(max_gap, abs(cash - expected))

    return Ledger(cash_daily=cash_daily, qty_daily=qty_daily, max_cash_gap=max_gap)


# --------------------------------------------------------------------------- #
# 四、单次调仓的归因
# --------------------------------------------------------------------------- #
@dataclass
class RebalanceAttribution:
    """一个调仓执行日的缺口分解（单位：元）。"""

    date: pd.Timestamp
    equity_open: float
    target_weight_sum: float
    components: dict[str, float]
    #: 目标里「当日无开盘价」的权重之和（数据结构缺陷的规模指标）
    unpriced_weight: float
    n_targets: int
    cash_after: float
    #: 代数恒等式残差：cash_after − Σ分量（应为 0，不为 0 就是漏了标的/分量）
    residual_identity: float
    #: 与成交回放现金的差额（应为 0，不为 0 说明对执行价的理解与引擎不同）
    residual_ledger: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": str(self.date.date()),
            "equity_open": round(self.equity_open, 2),
            "target_weight_sum": round(self.target_weight_sum, 10),
            "components": {k: round(v, 4) for k, v in sorted(self.components.items())},
            "unpriced_weight": round(self.unpriced_weight, 10),
            "n_targets": self.n_targets,
            "cash_after": round(self.cash_after, 2),
            "residual_identity": self.residual_identity,
            "residual_ledger": self.residual_ledger,
        }


def _valuation_price(
    open_series: pd.Series, prev_close: pd.Series, day: pd.Timestamp
) -> float | None:
    """估值参考价：开盘价优先，缺失时退回前收盘（与引擎 ``open_value`` 同口径）。"""
    px = _valid_price(open_series.get(day))
    if px is not None:
        return px
    return _valid_price(prev_close.get(day))


def attribute_rebalance(
    *,
    day: pd.Timestamp,
    target: TargetPortfolio,
    qty_pre: Mapping[str, int],
    qty_post: Mapping[str, int],
    cash_pre: float,
    cash_after_ledger: float,
    open_series: Mapping[str, pd.Series],
    prev_close: Mapping[str, pd.Series],
    index_by_symbol: Mapping[str, pd.DatetimeIndex],
    cash_flows: Mapping[str, tuple[float, float]],
    fees: float,
    unit: int,
    blocked: Mapping[str, str],
) -> RebalanceAttribution:
    """把一次调仓后的现金拆成「结构缺口 + 逐标的缺口 − 费用」。

    Args:
        day: 执行日。
        target: 该日的目标权重。
        qty_pre / qty_post: 调仓前 / 后持仓。
        cash_pre: 调仓前现金。
        cash_after_ledger: 成交回放算出的调仓后现金（真值，用于核对）。
        open_series / prev_close: 各标的开盘价与前收盘序列（按日索引）。
        index_by_symbol: 各标的行情日期，用于判断无价成因。
        cash_flows: ``{symbol: (买入成交额, 卖出成交额)}``，当日成交的**实际**金额。
        fees: 当日全部成交费用合计。
        unit: 取整粒度（``weights``=1，``lots``=100）。
        blocked: 当日被拒买单 ``{symbol: reason}``。

    Returns:
        :class:`RebalanceAttribution`。

    Raises:
        CashAuditError: 持仓标的同时缺开盘价与前收盘（无法估值）。
    """
    # ---- 调仓前权益 ----
    # 缺价时按 0 计：这不是本模块的选择，而是引擎 ``open_value`` 的既有行为
    # （``last_price.get(symbol, 0.0)``）。镜像它才能保证残差可核对；若这里改成
    # 报错或估值成别的数，残差会立刻把账撕开。
    mv_pre = 0.0
    for symbol, qty in qty_pre.items():
        if qty <= 0:
            continue
        px = _valuation_price(open_series[symbol], prev_close[symbol], day)
        mv_pre += qty * (px if px is not None else 0.0)
    equity_open = cash_pre + mv_pre

    # ---- 结构缺口 ----
    components: dict[str, float] = {name: 0.0 for name in BASE_COMPONENTS}
    target_sum = float(sum(target.weights.values()))
    components[CASH_CAP] = equity_open * max(0.0, 1.0 - target_sum)

    # ---- 逐标的缺口 ----
    unpriced_weight = 0.0
    for symbol in sorted(set(qty_pre) | set(qty_post) | set(target.weights)):
        w = float(target.weights.get(symbol, 0.0))
        q_pre = int(qty_pre.get(symbol, 0))
        q_after = int(qty_post.get(symbol, 0))
        px = _valuation_price(open_series[symbol], prev_close[symbol], day)
        open_px = _valid_price(open_series[symbol].get(day))
        if px is None and w <= 0 and q_after <= 0:
            continue
        # 缺口按**资金流**算，不按「市值差」算：引擎的成交价会先按最小变动单位
        # 取整（``execution_price`` 即使零滑点也走 ``apply_slippage``），
        # 用未经取整的开盘价算市值会凭空多出几百元的"缺口"。
        buy_gross, sell_gross = cash_flows.get(symbol, (0.0, 0.0))
        realized = q_pre * (px if px is not None else 0.0) + buy_gross - sell_gross
        target_value = equity_open * w
        gap = target_value - realized

        if w > 0 and (px is None or open_px is None):
            # 无开盘价 ⇒ 引擎连买单都发不出去（买入路径不接受前收盘 fallback）
            cause = classify_unpriced(index_by_symbol[symbol], day)
            components[cause] += gap
            unpriced_weight += w
            continue
        if px is None:
            # 有持仓但整段窗口内没有任何行情：引擎按 0 估值，也卖不掉。
            # 这类持仓不占现金（现金早付过了），缺口记 other 而不是塞进无价类。
            components[CASH_OTHER] += gap
            continue

        if gap > CENT:
            reason = blocked.get(symbol)
            if reason in (None, "below_one_unit"):
                # 「不足一个交易单位」本质是取整残差；无拒单记录但缺口很大
                # 只可能是资金不足导致的部分成交（引擎用 min(want, affordable)）
                if gap <= unit * px + CENT:
                    components[CASH_ROUNDING] += gap
                else:
                    components[CASH_INSUFFICIENT] += gap
            elif reason == "insufficient_cash":
                components[CASH_INSUFFICIENT] += gap
            elif reason == "no_price":
                # 有前收盘但无开盘价的情形已在上面拦掉，走到这里说明口径分叉
                components[CASH_OTHER] += gap
            elif reason == "limit_up":
                components[CASH_OTHER] += gap
            else:
                components[CASH_OTHER] += gap
        elif gap < -CENT:
            # 持仓高于目标（跌停卖不出 / 顺延）：负缺口，会减少现金
            components[CASH_OVERWEIGHT] += gap

    components[CASH_FEES] -= fees

    # ---- 两个残差 ----
    # 资金流口径的调仓后现金：与引擎逐笔记账完全同源（只差逐笔 round_money 的零头）
    total_buy = sum(b for b, _ in cash_flows.values())
    total_sell = sum(s for _, s in cash_flows.values())
    cash_after_flow = cash_pre + total_sell - total_buy - fees
    residual_identity = cash_after_ledger - sum(components.values())
    residual_ledger = cash_after_flow - cash_after_ledger

    return RebalanceAttribution(
        date=day,
        equity_open=equity_open,
        target_weight_sum=target_sum,
        components=components,
        unpriced_weight=unpriced_weight,
        n_targets=len(target.weights),
        cash_after=cash_after_ledger,
        residual_identity=residual_identity,
        residual_ledger=residual_ledger,
    )


# --------------------------------------------------------------------------- #
# 五、结果对象
# --------------------------------------------------------------------------- #
@dataclass
class CashAudit:
    """一份回测结果的现金占比归因。"""

    #: 逐日 ``cash/total_equity`` 的均值（与 L3p 的 ``avg_cash_ratio`` 同定义）
    avg_cash_ratio: float
    #: 各分量对该均值的贡献（无量纲；正数占用现金，负数减少现金）
    components: dict[str, float]
    #: 分量占现金总额的比例（只在 ``avg_cash_ratio > 0`` 时有意义）
    shares: dict[str, float]
    rebalances: list[RebalanceAttribution]
    days: int
    periods: int
    #: 目标权重和 < 1 的调仓次数
    periods_with_cap: int
    #: 目标里有「当日无开盘价」标的的调仓次数
    periods_with_unpriced: int
    residual_identity_max: float
    residual_ledger_max: float
    component_sum_gap: float
    ledger_gap: float
    unit: int

    def to_dict(self, *, with_rebalances: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "avg_cash_ratio": self.avg_cash_ratio,
            "components": {k: round(v, 10) for k, v in sorted(self.components.items())},
            "shares": {k: round(v, 10) for k, v in sorted(self.shares.items())},
            "days": self.days,
            "periods": self.periods,
            "periods_with_cap": self.periods_with_cap,
            "periods_with_unpriced": self.periods_with_unpriced,
            "residual_identity_max": self.residual_identity_max,
            "residual_ledger_max": self.residual_ledger_max,
            "component_sum_gap": self.component_sum_gap,
            "ledger_gap": self.ledger_gap,
            "unit": self.unit,
        }
        if with_rebalances:
            payload["rebalances"] = [item.to_dict() for item in self.rebalances]
        return payload


def audit_cash(
    result: PortfolioBacktestResult,
    frames: Mapping[str, pd.DataFrame],
    schedule: Sequence[TargetPortfolio],
    *,
    initial_cash: float,
    unit: int,
) -> CashAudit:
    """对一份组合回测结果做现金归因。

    Args:
        result: 引擎产出（必须与 ``schedule`` / ``frames`` 同源）。
        frames: 各标的行情（含 ``open`` / ``close``，日期索引）。
        schedule: 该次回测的目标权重序列。
        initial_cash: 初始资金（用于回放）。
        unit: 取整粒度，与回测配置的 ``allocation`` 对应（1 或 ``lot_size``）。

    Returns:
        :class:`CashAudit`。``component_sum_gap`` 是分量和与
        ``avg_cash_ratio`` 的差，应接近 0。

    Raises:
        CashAuditError: 任一次调仓的恒等式残差或回放残差超容差——两者都说明归因
            不可信，不得继续出数。
    """
    symbols = list(result.config.symbols)
    ledger = replay_ledger(result, symbols=symbols, initial_cash=initial_cash)
    if ledger.max_cash_gap > 0.01:
        raise CashAuditError(
            f"成交回放现金与引擎记账差 {ledger.max_cash_gap:.4f} 元："
            "回放逻辑与引擎口径不一致，归因全部作废"
        )

    calendar = pd.DatetimeIndex([state.date for state in result.states])
    open_series: dict[str, pd.Series] = {}
    prev_close: dict[str, pd.Series] = {}
    index_by_symbol: dict[str, pd.DatetimeIndex] = {}
    for symbol in symbols:
        frame = frames[symbol]
        open_series[symbol] = frame["open"].reindex(calendar)
        prev_close[symbol] = frame["close"].reindex(calendar).ffill().shift(1)
        index_by_symbol[symbol] = pd.DatetimeIndex(frame.index)

    # 信号日 → 执行日（与引擎同一条规则：信号日之后的首个交易日）
    exec_map: dict[pd.Timestamp, TargetPortfolio] = {}
    for target in schedule:
        pos = calendar.searchsorted(pd.Timestamp(target.signal_date), side="right")
        if pos >= len(calendar):
            continue
        exec_map[calendar[pos]] = target

    blocked_by_date: dict[pd.Timestamp, dict[str, str]] = defaultdict(dict)
    fees_by_date: dict[pd.Timestamp, float] = defaultdict(float)
    flows_by_date: dict[pd.Timestamp, dict[str, list[float]]] = defaultdict(dict)
    for item in result.blocked:
        if item.side == Side.BUY:
            blocked_by_date[item.date][item.symbol] = item.reason
    for fill in result.fills:
        fees_by_date[fill.date] += fill.fees_total
        slot = flows_by_date[fill.date].setdefault(fill.symbol, [0.0, 0.0])
        if fill.side == Side.BUY:
            slot[0] += fill.gross
        else:
            slot[1] += fill.gross

    rebalances: list[RebalanceAttribution] = []
    for day in sorted(exec_map):
        if day not in ledger.cash_daily:
            continue
        prior = calendar[calendar < day]
        if len(prior) == 0:
            cash_pre = float(initial_cash)
            qty_pre: dict[str, int] = {s: 0 for s in symbols}
        else:
            prev_day = prior[-1]
            cash_pre = ledger.cash_daily[prev_day]
            qty_pre = ledger.qty_daily[prev_day]
        rebalances.append(
            attribute_rebalance(
                day=day,
                target=exec_map[day],
                qty_pre=qty_pre,
                qty_post=ledger.qty_daily[day],
                cash_pre=cash_pre,
                cash_after_ledger=ledger.cash_daily[day],
                open_series=open_series,
                prev_close=prev_close,
                index_by_symbol=index_by_symbol,
                cash_flows={
                    s: (v[0], v[1]) for s, v in flows_by_date.get(day, {}).items()
                },
                fees=fees_by_date.get(day, 0.0),
                unit=unit,
                blocked=blocked_by_date.get(day, {}),
            )
        )

    identity_max = max((abs(item.residual_identity) for item in rebalances), default=0.0)
    ledger_max = max((abs(item.residual_ledger) for item in rebalances), default=0.0)
    if identity_max > IDENTITY_TOLERANCE:
        raise CashAuditError(
            f"归因恒等式残差 {identity_max:.4f} 元超过容差 {IDENTITY_TOLERANCE}："
            "分解漏了标的或分量，不得继续出数"
        )
    if ledger_max > LEDGER_TOLERANCE:
        raise CashAuditError(
            f"归因现金与成交回放差 {ledger_max:.4f} 元超过容差 {LEDGER_TOLERANCE}："
            "对执行价/权益的理解与引擎不一致，归因不可信"
        )

    # ---- 逐日归因：分量取最近调仓日的实测值，期间变动计入 other ----
    components = {name: 0.0 for name in BASE_COMPONENTS}
    equity_by_day = {state.date: state.total_equity for state in result.states}
    cursor = 0
    current: RebalanceAttribution | None = None
    usable_days = 0
    for day in calendar:
        equity = equity_by_day.get(day)
        if equity is None or equity <= 0:
            continue
        usable_days += 1
        while cursor < len(rebalances) and rebalances[cursor].date <= day:
            current = rebalances[cursor]
            cursor += 1
        cash = ledger.cash_daily[day]
        if current is None:
            # 首个建仓日之前：折窗口已开始但还没建仓，这一天 100% 是现金。
            # 单列成分而不是塞进 other，否则「设计成本」会和「未归因残余」混在一起。
            components[CASH_PRESTART] += cash / equity
            continue
        for name, value in current.components.items():
            components[name] += value / equity
        components[CASH_OTHER] += (cash - current.cash_after) / equity

    divisor = usable_days if usable_days else 1
    components = {k: v / divisor for k, v in components.items()}
    avg_cash = statistics.fmean(
        ledger.cash_daily[d] / equity_by_day[d]
        for d in calendar
        if equity_by_day.get(d)
    )
    return CashAudit(
        avg_cash_ratio=avg_cash,
        components=components,
        shares={k: (v / avg_cash if avg_cash else 0.0) for k, v in components.items()},
        rebalances=rebalances,
        days=usable_days,
        periods=len(rebalances),
        periods_with_cap=sum(
            1 for item in rebalances if item.target_weight_sum < 1 - 1e-9
        ),
        periods_with_unpriced=sum(1 for item in rebalances if item.unpriced_weight > 0),
        residual_identity_max=identity_max,
        residual_ledger_max=ledger_max,
        component_sum_gap=sum(components.values()) - avg_cash,
        ledger_gap=ledger.max_cash_gap,
        unit=unit,
    )


# --------------------------------------------------------------------------- #
# 六、诊断臂：只保留可成交标的
# --------------------------------------------------------------------------- #
def tradable_only_schedule(
    states: Mapping[str, pd.Series],
    signal_dates: Sequence[pd.Timestamp],
    *,
    calendar: pd.DatetimeIndex,
    frames: Mapping[str, pd.DataFrame],
    max_weight_per_asset: float | None = None,
    drop_causes: Sequence[str] = UNPRICED_COMPONENTS,
) -> tuple[TargetPortfolio, ...]:
    """诊断臂：把指定成因的持仓剔出目标，剩余等权重新归一。

    这是**诊断**，不是新策略：剔掉的是买不进的标的，归一化只是把本来会变成现金的
    权重还给可成交标的。它衡量「现金拖累吃掉多少折收益」，不得当作策略改进——修复
    的是数据缺陷，不是模型。

    口径：
    1. 每个信号日先按执行日（信号日之后的首个交易日）判断可成交性，再用
       :func:`classify_unpriced` 判成因；
    2. 只剔除 ``drop_causes`` 里的成因。默认剔全部无价标的（预登记口径）；若要
       **只修数据缺陷**，传 ``(CASH_DEAD, CASH_NOT_LISTED)`` 而保留停牌——停牌是
       现实约束，把它剔出目标会让复牌后第一次调仓就卖出，等于顺手改了持有期；
    3. 剔除后等权 ``1/k``，超过上限则**压到上限且不再二次归一**（与
       ``schedule_from_states`` 一致：上限触发后降低总仓位，而不是把权重挪给别人）；
    4. 持仓全部不可成交时**跳过本期**，不发空目标——发空目标等于清仓，会把
       「买不进」放大成「强制卖出」，凭空造出现实中不存在的交易。
    """
    from deepquant.gates.l3_portfolio import state_at

    schedule: list[TargetPortfolio] = []
    for day in signal_dates:
        pos = calendar.searchsorted(pd.Timestamp(day), side="right")
        exec_day = calendar[pos] if pos < len(calendar) else None
        held_all: list[str] = [
            symbol for symbol, series in states.items() if state_at(series, day) == 1
        ]
        if not held_all:
            # 策略本身空仓：这是合法的目标（全现金），必须照发
            schedule.append(TargetPortfolio(signal_date=pd.Timestamp(day), weights={}))
            continue
        held: list[str] = []
        for symbol in held_all:
            if exec_day is not None:
                px = _valid_price(frames[symbol]["open"].reindex([exec_day]).iloc[0])
                if px is None:
                    cause = classify_unpriced(
                        pd.DatetimeIndex(frames[symbol].index), exec_day
                    )
                    if cause in drop_causes:
                        continue
            held.append(symbol)
        if not held:
            # 持仓全部被剔（或全部不可成交）：跳过本期调仓，而不是发空目标
            continue
        weight = 1.0 / len(held)
        if max_weight_per_asset is not None:
            weight = min(weight, max_weight_per_asset)
        schedule.append(
            TargetPortfolio(
                signal_date=pd.Timestamp(day), weights={s: weight for s in held}
            )
        )
    return tuple(schedule)


# --------------------------------------------------------------------------- #
# 七、汇总与渲染
# --------------------------------------------------------------------------- #
def summarise_audits(audits: Sequence[CashAudit]) -> dict[str, Any]:
    """把逐折归因汇总成「中位分量 + 合计占比」。

    两套口径都给：中位（抵抗单折极值）、合计占比（看清总盘子）。只报其中一个
    会被挑选：中位数好看，但总现金可能集中在少数几折。
    """
    if not audits:
        return {"folds": 0}
    component_median = {
        n: statistics.median([a.components[n] for a in audits]) for n in BASE_COMPONENTS
    }
    component_total = {n: sum(a.components[n] for a in audits) for n in BASE_COMPONENTS}
    grand = sum(component_total.values())
    return {
        "folds": len(audits),
        "avg_cash_ratio_median": statistics.median([a.avg_cash_ratio for a in audits]),
        "avg_cash_ratio_mean": statistics.fmean(a.avg_cash_ratio for a in audits),
        "component_median": component_median,
        "component_total": component_total,
        "share_total": {n: (v / grand if grand else 0.0) for n, v in component_total.items()},
        "periods_total": sum(a.periods for a in audits),
        "periods_with_cap": sum(a.periods_with_cap for a in audits),
        "periods_with_unpriced": sum(a.periods_with_unpriced for a in audits),
        "residual_identity_max": max(a.residual_identity_max for a in audits),
        "residual_ledger_max": max(a.residual_ledger_max for a in audits),
        "component_sum_gap_max": max(abs(a.component_sum_gap) for a in audits),
        "ledger_gap_max": max(a.ledger_gap for a in audits),
    }


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman 秩相关（不引入 scipy）。任一序列退化（全相同）时返回 None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return None
    return num / den


def render_audit_markdown(payload: Mapping[str, Any]) -> str:
    """把审计结果渲染成 Markdown（只做格式化，不做二次加工）。"""
    lines: list[str] = []
    lines.append(f"# {payload.get('title', '现金归因审计')}")
    lines.append("")
    lines.append(f"- 审计对象：`{payload.get('subject')}`")
    lines.append(
        f"- 预登记：`{payload.get('prereg')}`（sha256 `{str(payload.get('prereg_sha256', ''))[:16]}…`）"
    )
    lines.append(f"- 折数：{payload.get('folds')}")
    lines.append("")
    summary = payload.get("summary") or {}
    lines.append("## 一、现金占比归因")
    lines.append("")
    lines.append("| 分量 | 含义 | 中位（pp） | 合计占比 |")
    lines.append("|---|---|---|---|")
    share = summary.get("share_total") or {}
    median = summary.get("component_median") or {}
    for name in BASE_COMPONENTS:
        lines.append(
            f"| `{name}` | {COMPONENT_LABELS[name]} | "
            f"{median.get(name, 0.0) * 100:+.3f} | {share.get(name, 0.0):.2%} |"
        )
    lines.append("")
    lines.append(
        f"- 平均现金占比（折中位）：**{summary.get('avg_cash_ratio_median', 0.0):.2%}**"
    )
    lines.append(
        f"- 上限触发期：{summary.get('periods_with_cap')} / {summary.get('periods_total')}；"
        f"含无价标的的调仓：{summary.get('periods_with_unpriced')} / {summary.get('periods_total')}"
    )
    lines.append("")
    return "\n".join(lines)
