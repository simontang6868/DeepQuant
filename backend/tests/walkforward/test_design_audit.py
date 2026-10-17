"""研究设计审计模块的测试。

用例中的数字尽量取自 **2018-2024 真实行情的实测结果**
（平安 000001 / 茅台 600519），这样测试不只是在覆盖代码，
也在守卫 `docs/DECISION_RULING_2026-10-17.md` 里的实证结论。
"""

from types import SimpleNamespace

import pytest

from deepquant.walkforward.design_audit import (
    A_SHARE_LOT_SIZE,
    EXTERNAL_MIN_HISTORY_YEARS,
    check_affordability,
    check_grid_design,
    summarize_participation,
)


# 哨兵：区分「未传 params」与「显式传入 params=None」。
# 用 `params is None` 判断会把显式的 None 误替换成默认值，导致永远测不到
# 「无选中」分支——这个坑我踩过一次，故留此哨兵。
_UNSET = object()


def _res(ret, trades=0, params=_UNSET):
    """构造一个 WindowWFResult 形态的对象（本模块不做类型耦合）。"""
    if params is _UNSET:
        params = {"fast_window": 5, "slow_window": 15}
    return SimpleNamespace(
        cumulative_return=ret,
        trade_count=trades,
        selected_params=params,
    )


# =========================================================================== #
# 参与率口径
# =========================================================================== #


def test_all_windows_active_participation_is_full():
    """全部窗口都有成交 → 参与率 1.0，主口径与诊断列一致。"""
    rs = [_res(0.01, 2), _res(-0.02, 1), _res(0.03, 3)]
    s = summarize_participation(rs)
    assert s.n_windows == 3
    assert s.participation_rate == 1.0
    assert s.sum_trades == 6
    assert s.n_zero_trade == 0
    assert s.sample_too_small is False


def test_zero_participation_windows_are_separated_from_returns():
    """零参与窗口必须被识别出来，且**仍然计入主口径的分母**。"""
    # 2 个有成交（1 正 1 负），2 个零成交收益 0
    rs = [_res(0.01, 2), _res(-0.02, 1), _res(0.0, 0), _res(0.0, 0)]
    s = summarize_participation(rs)
    assert s.n_windows == 4
    assert s.n_with_trades == 2
    assert s.n_zero_trade == 2
    assert s.participation_rate == 0.5
    # 主口径分母 = 全部 4 个窗口（零收益窗口不剔除）
    assert s.positive_ratio == pytest.approx(1 / 4)
    # 诊断列分母 = 2 个非零收益窗口
    assert s.n_active == 2
    assert s.median_return_active == pytest.approx((0.01 + -0.02) / 2)


def test_main_metric_and_diagnostic_column_must_differ():
    """主口径与诊断列**必须**不同 —— 这条测试防止有人把两者混用。

    真实数据（平安，2018-2024）：主口径中位数 +0.000000，
    剔除零收益后为 -0.002566。
    """
    rs = [_res(0.0, 0), _res(0.0, 0), _res(0.0, 0), _res(-0.01, 1), _res(-0.02, 1)]
    s = summarize_participation(rs)
    assert s.median_cumulative_return == 0.0
    assert s.median_return_active != s.median_cumulative_return
    assert s.median_return_active == pytest.approx(-0.015)


def test_participation_below_half_flags_sample_too_small():
    """参与率 < 50% 时标记样本不足以评价。

    实测茅台参与率 16.7%，属于此类。
    """
    rs = [_res(0.0, 0)] * 5 + [_res(0.01, 1)]
    s = summarize_participation(rs)
    assert s.participation_rate == pytest.approx(1 / 6)
    assert s.sample_too_small is True


def test_no_selection_counted_separately():
    """min_trades 把候选全部过滤光 → 窗口无选中，须单独计数。

    实测茅台：启用 min_trades 后 18/30 窗口无选中。
    """
    rs = [_res(None, 0, params=None)] * 18 + [_res(0.0, 0)] * 6 + [_res(0.01, 1)] * 6
    s = summarize_participation(rs)
    assert s.n_windows == 30
    assert s.n_selected == 12
    # 18 个无选中 + 12 个有选中但收益为 None 的 0 个 → n_no_selection = 18
    assert s.n_no_selection == 18
    assert s.n_with_trades == 6
    assert s.participation_rate == pytest.approx(6 / 30)


@pytest.mark.parametrize(
    "params,expected_selected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ({}, False),
        ({"fast_window": 5, "slow_window": 15}, True),
    ],
)
def test_selected_params_various_shapes(params, expected_selected):
    """selected_params 的各种空形态都要被识别为「无选中」。"""
    s = summarize_participation([_res(0.0, 0, params=params)])
    assert s.n_selected == (1 if expected_selected else 0)


def test_empty_results_does_not_explode():
    s = summarize_participation([])
    assert s.n_windows == 0
    assert s.participation_rate == 0.0
    assert s.median_cumulative_return is None
    assert s.median_return_active is None
    assert s.positive_ratio is None


