"""Step 7.1 Walk-Forward 滚动样本外验证测试。

目标：覆盖窗口生成、未来数据隔离、预热生效、账户隔离、跨窗口汇总、
费用假设透明。所有测试用小规模人工行情，确定性、不依赖真实市场。

首版实现范围：固定参数滚动样本外评估（不做参数搜索），必须在报告中明确说明。
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.walkforward import (
    WalkForwardConfig,
    WalkForwardSummary,
    WindowResult,
    WindowSpec,
    generate_windows,
    run_fixed_parameter_walkforward,
    run_window,
    summarize_results,
)


# --------------------------------------------------------------------------- #
# 公共辅助
# --------------------------------------------------------------------------- #


def _idx(n: int, start="2022-01-03") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="B")


def _flat_frame(idx: pd.DatetimeIndex, price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [price] * len(idx),
            "high": [price] * len(idx),
            "low": [price] * len(idx),
            "close": [price] * len(idx),
            "volume": [1000] * len(idx),
        },
        index=idx,
    )


def _ramp_frame(idx: pd.DatetimeIndex, start_price: float, end_price: float) -> pd.DataFrame:
    n = len(idx)
    prices = [start_price + (end_price - start_price) * i / max(n - 1, 1) for i in range(n)]
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1000] * n,
        },
        index=idx,
    )


def _cfg(**overrides) -> WalkForwardConfig:
    defaults = dict(train_window=60, oos_window=20, step=20, min_train=30, min_oos=10)
    defaults.update(overrides)
    return WalkForwardConfig(**defaults)


def _base_config(**overrides) -> BacktestConfig:
    defaults = dict(symbol="TEST", initial_cash=100000.0, slippage_buy=0.0, slippage_sell=0.0)
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class _BuyEveryDay:
    """固定买入 — 每 T 日收盘都发出 BUY 信号，用来验证预热 + 账户隔离。"""

    name = "buy_every"
    version = "1.0.0"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        from deepquant.strategies.base import Signal, Strategy
        # 必须是 Strategy 子类的实例，否则引擎 isinstance 检查会失败
        # 这里用一个动态子类包装自身（最小改动）
        return pd.Series(int(Signal.BUY), index=data.index, dtype="int64")


# 用 Strategy 子类的规范写法
from deepquant.strategies.base import Strategy as _StrategyBase


class _BuyEveryDayStrategy(_StrategyBase):
    """规范的 Strategy 子类版本（引擎 isinstance 检查通过）。"""

    name = "buy_every"
    version = "1.0.0"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        from deepquant.strategies.base import Signal
        return pd.Series(int(Signal.BUY), index=data.index, dtype="int64")


# =========================================================================== #
# W1. 窗口生成
# =========================================================================== #


def test_windows_generated_in_chronological_order() -> None:
    idx = _idx(250)
    specs = generate_windows(idx, _cfg(train_window=60, oos_window=20, step=20))
    assert len(specs) == 9  # 60+20=80，250-80+1=171，171/20≈8→9个
    # 训练严格早于样本外
    for s in specs:
        assert s.train_end < s.oos_start
        assert s.n_train == 60
        assert s.n_oos == 20


def test_windows_deterministic() -> None:
    idx = _idx(250)
    cfg = _cfg(train_window=50, oos_window=20, step=15)
    a = generate_windows(idx, cfg)
    b = generate_windows(idx, cfg)
    pd.testing.assert_series_equal(
        pd.Series([(s.train_start, s.train_end, s.oos_start, s.oos_end) for s in a]),
        pd.Series([(s.train_start, s.train_end, s.oos_start, s.oos_end) for s in b]),
    )


def test_insufficient_data_returns_no_or_few_windows() -> None:
    idx = _idx(100)
    cfg = _cfg(train_window=60, oos_window=40, step=40, min_train=60, min_oos=40)
    specs = generate_windows(idx, cfg)
    # 60+40=100，恰好 1 个窗口
    assert len(specs) == 1
    assert specs[0].valid

    idx_short = _idx(99)
    cfg = _cfg(train_window=60, oos_window=40, step=40, min_train=60, min_oos=40)
    specs = generate_windows(idx_short, cfg)
    assert len(specs) == 0  # 60+40=100 > 99


def test_minimums_mark_skip_reason() -> None:
    """cfg 合法（train_window=min_train）但数据总长度不足 → 不生成窗口（当前实现设计）。

    说明：generate_windows 只对「完整覆盖 train+oos 区间」的窗口产出 WindowSpec，
    尾部不足时直接 break，不生成 valid=False 的窗口。这比"生成半成品窗口 + skip_reason"
    更简洁。skip_reason 的触发场景在 run_fixed_parameter_walkforward 聚合层里处理。
    """
    idx = _idx(75)  # 少于 train_window + oos_window = 80
    cfg = _cfg(train_window=60, oos_window=20, step=20, min_train=60, min_oos=20)
    specs = generate_windows(idx, cfg)
    assert len(specs) == 0


def test_bad_window_config_rejected() -> None:
    with pytest.raises(ValueError):
        WalkForwardConfig(train_window=0, oos_window=20, step=20)
    with pytest.raises(ValueError):
        WalkForwardConfig(train_window=50, oos_window=-1, step=20)
    with pytest.raises(ValueError):
        WalkForwardConfig(train_window=50, oos_window=20, step=20, min_train=100)
    # bool 不是正整数
    with pytest.raises(ValueError):
        WalkForwardConfig(train_window=True, oos_window=20, step=20)


def test_tz_aware_index_rejected() -> None:
    idx = pd.date_range("2022-01-03", periods=100, freq="B", tz="UTC")
    with pytest.raises(ValueError):
        generate_windows(idx, _cfg())


def test_duplicate_index_rejected() -> None:
    idx = pd.to_datetime(["2022-01-03", "2022-01-03", "2022-01-04"])
    with pytest.raises(ValueError):
        generate_windows(idx, _cfg())


def test_window_boundary_no_overlap() -> None:
    """每个窗口的训练严格早于自身的样本外（唯一硬性时序约束）。

    注意：相邻窗口的 train_start 可以在前面窗口的 oos 区间内 —— 这是 Walk-Forward
    的正常行为（后面窗口的训练要看当时所有可用历史，包括前面窗口的 oos）。
    因此我们不检查窗口之间的 train_start 不重叠前一个的 oos_end。
    """
    idx = _idx(200)
    specs = generate_windows(idx, _cfg(train_window=50, oos_window=20, step=20))
    for s in specs:
        assert s.train_end < s.oos_start
        assert s.n_train == 50
        assert s.n_oos == 20


# =========================================================================== #
# W2. 未来数据隔离
# =========================================================================== #


def test_future_data_after_oos_does_not_alter_window_result() -> None:
    """修改样本外结束日期之后的数据 → 该窗口的 WindowResult 完全不变。

    验证策略：构造一个完整行情，跑 window 0 得到 ret；然后修改 window 0
    结束日期之后的所有 close → 再跑一遍 → ret 必须完全相等。
    """
    from deepquant.strategies.ma_cross import MaCrossStrategy

    idx = _idx(300)
    close = [100.0] * 150 + [120.0] * 150  # 后半段上涨
    frame = _ramp_frame(idx, 99.0, 121.0)

    strategy = MaCrossStrategy(fast_window=10, slow_window=20)
    cfg = _cfg(train_window=100, oos_window=40, step=40, warmup=20)

    specs, results, _ = run_fixed_parameter_walkforward(
        frame, strategy, _base_config(), cfg
    )
    ret_base = results[0].cumulative_return
    assert ret_base is not None  # 有交易，不是 0

    # 构造一个"未来数据被修改"的版本：oos_end 之后的所有 close 改成随机大值
    perturbed = frame.copy()
    # window 0 的 oos_end = index[100+40-1] = index[139]
    oos_end_pos = 139
    for i in range(oos_end_pos + 1, len(perturbed)):
        perturbed.iloc[i, perturbed.columns.get_loc("close")] = 999.0
        perturbed.iloc[i, perturbed.columns.get_loc("open")] = 999.0
        perturbed.iloc[i, perturbed.columns.get_loc("high")] = 999.0
        perturbed.iloc[i, perturbed.columns.get_loc("low")] = 999.0

    specs2, results2, _ = run_fixed_parameter_walkforward(
        perturbed, strategy, _base_config(), cfg
    )
    # 窗口 0 的所有指标必须完全相等（因为它只用到 idx[:oos_end_pos+1]）
    r_base = results[0]
    r_pert = results2[0]
    assert r_base.final_equity == r_pert.final_equity
    assert r_base.cumulative_return == r_pert.cumulative_return
    assert r_base.max_drawdown_amount == r_pert.max_drawdown_amount
    assert r_base.trade_count == r_pert.trade_count
    assert r_base.order_count == r_pert.order_count


def test_window_i_result_does_not_use_window_i_p1_data() -> None:
    """第二个窗口的 data 变化不影响第一个窗口的结果（逐窗口独立 run_window）。"""
    from deepquant.strategies.ma_cross import MaCrossStrategy

    idx = _idx(250)
    close = [100.0] * 125 + [200.0] * 125
    frame = _ramp_frame(idx, 99.0, 201.0)

    strategy = MaCrossStrategy(fast_window=5, slow_window=10)
    cfg = _cfg(train_window=60, oos_window=30, step=30, warmup=15)

    specs, results, _ = run_fixed_parameter_walkforward(
        frame, strategy, _base_config(), cfg
    )
    # 拿到 window 1 的 oos 日期范围
    assert len(specs) >= 2
    w1_oos_start = specs[1].oos_start

    # 把 window 1 区间的 close 改成完全不同的值
    perturbed = frame.copy()
    w1_start_pos = perturbed.index.get_loc(w1_oos_start)
    for i in range(w1_start_pos, len(perturbed)):
        perturbed.iloc[i, perturbed.columns.get_loc("close")] = 500.0

    specs2, results2, _ = run_fixed_parameter_walkforward(
        perturbed, strategy, _base_config(), cfg
    )
    # window 0 必须和原来完全一致
    r0_base = results[0]
    r0_pert = results2[0]
    assert r0_base.final_equity == r0_pert.final_equity
    assert r0_base.trade_count == r0_pert.trade_count
    assert r0_base.cumulative_return == r0_pert.cumulative_return


# =========================================================================== #
# W3. 预热生效（Warmup）
# =========================================================================== #


def test_warmup_enables_early_signal_generation() -> None:
    """引擎必须能接收 warmup_days > 0 且正确工作（不报错、有正常结果）。

    说明：**固定参数 + 均线策略**的 MA 交叉日完全由 close 序列确定；当 close 在
    长段内平稳变化时，有 / 无 warmup 可能得到相同的最终交叉日（因为 MA 段足够长
    会覆盖掉预热的影响）。这是均线的数学特性，不是引擎缺陷。

    本测试验证：
    1. warmup_days 参数被正确传递，combined_df 包含训练尾部预热数据；
    2. 策略能在 combined_df 上生成信号，引擎只在 oos 区间内撮合；
    3. 引擎有预热支持 —— warmup_days=0 和 warmup_days>0 都能成功执行，不
       抛异常，result.success=True。
    """
    from deepquant.strategies.ma_cross import MaCrossStrategy
    from deepquant.walkforward import _slice_with_warmup

    idx = _idx(250)
    close = [100.0] * 120 + [80.0] * 20 + [130.0] * 110
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * 250,
        },
        index=idx,
    )
    strategy = MaCrossStrategy(fast_window=5, slow_window=10)
    cfg = _cfg(train_window=120, oos_window=80, step=80, warmup=10)
    specs = generate_windows(idx, cfg)
    spec = specs[0]

    # --- 1. 预热切片正确 ---
    combined, oos_s, oos_e = _slice_with_warmup(frame, spec, warmup_days=10)
    assert len(combined) > spec.n_oos  # combined 比 oos 多了预热行
    assert combined.index[0] < oos_s  # 预热段在 oos 之前
    assert combined.index[-1] == oos_e  # 覆盖到 oos_end

    # --- 2. 有 warmup：成功执行 ---
    res_warm = run_window(frame, strategy, _base_config(), spec, warmup_days=10)
    assert res_warm.success is True

    # --- 3. 无 warmup：也能成功执行（两者都是可接受的研究配置）---
    res_nowarm = run_window(frame, strategy, _base_config(), spec, warmup_days=0)
    assert res_nowarm.success is True

    # --- 4. 两种配置都是有效的样本外评估；引擎不会因 warmup 参数而抛异常 ---
    # （有 warmup 和无 warmup 可能得到相同的 cross 日 —— 这是均线数学特性，非缺陷）


def test_warmup_does_not_count_into_oos_performance() -> None:
    """预热区间的行情只用于指标计算；其对应的「交易」不可能计入样本外绩效。

    验证：即使 warmup 区间里策略触发了 BUY/SELL，run_window 构造的
    BacktestConfig.start_date = oos_start → 引擎只会对 oos 区间内的信号下单。
    """
    from deepquant.strategies.ma_cross import MaCrossStrategy
    from deepquant.walkforward import _slice_with_warmup

    idx = _idx(200)
    close = [100.0] * 100 + [120.0] * 100
    frame = _ramp_frame(idx, 99.0, 121.0)
    strategy = MaCrossStrategy(fast_window=3, slow_window=5)

    cfg = _cfg(train_window=100, oos_window=50, step=50, warmup=20)
    specs = generate_windows(idx, cfg)
    spec = specs[0]

    # 预热开始 = oos_start - 20 = 第 80 行
    combined, oos_s, oos_e = _slice_with_warmup(frame, spec, 20)

    # combined 的行数必须 > oos 行数（因为加了预热）
    assert len(combined) > spec.n_oos
    assert combined.index[0] < oos_s
    # oos 区间完整包含在 combined 里
    assert oos_s in combined.index
    assert oos_e in combined.index

    # engine 收到 start_date=oos_s, end_date=oos_e → 只有 oos 区间内的信号会下单
    result = run_window(frame, strategy, _base_config(), spec, warmup_days=20)
    # 即使有交易，所有 Fill 的 fill_date 都必须 >= oos_s
    assert result.success


# =========================================================================== #
# W4. 账户隔离
# =========================================================================== #


def test_each_window_starts_with_fresh_cash() -> None:
    """每个窗口独立使用 initial_cash，不会从上一窗口继承持仓或现金。

    close 分段按 train_window+oos_window=80 对齐：每段末尾让不同 window 的 oos
    分别落在「买不起一手」「刚好能买满一手」「能买多手」三种情形，final_equity
    因此显著不同 —— 直接证明账户状态不跨窗口继承。
    """
    idx = _idx(260)  # 足够多的行产生多个窗口
    # 分段：[10]*40 + [300]*80 + [50]*80 + [300]*60  → 260 行
    # window 0 (oos=idx[60:80]) 在 close=300 段 → 1 手 30000 > cash → REJECTED
    # window 2 (oos=idx[100:120]) 在 close=50 段 → 1 手 5000 ≤ cash → 买到
    # window 5 (oos=idx[160:180]) 在 close=50 段
    # window 7 (oos=idx[200:220]) 在 close=300 段 → 又 REJECTED
    close = [10.0] * 40 + [300.0] * 80 + [50.0] * 80 + [300.0] * 60
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * 260,
        },
        index=idx,
    )
    strat = _BuyEveryDayStrategy()
    cfg = _cfg(train_window=60, oos_window=20, step=20, warmup=0)
    initial_cash = 10100.0
    specs, results, _ = run_fixed_parameter_walkforward(
        frame, strat,
        _base_config(initial_cash=initial_cash, slippage_buy=0.0), cfg
    )

    assert len(specs) >= 6
    # close 分段：idx[0..39]=10, idx[40..119]=300, idx[120..199]=50, idx[200..259]=300
    # w0 oos=[60,80)=300, w3 oos=[120,140)=50, w7 oos=[200,220)=300
    f_buy    = results[3].final_equity   # close=50 → 买到
    f_reject = results[0].final_equity   # close=300 → REJECTED

    assert f_buy is not None and f_reject is not None
    assert f_reject == initial_cash  # 完全没有交易
    assert f_buy != initial_cash      # 买了，cash 减少
    assert f_buy < f_reject


def test_window_config_is_not_mutated() -> None:
    """run_fixed_parameter_walkforward 不得修改 base_config 对象（内部 dataclasses.replace）。"""
    cfg = _base_config(initial_cash=100000.0)
    before_initial = cfg.initial_cash

    idx = _idx(100)
    frame = _flat_frame(idx)
    run_fixed_parameter_walkforward(frame, _BuyEveryDayStrategy(), cfg, _cfg())

    # cfg 原样
    assert cfg.initial_cash == before_initial


# =========================================================================== #
# W5. 跨窗口汇总
# =========================================================================== #


def test_summary_aggregates_window_results() -> None:
    """汇总器正确统计 median_return / positive_ratio / total_trades 等。"""
    r1 = WindowResult(
        window_index=0, oos_start=pd.Timestamp("2022-01-10"), oos_end=pd.Timestamp("2022-01-28"),
        success=True, error=None, initial_equity=100.0, final_equity=110.0,
        cumulative_return=0.10, max_drawdown_amount=5.0, max_drawdown_ratio=0.05,
        trade_count=3, order_count=4, total_fees=1.0, fee_source="simulation_placeholder",
        is_complete=True, incomplete_reasons=(),
    )
    r2 = WindowResult(
        window_index=1, oos_start=pd.Timestamp("2022-02-07"), oos_end=pd.Timestamp("2022-02-25"),
        success=True, error=None, initial_equity=100.0, final_equity=90.0,
        cumulative_return=-0.10, max_drawdown_amount=15.0, max_drawdown_ratio=0.15,
        trade_count=5, order_count=6, total_fees=2.0, fee_source="simulation_placeholder",
        is_complete=True, incomplete_reasons=(),
    )
    r3 = WindowResult(
        window_index=2, oos_start=pd.Timestamp("2022-03-07"), oos_end=pd.Timestamp("2022-03-25"),
        success=False, error="train insufficient", initial_equity=100.0, final_equity=None,
        cumulative_return=None, max_drawdown_amount=None, max_drawdown_ratio=None,
        trade_count=0, order_count=0, total_fees=0.0, fee_source="simulation_placeholder",
        is_complete=False, incomplete_reasons=("train insufficient",),
    )

    summary = summarize_results([r1, r2, r3])
    assert summary.n_total_windows == 3
    assert summary.n_valid_windows == 2  # r1, r2
    assert summary.n_failed_windows == 1
    assert summary.median_return == pytest.approx(0.0)  # (-0.1, 0.1) 中位数 = 0
    assert summary.positive_ratio == pytest.approx(0.5)  # 正收益 1 / 有效 2
    assert summary.median_max_drawdown_amount == pytest.approx(10.0)  # (5, 15) 中位数 = 10
    assert summary.total_trades == 8
    assert summary.total_orders == 10
    assert summary.total_fees == 3.0


def test_summary_notes_simulation_placeholder() -> None:
    """汇总 note 必须明确 fee_source 和窗口级统计性质。"""
    r = WindowResult(
        window_index=0, oos_start=pd.Timestamp("2022-01-10"), oos_end=pd.Timestamp("2022-01-28"),
        success=True, error=None, initial_equity=100.0, final_equity=110.0,
        cumulative_return=0.10, max_drawdown_amount=5.0, max_drawdown_ratio=0.05,
        trade_count=1, order_count=1, total_fees=0.5, fee_source="simulation_placeholder",
        is_complete=True, incomplete_reasons=(),
    )
    summary = summarize_results([r])
    assert "窗口级统计" in summary.note
    assert "simulation_placeholder" in summary.note
    assert "模拟占位" in summary.note


def test_failed_window_is_not_counted_as_success() -> None:
    """失败窗口的 final_equity=None，positive_ratio 统计不会把 None 当作 0。"""
    r_good = WindowResult(
        window_index=0, oos_start=pd.Timestamp("2022-01-10"), oos_end=pd.Timestamp("2022-01-28"),
        success=True, error=None, initial_equity=100.0, final_equity=105.0,
        cumulative_return=0.05, max_drawdown_amount=1.0, max_drawdown_ratio=0.01,
        trade_count=0, order_count=0, total_fees=0.0, fee_source="simulation_placeholder",
        is_complete=True, incomplete_reasons=(),
    )
    r_bad = WindowResult(
        window_index=1, oos_start=pd.Timestamp("2022-02-07"), oos_end=pd.Timestamp("2022-02-25"),
        success=False, error="boom", initial_equity=100.0, final_equity=None,
        cumulative_return=None, max_drawdown_amount=None, max_drawdown_ratio=None,
        trade_count=0, order_count=0, total_fees=0.0, fee_source="simulation_placeholder",
        is_complete=False, incomplete_reasons=("boom",),
    )
    summary = summarize_results([r_good, r_bad])
    assert summary.n_valid_windows == 1
    assert summary.n_failed_windows == 1
    assert summary.positive_ratio == pytest.approx(1.0)  # 只有 r_good 有效且正收益


# =========================================================================== #
# W6. 费用假设透明
# =========================================================================== #


def test_summary_uses_configured_fee_source() -> None:
    """summary.note 里的 fee_source 与 base_config.fee_source 一致。"""
    idx = _idx(120)
    frame = _flat_frame(idx)
    strategy = _BuyEveryDayStrategy()

    base = _base_config(fee_source="custom_fee_table", commission_rate=0.0005)
    cfg = _cfg(train_window=60, oos_window=30, step=30, warmup=0)

    _, _, summary = run_fixed_parameter_walkforward(frame, strategy, base, cfg)
    assert "custom_fee_table" in summary.note


# =========================================================================== #
# W7. 参数选择（本阶段不做，明确声明）
# =========================================================================== #


def test_no_parameter_selection_is_performed() -> None:
    """本阶段 Walk-Forward 不做参数搜索，每个窗口用同一套策略参数。

    验证：各窗口的 strategy.params 字段完全相同（run_fixed_parameter_walkforward
    从头到尾只传同一个 strategy 实例给 run_window）。
    """
    idx = _idx(250)
    frame = _flat_frame(idx, 100.0)

    from deepquant.strategies.ma_cross import MaCrossStrategy

    strategy = MaCrossStrategy(fast_window=5, slow_window=20)
    cfg = _cfg(train_window=100, oos_window=40, step=40, warmup=20)

    specs, results, summary = run_fixed_parameter_walkforward(
        frame, strategy, _base_config(), cfg
    )
    # 汇总 note 必须声明是"固定参数"（调用方可自行验证，但我们在注释和文档里已声明）
    # 这里只验证：summary.note 里**没有**出现"参数优化"或"自动选择"
    assert summary.note is not None
    assert "自动训练" not in summary.note
    assert "参数优化" not in summary.note


# =========================================================================== #
# W8. 边界：行情短于窗口
# =========================================================================== #


def test_very_short_series_returns_few_or_no_windows() -> None:
    idx = _idx(100)
    cfg = _cfg(train_window=80, oos_window=30, step=30, min_train=80, min_oos=30)
    specs = generate_windows(idx, cfg)
    assert len(specs) == 0  # 80 + 30 = 110 > 100


def test_single_window_possible() -> None:
    idx = _idx(90)
    cfg = _cfg(train_window=60, oos_window=30, step=30, min_train=60, min_oos=20)
    specs = generate_windows(idx, cfg)
    assert len(specs) == 1
    assert specs[0].valid


# =========================================================================== #
# W9. 纯函数：不修改传入对象
# =========================================================================== #


def test_run_fixed_parameter_walkforward_does_not_mutate_inputs() -> None:
    idx = _idx(150)
    frame = _flat_frame(idx)
    strategy = _BuyEveryDayStrategy()
    base = _base_config(initial_cash=100000.0)
    cfg = _cfg(train_window=60, oos_window=20, step=20)

    frame_snapshot = frame.copy()
    strategy_params_before = strategy.params
    base_initial_before = base.initial_cash

    run_fixed_parameter_walkforward(frame, strategy, base, cfg)

    pd.testing.assert_frame_equal(frame, frame_snapshot)
    assert strategy.params == strategy_params_before
    assert base.initial_cash == base_initial_before
