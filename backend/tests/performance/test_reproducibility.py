"""可复现性测试：数据集摘要、策略元数据、可复现性判定。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.performance.reproducibility import (
    capture_strategy_metadata,
    dataset_summary,
    reproducibility_report,
)
from deepquant.performance.serialize import config_summary
from deepquant.strategies.base import Signal, Strategy
from deepquant.strategies.ma_cross import MaCrossStrategy
from helpers import DATES, make_config, make_frame, run_engine

# ===================================================================== #
# 数据集摘要
# ===================================================================== #


def test_same_data_same_digest():
    frame = make_frame(DATES[:3], opens=[10.0, 12.0, 11.0], closes=[10.0, 12.0, 11.0])
    assert dataset_summary(frame)["digest"] == dataset_summary(frame.copy())["digest"]


def test_content_change_changes_digest():
    frame_a = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, 10.0])
    frame_b = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, 10.5])
    assert dataset_summary(frame_a)["digest"] != dataset_summary(frame_b)["digest"]


def test_column_order_is_standardized():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    reordered = frame[["volume", "close", "low", "high", "open"]]
    assert dataset_summary(frame)["digest"] == dataset_summary(reordered)["digest"]


def test_row_order_is_standardized():
    frame = make_frame(DATES[:3], opens=[10.0, 12.0, 11.0], closes=[10.0, 12.0, 11.0])
    reversed_rows = frame.iloc[::-1]
    assert dataset_summary(frame)["digest"] == dataset_summary(reversed_rows)["digest"]


def test_date_column_matches_index():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    as_column = frame.reset_index().rename(columns={"index": "date"})
    assert dataset_summary(frame)["digest"] == dataset_summary(as_column)["digest"]


def test_int_and_float_representations_same_digest():
    int_frame = make_frame(DATES[:2], opens=[10, 10], closes=[10, 10])
    float_frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    assert dataset_summary(int_frame)["digest"] == dataset_summary(float_frame)["digest"]


def test_summary_input_is_not_mutated():
    frame = make_frame(DATES[:3], opens=[10.0, 12.0, 11.0], closes=[10.0, 12.0, 11.0])
    snapshot = frame.copy()
    dataset_summary(frame)
    pd.testing.assert_frame_equal(frame, snapshot)


def test_summary_fields_and_canonical_columns():
    summary = dataset_summary(
        make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0]), dataset_id="600000"
    )
    assert summary["n_rows"] == 2
    assert summary["columns"] == ["open", "high", "low", "close", "volume"]
    assert summary["digest"].startswith("sha256:")
    assert summary["dataset_id"] == "600000"
    assert summary["date_range"] == {"start": "2024-01-02", "end": "2024-01-03"}
    assert summary["canonicalization"]["missing_value"] == "NaN"


def test_extra_columns_appended_alphabetically():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame["turnover"] = [3.0, 4.0]
    frame["amount"] = [1.0, 2.0]
    summary = dataset_summary(frame)
    # 行情列固定顺序在前，其余列按字母序追加
    assert summary["columns"] == ["open", "high", "low", "close", "volume", "amount", "turnover"]


def test_duplicate_dates_rejected():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame.index = pd.DatetimeIndex([DATES[0], DATES[0]])
    with pytest.raises(ValueError, match="重复日期"):
        dataset_summary(frame)


def test_nan_missing_value_digest_is_deterministic():
    frame = make_frame(DATES[:2], opens=[10.0, float("nan")], closes=[10.0, 10.0])
    assert dataset_summary(frame)["digest"] == dataset_summary(frame.copy())["digest"]


# ===================================================================== #
# 策略元数据
# ===================================================================== #


def test_strategy_metadata_recorded():
    meta = capture_strategy_metadata(MaCrossStrategy(fast_window=5, slow_window=20))
    assert meta["name"] == "ma_cross"
    assert meta["version"] == "0.1.0"
    assert meta["params"] == {"fast_window": 5, "slow_window": 20}
    assert meta["identified"] is True
    assert meta["reproducible"] is True


def test_unserializable_strategy_params_marked():
    class WeirdStrategy(Strategy):
        name = "weird"
        version = "1.0.0"

        @property
        def params(self) -> dict:
            return {"handler": object()}  # 不可序列化

        def generate_signals(self, data: pd.DataFrame) -> pd.Series:
            return pd.Series(int(Signal.HOLD), index=data.index)

    meta = capture_strategy_metadata(WeirdStrategy())
    assert meta["params"] is None
    assert meta["params_available"] is False
    assert meta["identified"] is True
    assert meta["reproducible"] is False
    assert any("不可序列化" in note for note in meta["notes"])


def test_missing_strategy_identity_marked():
    class AnonStrategy(Strategy):
        name = ""
        version = ""

        @property
        def params(self) -> dict:
            return {}

        def generate_signals(self, data: pd.DataFrame) -> pd.Series:
            return pd.Series(int(Signal.HOLD), index=data.index)

    meta = capture_strategy_metadata(AnonStrategy())
    assert meta["identified"] is False
    assert meta["reproducible"] is False
    assert any("名称或版本" in note for note in meta["notes"])


# ===================================================================== #
# 可复现性判定与费率来源
# ===================================================================== #


def test_fee_source_preserved_in_config_summary():
    summary = config_summary(make_config())
    assert summary["fee_source"] == "simulation_placeholder"


def test_reproducibility_report_true_when_complete():
    dataset = dataset_summary(
        make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0]), dataset_id="600000"
    )
    strategy = capture_strategy_metadata(MaCrossStrategy(fast_window=2, slow_window=3))
    report = reproducibility_report(
        strategy_meta=strategy, dataset_meta=dataset, config_summary=config_summary(make_config())
    )
    assert report["inputs_identifiable"] is True
    assert report["process_reproducible"] is True
    assert report["missing"] == []


def test_reproducibility_report_flags_missing():
    report = reproducibility_report(strategy_meta=None, dataset_meta=None, config_summary=None)
    assert report["inputs_identifiable"] is False
    assert report["process_reproducible"] is False
    assert "数据集摘要" in report["missing"]
    assert "策略元数据" in report["missing"]


def test_reproducibility_report_flags_missing_params():
    dataset = dataset_summary(
        make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0]), dataset_id="600000"
    )
    strategy = capture_strategy_metadata(MaCrossStrategy(fast_window=2, slow_window=3))
    strategy = {**strategy, "params": None, "params_available": False, "reproducible": False}
    report = reproducibility_report(
        strategy_meta=strategy, dataset_meta=dataset, config_summary=config_summary(make_config())
    )
    assert report["inputs_identifiable"] is True
    assert report["process_reproducible"] is False
    assert "策略参数" in report["missing"]


def test_engine_records_strategy_and_dataset_metadata():
    result = run_engine(strategy=MaCrossStrategy(fast_window=2, slow_window=3))
    assert result.dataset["digest"].startswith("sha256:")
    assert result.dataset["dataset_id"] == "600000"
    assert result.strategy["name"] == "ma_cross"
    assert result.strategy["params"] == {"fast_window": 2, "slow_window": 3}
    assert result.strategy["reproducible"] is True