def test_median_uses_average_for_even_count():
    s = summarize_participation([_res(0.02, 1), _res(0.04, 1)])
    assert s.median_cumulative_return == pytest.approx(0.03)


# =========================================================================== #
# 一手可负担性
# =========================================================================== #


def test_pingan_is_affordable():
    """平安银行：实测均价 11.17 元 → 1 手 1,117 元，10 万本金买得起。"""
    r = check_affordability(11.17, 100_000.0)
    assert r.tradable is True
    assert r.lot_cost == pytest.approx(1117.0)
    assert r.lot_size == A_SHARE_LOT_SIZE


def test_maotai_is_not_affordable():
    """贵州茅台：实测均价 1,243.67 元 → 1 手 124,367 元 > 10 万本金。

    这是诊断报告里「112 个订单只换来 5 笔成交」的根因。
    """
    r = check_affordability(1243.67, 100_000.0)
    assert r.tradable is False
    assert r.lot_cost == pytest.approx(124_367.0)


def test_exact_boundary_is_affordable():
    """正好等于本金时算买得起（<= 而非 <）。"""
    r = check_affordability(1000.0, 100_000.0)
    assert r.tradable is True
    assert r.lot_cost == pytest.approx(100_000.0)


def test_one_cent_over_becomes_untradable():
    r = check_affordability(1000.01, 100_000.0)
    assert r.tradable is False


def test_custom_lot_size_for_star_market():
    """科创板最小申报单位 200 股。"""
    r = check_affordability(600.0, 100_000.0, lot_size=200)
    assert r.tradable is False
    assert r.lot_cost == pytest.approx(120_000.0)


def test_reason_text_is_informative():
    cheap = check_affordability(11.17, 100_000.0)
    rich = check_affordability(1243.67, 100_000.0)
    assert "≤" in cheap.reason
    assert ">" in rich.reason
    assert "无法成交" in rich.reason


@pytest.mark.parametrize(
    "price,cash,lot",
    [(0, 100_000.0, 100), (-1.0, 100_000.0, 100), (11.17, 0, 100), (11.17, -1.0, 100)],
)
def test_invalid_inputs_raise(price, cash, lot):
    with pytest.raises(ValueError):
        check_affordability(price, cash, lot_size=lot)


def test_invalid_lot_size_raises():
    with pytest.raises(ValueError):
        check_affordability(11.17, 100_000.0, lot_size=0)
    with pytest.raises(ValueError):
        check_affordability(11.17, 100_000.0, lot_size=-100)


# =========================================================================== #
# 窗口 / 网格算术
# =========================================================================== #


def test_current_seven_year_design_facts():
    """现状实测：1,699 行 / IS 150 / OOS 50 → 30 个窗口，IS:OOS = 3:1。"""
    f = check_grid_design(
        oos_periods=50, is_periods=150, max_slow=60, history_periods=1699
    )
    assert f.n_windows == 30
    assert f.is_oos_ratio == pytest.approx(3.0)
    # 1,699 交易日 ≈ 6.96 年，低于外部惯例的 8 年门槛
    assert f.history_years == pytest.approx(1699 / 244)
    assert f.meets_external_history_threshold is False


def test_fifteen_year_design_facts():
    """扩到 15 年（实测 3,573 行）后窗口数与历史门槛。"""
    f = check_grid_design(
        oos_periods=50, is_periods=150, max_slow=60, history_periods=3573
    )
    assert f.n_windows == 68  # (3573 - 150) // 50
    assert f.meets_external_history_threshold is True


def test_longer_oos_yields_fewer_windows():
    """OOS 放长 → 窗口数必然减少（算术约束，不是建议）。"""
    short = check_grid_design(
        oos_periods=50, is_periods=150, max_slow=60, history_periods=1699
    )
    long = check_grid_design(
        oos_periods=120, is_periods=480, max_slow=60, history_periods=1699
    )
    assert long.n_windows < short.n_windows
    assert long.n_windows == 10  # (1699 - 480) // 120


def test_history_too_short_gives_zero_windows():
    f = check_grid_design(
        oos_periods=50, is_periods=150, max_slow=60, history_periods=180
    )
    assert f.n_windows == 0


def test_external_threshold_constant_is_documented():
    """外部惯例门槛应为 8 年（来源见模块 docstring）。"""
    assert EXTERNAL_MIN_HISTORY_YEARS == 8.0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(oos_periods=0, is_periods=150, max_slow=60, history_periods=1699),
        dict(oos_periods=50, is_periods=0, max_slow=60, history_periods=1699),
        dict(oos_periods=50, is_periods=150, max_slow=0, history_periods=1699),
        dict(oos_periods=50, is_periods=150, max_slow=60, history_periods=0),
        dict(oos_periods=-50, is_periods=150, max_slow=60, history_periods=1699),
    ],
)
def test_invalid_design_inputs_raise(kwargs):
    with pytest.raises(ValueError):
        check_grid_design(**kwargs)


def test_non_integer_inputs_raise():
    with pytest.raises(ValueError):
        check_grid_design(
            oos_periods=50.0, is_periods=150, max_slow=60, history_periods=1699
        )
