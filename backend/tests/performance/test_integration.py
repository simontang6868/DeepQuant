"""真实引擎集成测试：行情 → 信号 → 成交 → 账务 → 绩效 → 序列化 → 可复现性 全链路。

本模块不构造孤立的数据结构，而是通过 run_backtest 走完整调用链，
验证各模块之间的实际集成结果（数值与类型，而非「函数能跑」）。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys

import pytest

from deepquant.performance.comparison import compare_metadata
from deepquant.performance.metrics import PerformanceMetrics, compute_performance
from deepquant.performance.serialize import (
    result_from_json,
    result_to_dict,
    result_to_json,
)
from deepquant.strategies.base import Signal
from deepquant.strategies.ma_cross import MaCrossStrategy
from helpers import DATES, FakeStrategy, make_config, make_frame, run_engine


# ===================================================================== #
# 引擎与绩效的实际集成
# ===================================================================== #


def test_run_backtest_auto_populates_performance():
    result = run_engine()
    perf = result.performance
    assert isinstance(perf, PerformanceMetrics)
    assert perf.initial_equity == 100000.0
    assert perf.final_equity == pytest.approx(result.account_states[-1].total_equity)
    assert perf.cumulative_return == pytest.approx(perf.final_equity / 100000.0 - 1.0)
    assert perf.total_pnl == pytest.approx(round(perf.final_equity - 100000.0, 2))
    # 交易统计与实际订单 / 成交一致
    assert perf.trade_stats.n_orders == len(result.orders)
    assert perf.trade_stats.n_fills == len(result.fills)
    assert perf.trade_stats.n_filled_orders == sum(
        1 for o in result.orders if o.status.value == "filled"
    )
    assert perf.trade_stats.n_unfilled_orders == sum(
        1 for o in result.orders if o.status.value != "filled"
    )


def test_auto_performance_matches_explicit_compute():
    # 自动填充与显式调用的口径必须完全一致（幂等，不因调用路径不同而不同）
    result = run_engine()
    assert result.performance == compute_performance(result)


def test_auto_performance_does_not_change_accounting():
    result = run_engine()
    # 自动绩效计算是只读的：账户状态 / 成交 / 订单不应因此发生变化
    baseline = run_engine()
    assert result.account_states == baseline.account_states
    assert result.fills == baseline.fills
    assert result.orders == baseline.orders


def test_rejected_orders_do_not_invalidate_performance():
    # 正常的拒绝订单（现金不足）不应被当作错误：权益序列完整 → 绩效完整
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_engine(
        strategy=FakeStrategy({DATES[0]: Signal.BUY}),
        frame=frame,
        config=make_config(initial_cash=500.0),
    )
    assert result.is_complete is True
    assert result.performance.is_complete is True
    assert result.performance.trade_stats.n_unfilled_orders == 1
    assert result.performance.trade_stats.n_fills == 0
    assert result.performance.final_equity == pytest.approx(500.0)


# ===================================================================== #
# 账务历史案例（可人工复核）
# ===================================================================== #


def test_historical_accounting_case_reproduced():
    result = run_engine()  # 初始资金 100000，费率 simulation_placeholder

    buy = result.fills[0]
    assert buy.quantity == 9900
    assert buy.price == pytest.approx(10.01)
    assert buy.fees.commission == pytest.approx(29.73)
    assert result.account_states[1].cash == pytest.approx(871.27)
    assert result.account_states[1].total_equity == pytest.approx(109771.27)

    sell = result.fills[1]
    assert sell.quantity == 9900
    assert sell.price == pytest.approx(11.99)
    assert sell.fees.commission == pytest.approx(35.61)
    assert sell.fees.stamp_duty == pytest.approx(59.35)
    assert result.account_states[-1].cash == pytest.approx(119477.31)

    perf = result.performance
    assert perf.final_equity == pytest.approx(119477.31)
    assert perf.total_pnl == pytest.approx(19477.31)
    assert perf.cumulative_return == pytest.approx(119477.31 / 100000.0 - 1.0)
    assert perf.trade_stats.n_closed_trades == 1
    assert perf.trade_stats.n_winning_trades == 1
    assert perf.trade_stats.win_rate == pytest.approx(1.0)


# ===================================================================== #
# 序列化 / 可复现性集成（不依赖进程内残留对象）
# ===================================================================== #


def test_engine_result_json_round_trip_without_manual_metrics():
    result = run_engine()
    text = result_to_json(result)
    parsed = json.loads(text)
    # 引擎已自动填充绩效，序列化后不再为 null
    assert parsed["performance"] is not None
    assert parsed["performance"]["initial_equity"] == 100000.0

    restored = result_from_json(text)
    assert result_to_dict(restored) == result_to_dict(result)
    assert restored.performance == result.performance
    assert isinstance(restored.performance, PerformanceMetrics)


def test_engine_records_reproducibility_metadata():
    result = run_engine(strategy=MaCrossStrategy(fast_window=2, slow_window=3))
    doc = result_to_dict(result)
    assert doc["reproducibility"]["inputs_identifiable"] is True
    assert doc["reproducibility"]["process_reproducible"] is True
    assert doc["strategy"]["name"] == "ma_cross"
    assert doc["strategy"]["params"] == {"fast_window": 2, "slow_window": 3}
    assert doc["dataset"]["digest"].startswith("sha256:")
    assert doc["dataset"]["dataset_id"] == "600000"


def test_same_inputs_fairly_comparable_after_json_round_trip():
    original = run_engine()
    restored = result_from_json(result_to_json(run_engine()))
    comparison = compare_metadata(original, restored)
    assert comparison["fairly_comparable"] is True
    assert comparison["differences"] == {}


def test_engine_missing_close_marks_performance_incomplete():
    frame = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, math.nan])
    result = run_engine(
        strategy=FakeStrategy({DATES[0]: Signal.BUY}),
        frame=frame,
        config=make_config(initial_cash=100000.0),
    )
    assert result.is_complete is False
    assert result.performance.is_complete is False
    assert result.performance.final_equity is None
    assert result.performance.total_pnl is None
    assert result.performance.cumulative_return is None
    assert result.performance.incomplete_reasons


def test_deserialization_is_isolated_from_original_process(tmp_path):
    # 独立加载流程：把 JSON 写到文件，在**全新 Python 进程**中反序列化，
    # 不依赖当前进程中残留的原始对象。
    result = run_engine()
    path = tmp_path / "result.json"
    path.write_text(result_to_json(result), encoding="utf-8")

    script = (
        "import json, sys\n"
        "from deepquant.performance.serialize import result_from_json\n"
        "text = open(sys.argv[1], encoding='utf-8').read()\n"
        "r = result_from_json(text)\n"
        "print(json.dumps({\n"
        "  'orders': len(r.orders), 'fills': len(r.fills),\n"
        "  'has_perf': r.performance is not None,\n"
        "  'final': r.performance.final_equity,\n"
        "  'schema': r.schema_version,\n"
        "}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data == {
        "orders": 2,
        "fills": 2,
        "has_perf": True,
        "final": 119477.31,
        "schema": "0.1.0",
    }
