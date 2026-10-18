"""W1 逐窗明细落盘的契约测试。

这些测试守的不是「能不能写出 CSV」，而是四件在历史结果里已经出过问题的语义：

1. **失败窗口不能被静默忽略** —— 少一行，下游分母就变小、正收益率虚高；
2. **不可计算必须留空，不能填 0** —— 0 是「恰好持平」这个事实，不是「不知道」；
3. **不同配置不能互相覆盖** —— 否则两次实验的结果会互相顶掉且无从察觉；
4. **配对不能错位** —— 按位置 zip 两个窗口列表，是这类对照最典型的事故来源。

另有一条元测试：``capture_trades`` 默认关闭时，既有指标必须一字不差
（288 组历史结果的复现基础）。
"""

from __future__ import annotations

import dataclasses
import json

import pandas as pd
import pytest

from deepquant.performance import window_records as wr
from deepquant.performance.window_records import (
    ExperimentContext,
    STATUS_INCOMPLETE,
    STATUS_NO_METRICS,
    STATUS_OOS_FAILED,
    STATUS_OK,
    STATUS_SELECTION_FAILED,
    WindowAlignmentError,
    align_records,
    build_records,
    read_records,
    summarize_records,
    write_records,
)
from deepquant.walkforward.parameter_selection import WindowWFResult
from helpers import make_config, make_frame

DATES = pd.to_datetime(
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
)


def make_wf(
    *,
    window_index: int = 0,
    oos_start: str = "2024-01-04",
    oos_end: str = "2024-01-08",
    selection_success: bool = True,
    oos_success: bool | None = True,
    cumulative_return: float | None = 0.10,
    is_complete: bool | None = True,
    fills: tuple[dict, ...] = (),
    orders: tuple[dict, ...] = (),
    selection_error: str | None = None,
    oos_error: str | None = None,
) -> WindowWFResult:
    # 自洽性：选择失败 / OOS 失败时不可能有收益与期末权益。构造器必须跟着变，
    # 否则测试本身就在制造一个现实里不存在的窗口（实现会拒绝落盘，这是对的）。
    if not selection_success or oos_success is False:
        cumulative_return = None
        is_complete = None
    return WindowWFResult(
        window_index=window_index,
        train_start=pd.Timestamp("2024-01-02"),
        train_end=pd.Timestamp("2024-01-03"),
        oos_start=pd.Timestamp(oos_start),
        oos_end=pd.Timestamp(oos_end),
        train_periods=2,
        oos_periods=2,
        warmup_days=150,
        candidates=({"fast_window": 5, "slow_window": 20},),
        selection_metric="cumulative_return",
        selected_params={"fast_window": 5, "slow_window": 20} if selection_success else None,
        selection_success=selection_success,
        selection_error=selection_error,
        train_candidate_results=(),
        oos_success=oos_success,
        oos_error=oos_error,
        final_equity=110000.0 if cumulative_return is not None else None,
        cumulative_return=cumulative_return,
        max_drawdown_ratio=0.05 if cumulative_return is not None else None,
        trade_count=2 if cumulative_return is not None else None,
        order_count=2 if cumulative_return is not None else None,
        total_fees=10.0 if cumulative_return is not None else None,
        oos_is_complete=is_complete,
        oos_incomplete_reasons=() if is_complete is not False else ("收盘价缺失",),
        fee_source="simulation_placeholder",
        strategy_name="ma_cross",
        strategy_version="1.0.0",
        fills=fills,
        orders=orders,
    )


def make_ctx(experiment_id: str = "exp0000000000001", **overrides) -> ExperimentContext:
    kwargs = dict(
        experiment_id=experiment_id,
        design_version="oos75-v3",
        symbol="600000",
        strategy_name="ma_cross",
        strategy_version="1.0.0",
        initial_cash=100000.0,
        fee_source="simulation_placeholder",
        commission_rate=0.0003,
        min_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.0,
        slippage_buy=0.001,
        slippage_sell=0.001,
        price_limit_mode="board",
        price_limit_rate=0.10,
        dataset_digest="a" * 64,
        dataset_rows=1943,
        dataset_start="2017-01-03",
        dataset_end="2024-12-31",
        universe_id="300-2024",
        universe_size=48,
        oos_window=2,
        step=2,
    )
    kwargs.update(overrides)
    return ExperimentContext(**kwargs)


def make_window_frame(closes=(10.0, 11.0, 12.0)) -> pd.DataFrame:
    n = len(closes)
    opens = [c - 0.5 for c in closes]
    return make_frame(DATES[:n], opens=opens, closes=list(closes))


# ===================================================================== #
# 1. 字段完整性：任务书要求的 13 类信息必须都有列
# ===================================================================== #


