"""阶段 L（排除异常放量 SURGE）的单元测试。

钉住三件事：

1. **两个窗口都要 ≥19 个有效日** —— 这不是可选清洗，是可行性检查逼出来的
   （停牌期成交量为 0 → 复牌后比值爆炸到 2990 倍）；
2. **不缩尾**：等频分组只用秩，秩对单调变换不变 → 缩尾无影响却多一个参数；
3. **判据 D（net@0.3% ≥ 0.30pp）是新加的准入门槛**，必须真的挡住东西——
   显著但量级不够的信号要落到 `SIGNIFICANT_BUT_SMALL`，不能被判为 CONFIRMED。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_phase_l_surge import (  # noqa: E402
    ECON_MIN_PP, MIN_VALID, MIN_VALID_PRIOR, WIN, judge, spearman, surge_of,
)


def _win(n: int, lo: float = 1e7, hi: float | None = None) -> np.ndarray:
    """构造 `(WIN, n)` 的日成交额窗口。"""
    a = np.full((WIN, n), lo, dtype="float32")
    if hi is not None:
        a[-1, :] = hi
    return a


def _dims(v: np.ndarray) -> np.ndarray:
    return v.reshape(1, -1) if v.ndim == 1 else v


# ============================================================ SURGE 构造
def test_surge_is_one_when_volume_unchanged():
    cur, pri = _win(3), _win(3)
    s, e = surge_of(cur, pri)
    assert np.allclose(s, 1.0)
    assert e.all()


def test_surge_ratio_is_correct():
    cur, pri = _win(2, lo=2e7), _win(2, lo=1e7)
    s, _ = surge_of(cur, pri)
    assert np.allclose(s, 2.0)


def test_surge_uses_mean_of_valid_days_only():
    """非正/缺失的日子必须被排除，而不是当 0 拉低均值。

    ⚠️ 本测试第一版把 5 天设为 0 → 只剩 16 个有效日 < 19 → 直接不合格，
       于是"均值"根本没被测到。改成恰好 19 个有效日（门槛边界）才测得到。
    """
    cur = _win(1, lo=1e7)
    cur[: 21 - MIN_VALID, 0] = 0.0      # 恰好留 19 个有效日
    pri = _win(1, lo=1e7)
    s, e = surge_of(cur, pri)
    assert e[0], "19 个有效日应合格"
    assert s[0] == pytest.approx(1.0, rel=1e-6), "停牌日不能当 0 拉低均值"


def test_prior_window_gate_blocks_suspension_artifact():
    """⚠️ 本测试是可行性检查的直接产物。

    停牌期成交量为 0 → 复牌后 SURGE 爆炸到几十~几千倍。那是**口径产物**，
    不是投机性放量。前窗口不足 19 个有效日 → 必须判为不合格。
    """
    cur = _win(1, lo=1e9)
    pri = _win(1, lo=1e7)
    pri[: 21 - MIN_VALID_PRIOR + 1, 0] = 0.0        # 前窗只剩 18 个有效日
    s, e = surge_of(cur, pri)
    assert not e[0], "前窗口不足 19 个有效日，必须挡住（否则会引入复牌伪影）"


def test_current_window_gate_also_applies():
    cur = _win(1, lo=1e9)
    cur[: 21 - MIN_VALID + 1, 0] = 0.0              # 近窗只剩 18 个有效日
    pri = _win(1, lo=1e7)
    s, e = surge_of(cur, pri)
    assert not e[0]


def test_gate_boundary_19_is_eligible_18_is_not():
    for keep, ok in ((MIN_VALID, True), (MIN_VALID - 1, False)):
        cur = _win(1, lo=1e8)
        cur[: 21 - keep, 0] = 0.0
        pri = _win(1, lo=1e7)
        _, e = surge_of(cur, pri)
        assert bool(e[0]) is ok, f"有效日 {keep} 天的判定错了"


def test_rank_preserved_by_strictly_monotone_transform():
    """严格单调变换（log）不改秩 → SURGE 用比值还是 log 比值，分组完全一样。"""
    rng = np.random.default_rng(3)
    heavy = np.concatenate([rng.lognormal(0, 1, 200), np.array([50.0, 200.0, 3000.0])])
    assert np.array_equal(np.argsort(np.argsort(heavy)),
                          np.argsort(np.argsort(np.log(heavy))))


def test_winsorizing_DOES_change_ranks_so_it_is_a_free_parameter():
    """⚠️ 预登记 §1.1 写"秩对单调变换不变 → 缩尾无影响"是**错的**，本测试抓出来了。

    缩尾（clip）是**弱**单调：它把顶部所有超过阈值的压成同一个值 → **制造并列**，
    等频分组就得靠并列打破规则决定谁进 Q5。所以缩尾**会**改变结果，
    而且多一个自由参数。这就是不做缩尾的**真正**理由（不是"反正没影响"）。
    """
    rng = np.random.default_rng(3)
    heavy = np.concatenate([rng.lognormal(0, 1, 200), np.array([50.0, 200.0, 3000.0])])
    r_raw = np.argsort(np.argsort(heavy))
    r_clip = np.argsort(np.argsort(np.minimum(heavy, 10.0)))
    assert not np.array_equal(r_raw, r_clip), "缩尾必然在顶部制造并列，秩一定变"


# ============================================================ 判据五态（含 D）
def _nw(t: float, mean_pp: float = 0.30) -> dict:
    return {"mean_pp": mean_pp, "t_nw": t}


def _segs(a: float = 0.30, b: float = 0.30, ta: float = 4.0, tb: float = 4.0) -> dict:
    return {"2007-2015": {"n": 108, "mean_pp": a, "t_nw": ta, "enough": True},
            "2016-2026": {"n": 129, "mean_pp": b, "t_nw": tb, "enough": True}}


def _net(net_pp: float, t: float = 3.0) -> dict:
    return {"net_pp_month": net_pp, "t_net": t}


def test_confirmed_requires_all_four_including_D():
    assert judge(_nw(4.0), _segs(), _net(0.35))["verdict"] == "SURGE_AVOID_CONFIRMED"


def test_significant_but_small_when_D_fails():
    """⭐ 本轮新增门槛：显著但量级不够必须单列，不能算"找到了信号"。"""
    j = judge(_nw(4.0), _segs(), _net(0.20))
    assert j["verdict"] == "SURGE_AVOID_SIGNIFICANT_BUT_SMALL"
    assert j["A_stat"] and j["B_stable"] and j["C_cost"] and not j["D_magnitude"]


def test_D_boundary_is_inclusive_at_0_30():
    assert judge(_nw(4.0), _segs(), _net(0.30))["D_magnitude"] is True
    assert judge(_nw(4.0), _segs(), _net(0.299))["D_magnitude"] is False


def test_D_rejects_negative_net_even_if_significant():
    """净值为负、|t| 很大（因为成本很高）→ 不能靠 |t| 混过去。"""
    j = judge(_nw(4.0), _segs(), _net(-0.075, t=-1.93))
    assert j["C_cost"] is False and j["D_magnitude"] is False
    assert j["verdict"] == "SURGE_AVOID_ABSORBED"


def test_unstable_when_one_segment_fails():
    j = judge(_nw(4.0), _segs(a=0.30, b=0.05, ta=4.0, tb=0.9), _net(0.35))
    assert j["verdict"] == "SURGE_AVOID_UNSTABLE"


def test_not_detected_when_A_fails():
    assert judge(_nw(1.5), _segs(), _net(0.35))["verdict"] == "SURGE_AVOID_NOT_DETECTED"


# ============================================================ 工具
def test_spearman_is_rank_based_and_scale_free():
    # ⚠️ spearman 对 size<10 返回 nan（月度池里几乎不会触发，但要有定义），
    #    所以这里必须用 ≥10 个元素，否则测到的是 guard 而不是算法。
    x = np.arange(1.0, 21.0)
    assert spearman(x, x * 100 + 7) == pytest.approx(1.0)
    assert spearman(x, -x) == pytest.approx(-1.0)


def test_spearman_nan_on_degenerate_input():
    assert math.isnan(spearman(np.ones(5), np.arange(5.0)))


def test_econ_min_is_pinned():
    """0.30pp 是本轮的准入门槛，不许被悄悄改小。"""
    assert ECON_MIN_PP == 0.30
    assert MIN_VALID == 19 and MIN_VALID_PRIOR == 19
    assert WIN == 21
