"""L3s 截面信息含量检验的单元测试。

重点钉住三件事：
1. **无未来函数**：截断到 t 之后重算，t 处的分数必须一致；
2. **两端配对剔除**：期初或期末缺价的标的整只剔除，不能只影响一侧；
3. **判定顺序**：功效不足时不得报「无信息」，且 MDE 折算用同一口径。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from deepquant.gates import ic_test as ic


def _frame(prices: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices}, index=idx
    )


# ---------------------------------------------------------------- 池加载


def test_load_pool_parses_single_line_and_dedups(tmp_path):
    path = tmp_path / "pool.txt"
    path.write_text("000001.SZ,000002.SZ\n000001.SZ\n# 注释\n600000.SH\n", encoding="utf-8")
    assert ic.load_pool(path) == ("000001.SZ", "000002.SZ", "600000.SH")


def test_load_pool_rejects_empty(tmp_path):
    path = tmp_path / "pool.txt"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ic.ICTestError):
        ic.load_pool(path)


def test_load_pool_frames_records_dropped(tmp_path):
    data = tmp_path / "qfq"
    data.mkdir()
    _frame([10.0] * 80).to_csv(data / "A.qfq.csv", index_label="date")
    _frame([10.0] * 80).to_csv(data / "B.qfq.csv", index_label="date")
    frames, meta = ic.load_pool_frames(
        ["A", "B", "MISSING"],
        data_dir=data,
        span_start="2020-01-01",
        span_end="2021-12-31",
        min_rows_in_span=60,
    )
    assert set(frames) == {"A", "B"}
    assert meta["loaded"] == 2
    assert any(item["symbol"] == "MISSING" for item in meta["dropped"])


# ---------------------------------------------------------------- 无未来函数


def test_ma_gap_score_has_no_lookahead():
    """截断到 t 重算，t 处的分数必须与全样本计算一致。"""
    prices = [10.0 + 0.1 * i + (1.0 if i % 7 == 0 else 0.0) for i in range(120)]
    frame = _frame(prices)
    full = ic.ma_gap_scores({"A": frame}, fast=5, slow=20)["A"]

    cut = 90
    truncated = frame.iloc[: cut + 1]
    partial = ic.ma_gap_scores({"A": truncated}, fast=5, slow=20)["A"]

    day = frame.index[cut]
    assert full.loc[day] == pytest.approx(partial.loc[day], rel=0, abs=0)
    # 且预热期内必须是 NaN（不许用不足窗口的样本凑数）
    assert math.isnan(full.iloc[0])


def test_ma_gap_scores_rejects_bad_params():
    with pytest.raises(ic.ICTestError):
        ic.ma_gap_scores({"A": _frame([1.0] * 30)}, fast=20, slow=20)


# ---------------------------------------------------------------- 截面与配对剔除


def test_build_period_drops_symbols_missing_either_end():
    idx = pd.bdate_range("2020-01-01", periods=40)
    scores = {s: pd.Series(float(i), index=idx) for i, s in enumerate("ABDE", start=1)}
    frames = {
        # 收益与分数严格反向：分数最低涨最多
        "A": _frame([10.0] * 20 + [14.0] * 20),  # +40%
        "B": _frame([10.0] * 20 + [12.0] * 20),  # +20%
        "D": _frame([10.0] * 20 + [10.0] * 20),  # 0%
        "E": _frame([10.0] * 20 + [8.0] * 20),  # −20%
        # C 在期末没有行情（数据终止）→ 整只剔除，不得当 0 收益
        "C": _frame([10.0] * 25),
    }
    period = ic.build_period(
        score_by_symbol={**scores, "C": pd.Series(9.0, index=idx)},
        frames=frames,
        signal_date=idx[0],
        exec_date=idx[19],
        next_exec_date=idx[39],
        quintiles=2,
        min_names=2,
    )
    assert period.n_scored == 5
    assert period.n_used == 4  # C 被剔除（期初与期末两端配对）
    assert period.ic == pytest.approx(-1.0)  # 分数越高收益越差
    assert period.spread == pytest.approx(-0.4)  # Q2((0−0.2)/2) − Q1((0.4+0.2)/2)


def test_build_period_returns_none_below_min_names():
    idx = pd.bdate_range("2020-01-01", periods=30)
    period = ic.build_period(
        score_by_symbol={"A": pd.Series(1.0, index=idx)},
        frames={"A": _frame([10.0] * 30)},
        signal_date=idx[0],
        exec_date=idx[10],
        next_exec_date=idx[29],
        min_names=5,
    )
    assert period.ic is None
    assert period.n_used == 1


def test_spearman_perfect_and_flat():
    assert ic.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert ic.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert ic.spearman([1, 1, 1, 1], [1, 2, 3, 4]) == 0.0


# ---------------------------------------------------------------- 汇总与判定


def _periods(ics: list[float], n_used: int = 400) -> list[ic.ICPeriod]:
    idx = pd.bdate_range("2020-01-01", periods=len(ics) * 20)
    out = []
    for k, value in enumerate(ics):
        out.append(
            ic.ICPeriod(
                signal_date=idx[k * 20],
                exec_date=idx[k * 20 + 1],
                next_exec_date=idx[k * 20 + 19],
                n_scored=n_used,
                n_used=n_used,
                ic=value,
                spread=value * 2,
                quintile_returns=(0.00, 0.01, 0.02, 0.03, 0.04),
                cross_section_sd=0.15,
            )
        )
    return out


def test_summarise_ic_matches_manual_stats():
    # 期数要足够多，否则 NW(3) 的方差估计会退化（这本身是要暴露的行为，不是测试图方便）
    ics = [0.01, -0.02, 0.03, 0.0, 0.015] * 8
    summary = ic.summarise_ic(_periods(ics), bootstrap_reps=200, nw_lag=3)
    mean = sum(ics) / len(ics)
    assert summary["mean"] == pytest.approx(mean)
    assert summary["periods_with_ic"] == len(ics)
    sd = pd.Series(ics).std(ddof=1)
    assert summary["sd"] == pytest.approx(sd)
    assert summary["mde_return"] == pytest.approx(
        summary["mde_ic"] * 0.15 * ic.TAIL_EXPECTATION_TOP20
    )


def test_quintile_profile_monotonic_when_perfectly_increasing():
    periods = _periods([0.02, 0.03, 0.025])
    profile = ic.quintile_profile(periods)
    assert profile["monotonicity"] == pytest.approx(1.0)
    assert profile["spread_mean"] == pytest.approx(0.05)  # spread = value × 2 的均值


def test_decide_ic_requires_power_before_declaring_no_information():
    """功效不足（MDE 折回收益口径偏大）时必须报 INCONCLUSIVE_POWER，不能报「无信息」。"""
    weak = {"mean": 0.001, "t_nw3": 0.5, "mde_return": 0.01}
    verdict, _ = ic.decide_ic(weak, {"monotonicity": 0.2})
    assert verdict == "IC_INCONCLUSIVE_POWER"

    powered = {"mean": 0.001, "t_nw3": 0.5, "mde_return": 0.002}
    verdict, _ = ic.decide_ic(powered, {"monotonicity": 0.2})
    assert verdict == "IC_NO_INFORMATION_DETECTED"


def test_decide_ic_detects_information_and_reversal():
    strong = {"mean": 0.03, "t_nw3": 3.2, "mde_return": 0.002}
    verdict, _ = ic.decide_ic(strong, {"monotonicity": 0.9})
    assert verdict == "IC_INFORMATION_DETECTED"

    # 单调性不达标时不得判「有信息」——t 高也可能只是少数极端期
    verdict, _ = ic.decide_ic(strong, {"monotonicity": 0.4})
    assert verdict == "IC_INCONCLUSIVE"

    reversed_ = {"mean": -0.03, "t_nw3": -3.2, "mde_return": 0.002}
    verdict, _ = ic.decide_ic(reversed_, {"monotonicity": -0.9})
    assert verdict == "IC_SIGN_REVERSED"


def test_check_prereg_sha_rejects_tampering(tmp_path):
    path = tmp_path / "prereg.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ic.ICTestError):
        ic.check_prereg_sha(path, "deadbeef")