def test_schema_covers_required_field_groups():
    columns = set(wr._columns())
    required = {
        # experiment_id
        "experiment_id",
        # 策略名 + 版本
        "strategy_name", "strategy_version",
        # 参数配置
        "params_json",
        # 数据集标识 + 数据指纹
        "symbol", "dataset_digest", "dataset_rows", "dataset_start", "dataset_end",
        # 三段窗口起止（训练 / OOS）
        "train_start", "train_end", "oos_start", "oos_end",
        "train_periods", "oos_periods", "warmup_days",
        # 股票池 + 数量
        "universe_id", "universe_size",
        # 交易与成交记录
        "n_fills", "fills_json", "n_orders", "orders_json", "trade_count",
        # 成本配置
        "fee_source", "commission_rate", "min_commission", "stamp_duty_rate",
        "transfer_fee_rate", "slippage_buy", "slippage_sell", "initial_cash",
        # 策略绩效
        "strategy_cumulative_return", "strategy_max_drawdown", "strategy_final_equity",
        # 同期基准绩效
        "benchmark_kind", "benchmark_cumulative_return", "benchmark_max_drawdown",
        "benchmark_executable", "benchmark_blocked_reason",
        # 策略 − 基准
        "excess_return",
        # 运行状态与异常
        "status", "error", "selection_success", "oos_success", "oos_is_complete",
    }
    assert required <= columns, f"缺列：{sorted(required - columns)}"


def test_ok_window_excess_return_is_strategy_minus_benchmark():
    ctx = make_ctx()
    wf = make_wf(cumulative_return=0.10)
    rec = build_records(ctx, [wf], {0: make_window_frame()})[0]

    assert rec.status == STATUS_OK
    assert rec.benchmark_kind == "buy_and_hold"
    assert rec.benchmark_cumulative_return is not None
    assert rec.excess_return == pytest.approx(
        rec.strategy_cumulative_return - rec.benchmark_cumulative_return
    )
    assert rec.excess_return_na_reason is None


# ===================================================================== #
# 2. 失败窗口：必须落盘、必须带状态、必须不填 0
# ===================================================================== #


def test_selection_failed_window_is_recorded_and_written(tmp_path):
    ctx = make_ctx()
    failed = make_wf(
        window_index=1, selection_success=False, selection_error="无候选满足 min_trades"
    )
    records = build_records(ctx, [failed], {})
    assert len(records) == 1, "失败的窗口不得被丢弃"

    rec = records[0]
    assert rec.status == STATUS_SELECTION_FAILED
    assert rec.strategy_cumulative_return is None
    assert "min_trades" in rec.error

    path = write_records(records, tmp_path)
    back = read_records(path)
    assert len(back) == 1 and back[0].status == STATUS_SELECTION_FAILED


def test_oos_failed_window_keeps_error_and_no_fake_return():
    ctx = make_ctx()
    wf = make_wf(oos_success=False, cumulative_return=None, oos_error="引擎异常")
    rec = build_records(ctx, [wf], {})[0]
    assert rec.status == STATUS_OOS_FAILED
    assert rec.error == "引擎异常"
    assert rec.strategy_cumulative_return is None
    assert rec.excess_return is None
    assert rec.excess_return_na_reason == wr.NA_STRATEGY_MISSING


def test_incomplete_valuation_is_marked_not_treated_as_ok():
    ctx = make_ctx()
    wf = make_wf(is_complete=False)
    rec = build_records(ctx, [wf], {})[0]
    assert rec.status == STATUS_INCOMPLETE
    assert rec.oos_incomplete_reasons == "收盘价缺失"


def test_success_without_metrics_is_no_metrics():
    ctx = make_ctx()
    wf = make_wf(cumulative_return=None)
    rec = build_records(ctx, [wf], {})[0]
    assert rec.status == STATUS_NO_METRICS


def test_missing_values_stay_empty_not_zero_after_roundtrip(tmp_path):
    ctx = make_ctx()
    wf = make_wf(oos_success=False, cumulative_return=None)
    path = write_records(build_records(ctx, [wf], {}), tmp_path)
    rec = read_records(path)[0]
    assert rec.strategy_cumulative_return is None
    assert rec.excess_return is None
    assert rec.benchmark_cumulative_return is None


# ===================================================================== #
# 3. 基准：不可执行 ≠ 0% 收益
# ===================================================================== #


