"""P0-4 真实账本测试：验证会计恒等式与「买不进一手」的边界。

这三条是最容易悄悄错的地方：
1. 费用（最小 5 元佣金在小额调仓时主导）；
2. `nav == 股票市值 + 现金` 逐日恒等（写错就会凭空多钱）；
3. 整手约束：预算 4,000 元、股价 45 元 → **一手都买不进**，必须留现金而不是买碎股。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import p04_position_dd as p4  # noqa: E402


def test_fee_min_commission_dominates():
    # 4,000 元买入：万 2.5 = 1.0 元 < 5 元 → 按 5 元
    buy = p4.fee(4000.0, "buy")
    assert buy == pytest.approx(5.0 + 4000 * 1e-5)
    # 卖出多印花税 0.05%
    sell = p4.fee(4000.0, "sell")
    assert sell == pytest.approx(5.0 + 4000 * 1e-5 + 4000 * 0.0005)
    # 大额时退回比例佣金
    big = p4.fee(2_000_000.0, "buy")
    assert big == pytest.approx(2_000_000 * 0.00025 + 2_000_000 * 1e-5)


def test_lot_too_big_leaves_cash():
    """20% 仓位 / 10 万 / 5 只 → 每只 4,000 元；股价 45 元时一手 4,500 元 → 买不进。"""
    cal = ["20240131", "20240201", "20240229", "20240301"]
    cal_pos = {d: i for i, d in enumerate(cal)}
    months = ["20240131", "20240229"]
    buy, nxt = ["20240201", "20240301"], ["20240301", "20240301"]
    px = {c: np.full(len(cal), 45.0) for c in "ABCDE"}
    tabs = {c: (np.array(["20240201", "20240301"]), np.array([True, True]), np.array([True, True]))
            for c in "ABCDE"}
    holds = [list("ABCDE"), list("ABCDE")]
    df = p4.simulate(holds, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal, "rebal")
    row = df.iloc[0]
    assert row["stock_value"] == pytest.approx(0.0), "股价 45 元时 4,000 元买不进一手，必须全现金"


def test_nav_identity_holds_daily():
    cal = ["20240131", "20240201", "20240229", "20240301"]
    cal_pos = {d: i for i, d in enumerate(cal)}
    months = ["20240131", "20240229"]
    buy, nxt = ["20240201", "20240301"], ["20240301", "20240301"]
    px = {"A": np.array([10, 10, 11, 12], float), "B": np.array([20, 20, 19, 21], float)}
    tabs = {c: (np.array(["20240201", "20240301"]), np.array([True, True]), np.array([True, True]))
            for c in "AB"}
    holds = [["A", "B"], ["A", "B"]]
    df = p4.simulate(holds, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal, "rebal")
    assert np.allclose(df["nav"].to_numpy(), df["stock_value"].to_numpy() + df["cash"].to_numpy())
    # 股票部分确实持有（每股 10/20 元，4,000 元预算都买得起）
    assert df["stock_value"].iloc[0] > 0
