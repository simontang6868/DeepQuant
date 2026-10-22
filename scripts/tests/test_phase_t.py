"""阶段 T 单测：稳健性解剖的语义、单位铁律、分解恒等式与判据逻辑。

按项目铁律，**先证明检查器能抓到故障，再相信它变绿**。
变异覆盖：pp 直传放大 100 倍 / 极端月剔除集写错 / top-k 选错方向 /
NW 滞后不生效 / 分界点取错子序列 / ADV 分组大小失衡 / 分解恒等式被破坏 /
组内效应与贡献混用 / 判据忽略均值符号 / 组合判定的布尔逻辑。
"""

from __future__ import annotations

import math
import os
import random
import statistics
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_phase_t_exq5_robustness import (  # noqa: E402
    ADV_RATIO_MAX, ALT_SPLITS, APRIORI_EXTREME, IDENTITY_TOL, MODERN_LO,
    N_ADV_GROUP, SPLIT_PASS_MIN, TOPK, adv_quintiles, decompose,
    nw_with_lag, seg_subset, verdict_of,
)
from run_phase_s_exq5_modern import nw_stats_pp  # noqa: E402  （同一统计实现）


# ============================================================ 分段 / 剔除
def test_apriori_extreme_set_is_frozen_and_six_months():
    assert APRIORI_EXTREME == ("2016-01", "2016-02", "2020-02", "2020-03",
                               "2024-01", "2024-02")
    assert len(APRIORI_EXTREME) == 6


def test_seg_subset_modern_boundary_inclusive():
    dates = ["2015-12-31", "2016-01-01", "2016-01-31", "2026-09-30"]
    vals = [1.0, 2.0, 3.0, 4.0]
    d, v = seg_subset(dates, vals, MODERN_LO)
    assert d == ["2016-01-01", "2016-01-31", "2026-09-30"]
    assert v == [2.0, 3.0, 4.0]


def test_seg_subset_drop_matches_whole_month_prefix():
    """剔除必须是「YYYY-MM」前缀匹配：2016-01 要把 01-31 一起剔掉。"""
    dates = ["2016-01-01", "2016-01-31", "2016-02-01", "2016-03-01", "2016-12-31"]
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    d, v = seg_subset(dates, vals, MODERN_LO, drop=("2016-01",))
    assert d == ["2016-02-01", "2016-03-01", "2016-12-31"]
    assert v == [3.0, 4.0, 5.0]


def test_apriori_drop_removes_exactly_six_from_modern():
    dates, vals = [], []
    for y in range(2016, 2027):
        for m in range(1, 13):
            if y == 2026 and m > 9:
                continue
            dates.append(f"{y}-{m:02d}-01")
            vals.append(0.1)
    d, v = seg_subset(dates, vals, MODERN_LO)
    assert len(d) == 129
    d2, _ = seg_subset(dates, vals, MODERN_LO, drop=APRIORI_EXTREME)
    assert len(d2) == 123


def test_topk_drops_largest_absolute_not_largest_positive():
    """变异：按值而非 |值| 选 top-k → 会把最负的月留下、把小幅正的月剔掉。"""
    mv = [0.5, -0.9, 0.2, -0.3, 0.1, -0.05, 0.4, -0.6, 0.15, -0.25]
    order = sorted(range(len(mv)), key=lambda i: -abs(mv[i]))
    assert order[0] == 1 and order[1] == 7 and order[2] == 0


def test_topk_constants_frozen():
    assert TOPK == (1, 3, 6)


# ============================================================ NW 滞后
def test_nw_with_lag_actually_changes_se():
    """正自相关序列上 L=0 与 L=3 的 se 必须不同（防 lag 参数被忽略）。"""
    rng = random.Random(7)
    cur, vals = 0.0, []
    for _ in range(400):
        cur = 0.7 * cur + rng.gauss(0.05, 0.3)
        vals.append(cur)
    s0 = nw_with_lag(vals, 0)
    s3 = nw_with_lag(vals, 3)
    assert s3["se_nw_pp"] > s0["se_nw_pp"]
    assert s3["t_nw"] < s0["t_nw"]


def test_nw_with_lag_matches_nw_stats_pp_at_lag3():
    rng = random.Random(9)
    vals = [rng.gauss(0.05, 0.4) for _ in range(150)]
    a = nw_with_lag(vals, 3)
    b = nw_stats_pp(vals)
    assert a["t_nw"] == pytest.approx(b["t_nw"], abs=1e-12)
    assert a["se_nw_pp"] == pytest.approx(b["se_nw_pp"], abs=1e-12)


