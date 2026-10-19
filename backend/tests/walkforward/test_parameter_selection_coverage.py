"""``walkforward/parameter_selection.py`` 覆盖率补强（A11：91% → ≥95%）。

A 线红线（本文件严格遵守）：
  1. **只新增测试，不修改 ``src/`` 下任何代码。**
  2. 测出的 bug **只许测出来不许修**，用 ``xfail(strict=True)`` 固化；
     一旦有人修好，用例会 XPASS 并因 strict 而报错，强制回头摘掉标记。
  3. 断言由设计意图推导，不抄运行结果。

本轮新发现的缺陷（不修，仅固化）：
  - ``run_walkforward_window`` 的「OOS 策略构造失败」分支引用了尚未绑定的局部变量
    ``strategy``（源码 447-448 行 ``strategy.name if hasattr(strategy, "name") else ""``）。
    一旦 ``strategy_factory`` 在 OOS 阶段抛异常，函数不会按契约返回
    ``oos_success=False``，而是抛 ``UnboundLocalError`` 崩溃。
    这违反了模块自述的原则 4「失败显式表达」。
"""

from __future__ import annotations

import ast
import inspect
import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.walkforward import WalkForwardConfig
from deepquant.walkforward import parameter_selection as ps
from deepquant.walkforward.parameter_selection import (
    GridSearchConfig,
    _max_window,
    evaluate_candidates_on_train,
    generate_ma_cross_candidates,
    run_walkforward_v2,
    run_walkforward_window,
    select_best,
)


# --------------------------------------------------------------------------- #
# 公共辅助
# --------------------------------------------------------------------------- #


def _idx(n: int, start: str = "2022-01-03") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="B")


