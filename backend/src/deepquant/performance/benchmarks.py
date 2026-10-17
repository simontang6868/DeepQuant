"""基准策略：B0 现金 / B1 同标的买入持有 / B2 市场指数（纯计算，无 IO）。

为什么单独一个模块，而不是塞进 ``performance/metrics.py``
------------------------------------------------------------
``compute_performance`` 的输入是 ``BacktestResult``（订单、成交、每日账户状态）。
基准不是回测：它没有信号、没有订单，硬塞进 ``PerformanceMetrics`` 会污染既有口径，
也会让「策略绩效」与「基准绩效」看起来像同一种东西——它们只是**可比**，不是同类。

三个基准各自的合法用途
----------------------
- **B0 现金**：最弱基准。回答「策略是否只是把本金放着不动」。零成本、无假设。
- **B1 同标的买入持有**：单标的策略的公平对照。同一区间、同一复权、同一成交时点、
  同一费率。回答「择时是否优于不择时」。
- **B2 市场指数**：组合级策略的对照。回答「选股是否优于直接持有市场」。
  指数**不可交易**，因此不套用个股费率、不扣任何交易成本——这一点必须随结果声明。

不能做的事（写死在这里，避免被后来的调用方"顺手"用掉）
------------------------------------------------------
- 不得用「策略零成交窗口」冒充 B1：那是策略自身行为，不是买入持有；
- 不得用某只成分股冒充 B2：单标的 ≠ 市场；
- 不得把 B1 与 B2 直接相减当超额：口径不同（有无成本、有无股息）必须分别声明。

统一口径见 ``performance/curve.py``（累计/年化/波动/回撤的唯一定义处）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees, execution_price, round_money
from deepquant.market.ashare import limit_prices, open_fill_block_reason
from deepquant.market.rules import Side
from deepquant.performance.curve import DEFAULT_PERIODS_PER_YEAR, curve_stats

BenchmarkKind = Literal["cash", "buy_and_hold", "index"]
IndexKind = Literal["price", "total_return"]

# 股息口径的取值（必须显式写进结果，不能靠读者猜）
DIVIDEND_NONE = "none"
DIVIDEND_APPROX_QFQ = "approximate_qfq"
DIVIDEND_INCLUDED = "included"


@dataclass(frozen=True)
class BenchmarkResult:
    """一个基准的绩效摘要（口径字段随结果返回，避免事后口径漂移）。"""

    kind: BenchmarkKind
    label: str
    start_date: pd.Timestamp | None
    end_date: pd.Timestamp | None
    n_periods: int
    initial_value: float
    final_value: float | None
    cumulative_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    max_drawdown_ratio: float | None
    includes_trading_costs: bool
    dividend_convention: str
    is_complete: bool
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class BuyAndHoldResult:
    """B1 买入持有的摘要 + 可审计的成交细节。"""

    summary: BenchmarkResult
    buy_date: pd.Timestamp | None
    buy_price: float | None
    quantity: int
    buy_fees: float
    cash_left: float
    liquidated: bool
    sell_fees: float
    blocked_reason: str | None

    @property
    def is_executable(self) -> bool:
        """期初是否真的买到了至少 1 手（买不起 → 不是「零收益策略」）。"""
        return self.quantity > 0


def _summary(
    *,
    kind: BenchmarkKind,
    label: str,
    start_date: pd.Timestamp | None,
    end_date: pd.Timestamp | None,
    stats,
    initial_value: float,
    includes_trading_costs: bool,
    dividend_convention: str,
    assumptions: tuple[str, ...],
    limitations: tuple[str, ...],
) -> BenchmarkResult:
    """把 ``CurveStats`` 映射为 ``BenchmarkResult``（避免三处重复赋值）。"""
    return BenchmarkResult(
        kind=kind,
        label=label,
        start_date=start_date,
        end_date=end_date,
        n_periods=stats.n_periods,
        initial_value=initial_value,
        final_value=stats.final_value,
        cumulative_return=stats.cumulative_return,
        annualized_return=stats.annualized_return,
        annualized_volatility=stats.annualized_volatility,
        max_drawdown_ratio=stats.max_drawdown_ratio,
        includes_trading_costs=includes_trading_costs,
        dividend_convention=dividend_convention,
        is_complete=stats.is_complete,
        assumptions=assumptions,
        limitations=limitations,
    )


# --------------------------------------------------------------------------- #
# B0 现金
# --------------------------------------------------------------------------- #


def cash_benchmark(
    initial_value: float,
    *,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
    n_periods: int = 0,
) -> BenchmarkResult:
    """B0：现金基准（净收益恒为 0）。

    这是判定「策略是否只是没跑赢放着不动」的下界。**不假设任何无风险利率**——
    引入利率就变成一个需要声明来源的假设，而本模块不做无来源的假设。
    """
    if not isinstance(initial_value, (int, float)) or isinstance(initial_value, bool):
        raise ValueError(f"initial_value 必须是数值，收到 {initial_value!r}")
    if not math.isfinite(initial_value) or initial_value <= 0:
        raise ValueError(f"initial_value 必须为有限正数，收到 {initial_value!r}")
    if n_periods < 0:
        raise ValueError(f"n_periods 不能为负，收到 {n_periods}")
    return BenchmarkResult(
        kind="cash",
        label="B0 现金",
        start_date=start_date,
        end_date=end_date,
        n_periods=n_periods,
        initial_value=float(initial_value),
        final_value=float(initial_value),
        cumulative_return=0.0,
        annualized_return=0.0,
        annualized_volatility=0.0,
        max_drawdown_ratio=0.0,
        includes_trading_costs=False,
        dividend_convention=DIVIDEND_NONE,
        is_complete=True,
        assumptions=(
            "本金保持不变，无利息、无费用、无分红",
            "不引入无风险利率：本基准不假设任何未声明来源的收益率",
        ),
        limitations=("现金基准不反映通胀与实际购买力变化",),
    )


# --------------------------------------------------------------------------- #
# B1 同标的买入持有
# --------------------------------------------------------------------------- #


def _affordable_quantity(
    cash: float,
    price: float,
    lot_size: int,
    config: BacktestConfig,
) -> tuple[int, object | None]:
    """在「成交额 + 买入费用 <= 现金」约束下逐手回退，返回 (数量, Fees)。

    与引擎 ``_affordable_buy_quantity`` 同语义：买不起 1 手返回 (0, None)，
    **不得**四舍五入成「买一点点」——那会虚构出不可执行的收益。
    """
    if lot_size < 1:
        raise ValueError(f"lot_size 必须 >= 1，收到 {lot_size}")
    if price <= 0 or not math.isfinite(price):
        return 0, None
    lots = int(cash // (price * lot_size))
    quantity = lots * lot_size
    while quantity > 0:
        fees = compute_fees(Side.BUY, price, quantity, config)
        if price * quantity + fees.total <= cash + 1e-9:
            return quantity, fees
        quantity -= lot_size
    return 0, None


def buy_and_hold(
    dates: Sequence[pd.Timestamp],
    opens: Sequence[float | None],
    closes: Sequence[float | None],
    config: BacktestConfig,
    *,
    liquidate_at_end: bool = False,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> BuyAndHoldResult:
    """B1：同区间、同复权、同成交时点的买入持有。

    成交规则（与引擎一致，不另立一套）：
    - 期初**首个可执行日**（开盘价有限且 > 0）按开盘价买入；
    - 买入价 = ``execution_price(BUY, open, config)``，即含买入滑点；
    - 数量受 100 股整手与「成交额 + 费用 <= 现金」约束，逐手回退；
    - 持有期内每日估值 = 数量 × 当日收盘价 + 剩余现金（收盘缺失 → 该日不可估值）；
    - 期末默认按**收盘价估值**（``liquidate_at_end=False``），不虚构卖出；
      置 true 时按卖出滑点价平仓并扣卖出费用，用于「含平仓成本」口径。

    ``liquidate_at_end`` 默认 False 的理由：B1 的定位是「不交易的对照」，
    强行平仓会引入一个策略侧并不存在的时点假设。两种口径都可报告，
    但**必须声明用的是哪一种**——本函数把选择写进 ``summary.assumptions``。

    涨跌停：若 ``config.market_rules.price_limit`` 已配置，期初开盘价触及涨停
    则判定为买不进并顺延；若一直买不进，返回不可执行结果（不是 0% 收益）。
    """
    if not (len(dates) == len(opens) == len(closes)):
        raise ValueError(
            f"dates/opens/closes 长度不一致：{len(dates)}/{len(opens)}/{len(closes)}"
        )
    if len(dates) == 0:
        raise ValueError("dates 不能为空")

    lot_size = config.market_rules.lot_size
    price_limit = config.market_rules.price_limit
    initial_cash = float(config.initial_cash)
    cash = initial_cash

    buy_idx: int | None = None
    buy_price: float | None = None
    quantity = 0
    buy_fees_total = 0.0
    blocked_reason: str | None = None

    prev_close: float | None = None
    for i, (open_raw, close_raw) in enumerate(zip(opens, closes, strict=True)):
        open_ok = open_raw is not None and math.isfinite(open_raw) and open_raw > 0
        if open_ok:
            price = execution_price(Side.BUY, float(open_raw), config)
            # 复用既有纯函数：边界价与「前收缺失/未配置则跳过」的语义由它们定义，
            # 这里不另写一份，否则两处精度处理迟早会分叉。
            boundary = limit_prices(prev_close, price_limit)
            block = open_fill_block_reason(Side.BUY, price, boundary)
            if block is None:
                # 首个「可执行日」由此确定：买不进就记为不可执行，**不往后找便宜的日子**
                # ——那等于偷偷加了择时，B1 就不再是「买入持有」。
                qty, fees = _affordable_quantity(cash, price, lot_size, config)
                if qty > 0 and fees is not None:
                    cost = round_money(price * qty)
                    cash = round_money(cash - cost - fees.total)
                    buy_idx = i
                    buy_price = price
                    quantity = qty
                    buy_fees_total = fees.total
                else:
                    blocked_reason = "insufficient_cash"
                break
            # 涨停买不进：顺延到下一交易日（现实约束，不是择时）
            blocked_reason = block
        prev_close = close_raw if close_raw is not None else prev_close

    if buy_idx is None:
        # 期初买不进：这是**可执行性失败**，不是「零收益」。
        # 两种原因必须分开写，否则「全程涨停」会被误读成「没钱」。
        reason_text = {
            "insufficient_cash": "期初资金不足以买入 1 手（含买入费用）",
            "limit_up": "期初连续涨停，未获得可执行的买入时点",
        }.get(blocked_reason, "期初无有效开盘价")
        return BuyAndHoldResult(
            summary=BenchmarkResult(
                kind="buy_and_hold",
                label="B1 买入持有（不可执行）",
                start_date=dates[0],
                end_date=dates[-1],
                n_periods=len(dates),
                initial_value=initial_cash,
                final_value=None,
                cumulative_return=None,
                annualized_return=None,
                annualized_volatility=None,
                max_drawdown_ratio=None,
                includes_trading_costs=True,
                dividend_convention=DIVIDEND_APPROX_QFQ,
                is_complete=False,
                assumptions=(),
                limitations=(
                    f"{reason_text}，无法构造买入持有基准；不得把该情形记作 0% 收益",
                ),
            ),
            buy_date=None,
            buy_price=None,
            quantity=0,
            buy_fees=0.0,
            cash_left=initial_cash,
            liquidated=False,
            sell_fees=0.0,
            blocked_reason=blocked_reason,
        )

    # 买入日及之后的每日估值；买入前记初始本金（首期收益因此为 0，不是缺失）
    values: list[float | None] = []
    for i, close_raw in enumerate(closes):
        if i < buy_idx:
            values.append(initial_cash)
            continue
        if close_raw is None or not math.isfinite(close_raw):
            values.append(None)
            continue
        values.append(quantity * float(close_raw) + cash)

    sell_fees = 0.0
    liquidated = False
    last_close = closes[-1]
    if liquidate_at_end and last_close is not None and math.isfinite(last_close):
        sell_price = execution_price(Side.SELL, float(last_close), config)
        fees = compute_fees(Side.SELL, sell_price, quantity, config)
        sell_fees = fees.total
        values[-1] = cash + round_money(sell_price * quantity) - sell_fees
        liquidated = True

    stats = curve_stats(values, initial_cash, periods_per_year=periods_per_year)

    assumptions = [
        "期初首个可执行日按开盘价买入，买入价含买入滑点",
        f"数量按 {lot_size} 股整手与「成交额 + 费用 <= 初始资金」约束逐手回退",
        "持有期不调仓、不加仓、不减仓",
        "期末按收盘价估值" if not liquidated else "期末按卖出滑点价平仓并扣卖出费用",
    ]
    limitations = [
        "qfq 复权近似含股息再投资，但**未验证**（口径误差未知）",
        "停牌/缺价日不估值，序列因此可能不完整（is_complete=False）",
        "涨跌停仅在期初买入时判定；本基准持有期内不卖出，故不含跌停无法卖出问题",
    ]
    if price_limit is None:
        limitations.append(
            "config.market_rules 未配置涨跌停：期初涨停未做拦截（这并不表示未发生涨停）"
        )

    return BuyAndHoldResult(
        summary=_summary(
            kind="buy_and_hold",
            label="B1 买入持有",
            start_date=dates[buy_idx],
            end_date=dates[-1],
            stats=stats,
            initial_value=initial_cash,
            includes_trading_costs=True,
            dividend_convention=DIVIDEND_APPROX_QFQ,
            assumptions=tuple(assumptions),
            limitations=tuple(limitations),
        ),
        buy_date=dates[buy_idx],
        buy_price=buy_price,
        quantity=quantity,
        buy_fees=buy_fees_total,
        cash_left=cash,
        liquidated=liquidated,
        sell_fees=sell_fees,
        blocked_reason=blocked_reason,
    )


# --------------------------------------------------------------------------- #
# B2 市场指数
# --------------------------------------------------------------------------- #


def index_benchmark(
    dates: Sequence[pd.Timestamp],
    closes: Sequence[float | None],
    *,
    index_code: str,
    index_kind: IndexKind,
    initial_value: float = 1.0,
    label: str | None = None,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> BenchmarkResult:
    """B2：市场指数基准（归一化净值，不含任何交易成本）。

    Args:
        index_code: 指数代码（如 ``000300``），仅用于标注，**不发起任何取数**。
        index_kind: **必须显式**声明 ``price``（价格指数）或
            ``total_return``（全收益指数）。两者年化差异可达 1~2pp，
            且本项目**尚未核实**数据源提供的是哪一种——因此不允许默认猜测。

    为什么指数不扣费：指数不可交易。给它套个股费率会制造一个「可执行的指数」假象，
    从而使超额收益看起来更小（或更大）——两边都不真实。
    """
    if index_kind not in ("price", "total_return"):
        raise ValueError(f"index_kind 必须是 price / total_return，收到 {index_kind!r}")
    if not (len(dates) == len(closes)):
        raise ValueError(f"dates/closes 长度不一致：{len(dates)}/{len(closes)}")
    if len(dates) == 0:
        raise ValueError("dates 不能为空")

    base = closes[0]
    if base is None or not math.isfinite(base) or base <= 0:
        raise ValueError("首期指数收盘价必须为有限正数，无法以它为基期归一")

    values: list[float | None] = [
        None if (c is None or not math.isfinite(c)) else float(c) / float(base) * initial_value
        for c in closes
    ]
    stats = curve_stats(values, initial_value, periods_per_year=periods_per_year)

    return _summary(
        kind="index",
        label=label or f"B2 指数 {index_code}（{index_kind}）",
        start_date=dates[0],
        end_date=dates[-1],
        stats=stats,
        initial_value=initial_value,
        includes_trading_costs=False,
        dividend_convention=(
            DIVIDEND_INCLUDED if index_kind == "total_return" else DIVIDEND_NONE
        ),
        assumptions=(
            "以首期收盘价为基期归一",
            "不含任何交易成本、跟踪误差与申赎费用（指数不可交易）",
            f"index_kind={index_kind}："
            + (
                "全收益指数（含股息再投资）"
                if index_kind == "total_return"
                else "价格指数（不含股息）"
            ),
        ),
        limitations=(
            "指数口径（价格/全收益）由调用方声明；本项目尚未核实数据源实际提供哪一种",
            "不得与 B1 直接相减当超额收益：两者成本与股息口径不同",
        ),
    )


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def benchmark_table(results: Sequence[BenchmarkResult]) -> list[dict[str, object]]:
    """把若干基准整理成可直接落盘的表格行（数值保持 None，不填 0）。"""
    rows: list[dict[str, object]] = []
    for r in results:
        rows.append(
            {
                "kind": r.kind,
                "label": r.label,
                "start_date": None if r.start_date is None else str(r.start_date.date()),
                "end_date": None if r.end_date is None else str(r.end_date.date()),
                "n_periods": r.n_periods,
                "initial_value": r.initial_value,
                "final_value": r.final_value,
                "cumulative_return": r.cumulative_return,
                "annualized_return": r.annualized_return,
                "annualized_volatility": r.annualized_volatility,
                "max_drawdown_ratio": r.max_drawdown_ratio,
                "includes_trading_costs": r.includes_trading_costs,
                "dividend_convention": r.dividend_convention,
                "is_complete": r.is_complete,
            }
        )
    return rows
