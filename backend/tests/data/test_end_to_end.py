"""端到端数据契约测试：CSV → 加载 → 校验 → 信号 → 回测 → 绩效 → JSON 往返。

使用小型确定性人工数据，验证数据层、策略层、回测引擎与结果序列化之间的接口一致，
并确认回测引擎不会修改调用方传入的 DataFrame。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.data.loaders.csv_loader import load_daily_csv
from deepquant.data.pipeline import ingest_daily_csv
from deepquant.data.storage import io as storage_io
from deepquant.data.validation.checks import validate_daily
from deepquant.performance.reproducibility import dataset_summary
from deepquant.performance.serialize import result_from_json, result_to_dict, result_to_json
from deepquant.strategies.base import Signal, Strategy

# 确定性 6 行行情：与历史账务案例一致（买 2024-01-03 开盘 10 → 卖 2024-01-05 开盘 12）
CLEAN_CSV = (
    "date,open,high,low,close,volume\n"
    "2024-01-02,10.00,10.00,10.00,10.00,1000\n"
    "2024-01-03,10.00,11.00,10.00,11.00,1000\n"
    "2024-01-04,11.00,12.00,11.00,12.00,1000\n"
    "2024-01-05,12.00,13.00,12.00,13.00,1000\n"
    "2024-01-08,13.00,14.00,13.00,14.00,1000\n"
    "2024-01-09,14.00,15.00,14.00,15.00,1000\n"
)

BUY_DATE = pd.Timestamp("2024-01-02")
SELL_DATE = pd.Timestamp("2024-01-04")


class StepStrategy(Strategy):
    """确定性事件型策略：在预置日期发出信号，其余 HOLD。"""

    name = "step"
    version = "0.0.0"

    def __init__(self, signal_map: dict):
        self._map = {pd.Timestamp(k): int(v) for k, v in signal_map.items()}

    @property
    def params(self) -> dict:
        return {"signals": {str(k): int(v) for k, v in self._map.items()}}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        for ts, value in self._map.items():
            if ts in s.index:  # 只写行情中存在的日期，避免 .loc 扩充索引
                s.loc[ts] = value
        return s


def _write(tmp_path, text, name="600000.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _config() -> BacktestConfig:
    return BacktestConfig(symbol="600000", initial_cash=100000.0)


def _strategy() -> StepStrategy:
    return StepStrategy({BUY_DATE: Signal.BUY, SELL_DATE: Signal.SELL})


# ===================================================================== #
# 完整链路
# ===================================================================== #


def test_csv_to_backtest_to_json_end_to_end(tmp_path):
    # CSV → 加载
    df = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]

    # 加载 → 校验
    report = validate_daily(df)
    assert report.is_valid and report.issues == []

    # 校验通过的数据可直接进入回测（无需额外转换）
    result = run_backtest(df, _strategy(), _config())
    assert len(result.orders) == 2
    assert len(result.fills) == 2
    assert len(result.account_states) == len(df)
    assert result.is_complete is True
    assert result.account_states[-1].cash == pytest.approx(119477.31)

    # 绩效自动填充且口径一致
    perf = result.performance
    assert perf is not None
    assert perf.final_equity == pytest.approx(119477.31)
    assert perf.trade_stats.n_closed_trades == 1
    assert perf.trade_stats.win_rate == pytest.approx(1.0)

    # 结果序列化 → 反序列化
    text = result_to_json(result)
    parsed = json.loads(text)  # 标准 JSON 可解析
    assert parsed["schema_version"] == "0.1.0"
    restored = result_from_json(text)
    assert result_to_dict(restored) == result_to_dict(result)


def test_processed_parquet_enters_backtest(tmp_path):
    # 导入流水线（原始/处理分离）产出的处理后数据可直接进入回测
    result = ingest_daily_csv(
        _write(tmp_path, CLEAN_CSV, "src.csv"),
        symbol="600000",
        data_root=tmp_path / "data",
    )
    cleaned = storage_io.read_daily_parquet(result.processed_path)
    assert str(cleaned["date"].dtype).startswith("datetime64")

    back = run_backtest(cleaned, _strategy(), _config())
    assert back.account_states[-1].cash == pytest.approx(119477.31)


def test_run_backtest_does_not_mutate_input_frame(tmp_path):
    df = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    snapshot = df.copy(deep=True)
    run_backtest(df, _strategy(), _config())
    pd.testing.assert_frame_equal(df, snapshot)  # 引擎不得修改调用方数据


# ===================================================================== #
# 数据契约一致性
# ===================================================================== #


def test_digest_stable_across_csv_write_and_read(tmp_path):
    # 同一逻辑数据经「加载 → 写出 → 再读入」后，内容摘要必须一致
    original = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    round_trip_path = tmp_path / "roundtrip.csv"
    storage_io.write_daily_csv(original, round_trip_path)
    reloaded = load_daily_csv(round_trip_path)

    assert dataset_summary(reloaded)["digest"] == dataset_summary(original)["digest"]


def test_digest_changes_when_content_changes(tmp_path):
    base = load_daily_csv(_write(tmp_path, CLEAN_CSV, "a.csv"))
    changed_text = CLEAN_CSV.replace("2024-01-09,14.00,15.00,14.00,15.00,1000",
                                     "2024-01-09,14.00,15.00,14.00,15.50,1000")
    changed = load_daily_csv(_write(tmp_path, changed_text, "b.csv"))
    assert dataset_summary(changed)["digest"] != dataset_summary(base)["digest"]


def test_duplicate_dates_from_csv_rejected_by_engine(tmp_path):
    # 加载层保留重复日期交由校验层处理；回测引擎则明确拒绝重复（不静默去重）
    dup_csv = (
        "date,open,high,low,close,volume\n"
        "2024-01-02,10.00,10.00,10.00,10.00,1000\n"
        "2024-01-02,10.00,10.00,10.00,10.00,1000\n"
    )
    df = load_daily_csv(_write(tmp_path, dup_csv, "dup.csv"))
    assert validate_daily(df).has_code("duplicate_dates")  # 校验层：可清洗的告警

    with pytest.raises(ValueError, match="重复"):
        run_backtest(df, _strategy(), _config())  # 引擎：不静默去重


def test_unsorted_csv_is_sorted_by_loader_for_backtest(tmp_path):
    # 加载层会按日期升序排列，因此乱序 CSV 仍可进入回测
    unsorted_csv = (
        "date,open,high,low,close,volume\n"
        "2024-01-03,10.00,11.00,10.00,11.00,1000\n"
        "2024-01-02,10.00,10.00,10.00,10.00,1000\n"
    )
    df = load_daily_csv(_write(tmp_path, unsorted_csv, "unsorted.csv"))
    assert df["date"].is_monotonic_increasing
    result = run_backtest(df, StepStrategy({}), _config())
    assert len(result.account_states) == 2
