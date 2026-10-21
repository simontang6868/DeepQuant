"""阶段 J（冲击成本）的单元测试。

钉住四件容易错的事：

1. **成交量的单位（手 vs 股）** —— 差 100 倍会让整个冲击成本错一个量级，
   而这个错误不会报错，只会让数字"看起来很小"；
2. **两个模型的量纲**：Amihud 线性于交易额，平方根模型 ∝ sqrt(交易额)；
3. **组合层面按持仓权重加权**，不是简单地对个股冲击取算术平均；
4. **三态判据在全部资金档位上评估**，不许挑最好的那个。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_phase_j_impact import (  # noqa: E402
    SHARES_PER_LOT, breakeven_eta, impact_amihud, impact_sqrt, judge,
    liquidity_at,
)


# ============================================================ 成交量单位（最重要）
def test_volume_is_lots_not_shares():
    """⚠️ `exec_all` 的 volume 单位是**手**，必须 ×100。

    若当成股数，茅台的日成交额会变成 3,300 万元、换手率 0.002% —— 荒谬，
    但**不会报错**，只会让冲击成本低估 100 倍。这条测试专门防这个。
    """
    assert SHARES_PER_LOT == 100

    import pandas as pd
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "data", "exec_all", "600519.SH.exec.csv")
    if not os.path.exists(p):
        pytest.skip("缺少 600519 数据")
    d = pd.read_csv(p).tail(20)
    dv = (d["volume"] * SHARES_PER_LOT * d["close"]).median()
    # 茅台近 20 日日均成交额应在 1 亿 ~ 1000 亿之间
    assert 1e8 < dv < 1e11, f"日成交额 {dv/1e8:.2f} 亿，单位可能错了"


def test_shares_per_lot_is_used_in_dollar_volume():
    """常数必须被用到。若有人改成 1，上面的测试会挡住，这里再钉一次常量本身。"""
    assert SHARES_PER_LOT >= 1


# ============================================================ 流动性计算
def test_liquidity_at_computes_amihud_correctly():
    """ILLIQ = mean(|r| / DV)。手算验证：两日 0.01/1e8 与 0.03/2e8。"""
    ret = np.array([[0.01], [0.03]], dtype="float32")
    dv = np.array([[1e8], [2e8]], dtype="float32")
    il, adv, sd = liquidity_at(ret, dv)
    assert il[0] == pytest.approx((0.01 / 1e8 + 0.03 / 2e8) / 2, rel=1e-5)
    assert adv[0] == pytest.approx(1.5e8, rel=1e-5)


def test_liquidity_at_ignores_nonfinite():
    """除权日/停牌会让 DV 为 0 或 nan → 该日必须被排除，而不是把 ILLIQ 拉爆。"""
    ret = np.array([[0.01], [0.02], [0.03]], dtype="float32")
    dv = np.array([[1e8], [0.0], [2e8]], dtype="float32")
    il, _adv, _sd = liquidity_at(ret, dv)
    assert np.isfinite(il[0])
    assert il[0] == pytest.approx((0.01 / 1e8 + 0.03 / 2e8) / 2, rel=1e-5)


def test_illiq_is_inverse_to_liquidity():
    """成交额越大的股票，ILLIQ 越小 —— 这是"流动性"的方向性定义。"""
    ret = np.full((5, 2), 0.02, dtype="float32")
    dv = np.array([[1e7, 1e9]] * 5, dtype="float32")
    il, _, _ = liquidity_at(ret, dv)
    assert il[0] > il[1]


# ============================================================ 模型 A：线性
def test_impact_amihud_is_linear_in_position():
    """Amihud 是线性的：交易额翻倍 → 冲击翻倍。"""
    il = np.array([2e-10, 4e-10, 6e-10])
    idx = np.array([0, 1, 2])
    a = impact_amihud(il, idx, 1e4, 3)
    b = impact_amihud(il, idx, 2e4, 3)
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_impact_amihud_weights_by_holding_weight():
    """组合层面 = Σ (1/k) × 个股冲击，不是简单算术平均后再乘 traded 数。"""
    il = np.array([1e-10, 3e-10])
    idx = np.array([0, 1])
    got = impact_amihud(il, idx, p=1e4, k=10)
    # (1/10) × (1e-10×1e4 + 3e-10×1e4) × 100
    assert got == pytest.approx((1e-10 * 1e4 + 3e-10 * 1e4) / 10 * 100, rel=1e-9)


def test_impact_amihud_zero_when_nothing_traded():
    assert impact_amihud(np.array([1e-10]), np.array([], dtype=int), 1e4, 10) == 0.0


# ============================================================ 模型 B：平方根
def test_impact_sqrt_scales_as_square_root():
    """平方根模型的核心特征：交易额翻 4 倍 → 冲击只翻 2 倍。"""
    sd = np.array([0.02, 0.02])
    adv = np.array([1e8, 1e8])
    idx = np.array([0, 1])
    a = impact_sqrt(sd, adv, idx, 1e5, 2)
    b = impact_sqrt(sd, adv, idx, 4e5, 2)
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_impact_sqrt_is_linear_in_eta():
    sd = np.array([0.02])
    adv = np.array([1e8])
    idx = np.array([0])
    assert impact_sqrt(sd, adv, idx, 1e5, 1, 0.5) == \
        pytest.approx(0.5 * impact_sqrt(sd, adv, idx, 1e5, 1, 1.0), rel=1e-9)


def test_impact_sqrt_zero_when_nothing_traded():
    assert impact_sqrt(np.array([0.02]), np.array([1e8]), np.array([], dtype=int),
                       1e5, 10) == 0.0


def test_sqrt_model_is_more_punishing_than_linear_for_tiny_trades():
    """小单时平方根模型的单位冲击更大（凹函数在零点附近更陡）。

    这个性质决定了两个模型会给出**不同量级**的答案 —— 所以两个都必须报，
    不许只报好看的那个。
    """
    sd = np.array([0.02])
    adv = np.array([1e8])
    idx = np.array([0])
    p = 1e4                                    # 参与率 0.01%
    il = np.array([0.02 / 1e8])                # Amihud: |r|/DV
    lin = impact_amihud(il, idx, p, 1)
    sq = impact_sqrt(sd, adv, idx, p, 1, 1.0)
    assert sq > lin, "小单下平方根模型应给出更大的冲击"


# ============================================================ 打平 η*
def test_breakeven_eta_makes_mean_net_zero():
    base = [0.16] * 100          # f_inv×alpha − 佣金
    unit = [0.04] * 100          # η=1 时的冲击
    eta = breakeven_eta(base, unit)
    assert eta == pytest.approx(4.0, rel=1e-9)
    assert abs(statistics_mean(base) - eta * statistics_mean(unit)) < 1e-12


def test_breakeven_eta_nan_when_impact_is_zero():
    """冲击为 0 时不存在"打平门槛"——返回 nan，不许返回 inf 或随便给个数。"""
    assert math.isnan(breakeven_eta([0.1, 0.2], [0.0, 0.0]))


def statistics_mean(x):
    return sum(x) / len(x)


# ============================================================ 判据三态
def test_judge_significant_only_if_every_tier_passes():
    assert judge([2.5, 3.0, 2.1])["verdict"] == "NET_AFTER_EXECUTION_SIGNIFICANT"


def test_judge_scale_dependent_when_only_some_tiers_pass():
    assert judge([2.5, 1.2, 3.0])["verdict"] == "NET_AFTER_EXECUTION_SCALE_DEPENDENT"


def test_judge_absorbed_when_no_tier_passes():
    assert judge([1.5, 0.8, -1.0])["verdict"] == "NET_AFTER_EXECUTION_ABSORBED"


def test_judge_is_two_sided():
    """t = −2.5 与 t = +2.5 都算过 —— 判据是双侧的。"""
    assert judge([-2.5, -3.0])["verdict"] == "NET_AFTER_EXECUTION_SIGNIFICANT"
    assert judge([-1.9])["verdict"] == "NET_AFTER_EXECUTION_ABSORBED"


def test_judge_reports_per_tier_so_nothing_is_picked_silently():
    """必须逐档报出，防止"只看最好的那一档"这种事后挑选。"""
    j = judge([2.5, 1.2, 3.0])
    assert j["pass_by_w"] == [True, False, True]
    assert j["t_by_w"] == [2.5, 1.2, 3.0]
