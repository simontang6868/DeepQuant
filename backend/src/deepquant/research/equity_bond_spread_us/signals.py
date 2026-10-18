"""信号提取：阈值事件、事件合并、分位分组。

两条纪律（A 股研究中被审计确认过的）：
1. **事件合并**：连续多个月触发、或两次触发相隔很近，只算一个事件，
   否则同一波机会被反复计数，样本量虚增。
2. **信号月 ≠ 执行月**：执行月固定为信号月的下一月（``exec_lag >= 1``），
   由调用方在计算前视收益时使用。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Event:
    """一次合并后的信号事件。"""

    event_id: int
    signal_month: str            # 首次触发月（月末）
    last_signal_month: str       # 同一次事件中最后一次触发月
    n_months: int                # 事件内触发月数
    percentile_at_signal: float
    value_at_signal: float
    exec_month: str              # 执行月（signal_month + 1）
    peak_percentile: float

    def to_dict(self) -> dict:
        return asdict(self)


def threshold_flag(percentile: pd.Series, threshold: float) -> pd.Series:
    """百分位达到阈值则标记（NaN 视为未触发）。"""
    if not (0.0 < threshold <= 100.0):
        raise ValueError(f"threshold 必须在 (0, 100]，收到 {threshold}")
    flag = (percentile >= threshold).fillna(False)
    return flag.astype(bool)


def merge_events(
    flag: pd.Series,
    values: pd.Series,
    percentiles: pd.Series,
    gap: int = 12,
    exec_lag: int = 1,
) -> list[Event]:
    """把触发月合并成事件。

    合并规则：相邻两次触发之间的间隔（按观测数）小于等于 ``gap`` 即视为同一事件，
    合并后事件长度为「首次触发月 → 最后一次触发月」。
    """
    if gap < 0:
        raise ValueError("gap 必须 >= 0")
    if exec_lag < 1:
        raise ValueError("exec_lag 必须 >= 1")

    idx = flag.index
    pos = {ts: i for i, ts in enumerate(idx)}
    hits = np.flatnonzero(flag.to_numpy(dtype=bool))
    if len(hits) == 0:
        return []

    groups: list[list[int]] = [[hits[0]]]
    for h in hits[1:]:
        if h - groups[-1][-1] <= gap:
            groups[-1].append(h)
        else:
            groups.append([h])

    events: list[Event] = []
    for k, grp in enumerate(groups, start=1):
        first, last = grp[0], grp[-1]
        sig_month = idx[first]
        exec_pos = min(first + exec_lag, len(idx) - 1)
        pct_block = percentiles.iloc[first : last + 1]
        events.append(
            Event(
                event_id=k,
                signal_month=str(sig_month.date()),
                last_signal_month=str(idx[last].date()),
                n_months=len(grp),
                percentile_at_signal=float(percentiles.iloc[first]),
                value_at_signal=float(values.iloc[first]),
                exec_month=str(idx[exec_pos].date()),
                peak_percentile=float(pct_block.max()),
            )
        )
    return events


def decile_bucket(percentile: pd.Series) -> pd.Series:
    """按历史百分位分组（0-9），避免用全样本分位数造成的未来信息泄漏。

    `percentile` 本身是扩展/滚动窗口算出来的，因此分组也只依赖历史。
    """
    bucket = np.floor(percentile / 10.0)
    bucket = bucket.clip(lower=0, upper=9)
    bucket[percentile.isna()] = np.nan
    return bucket
