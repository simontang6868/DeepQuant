"""阶段 4：重点历史阶段的案例检查。

方法论说明（防止事后叙事）：
- 阶段区间是**预先给定**的研究窗口（对应常见的市场讨论年份），
  区间内的最低点由**数据**计算得出（不用记忆里的点位）。
- "信号是否领先"的判据：信号首次出现日是否早于该区间的最低价日。
- "信号是否过早"的判据：信号后到区间最低价日之间是否出现显著回撤
  （用信号后的最大不利变动 MAE 衡量）。
- 只报告**所有满足规则的信号事件**，不挑选成功案例。
- 明确指出：即便信号领先，也不能反推指标具有预测能力——必须结合
  全样本与样本外结果一起判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ResearchConfig

#: 预先定义的研究区间（起止均为观测日，含端点）
CASE_WINDOWS: dict[str, tuple[str, str]] = {
    "2008 金融危机及修复": ("2008-01-01", "2009-12-31"),
    "2014 前后低估值": ("2014-01-01", "2015-12-31"),
    "2018-2019 低迷与修复": ("2018-01-01", "2019-12-31"),
    "2024 行情之前": ("2024-01-01", "2024-12-31"),
}


@dataclass
class CaseResult:
    """一个历史阶段的检查结果。"""

    case_name: str
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    trough_date: pd.Timestamp | None = None
    trough_price: float | None = None
    #: 在区间内**首次出现**的信号事件数
    n_events: int = 0
    #: 区间内**处于信号状态**（事件区间与窗口有交集）的事件数
    n_events_active: int = 0
    events: list[dict] = field(default_factory=list)
    first_signal_date: pd.Timestamp | None = None
    signal_before_trough: bool | None = None
    data_available: bool = True
    #: 该区间内历史百分位首个可用的日期；None 表示区间起点即已可用
    #: 若非 None 且晚于 window_start，则区间前段**无法验证**，不能记为「无信号」
    pct_available_from: pd.Timestamp | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "case_name": self.case_name,
            "window_start": str(self.window_start.date()),
            "window_end": str(self.window_end.date()),
            "trough_date": None if self.trough_date is None else str(self.trough_date.date()),
            "trough_price": self.trough_price,
            "n_events": self.n_events,
            "n_events_active": self.n_events_active,
            "events": self.events,
            "first_signal_date": (
                None if self.first_signal_date is None else str(self.first_signal_date.date())
            ),
            "signal_before_trough": self.signal_before_trough,
            "data_available": self.data_available,
            "pct_available_from": (
                None if self.pct_available_from is None else str(self.pct_available_from.date())
            ),
            "note": self.note,
        }


def check_cases(
    indicator_frame: pd.DataFrame,
    event_df: pd.DataFrame,
    cfg: ResearchConfig,
) -> list[CaseResult]:
    """对每个预定义历史阶段做检查。"""
    results: list[CaseResult] = []
    prices = indicator_frame["price"].astype("float64")

    # 指标首个可用日期（百分位有值的第一天）；早于此的区间无法验证
    pct = indicator_frame["percentile"] if "percentile" in indicator_frame else pd.Series(dtype=float)
    first_valid = pct.dropna().index.min() if pct.notna().any() else None

    for name, (s, e) in CASE_WINDOWS.items():
        start, end = pd.Timestamp(s), pd.Timestamp(e)
        seg = prices[(prices.index >= start) & (prices.index <= end)].dropna()
        res = CaseResult(case_name=name, window_start=start, window_end=end)
        if seg.empty:
            res.data_available = False
            res.note = "该区间无有效价格数据，无法验证"
            results.append(res)
            continue

        trough_date = seg.idxmin()
        res.trough_date = trough_date
        res.trough_price = float(seg.min())

        # 区间前段百分位不可用：必须单独标记，否则会把「无法验证」误报成「无信号」
        if first_valid is not None and start < first_valid <= end:
            res.pct_available_from = pd.Timestamp(first_valid)

        if first_valid is not None and end < first_valid:
            res.data_available = False
            res.note = (
                f"该区间早于指标首个有效日期（{first_valid.date()}），"
                "历史百分位尚未可用，无法验证"
            )
            results.append(res)
            continue

        ev = event_df.copy()
        if "signal_date" in ev.columns and len(ev):
            sd = pd.to_datetime(ev["signal_date"])
            ed = pd.to_datetime(ev.get("last_signal_date", ev["signal_date"]))
            sub = ev[(sd >= start) & (sd <= end)]
            # 事件区间与窗口有交集即视为"该区间处于信号状态"
            active = ev[(sd <= end) & (ed >= start)]
            res.n_events_active = int(len(active))
        else:
            sub = ev.iloc[0:0]
        res.n_events = int(len(sub))

        if len(sub):
            res.first_signal_date = pd.Timestamp(pd.to_datetime(sub["signal_date"]).min())
            res.signal_before_trough = bool(res.first_signal_date < trough_date)
            for _, row in sub.iterrows():
                res.events.append(
                    {
                        "signal_date": str(pd.Timestamp(row["signal_date"]).date()),
                        "exec_date": (
                            None if pd.isna(row.get("exec_date"))
                            else str(pd.Timestamp(row["exec_date"]).date())
                        ),
                        "spread_at_signal": _f(row.get("spread_at_signal")),
                        "percentile_at_signal": _f(row.get("percentile_at_signal")),
                        "pe_at_signal": _f(row.get("pe_at_signal")),
                        "bond_10y_at_signal": _f(row.get("bond_10y_at_signal")),
                        "ret_20td": _f(row.get("ret_20td")),
                        "ret_60td": _f(row.get("ret_60td")),
                        "ret_120td": _f(row.get("ret_120td")),
                        "ret_250td": _f(row.get("ret_250td")),
                        "dd_250td": _f(row.get("dd_250td")),
                        "mae_250td": _f(row.get("mae_250td")),
                        "immature_any": bool(row.get("immature_any", False)),
                    }
                )
        else:
            if res.n_events_active:
                res.note = (
                    f"该区间内没有**首次出现**的信号事件；但有 {res.n_events_active} 个事件"
                    "的信号区间覆盖该窗口（信号起始更早，已在更早的月份触发）"
                )
            else:
                res.note = "该区间内没有满足规则的信号事件"
            # 区间前段百分位不可用时，不能把这段记成「无信号」——它是「无法验证」
            if res.pct_available_from is not None:
                res.note = (
                    f"**该区间前段无法验证**：历史百分位自 "
                    f"{res.pct_available_from.date()} 起才可用，"
                    f"{start.date()} ~ {res.pct_available_from.date()} 这段没有百分位读数，"
                    f"既不能算作有信号、也不能算作无信号。"
                    f"{res.pct_available_from.date()} 之后：{res.note}"
                )
        # 月频采样可能无法捕获月内低点，这一点必须披露
        caveat = "最低点基于月频观测计算，可能遗漏月内低点"
        res.note = f"{res.note}。{caveat}。"
        results.append(res)
    return results


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return float(v)