def test_unaffordable_benchmark_is_not_zero_return():
    """整手买不起时，基准不可执行 —— 差值必须为空并写明原因。"""
    ctx = make_ctx(initial_cash=100.0)  # 100 元买不起 1 手 10 元股
    wf = make_wf(cumulative_return=0.05)
    frame = make_window_frame(closes=(10.0, 11.0, 12.0))
    rec = build_records(ctx, [wf], {0: frame})[0]

    assert rec.benchmark_executable is False
    assert rec.benchmark_quantity == 0
    assert rec.benchmark_cumulative_return is None
    assert rec.excess_return is None
    assert rec.excess_return_na_reason == wr.NA_BENCH_NOT_EXECUTABLE


def test_benchmark_without_frame_is_marked_missing():
    ctx = make_ctx()
    rec = build_records(ctx, [make_wf()], {})[0]
    assert rec.benchmark_kind is None
    assert rec.excess_return_na_reason == wr.NA_BENCH_MISSING


# ===================================================================== #
# 4. 写盘：幂等、拒覆盖、多实验互不干扰
# ===================================================================== #


def test_write_then_read_roundtrip_preserves_all_fields(tmp_path):
    ctx = make_ctx()
    fills = ({"fill_id": "F1", "side": "buy", "price": 10.0, "quantity": 100, "fees": 5.0},)
    records = build_records(ctx, [make_wf(fills=fills)], {0: make_window_frame()})
    path = write_records(records, tmp_path)

    back = read_records(path)
    assert len(back) == 1
    original, restored = records[0], back[0]
    for name in wr._columns():
        assert getattr(original, name) == getattr(restored, name), f"字段 {name} 往返不一致"
    assert json.loads(restored.fills_json) == list(fills)


def test_write_is_idempotent_for_same_content(tmp_path):
    ctx = make_ctx()
    records = build_records(ctx, [make_wf()], {0: make_window_frame()})
    first = write_records(records, tmp_path)
    second = write_records(records, tmp_path)  # 不抛错、不重写
    assert first == second
    assert len(read_records(second)) == 1


def test_write_refuses_to_overwrite_different_content(tmp_path):
    ctx = make_ctx()
    write_records(build_records(ctx, [make_wf(cumulative_return=0.10)], {0: make_window_frame()}), tmp_path)
    changed = build_records(ctx, [make_wf(cumulative_return=0.99)], {0: make_window_frame()})
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        write_records(changed, tmp_path)


def test_two_experiments_never_collide(tmp_path):
    a = build_records(make_ctx("exp_a"), [make_wf()], {0: make_window_frame()})
    b = build_records(make_ctx("exp_b"), [make_wf(cumulative_return=0.5)], {0: make_window_frame()})
    pa = write_records(a, tmp_path)
    pb = write_records(b, tmp_path)
    assert pa != pb
    assert pa.parent.name == "exp_a" and pb.parent.name == "exp_b"


def test_write_rejects_mixed_experiments_in_one_call(tmp_path):
    records = build_records(make_ctx("exp_a"), [make_wf()], {}) + build_records(
        make_ctx("exp_b"), [make_wf(window_index=1)], {}
    )
    with pytest.raises(ValueError, match="只能属于一个实验"):
        write_records(records, tmp_path)


def test_duplicate_window_index_is_rejected(tmp_path):
    ctx = make_ctx()
    records = build_records(ctx, [make_wf(), make_wf()], {})
    with pytest.raises(ValueError, match="window_key 重复"):
        write_records(records, tmp_path)


def test_experiment_id_changes_with_config_and_is_stable():
    base = dict(
        design_version="oos75-v3", symbol="600000", strategy_name="ma_cross",
        fee_source="simulation_placeholder", price_limit_mode="board",
        dataset_digest="a" * 64, request_range=("20170101", "20241231"),
        params_grid={"fast": [3, 5], "slow": [20, 60]},
    )
    a = wr.make_experiment_id(**base)
    b = wr.make_experiment_id(**base)
    c = wr.make_experiment_id(**{**base, "dataset_digest": "b" * 64})
    d = wr.make_experiment_id(**{**base, "fee_source": "broker_reference_common_2024"})
    assert a == b, "同配置必须稳定（否则重跑会生成新目录）"
    assert len({a, c, d}) == 3, "换数据/换费率必须换 ID"


# ===================================================================== #
# 5. 配对：按 key 对齐，不允许位置硬凑
# ===================================================================== #


def test_align_pairs_by_key_not_by_position():
    a = build_records(make_ctx("exp"), [make_wf(window_index=0), make_wf(window_index=1)], {})
    b = build_records(make_ctx("exp"), [make_wf(window_index=1), make_wf(window_index=0)], {})
    pairs = align_records(a, b)
    assert [x.window_index for x, _ in pairs] == [0, 1], "配对必须按窗口序号，不按入参顺序"


