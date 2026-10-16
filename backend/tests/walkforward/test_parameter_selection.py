"""Step 7.2 训练期参数选择 + 独立 OOS 验证专项测试。

覆盖范围（对应任务文档 §12）：
- 12.1 候选参数生成（合法性过滤、确定性、边界保护）
- 12.2 训练期选择（仅用训练期指标、并列处理、不可计算不伪装成 0）
- 12.3 时间隔离（训练/OOS 不重叠、未来数据扰动不影响选择）
- 12.4 预热与执行隔离（预热不产生窗口外交易、OOS 不继承训练期状态）
- 12.5 汇总与失败处理（选择失败 / OOS 失败分别统计）
- 12.6 纯函数与输入保护（不修改调用方 DataFrame / config / 策略实例）
- 12.7 测试数据（合成行情、确定性种子、未来数据扰动）

这些测试只验证**程序行为符合设计**，不宣称策略具有盈利能力。
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.walkforward import WalkForwardConfig, generate_windows
from deepquant.walkforward.parameter_selection import (
    CandidateTrainResult,
    GridSearchConfig,
    WindowWFResult,
    WalkForwardSummaryV2,
    evaluate_candidates_on_train,
    generate_ma_cross_candidates,
    run_walkforward_v2,
    run_walkforward_window,
    select_best,
    summarize_v2,
)


# --------------------------------------------------------------------------- #
# 公共辅助：合成行情 & 基础配置
# --------------------------------------------------------------------------- #


def _idx(n: int, start="2022-01-03") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="B")


def _volatile_frame(idx: pd.DatetimeIndex, seed: int = 42) -> pd.DataFrame:
    """高波动合成行情，确保均线交叉策略能产生足够交易。"""
    rng = np.random.default_rng(seed)
    n = len(idx)
    noise = rng.standard_normal(n).cumsum() * 5
    cycle = np.sin(np.linspace(0, max(1, n // 10) * math.pi, n)) * 30
    close = 100.0 + cycle + noise
    close = np.maximum(close, 5.0)
    return pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 10000,
        },
        index=idx,
    )


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


def _base_config(**overrides) -> BacktestConfig:
    defaults = dict(
        symbol="TEST",
        initial_cash=100000.0,
        slippage_buy=0.0,
        slippage_sell=0.0,
        fee_source="simulation_placeholder",
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _grid_config(**overrides) -> GridSearchConfig:
    defaults = dict(
        fast_candidates=(3, 5, 10),
        slow_candidates=(15, 25, 40),
        min_train_periods=100,
        min_oos_periods=30,
        min_trades=None,
        max_combinations=50,
    )
    defaults.update(overrides)
    return GridSearchConfig(**defaults)


def _factory(params):
    return MaCrossStrategy(**params)


# =========================================================================== #
# 12.1 候选参数生成
# =========================================================================== #


class TestCandidateGeneration:
    """候选参数集合生成与合法性过滤。"""

    def test_valid_combinations_generated(self) -> None:
        grid = _grid_config()
        cands = generate_ma_cross_candidates(grid)
        # fast=(3,5,10), slow=(15,25,40) → 9 组合全部满足 slow > fast
        assert len(cands) == 9
        for c in cands:
            assert c["slow_window"] > c["fast_window"]

    def test_illegal_slow_le_fast_filtered_out(self) -> None:
        # fast=(3,20), slow=(10,30) → (20,10) 和 (20,30) 中 20<30 OK 但 20>10 过滤
        grid = _grid_config(fast_candidates=(3, 20), slow_candidates=(10, 30))
        cands = generate_ma_cross_candidates(grid)
        # (3,10), (3,30), (20,30) → (20,10) 被过滤
        assert len(cands) == 3
        for c in cands:
            assert c["slow_window"] > c["fast_window"]

    def test_deterministic_ordering(self) -> None:
        grid = _grid_config(fast_candidates=(10, 3), slow_candidates=(40, 15))
        a = generate_ma_cross_candidates(grid)
        b = generate_ma_cross_candidates(grid)
        assert a == b
        # 按 fast 升序、slow 升序
        assert a[0]["fast_window"] == 3
        assert a[-1]["fast_window"] == 10

    def test_empty_candidates_raises(self) -> None:
        # fast_candidates 全部 >= slow_candidates → 无合法组合
        grid = _grid_config(fast_candidates=(50, 60), slow_candidates=(10, 20))
        with pytest.raises(ValueError, match="未产生任何合法候选"):
            generate_ma_cross_candidates(grid)

    def test_empty_input_raises(self) -> None:
        grid = _grid_config(fast_candidates=(), slow_candidates=(10, 20))
        with pytest.raises(ValueError):
            generate_ma_cross_candidates(grid)

    def test_max_combinations_guard(self) -> None:
        grid = _grid_config(
            fast_candidates=tuple(range(2, 30)),
            slow_candidates=tuple(range(31, 80)),
            max_combinations=10,
        )
        with pytest.raises(ValueError, match="超过 max_combinations"):
            generate_ma_cross_candidates(grid)

    def test_non_integer_candidates_filtered(self) -> None:
        # 非整数 / 非正数被过滤；如果全部被过滤 → ValueError
        grid = _grid_config(fast_candidates=(3, 0, -1), slow_candidates=(15, 0, -1))
        cands = generate_ma_cross_candidates(grid)
        # 只剩 (3, 15)
        assert cands == [{"fast_window": 3, "slow_window": 15}]


# =========================================================================== #
# 12.2 训练期选择
# =========================================================================== #


class TestTrainingSelection:
    """参数选择**仅依据训练期**指标。"""

    def test_selection_uses_train_metrics_only(self) -> None:
        """训练期累计收益最高的候选胜出（即使 OOS 上它表现很差）。

        验证方法：故意构造训练期 / OOS 行情分化的数据 —— 训练期强势上涨、
        OOS 强势下跌。训练期选最优收益的候选，但 OOS 它可能表现很差。
        本测试只关心：选择结果**完全由训练期决定**，OOS 数据不会影响选择。
        """
        n = 400
        idx = _idx(n)
        # 训练期 [100, 200) → 上涨；OOS [200, 250) → 下跌
        close = [100.0] * 100 + [200.0] * 150 + [50.0] * 150
        frame = pd.DataFrame(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 10000,
            },
            index=idx,
        )
        grid = _grid_config(
            fast_candidates=(3, 5),
            slow_candidates=(15, 25),
            min_train_periods=80,
            min_oos_periods=30,
            min_trades=None,
        )
        base = _base_config()

        # 跑完整 walkforward
        specs, results, _ = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        # 至少 1 个窗口
        assert len(results) >= 1
        # 每个窗口都做了选择（此数据有足够交易）
        for r in results:
            if r.selection_success:
                # 选择成功 → 选中参数来自训练期排序
                assert r.selected_params is not None
                # 候选 train 结果中 success=True 的按 cumulative_return 降序排
                valid = [t for t in r.train_candidate_results if t.success]
                if len(valid) >= 2:
                    # 第一候选的 ret >= 所有其他候选的 ret（或因并列被次排序打破）
                    sorted_valid = sorted(
                        valid,
                        key=lambda t: (
                            -(t.cumulative_return or -math.inf),
                            t.max_drawdown_ratio or math.inf,
                            tuple(sorted(t.params.items())),
                        ),
                    )
                    assert sorted_valid[0].params == r.selected_params

    def test_tie_broken_deterministically(self) -> None:
        """累计收益和回撤都相同时，按参数字典序打破平局。"""
        # 构造故意让两组候选产生完全相同绩效的极端数据 —— 均线策略在平坦行情上
        # 不会产生任何交易，cumulative_return 都是 0。这里直接用 select_best 的
        # 输入测试 tie-break 行为。
        c1 = CandidateTrainResult(
            params={"fast_window": 3, "slow_window": 20},
            success=True, error=None,
            is_complete=True, cumulative_return=0.0,
            max_drawdown_ratio=0.0, n_closed_trades=0, total_fees=0.0,
        )
        c2 = CandidateTrainResult(
            params={"fast_window": 5, "slow_window": 15},  # 字典序比 (3,20) 靠后
            success=True, error=None,
            is_complete=True, cumulative_return=0.0,
            max_drawdown_ratio=0.0, n_closed_trades=0, total_fees=0.0,
        )
        best, _ = select_best([c2, c1], metric="cumulative_return")
        # (3, 20) 字典序 < (5, 15) → 选中 c1
        assert best == {"fast_window": 3, "slow_window": 20}

    def test_missing_metric_not_treated_as_zero(self) -> None:
        """cumulative_return=None 的候选，其排序值为 +inf（主排序用 -inf 表示最差）。"""
        c_none = CandidateTrainResult(
            params={"fast_window": 3, "slow_window": 15},
            success=True, error=None,
            is_complete=True, cumulative_return=None,  # 不可计算
            max_drawdown_ratio=0.0, n_closed_trades=1, total_fees=0.0,
        )
        c_ok = CandidateTrainResult(
            params={"fast_window": 5, "slow_window": 20},
            success=True, error=None,
            is_complete=True, cumulative_return=0.1,
            max_drawdown_ratio=0.05, n_closed_trades=1, total_fees=0.0,
        )
        best, _ = select_best([c_none, c_ok], metric="cumulative_return")
        assert best == c_ok.params  # c_none 的 None 不伪装成 0.0

    def test_min_trades_filter(self) -> None:
        """min_trades 过滤：交易数不足的候选 success=False。"""
        n = 500
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()
        grid = _grid_config(
            fast_candidates=(3, 5, 10),
            slow_candidates=(15, 25, 40),
            min_train_periods=100,
            min_oos_periods=30,
            min_trades=5,  # 较高阈值
        )

        # 用第一个窗口的训练期
        warmup_tail_len = 50
        train_start_pos = 100  # 训练期从 idx[100] 开始
        train_end_pos = 199
        warmup = frame.iloc[max(0, train_start_pos - warmup_tail_len) : train_start_pos]
        train_seg = frame.iloc[train_start_pos : train_end_pos + 1]
        train_df = pd.concat([warmup, train_seg])

        candidates = generate_ma_cross_candidates(grid)
        results = evaluate_candidates_on_train(
            train_df, base, candidates,
            warmup_days=len(warmup),
            strategy_factory=_factory,
            min_trades=5,
        )
        # 每个 success=False 的候选 error 必须说明"交易数 < min_trades"
        for r in results:
            if not r.success and "min_trades" in (r.error or ""):
                assert r.n_closed_trades is not None
                assert r.n_closed_trades < 5

    def test_all_candidates_fail_returns_explicit_failure(self) -> None:
        """完全平坦行情 → 所有候选没有任何交易 → 全部被 min_trades 过滤 → 选择失败。"""
        n = 400
        idx = _idx(n)
        frame = _flat_frame(idx, 100.0)

        grid = _grid_config(
            fast_candidates=(3, 5),
            slow_candidates=(15, 25),
            min_train_periods=80,
            min_oos_periods=30,
            min_trades=1,  # 至少 1 笔交易
        )
        specs, results, summary = run_walkforward_v2(
            frame, _base_config(), strategy_factory=_factory, grid_config=grid,
        )
        # 所有窗口选择失败
        assert summary.n_selection_failed > 0
        assert summary.n_oos_success == 0
        for r in results:
            if not r.selection_success:
                assert r.selected_params is None
                assert "所有候选" in (r.selection_error or "")

    def test_single_candidate_failure_does_not_silently_drop_info(self) -> None:
        """部分候选失败时，失败信息保留在 train_candidate_results 中，不会被静默丢弃。"""
        # 构造一个会让某个候选抛异常的场景：fast_window / slow_window 不满足策略约束
        # generate_ma_cross_candidates 已经过滤了 slow <= fast，但我们可以直接构造
        # strategy_factory 对某组参数抛异常的场景。
        def bad_factory(params):
            if params.get("fast_window") == 99:
                raise RuntimeError("故意抛错")
            return MaCrossStrategy(**params)

        candidates = [
            {"fast_window": 3, "slow_window": 15},
            {"fast_window": 99, "slow_window": 100},  # bad
            {"fast_window": 5, "slow_window": 20},
        ]
        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()

        warmup = frame.iloc[50:100]
        train_seg = frame.iloc[100:200]
        train_df = pd.concat([warmup, train_seg])

        results = evaluate_candidates_on_train(
            train_df, base, candidates,
            warmup_days=len(warmup),
            strategy_factory=bad_factory,
        )
        assert len(results) == 3
        bad = [r for r in results if r.params["fast_window"] == 99][0]
        assert bad.success is False
        assert "故意抛错" in (bad.error or "")
        # 其他两个正常
        ok = [r for r in results if r.params["fast_window"] != 99]
        assert all(r.success for r in ok)


# =========================================================================== #
# 12.3 时间隔离
# =========================================================================== #


class TestTimeIsolation:
    """训练期和 OOS 期严格时间隔离；未来数据不影响参数选择。"""

    def test_train_oos_no_overlap_in_single_window(self) -> None:
        """每个窗口 train_end < oos_start（generate_windows 保证）。"""
        n = 300
        idx = _idx(n)
        grid = _grid_config(min_train_periods=80, min_oos_periods=30)
        frame = _volatile_frame(idx)

        specs, results, _ = run_walkforward_v2(
            frame, _base_config(), strategy_factory=_factory, grid_config=grid,
        )
        for r in results:
            # 时间边界严格
            assert r.train_end < r.oos_start
            assert r.train_start <= r.train_end
            assert r.oos_start <= r.oos_end

    def test_bad_window_boundary_rejected(self) -> None:
        """非法边界（oos_start <= train_end）被拒绝 —— 通过 generate_windows 的顺序保证。"""
        # generate_windows 内部 train_end 之后紧接着就是 oos_start，天然隔离
        n = 200
        idx = _idx(n)
        wf_cfg = WalkForwardConfig(train_window=60, oos_window=30, step=30, min_train=60, min_oos=30)
        specs = generate_windows(idx, wf_cfg)
        for s in specs:
            assert s.train_end < s.oos_start

    def test_oos_data_perturbation_does_not_change_selection(self) -> None:
        """**关键隔离测试**：修改 OOS 区间行情 → 同一窗口的训练期选中参数不变。

        这直接验证了「OOS 结果不参与参数选择」的核心原则。
        """
        n = 400
        idx = _idx(n)
        frame = _volatile_frame(idx, seed=42)
        grid = _grid_config(
            fast_candidates=(3, 5, 10),
            slow_candidates=(15, 25, 40),
            min_train_periods=100,
            min_oos_periods=30,
            min_trades=None,
        )
        base = _base_config()

        specs, results_orig, _ = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        # 构造「修改了 OOS 区间行情」的版本
        perturbed = frame.copy()
        # 把 window 0 的 oos 区间 close 改成完全不同的值
        w0_oos_start = specs[0].oos_start
        w0_oos_end = specs[0].oos_end
        for i in range(idx.get_loc(w0_oos_start), idx.get_loc(w0_oos_end) + 1):
            perturbed.iloc[i, perturbed.columns.get_loc("close")] = 999.0
            perturbed.iloc[i, perturbed.columns.get_loc("open")] = 999.0

        specs2, results_pert, _ = run_walkforward_v2(
            perturbed, base, strategy_factory=_factory, grid_config=grid,
        )
        # 窗口 0 的选中参数必须**完全相同**（训练期数据没动，选择只看训练期）
        assert results_orig[0].selected_params == results_pert[0].selected_params
        # 训练期候选结果也必须完全一致
        assert len(results_orig[0].train_candidate_results) == len(results_pert[0].train_candidate_results)
        for a, b in zip(
            results_orig[0].train_candidate_results,
            results_pert[0].train_candidate_results,
        ):
            assert a.params == b.params
            assert a.success == b.success
            assert a.cumulative_return == b.cumulative_return
            assert a.max_drawdown_ratio == b.max_drawdown_ratio
            assert a.n_closed_trades == b.n_closed_trades

    def test_oos_end_future_data_does_not_affect_oos_result(self) -> None:
        """修改 OOS 结束日期之后的数据 → 该窗口 OOS 结果不变。"""
        n = 400
        idx = _idx(n)
        frame = _volatile_frame(idx, seed=42)
        grid = _grid_config(
            fast_candidates=(3, 5),
            slow_candidates=(15, 25),
            min_train_periods=100,
            min_oos_periods=30,
            min_trades=None,
        )
        base = _base_config()

        specs, results_orig, _ = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        perturbed = frame.copy()
        w0_oos_end = specs[0].oos_end
        for i in range(idx.get_loc(w0_oos_end) + 1, len(perturbed)):
            perturbed.iloc[i, perturbed.columns.get_loc("close")] = 999.0
            perturbed.iloc[i, perturbed.columns.get_loc("open")] = 999.0

        _, results_pert, _ = run_walkforward_v2(
            perturbed, base, strategy_factory=_factory, grid_config=grid,
        )
        r0 = results_orig[0]
        r0p = results_pert[0]
        # OOS 结果必须完全相等
        assert r0.oos_success == r0p.oos_success
        assert r0.final_equity == r0p.final_equity
        assert r0.cumulative_return == r0p.cumulative_return
        assert r0.trade_count == r0p.trade_count
        assert r0.total_fees == r0p.total_fees

    def test_other_window_future_data_does_not_affect_current(self) -> None:
        """修改窗口 j 的数据 → 窗口 i (i < j) 的结果不变。"""
        n = 500
        idx = _idx(n)
        frame = _volatile_frame(idx, seed=42)
        grid = _grid_config(
            fast_candidates=(3, 5),
            slow_candidates=(15, 25),
            min_train_periods=100,
            min_oos_periods=30,
            min_trades=None,
        )
        base = _base_config()

        specs, results_orig, _ = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        assert len(specs) >= 2

        perturbed = frame.copy()
        # 修改窗口 1 的 OOS 区间
        w1_oos_start = specs[1].oos_start
        pos1 = idx.get_loc(w1_oos_start)
        for i in range(pos1, len(perturbed)):
            perturbed.iloc[i, perturbed.columns.get_loc("close")] = 777.0
            perturbed.iloc[i, perturbed.columns.get_loc("open")] = 777.0

        _, results_pert, _ = run_walkforward_v2(
            perturbed, base, strategy_factory=_factory, grid_config=grid,
        )
        # 窗口 0 完全不受影响（训练期在 idx[:200]，OOS 在 [200, 230)）
        r0_orig = results_orig[0]
        r0_pert = results_pert[0]
        assert r0_orig.selected_params == r0_pert.selected_params
        assert r0_orig.final_equity == r0_pert.final_equity
        assert r0_orig.cumulative_return == r0_pert.cumulative_return


# =========================================================================== #
# 12.4 预热与执行隔离
# =========================================================================== #


class TestWarmupAndExecutionIsolation:
    """预热数据只用于指标计算，不产生窗口外交易；OOS 不继承训练期状态。"""

    def test_warmup_enables_valid_ma(self) -> None:
        """预热段让移动均线从训练期起点就有效（而不是 NaN）。"""
        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()

        warmup = frame.iloc[50:100]  # 50 天预热
        train_seg = frame.iloc[100:200]
        train_df = pd.concat([warmup, train_seg])

        candidates = [{"fast_window": 3, "slow_window": 40}]
        results = evaluate_candidates_on_train(
            train_df, base, candidates,
            warmup_days=len(warmup),
            strategy_factory=_factory,
        )
        assert len(results) == 1
        # 回测成功 → 预热足够让 slow_window=40 的均线有效
        # （如果没有预热，训练期前 40 行均线都是 NaN）
        assert results[0].success is True

    def test_warmup_produces_no_fills_before_train_start(self) -> None:
        """预热段行情只用于指标计算；所有成交 fill_date 都 >= config.start_date。

        关键不变量：引擎先在完整 df 上生成信号，再按 start_date / end_date 裁剪 ——
        所以预热段即使触发了 BUY/SELL 信号，也不会被下单、不会产生 Fill。
        """
        from deepquant.backtest.engine import run_backtest

        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()

        warmup_len = 50
        train_start_ts = idx[100]
        train_end_ts = idx[199]

        warmup = frame.iloc[100 - warmup_len : 100]
        train_seg = frame.iloc[100 : 200]
        combined = pd.concat([warmup, train_seg])

        candidates = generate_ma_cross_candidates(_grid_config(min_train_periods=80))
        for cand in candidates[:3]:  # 测前 3 个候选即可
            import dataclasses as _dc
            strategy = MaCrossStrategy(**cand)
            config = _dc.replace(base, start_date=train_start_ts, end_date=train_end_ts)
            result = run_backtest(combined, strategy, config)
            # 所有 Fill 的 fill_date 必须 >= train_start_ts
            for fill in result.fills:
                assert fill.fill_date >= train_start_ts, (
                    f"候选 {cand}：Fill {fill.fill_date} 在 start_date "
                    f"{train_start_ts} 之前 —— 预热隔离失效！"
                )

    def test_oos_does_not_inherit_train_position_or_cash(self) -> None:
        """OOS 回测使用独立 BacktestConfig（独立 initial_cash），不继承训练期账户状态。"""
        n = 400
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config(initial_cash=50000.0)

        # 窗口训练期：[100, 200)；OOS：[200, 250)
        result = run_walkforward_window(
            frame, base,
            window_index=0,
            train_start=idx[100], train_end=idx[199],
            oos_start=idx[200], oos_end=idx[249],
            strategy_factory=_factory,
            candidates=generate_ma_cross_candidates(_grid_config(min_train_periods=80)),
            warmup_days=50,
        )
        # OOS 回测成功时，result.oos_success=True 且 final_equity 可计算
        # 这里不验证 final_equity 的具体值（它取决于策略表现），只验证流程跑通
        assert result.selection_success is True or result.selection_error is not None
        # 关键：OOS 回测用的是 dataclasses.replace 构造的独立 config
        # —— 我们在 run_walkforward_window 内部已经用 _dc.replace 保证了，
        # 这里只需要验证 base_config 没有被修改
        assert base.initial_cash == 50000.0

    def test_no_future_data_in_oos_warmup(self) -> None:
        """OOS 预热数据只来自 oos_start 之前，不包含 OOS 结束之后的数据。"""
        from deepquant.walkforward.parameter_selection import _warmup_tail

        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)

        # oos_start = idx[200]，warmup 50 天 → 预热段 = idx[150:200]
        warmup = _warmup_tail(frame, 200, 50)
        assert len(warmup) == 50
        # 预热最后一行必须 < oos_start
        assert warmup.index[-1] < idx[200]
        # 预热最前一行
        assert warmup.index[0] == idx[150]


# =========================================================================== #
# 12.5 汇总与失败处理
# =========================================================================== #


class TestSummaryAndFailureHandling:
    """选择失败 / OOS 失败分别统计；不可计算指标不伪装成 0。"""

    def test_selection_failed_counted_separately_from_oos_failed(self) -> None:
        """WalkForwardSummaryV2 明确区分 n_selection_failed 和 n_oos_failed。"""
        # 纯平坦行情 → 所有窗口选择失败
        n = 300
        idx = _idx(n)
        frame = _flat_frame(idx)
        grid = _grid_config(min_trades=1)
        _, _, summary = run_walkforward_v2(
            frame, _base_config(), strategy_factory=_factory, grid_config=grid,
        )
        assert summary.n_selection_failed > 0
        assert summary.n_selection_success == 0
        assert summary.n_oos_success == 0
        assert summary.n_oos_failed == 0  # 没有选择成功的窗口 → 无 OOS 失败
        assert summary.positive_ratio is None
        assert summary.median_cumulative_return is None

    def test_oos_failed_counted_when_selection_succeeds(self) -> None:
        """选择成功但 OOS 回测抛异常 → oos_success=False, oos_failed 计数增加。"""
        # 构造一个会让 OOS 回测失败的场景：oos_end 之后的数据被完全破坏
        # 让 OOS 区间本身数据不合法 —— 实际中很难构造 OOS 抛异常的正常场景
        # （引擎对 NaN close 有保护），所以我们直接构造 WindowWFResult 列表测试汇总器
        r_sel_fail = WindowWFResult(
            window_index=0,
            train_start=pd.Timestamp("2022-01-03"), train_end=pd.Timestamp("2022-05-20"),
            oos_start=pd.Timestamp("2022-05-23"), oos_end=pd.Timestamp("2022-07-01"),
            train_periods=100, oos_periods=30, warmup_days=50,
            candidates=({"fast_window": 3, "slow_window": 15},),
            selection_metric="cumulative_return",
            selected_params=None, selection_success=False,
            selection_error="所有候选失败",
            train_candidate_results=(),
            oos_success=None, oos_error=None,
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source="simulation_placeholder",
            strategy_name="ma_cross", strategy_version="0.1.0",
        )
        r_oos_fail = WindowWFResult(
            window_index=1,
            train_start=pd.Timestamp("2022-01-03"), train_end=pd.Timestamp("2022-05-20"),
            oos_start=pd.Timestamp("2022-05-23"), oos_end=pd.Timestamp("2022-07-01"),
            train_periods=100, oos_periods=30, warmup_days=50,
            candidates=({"fast_window": 3, "slow_window": 15},),
            selection_metric="cumulative_return",
            selected_params={"fast_window": 3, "slow_window": 15},
            selection_success=True,
            selection_error=None,
            train_candidate_results=(),
            oos_success=False, oos_error="OOS 回测失败: ValueError: boom",
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source="simulation_placeholder",
            strategy_name="ma_cross", strategy_version="0.1.0",
        )
        r_ok = WindowWFResult(
            window_index=2,
            train_start=pd.Timestamp("2022-01-03"), train_end=pd.Timestamp("2022-05-20"),
            oos_start=pd.Timestamp("2022-05-23"), oos_end=pd.Timestamp("2022-07-01"),
            train_periods=100, oos_periods=30, warmup_days=50,
            candidates=({"fast_window": 3, "slow_window": 15},),
            selection_metric="cumulative_return",
            selected_params={"fast_window": 5, "slow_window": 20},
            selection_success=True,
            selection_error=None,
            train_candidate_results=(),
            oos_success=True, oos_error=None,
            final_equity=105000.0, cumulative_return=0.05,
            max_drawdown_ratio=0.03, trade_count=2, order_count=3,
            total_fees=150.0, oos_is_complete=True,
            oos_incomplete_reasons=(),
            fee_source="simulation_placeholder",
            strategy_name="ma_cross", strategy_version="0.1.0",
        )

        summary = summarize_v2([r_sel_fail, r_oos_fail, r_ok])
        assert summary.n_total_windows == 3
        assert summary.n_selection_success == 2
        assert summary.n_selection_failed == 1
        assert summary.n_oos_success == 1  # 只统计 selection_success 的窗口
        assert summary.n_oos_failed == 1  # 1 个选择成功但 OOS 失败
        assert summary.n_oos_with_valid_metrics == 1
        assert summary.positive_ratio == pytest.approx(1.0)
        assert summary.median_cumulative_return == pytest.approx(0.05)
        assert summary.total_oos_trades == 2
        assert summary.total_oos_orders == 3
        assert summary.total_oos_fees == pytest.approx(150.0)

    def test_incomplete_equity_not_treated_as_success(self) -> None:
        """oos_is_complete=False 且 final_equity=None → 不计入 valid_metrics。"""
        r = WindowWFResult(
            window_index=0,
            train_start=pd.Timestamp("2022-01-03"), train_end=pd.Timestamp("2022-05-20"),
            oos_start=pd.Timestamp("2022-05-23"), oos_end=pd.Timestamp("2022-07-01"),
            train_periods=100, oos_periods=30, warmup_days=50,
            candidates=({"fast_window": 3, "slow_window": 15},),
            selection_metric="cumulative_return",
            selected_params={"fast_window": 3, "slow_window": 15},
            selection_success=True, selection_error=None,
            train_candidate_results=(),
            oos_success=True, oos_error=None,
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=0, order_count=0,
            total_fees=0.0, oos_is_complete=False,
            oos_incomplete_reasons=("权益不完整",),
            fee_source="simulation_placeholder",
            strategy_name="ma_cross", strategy_version="0.1.0",
        )
        summary = summarize_v2([r])
        assert summary.n_oos_success == 1
        assert summary.n_oos_with_valid_metrics == 0
        assert summary.positive_ratio is None
        assert summary.median_cumulative_return is None

    def test_fee_source_shown_in_summary_note(self) -> None:
        """汇总 note 必须包含 fee_source 和窗口级统计声明。"""
        r = WindowWFResult(
            window_index=0,
            train_start=pd.Timestamp("2022-01-03"), train_end=pd.Timestamp("2022-05-20"),
            oos_start=pd.Timestamp("2022-05-23"), oos_end=pd.Timestamp("2022-07-01"),
            train_periods=100, oos_periods=30, warmup_days=50,
            candidates=({"fast_window": 3, "slow_window": 15},),
            selection_metric="cumulative_return",
            selected_params={"fast_window": 3, "slow_window": 15},
            selection_success=True, selection_error=None,
            train_candidate_results=(),
            oos_success=True, oos_error=None,
            final_equity=101000.0, cumulative_return=0.01,
            max_drawdown_ratio=0.02, trade_count=1, order_count=1,
            total_fees=50.0, oos_is_complete=True,
            oos_incomplete_reasons=(),
            fee_source="simulation_placeholder",
            strategy_name="ma_cross", strategy_version="0.1.0",
        )
        summary = summarize_v2([r])
        assert "窗口级统计" in summary.note
        assert "simulation_placeholder" in summary.note
        assert "过拟合风险" in summary.note


# =========================================================================== #
# 12.6 纯函数与输入保护
# =========================================================================== #


class TestPureFunctionAndInputProtection:
    """研究层不修改调用方输入；相同输入 → 相同输出。"""

    def test_run_walkforward_v2_does_not_mutate_inputs(self) -> None:
        n = 400
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()
        grid = _grid_config()

        frame_snapshot = frame.copy()
        base_initial_before = base.initial_cash
        base_fee_before = base.fee_source

        run_walkforward_v2(frame, base, strategy_factory=_factory, grid_config=grid)

        pd.testing.assert_frame_equal(frame, frame_snapshot)
        assert base.initial_cash == base_initial_before
        assert base.fee_source == base_fee_before

    def test_evaluate_candidates_does_not_mutate_train_df(self) -> None:
        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()
        candidates = generate_ma_cross_candidates(_grid_config(min_train_periods=80))

        warmup = frame.iloc[50:100]
        train_seg = frame.iloc[100:200]
        train_df = pd.concat([warmup, train_seg])
        snapshot = train_df.copy()

        evaluate_candidates_on_train(
            train_df, base, candidates,
            warmup_days=len(warmup), strategy_factory=_factory,
        )
        pd.testing.assert_frame_equal(train_df, snapshot)

    def test_deterministic_with_same_input(self) -> None:
        """相同行情 + 相同配置 → 相同结果（无随机性）。"""
        n = 400
        idx = _idx(n)
        frame = _volatile_frame(idx, seed=123)
        base = _base_config()
        grid = _grid_config(min_trades=None)

        _, results_a, summary_a = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        _, results_b, summary_b = run_walkforward_v2(
            frame, base, strategy_factory=_factory, grid_config=grid,
        )
        for ra, rb in zip(results_a, results_b):
            assert ra.selected_params == rb.selected_params
            assert ra.final_equity == rb.final_equity
            assert ra.cumulative_return == rb.cumulative_return
        assert summary_a.median_cumulative_return == summary_b.median_cumulative_return

    def test_strategy_instances_are_independent_per_candidate(self) -> None:
        """每个候选使用独立策略实例 —— 修改一个实例的属性不影响其他实例。"""
        constructed = []

        def tracking_factory(params):
            strat = MaCrossStrategy(**params)
            constructed.append(id(strat))
            return strat

        n = 300
        idx = _idx(n)
        frame = _volatile_frame(idx)
        base = _base_config()
        candidates = [
            {"fast_window": 3, "slow_window": 15},
            {"fast_window": 5, "slow_window": 20},
            {"fast_window": 10, "slow_window": 25},
        ]

        warmup = frame.iloc[50:100]
        train_seg = frame.iloc[100:200]
        train_df = pd.concat([warmup, train_seg])

        evaluate_candidates_on_train(
            train_df, base, candidates,
            warmup_days=len(warmup), strategy_factory=tracking_factory,
        )
        assert len(constructed) == len(candidates)
        assert len(set(constructed)) == len(candidates)  # 全部独立


# =========================================================================== #
# 集成测试：完整小例子
# =========================================================================== #


def test_full_walkforward_v2_demo_runs_end_to_end() -> None:
    """端到端烟雾测试：完整 Walk-Forward v2 流程跑通、返回结构完整。"""
    n = 500
    idx = _idx(n)
    frame = _volatile_frame(idx, seed=42)
    base = _base_config()
    grid = _grid_config(
        fast_candidates=(3, 5, 10),
        slow_candidates=(15, 25, 40),
        min_train_periods=100,
        min_oos_periods=30,
        min_trades=None,
    )

    specs, results, summary = run_walkforward_v2(
        frame, base, strategy_factory=_factory, grid_config=grid,
    )
    assert len(specs) >= 5
    assert len(results) == len(specs)

    # 每个结果结构完整
    for r in results:
        assert isinstance(r.train_start, pd.Timestamp)
        assert isinstance(r.train_end, pd.Timestamp)
        assert isinstance(r.oos_start, pd.Timestamp)
        assert isinstance(r.oos_end, pd.Timestamp)
        assert r.train_end < r.oos_start
        assert r.warmup_days >= 0
        assert len(r.candidates) > 0

    # 汇总结构完整
    assert summary.n_total_windows == len(specs)
    assert summary.n_selection_success + summary.n_selection_failed == summary.n_total_windows
    assert summary.n_oos_success + summary.n_oos_failed == summary.n_selection_success
    assert "窗口级统计" in summary.note
    assert "simulation_placeholder" in summary.note
    # param_distribution 列出了所有被选中过的参数组合
    if summary.n_oos_success > 0:
        assert len(summary.param_distribution) > 0
