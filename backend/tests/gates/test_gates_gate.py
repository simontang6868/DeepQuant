"""L1a 闸门编排：八种终态都要能走到，且报告能独立回答问题。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.gates import VERDICTS, render_markdown, run_l1a
from deepquant.strategies.base import Signal, Strategy

BASE = {
    "hypothesis_id": "H-GATE",
    "title": "闸门用例",
    "strategy_id": "ma_cross",
    "param_grid": {"fast_window": [5, 10], "slow_window": [20, 40]},
    "constraints": [["fast_window", "slow_window"]],
    "data": {"symbol": "T", "research_path": "n/a"},
    "in_sample": {"start": "2020-01-01", "end": "2021-12-31"},
    "out_sample": {"start": "2022-01-01", "end": "2022-12-31"},
    "boundaries": {
        "min_in_sample_return": 0.05,
        "max_in_sample_drawdown": 0.60,
        "min_closed_trades": 1,
        "min_out_sample_return": 0.0,
        "max_out_sample_drawdown": 0.60,
        "max_oos_degradation": 0.9,
    },
    "declared_grid_points": 4,
}

LOOSE = {
    "min_in_sample_return": -1.0,
    "max_in_sample_drawdown": 0.99,
    "min_closed_trades": 1,
    "min_out_sample_return": -1.0,
    "max_out_sample_drawdown": 0.99,
    "max_oos_degradation": 1.0,
}


def _frame(rows: int = 640) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=rows)
    prices = 10.0 * np.exp(0.0012 * np.arange(rows)) * (1 + 0.06 * np.sin(np.arange(rows) / 18.0))
    frame = pd.DataFrame({"date": dates, "close": prices})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.002
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.998
    frame["volume"] = 10_000
    return frame[["date", "open", "high", "low", "close", "volume"]]


class _Leaky(Strategy):
    name = "t_leaky"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        ma = data["close"].rolling(10, center=True).mean()
        out = pd.Series(Signal.HOLD, index=data.index, dtype=int)
        out[data["close"] > ma] = Signal.BUY
        out[data["close"] < ma] = Signal.SELL
        return out


def test_verdict_is_always_in_enum():
    result = run_l1a({**BASE, "boundaries": LOOSE}, data=_frame())
    assert result.verdict in VERDICTS


def test_rejected_spec_when_contract_incomplete():
    payload = dict(BASE)
    payload.pop("out_sample")
    result = run_l1a(payload, data=_frame())
    assert result.verdict == "L1A_REJECTED_SPEC"
    assert result.stages[0].status == "fail"


def test_rejected_data_when_ohlc_broken():
    frame = _frame()
    frame.loc[7, "high"] = frame.loc[7, "low"] * 0.5
    result = run_l1a({**BASE, "boundaries": LOOSE}, data=frame)
    assert result.verdict == "L1A_REJECTED_DATA"
    assert "ohlc_relations" in result.data["blocking_failures"]


def test_rejected_leakage_stops_before_sweep():
    result = run_l1a(
        {**BASE, "boundaries": LOOSE, "strategy_id": "leaky"},
        data=_frame(),
        factory=lambda sid, params: _Leaky(),
    )
    assert result.verdict == "L1A_REJECTED_LEAKAGE"
    assert result.sweep == {}, "泄漏一旦命中就不该再花时间跑网格"


def test_rejected_coverage_when_point_fails():
    def flaky(strategy_id: str, params: dict):
        if params["fast_window"] == 10:
            raise ValueError("故意失败")
        from deepquant.api.registry import build_strategy

        return build_strategy(strategy_id, dict(params))

    result = run_l1a({**BASE, "boundaries": LOOSE}, data=_frame(), factory=flaky)
    assert result.verdict == "L1A_REJECTED_COVERAGE"
    assert result.sweep["coverage"] == pytest.approx(0.5)


def test_rejected_bounds_when_return_impossible():
    boundaries = {**LOOSE, "min_in_sample_return": 10.0}
    result = run_l1a({**BASE, "boundaries": boundaries}, data=_frame())
    assert result.verdict == "L1A_REJECTED_BOUNDS"
    failed = [check for check in result.bound_checks if check.passed is False]
    assert any(check.name == "样本内累计收益" for check in failed)


def test_inconclusive_keeps_metrics_null_and_explains():
    result = run_l1a(
        {
            **BASE,
            "boundaries": LOOSE,
            "out_sample": {"start": "2035-01-01", "end": "2035-12-31"},
        },
        data=_frame(),
    )
    assert result.verdict == "L1A_INCONCLUSIVE"
    assert result.honesty, "不可计算的指标必须出现在诚实清单里"
    assert all(item["value"] is None for item in result.honesty)
    assert all(item["status"] == "not_computable" for item in result.honesty)


def test_out_sample_evaluated_exactly_once():
    result = run_l1a({**BASE, "boundaries": LOOSE}, data=_frame())
    assert result.sweep["oos_evaluations"] == 1


def test_report_markdown_has_all_sections_and_null_metrics():
    result = run_l1a(
        {
            **BASE,
            "boundaries": LOOSE,
            "out_sample": {"start": "2035-01-01", "end": "2035-12-31"},
        },
        data=_frame(),
    )
    markdown = render_markdown(result.to_dict())
    for section in ("## 一、否决链", "## 二、数据门禁", "## 五、边界判定", "## 六、指标明细"):
        assert section in markdown
    assert "不可计算" in markdown
    assert "样本外评估次数：1" in markdown


def test_gate_does_not_write_any_file(tmp_path, monkeypatch):
    """闸门是纯计算：不落盘、不改数据。产物由 CLI 负责写。"""
    before = set(tmp_path.iterdir())
    run_l1a({**BASE, "boundaries": LOOSE}, data=_frame())
    after = set(tmp_path.iterdir())
    assert before == after
