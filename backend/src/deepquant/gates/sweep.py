"""L1a 粗筛：网格扫描、覆盖率核算、样本内选参、样本外只评一次。

三个容易糊弄过去的点，这里都做成硬约束
--------------------------------------
1. **覆盖率**：分母是「契约声明的网格点数」，不是「实际跑成功的点数」。
   跑失败的点也算"没覆盖"，否则少跑几个点就能把覆盖率做到 100%。
2. **选参只用样本内**：最优参数在样本内指标上选出；样本外只做**一次**评估，
   且评估发生在选择之后（代码顺序即证据，报告里同时登记 ``oos_evaluations``）。
3. **参数高原**：单点最优很容易是噪声。这里报"邻域内正收益点占比"——
   邻域定义为「与最优点只差一个参数、且各差一格」，这是最小可辩护的定义。

全流程 fail-closed：任一参数点抛错都会被记录，不会静默跳过。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.gates.metrics import Metric, ok, unavailable
from deepquant.gates.spec import HypothesisSpec

#: 策略工厂签名：(strategy_id, params) -> Strategy。默认为 API 注册表实现。
StrategyFactory = Callable[[str, Mapping[str, Any]], Any]


def default_strategy_factory(strategy_id: str, params: Mapping[str, Any]) -> Any:
    """复用 API 注册表构造策略：闸门与线上入口用同一套参数校验，不另开一条路。"""
    from deepquant.api.registry import build_strategy

    return build_strategy(strategy_id, dict(params))


@dataclass
class PointOutcome:
    params: dict[str, Any]
    ok: bool
    metrics: dict[str, Metric] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "ok": self.ok,
            "error": self.error,
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
        }


@dataclass
class SweepResult:
    points: list[PointOutcome] = field(default_factory=list)
    declared_points: int = 0
    executed_points: int = 0
    failed_points: int = 0
    best: PointOutcome | None = None
    oos: PointOutcome | None = None
    oos_evaluations: int = 0
    plateau_ratio: float | None = None
    plateau_note: str = ""

    @property
    def coverage(self) -> float:
        return (self.executed_points / self.declared_points) if self.declared_points else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_points": self.declared_points,
            "executed_points": self.executed_points,
            "failed_points": self.failed_points,
            "coverage": self.coverage,
            "best": self.best.to_dict() if self.best else None,
            "oos": self.oos.to_dict() if self.oos else None,
            "oos_evaluations": self.oos_evaluations,
            "plateau_ratio": self.plateau_ratio,
            "plateau_note": self.plateau_note,
            # 逐点明细可能很长；报告只引用摘要，明细留在这里供复核
            "points": [point.to_dict() for point in self.points],
        }


def expand_grid(param_grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """按参数名排序后做笛卡尔积，保证顺序确定（报告可复现）。"""
    names = sorted(param_grid)
    value_lists = [list(param_grid[name]) for name in names]
    return [dict(zip(names, combo)) for combo in itertools.product(*value_lists)]


def _metrics_from_result(result: Any) -> dict[str, Metric]:
    performance = getattr(result, "performance", None)
    if performance is None:
        return {
            "cumulative_return": unavailable("cumulative_return", "回测结果未产出绩效指标"),
            "max_drawdown": unavailable("max_drawdown", "回测结果未产出绩效指标"),
            "closed_trades": unavailable("closed_trades", "回测结果未产出绩效指标"),
            "annualized_return": unavailable("annualized_return", "回测结果未产出绩效指标"),
        }

    trades = performance.trade_stats
    unavailable_reasons = tuple(performance.unavailable_metrics or ())
    metrics: dict[str, Metric] = {}

    if performance.cumulative_return is None:
        metrics["cumulative_return"] = unavailable(
            "cumulative_return",
            "；".join(unavailable_reasons) or "累计收益不可计算（权益曲线不完整）",
        )
    else:
        metrics["cumulative_return"] = ok("cumulative_return", performance.cumulative_return, "ratio")

    if performance.max_drawdown_ratio is None:
        metrics["max_drawdown"] = unavailable(
            "max_drawdown",
            "；".join(unavailable_reasons) or "回撤不可计算（可用权益点不足）",
        )
    else:
        metrics["max_drawdown"] = ok("max_drawdown", performance.max_drawdown_ratio, "ratio")

    metrics["closed_trades"] = ok("closed_trades", float(trades.n_closed_trades), "count", samples=trades.n_closed_trades)

    if performance.annualized_return is None:
        metrics["annualized_return"] = unavailable(
            "annualized_return",
            "；".join(unavailable_reasons) or "年化收益不可计算（样本期不足）",
        )
    else:
        metrics["annualized_return"] = ok("annualized_return", performance.annualized_return, "ratio")

    if not result.is_complete:
        # 回测本身没跑完：指标照报，但必须带上"不完整"的原因，闸门会因此拒绝据此选参。
        metrics["complete"] = unavailable(
            "complete", "回测未完成：" + "；".join(result.incomplete_reasons or ("原因未记录",))
        )
    return metrics


def _run_point(
    data: pd.DataFrame,
    spec: HypothesisSpec,
    params: dict[str, Any],
    window: tuple[str, str],
    factory: StrategyFactory,
) -> PointOutcome:
    try:
        strategy = factory(spec.strategy_id, params)
    except Exception as exc:  # noqa: BLE001 - 参数组合非法属预期情形，必须记为失败点
        return PointOutcome(params=params, ok=False, error=f"构造策略失败：{type(exc).__name__}: {exc}")
    config = BacktestConfig(
        symbol=spec.data.symbol,
        initial_cash=spec.initial_cash,
        start_date=pd.Timestamp(window[0]),
        end_date=pd.Timestamp(window[1]),
    )
    if config.signal_lag < 1:
        return PointOutcome(params=params, ok=False, error="signal_lag < 1：信号与成交同日，等价未来函数")
    try:
        result = run_backtest(data, strategy, config)
    except Exception as exc:  # noqa: BLE001
        return PointOutcome(params=params, ok=False, error=f"回测失败：{type(exc).__name__}: {exc}")
    return PointOutcome(params=params, ok=True, metrics=_metrics_from_result(result))


def _score(point: PointOutcome, metric_name: str) -> float | None:
    metric = point.metrics.get(metric_name)
    if metric is None or not metric.available or metric.value is None:
        return None
    return float(metric.value)


def _plateau(points: Sequence[PointOutcome], best: PointOutcome, grid: Mapping[str, Sequence[Any]]) -> tuple[float | None, str]:
    """邻域正收益占比：与最优点只差一个参数、各差一格的点里，收益为正的比例。"""
    names = sorted(grid)
    neighbours: list[PointOutcome] = []
    for point in points:
        if not point.ok or point is best:
            continue
        differing = [name for name in names if point.params.get(name) != best.params.get(name)]
        if len(differing) != 1:
            continue
        name = differing[0]
        values = list(grid[name])
        try:
            best_index = values.index(best.params.get(name))
            other_index = values.index(point.params.get(name))
        except ValueError:
            continue
        if abs(best_index - other_index) == 1:
            neighbours.append(point)
    scored = [item for item in neighbours if _score(item, "cumulative_return") is not None]
    if not scored:
        return None, "邻域内没有可比较的已完成点（网格过稀或参数点普遍失败）"
    positive = sum(1 for item in scored if (_score(item, "cumulative_return") or 0.0) > 0)
    return positive / len(scored), f"邻域 {len(scored)} 点中 {positive} 点收益为正"


def run_sweep(
    spec: HypothesisSpec,
    data: pd.DataFrame,
    *,
    factory: StrategyFactory = default_strategy_factory,
) -> SweepResult:
    """执行样本内网格 + 单次样本外评估。

    顺序即证据：先全网格（仅样本内）→ 选最优 → 再跑一次样本外。中间没有任何
    用样本外数据回头改参数的步骤。
    """
    result = SweepResult(declared_points=spec.declared_grid_points or spec.grid_size)
    # 分母是「通过约束过滤后的组合数」：契约里就不可能成立的组合不算漏跑
    combos = spec.grid_points()
    in_window = (spec.in_sample.start, spec.in_sample.end)
    for params in combos:
        outcome = _run_point(data, spec, params, in_window, factory)
        result.points.append(outcome)
        if outcome.ok:
            result.executed_points += 1
        else:
            result.failed_points += 1

    candidates = [point for point in result.points if point.ok]
    scored = [(point, _score(point, spec.selection_metric)) for point in candidates]
    scored = [(point, value) for point, value in scored if value is not None]
    if not scored:
        return result
    # 选参：只按样本内指标；并列时取参数顺序靠前者（确定性）
    best = sorted(scored, key=lambda item: -item[1])[0][0]
    result.best = best
    result.plateau_ratio, result.plateau_note = _plateau(result.points, best, spec.param_grid)

    out_window = (spec.out_sample.start, spec.out_sample.end)
    result.oos = _run_point(data, spec, dict(best.params), out_window, factory)
    result.oos_evaluations += 1
    return result
