"""L1a 未来函数检测：静态认写法、动态认行为，两条都不能是摆设。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deepquant.gates.leakage import check_leakage, scan_source, truncation_test
from deepquant.strategies.base import Signal, Strategy
from deepquant.strategies.ma_cross import MaCrossStrategy


def _frame(rows: int = 400) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=rows)
    prices = 10.0 * np.exp(0.0008 * np.arange(rows)) * (1 + 0.05 * np.sin(np.arange(rows) / 12.0))
    frame = pd.DataFrame({"date": dates, "close": prices})
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.002
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.998
    frame["volume"] = 10_000
    return frame


class _Center(Strategy):
    name = "t_center"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        ma = data["close"].rolling(10, center=True).mean()
        return pd.Series(np.where(data["close"] > ma, Signal.BUY, Signal.HOLD), index=data.index)


class _FullSample(Strategy):
    name = "t_fullsample"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        z = (data["close"] - data["close"].mean()) / data["close"].std()
        out = pd.Series(Signal.HOLD, index=data.index, dtype=int)
        out[z > 1] = Signal.BUY
        out[z < -1] = Signal.SELL
        return out


class _Nested(Strategy):
    name = "t_nested"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        base = data["close"].rolling(5).mean().shift(-1)
        return pd.Series(np.where(base > 0, Signal.BUY, Signal.HOLD), index=data.index)


def test_static_scan_detects_center_rolling():
    findings = scan_source("x = df['close'].rolling(20, center=True).mean()")
    assert [item.code for item in findings] == ["center_rolling"]


def test_static_scan_detects_negative_shift():
    findings = scan_source("y = df['close'].shift(-1)")
    assert [item.code for item in findings] == ["negative_shift"]


def test_static_scan_detects_backfill_forms():
    codes = [item.code for item in scan_source("z = df['close'].bfill()")]
    codes += [item.code for item in scan_source("z = df['close'].fillna(method='bfill')")]
    assert codes.count("backfill") == 2


def test_static_scan_clean_source_has_no_findings():
    findings = scan_source("m = df['close'].rolling(20).mean()\ns = df['close'].shift(1)")
    assert findings == []


def test_static_scan_unparsable_source_is_not_clean():
    findings = scan_source("def broken(:\n    pass")
    assert findings and findings[0].code == "unparsable_source"


def test_truncation_passes_for_prefix_stable_strategy():
    result = truncation_test(MaCrossStrategy(fast_window=5, slow_window=20), _frame())
    assert result.probes_tested > 0
    assert result.dynamic == [], "只用到历史的均线策略不应被判泄漏"


def test_truncation_detects_fullsample_statistics():
    result = truncation_test(_FullSample(), _frame())
    assert [item.code for item in result.dynamic] == ["prefix_mismatch"]


def test_check_leakage_finds_center_rolling_both_ways():
    result = check_leakage(_Center(), _frame())
    codes = {item.code for item in result.findings}
    assert "center_rolling" in codes, "静态应命中"
    assert "prefix_mismatch" in codes, "动态也应命中（居中窗口确实用到未来）"


def test_leakage_static_flags_negative_shift_strategy():
    result = check_leakage(_Nested(), _frame())
    assert any(item.code == "negative_shift" for item in result.static)
