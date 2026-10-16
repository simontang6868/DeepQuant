"""多信号组合层（Step 8.4'）—— 纯加法旁路，不改动既有策略与回测引擎。

把多个策略的信号按投票规则合成为**一条** `pd.Series`，可直接喂给既有引擎
（`generate_signals` 的原契约）。引擎默认不读取本模块，两者无代码依赖。
投票内核在 `ensemble_voting.py`，本模块负责编排与结果封装。

三条硬边界（红线）：
- **不拟合权重。** `weights` 由调用方给定，模块内无搜索 / 优化 / 排序择优入口。
  用样本外收益去挑权重或 `min_sources` 违反 R2 / R11。
- **不评价。** 本模块不 import `analysis/signal_metrics`。
- **不改引擎时序。** `valid_until` 沿用「T 收盘 → T+1 开盘」的 1 日有效期。

诚实性：合成信号的 `strength` 是**一致性比例**（共识度），不是标定胜率；
**组合不承诺 alpha**，多个无 alpha 的策略组合起来依然无 alpha。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from deepquant.strategies.base import Signal
from deepquant.strategies.ensemble_voting import (
    COMBINE_METHODS,
    MAJORITY,
    UNANIMOUS,
    WEIGHTED,
    collect_votes,
    decide,
    validate_inputs,
    validate_trading_days,
    validate_weights,
)
from deepquant.strategies.signals import (
    DEFAULT_VALID_DAYS,
    SignalEvent,
    events_from_signals,
)

__all__ = [
    "MAJORITY",
    "WEIGHTED",
    "UNANIMOUS",
    "COMBINE_METHODS",
    "EnsembleResult",
    "combine_events",
    "combine_series",
]


@dataclass(frozen=True)
class EnsembleResult:
    """多信号组合结果。

    Attributes:
        signals: 合成信号序列，索引为交易日历，取值 ∈ {-1, 0, 1}。
        events: 结构化事件列表（仅可操作信号，HOLD 不产出事件）。
        source: 合成源标识，形如 `ensemble[majority](ma_cross,mr)`。
        method: 使用的组合方法。
        votes: 逐日投票明细，行 = 交易日，列 = 源，值为带符号贡献
            （权重 × 方向），缺席记 0.0。供审计，**不可用于选参**。
        agreement: 逐日一致性比例（0..1），与 `events[*].strength` 同源。
    """

    signals: pd.Series
    events: list[SignalEvent] = field(default_factory=list)
    source: str = ""
    method: str = MAJORITY
    votes: pd.DataFrame = field(default_factory=pd.DataFrame)
    agreement: pd.Series = field(default_factory=pd.Series)

    @property
    def n_days(self) -> int:
        """交易日历长度。"""
        return int(len(self.signals))

    @property
    def n_actionable(self) -> int:
        """合成信号中可操作（非 HOLD）的天数。"""
        return int((self.signals != int(Signal.HOLD)).sum())

    @property
    def coverage(self) -> float:
        """可操作信号占比（0..1）。空日历返回 0.0。"""
        if self.n_days == 0:
            return 0.0
        return self.n_actionable / self.n_days


def combine_events(
    inputs: Mapping[str, Sequence[SignalEvent]],
    trading_days: pd.DatetimeIndex,
    *,
    weights: Mapping[str, float] | None = None,
    method: str = MAJORITY,
    min_sources: int = 1,
) -> EnsembleResult:
    """把多个源的信号事件合成为一条信号序列。

    Args:
        inputs: `{源名称: SignalEvent 列表}`，事件 `source` 必须与键一致。
        trading_days: 升序无重复的交易日历，所有事件日期必须落在其中。
        weights: 仅 `WEIGHTED` 可用；未列出的源默认 1.0，必须为正有限数。
            **权重由调用方给定，本函数不做任何拟合。**
        method: `MAJORITY` / `WEIGHTED` / `UNANIMOUS` 之一。
        min_sources: 当日至少几个源给出可操作信号才合成，不足则 HOLD。

    Raises:
        ValueError: 参数非法、日期不在日历、同日重复事件、名称冲突等。
        TypeError: 类型不符。
    """
    if method not in COMBINE_METHODS:
        raise ValueError(f"method 非法：{method!r}（允许 {list(COMBINE_METHODS)}）")
    if not isinstance(min_sources, int) or isinstance(min_sources, bool):
        raise TypeError(f"min_sources 必须是 int，收到 {type(min_sources)!r}")

    trading_days = validate_trading_days(trading_days)
    cleaned = validate_inputs(inputs)
    names = sorted(cleaned)

    if min_sources < 1:
        raise ValueError(f"min_sources 必须 >= 1，收到 {min_sources}")
    if min_sources > len(names):
        raise ValueError(
            f"min_sources({min_sources}) 超过源数量({len(names)})：结果必然全 HOLD，"
            "疑似调用错误"
        )

    effective_weights = validate_weights(weights, names, method)
    positions = pd.Series(range(len(trading_days)), index=trading_days)
    votes = collect_votes(cleaned, effective_weights, positions)

    source_label = f"ensemble[{method}]({','.join(names)})"
    signal_values: list[int] = []
    agreements: list[float] = []
    events: list[SignalEvent] = []

    n = len(trading_days)
    for i, day in enumerate(trading_days):
        day_votes = votes.get(day, {})
        direction, agreement = decide(day_votes, method, min_sources)
        signal_values.append(direction)
        agreements.append(agreement)

        if direction == int(Signal.HOLD):
            continue

        valid_until = (
            trading_days[i + DEFAULT_VALID_DAYS]
            if (i + DEFAULT_VALID_DAYS) < n
            else None
        )
        events.append(
            SignalEvent(
                date=day,
                direction=Signal(direction),
                strength=agreement,
                source=source_label,
                valid_until=valid_until,
                metadata={
                    "method": method,
                    "n_sources": len(day_votes),
                    "score": float(sum(day_votes.values())),
                    "sources": sorted(day_votes),
                    # 明确标注：这是共识度，不是标定概率
                    "strength_meaning": "agreement_ratio",
                },
            )
        )

    vote_frame = pd.DataFrame(
        {
            name: [votes.get(day, {}).get(name, 0.0) for day in trading_days]
            for name in names
        },
        index=trading_days,
        dtype="float64",
    )

    return EnsembleResult(
        signals=pd.Series(signal_values, index=trading_days, dtype="int8"),
        events=events,
        source=source_label,
        method=method,
        votes=vote_frame,
        agreement=pd.Series(agreements, index=trading_days, dtype="float64"),
    )


def combine_series(
    signals: Mapping[str, pd.Series],
    *,
    weights: Mapping[str, float] | None = None,
    method: str = MAJORITY,
    min_sources: int = 1,
    include_hold: bool = False,
) -> EnsembleResult:
    """便捷入口：直接组合多个既有策略的 `pd.Series` 输出。

    各序列索引必须**完全一致**，否则报错——拒绝隐式对齐，
    避免把不同交易日历的信号悄悄拼在一起。

    Args:
        signals: `{源名称: 信号序列}`，取值 ∈ {-1, 0, 1}。
        weights / method / min_sources: 同 `combine_events`。
        include_hold: 透传给 `events_from_signals`。默认 False。
    """
    if not isinstance(signals, Mapping) or not signals:
        raise ValueError("signals 必须是非空 Mapping[str, pd.Series]")

    items = list(signals.items())
    first_name, first_series = items[0]
    if not isinstance(first_series, pd.Series):
        raise TypeError(
            f"源 {first_name!r} 的值必须是 pd.Series，收到 {type(first_series)!r}"
        )

    for name, series in items[1:]:
        if not isinstance(series, pd.Series):
            raise TypeError(f"源 {name!r} 的值必须是 pd.Series，收到 {type(series)!r}")
        if not series.index.equals(first_series.index):
            raise ValueError(
                f"源 {name!r} 的索引与 {first_name!r} 不一致：拒绝隐式对齐，"
                "请先统一交易日历"
            )

    trading_days = pd.DatetimeIndex(first_series.index)
    inputs = {
        name: events_from_signals(series, source=name, include_hold=include_hold)
        for name, series in items
    }
    return combine_events(
        inputs,
        trading_days,
        weights=weights,
        method=method,
        min_sources=min_sources,
    )
