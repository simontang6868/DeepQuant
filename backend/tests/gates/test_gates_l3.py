"""L3 Walk-Forward 闸门测试。

三条主线，各自对应一种「会造假」的写法：

1. **窗口结构**：训练窗与测试窗必须严格相邻且不共用任何一天；测试窗重叠时
   折数不能当独立证据（这类"通过"是本闸门最容易产生的假阳性）。
2. **选参与打分分离**：每折必须"训练窗跑满网格 + 测试窗只跑一次"。
   这一点用**回测调用记录**直接证明，而不是相信代码注释。
3. **判定链**：PASSED / 系统性背离 / 折间不一致 / 不可判定 必须能被分别逼出来。
   合成行情按「趋势市 / 周期震荡 / 趋势后转震荡」三种形态构造——
   它们分别代表"策略有效""策略在震荡里亏""策略失效"，是判定链的最小检验集。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from deepquant.gates import sweep as sweep_module
from deepquant.gates.l3 import (
    ALL_VERDICTS,
    VERDICT_DIVERGENCE,
    VERDICT_INCONCLUSIVE,
    VERDICT_INCONSISTENCY,
    VERDICT_PASSED,
    VERDICT_PREREQUISITE,
    VERDICT_DATA,
    VERDICT_SPEC,
    VERDICT_WINDOW,
    WalkForwardSpec,
    WalkForwardSpecError,
    build_folds,
    overlap_report,
    parse_walk_forward,
    render_l3_markdown,
    resolve_warmup,
    run_l3,
)
from deepquant.gates.spec import parse_spec
from deepquant.strategies.base import Signal, Strategy

# --------------------------------------------------------------------------- #
# 夹具：合成行情
# --------------------------------------------------------------------------- #
GRID = {"fast_window": [3, 5], "slow_window": [10, 20]}
GRID_POINTS = 4  # fast < slow 约束后：(3,10)(3,20)(5,10)(5,20)


class TrendStateStrategy(Strategy):
    """状态型趋势策略：快线在上就持有，否则空仓。

    刻意不用仓库里的 ``MaCrossStrategy``：它只在**交叉当天**出信号，
    单边趋势里一次建仓后就再无信号，导致后续折"零成交"，跨折比较失去意义。
    本类每个窗口都会重新建仓，正好用来检验折间一致性。
    """

    name = "trend_state"
    version = "0.0.1"

    def __init__(self, fast_window: int = 3, slow_window: int = 10) -> None:
        self.fast_window = fast_window
        self.slow_window = slow_window

    @property
    def params(self) -> dict:
        return {"fast_window": self.fast_window, "slow_window": self.slow_window}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        fast = close.rolling(self.fast_window, min_periods=self.fast_window).mean()
        slow = close.rolling(self.slow_window, min_periods=self.slow_window).mean()
        out = pd.Series(int(Signal.HOLD), index=close.index, dtype="int8")
        out[fast > slow] = int(Signal.BUY)
        out[fast < slow] = int(Signal.SELL)
        return out


def factory(strategy_id: str, params: dict) -> Strategy:
    return TrendStateStrategy(**params)


def _frame(prices: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2018-01-01", periods=len(prices))
    close = np.asarray(prices, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]]) * 1.001
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    return pd.DataFrame(
        {"date": idx, "open": open_, "high": high, "low": low, "close": close, "volume": 1e6}
    ).set_index("date")


def _block(n: int, start: float, kind: str, *, up: float = 0.005, amp: float = 0.09, cycle: int = 2) -> np.ndarray:
    out = [start]
    for i in range(1, n):
        if kind == "up":
            out.append(out[-1] * (1 + up))
        else:
            phase = i % (2 * cycle)
            out.append(out[-1] * (1 + (amp if phase < cycle else -amp)))
    return np.array(out)


def _series(pattern: str, *, start: float = 10.0, block_days: int = 20) -> np.ndarray:
    """按形态串拼价格：U=平滑上涨块，O=剧烈震荡块。先放一段下跌制造首次上穿。"""
    values = list((start * np.cumprod(np.full(20, 0.996))).tolist())
    level = values[-1]
    for char in pattern:
        segment = _block(block_days + 1, level, "up" if char == "U" else "chop")[1:]
        values.extend(segment.tolist())
        level = segment[-1]
    return np.array(values)


def _spec(frame: pd.DataFrame, *, declared: int = GRID_POINTS, grid: dict | None = None) -> dict:
    return {
        "hypothesis_id": "H-L3-TEST",
        "title": "L3 合成用例",
        "strategy_id": "trend_state",
        "param_grid": grid or GRID,
        "constraints": [["fast_window", "slow_window"]],
        "data": {"symbol": "SYN", "research_path": "unused.csv", "trading_path": ""},
        # 数据门禁要求样本内 ≥250 行，所以样本内取到倒数第 22 根
        "in_sample": {"start": str(frame.index[0].date()), "end": str(frame.index[-22].date())},
        "out_sample": {"start": str(frame.index[-21].date()), "end": str(frame.index[-1].date())},
        "boundaries": {
            "min_in_sample_return": -1.0,
            "max_in_sample_drawdown": 0.99,
            "min_closed_trades": 1,
            "min_out_sample_return": -1.0,
            "max_out_sample_drawdown": 0.99,
            "max_oos_degradation": 0.99,
        },
        "declared_grid_points": declared,
    }


WF = WalkForwardSpec(train_days=40, test_days=20, step_days=20, min_folds=5, min_test_days=10)


def _run(pattern: str, *, wf: WalkForwardSpec = WF, **kwargs):
    frame = _frame(_series(pattern))
    return run_l3(parse_spec(_spec(frame)), data=frame, walk_forward=wf, factory=factory, **kwargs)


# --------------------------------------------------------------------------- #
# 一、窗口结构
# --------------------------------------------------------------------------- #
def test_windows_are_adjacent_and_do_not_share_days() -> None:
    """训练窗末根的**下一天**才是测试窗首根——共用一天就是最隐蔽的泄漏。"""
    index = _frame(_series("U" * 8)).index
    folds, _ = build_folds(index, WF)
    assert len(folds) > 3
    positions = {day: i for i, day in enumerate(index)}
    for fold in folds:
        assert positions[fold.test_start] == positions[fold.train_end] + 1
        assert fold.test_days == WF.test_days


def test_test_windows_are_pairwise_disjoint() -> None:
    index = _frame(_series("U" * 10)).index
    folds, _ = build_folds(index, WF)
    report = overlap_report(folds)
    assert report["independent"] is True
    assert report["overlap_pairs"] == 0
    covered: set = set()
    for fold in folds:
        days = set(index[(index >= fold.test_start) & (index <= fold.test_end)])
        assert not (covered & days), "测试窗出现重复交易日"
        covered |= days


def test_anchored_mode_keeps_train_start_fixed_and_grows() -> None:
    """锚定模式：训练窗起点固定、长度逐折 +step（首版漏了这一步，切窗死循环）。"""
    index = _frame(_series("U" * 8)).index
    folds, _ = build_folds(index, WalkForwardSpec(train_days=40, test_days=20, step_days=20, mode="anchored"))
    assert len(folds) > 2
    assert {fold.train_start for fold in folds} == {index[0]}
    lengths = [fold.train_days for fold in folds]
    assert lengths == [40 + 20 * i for i in range(len(folds))]
    assert len(set(lengths)) == len(lengths), "锚定模式下每折训练窗必须比上一折更长"


def test_step_smaller_than_test_is_flagged_as_overlap() -> None:
    index = _frame(_series("U" * 8)).index
    folds, _ = build_folds(index, WalkForwardSpec(train_days=40, test_days=20, step_days=10))
    report = overlap_report(folds)
    assert report["independent"] is False
    assert report["overlap_pairs"] >= 1


def test_zero_folds_when_history_too_short() -> None:
    index = _frame(_series("U" * 2)).index
    folds, meta = build_folds(index, WalkForwardSpec(train_days=len(index) - 10, test_days=20))
    assert folds == []
    assert meta["folds"] == 0


def test_unused_tail_is_reported_not_hidden() -> None:
    index = _frame(_series("U" * 9)).index
    folds, meta = build_folds(index, WF)
    last_used = index.get_loc(folds[-1].test_end) + 1
    assert meta["unused_tail_days"] == len(index) - last_used


# --------------------------------------------------------------------------- #
# 二、契约校验
# --------------------------------------------------------------------------- #
def test_unknown_walk_forward_key_is_rejected() -> None:
    """拼错的键被静默忽略 = 阈值看起来生效其实没生效，必须拒绝。"""
    with pytest.raises(WalkForwardSpecError, match="未知键"):
        parse_walk_forward({"train_day": 40})


def test_contradictory_windows_are_rejected() -> None:
    with pytest.raises(WalkForwardSpecError, match="窗口自相矛盾"):
        WalkForwardSpec(test_days=10, min_test_days=20)
    with pytest.raises(WalkForwardSpecError, match="mode"):
        WalkForwardSpec(mode="sliding")
    with pytest.raises(WalkForwardSpecError, match="step_days"):
        WalkForwardSpec(step_days=0)


def test_parse_walk_forward_roundtrip() -> None:
    spec = parse_walk_forward({"train_days": 60, "test_days": 30, "step_days": 30, "min_folds": 4})
    assert spec.train_days == 60
    assert spec.test_days == 30
    assert spec.min_folds == 4
    assert spec.overlaps is False


def test_warmup_follows_grid_when_not_given() -> None:
    frame = _frame(_series("U" * 6))
    spec = parse_spec(_spec(frame))
    assert resolve_warmup(WalkForwardSpec(), spec) == 20 * 2  # 网格最大整数 20 × 倍数 2
    assert resolve_warmup(WalkForwardSpec(warmup_days=7), spec) == 7


# --------------------------------------------------------------------------- #
# 三、选参与打分分离（用回测调用记录证明）
# --------------------------------------------------------------------------- #
def test_selection_and_scoring_are_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """每折必须「训练窗跑满网格 + 测试窗只跑一次」，且测试窗绝不参与选参。"""
    calls: list[tuple[str, str, str]] = []
    original = sweep_module.run_backtest

    def spy(data, strategy, config):  # noqa: ANN001
        calls.append(
            (
                str(config.start_date.date()),
                str(config.end_date.date()),
                json.dumps(strategy.params, sort_keys=True),
            )
        )
        return original(data, strategy, config)

    monkeypatch.setattr(sweep_module, "run_backtest", spy)

    frame = _frame(_series("U" * 10))
    result = run_l3(parse_spec(_spec(frame)), data=frame, walk_forward=WF, factory=factory)
    folds = [fold for fold in result.folds if fold.ok]

    train_windows = {(str(f.train_start.date()), str(f.train_end.date())) for f in folds}
    test_windows = {(str(f.test_start.date()), str(f.test_end.date())) for f in folds}
    # 窗口互不重叠 ⇒ 每个窗口只对应一折
    assert len(train_windows) == len(folds)
    assert len(test_windows) == len(folds)
    assert not (train_windows & test_windows)

    for start, end, params in calls:
        window = (start, end)
        assert window in train_windows or window in test_windows

    for fold in folds:
        train_key = (str(fold.train_start.date()), str(fold.train_end.date()))
        test_key = (str(fold.test_start.date()), str(fold.test_end.date()))
        train_calls = [item for item in calls if (item[0], item[1]) == train_key]
        test_calls = [item for item in calls if (item[0], item[1]) == test_key]
        assert len(train_calls) == GRID_POINTS, f"折 {fold.index} 训练窗没跑满网格"
        assert len(test_calls) == 1, f"折 {fold.index} 测试窗被评估了 {len(test_calls)} 次"
        # 测试窗那一次用的必须是训练窗选出的参数
        assert json.loads(test_calls[0][2]) == fold.best_params
        # 训练窗内每个参数恰好一次
        assert len({item[2] for item in train_calls}) == GRID_POINTS


# --------------------------------------------------------------------------- #
# 四、判定链
# --------------------------------------------------------------------------- #
def test_trend_market_passes() -> None:
    result = _run("U" * 20)
    assert result.verdict == VERDICT_PASSED
    assert result.summary["test_positive_fold_ratio"] == 1.0
    assert result.summary["median_wfe"] >= WF.min_median_wfe


def test_periodic_chop_after_trend_triggers_divergence() -> None:
    """趋势段训练、震荡段测试：训练普遍为正而测试平均为负 = 表 7 的一票否决项。"""
    result = _run("UUO" * 7)
    assert result.verdict == VERDICT_DIVERGENCE
    summary = result.summary
    assert summary["train_positive_fold_ratio"] >= WF.min_train_positive_ratio
    assert summary["test_return_mean"] <= 0
    assert summary["divergent_folds"] >= 1
    names = [check["name"] for check in result.checks]
    assert "样本内外未出现符号背离" in names
    assert next(c for c in result.checks if c["name"] == "样本内外未出现符号背离")["passed"] is False


def test_trend_then_chop_is_rejected_as_inconsistent() -> None:
    """趋势后长期转震荡：训练也亏 ⇒ 不是"背离"而是"没有一致性"，两者都该被拒。"""
    result = _run("U" * 10 + "O" * 10)
    assert result.verdict in (VERDICT_INCONSISTENCY, VERDICT_DIVERGENCE)
    assert result.verdict != VERDICT_PASSED
    assert result.summary["test_positive_fold_ratio"] < WF.min_positive_fold_ratio


def test_too_few_folds_is_inconclusive_not_failure() -> None:
    """3 折的均值本身就是噪声：不可判定 ≠ 不通过。"""
    strict = WalkForwardSpec(train_days=40, test_days=20, step_days=20, min_folds=99)
    result = _run("U" * 20, wf=strict)
    assert result.verdict == VERDICT_INCONCLUSIVE
    # 数字照算，不因为"不可判定"就把证据丢掉
    assert result.summary["folds_usable"] > 0


def test_overlapping_windows_can_never_pass() -> None:
    overlapping = WalkForwardSpec(train_days=40, test_days=20, step_days=10, min_folds=3)
    result = _run("U" * 20, wf=overlapping)
    assert result.verdict == VERDICT_INCONCLUSIVE
    assert result.overlap["independent"] is False
    check = next(c for c in result.checks if c["name"] == "测试窗互不重叠")
    assert check["passed"] is False


def test_short_history_is_rejected_as_window_problem() -> None:
    # 夹具必须够长：数据门禁要求样本内 ≥250 行，否则先被"数据不达标"拦住，
    # 测到的就不是"切窗不足"这条路径了。
    frame = _frame(_series("U" * 20))
    wf = WalkForwardSpec(train_days=len(frame) - 20, test_days=60, min_test_days=10)
    result = run_l3(parse_spec(_spec(frame)), data=frame, walk_forward=wf, factory=factory)
    assert result.verdict == VERDICT_WINDOW
    assert result.folds == []
    assert any("窗口不足" in item for item in result.limitations)


def test_invalid_spec_returns_rejected_spec() -> None:
    frame = _frame(_series("U" * 6))
    payload = _spec(frame)
    payload["param_grid"] = {}
    result = run_l3(payload, data=frame, walk_forward=WF, factory=factory)
    assert result.verdict == VERDICT_SPEC
    assert result.stages[0]["name"] == "spec"


def test_data_gate_is_applied_over_the_walk_forward_span() -> None:
    """门禁窗口必须覆盖实际跨度，而不是契约的 in_sample。"""
    frame = _frame(_series("U" * 20))
    payload = _spec(frame)
    # 跨度只覆盖最后 100 行：契约的 in_sample 有 398 行（能过门禁），
    # 只有"按跨度门禁"才会发现这段跨度不足 250 行。
    wf = WalkForwardSpec(
        train_days=40,
        test_days=20,
        step_days=20,
        min_folds=3,
        span_start=str(frame.index[-100].date()),
        span_end=str(frame.index[-1].date()),
    )
    result = run_l3(parse_spec(payload), data=frame, walk_forward=wf, factory=factory)
    assert result.verdict == VERDICT_DATA
    assert any("数据门禁未通过" in item or "没有可用行情" in item for item in result.limitations)


def test_span_is_restricted_before_walking() -> None:
    """给定跨度后，折只在该跨度内切，不会偷偷用到跨度外的行情。"""
    frame = _frame(_series("U" * 20))
    # 跨度取 301 行：低于数据门禁的 250 行门槛会被判"数据不足"，测不到切窗
    start = str(frame.index[50].date())
    end = str(frame.index[350].date())
    wf = WalkForwardSpec(train_days=40, test_days=20, step_days=20, min_folds=3,
                         span_start=start, span_end=end)
    result = run_l3(parse_spec(_spec(frame)), data=frame, walk_forward=wf, factory=factory)
    assert result.folds, "跨度内应切得出折"
    assert result.folds[0].train_start >= pd.Timestamp(start)
    assert result.folds[-1].test_end <= pd.Timestamp(end)


def test_prerequisite_downgrades_a_passing_result() -> None:
    """L1a 已否决的假设，即使在 L3 全过也不能当放行结论。"""
    result = _run("U" * 20, prior_verdicts={"L1a": "L1A_REJECTED_BOUNDS"})
    assert result.verdict == VERDICT_PREREQUISITE
    assert result.summary["folds_usable"] > 0  # 数字仍然算完并保留
    assert any("不构成放行结论" in item for item in result.limitations)


def test_all_verdicts_are_declared() -> None:
    assert set(ALL_VERDICTS) == {
        VERDICT_PASSED,
        VERDICT_DIVERGENCE,
        VERDICT_INCONSISTENCY,
        VERDICT_WINDOW,
        VERDICT_DATA,
        VERDICT_INCONCLUSIVE,
        VERDICT_SPEC,
        VERDICT_PREREQUISITE,
    }


# --------------------------------------------------------------------------- #
# 五、诚实性约束
# --------------------------------------------------------------------------- #
def test_result_never_contains_a_continuous_equity_curve() -> None:
    """折收益不可拼成净值：结果里不许出现任何"连续账户"字段。"""
    result = _run("UUO" * 7)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    for forbidden in ("equity_curve", "continuous_equity", "compounded_return", "nav"):
        assert forbidden not in payload
    assert "naive_sum_of_fold_returns" in result.summary
    assert any("不可拼成连续净值" in item for item in result.limitations)
    assert any("只针对参数选择" in item for item in result.limitations)


def test_wfe_is_unavailable_with_reason_when_train_loses() -> None:
    result = _run("U" * 10 + "O" * 10)
    losing = [fold for fold in result.folds if fold.ok and (fold.train_return or 0) <= 0]
    assert losing, "该用例应产生训练亏损的折"
    for fold in losing:
        assert fold.wfe is None
        assert fold.wfe_reason, "不可计算必须写理由"


def test_failed_folds_are_counted_not_silently_dropped() -> None:
    result = _run("U" * 20)
    assert result.summary["folds_total"] == len(result.folds)
    assert result.summary["folds_failed"] == len([f for f in result.folds if not f.ok])


def test_markdown_report_covers_required_sections() -> None:
    result = _run("UUO" * 7)
    markdown = render_l3_markdown(result, title="合成用例")
    for section in ("## 一、切窗规则", "## 三、逐折明细", "## 四、折级汇总", "## 六、判定明细", "## 七、口径与限制"):
        assert section in markdown
    assert result.verdict in markdown
    assert "不可复利" in markdown


def test_l3_is_pure_computation_no_side_effects(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """闸门只算不写：跑完不在工作目录留下任何文件。"""
    monkeypatch.chdir(tmp_path)
    frame = _frame(_series("U" * 8))
    run_l3(parse_spec(_spec(frame)), data=frame, walk_forward=WF, factory=factory)
    assert list(tmp_path.iterdir()) == []
