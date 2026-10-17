"""净值曲线统计（基准与组合绩效共用，唯一定义处）。

为什么单独一个模块
------------------
「累计收益 / 年化 / 波动 / 最大回撤」这套口径，原先只存在于
``performance/metrics.py`` 的 ``compute_performance`` 里，且绑定在
``BacktestResult`` 上（需要 AccountState 序列）。

B18 之后需要给**基准**（没有订单、没有账户状态）和**组合净值**（连续账户曲线）
算同一组指标。若各自复制一份，两处精度/年化因子迟早分叉，而分叉之后
「策略 vs 基准」的差就变成口径差，不是策略效果。

因此把口径集中到这里：
- ``metrics.compute_performance`` 仍然独立（它要处理账户状态缺失、订单配对等
  策略专属语义），**本模块不替换它**；
- 基准与组合层使用本模块，并与之逐项对齐。

口径（与 ``metrics.py`` 完全一致，改动必须同步）：
- 累计收益 = 期末值 / 期初值 − 1；
- 年化 = ``(期末/期初)^(1/年数) − 1``，``年数 = 期数 / periods_per_year``（CAGR）；
- 年化波动 = ``样本标准差(日收益, ddof=1) × sqrt(periods_per_year)``；
- 最大回撤 = 基于可用点的历史高水位回撤；
- 首期收益的前一期基准 = 期初值（**不得**把首日收益硬设为 0）；
- 缺失期（``None``）之后的一期收益不可算，不填 0。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_PERIODS_PER_YEAR = 252


@dataclass(frozen=True)
class CurveStats:
    """一条净值/权益序列的统计结果。"""

    n_periods: int
    n_returns: int
    initial_value: float
    final_value: float | None
    cumulative_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    max_drawdown_ratio: float | None
    max_drawdown_amount: float | None
    daily_returns: tuple[float, ...]
    is_complete: bool


def curve_stats(
    values: Sequence[float | None],
    initial_value: float,
    *,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> CurveStats:
    """由净值序列计算收益与风险指标。

    Args:
        values: 逐期净值/权益。``None`` 表示该期不可估值。
        initial_value: 期初净值，必须为有限正数（用于首期收益的基准）。
        periods_per_year: 年化因子（日线为 252）。

    Raises:
        ValueError: ``periods_per_year < 1`` 或 ``initial_value`` 非有限正数。
    """
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year 必须 >= 1，收到 {periods_per_year}")
    if not isinstance(initial_value, (int, float)) or isinstance(initial_value, bool):
        raise ValueError(f"initial_value 必须是数值，收到 {initial_value!r}")
    if not math.isfinite(initial_value) or initial_value <= 0:
        raise ValueError(f"initial_value 必须为有限正数，收到 {initial_value!r}")

    n_periods = len(values)
    n_available = sum(1 for v in values if v is not None)
    is_complete = n_periods > 0 and n_available == n_periods
    final_value = values[-1] if values else None

    cumulative_return: float | None = None
    annualized_return: float | None = None
    if final_value is not None:
        cumulative_return = final_value / initial_value - 1.0
        if final_value > 0 and n_periods > 0:
            years = n_periods / periods_per_year
            annualized_return = (final_value / initial_value) ** (1.0 / years) - 1.0

    # 逐期收益：缺失期会把 prev 置为 None，使其后一期也不可算（不填补、不前向填充）
    returns: list[float] = []
    prev: float | None = initial_value
    for v in values:
        if v is None or prev is None or prev <= 0:
            prev = v
            continue
        returns.append(v / prev - 1.0)
        prev = v

    annualized_volatility: float | None = None
    if len(returns) >= 2:
        annualized_volatility = statistics.stdev(returns) * math.sqrt(periods_per_year)

    max_dd_amount: float | None = None
    max_dd_ratio: float | None = None
    available = [v for v in values if v is not None]
    if available:
        peak = available[0]
        worst_amount = 0.0
        worst_ratio = 0.0
        for v in available:
            if v > peak:
                peak = v
            drawdown = peak - v
            if drawdown > worst_amount:
                worst_amount = drawdown
                worst_ratio = (drawdown / peak) if peak > 0 else 0.0
        max_dd_amount = worst_amount
        max_dd_ratio = worst_ratio

    return CurveStats(
        n_periods=n_periods,
        n_returns=len(returns),
        initial_value=float(initial_value),
        final_value=final_value,
        cumulative_return=cumulative_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        max_drawdown_ratio=max_dd_ratio,
        max_drawdown_amount=max_dd_amount,
        daily_returns=tuple(returns),
        is_complete=is_complete,
    )