def test_align_rejects_missing_window():
    a = build_records(make_ctx("exp"), [make_wf(window_index=0), make_wf(window_index=1)], {})
    b = build_records(make_ctx("exp"), [make_wf(window_index=0)], {})
    with pytest.raises(WindowAlignmentError, match="窗口集合不一致"):
        align_records(a, b)


def test_align_rejects_oos_date_mismatch():
    a = build_records(make_ctx("exp"), [make_wf(window_index=0)], {})
    b = build_records(
        make_ctx("exp"), [make_wf(window_index=0, oos_start="2024-01-05")], {}
    )
    with pytest.raises(WindowAlignmentError, match="OOS 区间不一致"):
        align_records(a, b)


def test_align_rejects_cross_experiment_pairing():
    a = build_records(make_ctx("exp_a"), [make_wf()], {})
    b = build_records(make_ctx("exp_b"), [make_wf()], {})
    with pytest.raises(WindowAlignmentError):
        align_records(a, b)


# ===================================================================== #
# 6. 汇总：两个「差值」必须分开命名
# ===================================================================== #


def test_summarize_separates_paired_median_from_median_difference():
    """构造一个「配对差中位数」与「中位数之差」明显不同的样本。

    直接改写记录里的收益字段（不经价格管道）：本用例要验的是汇总口径，
    价格管道另有测试。样本取自经典反例——
    策略 [0, 0, .10, .10] 中位数 .05，基准 [0, .10, .10, .10] 中位数 .10，
    中位数之差 −.05；而逐窗差 [0, −.10, 0, 0] 的中位数是 **0**。
    """
    ctx = make_ctx()
    specs = [(0.0, 0.0), (0.0, 0.10), (0.10, 0.10), (0.10, 0.10)]
    records = []
    for i, (strat, bench) in enumerate(specs):
        base = build_records(ctx, [make_wf(window_index=i)], {})[0]
        records.append(
            dataclasses.replace(
                base,
                strategy_cumulative_return=strat,
                benchmark_cumulative_return=bench,
                excess_return=strat - bench,
            )
        )
    summary = summarize_records(records)

    assert summary["n_paired"] == 4
    assert summary["median_strategy_minus_median_benchmark"] == pytest.approx(-0.05)
    assert summary["median_paired_excess"] == pytest.approx(0.0)
    assert "不是连续账户净值" in summary["note"]


def test_summarize_counts_statuses_including_failures():
    ctx = make_ctx()
    records = build_records(
        ctx,
        [
            make_wf(window_index=0),
            make_wf(window_index=1, selection_success=False),
            make_wf(window_index=2, oos_success=False, cumulative_return=None),
        ],
        {},
    )
    summary = summarize_records(records)
    assert summary["n_windows"] == 3
    assert summary["status_counts"][STATUS_OK] == 1
    assert summary["status_counts"][STATUS_SELECTION_FAILED] == 1
    assert summary["status_counts"][STATUS_OOS_FAILED] == 1


# ===================================================================== #
# 7. 无回归：capture_trades 默认关闭时既有指标一字不差
# ===================================================================== #


def _synthetic_frame(n: int = 400) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=n)
    # 确定性锯齿行情：保证均线策略会交叉、会有成交
    closes = [10.0 + (i % 40) * 0.1 for i in range(n)]
    opens = [c for c in closes]
    return make_frame(idx, opens=opens, closes=closes)


def test_capture_trades_off_keeps_metrics_and_on_adds_fills():
    from deepquant.strategies.ma_cross import MaCrossStrategy
    from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2

    frame = _synthetic_frame()
    config = make_config(initial_cash=100000.0)
    grid = GridSearchConfig(
        fast_candidates=(3, 5), slow_candidates=(10, 20),
        min_train_periods=150, min_oos_periods=50, min_trades=1, max_combinations=4,
    )

    def factory(p):
        return MaCrossStrategy(fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"]))

    _, off, sum_off = run_walkforward_v2(frame, config, strategy_factory=factory, grid_config=grid)
    _, on, sum_on = run_walkforward_v2(
        frame, config, strategy_factory=factory, grid_config=grid, capture_trades=True
    )

    assert sum_off.median_cumulative_return == sum_on.median_cumulative_return
    assert sum_off.total_oos_trades == sum_on.total_oos_trades
    assert sum_off.total_oos_fees == sum_on.total_oos_fees
    assert all(r.fills == () and r.orders == () for r in off), "默认必须不采集"

    captured = [f for r in on for f in r.fills]
    assert captured, "开启采集后至少应有一笔成交（否则等于没验证）"
    assert set(captured[0]) == {
        "fill_id", "order_id", "symbol", "side", "fill_date", "price", "quantity", "fees"
    }
    assert all("status" in o for r in on for o in r.orders)
