# -*- coding: utf-8 -*-
"""P1-1 可执行区间审计的行为测试。

每个测试都对应一个**真实踩过的坑**或一个**必须成立的口径关系**，
不是"跑通就行"的冒烟测试。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import p11_exec_window as p11  # noqa: E402


# ------------------------------------------------------------------ month_series
def test_placeholder_month_does_not_poison_series():
    """建仓月账本里没有记录（NaN）时，不能把整条月收益序列污染成 NaN。

    真实 bug：早期用 `nv = nv / nv[0]`，nv[0] 是 NaN → 全序列 NaN →
    口径 B 的 per_path 变空 → 被静默当成"无样本"，打印时 None*100 才暴露。
    """
    months = ["20230131", "20230228", "20230331", "20230428"]
    cal = months
    # 20230131（建仓月）没有账本行
    df = pd.DataFrame({
        "date": ["20230201", "20230228", "20230331", "20230428"],
        "nav": [100.0, 110.0, 99.0, 108.9],
    })
    ok, r = p11.month_series(df, months, cal)
    assert not ok[0], "建仓月应标记为无记录"
    assert ok[1] and ok[2] and ok[3]
    assert np.isnan(r[0]) and np.isnan(r[1]), "首月无前值 → 收益应为 NaN"
    assert abs(r[2] - (99.0 / 110.0 - 1)) < 1e-12
    assert abs(r[3] - (108.9 / 99.0 - 1)) < 1e-12
    assert np.isfinite(r).sum() == 2, "应恰有 2 个有效月收益，而不是 0 个"


def test_month_series_skips_when_middle_month_missing():
    """中间缺一个月时，只丢掉跨该月的两个收益，不能前向填充造假。"""
    months = ["20230131", "20230228", "20230331", "20230428"]
    df = pd.DataFrame({
        "date": ["20230131", "20230228", "20230428"],
        "nav": [100.0, 120.0, 150.0],
    })
    ok, r = p11.month_series(df, months, months)
    assert list(ok) == [True, True, False, True]
    assert abs(r[1] - 0.20) < 1e-12
    assert np.isnan(r[2]) and np.isnan(r[3]), "跨缺失月的收益必须为 NaN"


def test_accumulate_drops_nan_instead_of_propagating():
    r = np.array([np.nan, 0.10, np.nan, -0.05])
    acc = p11.accumulate(r)
    assert len(acc) == 3
    assert abs(acc[-1] - 1.10 * 0.95) < 1e-12


# ------------------------------------------------------------------ 统计口径
def test_nw_se_reduces_to_iid_for_white_noise_ish():
    """iid 序列上 NW(3) 不应系统性偏离 iid 标准误（不检验方向，只检验量级）。"""
    rng = np.random.default_rng(7)
    x = rng.normal(0.01, 0.08, 4000)
    se_i = x.std(ddof=1) / math.sqrt(len(x))
    se_nw = p11.nw_se(x, 3)
    assert 0.8 < se_nw / se_i < 1.25, f"NW/iid = {se_nw/se_i:.3f} 偏离过大"


def test_nw_se_widens_under_positive_autocorrelation():
    """正自相关时 NW 必须大于 iid，否则核函数写错了。"""
    rng = np.random.default_rng(11)
    n = 800
    e = rng.normal(0, 1, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.8 * x[i - 1] + e[i]          # AR(1), rho=0.8
    x = x * 0.05 + 0.01
    se_i = x.std(ddof=1) / math.sqrt(n)
    se_nw = p11.nw_se(x, 3)
    assert se_nw > 1.3 * se_i, f"AR(0.8) 下 NW 应明显更宽，实测 {se_nw/se_i:.2f}"


def test_nw_se_accepts_decimal_not_percent():
    """入参是小数收益。传 pp 会让 se 放大 100 倍——这里钉住尺度无关性所依赖的前提。"""
    rng = np.random.default_rng(3)
    x = rng.normal(0.01, 0.05, 200)
    assert abs(p11.nw_se(x * 100, 3) - 100 * p11.nw_se(x, 3)) < 1e-9


def test_stats_mde_and_power_relations():
    rng = np.random.default_rng(5)
    x = rng.normal(0.008, 0.06, 44)
    st = p11.stats(x)
    assert abs(st["mde"] - 2.8016 * st["se_iid"]) < 1e-12
    assert st["n"] == 44

    # |t| = 1.96 时事后功效应≈0.50004（不是 0.5）
    y = x.copy()
    y = y - y.mean() + 1.96 * st["se_iid"]
    st2 = p11.stats(y)
    assert abs(abs(st2["t_iid"]) - 1.96) < 1e-6
    assert abs(st2["power"] - 0.500044) < 1e-4, f"power={st2['power']}"


def test_stats_handles_too_few_observations():
    st = p11.stats([0.01, 0.02])
    assert st == {"n": 2} and "t_iid" not in st


# ------------------------------------------------------------------ 连续段
def test_longest_run_returns_first_longest():
    assert p111_runs([True, True, False, True, True, True, False, True]) == (3, 3)


def p111_runs(flags):
    return p11.longest_run(flags)


def test_longest_run_all_true_and_all_false():
    assert p11.longest_run([True] * 44) == (44, 0)
    assert p11.longest_run([False] * 44) == (0, -1)


# ------------------------------------------------------------------ 判定分支
def test_verdict_never_promotes_short_window():
    """预登记 v1.2 §11：n<60 时无论结果多好都不能判 EXECUTABLE_WINDOW_FOUND。

    构造"完美"结果（44 月全可执行、连续段 44、年化 20%、回撤 −5%），
    判定必须仍然带 EXPLORATORY_ONLY_WINDOW_LT_60M，且**不得**出现 EXECUTABLE_WINDOW_FOUND。
    """
    v = p11.decide(44, 44, 44, {"ann_med": 0.20, "mdd_med": -0.05})
    assert "EXPLORATORY_ONLY_WINDOW_LT_60M" in v
    assert "EXECUTABLE_WINDOW_FOUND" not in v


def test_verdict_promotes_only_at_60_months():
    v = p11.decide(60, 60, 60, {"ann_med": 0.10, "mdd_med": -0.10})
    assert "EXECUTABLE_WINDOW_FOUND" in v
    assert "EXPLORATORY_ONLY_WINDOW_LT_60M" not in v


def test_verdict_flags_breach_and_shortfall_independently():
    v = p11.decide(44, 44, 44, {"ann_med": 0.05, "mdd_med": -0.25})
    assert "RETURN_BELOW_8PCT" in v and "DD_BREACH_20PCT" in v


def test_verdict_flags_inexecutable_months():
    v = p11.decide(30, 44, 30, {"ann_med": 0.12, "mdd_med": -0.10})
    assert "SOME_MONTHS_NOT_EXECUTABLE" in v
    assert "NO_CONTIGUOUS_36M_WINDOW" in v


def test_start_window_constant_is_2023():
    assert p11.START == "20230131"
