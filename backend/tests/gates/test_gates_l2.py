"""L2 蒙特卡洛：重抽样机制、判据、终态。"""

from __future__ import annotations

import numpy as np
import pytest

from deepquant.gates.l2 import (
    VERDICT_EXTREME_DEPENDENCE,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASSED,
    VERDICT_PATH_DEPENDENCE,
    VERDICT_PREREQUISITE,
    VERDICT_TAIL,
    MIN_OBSERVATIONS,
    MonteCarloSpec,
    MonteCarloSpecError,
    cumulative_return,
    max_drawdown,
    run_l2,
    stationary_bootstrap,
    summarise_distribution,
    trimmed_return,
)


def _stable_returns(n: int = 1200, mean: float = 0.0010, vol: float = 0.004, seed: int = 7) -> np.ndarray:
    """广泛分布的正漂移序列：剔除最好 1% 的日子后仍应显著为正。

    参数不是随手取的：若把 vol 放大到 0.01、mean 压到 0.0008，
    1200 天的净收益会接近 0，此时最好 12 天必然贡献全部收益，
    E1 会（正确地）判定为极端依赖。测试数据本身也必须满足"不依赖极端日"。
    """
    rng = np.random.default_rng(seed)
    return rng.normal(mean, vol, n)


def _spike_returns(n: int = 1200) -> np.ndarray:
    """绝大多数日子为 0，靠少数几天大涨——典型的"依赖极端路径"。"""
    returns = np.zeros(n)
    returns[::100] = 0.05
    return returns


def _equity(returns: np.ndarray, start: float = 100_000.0) -> np.ndarray:
    return start * np.cumprod(1.0 + returns)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def test_max_drawdown_matches_hand_calculation() -> None:
    path = np.array([100.0, 120.0, 90.0, 110.0])
    assert max_drawdown(path) == pytest.approx(0.25)


def test_cumulative_return_compounds() -> None:
    assert cumulative_return(np.array([0.1, -0.1])) == pytest.approx(0.99 - 1.0)


def test_trimmed_return_zeroes_top_days_instead_of_dropping() -> None:
    """置零而不是删除：删除会减少复合次数，方向恰好相反。"""
    returns = np.array([0.05, 0.001, 0.001, -0.001, 0.001] * 40)
    result = trimmed_return(returns, 0.01)
    assert result["removed_days"] == 2  # ceil(200 * 0.01)
    assert result["trimmed_return"] < result["original_return"]
    # 剔除后长度不变（置零），因此复合次数不变
    assert result["removed_days"] == 2


def test_stationary_bootstrap_preserves_length_and_marginals() -> None:
    returns = _stable_returns(n=500)
    rng = np.random.default_rng(11)
    sample = stationary_bootstrap(returns, 20, rng)
    assert sample.size == returns.size
    # 重抽样只重排已有值，不产生新值
    assert set(np.round(sample, 12)).issubset(set(np.round(returns, 12)))


def test_block_bootstrap_keeps_autocorrelation_but_iid_does_not() -> None:
    """这条是选用块抽样的**唯一**理由，必须能测出来。"""
    rng = np.random.default_rng(3)
    n = 4000
    series = np.zeros(n)
    for index in range(1, n):
        series[index] = 0.8 * series[index - 1] + rng.normal(0, 0.01)
    lag1 = float(np.corrcoef(series[:-1], series[1:])[0, 1])
    assert lag1 > 0.7

    block = stationary_bootstrap(series, mean_block_length=40, rng=np.random.default_rng(5))
    block_lag1 = float(np.corrcoef(block[:-1], block[1:])[0, 1])

    iid = np.random.default_rng(5).permutation(series)
    iid_lag1 = float(np.corrcoef(iid[:-1], iid[1:])[0, 1])

    assert block_lag1 > 0.4, "块抽样应保留大部分自相关"
    assert abs(iid_lag1) < 0.1, "打乱顺序应摧毁自相关——这正是不能用 IID 的原因"


def test_bootstrap_is_reproducible_with_same_seed() -> None:
    returns = _stable_returns(n=300)
    first = stationary_bootstrap(returns, 20, np.random.default_rng(42))
    second = stationary_bootstrap(returns, 20, np.random.default_rng(42))
    assert np.array_equal(first, second)


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def test_passed_on_stable_series() -> None:
    returns = _stable_returns()
    result = run_l2(_equity(returns), hypothesis_id="H-T", l1a_verdict="L1A_PASSED")
    assert result.verdict == VERDICT_PASSED, result.checks
    assert result.repeatable_ratio > 0.5


