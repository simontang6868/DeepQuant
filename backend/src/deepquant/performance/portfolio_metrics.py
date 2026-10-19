"""组合级绩效指标：夏普、换手率、成本占比、证据等级（纯计算，无 IO）。

为什么需要这个模块（B18 §9.1 / D4）
------------------------------------
``performance/metrics.py`` 的 ``PerformanceMetrics`` 只有：
累计收益、年化收益、年化波动、最大回撤、交易统计。
**没有夏普比率，也没有换手率** —— 而组合层（横截面动量）如果不看换手率，
就无法判断收益是不是被「高频再平衡」的错觉撑起来的。

本模块只做**加法**：不改 ``PerformanceMetrics``、不改 ``compute_performance``，
避免动既有 980 条测试锁定的口径。

口径写死，不允许调用方各自解释
------------------------------
夏普（``compute_sharpe``）::

    rf_daily   = risk_free_rate_annual / periods_per_year      # 简单折算，不做复利
    excess_t   = r_t - rf_daily
    sharpe     = mean(excess) / stdev(excess, ddof=1) * sqrt(periods_per_year)

- ``r_t`` 为**净**日收益（费用已从权益扣除）；
- 无风险利率默认 **0**，必须显式声明来源后才能改；
- 样本 < 2 或标准差为 0 → 返回 ``None`` 并给出原因，**不得**返回 0。

换手率（``compute_turnover``）::

    average_equity        = mean(可用权益点)
    years                 = n_periods / periods_per_year
    one_way_annualized    = (总成交额 / 2) / average_equity / years

- 「总成交额」= 买入成交额 + 卖出成交额（**不含费用**）；
- 除以 2 是把双边成交折成**单边**换手（买卖各占一半），这是要声明的口径选择；
- 分母用**平均权益**而不是初始权益：组合净值会漂移，用初始权益会在上涨行情中低估换手。

成本占毛收益比（``compute_cost_ratio``）::

    毛收益 = 净收益 + 总费用        # 费用已被扣出权益，故「加回」即为毛
    比值   = 总费用 / 毛收益

- 毛收益 <= 0 时**无定义**（费用占比超过 100% 或毛收益为负）→ ``None`` + 原因，
  此时应报告「成本吞掉全部毛收益」，不得给出一个看似精确的百分比。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from deepquant.performance.curve import DEFAULT_PERIODS_PER_YEAR, CurveStats, curve_stats

# 无风险利率默认值：0。任何非 0 取值都必须由调用方声明来源（国债品种/期限/取数日）。
DEFAULT_RISK_FREE_RATE = 0.0

# B18 §9.2 的证据等级门槛（预先登记，不得事后调整）
MIN_REBALANCE_PERIODS_FOR_CONFIDENCE = 36  # 3 年
MIN_TRADABLE_ASSETS_FOR_CONFIDENCE = 100


@dataclass(frozen=True)
class SharpeResult:
    """夏普比率及其完整口径。"""

    value: float | None
    risk_free_rate_annual: float
    periods_per_year: int
    n_returns: int
    unavailable_reason: str | None
    definition: str


@dataclass(frozen=True)
class TurnoverResult:
    """换手率及其完整口径。"""

    total_traded_amount: float
    average_equity: float | None
    years: float
    one_way_annualized: float | None
    n_fills_side_note: str
    unavailable_reason: str | None
    definition: str


@dataclass(frozen=True)
class CostRatioResult:
    """成本占毛收益比。"""

    total_fees: float
    net_profit: float | None
    gross_profit: float | None
    cost_to_gross_ratio: float | None
    unavailable_reason: str | None
    definition: str


@dataclass(frozen=True)
class EvidenceLevel:
    """证据等级（B18 §9.2）：样本不足时必须显式降级，不得强行下结论。"""

    level: str  # "low" | "adequate"
    n_rebalance_periods: int
    n_tradable_assets: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioPerformance:
    """组合层绩效汇总（净值曲线 + 夏普 + 换手 + 成本占比 + 证据等级）。"""

    stats: CurveStats
    sharpe: SharpeResult
    turnover: TurnoverResult
    cost: CostRatioResult
    evidence: EvidenceLevel
    total_pnl: float | None


def compute_sharpe(
    daily_returns: Sequence[float | None],
    *,
    risk_free_rate_annual: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> SharpeResult:
    """年化夏普比率（口径见模块 docstring）。

    缺失收益（``None``）直接剔除，**不前向填充、不当 0**。
    """
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year 必须 >= 1，收到 {periods_per_year}")
    if not math.isfinite(risk_free_rate_annual):
        raise ValueError(f"risk_free_rate_annual 必须有限，收到 {risk_free_rate_annual!r}")

    definition = (
        "mean(r - rf/N) / stdev(r - rf/N, ddof=1) * sqrt(N)；"
        "r 为净日收益，rf 为年化无风险利率按 rf/N 简单折算"
    )
    rs = [float(r) for r in daily_returns if r is not None and math.isfinite(r)]
    rf_daily = risk_free_rate_annual / periods_per_year

    if len(rs) < 2:
        return SharpeResult(
            value=None,
            risk_free_rate_annual=risk_free_rate_annual,
            periods_per_year=periods_per_year,
            n_returns=len(rs),
            unavailable_reason="可计算日收益少于 2 个，标准差无定义",
            definition=definition,
        )

    excess = [r - rf_daily for r in rs]
    sd = statistics.stdev(excess)
    if sd == 0:
        return SharpeResult(
            value=None,
            risk_free_rate_annual=risk_free_rate_annual,
            periods_per_year=periods_per_year,
            n_returns=len(rs),
            unavailable_reason="超额收益标准差为 0（净值无波动），夏普无定义",
            definition=definition,
        )

    value = statistics.fmean(excess) / sd * math.sqrt(periods_per_year)
    return SharpeResult(
        value=value,
        risk_free_rate_annual=risk_free_rate_annual,
        periods_per_year=periods_per_year,
        n_returns=len(rs),
        unavailable_reason=None,
        definition=definition,
    )


def compute_turnover(
    total_traded_amount: float,
    equity_values: Sequence[float | None],
    *,
    n_periods: int | None = None,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> TurnoverResult:
    """年化单边换手率（口径见模块 docstring）。"""
    if total_traded_amount < 0 or not math.isfinite(total_traded_amount):
        raise ValueError(f"total_traded_amount 必须为有限非负数，收到 {total_traded_amount!r}")
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year 必须 >= 1，收到 {periods_per_year}")

    definition = (
        "(买入成交额 + 卖出成交额) / 2 / 平均权益 / 年数；"
        "成交额不含费用；除 2 表示折算为单边换手"
    )
    available = [v for v in equity_values if v is not None and math.isfinite(v)]
    periods = n_periods if n_periods is not None else len(equity_values)
    years = periods / periods_per_year if periods > 0 else 0.0

    if not available:
        return TurnoverResult(
            total_traded_amount=float(total_traded_amount),
            average_equity=None,
            years=years,
            one_way_annualized=None,
            n_fills_side_note="无可用权益点",
            unavailable_reason="权益序列为空，无法计算平均权益",
            definition=definition,
        )

    avg_equity = statistics.fmean(available)
    if avg_equity <= 0:
        return TurnoverResult(
            total_traded_amount=float(total_traded_amount),
            average_equity=avg_equity,
            years=years,
            one_way_annualized=None,
            n_fills_side_note="平均权益非正",
            unavailable_reason="平均权益 <= 0，换手率无定义",
            definition=definition,
        )
    if years <= 0:
        return TurnoverResult(
            total_traded_amount=float(total_traded_amount),
            average_equity=avg_equity,
            years=years,
            one_way_annualized=None,
            n_fills_side_note="期数为 0",
            unavailable_reason="期数为 0，年化无定义",
            definition=definition,
        )

    return TurnoverResult(
        total_traded_amount=float(total_traded_amount),
        average_equity=avg_equity,
        years=years,
        one_way_annualized=(float(total_traded_amount) / 2.0) / avg_equity / years,
        n_fills_side_note="成交额 = 买入 + 卖出，均不含费用",
        unavailable_reason=None,
        definition=definition,
    )


def compute_cost_ratio(
    total_fees: float,
    *,
    initial_value: float,
    final_value: float | None,
) -> CostRatioResult:
    """成本占毛收益比（口径见模块 docstring）。"""
    if total_fees < 0 or not math.isfinite(total_fees):
        raise ValueError(f"total_fees 必须为有限非负数，收到 {total_fees!r}")

    definition = "总费用 / 毛收益；毛收益 = 净收益 + 总费用（费用已从权益扣除，故加回）"
    if final_value is None:
        return CostRatioResult(
            total_fees=float(total_fees),
            net_profit=None,
            gross_profit=None,
            cost_to_gross_ratio=None,
            unavailable_reason="最终权益不可用，无法计算净收益与毛收益",
            definition=definition,
        )

    net_profit = final_value - initial_value
    gross_profit = net_profit + total_fees
    if gross_profit <= 0:
        return CostRatioResult(
            total_fees=float(total_fees),
            net_profit=net_profit,
            gross_profit=gross_profit,
            cost_to_gross_ratio=None,
            unavailable_reason=(
                "毛收益 <= 0：费用已吞掉全部毛收益或策略本身毛亏，"
                "此时百分比无解释力，应直接报告「不可覆盖成本」"
            ),
            definition=definition,
        )

    return CostRatioResult(
        total_fees=float(total_fees),
        net_profit=net_profit,
        gross_profit=gross_profit,
        cost_to_gross_ratio=float(total_fees) / gross_profit,
        unavailable_reason=None,
        definition=definition,
    )


def assess_evidence_level(
    *,
    n_rebalance_periods: int,
    n_tradable_assets: int,
    min_periods: int = MIN_REBALANCE_PERIODS_FOR_CONFIDENCE,
    min_assets: int = MIN_TRADABLE_ASSETS_FOR_CONFIDENCE,
) -> EvidenceLevel:
    """按 B18 §9.2 预先登记的门槛判定证据等级。

    门槛不得事后调整：一旦按结果反过来调门槛，就等于用样本挑选结论。
    """
    if n_rebalance_periods < 0 or n_tradable_assets < 0:
        raise ValueError("期数与标的数不能为负")
    reasons: list[str] = []
    if n_rebalance_periods < min_periods:
        reasons.append(
            f"有效调仓期数 {n_rebalance_periods} < {min_periods}（不足 3 年），"
            "跨市场状态覆盖不足"
        )
    if n_tradable_assets < min_assets:
        reasons.append(
            f"可交易标的数 {n_tradable_assets} < {min_assets}，截面分散化不足、"
            "单只极端值影响过大"
        )
    return EvidenceLevel(
        level="low" if reasons else "adequate",
        n_rebalance_periods=n_rebalance_periods,
        n_tradable_assets=n_tradable_assets,
        reasons=tuple(reasons),
    )


def compute_portfolio_performance(
    equity_values: Sequence[float | None],
    *,
    initial_value: float,
    total_traded_amount: float,
    total_fees: float,
    risk_free_rate_annual: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
    n_rebalance_periods: int = 0,
    n_tradable_assets: int = 0,
) -> PortfolioPerformance:
    """组合净值的完整绩效汇总。

    ``equity_values`` 必须是**连续账户净值**（不是窗口级独立收益的拼接）——
    这正是 R12 要求的形态；调用方若只有窗口级结果，不得调用本函数冒充组合绩效。
    """
    stats = curve_stats(equity_values, initial_value, periods_per_year=periods_per_year)
    sharpe = compute_sharpe(
        stats.daily_returns,
        risk_free_rate_annual=risk_free_rate_annual,
        periods_per_year=periods_per_year,
    )
    turnover = compute_turnover(
        total_traded_amount,
        equity_values,
        n_periods=stats.n_periods,
        periods_per_year=periods_per_year,
    )
    cost = compute_cost_ratio(
        total_fees,
        initial_value=initial_value,
        final_value=stats.final_value,
    )
    evidence = assess_evidence_level(
        n_rebalance_periods=n_rebalance_periods,
        n_tradable_assets=n_tradable_assets,
    )
    total_pnl = (
        None if stats.final_value is None else stats.final_value - initial_value
    )
    return PortfolioPerformance(
        stats=stats,
        sharpe=sharpe,
        turnover=turnover,
        cost=cost,
        evidence=evidence,
        total_pnl=total_pnl,
    )
