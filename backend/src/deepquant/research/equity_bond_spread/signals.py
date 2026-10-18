"""股债利差研究：信号生成与事件合并。

规则（预先定义，不随结果调整）：
1. 当股债利差的历史百分位 >= 阈值时，该观测标记为候选信号。
2. 连续（或间隔小于 ``min_gap_obs``）的候选观测合并为**一个事件**，
   避免把同一段行情重复计成大量独立机会。
3. 事件的执行时点 = 信号首次出现后的第 ``exec_lag_obs`` 个观测
   （默认下一个观测），**禁止**用信号当日收盘价成交（那是未来函数）。
4. 未来收益一律从执行时点之后开始计算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ResearchConfig


def trading_days_to_obs(trading_days: int, obs_per_year: int) -> int:
    """把「交易日」窗口换算为当前观测频率下的观测数。

    例：月频（12 obs/年）下 20 交易日 ≈ 0.95 → 1 个观测（约 1 个月）。
    换算在报告中必须披露：月频数据的前瞻窗口是「近似交易日」口径。
    """
    if trading_days <= 0:
        raise ValueError(f"trading_days 必须 > 0，收到 {trading_days}")
    if obs_per_year <= 0:
        raise ValueError(f"obs_per_year 必须 > 0，收到 {obs_per_year}")
    return max(1, int(round(trading_days / 252.0 * obs_per_year)))


def merge_signal_runs(
    flag: pd.Series,
    *,
    min_gap_obs: int = 1,
) -> list[tuple[int, int]]:
    """把布尔/0-1 信号序列合并为连续区间（含端点索引）。

    两段信号之间的"非信号"观测数小于 ``min_gap_obs`` 时合并为同一区间。

    Args:
        flag: 0/1 或布尔序列（NaN 视为非信号）。
        min_gap_obs: 允许的间隔观测数；间隔 < 该值则合并。

    Returns:
        ``[(start_pos, end_pos), ...]``，位置是 ``flag`` 的整数下标。
    """
    if not isinstance(flag, pd.Series):
        raise TypeError(f"flag 必须是 pd.Series，收到 {type(flag)!r}")
    if min_gap_obs < 1:
        raise ValueError(f"min_gap_obs 必须 >= 1，收到 {min_gap_obs}")

    arr = flag.fillna(0).astype("int64").to_numpy()
    on_positions = np.flatnonzero(arr == 1)
    if on_positions.size == 0:
        return []

    runs: list[tuple[int, int]] = []
    start = int(on_positions[0])
    prev = int(on_positions[0])
    for p in on_positions[1:]:
        p = int(p)
        if p - prev <= min_gap_obs:
            prev = p
        else:
            runs.append((start, prev))
            start = p
            prev = p
    runs.append((start, prev))
    return runs


@dataclass
class SpreadEvent:
    """一次合并后的股债利差信号事件。"""

    event_id: int
    signal_date: pd.Timestamp
    last_signal_date: pd.Timestamp
    exec_date: pd.Timestamp | None
    exec_price: float | None
    n_signal_obs: int
    spread_at_signal: float | None
    spread_max_in_event: float | None
    percentile_at_signal: float | None
    percentile_max_in_event: float | None
    bond_10y_at_signal: float | None
    pe_at_signal: float | None
    history_obs: float | None
    #: True 表示信号后没有可执行观测（例如落在数据末尾）
    no_execution: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "event_id": self.event_id,
            "signal_date": self.signal_date.isoformat() if self.signal_date is not None else None,
            "last_signal_date": (
                self.last_signal_date.isoformat() if self.last_signal_date is not None else None
            ),
            "exec_date": self.exec_date.isoformat() if self.exec_date is not None else None,
            "exec_price": self.exec_price,
            "n_signal_obs": self.n_signal_obs,
            "spread_at_signal": self.spread_at_signal,
            "spread_max_in_event": self.spread_max_in_event,
            "percentile_at_signal": self.percentile_at_signal,
            "percentile_max_in_event": self.percentile_max_in_event,
            "bond_10y_at_signal": self.bond_10y_at_signal,
            "pe_at_signal": self.pe_at_signal,
            "history_obs": self.history_obs,
            "no_execution": self.no_execution,
        }
        d.update(self.metadata)
        return d


def extract_events(frame: pd.DataFrame, cfg: ResearchConfig) -> list[SpreadEvent]:
    """从指标面板抽取合并后的信号事件。

    Args:
        frame: ``build_indicator_frame`` 的输出，需含
            ``signal`` / ``spread_pct`` / ``percentile`` / ``price`` 等列。
        cfg: 研究配置。
    """
    if "signal" not in frame.columns:
        raise ValueError("frame 缺少 signal 列")
    if len(frame) == 0:
        return []

    obs_per_year = cfg.data.obs_per_year()
    _ = obs_per_year  # 频率在 trading_days_to_obs 中使用，这里保留以备扩展

    runs = merge_signal_runs(frame["signal"], min_gap_obs=cfg.min_gap_obs)

    events: list[SpreadEvent] = []
    for i, (s, e) in enumerate(runs, start=1):
        seg = frame.iloc[s : e + 1]
        signal_date = frame.index[s]
        last_signal_date = frame.index[e]

        exec_pos = s + cfg.exec_lag_obs
        if exec_pos >= len(frame.index):
            exec_date = None
            exec_price = None
            no_exec = True
        else:
            exec_date = frame.index[exec_pos]
            exec_price = frame["price"].iloc[exec_pos]
            exec_price = None if pd.isna(exec_price) else float(exec_price)
            no_exec = exec_price is None

        def _val(col: str, pos: int) -> float | None:
            v = frame[col].iloc[pos]
            return None if pd.isna(v) else float(v)

        events.append(
            SpreadEvent(
                event_id=i,
                signal_date=signal_date,
                last_signal_date=last_signal_date,
                exec_date=exec_date,
                exec_price=exec_price,
                n_signal_obs=int(e - s + 1),
                spread_at_signal=_val("spread_pct", s),
                spread_max_in_event=(
                    float(seg["spread_pct"].max()) if seg["spread_pct"].notna().any() else None
                ),
                percentile_at_signal=_val("percentile", s),
                percentile_max_in_event=(
                    float(seg["percentile"].max()) if seg["percentile"].notna().any() else None
                ),
                bond_10y_at_signal=_val("bond_10y", s),
                pe_at_signal=_val("pe", s),
                history_obs=_val("history_obs", s),
                no_execution=no_exec,
            )
        )
    return events


def events_to_frame(events: list[SpreadEvent]) -> pd.DataFrame:
    """事件列表 → DataFrame（便于导出 CSV）。"""
    if not events:
        return pd.DataFrame(
            columns=[
                "event_id", "signal_date", "last_signal_date", "exec_date",
                "exec_price", "n_signal_obs", "spread_at_signal",
                "percentile_at_signal", "bond_10y_at_signal", "pe_at_signal",
                "no_execution",
            ]
        )
    return pd.DataFrame([e.to_dict() for e in events])