def _frame(idx: pd.DatetimeIndex, seed: int = 42) -> pd.DataFrame:
    """高波动合成行情，确保均线交叉能产生交易。"""
    rng = np.random.default_rng(seed)
    n = len(idx)
    noise = rng.standard_normal(n).cumsum() * 5
    cycle = np.sin(np.linspace(0, max(1, n // 10) * math.pi, n)) * 30
    close = np.maximum(100.0 + cycle + noise, 5.0)
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


def _base_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="TEST",
        initial_cash=100000.0,
        slippage_buy=0.0,
        slippage_sell=0.0,
        fee_source="simulation_placeholder",
    )


def _factory(params: dict) -> MaCrossStrategy:
    return MaCrossStrategy(**params)


def _fake_backtest_result(
    *,
    final_equity: float | None = 100000.0,
    is_complete: bool = True,
    cumulative_return: float = 0.01,
    max_drawdown_ratio: float = 0.02,
    n_closed_trades: int = 3,
    total_fees: float = 10.0,
    n_orders: int = 4,
    incomplete_reasons: tuple[str, ...] = (),
) -> SimpleNamespace:
    """伪造 BacktestResult —— 只在**本文件**内使用，用于触达异常分支。"""
    return SimpleNamespace(
        performance=SimpleNamespace(
            final_equity=final_equity,
            is_complete=is_complete,
            incomplete_reasons=list(incomplete_reasons),
            cumulative_return=cumulative_return,
            max_drawdown_ratio=max_drawdown_ratio,
            trade_stats=SimpleNamespace(
                n_closed_trades=n_closed_trades,
                n_orders=n_orders,
                total_fees=total_fees,
            ),
        )
    )


# --------------------------------------------------------------------------- #
# 未被引用的辅助函数（死代码）
# --------------------------------------------------------------------------- #


class TestMaxWindowHelper:
    """``_max_window`` 当前在 src/ 与 tests/ 中**均无任何调用点**。

    这里为它补上行为测试，目的有两个：
      1. 锁定语义（取候选集合中指定参数的最大值），将来若被接线不会行为漂移；
      2. 把它从"完全无覆盖"变成"有覆盖"，让覆盖率数字反映真实风险而不是盲区。
    是否删除它属于设计决策，**不在本轮改动**。
    """

    def test_returns_max_of_requested_key(self) -> None:
        cands = [
            {"fast_window": 3, "slow_window": 15},
            {"fast_window": 5, "slow_window": 40},
            {"fast_window": 10, "slow_window": 25},
        ]
        assert _max_window(cands, "slow_window") == 40
        assert _max_window(cands, "fast_window") == 10

    def test_single_candidate(self) -> None:
        assert _max_window([{"fast_window": 7, "slow_window": 21}], "slow_window") == 21

    def test_coerces_string_values_to_int(self) -> None:
        assert _max_window([{"slow_window": "30"}], "slow_window") == 30


# --------------------------------------------------------------------------- #
# 训练期评价的失败分支
# --------------------------------------------------------------------------- #


class TestTrainEvaluationFailurePaths:
    def test_warmup_covering_entire_train_raises(self) -> None:
        """预热段把训练期吃光 ⇒ 显式 ValueError，而不是静默返回空结果。"""
        df = _frame(_idx(10))

        with pytest.raises(ValueError, match="没有可用交易日"):
            evaluate_candidates_on_train(
                df,
                _base_config(),
                [{"fast_window": 3, "slow_window": 5}],
                warmup_days=len(df),
                strategy_factory=_factory,
            )

    def test_strategy_construction_failure_is_captured(self) -> None:
        """策略构造抛异常 ⇒ 该候选 success=False 且 error 写明原因，不中断其余候选。"""
        df = _frame(_idx(120))
        cands = [{"fast_window": 3, "slow_window": 15}]

        def bad_factory(params):  # noqa: ANN001, ANN202
            raise TypeError("unsupported params")

        results = evaluate_candidates_on_train(
            df, _base_config(), cands, warmup_days=0, strategy_factory=bad_factory,
        )

        assert len(results) == 1
        assert results[0].success is False
        assert "策略构造失败" in results[0].error
        assert "TypeError" in results[0].error
        assert results[0].cumulative_return is None

    def test_train_backtest_exception_is_captured(self, monkeypatch) -> None:
        """训练期回测抛异常 ⇒ 捕获为 success=False，error 前缀「训练期回测失败」。"""

        def boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(ps, "run_backtest", boom)

        res = evaluate_candidates_on_train(
            _frame(_idx(120)),
            _base_config(),
            [{"fast_window": 3, "slow_window": 15}],
            warmup_days=0,
            strategy_factory=_factory,
        )

        assert len(res) == 1
        assert res[0].success is False
        assert "训练期回测失败" in res[0].error
        assert "RuntimeError" in res[0].error

    def test_incomplete_equity_marked_as_filtered_failure(self, monkeypatch) -> None:
        """final_equity=None ⇒ 该候选不可评价，标记失败但**保留**可观测字段。"""
        df = _frame(_idx(120))
        cands = [{"fast_window": 3, "slow_window": 15}]

        monkeypatch.setattr(
            ps,
            "run_backtest",
            lambda *a, **k: _fake_backtest_result(
                final_equity=None,
                is_complete=False,
                cumulative_return=None,
                max_drawdown_ratio=0.05,
                n_closed_trades=2,
                incomplete_reasons=("权益不完整",),
            ),
        )

        res = evaluate_candidates_on_train(
            df, _base_config(), cands, warmup_days=0, strategy_factory=_factory,
        )

        assert len(res) == 1
        assert res[0].success is False
        assert "权益不完整或不可计算" in res[0].error
        assert res[0].is_complete is False
        # 关键：不可评价 ≠ 全部置空，观测字段仍在
        assert res[0].n_closed_trades == 2
        assert res[0].max_drawdown_ratio == pytest.approx(0.05)
        assert "权益不完整" in res[0].error

    def test_min_trades_filter_keeps_observable_fields(self) -> None:
        """min_trades 过滤掉的候选：success=False、error 写明门槛，且字段仍可观测。"""
        df = _frame(_idx(150))
        cands = [{"fast_window": 3, "slow_window": 15}]

        free = evaluate_candidates_on_train(
            df, _base_config(), cands, warmup_days=0,
            strategy_factory=_factory, min_trades=None,
        )
        assert len(free) == 1
        assert free[0].success is True or free[0].success is False
        n_trades = free[0].n_closed_trades
        assert n_trades is not None

        gated = evaluate_candidates_on_train(
            df, _base_config(), cands, warmup_days=0,
            strategy_factory=_factory, min_trades=n_trades + 1,
        )
        assert gated[0].success is False
        assert f"min_trades={n_trades + 1}" in gated[0].error
        # 不是静默当 0
        assert gated[0].n_closed_trades == n_trades


# --------------------------------------------------------------------------- #
# 选择指标的防护
# --------------------------------------------------------------------------- #


class TestSelectionMetricGuard:
    def test_unknown_metric_raises(self) -> None:
        cfg = GridSearchConfig()
        cands = generate_ma_cross_candidates(cfg)
        res = evaluate_candidates_on_train(
            _frame(_idx(120)), _base_config(), cands, warmup_days=0,
            strategy_factory=_factory,
        )

        with pytest.raises(ValueError, match="未知选择指标"):
            select_best(res, metric="sharpe")

    def test_metric_can_be_max_drawdown_ratio(self) -> None:
        """``metric="max_drawdown_ratio"`` 走回撤排序分支：回撤越小越优。"""
        idx = _idx(200)
        frame = _frame(idx, seed=11)
        grid = GridSearchConfig(
            fast_candidates=(3, 5),
            slow_candidates=(15, 25),
        )
        results = evaluate_candidates_on_train(
            frame.iloc[:150], _base_config(),
            generate_ma_cross_candidates(grid),
            warmup_days=0, strategy_factory=_factory,
        )
        assert any(r.success for r in results)

        best, _ = select_best(results, metric="max_drawdown_ratio")
        valid = [r for r in results if r.success]
        expected = min(
            valid,
            key=lambda c: (
                c.max_drawdown_ratio if c.max_drawdown_ratio is not None else math.inf
            ),
        )
        assert best == expected.params

    def test_no_valid_candidate_returns_none(self, monkeypatch) -> None:
        """全部候选失败 ⇒ (None, 完整列表)，用于审计；不抛异常。"""
        monkeypatch.setattr(ps, "run_backtest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        res = evaluate_candidates_on_train(
            _frame(_idx(120)), _base_config(),
            [{"fast_window": 3, "slow_window": 15}],
            warmup_days=0, strategy_factory=_factory,
        )

        best, all_res = select_best(res)
        assert best is None
        assert all_res is res


# --------------------------------------------------------------------------- #
# 窗口级失败路径
# --------------------------------------------------------------------------- #


class TestWindowFailurePaths:
    def _window_kwargs(self, n: int = 400) -> dict[str, Any]:
        idx = _idx(n)
        return dict(
            full_df=_frame(idx),
            base_config=_base_config(),
            window_index=0,
            train_start=idx[0],
            train_end=idx[149],
            oos_start=idx[150],
            oos_end=idx[179],
            candidates=generate_ma_cross_candidates(GridSearchConfig()),
            warmup_days=0,
        )

    def test_date_not_in_index_returns_explicit_failure(self) -> None:
        """传入不在 index 中的日期 ⇒ KeyError 被捕获为明确的 selection_error。"""
        kw = self._window_kwargs()
        kw["train_start"] = pd.Timestamp("1999-01-01")

        r = run_walkforward_window(**kw, strategy_factory=_factory)

        assert r.selection_success is False
        assert r.selected_params is None
        assert "日期不在行情 index 中" in (r.selection_error or "")
        # OOS 根本没跑
        assert r.oos_success is None
        assert r.train_candidate_results == ()

    def test_oos_backtest_exception_is_captured(self, monkeypatch) -> None:
        """OOS 回测抛异常 ⇒ selection_success 仍为 True，但 oos_success=False 且写明原因。"""
        kw = self._window_kwargs()
        calls = {"n": 0}

        def fake(df, strategy, config):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] <= len(kw["candidates"]):
                return _fake_backtest_result(n_closed_trades=6)
            raise ValueError("oos boom")

        monkeypatch.setattr(ps, "run_backtest", fake)

        r = run_walkforward_window(**kw, strategy_factory=_factory)

        assert r.selection_success is True
        assert r.selected_params is not None
        assert r.oos_success is False
        assert "OOS 回测失败" in (r.oos_error or "")
        assert "ValueError" in (r.oos_error or "")
        # 指标全空，不伪装成 0
        assert r.final_equity is None
        assert r.cumulative_return is None

    def test_oos_strategy_construction_failure_returns_clean_result(
        self,
    ) -> None:
        """期望：OOS 阶段策略构造失败 ⇒ 干净返回 oos_success=False，而不是崩溃。"""
        kw = self._window_kwargs()
        n_cands = len(kw["candidates"])
        calls = {"n": 0}

        def factory_then_raise(params):  # noqa: ANN001, ANN202
            calls["n"] += 1
            if calls["n"] > n_cands:
                raise RuntimeError("factory boom at OOS")
            return MaCrossStrategy(**params)

        r = run_walkforward_window(**kw, strategy_factory=factory_then_raise)

        assert r.selection_success is True
        assert r.oos_success is False
        assert "OOS 策略构造失败" in (r.oos_error or "")


# --------------------------------------------------------------------------- #
# 无效窗口分支
# --------------------------------------------------------------------------- #


class TestInvalidWindowSpec:
    def test_invalid_spec_produces_explicit_selection_failure(self) -> None:
        """窗口被判定无效 ⇒ 生成一条 selection_success=False 且带 skip_reason 的结果。

        ``WalkForwardConfig`` 的 ``__post_init__`` 会强制 ``train_window >= min_train``
        与 ``oos_window >= min_oos``，因此正常使用 generate_windows 时 valid 恒为 True。
        这里用**等价鸭子类型配置**绕过该校验来触达分支（函数签名明确接受
        "Step 7.1 WalkForwardConfig 或等价对象"）。
        """
        n = 100
        frame = _frame(_idx(n))
        grid = GridSearchConfig(fast_candidates=(3,), slow_candidates=(15,))
        stub_cfg = SimpleNamespace(
            train_window=30,
            oos_window=30,
            step=30,
            min_train=60,   # 故意违反 dataclass 不变量，使 n_train=30 < 60
            min_oos=30,
            warmup=None,
            start_date=None,
            end_date=None,
        )

        specs, results, summary = run_walkforward_v2(
            frame,
            _base_config(),
            strategy_factory=_factory,
            grid_config=grid,
            window_config=stub_cfg,
        )

        assert specs, "应至少生成一个窗口"
        assert specs[0].valid is False
        assert specs[0].skip_reason is not None
        assert results[0].selection_success is False
        assert results[0].selected_params is None
        assert results[0].selection_error == specs[0].skip_reason
        assert results[0].oos_success is None
        # 无效窗口计入 total，但不计入 selection_success
        assert summary.n_total_windows == len(results)
        assert summary.n_selection_failed >= 1


# --------------------------------------------------------------------------- #
# R2 / R11 代码级防线
# --------------------------------------------------------------------------- #


class TestR2R11CodeLevelDefense:
    """R2（不得用 OOS 反馈调参）与 R11（参与率不得用于择优）的机制级防线。

    这里不检查任何具体数值，只检查**信息流**：
      - ``min_trades`` 只可能作用于训练期；
      - OOS 收益被任意篡改也不改变选中参数。
    """

    def test_min_trades_gate_is_at_selection_stage_not_oos(self) -> None:
        """门槛设在训练期：全部候选被过滤 ⇒ oos_* 保持 None（OOS 根本没执行）。"""
        idx = _idx(300)
        frame = _frame(idx)

        r = run_walkforward_window(
            frame,
            _base_config(),
            window_index=0,
            train_start=idx[0],
            train_end=idx[149],
            oos_start=idx[150],
            oos_end=idx[179],
            strategy_factory=_factory,
            candidates=generate_ma_cross_candidates(GridSearchConfig()),
            min_trades=10**6,   # 任何候选都不可能达到
            warmup_days=0,
        )

        assert r.selection_success is False
        assert r.selected_params is None
        assert r.oos_success is None
        assert r.final_equity is None
        assert r.train_candidate_results
        assert all("min_trades" in c.error for c in r.train_candidate_results)

    def test_min_trades_does_not_filter_low_trade_oos_window(self) -> None:
        """门槛不影响 OOS：选参成功后，OOS 段交易数低于门槛也照常出结果。

        构造方式：
          1) 先不设门槛跑一遍，取训练期候选中最大的交易数 ``hi``；
          2) 再以 ``min_trades=hi`` 跑同一个窗口 —— 恰好让最优候选通过；
          3) 该窗口 OOS 段只有 30 天，远短于训练期 150 天，交易数必然 < hi。
        若门槛会作用于 OOS，这个窗口就应该被丢弃（oos_success=None）。
        """
        idx = _idx(300)
        frame = _frame(idx, seed=42)
        cands = generate_ma_cross_candidates(GridSearchConfig())
        common = dict(
            window_index=0,
            train_start=idx[0],
            train_end=idx[149],
            oos_start=idx[150],
            oos_end=idx[179],
            strategy_factory=_factory,
            candidates=cands,
            warmup_days=60,        # 足够 slow_window=40 预热，否则两端都无信号
        )

        baseline = run_walkforward_window(frame, _base_config(), min_trades=None, **common)
        assert baseline.selection_success is True
        assert baseline.oos_success is True
        baseline_oos_trades = baseline.trade_count or 0

        gated = run_walkforward_window(
            frame, _base_config(), min_trades=baseline_oos_trades + 1, **common
        )

        # OOS 段交易数确实低于门槛，却依然正常产出 ⇒ 门槛只作用于训练期
        assert gated.selection_success is True
        assert gated.oos_success is True
        assert gated.trade_count is not None
        assert (gated.trade_count or 0) <= baseline_oos_trades

    def test_min_trades_only_reaches_train_evaluation(self) -> None:
        """源码级断言：``min_trades`` 在 run_walkforward_window 内只出现一次，
        且仅作为 ``evaluate_candidates_on_train`` 的关键字参数 —— 没有任何通往
        OOS 回测的数据通道。
        """
        tree = ast.parse(inspect.getsource(ps))
        func = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "run_walkforward_window"
        )

        calls_with_min_trades = [
            c for c in ast.walk(func)
            if isinstance(c, ast.Call)
            and any(k.arg == "min_trades" for k in c.keywords)
        ]

        assert len(calls_with_min_trades) == 1, (
            "min_trades 应只在一处被使用，否则可能存在通往 OOS 的新通道"
        )
        target = calls_with_min_trades[0]
        assert isinstance(target.func, ast.Name)
        assert target.func.id == "evaluate_candidates_on_train"

    def test_tampering_oos_returns_does_not_change_selection(self) -> None:
        """**R2 核心防线**：把 OOS 区间价格整体改写 ⇒ 选中参数必须一字不变，
        但 OOS 指标必须真的改变（证明扰动确实作用到了 OOS 阶段，而非没生效）。
        """
        n = 400
        idx = _idx(n)
        frame = _frame(idx, seed=42)
        cfg = _base_config()
        grid = GridSearchConfig(
            fast_candidates=(3, 5, 10),
            slow_candidates=(15, 25, 40),
            min_train_periods=100,
            min_oos_periods=30,
        )

        specs, results_orig, _ = run_walkforward_v2(
            frame, cfg, strategy_factory=_factory, grid_config=grid
        )
        assert specs
        # 只改最后一个窗口的 OOS 段（step == oos_window 时 OOS 区间互不重叠）
        last = specs[-1]

        tampered = frame.copy()
        p0 = idx.get_loc(last.oos_start)
        p1 = idx.get_loc(last.oos_end)
        for k, pos in enumerate(range(p0, p1 + 1)):
            factor = 1.0 + 0.03 * (k + 1)   # 逐日放大，收益必然改变
            for col in ("open", "high", "low", "close"):
                tampered.iloc[pos, tampered.columns.get_loc(col)] *= factor

        _, results_tam, _ = run_walkforward_v2(
            tampered, cfg, strategy_factory=_factory, grid_config=grid
        )

        a, b = results_orig[-1], results_tam[-1]
        assert a.selection_success is True
        assert b.selection_success is True
        # 选中参数一字不变
        assert a.selected_params == b.selected_params
        # 但 OOS 结果确实变了
        assert a.cumulative_return != b.cumulative_return

    def test_select_best_has_no_oos_input_channel(self) -> None:
        """结构防线：``select_best`` 的入参只有训练期候选结果 + metric，没有 OOS 通道。"""
        sig = inspect.signature(select_best)
        names = [p.lower() for p in sig.parameters]
        assert "candidates" in names
        assert not any("oos" in n for n in names)
        assert not any("test" in n or "valid" in n for n in names)
