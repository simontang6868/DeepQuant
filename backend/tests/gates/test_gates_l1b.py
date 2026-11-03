"""L1b 精验闸门：八种终态各自可实测，成交价核验能抓到偏差。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.gates.l1b import (
    VERDICT_DECAY,
    VERDICT_FILL_RATE,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASSED,
    VERDICT_PREREQUISITE,
    VERDICT_SIGN_REVERSAL,
    ExecutionSpec,
    L1bSpecError,
    explain_fill_prices,
    parse_execution,
    run_l1b,
)
from deepquant.strategies.base import Signal, Strategy


class ScriptedStrategy(Strategy):
    """在指定位置发出 BUY / SELL，用于精确控制成交笔数与盈亏方向。"""

    name = "scripted"

    def __init__(self, buy_at: int = 5, sell_at: int = 60) -> None:
        self.buy_at = buy_at
        self.sell_at = sell_at

    @property
    def params(self) -> dict:
        return {"buy_at": self.buy_at, "sell_at": self.sell_at}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        signals = pd.Series(int(Signal.HOLD), index=data.index, dtype=int)
        if self.buy_at < len(data):
            signals.iloc[self.buy_at] = int(Signal.BUY)
        if self.sell_at < len(data):
            signals.iloc[self.sell_at] = int(Signal.SELL)
        return signals


def _factory(strategy_id: str, params: dict) -> Strategy:
    return ScriptedStrategy(**params)


def _frame(days: int = 120, start: float = 10.0, step: float = 0.02) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=days)
    close = start + np.arange(days) * step
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 10_000_000.0,
        }
    )


def _payload(**execution) -> dict:
    return {
        "hypothesis_id": "H-TEST-L1B",
        "title": "L1b 测试",
        "strategy_id": "scripted",
        "param_grid": {"buy_at": [5], "sell_at": [60]},
        "data": {
            "symbol": "TEST",
            "research_path": "data/qfq_all/000001.SZ.qfq.csv",
            "trading_path": None,
        },
        "in_sample": {"start": "2020-01-01", "end": "2020-12-31"},
        "out_sample": {"start": "2021-01-01", "end": "2021-12-31"},
        "boundaries": {
            "min_in_sample_return": 0.0,
            "max_in_sample_drawdown": 0.99,
            "min_closed_trades": 1,
            "min_out_sample_return": -1.0,
            "max_out_sample_drawdown": 0.99,
            "max_oos_degradation": 0.99,
        },
        "initial_cash": 100_000.0,
        "declared_grid_points": 1,
        "execution": execution,
    }


BEST = {"buy_at": 5, "sell_at": 60}


def test_passed_when_friction_does_not_bind() -> None:
    result = run_l1b(
        _payload(participation_limit=1.0, queue_position=0.0, slippage_mode="none"),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_PASSED
    assert result.retention == pytest.approx(1.0, abs=1e-9)
    assert result.stressed.fill_rate_market == pytest.approx(1.0)


def test_sign_reversal_rejected_by_large_slippage() -> None:
    result = run_l1b(
        _payload(slippage_mode="constant", slippage_bps=1200.0),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_SIGN_REVERSAL
    assert result.stressed.cumulative_return < 0
    assert result.baseline.cumulative_return > 0


def test_fill_rate_rejected_by_tiny_participation() -> None:
    result = run_l1b(
        _payload(participation_limit=1e-8, queue_position=0.9),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_FILL_RATE
    assert result.stressed.fill_rate_market == 0.0
    # 成交率不足必须**先于**符号判定报出来：判定链在此中断，
    # 不应再出现"收益符号未反转"这一项（否则归因会变成"策略失效"）。
    names = [check["name"] for check in result.checks]
    assert "市场性成交率达标" in names
    assert "收益符号未反转" not in names


def test_decay_rejected_by_moderate_slippage() -> None:
    result = run_l1b(
        _payload(slippage_mode="constant", slippage_bps=500.0, max_return_decay=0.3),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_DECAY
    assert 0 < result.stressed.cumulative_return < result.baseline.cumulative_return


def test_inconclusive_when_baseline_is_not_positive() -> None:
    falling = _frame(step=-0.05)
    result = run_l1b(
        _payload(),
        best_params=BEST,
        data=falling,
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert result.verdict == VERDICT_INCONCLUSIVE
    assert any("两级口径脱节" in check["detail"] for check in result.checks)


def test_prerequisite_blocks_pass_verdict() -> None:
    result = run_l1b(
        _payload(participation_limit=1.0, queue_position=0.0),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_REJECTED_BOUNDS",
    )
    assert result.verdict == VERDICT_PREREQUISITE
    assert any("不构成放行结论" in item for item in result.limitations)


def test_unreadable_data_rejects_without_crashing() -> None:
    payload = _payload()
    payload["data"]["research_path"] = "data/qfq_all/__missing__.csv"
    result = run_l1b(payload, best_params=BEST, strategy_factory=_factory)
    assert result.verdict == "L1B_REJECTED_DATA"


def test_explain_fill_prices_detects_deviation() -> None:
    frame = _frame()
    day = f"{frame['date'].iloc[10]:%Y-%m-%d}"
    open_price = float(frame["open"].iloc[10])
    good = {
        "fill_id": "F1",
        "day": day,
        "side": "BUY",
        "price": open_price,
        "unit_impact": 0.0,
    }
    bad = {**good, "fill_id": "F2", "price": open_price * 1.05}
    report = explain_fill_prices([good, bad], frame.set_index("date"), tolerance_ticks=1.0)
    assert report["fills"] == 2
    assert report["unexplained"] == 1
    assert report["examples"][0]["fill_id"] == "F2"


def test_execution_spec_rejects_out_of_range() -> None:
    with pytest.raises(L1bSpecError, match="participation_limit"):
        ExecutionSpec(participation_limit=0.0)
    with pytest.raises(L1bSpecError, match="max_return_decay"):
        ExecutionSpec(max_return_decay=1.0)
    with pytest.raises(L1bSpecError, match="min_fill_rate"):
        ExecutionSpec(min_fill_rate=1.5)


def test_parse_execution_rejects_unknown_mode() -> None:
    with pytest.raises(L1bSpecError, match="未知的 slippage_mode"):
        parse_execution({"slippage_mode": "magic"})


def test_execution_digest_is_stable_and_sensitive() -> None:
    first = ExecutionSpec(participation_limit=0.1).digest
    second = ExecutionSpec(participation_limit=0.1).digest
    third = ExecutionSpec(participation_limit=0.2).digest
    assert first == second
    assert first != third


def test_no_trading_layer_is_declared_in_limitations() -> None:
    result = run_l1b(
        _payload(participation_limit=1.0),
        best_params=BEST,
        data=_frame(),
        strategy_factory=_factory,
        l1a_verdict="L1A_PASSED",
    )
    assert any("未执行涨跌停判定" in item for item in result.limitations)
