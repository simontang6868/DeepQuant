"""多信号组合的投票内核（Step 8.4'）。

从 `ensemble.py` 拆出的纯函数层，职责三件：输入校验、逐日投票收集、投票裁决。
对外 API 请走 `ensemble.combine_events` / `ensemble.combine_series`；
本模块的接口不承诺稳定性。

诚实性约定（裁决语义，别想当然）：
- **缺席不等于 HOLD。** 某源在某交易日没有事件，则不计入分母——不是投弃权票。
- **HOLD 事件不参与投票**（`is_actionable` 为 False 的事件被跳过）。
- **平票与方向冲突一律裁决为 HOLD。** 源数为偶数时平票频繁出现，属投票法固有性质。
- **一致性比例 = |加权净票| / 加权总票**，取值 0..1。它是共识度，
  不是标定过的胜率，也不是概率。
- 本模块**不做任何权重拟合**，权重一律由调用方给定。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pandas as pd

from deepquant.strategies.base import Signal
from deepquant.strategies.signals import SignalEvent

#: 等权投票：每个源一票，忽略权重。
MAJORITY = "majority"

#: 加权投票：按权重加权求和。
WEIGHTED = "weighted"

#: 一致同意：当日所有有票的源方向必须完全相同，否则 HOLD。
UNANIMOUS = "unanimous"

COMBINE_METHODS = (MAJORITY, WEIGHTED, UNANIMOUS)

__all__ = [
    "MAJORITY",
    "WEIGHTED",
    "UNANIMOUS",
    "COMBINE_METHODS",
    "validate_trading_days",
    "validate_inputs",
    "validate_weights",
    "collect_votes",
    "decide",
]


def validate_trading_days(trading_days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """校验交易日历：必须是升序且无重复的 `pd.DatetimeIndex`。"""
    if not isinstance(trading_days, pd.DatetimeIndex):
        raise TypeError(
            f"trading_days 必须是 pd.DatetimeIndex，收到 {type(trading_days)!r}"
        )
    if trading_days.has_duplicates or not trading_days.is_monotonic_increasing:
        raise ValueError("trading_days 必须升序且无重复")
    return trading_days


def validate_inputs(
    inputs: Mapping[str, Sequence[SignalEvent]],
) -> dict[str, list[SignalEvent]]:
    """校验输入映射：键为源名称，值为该源的 `SignalEvent` 列表。

    事件自身的 `source` 必须与键一致——不一致时报错，不猜测归属。
    """
    if not isinstance(inputs, Mapping):
        raise TypeError(
            f"inputs 必须是 Mapping[str, Sequence[SignalEvent]]，收到 {type(inputs)!r}"
        )
    if not inputs:
        raise ValueError("inputs 不能为空：至少需要一个信号源")

    cleaned: dict[str, list[SignalEvent]] = {}
    for name, events in inputs.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"源名称必须是非空字符串，收到 {name!r}")
        if name in cleaned:
            raise ValueError(f"源名称重复：{name!r}")
        for event in events:
            if not isinstance(event, SignalEvent):
                raise TypeError(
                    f"源 {name!r} 的元素必须是 SignalEvent，收到 {type(event)!r}"
                )
            if event.source != name:
                raise ValueError(
                    f"事件 source({event.source!r}) 与输入键({name!r}) 不一致："
                    "拒绝猜测归属，请修正调用"
                )
        cleaned[name] = list(events)
    return cleaned


def validate_weights(
    weights: Mapping[str, float] | None,
    names: Sequence[str],
    method: str,
) -> dict[str, float]:
    """解析权重；未列出的源默认 1.0。仅 `WEIGHTED` 方法允许传权重。"""
    if weights is None:
        return {name: 1.0 for name in names}

    if method != WEIGHTED:
        raise ValueError(
            f"weights 仅对 {WEIGHTED!r} 方法生效，当前方法为 {method!r}："
            "为避免静默忽略，这里直接报错"
        )

    unknown = sorted(set(weights) - set(names))
    if unknown:
        raise ValueError(f"weights 含未知源：{unknown}（已知 {sorted(names)}）")

    resolved: dict[str, float] = {}
    for name in names:
        value = float(weights.get(name, 1.0))
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"源 {name!r} 的权重必须为正有限数，收到 {value!r}")
        resolved[name] = value
    return resolved


def collect_votes(
    cleaned: Mapping[str, list[SignalEvent]],
    effective_weights: Mapping[str, float],
    positions: pd.Series,
) -> dict[pd.Timestamp, dict[str, float]]:
    """按交易日收集各源的带符号贡献 `权重 × 方向`。

    同时校验：信号日期必须落在交易日历内；同一源同一日不得有多条事件
    （多于一条时组合语义未定义，要求调用方先自行去重）。
    """
    votes: dict[pd.Timestamp, dict[str, float]] = {}
    seen: set[tuple[str, pd.Timestamp]] = set()

    for name, events in cleaned.items():
        weight = effective_weights[name]
        for event in events:
            if not event.is_actionable:
                continue  # HOLD 不参与投票
            if event.date not in positions.index:
                raise ValueError(
                    f"源 {name!r} 的信号日期 {event.date} 不在交易日历中："
                    "拒绝对齐到不存在的交易日"
                )
            key = (name, event.date)
            if key in seen:
                raise ValueError(
                    f"源 {name!r} 在 {event.date} 有多条事件：组合语义未定义，"
                    "请先在该源内部去重（例如取最后一条）"
                )
            seen.add(key)
            votes.setdefault(event.date, {})[name] = weight * float(
                int(event.direction)
            )
    return votes


def decide(
    day_votes: Mapping[str, float],
    method: str,
    min_sources: int,
) -> tuple[int, float]:
    """裁决单日方向，返回 `(direction, agreement)`。

    `agreement` 为一致性比例 0..1；不足 `min_sources` 时返回 `(HOLD, 0.0)`。
    """
    if len(day_votes) < min_sources:
        return int(Signal.HOLD), 0.0

    signed = list(day_votes.values())
    total_weight = sum(abs(v) for v in signed)
    score = sum(signed)

    if method == UNANIMOUS:
        directions = {1 if v > 0 else -1 for v in signed}
        if len(directions) != 1:
            return int(Signal.HOLD), 0.0
        return int(directions.pop()), 1.0

    if total_weight <= 0.0:
        return int(Signal.HOLD), 0.0
    if score > 0:
        return int(Signal.BUY), abs(score) / total_weight
    if score < 0:
        return int(Signal.SELL), abs(score) / total_weight
    return int(Signal.HOLD), 0.0  # 平票 / 完全冲突，保守不动作
