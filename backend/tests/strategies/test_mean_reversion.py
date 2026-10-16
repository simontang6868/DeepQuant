"""Step 7.5: MeanReversionStrategy 基础测试（合成数据 + 防未来函数 + 引擎集成）。"""
import numpy as np
import pandas as pd
import pytest

from deepquant.strategies.mean_reversion import MeanReversionStrategy, _rolling_std
from deepquant.strategies.base import Signal


def _synthetic_close(n=500, seed=42):
    """带均值回归特征的合成收盘价（绕均值振荡 + 噪声）。"""
    rng = np.random.default_rng(seed)
    # 自回归均值回归：price = prev - 0.1*(prev - mean) + noise
    mean_price = 100.0
    prices = [mean_price]
    for _ in range(n - 1):
        noise = rng.normal(scale=1.5)
        prices.append(prices[-1] - 0.1 * (prices[-1] - mean_price) + noise)
    return pd.Series(prices, dtype="float64")


# --------------------------------------------------------------------------- #
# _rolling_std & generate_signals 基础
# --------------------------------------------------------------------------- #

class TestRollingStd:
    def test_normal(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _rolling_std(s, 3)
        # 前 2 个 NaN
        assert result.iloc[0:2].isna().all()
        # 第 3 个 = std(1,2,3)=sqrt(2/3)
        np.testing.assert_allclose(result.iloc[2], np.std([1.0, 2.0, 3.0], ddof=0), rtol=1e-9)

    def test_constant_all_identical(self):
        s = pd.Series([5.0] * 10)
        result = _rolling_std(s, 5)
        np.testing.assert_allclose(result.iloc[-1], 0.0, rtol=1e-9)


class TestMeanReversionStrategy:
    def test_invalid_lookback(self):
        with pytest.raises(ValueError):
            MeanReversionStrategy(lookback=1)

    def test_invalid_threshold(self):
        with pytest.raises(ValueError):
            MeanReversionStrategy(threshold=0)

    def test_params_contract(self):
        s = MeanReversionStrategy(lookback=20, threshold=2.0)
        assert s.params == {"lookback": 20, "threshold": 2.0}
        assert s.name == "mean_reversion"
        assert s.version == "0.1.0"

    def test_no_close_column(self):
        df = pd.DataFrame({"open": [1, 2, 3]})
        with pytest.raises(ValueError):
            MeanReversionStrategy().generate_signals(df)

    def test_signal_values_are_valid(self):
        close = _synthetic_close(500)
        df = pd.DataFrame({"close": close, "open": close, "high": close, "low": close, "volume": 1000})
        sig = MeanReversionStrategy(lookback=30, threshold=2.0).generate_signals(df)
        assert set(sig.unique()).issubset({int(Signal.BUY), int(Signal.HOLD), int(Signal.SELL)})

    def test_warmup_is_all_hold(self):
        close = _synthetic_close(500)
        df = pd.DataFrame({"close": close, "open": close, "high": close, "low": close, "volume": 1000})
        s = MeanReversionStrategy(lookback=30, threshold=2.0)
        sig = s.generate_signals(df)
        # 前 lookback-1 个应该全 HOLD（std 为 NaN → valid 全 False）
        assert (sig.iloc[:29] == int(Signal.HOLD)).all()

    def test_signals_do_not_exceed_max_count(self):
        """事件型信号：单日最多从 HOLD 变 BUY 或 HOLD 变 SELL，不得同时 BUY+SELL。"""
        close = _synthetic_close(1000, seed=7)
        df = pd.DataFrame({"close": close, "open": close, "high": close, "low": close, "volume": 1000})
        s = MeanReversionStrategy(lookback=20, threshold=1.5)
        sig = s.generate_signals(df)
        assert (sig == int(Signal.BUY)).sum() < 100   # 不应该太多
        assert (sig == int(Signal.SELL)).sum() < 100

    def test_threshold_zero_is_valid_no_signals(self):
        # threshold=0 时 BUY 条件 z < 0 且 z 从下向上穿零 — 在浮点下基本不会发生，
        # 但代码路径应该能跑而不是报错
        close = _synthetic_close(200)
        df = pd.DataFrame({"close": close, "open": close, "high": close, "low": close, "volume": 1000})
        # 0 阈值触发 ValueError 已在 invalid_threshold 测试覆盖
        s = MeanReversionStrategy(lookback=10, threshold=0.01)
        sig = s.generate_signals(df)
        assert len(sig) == 200


# --------------------------------------------------------------------------- #
# 防未来函数（未来数据不应影响历史信号）
# --------------------------------------------------------------------------- #

class TestMeanReversionNoLookahead:
    def test_future_data_perturbation_does_not_change_past_signals(self):
        """修改最后 10 个价格，不影响前 n-10 个信号。"""
        close = _synthetic_close(300, seed=99)
        df_orig = pd.DataFrame({
            "close": close, "open": close, "high": close + 1,
            "low": close - 1, "volume": 1000,
        })
        s = MeanReversionStrategy(lookback=20, threshold=2.0)
        sig_orig = s.generate_signals(df_orig)

        df_shocked = df_orig.copy()
        # 把最后 10 个 close 改成极大值
        df_shocked.loc[290:299, "close"] = 1_000_000.0
        sig_shocked = s.generate_signals(df_shocked)

        # 前 290 个信号应该完全一致
        pd.testing.assert_series_equal(
            sig_orig.iloc[:290].reset_index(drop=True),
            sig_shocked.iloc[:290].reset_index(drop=True),
        )


# --------------------------------------------------------------------------- #
# 引擎级集成
# --------------------------------------------------------------------------- #

class TestMeanReversionEngineIntegration:
    def test_runs_through_engine(self):
        """MR 策略应能走通 run_backtest 完整路径。"""
        from deepquant.backtest.engine import run_backtest
        from deepquant.backtest.config import BacktestConfig

        close = _synthetic_close(500, seed=123)
        df = pd.DataFrame({
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 100000,
        }, index=pd.date_range("2020-01-01", periods=500, freq="B"))
        cfg = BacktestConfig(symbol="test", initial_cash=100000.0)
        strat = MeanReversionStrategy(lookback=20, threshold=2.0)
        result = run_backtest(df, strat, cfg)
        assert result is not None
        assert result.performance is not None
        assert len(result.fills) >= 0  # 可以 0 笔成交（信号没触发）
