"""结果元数据比较测试：关键配置一致才判定为公平可比。"""

from __future__ import annotations

from deepquant.performance.comparison import compare_metadata, result_profile
from deepquant.strategies.ma_cross import MaCrossStrategy
from helpers import DATES, make_config, make_frame, run_engine

MA_FRAME = make_frame(
    DATES, opens=[10.0, 10.0, 11.0, 12.0, 13.0, 14.0], closes=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
)
BASE_CONFIG = make_config(initial_cash=100000.0)


def test_identical_runs_are_fairly_comparable():
    result_a = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is True
    assert comparison["differences"] == {}


def test_different_dataset_is_not_comparable():
    other_frame = make_frame(
        DATES, opens=[10.0, 10.0, 11.0, 12.0, 13.0, 14.0], closes=[10.0, 10.0, 12.0, 12.0, 13.0, 15.0]
    )
    result_a = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(frame=other_frame, config=BASE_CONFIG)
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is False
    assert "dataset_digest" in comparison["differences"]
    assert any("dataset_digest" in reason for reason in comparison["reasons"])


def test_different_fees_is_not_comparable():
    result_a = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(
        frame=MA_FRAME, config=make_config(initial_cash=100000.0, commission_rate=0.001)
    )
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is False
    assert "fees" in comparison["differences"]


def test_different_market_rules_is_not_comparable():
    from deepquant.market.rules import a_share_rules

    result_a = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(
        frame=MA_FRAME,
        config=make_config(initial_cash=100000.0, market_rules=a_share_rules(price_limit_rate=0.1)),
    )
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is False
    assert "market_rules" in comparison["differences"]


def test_different_period_is_not_comparable():
    result_a = run_engine(frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(
        frame=MA_FRAME, config=make_config(initial_cash=100000.0, start_date="2024-01-04")
    )
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is False
    assert "period" in comparison["differences"]


def test_different_strategy_params_still_comparable():
    result_a = run_engine(strategy=MaCrossStrategy(fast_window=2, slow_window=3), frame=MA_FRAME, config=BASE_CONFIG)
    result_b = run_engine(strategy=MaCrossStrategy(fast_window=2, slow_window=4), frame=MA_FRAME, config=BASE_CONFIG)
    comparison = compare_metadata(result_a, result_b)
    assert comparison["fairly_comparable"] is True
    assert comparison["strategy_differs"] is True
    assert "strategy" in comparison["differences"]


def test_result_profile_exposes_key_fields():
    profile = result_profile(run_engine(frame=MA_FRAME, config=BASE_CONFIG))
    assert profile["symbol"] == "600000"
    assert profile["dataset_digest"].startswith("sha256:")
    assert profile["exec_price_rule"] == "next_open"
    assert profile["fees"]["fee_source"] == "simulation_placeholder"
