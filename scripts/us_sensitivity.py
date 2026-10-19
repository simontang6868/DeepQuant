"""阈值口径敏感性：扩展窗口 vs 固定窗口、四档阈值。

为什么单独跑这一步
------------------
A 股研究的下一阶段建议是「换到 155 年长历史，预期能出 15-25 个事件」。
实测只有 7 个，而且全部落在 1950 年之前。这说明事件数量不由样本长度决定，
而由**百分位口径 + 序列非平稳性**决定。这里把两种窗口口径并排跑一遍，
把「延长样本能不能救阈值法」这个问题直接测出来。

输出：``data/equity_bond_spread_us/results/sensitivity.csv``
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from deepquant.research.equity_bond_spread_us.config import USConfig  # noqa: E402
from deepquant.research.equity_bond_spread_us.data import load_panel  # noqa: E402
from deepquant.research.equity_bond_spread_us.experiment import prepare_frame  # noqa: E402
from deepquant.research.equity_bond_spread_us.indicators import historical_percentile  # noqa: E402
from deepquant.research.equity_bond_spread_us.signals import merge_events, threshold_flag  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "equity_bond_spread_us"
RESULT_DIR = DATA_DIR / "results"


def main() -> int:
    cfg = USConfig()
    panel = load_panel(DATA_DIR / cfg.panel_csv)
    frame = panel.frame
    frame = frame[~frame["partial_month"].astype(bool)]
    frame = frame.loc[pd.Timestamp(cfg.start_date) : pd.Timestamp(cfg.end_date)]
    prepared = prepare_frame(frame, cfg)

    rows = []
    for window_type, expanding, window in (
        ("扩展窗口", True, 120),
        ("固定 120 月", False, 120),
        ("固定 240 月", False, 240),
        ("固定 60 月", False, 60),
    ):
        pct = historical_percentile(
            prepared["spread_cape"], window=window, min_obs=min(window, cfg.min_history),
            expanding=expanding,
        )
        for thr in (80.0, 85.0, 90.0, 95.0):
            flag = threshold_flag(pct, thr)
            events = merge_events(flag, prepared["spread_cape"], pct,
                                  gap=cfg.event_gap, exec_lag=cfg.exec_lag)
            pos = {ts: i for i, ts in enumerate(prepared.index)}
            fwd = prepared["fwd_real_60"]
            base = fwd.dropna()
            sig = np.array([fwd.iloc[pos[pd.Timestamp(e.signal_month)]] for e in events])
            sig = sig[np.isfinite(sig)]
            years = [int(e.signal_month[:4]) for e in events]
            rows.append(
                {
                    "window_type": window_type,
                    "threshold": thr,
                    "flagged_months": int(flag.sum()),
                    "n_events": len(events),
                    "first_event_year": min(years) if years else None,
                    "last_event_year": max(years) if years else None,
                    "events_after_1950": int(sum(1 for y in years if y >= 1950)),
                    "signal_mean_60m_pct": round(float(sig.mean() * 100), 2) if len(sig) else None,
                    "baseline_mean_60m_pct": round(float(base.mean() * 100), 2),
                    "diff_60m_pct": round(float((sig.mean() - base.mean()) * 100), 2)
                    if len(sig) else None,
                }
            )

    df = pd.DataFrame(rows)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULT_DIR / "sensitivity.csv", index=False, encoding="utf-8")
    print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