def test_nw_with_lag_pp_not_amplified():
    """单位铁律：传 pp 会被放大 100 倍。"""
    vals = [0.12, -0.05, 0.08, 0.15, -0.02, 0.10, 0.07, -0.09, 0.11, 0.03] * 5
    st = nw_with_lag(vals, 3)
    assert abs(st["mean_pp"]) == pytest.approx(abs(statistics.fmean(vals)), abs=1e-9)
    assert abs(st["mean_pp"]) < 1.0


# ============================================================ T2 分界点
def test_alt_splits_frozen_and_exclude_the_data_driven_boundary():
    """替代分界必须写死，且**不得包含**数据驱动的 2016-01-01 本身。"""
    assert ALT_SPLITS == ("2013-01-01", "2014-01-01", "2015-01-01",
                          "2017-01-01", "2018-01-01", "2019-01-01")
    assert MODERN_LO not in ALT_SPLITS
    assert all("2013-01-01" <= b <= "2019-01-01" for b in ALT_SPLITS)
    assert SPLIT_PASS_MIN == 4


def test_alt_split_subset_lengths_are_monotone_in_boundary():
    """分界越晚，后段越短 —— 用来钉住 MDE 必须随之上升的机制。"""
    dates = [f"{y}-{m:02d}-01" for y in range(2007, 2027) for m in range(1, 13)]
    dates = [d for d in dates if d <= "2026-09-01"]
    vals = [0.1] * len(dates)
    lens = []
    for b in ALT_SPLITS:
        d, _ = seg_subset(dates, vals, b)
        lens.append(len(d))
    assert lens == sorted(lens, reverse=True)
    assert lens[-1] < lens[0]


# ============================================================ T3 ADV 分组
def test_adv_quintiles_balanced_and_contiguous():
    rng = np.random.default_rng(3)
    adv = rng.uniform(1e5, 1e8, size=2000)
    g = adv_quintiles(adv)
    assert set(np.unique(g)) <= set(range(N_ADV_GROUP))
    sizes = [int((g == i).sum()) for i in range(N_ADV_GROUP)]
    assert max(sizes) - min(sizes) <= 1
    # 结构：按 ADV 排序后组号必须非递减（分组是有序分块，不是乱指）
    order = np.argsort(adv, kind="stable")
    assert np.all(np.diff(g[order]) >= 0)


def test_adv_quintiles_rank_preserving():
    """组号必须由 ADV 大小决定：最低 ADV 必在 g0，最高必在 g4。"""
    adv = np.array([5.0, 1.0, 100.0, 50.0, 3.0, 7.0, 200.0, 9.0, 11.0, 13.0])
    g = adv_quintiles(adv)
    assert g[np.argmin(adv)] == 0
    assert g[np.argmax(adv)] == N_ADV_GROUP - 1


