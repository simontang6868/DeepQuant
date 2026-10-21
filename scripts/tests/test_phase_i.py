"""阶段 I（exQ5 正式检验）的单元测试。

重点不是"跑通"，而是钉住四件容易悄悄错的事：

1. **独立分组与阶段 F 的分组必须逐元素一致** —— 否则 V0 复现闸门只是一个巧合；
2. **MAX5 用 partition 与用 sort 必须等价** —— 换算法是为了证明不是同一个 bug；
3. **判据四态的边界** —— 尤其 B 条件（两段都要显著同号），这是本实验唯一的卖点；
4. **容量必须同时报"未投资比例"** —— 只看 weight_mae 会漏掉现金残留。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_phase_i_exq5 import (  # noqa: E402
    N_GROUP, TOP_N, WIN, affordable_subset, capacity_of, judge, long_tradable,
    max5_partition, seg_stats, split_equal_indep, turnover_of_members,
)


# ============================================================ 独立分组
def test_indep_split_matches_phase_f_exactly():
    """与阶段 F 的 `split_equal` **逐元素**一致（含 ties 与不能整除的情形）。

    这是本实验"实现独立"的唯一可检验形式：算法不同、结果必须相同。
    """
    from run_phase_f_max5 import split_equal as ref

    rng = np.random.default_rng(20261021)
    for n in (0, 1, 4, 5, 6, 7, 10, 101, 997, 2373, 5000):
        for _ in range(3):
            x = rng.normal(size=n)
            assert np.array_equal(split_equal_indep(x, N_GROUP), ref(x, N_GROUP)), \
                f"n={n} 分组与阶段 F 不一致"


def test_indep_split_matches_phase_f_with_heavy_ties():
    """**大量并列值**时也必须一致 —— 这是分区算法最容易分叉的地方。"""
    from run_phase_f_max5 import split_equal as ref

    x = np.repeat(np.arange(7, dtype=float), 300)      # 7 个取值、每个 300 个并列
    x = x[np.random.default_rng(1).permutation(len(x))]
    got, exp = split_equal_indep(x, N_GROUP), ref(x, N_GROUP)
    assert np.array_equal(got, exp)
    # 全同值也不能崩，且必须恰好分成 5 块
    same = np.full(1003, 3.14)
    assert np.array_equal(split_equal_indep(same, N_GROUP), ref(same, N_GROUP))


def test_split_sizes_put_remainder_in_front():
    """定义：前 `n % k` 组各多 1 只。写死这个约定，否则跨实现会分叉。"""
    for n in (23, 100, 2373):
        g = split_equal_indep(np.arange(n, dtype=float), N_GROUP)
        sizes = np.bincount(g, minlength=N_GROUP)
        base, rem = divmod(n, N_GROUP)
        exp = np.full(N_GROUP, base)
        exp[:rem] += 1
        assert list(sizes) == list(exp), f"n={n}: {list(sizes)} != {list(exp)}"


def test_split_is_monotone_in_value():
    """组号必须沿取值升序单调不减 —— 这是"Q5 = 最高 MAX5"的定义前提。"""
    x = np.random.default_rng(7).normal(size=1000)
    g = split_equal_indep(x, N_GROUP)
    order = np.argsort(x, kind="stable")
    assert np.all(np.diff(g[order]) >= 0)
    # 最高组的所有取值不得低于最低组的任何取值
    assert x[g == N_GROUP - 1].min() >= x[g == 0].max()


# ============================================================ MAX5
def test_max5_partition_equals_sort_version():
    """partition 与 sort 取尾 5 个必须给出同一个均值。"""
    rng = np.random.default_rng(11)
    w = rng.normal(0, 0.03, size=(WIN, 500))
    ref = np.sort(w, axis=0)[-TOP_N:, :].mean(axis=0)
    assert np.allclose(max5_partition(w), ref, rtol=0, atol=1e-15)


def test_max5_uses_top_five_not_all():
    """必须只取最大的 5 个：一组 21 天里塞 5 个大涨、16 个小跌，结果应≈大涨均值。"""
    w = np.full((WIN, 1), -0.01)
    w[:5, 0] = [0.10, 0.09, 0.08, 0.07, 0.06]
    got = max5_partition(w)[0]
    assert got == pytest.approx(0.08, abs=1e-12), "取的不是前 5 大"


def test_max5_with_inf_padding_is_guarded_by_min_valid():
    """`-inf` 占位只在有效日不足 5 个时会污染结果；MIN_VALID=19 保证不会发生。

    这里把这条依赖关系**显式测出来**：19 个有效值时结果必须是有限数。
    """
    w = np.full((WIN, 1), -np.inf)
    w[:19, 0] = np.linspace(-0.02, 0.05, 19)
    assert np.isfinite(max5_partition(w)[0])
    w2 = np.full((WIN, 1), -np.inf)
    w2[:3, 0] = [0.01, 0.02, 0.03]
    assert not np.isfinite(max5_partition(w2)[0]), "有效值不足时应暴露为 nan/-inf，而不是静默给个数"


# ============================================================ 可成交性
def test_limit_up_blocks_entry_but_limit_down_allows_it():
    """买不到只可能是涨停方向。**跌停可以买入** —— 这条错了会系统性剔除输家。"""
    assert long_tradable({}, "limit_up")[0] is False
    ok, _ = long_tradable({}, "limit_down")
    assert ok is True, "跌停被当成买不到 → 会系统性剔掉当期最弱的票"


def test_one_word_board_only_blocks_up_direction():
    assert long_tradable({"dir": "up"}, "one_word_board")[0] is False
    assert long_tradable({"dir": "down"}, "one_word_board")[0] is True


def test_suspension_and_delisting_block_entry():
    for r in ("no_bar", "after_last_data"):
        assert long_tradable({}, r)[0] is False, f"{r} 应视为买不到"


def test_undetermined_flags_are_kept_not_dropped():
    """缺前收/前收非正：预登记**没有授权剔除**，必须保留并只计数。"""
    for r in ("no_prev", "bad_prev_close"):
        assert long_tradable({}, r)[0] is True


# ============================================================ 判据四态
def _nw(t: float, mean_pp: float = 0.23) -> dict:
    return {"mean_pp": mean_pp, "t_nw": t}


def _seg(mean_pp: float, t: float) -> dict:
    return {"n": 100, "mean_pp": mean_pp, "t_nw": t}


def _net(t: float) -> dict:
    return {"t_net": t, "net_pp_month": 0.12, "cost_pp_month": 0.11}


def test_judge_confirmed_requires_all_three():
    segs = {"2007-2015": _seg(0.24, 5.2), "2016-2026": _seg(0.22, 4.1)}
    j = judge(_nw(6.36), segs, _net(2.50))
    assert j["verdict"] == "EXQ5_CONFIRMED"
    assert j["A_stat"] and j["B_stable"] and j["C_cost"]


def test_judge_unstable_when_one_segment_loses_significance():
    """B 只要求"两段都显著"，不看合并 t —— 这正是本实验的核心判据。"""
    segs = {"2007-2015": _seg(0.40, 5.2), "2016-2026": _seg(0.05, 1.30)}
    j = judge(_nw(6.36), segs, _net(2.50))
    assert j["verdict"] == "EXQ5_SIGNIFICANT_BUT_UNSTABLE"
    assert j["A_stat"] and j["C_cost"] and not j["B_stable"]


def test_judge_unstable_when_segments_disagree_in_sign():
    """两段都"显著"但符号相反 → 必须判不稳定，不能被高 t 值糊过去。"""
    segs = {"2007-2015": _seg(0.60, 6.0), "2016-2026": _seg(-0.20, -2.5)}
    j = judge(_nw(6.36), segs, _net(2.50))
    assert not j["B_stable"] and j["verdict"] == "EXQ5_SIGNIFICANT_BUT_UNSTABLE"


def test_judge_cost_absorbed():
    segs = {"2007-2015": _seg(0.24, 5.2), "2016-2026": _seg(0.22, 4.1)}
    assert judge(_nw(6.36), segs, _net(1.40))["verdict"] == "EXQ5_COST_ABSORBED"


def test_judge_not_significant():
    segs = {"2007-2015": _seg(0.24, 5.2), "2016-2026": _seg(0.22, 4.1)}
    assert judge(_nw(1.90), segs, _net(2.50))["verdict"] == "EXQ5_NOT_SIGNIFICANT"


def test_judge_uses_two_sided_196_not_1645():
    """t=1.90 在单侧 1.645 下会过，在双侧 1.96 下不过。判据写死 1.96。"""
    segs = {"2007-2015": _seg(0.24, 5.2), "2016-2026": _seg(0.22, 4.1)}
    assert judge(_nw(1.90), segs, _net(3.0))["A_stat"] is False
    assert judge(_nw(1.97), segs, _net(3.0))["A_stat"] is True


# ============================================================ 容量
def test_affordable_subset_respects_budget_cap():
    """子集必须满足 `k × max(lot) ≤ W` —— 这是"等权且每只 ≥1 手"的充要条件。"""
    lots = [1000.0] * 50 + [50000.0] * 10
    for budget in (60000.0, 200000.0, 1e7):
        idx = affordable_subset(lots, budget)
        if idx:
            k = len(idx)
            assert k * max(lots[i] for i in idx) <= budget + 1e-9


def test_affordable_subset_drops_the_expensive_ones():
    """规则写死：按一手金额从高到低剔，不许按信号值剔。"""
    lots = [100.0, 200.0, 1e6, 1e6]
    idx = affordable_subset(lots, 400.0)
    assert set(idx) == {0, 1}, idx


def test_affordable_subset_empty_when_budget_too_small():
    assert affordable_subset([10_000.0], 9_999.0) == []


def test_capacity_invariant_invested_plus_idle_equals_budget():
    lots = [1200.0] * 300 + [250_000.0] * 5
    for budget in (1e5, 1e6, 1e7):
        cs = capacity_of(lots, budget)
        if cs["k"] == 0:
            assert cs["uninvested"] == 1.0
            continue
        assert cs["invested"] / budget + cs["uninvested"] == pytest.approx(1.0, abs=1e-12)


def test_capacity_must_report_uninvested_alongside_weight_mae():
    """⚠️ 只报 weight_mae 会漏掉现金残留 —— 本测试把这条教训钉住。

    构造：每只 7,000 元、资金 20,000，只能买 2 只 → 实际投入 14,000，
    **30% 现金闲置**。如果只看权重偏离，很容易把它读成"仓位基本到位"。
    """
    cs = capacity_of([7000.0] * 100, 20000.0)
    assert cs["k"] == 2
    assert cs["uninvested"] == pytest.approx(0.30, abs=1e-12)
    assert "weight_mae_pp" in cs, "必须同时报权重偏离"
    assert cs["uninvested"] > 0.05, "这个场景应被 5% 上限挡下"


def test_capacity_small_budget_lowers_coverage_not_idle_cash():
    """资金变小时，先掉的是"覆盖只数"，不是"闲置比例"（分布均匀时）。"""
    lots = [1000.0] * 200
    a, b = capacity_of(lots, 1e6), capacity_of(lots, 1e5)
    assert a["covered"] > b["covered"]
    assert a["uninvested"] == pytest.approx(0.0, abs=1e-12)
    assert b["uninvested"] == pytest.approx(0.0, abs=1e-12)


# ============================================================ 换手与分段
def test_turnover_zero_when_members_unchanged():
    assert turnover_of_members([{"a", "b"}, {"a", "b"}]) == pytest.approx(0.0)


def test_turnover_one_when_fully_replaced():
    assert turnover_of_members([{"a", "b"}, {"c", "d"}]) == pytest.approx(1.0)


def test_turnover_is_half_the_member_change():
    """单边换手 = 成员变动 / 2 —— 买和卖各算一边，别重复。"""
    got = turnover_of_members([{"a", "b", "c", "d"}, {"a", "b", "c", "e"}])
    assert got == pytest.approx(0.5 * (1 + 1) / 4)


def test_seg_stats_filters_by_date_inclusive():
    a = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.99, 0.99]
    d = ["2007-01-31", "2007-02-28", "2007-03-31", "2007-04-30",
         "2007-05-31", "2015-12-31", "2016-01-31", "2026-09-30"]
    s = seg_stats(a, d, "2007-01-01", "2015-12-31")
    assert s["n"] == 6 and s["enough"] is True
    # 前 6 个的均值 = (1+2+3+4+5+6)/6 / 100 = 0.035 → 3.5pp
    assert s["mean_pp"] == pytest.approx(3.5, abs=1e-6)


def test_seg_stats_survives_too_few_months():
    """`newey_west` 在 n<6 时不返回 `mean_pp`。分段统计必须不崩，并标 `enough=False`。

    这条是单测抓出来的真问题：生产段有 108/129 个月不会触发，
    但一旦某段月数不足，KeyError 会让整个实验半途死掉。
    """
    s = seg_stats([0.01, 0.02], ["2015-01-31", "2015-02-28"], "2015-01-01", "2015-12-31")
    assert s["n"] == 2
    assert s["enough"] is False
    assert math.isnan(s["mean_pp"]) and math.isnan(s["t_nw"])


def test_judge_fails_stability_when_segment_too_short():
    """样本不足的分段必须判 B 不通过，不能靠 nan 比较恒 False 蒙过去。"""
    segs = {"2007-2015": _seg(0.24, 5.2),
            "2016-2026": {"n": 3, "mean_pp": float("nan"), "t_nw": float("nan"),
                          "enough": False}}
    assert judge(_nw(6.36), segs, _net(2.50))["B_stable"] is False


def test_max5_window_constants_are_pinned():
    """参数写死在预登记里，改动必须新预登记 —— 这里只是防止手滑。"""
    assert (WIN, TOP_N, N_GROUP) == (21, 5, 5)
    assert math.isclose(WIN / TOP_N, 4.2)
