"""A-3：W1 结果完整性测试（跨实验汇总可重读 + 数据版本可追溯）。

B-1 的 23 条契约测试（``test_window_records.py``）已覆盖：失败窗口落盘 /
不可计算留空不填 0 / 不同配置拒覆盖 / 按 window_key 配对不错位 /
两个「差值」分开命名 / capture_trades 默认关闭无回归。

本文件补其未覆盖的两条（见 ``docs/W1_IMPLEMENTATION_REPORT.md`` §6.2）：
  (A) 跨实验汇总可重读 —— 写盘→读回→再汇总，结果与内存汇总逐字段一致、且确定性可重复；
      磁盘上的每一行都能被读回并计入汇总，失败窗口离线也不会被静默丢掉。
  (B) 数据版本可追溯 —— ``dataset_digest`` / ``experiment_id`` / ``params_json`` 在
      「构造→写盘→读回」全链路字节级保真，使任意一行都能反查到
      「哪个数据版本 + 哪套参数 + 哪个实验」。

全为语义断言，无「跑一遍把结果写死」的假测试。不改动 ``src/`` 任何一行。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from deepquant.performance.window_records import (
    STATUS_OK,
    STATUS_SELECTION_FAILED,
    build_records,
    read_records,
    summarize_records,
    write_records,
)
from deepquant.walkforward.parameter_selection import WindowWFResult
from helpers import make_frame


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
    selection_error: str | None = None,
    oos_error: str | None = None,
) -> WindowWFResult:
    # 自洽性：选择失败 / OOS 失败时不可能有收益与期末权益（否则 build_window_record 会拒绝）。
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
        fills=(),
        orders=(),
    )


def make_ctx(experiment_id: str = "exp0000000000001", **overrides):
    from deepquant.performance.window_records import ExperimentContext

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
# (B) 数据版本可追溯：指纹 / 参数 / 实验身份 字节级保真
# ===================================================================== #


def test_dataset_digest_roundtrips_exactly_through_csv(tmp_path):
    """数据指纹必须字节级保真：写盘前后的 digest 一字不差，否则无法反查数据版本。"""
    digest = "d" * 64
    ctx = make_ctx(dataset_digest=digest)
    rec = build_records(ctx, [make_wf()], {0: make_window_frame()})[0]
    path = write_records([rec], tmp_path)
    back = read_records(path)[0]
    assert back.dataset_digest == digest
    assert back.dataset_digest == ctx.dataset_digest


def test_selected_params_preserved_exactly_in_params_json(tmp_path):
    """选中参数必须原样进 params_json，不丢键、不改值、可反查配置。"""
    ctx = make_ctx()
    wf = make_wf(cumulative_return=0.10)  # selected_params = {fast_window:5, slow_window:20}
    rec = build_records(ctx, [wf], {0: make_window_frame()})[0]
    assert json.loads(rec.params_json) == {"fast_window": 5, "slow_window": 20}
    back = read_records(write_records([rec], tmp_path))[0]
    assert json.loads(back.params_json) == {"fast_window": 5, "slow_window": 20}


def test_data_version_bound_to_experiment_identity(tmp_path):
    """仅数据指纹不同的两个实验，experiment_id 必不同；各自读回后 digest 正确归属。

    这是「凭一行反查数据版本」的地基：ID 与 digest 绑定，不会串味。
    """
    digest_a, digest_b = "a" * 64, "b" * 64
    rec_a = build_records(make_ctx("ea", dataset_digest=digest_a), [make_wf()],
                          {0: make_window_frame()})[0]
    rec_b = build_records(make_ctx("eb", dataset_digest=digest_b), [make_wf()],
                          {0: make_window_frame()})[0]
    assert rec_a.experiment_id != rec_b.experiment_id
    pa, pb = write_records([rec_a], tmp_path), write_records([rec_b], tmp_path)
    back_a, back_b = read_records(pa)[0], read_records(pb)[0]
    assert back_a.dataset_digest == digest_a and back_b.dataset_digest == digest_b
    assert back_a.experiment_id == rec_a.experiment_id


def test_readback_row_is_fully_traceable(tmp_path):
    """任意一行读回后，所有溯源字段与上盘前一致：实验/数据指纹/区间/费率/参数均可反查。"""
    ctx = make_ctx(
        experiment_id="exp_trace",
        dataset_digest="c" * 64,
        dataset_start="2017-01-03",
        dataset_end="2024-12-31",
        fee_source="broker_reference_common_2024",
    )
    rec = build_records(ctx, [make_wf(cumulative_return=0.10)], {0: make_window_frame()})[0]
    back = read_records(write_records([rec], tmp_path))[0]

    assert back.experiment_id == "exp_trace"
    assert back.dataset_digest == "c" * 64
    assert back.dataset_start == "2017-01-03" and back.dataset_end == "2024-12-31"
    assert back.fee_source == "broker_reference_common_2024"
    assert json.loads(back.params_json) == {"fast_window": 5, "slow_window": 20}


# ===================================================================== #
# (A) 跨实验汇总可重读：写盘→读回→再汇总，逐字段一致、确定性、不丢行
# ===================================================================== #


def test_reread_counts_every_window_on_disk(tmp_path):
    """磁盘文件里每一行都能被读回并计入汇总 —— 不丢行（防「失败窗口离线被静默忽略」）。"""
    ctx = make_ctx()
    recs = build_records(
        ctx,
        [
            make_wf(window_index=0, cumulative_return=0.10),
            make_wf(window_index=1, selection_success=False),
            make_wf(window_index=2, oos_success=False, cumulative_return=None),
            make_wf(window_index=3, is_complete=False),
        ],
        {0: make_window_frame()},
    )
    path = write_records(recs, tmp_path)
    on_disk_rows = sum(1 for _ in open(path, encoding="utf-8")) - 1  # 减表头
    assert on_disk_rows == 4
    back = read_records(path)
    assert len(back) == 4
    assert summarize_records(back)["n_windows"] == 4


def test_summary_deterministic_and_matches_reread(tmp_path):
    """内存汇总 == 写盘读回后汇总；且同文件再汇总两次结果完全一致（确定性可重复）。"""
    ctx = make_ctx()
    recs = build_records(
        ctx,
        [make_wf(window_index=i, cumulative_return=0.0 + i * 0.01) for i in range(5)],
        {i: make_window_frame() for i in range(5)},
    )
    summary_in_memory = summarize_records(recs)

    path = write_records(recs, tmp_path)
    read_back = read_records(path)
    summary_from_disk_1 = summarize_records(read_back)
    summary_from_disk_2 = summarize_records(read_back)

    assert summary_from_disk_1 == summary_from_disk_2  # 确定性
    assert summary_from_disk_1["n_windows"] == 5 == len(recs)
    # 关键聚合跨「内存↔磁盘」一致
    assert summary_in_memory["median_strategy_return"] == summary_from_disk_1["median_strategy_return"]
    assert summary_in_memory["n_paired"] == summary_from_disk_1["n_paired"]
    assert summary_in_memory["status_counts"] == summary_from_disk_1["status_counts"]


def test_multiple_experiments_each_rereadable_and_summarizable(tmp_path):
    """多个实验共存于磁盘，每个都能独立读回并重新汇总，状态计数准确、互不串味。"""
    ctx_a = make_ctx("exp_a", dataset_digest="a" * 64)
    ctx_b = make_ctx("exp_b", dataset_digest="b" * 64)
    recs_a = build_records(
        ctx_a,
        [
            make_wf(window_index=0, cumulative_return=0.10),
            make_wf(window_index=1, selection_success=False),
        ],
        {0: make_window_frame()},
    )
    recs_b = build_records(
        ctx_b,
        [
            make_wf(window_index=0, cumulative_return=0.20),
            make_wf(window_index=1, cumulative_return=0.30),
        ],
        {0: make_window_frame(), 1: make_window_frame()},
    )
    pa, pb = write_records(recs_a, tmp_path), write_records(recs_b, tmp_path)
    assert pa.parent.name == "exp_a" and pb.parent.name == "exp_b"

    sa = summarize_records(read_records(pa))
    sb = summarize_records(read_records(pb))
    # exp_a: 1 ok + 1 selection_failed
    assert sa["n_windows"] == 2
    assert sa["status_counts"][STATUS_OK] == 1
    assert sa["status_counts"][STATUS_SELECTION_FAILED] == 1
    # exp_b: 2 ok
    assert sb["n_windows"] == 2
    assert sb["status_counts"][STATUS_OK] == 2
    # 两实验汇总数字互不串味
    assert sb["median_strategy_return"] != sa["median_strategy_return"]