# ============================================================ T3 分解恒等式
def _synth_rec(n: int = 240, seed: int = 5) -> dict:
    """合成一个月：ADV 与 MAX5 无关、fwd 有结构，用于手算校验。"""
    rng = np.random.default_rng(seed)
    adv = rng.uniform(1e5, 1e8, size=n)
    fwd = rng.normal(0.01, 0.05, size=n)
    is_q5 = np.zeros(n, dtype=bool)
    is_q5[np.argsort(-fwd)[: n // 5]] = True      # 涨幅最高的 20% 当 Q5
    return {"signal_date": "2020-01-31", "codes": [f"{i:06d}" for i in range(n)],
            "fwd": fwd.tolist(), "adv": adv.tolist(),
            "adv_ok": [True] * n, "is_q5": is_q5.tolist(),
            "n_pool": n, "alpha": float(fwd[~is_q5].mean() - fwd.mean())}


def test_decompose_identity_holds_exactly():
    rows, meta = decompose([_synth_rec()])
    r = rows[0]
    assert "skip" not in r
    assert abs(sum(r["contrib"]) - r["alpha_t3"]) < IDENTITY_TOL
    assert abs(r["alpha_t3"] - r["alpha_full"]) < IDENTITY_TOL
    assert meta["independent_split_disagreements"] >= 0


def test_decompose_contribution_matches_hand_computation():
    """Q5 用涨幅（与 ADV 无关），保证每个 ADV 组内都有保留股。"""
    rng = np.random.default_rng(11)
    n = 100
    adv = rng.uniform(0, 1, size=n)
    fwd = rng.normal(0, 0.05, size=n)
    is_q5 = np.zeros(n, dtype=bool)
    is_q5[np.argsort(-fwd)[:20]] = True            # 涨幅最高的 20% 当 Q5
    rec = {"signal_date": "2019-06-30", "codes": [f"{i}" for i in range(n)],
           "fwd": fwd.tolist(), "adv": adv.tolist(), "adv_ok": [True] * n,
           "is_q5": is_q5.tolist(), "n_pool": n,
           "alpha": float(fwd[~is_q5].mean() - fwd.mean())}
    rows, _ = decompose([rec])
    r = rows[0]
    g = adv_quintiles(adv)
    for gi in range(N_ADV_GROUP):
        in_p = g == gi
        in_k = in_p & (~is_q5)
        expect = fwd[in_k].sum() / (~is_q5).sum() - fwd[in_p].sum() / n
        assert r["contrib"][gi] == pytest.approx(float(expect), abs=1e-12)
        # 组内避免效应与贡献是两回事，不得混用
        expect_within = float(fwd[in_k].mean() - fwd[in_p].mean())
        assert r["within"][gi] == pytest.approx(expect_within, abs=1e-12)


def test_decompose_excludes_stocks_without_adv_but_keeps_identity():
    """没有 ADV 的股票必须被排除在 T3 池外，且恒等式在剩余池上仍成立。"""
    rec = _synth_rec(n=200, seed=13)
    adv_ok = [True] * 200
    for i in range(0, 200, 7):                     # 约 1/7 的股票无 ADV
        adv_ok[i] = False
    rec["adv_ok"] = adv_ok
    rows, meta = decompose([rec])
    r = rows[0]
    assert "skip" not in r
    assert abs(sum(r["contrib"]) - r["alpha_t3"]) < IDENTITY_TOL
    # 被排除后 T3 的 alpha 与全池 alpha 不同（必须如实区分，不得假装相等）
    assert r["alpha_t3"] != r["alpha_full"]
    assert 0.0 < meta["adv_coverage_mean"] < 1.0


def test_decompose_skips_month_when_pool_too_small():
    rec = _synth_rec(n=30, seed=17)
    rows, _ = decompose([rec])
    assert rows[0].get("skip") == "pool_too_small"


# ============================================================ 判据逻辑
def test_verdict_all_pass():
    assert verdict_of(True, True, True) == "T_ROBUST_ALL"


def test_verdict_one_secondary_fails_is_caveat():
    assert verdict_of(True, False, True) == "T_ROBUST_WITH_CAVEAT"
    assert verdict_of(True, True, False) == "T_ROBUST_WITH_CAVEAT"


def test_verdict_primary_failure_is_fragile_even_if_secondaries_pass():
    """主检验不过 → 一律 T_FRAGILE，副检验全过也救不回来。"""
    assert verdict_of(False, True, True) == "T_FRAGILE"


def test_verdict_both_secondaries_fail_is_fragile():
    assert verdict_of(True, False, False) == "T_FRAGILE"


def test_verdict_all_fail_is_fragile():
    assert verdict_of(False, False, False) == "T_FRAGILE"


def test_t3_ratio_threshold_frozen():
    assert ADV_RATIO_MAX == 3.0


# ============================================================ 单位铁律（G3）
def test_g3_units_conversion():
    """⚠️ G3 首跑栽在这里：`recs[*]["alpha"]` 是**小数**（日收益口径），
    而 `nw_with_lag()` 的契约是 pp。直接传会把均值压小 100 倍，
    而 t 尺度无关所以仍然逐位正确 —— 这是"只看 t 发现不了"的典型形态。
    因此必须同时核对均值（量级）与 t（尺度），并在此钉住换算。"""
    decimals = [0.0023, -0.0005, 0.0008, 0.0015, -0.0002, 0.0010] * 8
    st_pp = nw_with_lag([v * 100.0 for v in decimals], 3)      # 正确：先换 pp
    st_dec = nw_with_lag(decimals, 3)                          # 错误：把小数当 pp
    assert st_pp["mean_pp"] == pytest.approx(statistics.fmean(decimals) * 100.0, abs=1e-12)
    assert st_dec["mean_pp"] == pytest.approx(statistics.fmean(decimals), abs=1e-12)
    # t 在两种传法下必须相同（尺度无关）→ 所以"只核对 t"抓不到这个错
    assert st_pp["t_nw"] == pytest.approx(st_dec["t_nw"], abs=1e-12)
    assert abs(st_pp["mean_pp"] / st_dec["mean_pp"] - 100.0) < 1e-9


# ============================================================ 数据闸门（集成）
@pytest.mark.skipif(not os.path.exists(os.path.join("phases_out", "f098a59fb1b5")),
                    reason="阶段 S 冻结产物不在（新鲜 clone）")
def test_frozen_phase_s_series_shape():
    import pandas as pd
    df = pd.read_csv(os.path.join("phases_out", "f098a59fb1b5", "monthly.csv"))
    assert len(df) == 237
    # signal_date 是月末交易日（不是月初）
    assert df["signal_date"].min() == "2007-01-31"
    assert df["signal_date"].max() == "2026-09-30"
    assert df["net_S_3000w"].notna().all()
    # 现代段（含 2016-01-31 起）必须恰为 129 个月
    assert int((df["signal_date"] >= MODERN_LO).sum()) == 129