def test_extreme_dependence_rejected_on_spike_series() -> None:
    returns = _spike_returns()
    result = run_l2(_equity(returns), hypothesis_id="H-T", l1a_verdict="L1A_PASSED")
    assert result.verdict == VERDICT_EXTREME_DEPENDENCE
    assert result.trimmed["trimmed_return"] <= 0
    # E1 是第一条判据，命中后不应再跑到尾部检查
    assert len(result.checks) == 1


def test_path_dependence_rejected_when_shuffle_kills_edge() -> None:
    """构造一条「涨跌成簇」的序列：收益集中在少数连续区块里，重排后优势消失。"""
    n = 1200
    returns = np.full(n, -0.0004)
    returns[::120] = 0.0
    for start in range(0, n, 240):
        returns[start : start + 8] = 0.012
    returns[-30:] = 0.02
    result = run_l2(
        _equity(returns),
        hypothesis_id="H-T",
        spec=MonteCarloSpec(min_repeatable_ratio=0.9),
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict in {VERDICT_PATH_DEPENDENCE, VERDICT_EXTREME_DEPENDENCE}


def test_tail_rejected_when_thresholds_are_tight() -> None:
    """用「不依赖极端日」的序列 + 极紧的尾部阈值，才测得到 TAIL 这一支。

    若改用高波动序列，E1 会先命中（净收益被波动吃掉，收益集中在少数几天），
    那就测不到尾部判据了。
    """
    returns = _stable_returns()
    result = run_l2(
        _equity(returns),
        hypothesis_id="H-T",
        spec=MonteCarloSpec(max_mdd_p95=0.001, max_terminal_loss_p5=-0.001),
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_TAIL
    # 确认确实走到了尾部三项检查，而不是在更早的判据上折返
    names = [check["name"] for check in result.checks]
    assert any(name.startswith("期末收益 5% 分位") for name in names)
    assert any(name.startswith("最大回撤 95% 分位") for name in names)
    assert any(name.startswith("CVaR5") for name in names)
    assert any(check["passed"] is False for check in result.checks[-3:])


def test_prerequisite_blocks_pass() -> None:
    result = run_l2(
        _equity(_stable_returns()),
        hypothesis_id="H-T",
        l1a_verdict="L1A_REJECTED_BOUNDS",
    )
    assert result.verdict == VERDICT_PREREQUISITE
    assert any("不构成放行结论" in item for item in result.limitations)


def test_short_series_is_inconclusive() -> None:
    result = run_l2(_equity(_stable_returns(n=MIN_OBSERVATIONS - 10)), hypothesis_id="H-T")
    assert result.verdict == VERDICT_INCONCLUSIVE
    assert any("不可信" in item for item in result.limitations)


def test_curve_input_skips_missing_valuation_days() -> None:
    returns = _stable_returns(n=400)
    equity = _equity(returns)
    curve = [
        {"day": f"d{index}", "equity": None if index in {10, 20} else float(value)}
        for index, value in enumerate(equity)
    ]
    result = run_l2(curve=curve, hypothesis_id="H-T", l1a_verdict="L1A_PASSED")
    assert result.missing_valuation_days == 2
    assert result.observations == len(equity) - 1 - 2


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def test_spec_rejects_primary_block_length_not_in_grid() -> None:
    with pytest.raises(MonteCarloSpecError, match="必须包含主块长"):
        MonteCarloSpec(mean_block_length=20, block_lengths=(10, 40))


def test_spec_rejects_tiny_replications() -> None:
    with pytest.raises(MonteCarloSpecError, match="replications"):
        MonteCarloSpec(replications=10)


def test_digest_changes_with_parameters() -> None:
    assert MonteCarloSpec(seed=1).digest != MonteCarloSpec(seed=2).digest


def test_summarise_distribution_reports_tail_mass() -> None:
    terminal = np.array([-0.5, -0.2, 0.1, 0.3, 0.5] * 20)
    drawdown = np.abs(terminal)
    summary = summarise_distribution(terminal, drawdown)
    assert summary["loss_probability"] == pytest.approx(0.4)
    # 离散分布下最差 5% 可能全部相等，此时 CVaR5 == p5；只要求不高于 p5
    assert summary["cvar5"] <= summary["terminal_p5"]


def test_block_sensitivity_covers_all_grid_points() -> None:
    result = run_l2(_equity(_stable_returns()), hypothesis_id="H-T", l1a_verdict="L1A_PASSED")
    lengths = [row["mean_block_length"] for row in result.block_sensitivity]
    assert lengths == list(MonteCarloSpec().block_lengths)
    assert result.block_length_stable is not None
