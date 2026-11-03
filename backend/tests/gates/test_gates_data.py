"""L1a 数据门禁：一票否决项必须真的会否决。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deepquant.gates.data_gate import check_frame, check_data
from deepquant.gates.spec import DataSource, Window

WINDOW = Window(start="2020-01-01", end="2021-12-31")


def _frame(rows: int = 600, *, start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=rows)
    prices = 10.0 * np.exp(0.0008 * np.arange(rows)) * (1 + 0.04 * np.sin(np.arange(rows) / 15.0))
    frame = pd.DataFrame({"date": dates, "close": prices})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.003
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.997
    frame["volume"] = 10_000
    return frame[["date", "open", "high", "low", "close", "volume"]]


def test_clean_frame_passes_and_cross_source_undetermined():
    verdict = check_frame(_frame(), window=WINDOW)
    assert verdict.ok
    cross = next(check for check in verdict.checks if check.code == "cross_source")
    assert cross.passed is None, "未提供交易层时跨源一致性必须是「未判定」，不能默认通过"
    assert "cross_source" in verdict.to_dict()["undetermined"]


def test_broken_ohlc_relation_blocks():
    frame = _frame()
    frame.loc[3, "high"] = frame.loc[3, "low"] * 0.5
    verdict = check_frame(frame, window=WINDOW)
    assert not verdict.ok
    assert "ohlc_relations" in [check.code for check in verdict.blocking_failures]


def test_short_history_blocks():
    verdict = check_frame(_frame(rows=100), window=WINDOW)
    assert not verdict.ok
    assert "in_sample_coverage" in [check.code for check in verdict.blocking_failures]


def test_duplicate_dates_block():
    frame = _frame()
    frame.loc[10, "date"] = frame.loc[9, "date"]
    verdict = check_frame(frame, window=WINDOW)
    assert "duplicate_dates" in [check.code for check in verdict.blocking_failures]


def test_missing_column_blocks():
    frame = _frame().drop(columns=["volume"])
    verdict = check_frame(frame, window=WINDOW)
    assert not verdict.ok


def test_cross_source_consistent_passes():
    research = _frame()
    trading = research.copy()
    # 交易层：同收益、不同价格水平（模拟不复权 vs 复权）
    trading["close"] = trading["close"] * 3.0
    trading["open"] = trading["open"] * 3.0
    trading["high"] = trading["high"] * 3.0
    trading["low"] = trading["low"] * 3.0
    verdict = check_frame(research, window=WINDOW, trading=trading)
    cross = next(check for check in verdict.checks if check.code == "cross_source")
    assert cross.passed is True, cross.detail


def test_cross_source_inconsistent_blocks():
    research = _frame()
    trading = research.copy()
    rng = np.random.default_rng(7)
    # 交易层收益被替换成独立噪声：不一致天数应远超 5%
    shock = pd.Series(rng.normal(0, 0.02, len(trading)))
    trading["close"] = trading["close"].iloc[0] * (1 + shock).cumprod().values
    verdict = check_frame(research, window=WINDOW, trading=trading)
    cross = next(check for check in verdict.checks if check.code == "cross_source")
    assert cross.passed is False, cross.detail


def test_missing_file_returns_verdict_not_exception():
    verdict = check_data(DataSource(symbol="X", research_path="no/such/file.csv"), WINDOW)
    assert not verdict.ok
    assert verdict.checks[0].code == "research_file"


def test_zero_volume_is_not_blocking():
    frame = _frame()
    frame.loc[5, "volume"] = 0
    verdict = check_frame(frame, window=WINDOW)
    check = next(item for item in verdict.checks if item.code == "volume_present")
    assert check.passed is True
    assert check.blocking is False
