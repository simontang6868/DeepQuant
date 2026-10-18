"""日频执行层的单元测试（合成数据，手工可核算）。

重点验证四个硬约束：
1. 执行时点必须是信号日**之后**第一个交易日（T+1，绝不允许当日收盘成交）。
2. 持有期是**精确交易日数**，不是日历天数、也不是月数折算。
3. 日频未覆盖区间必须标记 ``daily_available=False``，不得用任何估计量填充。
4. 未成熟样本不得产生收益数字。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.daily_exec import (
    build_baseline_daily,
    buy_and_hold_daily,
    check_events_daily,
    compare_daily,
    daily_forward,
    load_daily_close,
    load_trading_calendar,
    split_segments,
    next_trading_day,
)


def _calendar(start: str, n: int) -> pd.DatetimeIndex:
    """连续 n 个工作日构成的合成交易日历。"""
    return pd.bdate_range(start, periods=n)


def _daily(start: str, closes: list[float]) -> pd.Series:
    idx = _calendar(start, len(closes))
    return pd.Series(closes, index=idx, dtype="float64", name="close")


# --------------------------------------------------------------------------- #
# 时点映射
# --------------------------------------------------------------------------- #
def test_next_trading_day_skips_weekend():
    cal = _calendar("2024-01-01", 10)
    # 2024-01-05 是周五 → 下一个交易日是 01-08 周一
    assert next_trading_day(cal, pd.Timestamp("2024-01-05")) == pd.Timestamp("2024-01-08")


def test_next_trading_day_is_strictly_after():
    """即使信号日本身是交易日，也必须返回下一日（T+1）。"""
    cal = _calendar("2024-01-01", 10)
    got = next_trading_day(cal, pd.Timestamp("2024-01-08"))
    assert got == pd.Timestamp("2024-01-09")
    assert got > pd.Timestamp("2024-01-08")


def test_next_trading_day_returns_none_at_calendar_end():
    cal = _calendar("2024-01-01", 3)
    assert next_trading_day(cal, cal[-1]) is None


# --------------------------------------------------------------------------- #
# 前瞻收益
# --------------------------------------------------------------------------- #
def test_daily_forward_executes_at_t_plus_one():
    # 信号 2024-01-01（周一），T+1 是 01-02
    daily = _daily("2024-01-01", [100.0, 110.0, 120.0, 130.0])
    cal = daily.index
    out = daily_forward(daily, cal, pd.Timestamp("2024-01-01"), 2)
    assert out.exec_date == pd.Timestamp("2024-01-02")
    assert out.exec_price == pytest.approx(110.0)
    # 持有 2 个交易日 → 01-04 收盘
    assert out.end_date == pd.Timestamp("2024-01-04")
    assert out.gross_return == pytest.approx(130.0 / 110.0 - 1.0)


def test_daily_forward_window_is_exact_trading_days_not_calendar_days():
    """20 个交易日必须正好前进 20 个观测位置。"""
    n = 40
    closes = [100.0 + i for i in range(n)]
    daily = _daily("2024-01-01", closes)
    cal = daily.index
    sig = daily.index[0]
    out = daily_forward(daily, cal, sig, 20)
    exec_pos = 1                       # T+1
    assert out.exec_date == daily.index[exec_pos]
    assert out.end_date == daily.index[exec_pos + 20]
    assert out.actual_td == 20
    # 手工核算
    expected = closes[exec_pos + 20] / closes[exec_pos] - 1.0
    assert out.gross_return == pytest.approx(expected)


def test_daily_forward_max_drawdown_matches_manual():
    # 执行后路径：120 → 90 → 100 → 100，从 120 到 90 的回撤 = -25%
    # 序列需足够长：T+1 起再走 3 个交易日，故至少 5 个观测
    daily = _daily("2024-01-01", [100.0, 120.0, 90.0, 100.0, 100.0])
    cal = daily.index
    out = daily_forward(daily, cal, pd.Timestamp("2024-01-01"), 3)
    assert not out.is_immature
    assert out.exec_price == pytest.approx(120.0)
    assert out.max_drawdown == pytest.approx(90.0 / 120.0 - 1.0)
    assert out.max_adverse == pytest.approx(-0.25)
    assert out.max_favorable == pytest.approx(0.0)  # 执行价即区间最高


def test_daily_forward_applies_cost_once():
    daily = _daily("2024-01-01", [100.0, 100.0, 100.0])
    cal = daily.index
    out = daily_forward(daily, cal, pd.Timestamp("2024-01-01"), 1, cost_rate=0.0015)
    assert out.gross_return == pytest.approx(0.0)
    assert out.net_return == pytest.approx(-0.0015)


def test_daily_forward_flags_immature_when_window_incomplete():
    daily = _daily("2024-01-01", [100.0, 101.0, 102.0])  # 只有 3 个观测
    cal = daily.index
    out = daily_forward(daily, cal, pd.Timestamp("2024-01-01"), 250)
    assert out.is_immature is True
    assert np.isnan(out.net_return)
    assert out.actual_td == 1  # T+1 之后只剩 1 个观测


def test_daily_forward_flags_unavailable_when_daily_starts_later():
    """日频数据自 2020 起，信号在 2011 → 必须标记不可用，绝不能回填。"""
    daily = _daily("2020-01-01", [100.0] * 10)
    cal = _calendar("2010-01-04", 3000)  # 日历覆盖 2010 起
    out = daily_forward(daily, cal, pd.Timestamp("2011-05-31"), 20)
    assert out.daily_available is False
    assert np.isnan(out.net_return)
    assert "未覆盖" in out.note


def test_daily_forward_does_not_use_signal_day_close():
    """反向验证：若误用信号日收盘，收益会是另一个值。"""
    daily = _daily("2024-01-01", [100.0, 200.0, 200.0])
    cal = daily.index
    out = daily_forward(daily, cal, pd.Timestamp("2024-01-01"), 1)
    # 正确：200 → 200 = 0%；错误（当日成交）：100 → 200 = +100%
    assert out.gross_return == pytest.approx(0.0)


def test_daily_forward_rejects_far_away_exec_date():
    """日频序列中间有巨大空洞时，绝不能把执行日顺延到远端价格。

    场景：日频只有 2011 段与 2021 段，信号在 2015 → 必须判为数据缺失。
    """
    idx = list(pd.bdate_range("2011-06-01", periods=5)) + list(
        pd.bdate_range("2021-10-18", periods=5)
    )
    daily = pd.Series([100.0] * 10, index=pd.DatetimeIndex(idx), dtype="float64")
    cal = _calendar("2010-01-04", 3200)
    out = daily_forward(daily, cal, pd.Timestamp("2015-06-30"), 3)
    assert out.daily_available is False
    assert np.isnan(out.net_return)
    assert "数据缺失" in out.note


def test_daily_forward_rejects_hold_window_with_hole():
    """持有期内部跨越数据空洞 → 拒绝计算（250 个观测不能跨过几年）。"""
    idx = list(pd.bdate_range("2011-06-01", periods=30)) + list(
        pd.bdate_range("2021-10-18", periods=30)
    )
    daily = pd.Series([100.0 + i for i in range(60)], index=pd.DatetimeIndex(idx), dtype="float64")
    cal = pd.DatetimeIndex(sorted(idx))
    # 执行 2011-06-01 之后持有 250 个观测，必然会跨过 2011→2021 的空洞
    out = daily_forward(daily, cal, pd.Timestamp("2011-06-01"), 250)
    assert out.daily_available is False
    assert "空洞" in out.note


# --------------------------------------------------------------------------- #
# 基准与比较
# --------------------------------------------------------------------------- #
def test_baseline_uses_same_daily_basis():
    n = 30
    closes = [100.0 * (1.01 ** i) for i in range(n)]
    daily = _daily("2024-01-01", closes)
    cal = daily.index
    frame = pd.DataFrame({"price": closes[:20]}, index=daily.index[:20])
    base = build_baseline_daily(frame, daily, cal, 5)
    assert len(base) > 0
    # 每一步都应精确 5 个交易日
    for _, r in base.iterrows():
        pos = daily.index.get_loc(r["exec_date"])
        assert pos + 5 < len(daily)


def test_baseline_skips_immature_tail():
    daily = _daily("2024-01-01", [100.0] * 10)
    cal = daily.index
    frame = pd.DataFrame({"price": [100.0] * 10}, index=daily.index)
    base = build_baseline_daily(frame, daily, cal, 5)
    # 最后 5 个观测的持有期走不完，必须被剔除
    assert base["exec_date"].max() < daily.index[-5]


def test_compare_daily_splits_is_oos_by_exec_date():
    ev = pd.DataFrame(
        {
            "event_id": [1, 2],
            "signal_date": ["2018-01-01", "2019-01-01"],
            "exec_date_daily": ["2018-01-02", "2019-01-02"],
            "daily_available": [True, True],
            "ret_20td": [0.10, -0.05],
            "dd_20td": [-0.02, -0.08],
            "immature_20td": [False, False],
        }
    )
    base = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["2018-01-01", "2019-01-01"]),
            "exec_date": pd.to_datetime(["2018-01-02", "2019-01-02"]),
            "net_return": [0.01, 0.02],
            "gross_return": [0.01, 0.02],
            "max_drawdown": [-0.01, -0.01],
        }
    )
    res = compare_daily(ev, base, 20, is_end_date="2018-12-31")
    assert res["is"]["n"] == 1
    assert res["oos"]["n"] == 1
    assert res["is"]["mean"] == pytest.approx(0.10)
    assert res["oos"]["mean"] == pytest.approx(-0.05)
    assert res["is"]["mean_difference"] == pytest.approx(0.10 - 0.01)


def test_compare_daily_ignores_immature_events():
    ev = pd.DataFrame(
        {
            "event_id": [1],
            "signal_date": ["2024-01-01"],
            "exec_date_daily": ["2024-01-02"],
            "daily_available": [True],
            "ret_20td": [np.nan],
            "dd_20td": [np.nan],
            "immature_20td": [True],
        }
    )
    base = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["2024-01-01"]),
            "exec_date": pd.to_datetime(["2024-01-02"]),
            "net_return": [0.01],
            "gross_return": [0.01],
            "max_drawdown": [-0.01],
        }
    )
    res = compare_daily(ev, base, 20, is_end_date="2023-12-31")
    assert res["n_signals"] == 0


# --------------------------------------------------------------------------- #
# 事件表
# --------------------------------------------------------------------------- #
def test_check_events_daily_reports_exec_gap():
    daily = _daily("2024-01-01", [100.0, 110.0, 120.0, 130.0, 140.0])
    cal = daily.index
    monthly = pd.DataFrame(
        {
            "event_id": [1],
            "signal_date": [pd.Timestamp("2024-01-01")],
            "last_signal_date": [pd.Timestamp("2024-01-01")],
            "exec_date": [pd.Timestamp("2024-01-03")],
            "exec_price": [120.0],
        }
    )
    out = check_events_daily(monthly, daily, cal, windows=(2,), cost_rate=0.0)
    assert len(out) == 1
    assert out.loc[0, "exec_date_daily"] == "2024-01-02"
    assert out.loc[0, "exec_price_daily"] == pytest.approx(110.0)
    # 月频买贵了 120/110 - 1
    assert out.loc[0, "exec_gap_pct"] == pytest.approx(120.0 / 110.0 - 1.0)


def test_check_events_daily_handles_empty():
    out = check_events_daily(pd.DataFrame(), _daily("2024-01-01", [1.0]), _calendar("2024-01-01", 1))
    assert out.empty


# --------------------------------------------------------------------------- #
# 数据装载与买入持有
# --------------------------------------------------------------------------- #
def test_load_daily_close_dedupes_and_sorts(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text(
        "date,close\n2024-01-03,103\n2024-01-01,100\n2024-01-02,101\n2024-01-03,104\n",
        encoding="utf-8",
    )
    s = load_daily_close(p)
    assert len(s) == 3
    assert s.index.is_monotonic_increasing
    assert s.iloc[-1] == pytest.approx(104.0)  # 重复日期取最后一条


def test_load_daily_close_rejects_nonpositive(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,close\n2024-01-01,100\n2024-01-02,-5\n2024-01-03,0\n", encoding="utf-8")
    s = load_daily_close(p)
    assert len(s) == 1


def test_load_trading_calendar(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("trade_date\n2024-01-02\n2024-01-03\n", encoding="utf-8")
    cal = load_trading_calendar(p)
    assert len(cal) == 2
    assert cal.is_monotonic_increasing


def test_buy_and_hold_daily_matches_manual():
    # 505 个观测，价格一直 100、最后跳到 200
    n = 505
    closes = [100.0] * n
    closes[-1] = 200.0
    daily = _daily("2020-01-01", closes)
    bh = buy_and_hold_daily(daily, daily.index[0], daily.index[-1])
    assert bh["total_return"] == pytest.approx(1.0)
    # 单调不跌 → 无回撤
    assert bh["max_drawdown"] == pytest.approx(0.0)
    assert bh["cagr"] > 0


def test_buy_and_hold_daily_drawdown_matches_manual():
    daily = _daily("2020-01-01", [100.0, 200.0, 150.0, 250.0])
    bh = buy_and_hold_daily(daily, daily.index[0], daily.index[-1])
    # 峰值 200 → 谷底 150，回撤 -25%
    assert bh["max_drawdown"] == pytest.approx(-0.25)
    assert bh["total_return"] == pytest.approx(1.5)


def test_buy_and_hold_daily_returns_note_when_too_short():
    daily = _daily("2024-01-01", [100.0])
    bh = buy_and_hold_daily(daily, "2024-01-01", "2024-12-31")
    assert "note" in bh


def test_buy_and_hold_daily_splits_at_data_hole():
    """有数据空洞时必须分段计算，不得把 9 年跳跃当成单日收益。"""
    idx = list(pd.bdate_range("2011-06-01", periods=100)) + list(
        pd.bdate_range("2021-10-18", periods=100)
    )
    closes = [100.0 + i * 0.1 for i in range(200)]
    daily = pd.Series(closes, index=pd.DatetimeIndex(idx), dtype="float64")
    bh = buy_and_hold_daily(daily, "2011-06-01", "2026-10-16")
    assert bh.get("n_segments") == 2
    # 两段各自的波动率必须落在合理量级（不能被跨段跳跃污染）
    for seg in bh["segments"]:
        assert seg["ann_vol"] < 1.0
    assert "跨段" in bh["note"]


def test_split_segments_finds_holes():
    idx = list(pd.bdate_range("2011-06-01", periods=10)) + list(
        pd.bdate_range("2021-10-18", periods=10)
    )
    daily = pd.Series([100.0] * 20, index=pd.DatetimeIndex(idx), dtype="float64")
    segs = split_segments(daily)
    assert len(segs) == 2
    assert str(segs[0].index[-1].date()) == "2011-06-14"
    assert str(segs[1].index[0].date()) == "2021-10-18"
