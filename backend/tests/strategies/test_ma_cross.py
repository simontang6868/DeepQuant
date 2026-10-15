"""双均线交叉策略测试。"""

import pandas as pd
import pytest

from deepquant.strategies.ma_cross import MaCrossStrategy


def _data(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": close})


def test_generates_buy_and_sell_events():
    strat = MaCrossStrategy(fast_window=3, slow_window=5)
    df = _data([10, 10, 10, 10, 10, 14, 14, 10, 10, 10])

    signals = strat.generate_signals(df)

    assert signals.dtype == "int8"
    assert set(signals.unique()) <= {-1, 0, 1}
    assert signals.iloc[5] == 1  # 快线上穿 -> BUY
    assert signals.iloc[8] == -1  # 快线下穿 -> SELL
    assert bool((signals.iloc[:5] == 0).all())  # 预热期无信号
    assert signals.iloc[6] == 0 and signals.iloc[7] == 0  # 事件型：不重复发信号


def test_no_lookahead():
    strat = MaCrossStrategy(fast_window=3, slow_window=5)
    df = _data([10, 10, 10, 10, 10, 14, 14, 10, 10, 10, 11, 9, 12, 13, 8, 7])

    full = strat.generate_signals(df)

    # 截掉未来数据后，之前已产生的信号必须完全一致
    for k in range(1, len(df) + 1):
        partial = strat.generate_signals(df.iloc[:k])
        pd.testing.assert_series_equal(full.iloc[:k], partial)


def test_signal_index_matches_data():
    strat = MaCrossStrategy(fast_window=2, slow_window=4)
    index = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    )
    df = pd.DataFrame({"close": [10, 10, 10, 12, 13]}, index=index)

    signals = strat.generate_signals(df)
    pd.testing.assert_index_equal(signals.index, df.index)


def test_describe_has_name_version_params():
    assert MaCrossStrategy(fast_window=5, slow_window=20).describe() == {
        "name": "ma_cross",
        "version": "0.1.0",
        "params": {"fast_window": 5, "slow_window": 20},
    }


def test_invalid_params_raise():
    with pytest.raises(ValueError):
        MaCrossStrategy(fast_window=0, slow_window=5)
    with pytest.raises(ValueError):
        MaCrossStrategy(fast_window=5, slow_window=5)


def test_missing_close_raises():
    with pytest.raises(ValueError):
        MaCrossStrategy().generate_signals(pd.DataFrame({"open": [1.0, 2.0]}))
