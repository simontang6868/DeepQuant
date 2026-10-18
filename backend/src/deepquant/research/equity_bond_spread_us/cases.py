"""历史案例核查。

选案例的原则：**看结果之前就定好**，按「美国股市公认的关键时点」挑，
不按「信号表现好/坏」挑。每个案例输出：当时利差、百分位、之后 1/5/10 年真实收益。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

#: 预登记的案例时点（月末）
CASE_MONTHS = (
    ("1929-09-30", "大萧条前夜"),
    ("1932-06-30", "大萧条底部"),
    ("1937-03-31", "二次衰退前"),
    ("1974-10-31", "石油危机熊市底"),
    ("1982-07-31", "长期牛市起点"),
    ("1999-12-31", "互联网泡沫顶"),
    ("2007-10-31", "金融危机前"),
    ("2009-03-31", "金融危机底"),
    ("2020-03-31", "疫情冲击"),
    ("2026-09-30", "当前（研究截止）"),
)


@dataclass
class CaseResult:
    month: str
    label: str
    spread_pct: float | None
    percentile: float | None
    cape: float | None
    y10: float | None
    ey_pct: float | None
    fwd_12m_pct: float | None
    fwd_60m_pct: float | None
    fwd_120m_pct: float | None
    signal_on: bool | None
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_cases(
    frame: pd.DataFrame,
    threshold: float = 90.0,
    predictor: str = "spread_cape",
) -> list[CaseResult]:
    """逐个案例取值；缺失则如实标记，不用邻近月份替代。"""
    pct_col = f"{predictor}_pct"
    out: list[CaseResult] = []
    for month, label in CASE_MONTHS:
        ts = pd.Timestamp(month)
        if ts > frame.index.max():
            out.append(
                CaseResult(month=month, label=label, spread_pct=None, percentile=None,
                           cape=None, y10=None, ey_pct=None, fwd_12m_pct=None,
                           fwd_60m_pct=None, fwd_120m_pct=None, signal_on=None,
                           note="超出数据区间")
            )
            continue
        if ts not in frame.index:
            # 用不晚于该月的最后一个观测（并标注）
            prior = frame.index[frame.index <= ts]
            if len(prior) == 0:
                continue
            ts_used = prior[-1]
            note = f"无该月数据，使用 {ts_used.date()}"
        else:
            ts_used = ts
            note = ""

        row = frame.loc[ts_used]
        pct = row.get(pct_col, np.nan)
        fwd = {}
        for h in (12, 60, 120):
            col = f"fwd_real_{h}"
            v = row.get(col, np.nan)
            fwd[h] = None if not np.isfinite(v) else round(float(v) * 100.0, 2)
        out.append(
            CaseResult(
                month=str(ts_used.date()),
                label=label,
                spread_pct=round(float(row["spread_cape"]), 2) if np.isfinite(row["spread_cape"]) else None,
                percentile=round(float(pct), 2) if np.isfinite(pct) else None,
                cape=round(float(row["cape"]), 2) if np.isfinite(row["cape"]) else None,
                y10=round(float(row["y10"]), 2) if np.isfinite(row["y10"]) else None,
                ey_pct=round(float(row["ey_cape"]), 2) if np.isfinite(row["ey_cape"]) else None,
                fwd_12m_pct=fwd[12],
                fwd_60m_pct=fwd[60],
                fwd_120m_pct=fwd[120],
                signal_on=bool(np.isfinite(pct) and pct >= threshold),
                note=note,
            )
        )
    return out
