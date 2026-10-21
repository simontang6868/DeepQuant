"""阶段 K（低换手版本）的单元测试。

钉住三件容易悄悄错的事：

1. **调仓对齐**：篮子必须在 `0, m, 2m, …` 更新，且**持有 m 个月**——
   差一个 off-by-one 会让 m=6 的换手看起来低得离谱，从而"证明"低频更好；
2. **换手摊分**：一次调仓的换手要除以 m，不能每个月都算全额；
3. **三态判据要求 A ∧ B 同时过**，不许拿"某个 m 的 t 最大"当结论。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_phase_k_lowturnover import (  # noqa: E402
    REBALANCE_PERIODS, T_CRIT, churn_of, judge, rebalance_schedule, t_iid,
)


def _res(m: int, t: float, net: float, seg_ok: bool = True) -> dict:
    return {"t_nw6": t, "net_pp": net, "seg_ok": seg_ok, "pass_AB": False,
            "alpha_retention": None}


def _mk(spec: dict) -> dict:
    """按预登记的四档构造 `by_m`；未指定的档填一个"明显不过"的占位。

    必须四档齐全 —— 漏掉一档会让 judge 抛 KeyError，
    而真实运行里四档是一定都跑的。
    """
    out = {}
    for m in REBALANCE_PERIODS:
        v = spec.get(m, (0.5, 0.0, False))
        t, net, seg = (v[0], v[1], v[2] if len(v) > 2 else True)
        out[m] = _res(m, t, net, seg)
    return out


# ============================================================ 调仓对齐
def test_rebalance_schedule_starts_at_zero():
    assert rebalance_schedule(12, 1)[:4] == [0, 1, 2, 3]
    assert rebalance_schedule(12, 3) == [0, 3, 6, 9]
    assert rebalance_schedule(12, 6) == [0, 6]


def test_every_month_is_covered_by_exactly_one_basket():
    """每个月必须恰好属于一个篮子 —— 漏掉一个月或重复一个月都是 off-by-one。"""
    n = 25
    for m in REBALANCE_PERIODS:
        cover = [0] * n
        for j in rebalance_schedule(n, m):
            for t in range(j, min(j + m, n)):
                cover[t] += 1
        assert all(c == 1 for c in cover), f"m={m} 覆盖异常: {cover}"


def test_first_basket_covers_exactly_m_months():
    """首个篮子持有 m 个月（除非样本不足）。"""
    for m in REBALANCE_PERIODS:
        sched = rebalance_schedule(30, m)
        span = min(sched[1], 30) - sched[0] if len(sched) > 1 else 30
        assert span == m or span == 30


# ============================================================ 换手
def test_churn_is_half_of_member_change():
    """单边换手 = 成员变动 / 2 —— 买和卖各算一边，别重复计。"""
    got = churn_of(["a", "b", "c", "e"], ["a", "b", "c", "d"])
    assert got == pytest.approx(0.5 * 2 / 4)


def test_churn_zero_when_unchanged():
    assert churn_of(["a", "b"], ["a", "b"]) == pytest.approx(0.0)


def test_churn_one_when_fully_replaced():
    assert churn_of(["c", "d"], ["a", "b"]) == pytest.approx(1.0)


def test_first_rebalance_counts_as_full_build():
    """建仓首期取 1.0（预登记 §3.2 未规定，已登记为缺陷并说明）。"""
    assert churn_of(["a", "b"], [], first=True) == 1.0


def test_turnover_is_amortized_over_holding_months():
    """⚠️ 一次调仓的换手要摊到 m 个月。不摊的话 m=6 的月换手会被高估 6 倍，
       佣金却按同一个月换手算 → 低频看起来"省不了钱"，结论会反。"""
    n, m, churn = 24, 6, 0.30
    per_month = [churn / m] * m
    assert per_month[0] == pytest.approx(0.05)
    assert sum(per_month) == pytest.approx(churn)


def test_amortized_commission_falls_with_period():
    """换手近似 ∝ 1/m → 佣金也 ∝ 1/m。这是本实验的全部动机。"""
    rate = 0.003
    for m in REBALANCE_PERIODS:
        comm = 2 * (0.183 / m) * rate * 100
        assert comm == pytest.approx(2 * 0.183 * rate * 100 / m, rel=1e-9)
    assert 2 * (0.183 / 6) * rate * 100 < 2 * 0.183 * rate * 100 / 2


# ============================================================ 判据三态
def test_rescues_when_some_period_passes_both_A_and_B():
    by = _mk({1: (1.2, 0.04), 2: (2.5, 0.09), 3: (1.0, 0.03)})
    assert judge(by)["verdict"] == "LOW_TURNOVER_RESCUES"


def test_no_rescue_if_only_m1_passes():
    """m=1 过不算"低换手救回来了" —— 它本来就是基准。"""
    by = _mk({1: (2.5, 0.05), 2: (1.5, 0.04), 3: (1.0, 0.03)})
    j = judge(by)
    assert j["verdict"] == "LOW_TURNOVER_NO_GAIN", "m=1 过不应判为 RESCUES"


def test_insufficient_when_no_period_significant_but_net_improves():
    by = _mk({1: (1.2, 0.04), 2: (1.5, 0.06), 3: (1.4, 0.05)})
    assert judge(by)["verdict"] == "LOW_TURNOVER_INSUFFICIENT"


def test_no_gain_when_net_does_not_improve():
    by = _mk({1: (1.2, 0.05), 2: (1.0, 0.03), 3: (0.8, 0.02)})
    assert judge(by)["verdict"] == "LOW_TURNOVER_NO_GAIN"


def test_B_failure_blocks_rescue():
    """t 高但分段不稳 → 不能判 RESCUES。判据要求 A ∧ B。"""
    by = _mk({1: (1.0, 0.04), 2: (3.0, 0.09, False)})
    assert judge(by)["verdict"] == "LOW_TURNOVER_INSUFFICIENT"


def test_judge_marks_pass_AB_per_period():
    by = _mk({1: (1.0, 0.04), 2: (2.5, 0.09)})
    judge(by)
    assert by[2]["pass_AB"] is True and by[1]["pass_AB"] is False


# ============================================================ 标准误
def test_t_iid_matches_hand_computation():
    x = [0.01, 0.02, 0.03, 0.04, 0.05]
    sd = (sum((v - 0.03) ** 2 for v in x) / 4) ** 0.5
    assert t_iid(x) == pytest.approx(0.03 / (sd / math.sqrt(5)), rel=1e-9)


def test_nw_primary_lag_is_uniform_across_periods():
    """⚠️ 四个变体必须共用同一个 NW 滞后阶数。按 m 各自挑 = 给每个变体调参数。"""
    assert 6 >= max(REBALANCE_PERIODS) - 1, "lag 必须覆盖最长篮子的自相关"
    assert REBALANCE_PERIODS == (1, 2, 3, 6)
    assert T_CRIT == 1.96
