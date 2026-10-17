"""组合层配置与调仓调度（A1 / A2，纯数据 + 纯函数，无 IO、无回测循环）。

范围声明（很重要）
------------------
本模块**只**提供组合层最底层的两件事：

- **A1** ``PortfolioBacktestConfig``：组合回测的输入契约（标的、资金、费率模板、口径）；
- **A2** 调仓日历与截面选股/权重（``month_end_signal_dates`` /
  ``equal_weight_targets`` / ``build_rebalance_schedule``）。

**不包含** A3 ``run_portfolio_backtest``（组合记账循环）。原因不是没时间，而是它与
下面的四件事强耦合，必须一起设计才有意义：

1. 卖出先于买入的现金释放顺序；
2. 权重记账口径下「费用怎么算」（整手 vs 1 股粒度，最低佣金是否仍生效）；
3. 停牌/缺价标的的估值与持仓冻结；
4. 涨跌停导致的「该卖卖不出 / 该买买不进」如何顺延。

在这些规则定稿前写循环，只会产出一条**看起来像组合净值、但口径说不清**的曲线。
按 B18 §7，A3 属于 Gate 0-5（6~8 人日），单独立项。

为什么这些要单独成模块而不是塞进 strategies
--------------------------------------------
``Strategy.generate_signals`` 是「单标的 → 单序列」的接口（``strategies/base.py``）。
横截面选股需要「同一时点多标的比较」，语义上不属于任何单标的策略。
把它放这里，避免有人为了「复用」而把多标的逻辑硬塞进单标的接口
——B18 §6.3 明令禁止「把多股票策略伪装成单股票策略」。

绝不做的事
----------
- 不 import ``backtest.engine``：本模块不执行任何撮合；
- 不修改 ``BacktestConfig``：费率与市场规则由调用方以「模板」形式传入；
- 不做参数搜索：``top_fraction`` 等是先验固定的设计参数，不是待优化超参。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from deepquant.backtest.config import BacktestConfig

AllocationMode = Literal["weights", "lots"]

# 权重和的容差：等权 1/k 在浮点下未必精确等于 1
WEIGHT_SUM_TOLERANCE = 1e-9


@dataclass(frozen=True)
class PortfolioBacktestConfig:
    """组合回测的输入契约（A1）。

    Args:
        symbols: 组合可选的标的池（code 列表，顺序不影响结果）。
        initial_cash: 组合初始资金。
        fee_config: **费率与市场规则的模板**（复用 ``BacktestConfig``，
            其 ``symbol`` / ``initial_cash`` 字段不参与组合层计算）。
            这样费用口径与单标的回测**共用一处定义**，不会两边漂移。
        allocation: ``weights``（权重记账，主口径，不受整手约束）或
            ``lots``（整手约束，用于检验可执行性）。B18 Q3：(a) 主 + (c) 附。
        lot_size: 整手股数；``weights`` 模式下不生效，仅记录。
        max_weight_per_asset: 单标的上限（防止等权在小样本下过度集中）。
            ``None`` 表示不限制。

    Raises:
        ValueError: 标的为空/重复、资金非正、allocation 非法、权重上限不合法。
    """

    symbols: tuple[str, ...]
    initial_cash: float
    fee_config: BacktestConfig
    allocation: AllocationMode = "weights"
    lot_size: int = 100
    max_weight_per_asset: float | None = None

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("symbols 不能为空")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols 含重复代码")
        if not isinstance(self.initial_cash, (int, float)) or isinstance(self.initial_cash, bool):
            raise ValueError(f"initial_cash 必须是数值，收到 {self.initial_cash!r}")
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError(f"initial_cash 必须为有限正数，收到 {self.initial_cash!r}")
        if self.allocation not in ("weights", "lots"):
            raise ValueError(
                f"allocation 必须是 weights / lots，收到 {self.allocation!r}"
            )
        if self.lot_size < 1:
            raise ValueError(f"lot_size 必须 >= 1，收到 {self.lot_size}")
        if self.max_weight_per_asset is not None and not (
            0 < self.max_weight_per_asset <= 1
        ):
            raise ValueError(
                f"max_weight_per_asset 必须在 (0, 1]，收到 {self.max_weight_per_asset!r}"
            )


@dataclass(frozen=True)
class TargetPortfolio:
    """某一信号日的目标权重表（执行发生在**次一交易日开盘**）。

    ``weights`` 为空 dict 表示「本期空仓（全部持现金）」——这是合法状态，
    不是错误。把它当作错误会导致「选不出股票时被迫随便买点什么」。
    """

    signal_date: pd.Timestamp
    weights: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if total > 1.0 + WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"权重和 {total:.12f} 超过 1.0")
        for symbol, w in self.weights.items():
            if not math.isfinite(w):
                raise ValueError(f"{symbol} 的权重非有限值：{w!r}")
            if w < 0:
                raise ValueError(
                    f"{symbol} 的权重为负 {w!r}：本模块不支持做空"
                    "（引擎 Side.SELL 仅用于平仓，见 B18 §4）"
                )

    @property
    def is_cash_only(self) -> bool:
        """本期是否全现金（无持仓）。"""
        return not self.weights


def month_end_signal_dates(
    trading_days: Sequence[pd.Timestamp],
) -> tuple[pd.Timestamp, ...]:
    """返回每月**最后一个交易日**（信号日）。

    信号在月末收盘后产生，执行在次月首个交易日开盘 —— 与引擎
    「T 日收盘信号、T+1 开盘成交」一致（``engine.py``），不做例外。

    用「月末最后交易日」而不是「每月最后一个自然日」：自然日可能不是交易日，
    以它为信号日会让「次一交易日」变成不可预期的跳跃。
    """
    if len(trading_days) == 0:
        return ()
    index = pd.DatetimeIndex(pd.to_datetime(list(trading_days))).sort_values()
    if index.has_duplicates:
        raise ValueError("trading_days 含重复日期")
    series = pd.Series(index=index, data=index)
    return tuple(series.groupby([index.year, index.month]).last().tolist())


def equal_weight_targets(
    scores: Mapping[str, float | None],
    *,
    top_fraction: float = 0.2,
    min_assets: int = 1,
    max_weight_per_asset: float | None = None,
) -> dict[str, float]:
    """按分数取前 ``top_fraction`` 比例，等权分配（A2 的选股核心）。

    Args:
        scores: ``{symbol: 分数}``。``None`` / ``NaN`` 视为「当期不可参与排序」
            （例如历史长度不足或停牌），**不填 0**——填 0 会让缺数据的标的
            排到中间，凭空进入组合。
        top_fraction: 选前多少比例，``(0, 1]``。
        min_assets: 可参与排序的标的少于此数时返回空仓（不强行凑数）。
        max_weight_per_asset: 单标的上限；若 ``1/k`` 超过上限，则**降低总仓位**
            而不是把权重挪给别的标的（后者等于偷偷改变选股结果）。

    Returns:
        ``{symbol: weight}``，权重和 <= 1。空 dict 表示空仓。

    排序确定性：分数降序，**并列时按代码升序**。没有这条，同一份数据两次运行
    可能选出不同组合，回测就不可复现。
    """
    if not (0.0 < top_fraction <= 1.0):
        raise ValueError(f"top_fraction 必须在 (0, 1]，收到 {top_fraction}")
    if min_assets < 1:
        raise ValueError(f"min_assets 必须 >= 1，收到 {min_assets}")

    usable = {
        str(s): float(v)
        for s, v in scores.items()
        if v is not None and isinstance(v, (int, float)) and math.isfinite(float(v))
    }
    if len(usable) < min_assets:
        return {}

    ranked = sorted(usable.items(), key=lambda kv: (-kv[1], kv[0]))
    k = max(1, math.ceil(len(ranked) * top_fraction))
    picked = ranked[:k]

    weight = 1.0 / k
    if max_weight_per_asset is not None and weight > max_weight_per_asset:
        weight = max_weight_per_asset
    return {symbol: weight for symbol, _ in picked}


def build_rebalance_schedule(
    scores_by_date: Mapping[pd.Timestamp, Mapping[str, float | None]],
    *,
    top_fraction: float = 0.2,
    min_assets: int = 1,
    max_weight_per_asset: float | None = None,
) -> tuple[TargetPortfolio, ...]:
    """把「每个信号日的分数表」转成目标权重序列（A2 的调度产物）。

    信号日必须严格递增；同一日期重复出现会抛错（重复调仓等于改变了持有期）。
    """
    if not scores_by_date:
        return ()
    dates = [pd.Timestamp(d) for d in scores_by_date]
    if len(set(dates)) != len(dates):
        raise ValueError("scores_by_date 含重复信号日")
    ordered = sorted(dates)

    schedule: list[TargetPortfolio] = []
    for d in ordered:
        weights = equal_weight_targets(
            scores_by_date[d],
            top_fraction=top_fraction,
            min_assets=min_assets,
            max_weight_per_asset=max_weight_per_asset,
        )
        schedule.append(TargetPortfolio(signal_date=d, weights=weights))
    return tuple(schedule)


def validate_schedule(
    schedule: Sequence[TargetPortfolio],
    *,
    symbols: Sequence[str],
) -> None:
    """校验调度与标的池一致（在执行前拦掉「选了池外标的」这种错误）。

    这类错误如果留到回测循环里，表现是「某只权重永远买不到」——
    净值看起来正常，但组合其实少了持仓，很难被发现。
    """
    pool = set(symbols)
    prev: pd.Timestamp | None = None
    for i, target in enumerate(schedule):
        if prev is not None and target.signal_date <= prev:
            raise ValueError(
                f"第 {i + 1} 个信号日 {target.signal_date} 未晚于前一个 {prev}；"
                "调仓日必须严格递增"
            )
        prev = target.signal_date
        outside = set(target.weights) - pool
        if outside:
            raise ValueError(f"信号日 {target.signal_date} 含池外标的：{sorted(outside)}")
