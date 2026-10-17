"""Step 8.5' ensemble 评价接入的测试。

覆盖：并列评价、索引一致性、尾部剔除、红线说明、输入不可变，
以及一条守卫测试——**本模块不暴露任何择优 / 搜索 / 优化入口**（守 R2 / R11）。

注意：`_index` / `_sig` / `_close` 的索引长度一律由传入取值的长度推导，
不写死天数（曾经因写死默认天数导致 Length mismatch，同一坑踩过两次）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.analysis import ensemble_eval as ee
from deepquant.analysis.ensemble_eval import EnsembleEvalReport, evaluate_ensemble
from deepquant.analysis.signal_metrics import SignalEvalReport, evaluate_signals


def _index(n: int) -> pd.DatetimeIndex:
    """生成长度为 n 的交易日历（长度由调用方给定，不写死）。"""
    return pd.date_range("2024-01-01", periods=n, freq="D", name="date")


def _close(values: list[float]) -> pd.Series:
    return pd.Series(values, index=_index(len(values)), dtype="float64")


def _sig(values: list[int]) -> pd.Series:
    return pd.Series(values, index=_index(len(values)), dtype="int8")


def _rising10() -> pd.Series:
    """10 个交易日单调上涨的收盘价。"""
    return _close([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])


# ---------------------------------------------------------------- 基本结构


def test_evaluate_ensemble_returns_reports():
    close = _rising10()
    a = _sig([1] + [0] * 9)
    b = _sig([0] * 5 + [1] + [0] * 4)

    rep = evaluate_ensemble({"a": a, "b": b}, close, horizon=5)

    assert isinstance(rep, EnsembleEvalReport)
    assert isinstance(rep.ensemble, SignalEvalReport)
    assert set(rep.per_source) == {"a", "b"}
    assert all(isinstance(v, SignalEvalReport) for v in rep.per_source.values())
    assert rep.method == "majority"
    assert rep.horizon == 5
    assert rep.n_sources == 2


def test_ensemble_source_label_carries_method_and_sources():
    rep = evaluate_ensemble({"ma": _sig([1] + [0] * 9)}, _rising10(), horizon=5)

    assert rep.ensemble.source.startswith("ensemble[majority](")
    assert "ma" in rep.ensemble.source


def test_weights_and_method_passed_through():
    a = _sig([1] + [0] * 9)
    b = _sig([1] + [0] * 9)

    rep = evaluate_ensemble(
        {"a": a, "b": b},
        _rising10(),
        method="weighted",
        weights={"a": 2.0, "b": 1.0},
        horizon=5,
    )

    assert rep.method == "weighted"
    assert rep.ensemble.source.startswith("ensemble[weighted](")


# ---------------------------------------------------------------- 评价正确性


def test_buy_hit_rate_is_one_on_rising_close():
    # 第 0 日 BUY，horizon=5 → 未来收益 = 105/100 - 1 > 0 → 命中
    rep = evaluate_ensemble({"a": _sig([1] + [0] * 9)}, _rising10(), horizon=5)

    assert rep.ensemble.n_buy == 1
    assert rep.ensemble.n_evaluated == 1
    assert rep.ensemble.buy_hit_rate == pytest.approx(1.0)


def test_sell_hit_rate_is_zero_on_rising_close():
    # 第 0 日 SELL，但行情上涨 → 方向判断错误 → 命中率 0
    rep = evaluate_ensemble({"a": _sig([-1] + [0] * 9)}, _rising10(), horizon=5)

    assert rep.ensemble.n_sell == 1
    assert rep.ensemble.sell_hit_rate == pytest.approx(0.0)


def test_tail_signal_excluded_not_filled_with_zero():
    # 最后一日 BUY，horizon=5 无未来数据 → 必须剔除，不得用 0 填补
    rep = evaluate_ensemble({"a": _sig([0] * 9 + [1])}, _rising10(), horizon=5)

    assert rep.ensemble.n_signals == 1
    assert rep.ensemble.n_evaluated == 0
    assert rep.ensemble.excluded_tail == 1
    assert rep.ensemble.buy_hit_rate is None


def test_per_source_matches_direct_evaluation():
    close = _rising10()
    a = _sig([1] + [0] * 9)

    rep = evaluate_ensemble({"a": a}, close, horizon=5)
    direct = evaluate_signals(a, close, source="a", horizon=5)

    assert rep.per_source["a"].to_dict() == direct.to_dict()


def test_ensemble_coverage_reported():
    rep = evaluate_ensemble({"a": _sig([1] + [0] * 9)}, _rising10(), horizon=5)

    # 10 个交易日里 1 个可操作信号
    assert rep.ensemble_coverage == pytest.approx(0.1)


def test_evaluate_sources_false_skips_per_source():
    rep = evaluate_ensemble(
        {"a": _sig([1] + [0] * 9)},
        _rising10(),
        horizon=5,
        evaluate_sources=False,
    )

    assert rep.per_source == {}
    assert rep.n_sources == 0
    # 合成信号本身仍然被评价
    assert rep.ensemble.n_signals >= 1


# ---------------------------------------------------------------- 参数校验


def test_close_must_be_series():
    with pytest.raises(TypeError, match="close"):
        evaluate_ensemble({"a": _sig([1] + [0] * 9)}, [100, 101], horizon=5)


def test_close_empty_raises():
    with pytest.raises(ValueError, match="close"):
        evaluate_ensemble({"a": _sig([1])}, pd.Series(dtype="float64"), horizon=1)


def test_signal_index_mismatch_raises():
    close = _close([100.0, 101.0, 102.0])
    a = pd.Series(
        [1, 0, 0],
        index=pd.date_range("2024-02-01", periods=3, freq="D"),
        dtype="int8",
    )

    with pytest.raises(ValueError, match="索引"):
        evaluate_ensemble({"a": a}, close, horizon=1)


def test_signals_empty_mapping_raises():
    with pytest.raises(ValueError, match="signals"):
        evaluate_ensemble({}, _close([100.0, 101.0]), horizon=1)


def test_horizon_must_be_positive():
    # 信号长度必须与 close 一致（10），否则会先触发索引校验而非 horizon 校验
    with pytest.raises(ValueError, match="horizon"):
        evaluate_ensemble({"a": _sig([1] + [0] * 9)}, _rising10(), horizon=0)


def test_min_sources_higher_than_source_count_raises():
    a = _sig([1] + [0] * 9)
    b = _sig([1] + [0] * 9)

    with pytest.raises(ValueError, match="min_sources"):
        evaluate_ensemble({"a": a, "b": b}, _rising10(), min_sources=3, horizon=5)


def test_evaluate_sources_must_be_bool():
    with pytest.raises(TypeError, match="evaluate_sources"):
        evaluate_ensemble(
            {"a": _sig([1] + [0] * 9)},
            _rising10(),
            horizon=5,
            evaluate_sources="yes",
        )


# ---------------------------------------------------------------- 红线与序列化


def test_notes_carry_red_line_warning():
    rep = evaluate_ensemble({"a": _sig([1] + [0] * 9)}, _rising10(), horizon=5)
    joined = " ".join(rep.notes)

    assert "R2" in joined
    assert "R11" in joined
    assert "参数选择" in joined
    assert "不承诺 alpha" in joined


def test_to_dict_roundtrip():
    rep = evaluate_ensemble({"a": _sig([1] + [0] * 9)}, _rising10(), horizon=5)

    d = rep.to_dict()

    assert set(d) == {
        "method",
        "horizon",
        "ensemble_coverage",
        "ensemble",
        "per_source",
        "notes",
    }
    assert isinstance(d["ensemble"], dict)
    assert d["ensemble"]["source"].startswith("ensemble[")
    assert isinstance(d["per_source"]["a"], dict)
    assert d["horizon"] == 5


def test_no_optimizer_entry_points_exposed():
    """守卫：模块不得暴露任何择优 / 搜索 / 优化 / 拟合入口（R2 / R11）。"""
    forbidden = ("select", "best", "optimi", "optima", "tune", "search", "grid", "fit")
    public = [name for name in dir(ee) if not name.startswith("_")]

    for name in public:
        lowered = name.lower()
        assert not any(word in lowered for word in forbidden), (
            f"模块暴露了疑似择优选参入口：{name!r}（违反 R2 / R11）"
        )


def test_inputs_not_mutated():
    close = _rising10()
    a = _sig([1] + [0] * 9)
    close_before = close.copy()
    a_before = a.copy()

    evaluate_ensemble({"a": a}, close, horizon=5)

    pd.testing.assert_series_equal(close, close_before)
    pd.testing.assert_series_equal(a, a_before)
