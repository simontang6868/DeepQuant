"""L1a 契约：不自洽的假设必须在进入评估前被拦下。"""

from __future__ import annotations

import pytest

from deepquant.gates.metrics import Metric, MetricError, check_bound, ok, summarise, unavailable
from deepquant.gates.spec import SpecError, parse_spec

BASE = {
    "hypothesis_id": "H-T",
    "title": "测试假设",
    "strategy_id": "ma_cross",
    "param_grid": {"fast_window": [5, 10], "slow_window": [20, 40]},
    "constraints": [["fast_window", "slow_window"]],
    "data": {"symbol": "T", "research_path": "n/a"},
    "in_sample": {"start": "2020-01-01", "end": "2021-12-31"},
    "out_sample": {"start": "2022-01-01", "end": "2022-12-31"},
    "boundaries": {
        "min_in_sample_return": 0.1,
        "max_in_sample_drawdown": 0.3,
        "min_closed_trades": 2,
        "min_out_sample_return": 0.0,
        "max_out_sample_drawdown": 0.3,
        "max_oos_degradation": 0.5,
    },
    "declared_grid_points": 4,
}


def test_parse_accepts_valid_spec():
    spec = parse_spec(BASE)
    assert spec.grid_size == 4
    assert spec.excluded_by_constraint == 0


def test_constraint_filters_invalid_combinations():
    payload = {
        **BASE,
        "param_grid": {"fast_window": [5, 20], "slow_window": [15, 30]},
        "declared_grid_points": 3,
    }
    spec = parse_spec(payload)
    # 4 个组合里 (20,15) 违反 fast<slow，被排除；分母应为 3 而不是 4
    assert spec.grid_size == 3
    assert spec.excluded_by_constraint == 1
    assert spec.violates_constraint({"fast_window": 20, "slow_window": 15}) is True
    assert spec.violates_constraint({"fast_window": 5, "slow_window": 15}) is False


def test_declared_points_use_valid_grid_not_raw_product():
    """声明 4（原始笛卡尔积）应被拒：分母是有效组合数，不是"看起来跑满了"。"""
    payload = {
        **BASE,
        "param_grid": {"fast_window": [5, 20], "slow_window": [15, 30]},
        "declared_grid_points": 4,
    }
    with pytest.raises(SpecError, match="declared_grid_points"):
        parse_spec(payload)


def test_all_combos_filtered_out_rejected():
    payload = {
        **BASE,
        "param_grid": {"fast_window": [20], "slow_window": [15]},
        "declared_grid_points": 0,
    }
    with pytest.raises(SpecError, match="网格为空"):
        parse_spec(payload)


def test_declared_points_must_match_grid():
    with pytest.raises(SpecError, match="declared_grid_points"):
        parse_spec({**BASE, "declared_grid_points": 3})


def test_overlapping_windows_rejected():
    payload = {**BASE, "out_sample": {"start": "2021-06-01", "end": "2022-12-31"}}
    with pytest.raises(SpecError, match="样本外区间必须完全在样本内之后"):
        parse_spec(payload)


def test_missing_boundaries_rejected():
    payload = dict(BASE)
    payload.pop("boundaries")
    with pytest.raises(SpecError, match="缺少字段 'boundaries'"):
        parse_spec(payload)


def test_drawdown_boundary_must_be_ratio():
    payload = {**BASE, "boundaries": {**BASE["boundaries"], "max_in_sample_drawdown": 30}}
    with pytest.raises(SpecError, match="max_in_sample_drawdown"):
        parse_spec(payload)


def test_unknown_selection_metric_rejected():
    with pytest.raises(SpecError, match="selection_metric"):
        parse_spec({**BASE, "selection_metric": "sharpe"})


def test_constraint_referring_unknown_param_rejected():
    with pytest.raises(SpecError, match="不存在的参数"):
        parse_spec({**BASE, "constraints": [["fast_window", "nope"]]})


def test_digest_is_stable_and_sensitive_to_boundaries():
    first = parse_spec(BASE).digest()
    second = parse_spec(BASE).digest()
    changed = parse_spec(
        {**BASE, "boundaries": {**BASE["boundaries"], "max_in_sample_drawdown": 0.29}}
    ).digest()
    assert first == second
    assert first != changed, "边界改动必须改变契约摘要，否则报告无法追溯用的是哪版标准"


def test_metric_requires_reason_when_not_computable():
    with pytest.raises(MetricError, match="没写理由"):
        Metric(name="x", value=None, status="not_computable", reason="")
    with pytest.raises(MetricError, match="无数值却标记"):
        Metric(name="x", value=None, status="ok")


def test_bound_check_undetermined_does_not_become_pass():
    metric = unavailable("cumulative_return", "样本不足")
    check = check_bound(metric, threshold=0.1, name="收益", direction=">=")
    assert check.passed is None
    outcome, reasons = summarise([check])
    assert outcome == "undetermined"
    assert reasons


def test_fail_beats_undetermined():
    fail = check_bound(ok("r", 0.01), threshold=0.1, name="收益", direction=">=")
    unknown = check_bound(unavailable("d", "缺数据"), threshold=0.3, name="回撤", direction="<=")
    outcome, reasons = summarise([fail, unknown])
    assert outcome == "fail"
    assert any("收益" in item for item in reasons)
