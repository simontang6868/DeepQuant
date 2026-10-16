"""结构化信号层（Step 8.1）—— 纯加法，不改动既有 `generate_signals` 契约。

设计约束（源自项目红线与最小改动原则）：
- R7 不弱化既有测试：既有 `Signal(IntEnum)` 与 `generate_signals -> pd.Series`
  的契约保持不变，本模块不要求任何策略改写返回值。
- 最小改动：本模块是旁路（side-channel），回测引擎**默认不读取**它。
  引入它的目的是让「信号层面可比 / 可组合 / 可评价」这三类研究成立，
  而不是替换现有撮合链路。
- 命名决策（关键）：`deepquant/strategies/base.py` **已经占用了 `Signal` 这个
  名字**（IntEnum：SELL=-1 / HOLD=0 / BUY=1）。因此结构化信号对象命名为
  `SignalEvent`。若直接新建同名 `Signal` dataclass，会遮蔽既有 IntEnum，
  导致引擎 `validate_signals` 与全部策略测试失败。

诚实性约定：
- `strength` 表示信号置信度（0..1）。现有 MA / MR 策略是**二值**信号，
  没有强度概念，一律填 1.0 —— 不虚构强度，不把「二值」伪装成「有置信度」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from deepquant.strategies.base import Signal
from deepquant.market.rules import Side

#: 引擎当前时序：T 日收盘产生信号，最早 T+1 开盘成交。
#: 因此默认有效期为 1 个交易日，与现有引擎行为严格一致。
DEFAULT_VALID_DAYS = 1

_ALLOWED_DIRECTIONS = (int(Signal.SELL), int(Signal.HOLD), int(Signal.BUY))


@dataclass(frozen=True)
class SignalEvent:
    """单条结构化信号事件。

    Attributes:
        date: 信号产生日（收盘后）。
        direction: 方向，复用既有 `Signal` IntEnum，保证取值集合与引擎一致。
        strength: 置信度，0..1。二值信号恒为 1.0。
        source: 产生信号的策略标识（通常为 `Strategy.name`）。
        valid_until: 有效期截止日（含）。None 表示「无可执行日」，
            对应引擎中「最后一个交易日的信号不生成订单」的行为。
        metadata: 策略私有信息（如 z-score、均线差值），仅供研究层使用。
    """

    date: pd.Timestamp
    direction: Signal
    strength: float
    source: str
    valid_until: pd.Timestamp | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.date, pd.Timestamp):
            raise TypeError(f"date 必须是 pd.Timestamp，收到 {type(self.date)!r}")
        if int(self.direction) not in _ALLOWED_DIRECTIONS:
            raise ValueError(
                f"direction 非法：{self.direction!r}（允许 {list(_ALLOWED_DIRECTIONS)}）"
            )
        if not (0.0 <= float(self.strength) <= 1.0):
            raise ValueError(f"strength 必须在 0..1 之间，收到 {self.strength!r}")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source 必须是非空字符串（策略标识）")
        if self.valid_until is not None and self.valid_until < self.date:
            raise ValueError(
                f"valid_until({self.valid_until}) 不能早于 date({self.date})"
            )

    @property
    def is_actionable(self) -> bool:
        """是否为可产生订单的信号（非 HOLD）。"""
        return int(self.direction) != int(Signal.HOLD)

    @property
    def side(self) -> Side | None:
        """方向对应的买卖侧；HOLD 返回 None。"""
        if int(self.direction) == int(Signal.BUY):
            return Side.BUY
        if int(self.direction) == int(Signal.SELL):
            return Side.SELL
        return None

    def to_dict(self) -> dict[str, Any]:
        """可序列化字典（用于持久化 / API 输出）。"""
        return {
            "date": self.date.isoformat(),
            "direction": int(self.direction),
            "strength": float(self.strength),
            "source": self.source,
            "valid_until": None if self.valid_until is None else self.valid_until.isoformat(),
            "metadata": dict(self.metadata),
        }


def events_from_signals(
    signals: pd.Series,
    *,
    source: str,
    strength: float | pd.Series = 1.0,
    valid_days: int = DEFAULT_VALID_DAYS,
    metadata: dict[str, Any] | None = None,
    include_hold: bool = False,
) -> list[SignalEvent]:
    """把既有 `pd.Series` 信号序列转换为结构化事件列表。

    Args:
        signals: 与行情同索引的信号序列，取值 ∈ {-1, 0, 1}。
        source: 产生信号的策略标识。
        strength: 标量或与 signals 同索引的序列（用于带强度的策略）。
        valid_days: 有效期交易日数。默认 1，与引擎「T 收盘 → T+1 开盘」一致。
        metadata: 附加到每条事件的策略私有信息。
        include_hold: 是否同时产出 HOLD 事件（默认否，HOLD 不是「事件」）。
    """
    if not isinstance(signals, pd.Series):
        raise TypeError(f"signals 必须是 pandas.Series，收到 {type(signals)!r}")
    if valid_days < 1:
        raise ValueError(f"valid_days 必须 >= 1，收到 {valid_days}")

    index = signals.index
    n = len(index)
    strength_is_series = isinstance(strength, pd.Series)

    if strength_is_series and not strength.index.equals(index):
        raise ValueError("strength 序列索引必须与 signals 索引一致")

    events: list[SignalEvent] = []
    for i in range(n):
        raw = signals.iloc[i]
        if pd.isna(raw):
            raise ValueError(f"signals 在 {index[i]} 处为 NaN：信号不允许缺失")
        value = int(raw)
        if value not in _ALLOWED_DIRECTIONS:
            raise ValueError(
                f"signals 在 {index[i]} 处取值非法：{raw!r}（允许 {list(_ALLOWED_DIRECTIONS)}）"
            )
        if value == int(Signal.HOLD) and not include_hold:
            continue

        if strength_is_series:
            s_value = float(strength.iloc[i])
        else:
            s_value = float(strength)

        # 有效期：默认只到下一个交易日；末日信号无可执行日 → None（与引擎一致）
        valid_until = index[i + valid_days] if (i + valid_days) < n else None

        events.append(
            SignalEvent(
                date=index[i],
                direction=Signal(value),
                strength=s_value,
                source=source,
                valid_until=valid_until,
                metadata=dict(metadata or {}),
            )
        )
    return events


@dataclass(frozen=True)
class OrderPlan:
    """由信号派生的订单计划（尚未确定数量）。

    quantity 为 None 表示「数量由执行层按当日资金与手数规则决定」，
    与引擎中 `_target_buy_quantity` / `_affordable_buy_quantity` 的职责保持一致。
    本层不预先计算数量，避免与账户状态耦合（保持纯函数）。
    """

    signal_date: pd.Timestamp
    planned_exec_date: pd.Timestamp
    side: Side
    source: str
    strength: float = 1.0
    quantity: int | None = None

    def __post_init__(self) -> None:
        if self.planned_exec_date <= self.signal_date:
            raise ValueError(
                f"planned_exec_date({self.planned_exec_date}) 必须晚于 "
                f"signal_date({self.signal_date})：禁止同日成交（未来函数）"
            )
        if not (0.0 <= float(self.strength) <= 1.0):
            raise ValueError(f"strength 必须在 0..1 之间，收到 {self.strength!r}")


class SignalExecutor(ABC):
    """「信号 → 订单计划」的转换接口。

    Step 8.1 只定义接口与一个与引擎时序等价的默认实现，**不接入回测引擎**。
    是否接入、如何接入属于 Step 8.3，届时必须先证明现有内嵌转换无法满足需求。
    """

    @abstractmethod
    def plan(
        self,
        events: Sequence[SignalEvent],
        trading_days: pd.DatetimeIndex,
    ) -> list[OrderPlan]:
        """把信号事件转换为订单计划。"""


class NextOpenExecutor(SignalExecutor):
    """默认实现：T 日收盘信号 → T+1 开盘执行（与现有引擎时序一致）。

    落在最后一个交易日、或有效期截止日之后无可成交日的信号，不产出计划
    （对应引擎「最后一个交易日的信号未生成订单」）。
    """

    def plan(
        self,
        events: Sequence[SignalEvent],
        trading_days: pd.DatetimeIndex,
    ) -> list[OrderPlan]:
        if not isinstance(trading_days, pd.DatetimeIndex):
            raise TypeError(
                f"trading_days 必须是 pd.DatetimeIndex，收到 {type(trading_days)!r}"
            )
        if trading_days.has_duplicates or not trading_days.is_monotonic_increasing:
            raise ValueError("trading_days 必须升序且无重复")

        positions = pd.Series(range(len(trading_days)), index=trading_days)
        plans: list[OrderPlan] = []

        for event in events:
            if not event.is_actionable or event.side is None:
                continue
            if event.date not in positions.index:
                raise ValueError(
                    f"信号日期 {event.date} 不在交易日历中：禁止对齐到不存在的交易日"
                )
            idx = int(positions.loc[event.date])
            if idx + 1 >= len(trading_days):
                continue  # 无可执行日（与引擎一致）
            exec_date = trading_days[idx + 1]
            if event.valid_until is not None and exec_date > event.valid_until:
                continue  # 已过期
            plans.append(
                OrderPlan(
                    signal_date=event.date,
                    planned_exec_date=exec_date,
                    side=event.side,
                    source=event.source,
                    strength=event.strength,
                )
            )
        return plans
