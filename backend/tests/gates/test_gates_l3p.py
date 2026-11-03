"""L3p 组合级 Walk-Forward 测试。

重点不在"能跑通"，而在四条**容易被做假**的口径：

1. **共享现金池 ≠ 各标的收益平均**。用两只涨跌相反的合成标的 + 不等权，验证组合收益
   恰好是加权和（而等权平均会给出完全不同甚至符号相反的答案）。
2. **事件信号 → 状态重建**必须等于「持有到反向信号」。若直接拿事件序列当状态，
   月末调仓时几乎永远选不到票，组合会静默变成全现金。
3. **weights 模式收益与初始资金无关**（不受整手约束）。这条一旦不成立，
   说明资金在某个环节被当成了约束，收益就不再是"权重记账"的结果。
4. **判据同源**：L3p 不许自己写一套判定链。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import PortfolioBacktestConfig, run_portfolio_backtest
from deepquant.gates import l3 as l3_module
from deepquant.gates import l3_portfolio as l3p
from deepquant.gates.l3 import WalkForwardSpec, decide_from_summary
from deepquant.gates.l3_portfolio import (
    ALL_VERDICTS,
    PortfolioFold,
    PortfolioGateError,
    PortfolioSpec,
    Universe,
    control_schedule,
    load_universe,
    month_end_dates,
    parse_portfolio_spec,
    render_l3p_markdown,
    run_l3_portfolio,
    schedule_from_states,
    state_at,
    state_from_events,
    to_portfolio_verdict,
)
from deepquant.gates.spec import parse_spec
from deepquant.strategies.base import Signal

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _frame_from_closes(closes, *, open_price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": open_price,
            "high": np.maximum(close, open_price) * 1.01,
            "low": np.minimum(close, open_price) * 0.99,
            "close": close,
            "volume": 1e6,
        },
        index=idx,
    )


def _free_config(cash: float = 1_000_000.0) -> BacktestConfig:
    """零费率：本组测试校验的是记账结构，不是成本。"""
    return BacktestConfig(
        symbol="TEST",
        initial_cash=cash,
        commission_rate=0.0,
        min_commission=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_buy=0.0,
        slippage_sell=0.0,
    )


def _portfolio_config(symbols, cash: float = 1_000_000.0) -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=tuple(symbols),
        initial_cash=cash,
        fee_config=_free_config(cash),
        allocation="weights",
    )


# --------------------------------------------------------------------------- #
# 一、共享现金池的口径
# --------------------------------------------------------------------------- #
def test_portfolio_return_equals_weighted_sum_not_equal_average() -> None:
    """A 涨 20%、B 跌 20%，权重 0.8/0.2 ⇒ 组合 +12%；等权平均会是 0%。"""
    frames = {
        "A": _frame_from_closes([10.0] + [12.0] * 9),
        "B": _frame_from_closes([10.0] + [8.0] * 9),
    }
    signal_day = frames["A"].index[0]
    from deepquant.backtest.portfolio import TargetPortfolio

    schedule = (TargetPortfolio(signal_date=signal_day, weights={"A": 0.8, "B": 0.2}),)
    result = run_portfolio_backtest(
        frames, schedule, _portfolio_config(["A", "B"]), start_date=frames["A"].index[0]
    )
    assert result.cumulative_return == pytest.approx(0.12, abs=1e-9)
    # 若错写成"各标的收益平均"，会得到 0.0 —— 差 12pp，符号都不同（对 B 而言）
    naive_average = (0.2 + -0.2) / 2
    assert naive_average == pytest.approx(0.0)
    assert result.cumulative_return > naive_average + 0.1


def test_returns_are_invariant_to_initial_cash_in_weights_mode() -> None:
    """weights 模式不满足整手 ⇒ 收益与资金规模无关。"""
    frames = {
        "A": _frame_from_closes([10.0] + [12.0] * 9),
        "B": _frame_from_closes([10.0] + [8.0] * 9),
    }
    from deepquant.backtest.portfolio import TargetPortfolio

    schedule = (TargetPortfolio(signal_date=frames["A"].index[0], weights={"A": 0.6, "B": 0.4}),)
    small = run_portfolio_backtest(frames, schedule, _portfolio_config(["A", "B"], 1e5))
    large = run_portfolio_backtest(frames, schedule, _portfolio_config(["A", "B"], 1e8))
    assert small.cumulative_return == pytest.approx(large.cumulative_return, abs=1e-12)


# --------------------------------------------------------------------------- #
# 二、事件 → 状态
# --------------------------------------------------------------------------- #
def test_state_from_events_holds_until_opposite_signal() -> None:
    idx = pd.bdate_range("2020-01-01", periods=6)
    events = pd.Series([0, 0, int(Signal.BUY), 0, int(Signal.SELL), 0], index=idx, dtype="int8")
    state = state_from_events(events)
    assert state.tolist() == [0, 0, 1, 1, 0, 0]


def test_state_from_events_starts_flat() -> None:
    idx = pd.bdate_range("2020-01-01", periods=3)
    state = state_from_events(pd.Series([0, int(Signal.SELL), 0], index=idx, dtype="int8"))
    assert state.tolist() == [0, 0, 0]


def test_state_at_uses_last_known_state_not_future() -> None:
    idx = pd.bdate_range("2020-01-01", periods=4)
    state = pd.Series([0, 1, 1, 0], index=idx, dtype="int8")
    assert state_at(state, idx[0]) == 0
    assert state_at(state, idx[2]) == 1
    assert state_at(state, idx[3]) == 0
    # 早于序列起点的日期：空仓，不报错
    assert state_at(state, pd.Timestamp("2019-12-31")) == 0


def test_event_signals_alone_would_leave_the_book_empty() -> None:
    """反证：如果直接把事件序列当状态用，月末几乎选不到票。"""
    idx = pd.bdate_range("2020-01-01", periods=40)
    events = pd.Series(int(Signal.HOLD), index=idx, dtype="int8")
    events.iloc[3] = int(Signal.BUY)
    events.iloc[4:] = int(Signal.HOLD)
    month_ends = month_end_dates(idx)
    # 事件口径：月末那天是 HOLD
    assert all(int(events.loc[day]) == int(Signal.HOLD) for day in month_ends if day in events.index)
    # 状态口径：月末是"持有"
    state = state_from_events(events)
    assert all(state_at(state, day) == 1 for day in month_ends)


# --------------------------------------------------------------------------- #
# 三、调度与对照臂
# --------------------------------------------------------------------------- #
def test_month_end_dates_takes_last_trading_day_per_month() -> None:
    idx = pd.DatetimeIndex(
        ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02"]
    )
    got = month_end_dates(idx)
    # 3 月只有 03-02 一天，它仍是"当月最后一个可用交易日"——窗口端点处会出现
    # "月初即月末"的信号日，这是可接受的（信号仍落在窗口内、执行在其次日）。
    assert [str(d.date()) for d in got] == ["2020-01-31", "2020-02-28", "2020-03-02"]


def test_schedule_weights_sum_to_one_without_cap() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    states = {
        "A": pd.Series([1, 1, 1, 1, 1], index=idx, dtype="int8"),
        "B": pd.Series([1, 1, 1, 1, 1], index=idx, dtype="int8"),
        "C": pd.Series([0, 0, 0, 0, 0], index=idx, dtype="int8"),
    }
    schedule = schedule_from_states(states, [idx[0]])
    assert set(schedule[0].weights) == {"A", "B"}
    assert sum(schedule[0].weights.values()) == pytest.approx(1.0)


def test_schedule_respects_weight_cap_and_leaves_cash() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    states = {"A": pd.Series([1] * 5, index=idx, dtype="int8")}
    capped = schedule_from_states(states, [idx[0]], max_weight_per_asset=0.2)
    assert capped[0].weights["A"] == pytest.approx(0.2)
    assert sum(capped[0].weights.values()) == pytest.approx(0.2)


def test_all_cash_is_a_legal_state() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    states = {"A": pd.Series([0] * 5, index=idx, dtype="int8")}
    schedule = schedule_from_states(states, [idx[0], idx[1]])
    assert all(item.is_cash_only for item in schedule)


def test_control_schedule_holds_every_symbol() -> None:
    idx = pd.bdate_range("2020-01-01", periods=2)
    schedule = control_schedule(["A", "B", "C", "D"], [idx[0]])
    assert set(schedule[0].weights) == {"A", "B", "C", "D"}
    assert sum(schedule[0].weights.values()) == pytest.approx(1.0)
    assert control_schedule([], [idx[0]]) == ()


# --------------------------------------------------------------------------- #
# 四、标的池加载
# --------------------------------------------------------------------------- #
def test_load_universe_records_dropped_symbols(tmp_path) -> None:
    """剔标必须记账：静默缩池会让"30 只"在无人察觉时变成 27 只。"""
    data_dir = tmp_path / "qfq"
    data_dir.mkdir()
    idx = pd.bdate_range("2020-01-01", periods=300)
    good = pd.DataFrame(
        {"date": idx, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1e6}
    )
    good.to_csv(data_dir / "A.qfq.csv", index=False)
    few = good.head(5)
    few.to_csv(data_dir / "B.qfq.csv", index=False)

    spec_path = tmp_path / "universe.json"
    spec_path.write_text(
        json.dumps(
            {
                "universe_id": "U-TEST",
                "constituents": [
                    {"symbol": "A"},
                    {"symbol": "B"},
                    {"symbol": "MISSING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    universe = load_universe(
        spec_path, data_dir=data_dir, span_start="2020-01-01", span_end="2021-12-31"
    )
    assert universe.constituents == ["A"]
    reasons = {item["symbol"]: item["reason"] for item in universe.dropped}
    assert "qfq" in reasons["MISSING"]
    assert "行" in reasons["B"]
    payload = universe.to_dict()
    assert payload["size"] == 1
    assert len(payload["dropped"]) == 2


def test_load_universe_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(PortfolioGateError, match="不存在"):
        load_universe(tmp_path / "nope.json", data_dir=tmp_path, span_start="2020-01-01", span_end="2021-01-01")


# --------------------------------------------------------------------------- #
# 五、契约校验
# --------------------------------------------------------------------------- #
def test_unknown_portfolio_key_is_rejected() -> None:
    with pytest.raises(PortfolioGateError, match="未知键"):
        parse_portfolio_spec({"max_weight": 0.2})


def test_portfolio_spec_validates_fields() -> None:
    with pytest.raises(PortfolioGateError, match="allocation"):
        PortfolioSpec(allocation="equal")
    with pytest.raises(PortfolioGateError, match="max_weight_per_asset"):
        PortfolioSpec(max_weight_per_asset=1.5)
    with pytest.raises(PortfolioGateError, match="initial_cash"):
        PortfolioSpec(initial_cash=0.0)
    assert PortfolioSpec().to_dict()["rebalance"] == "monthly"


# --------------------------------------------------------------------------- #
# 六、判据同源与终态
# --------------------------------------------------------------------------- #
def test_decision_chain_is_literally_shared_with_l3() -> None:
    """L3p 不许自建判定链：必须是同一个函数对象。"""
    assert l3p.decide_from_summary is l3_module.decide_from_summary


def test_same_summary_yields_same_verdict_across_gates() -> None:
    summary = {
        "folds_total": 10,
        "folds_usable": 10,
        "test_positive_folds": 3,
        "test_positive_fold_ratio": 0.3,
        "train_positive_fold_ratio": 0.9,
        "test_return_mean": -0.02,
        "divergent_folds": 6,
        "divergent_fold_ratio": 0.6,
        "median_wfe": 0.1,
        "wfe_samples": 9,
    }
    overlap = {"independent": True, "overlap_pairs": 0}
    wf = WalkForwardSpec(train_days=252, test_days=63, step_days=63, min_folds=5)
    verdict, checks = decide_from_summary(
        summary, overlap, wf, usable_folds=10, failed_folds=0
    )
    assert verdict == "L3_REJECTED_SYSTEMATIC_DIVERGENCE"
    assert to_portfolio_verdict(verdict) == "L3P_REJECTED_SYSTEMATIC_DIVERGENCE"
    assert to_portfolio_verdict(verdict) in ALL_VERDICTS


def test_verdict_prefix_mapping_is_total() -> None:
    for name in ALL_VERDICTS:
        if name.startswith("L3P_"):
            back = "L3_" + name[len("L3P_") :]
            assert to_portfolio_verdict(back) == name
    # 不带前缀的输入原样返回，不制造新的终态
    assert to_portfolio_verdict("SOMETHING_ELSE") == "SOMETHING_ELSE"


def test_portfolio_fold_exposes_control_fields() -> None:
    fold = PortfolioFold(
        index=1,
        train_start=pd.Timestamp("2020-01-01"),
        train_end=pd.Timestamp("2020-06-30"),
        test_start=pd.Timestamp("2020-07-01"),
        test_end=pd.Timestamp("2020-12-31"),
        train_days=120,
        test_days=120,
        fills=42,
        avg_positions=12.5,
        control_return=0.03,
        excess_vs_control=0.01,
    )
    payload = fold.to_dict()
    for key in ("fills", "avg_positions", "control_return", "excess_vs_control"):
        assert key in payload
    assert payload["control_return"] == pytest.approx(0.03)


# --------------------------------------------------------------------------- #
# 七、端到端（真实数据、小规模）
# --------------------------------------------------------------------------- #
SMALL_UNIVERSE = ("000001.SZ", "600000.SH", "000002.SZ", "600009.SH", "002001.SZ", "300014.SZ")


def _small_spec(symbols) -> dict:
    return {
        "hypothesis_id": "H-L3P-TEST",
        "title": "组合级小规模",
        "strategy_id": "ma_cross",
        "param_grid": {"fast_window": [3, 10], "slow_window": [20, 60]},
        "constraints": [["fast_window", "slow_window"]],
        "data": {"symbol": "SMALL", "research_path": "unused.csv", "trading_path": ""},
        "in_sample": {"start": "2019-01-01", "end": "2020-12-31"},
        "out_sample": {"start": "2021-01-01", "end": "2021-06-30"},
        "boundaries": {
            "min_in_sample_return": -1.0,
            "max_in_sample_drawdown": 0.99,
            "min_closed_trades": 1,
            "min_out_sample_return": -1.0,
            "max_out_sample_drawdown": 0.99,
            "max_oos_degradation": 0.99,
        },
        "declared_grid_points": 4,
    }


def _small_universe(data_dir) -> Universe:
    frames = {}
    for code in SMALL_UNIVERSE:
        path = data_dir / f"{code}.qfq.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        frames[code] = frame.loc[(frame.index >= pd.Timestamp("2019-01-01")) & (frame.index <= pd.Timestamp("2021-06-30"))]
    if len(frames) < 4:
        pytest.skip("样本数据不足，跳过端到端用例")
    return Universe(
        universe_id="U-SMALL",
        frames=frames,
        constituents=sorted(frames),
        source="inline",
        rows_in_span={code: len(frame) for code, frame in frames.items()},
    )


@pytest.fixture(scope="module")
def small_result():
    from pathlib import Path

    data_dir = Path(__file__).resolve().parents[3] / "data" / "qfq_all"
    universe = _small_universe(data_dir)
    wf = WalkForwardSpec(
        train_days=120, test_days=40, step_days=40, min_folds=3, min_test_days=20,
        span_start="2019-01-01", span_end="2021-06-30",
    )
    return run_l3_portfolio(
        parse_spec(_small_spec(SMALL_UNIVERSE)),
        universe=universe,
        walk_forward=wf,
        portfolio=PortfolioSpec(initial_cash=1_000_000.0),
    )


def test_end_to_end_produces_folds_with_positions(small_result) -> None:
    assert small_result.verdict in ALL_VERDICTS
    usable = [fold for fold in small_result.folds if fold.ok]
    assert usable, "至少要有可用折"
    assert all((fold.avg_positions or 0) > 0 for fold in usable), "每折都应有持仓"
    assert small_result.portfolio["folds_with_positions"] == len(usable)


def test_end_to_end_has_control_arm(small_result) -> None:
    control = small_result.control
    assert control["folds_compared"] == len([f for f in small_result.folds if f.ok])
    assert control["beats_control_ratio"] is not None
    assert 0.0 <= control["beats_control_ratio"] <= 1.0


def test_end_to_end_report_has_required_sections(small_result) -> None:
    markdown = render_l3p_markdown(small_result, title="组合级小规模")
    for section in ("## 一、口径", "## 三、逐折明细", "## 四、折级汇总", "## 五、组合形态",
                    "## 六、与对照臂的对比", "## 八、判定明细", "## 九、口径与限制"):
        assert section in markdown
    assert small_result.verdict in markdown


def test_end_to_end_limitations_state_the_real_boundaries(small_result) -> None:
    joined = " ".join(small_result.limitations)
    assert "不降低共同因子风险" in joined
    assert "折收益仍不可拼成连续净值" in joined
    assert "只针对参数选择" in joined
    assert "生存偏差" in joined
    assert "30 只≠全市场" in joined or "≠全市场" in joined


def test_end_to_end_is_deterministic(small_result) -> None:
    """同一输入重跑必须逐折一致——否则报告无法复核。"""
    from pathlib import Path

    data_dir = Path(__file__).resolve().parents[3] / "data" / "qfq_all"
    universe = _small_universe(data_dir)
    wf = WalkForwardSpec(
        train_days=120, test_days=40, step_days=40, min_folds=3, min_test_days=20,
        span_start="2019-01-01", span_end="2021-06-30",
    )
    again = run_l3_portfolio(
        parse_spec(_small_spec(SMALL_UNIVERSE)),
        universe=universe,
        walk_forward=wf,
        portfolio=PortfolioSpec(initial_cash=1_000_000.0),
    )
    left = [(f.index, f.best_params, f.train_return, f.test_return) for f in small_result.folds]
    right = [(f.index, f.best_params, f.train_return, f.test_return) for f in again.folds]
    assert left == right
