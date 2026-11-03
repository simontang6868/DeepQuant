"""L1a 闸门编排：否决链、终态枚举、证据汇总。

否决链顺序（顺序本身就是判定逻辑）
----------------------------------
``契约 → 数据 → 未来函数 → 网格覆盖 → 收益/回撤边界``

为什么把数据和未来函数放在收益之前：一个策略收益再漂亮，只要数据不达标或偷看了
未来，结论就是零信息量。先跑便宜的、能一票否决的检查，也避免在脏数据上浪费
几百次回测。

终态枚举（不允许出现"其他"）
---------------------------
- ``L1A_PASS``：全部判定可用且都过
- ``L1A_REJECTED_SPEC``：契约不完整/自相矛盾（未进入评估）
- ``L1A_REJECTED_DATA``：数据门禁有阻塞项
- ``L1A_REJECTED_LEAKAGE``：静态或动态检测到未来函数
- ``L1A_REJECTED_COVERAGE``：网格没跑满声明点数
- ``L1A_REJECTED_BOUNDS``：边界判定明确失败
- ``L1A_INCONCLUSIVE``：有边界判不了（指标不可计算/样本不足/样本外评估次数异常）
- ``L1A_ERROR``：闸门自身异常（必须排查，不是策略问题）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

from deepquant.gates.data_gate import check_data, check_frame
from deepquant.gates.leakage import LeakResult, check_leakage
from deepquant.gates.metrics import BoundCheck, Metric, check_bound, summarise, unavailable
from deepquant.gates.spec import HypothesisSpec, SpecError, dump_spec, parse_spec
from deepquant.gates.sweep import StrategyFactory, SweepResult, default_strategy_factory, run_sweep

PASS = "L1A_PASS"
REJECTED_SPEC = "L1A_REJECTED_SPEC"
REJECTED_DATA = "L1A_REJECTED_DATA"
REJECTED_LEAKAGE = "L1A_REJECTED_LEAKAGE"
REJECTED_COVERAGE = "L1A_REJECTED_COVERAGE"
REJECTED_BOUNDS = "L1A_REJECTED_BOUNDS"
INCONCLUSIVE = "L1A_INCONCLUSIVE"
ERROR = "L1A_ERROR"

VERDICTS = (
    PASS,
    REJECTED_SPEC,
    REJECTED_DATA,
    REJECTED_LEAKAGE,
    REJECTED_COVERAGE,
    REJECTED_BOUNDS,
    INCONCLUSIVE,
    ERROR,
)

REPORT_VERSION = "l1a-report-v1"


@dataclass(frozen=True)
class Stage:
    name: str
    status: str  # "pass" / "fail" / "undetermined" / "skipped"
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class GateResult:
    verdict: str
    spec: dict[str, Any] = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=list)
    bound_checks: list[BoundCheck] = field(default_factory=list)
    metrics: dict[str, Metric] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    leakage: dict[str, Any] = field(default_factory=dict)
    sweep: dict[str, Any] = field(default_factory=dict)
    honesty: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": REPORT_VERSION,
            "verdict": self.verdict,
            "spec": self.spec,
            "stages": [stage.to_dict() for stage in self.stages],
            "bound_checks": [check.to_dict() for check in self.bound_checks],
            "metrics": {name: metric.to_dict() for name, metric in sorted(self.metrics.items())},
            "data": self.data,
            "leakage": self.leakage,
            "sweep": self.sweep,
            "honesty": self.honesty,
            "notes": self.notes,
        }


def _coerce_spec(spec_or_payload: HypothesisSpec | Mapping[str, Any]) -> HypothesisSpec:
    if isinstance(spec_or_payload, HypothesisSpec):
        return spec_or_payload
    return parse_spec(spec_or_payload)


def run_l1a(
    spec_or_payload: HypothesisSpec | Mapping[str, Any],
    *,
    data: pd.DataFrame | None = None,
    factory: StrategyFactory = default_strategy_factory,
    probes: int = 8,
) -> GateResult:
    """执行 L1a 闸门。``data`` 为空时按契约里的数据源自行加载。"""
    try:
        spec = _coerce_spec(spec_or_payload)
    except SpecError as exc:
        result = GateResult(verdict=REJECTED_SPEC)
        result.stages.append(Stage("spec", "fail", str(exc)))
        return result

    result = GateResult(verdict=REJECTED_SPEC, spec=dump_spec(spec))
    result.stages.append(Stage("spec", "pass", f"契约摘要 {spec.digest()[:16]}…"))

    # ---- 1. 数据门禁 ----
    if data is not None:
        data_verdict = check_frame(data, window=spec.in_sample, label="调用方传入的内存数据")
    else:
        data_verdict = check_data(spec.data, spec.in_sample)
    frame = data_verdict.frame
    result.data = data_verdict.to_dict()
    if not data_verdict.ok:
        detail = "；".join(f"{check.code}: {check.detail}" for check in data_verdict.blocking_failures)
        result.stages.append(Stage("data", "fail", detail))
        result.verdict = REJECTED_DATA
        return result
    undetermined = [check.code for check in data_verdict.undetermined]
    result.stages.append(
        Stage(
            "data",
            "undetermined" if undetermined else "pass",
            "阻塞项全部通过"
            + (f"；未判定项：{undetermined}（不等于通过）" if undetermined else ""),
        )
    )
    if frame is None:
        result.stages.append(Stage("data_frame", "fail", "数据为空，无法继续"))
        result.verdict = REJECTED_DATA
        return result

    # ---- 2. 未来函数 ----
    first_params = _first_params(spec)
    try:
        strategy = factory(spec.strategy_id, first_params)
    except Exception as exc:  # noqa: BLE001
        result.stages.append(Stage("strategy", "fail", f"构造策略失败：{type(exc).__name__}: {exc}"))
        result.verdict = ERROR
        return result
    leakage: LeakResult = check_leakage(strategy, frame, probes=probes)
    result.leakage = leakage.to_dict()
    if leakage.leaked:
        result.stages.append(
            Stage("leakage", "fail", "；".join(f"{item.code}@{item.location}" for item in leakage.findings))
        )
        result.verdict = REJECTED_LEAKAGE
        return result
    result.stages.append(
        Stage(
            "leakage",
            "pass" if leakage.probes_tested else "undetermined",
            (
                f"静态 0 命中；动态截断 {leakage.probes_tested} 个点全部一致"
                if leakage.probes_tested
                else f"静态 0 命中；动态未取得证据：{leakage.dynamic_note}"
            ),
        )
    )

    # ---- 3. 网格扫描（仅样本内）与覆盖率 ----
    sweep: SweepResult = run_sweep(spec, frame, factory=factory)
    result.sweep = _sweep_summary(sweep)
    if sweep.coverage < 1.0:
        result.stages.append(
            Stage(
                "coverage",
                "fail",
                f"覆盖 {sweep.executed_points}/{sweep.declared_points} = {sweep.coverage:.1%}"
                f"（失败 {sweep.failed_points} 点）",
            )
        )
        result.verdict = REJECTED_COVERAGE
        return result
    result.stages.append(
        Stage("coverage", "pass", f"覆盖 {sweep.executed_points}/{sweep.declared_points} = 100%")
    )
    if sweep.best is None:
        result.stages.append(Stage("selection", "fail", "样本内没有任何可用指标点，无法选参"))
        result.verdict = INCONCLUSIVE
        return result

    # ---- 4. 边界 ----
    boundaries = spec.boundaries
    best_metrics = sweep.best.metrics
    oos_metrics = (sweep.oos.metrics if sweep.oos and sweep.oos.ok else {})
    result.metrics = {f"in_sample.{name}": metric for name, metric in best_metrics.items()}
    result.metrics.update({f"out_sample.{name}": metric for name, metric in oos_metrics.items()})
    if not oos_metrics:
        # 样本外根本没跑成（区间无数据/回测失败）：必须留下"不可计算"的痕迹，
        # 而不是让 out_sample.* 从报告里消失——消失的指标会被误读成"没这项要求"。
        reason = (sweep.oos.error if sweep.oos and sweep.oos.error else "样本外未产出绩效指标")
        for name in ("cumulative_return", "max_drawdown", "closed_trades"):
            result.metrics[f"out_sample.{name}"] = unavailable(name, f"样本外不可计算：{reason}")

    checks: list[BoundCheck] = [
        check_bound(
            best_metrics.get("cumulative_return", Metric("cumulative_return", None, status="not_computable", reason="未产出")),
            threshold=boundaries.min_in_sample_return,
            name="样本内累计收益",
            direction=">=",
        ),
        check_bound(
            best_metrics.get("max_drawdown", Metric("max_drawdown", None, status="not_computable", reason="未产出")),
            threshold=boundaries.max_in_sample_drawdown,
            name="样本内最大回撤",
            direction="<=",
        ),
        check_bound(
            best_metrics.get("closed_trades", Metric("closed_trades", None, status="not_computable", reason="未产出")),
            threshold=float(boundaries.min_closed_trades),
            name="样本内完成交易笔数",
            direction=">=",
        ),
        check_bound(
            oos_metrics.get("cumulative_return", Metric("cumulative_return", None, status="not_computable", reason="样本外未评估")),
            threshold=boundaries.min_out_sample_return,
            name="样本外累计收益",
            direction=">=",
        ),
        check_bound(
            oos_metrics.get("max_drawdown", Metric("max_drawdown", None, status="not_computable", reason="样本外未评估")),
            threshold=boundaries.max_out_sample_drawdown,
            name="样本外最大回撤",
            direction="<=",
        ),
    ]
    checks.append(_degradation_check(sweep, boundaries.max_oos_degradation))
    result.bound_checks = checks

    outcome, reasons = summarise(checks)
    result.stages.append(
        Stage("bounds", outcome if outcome != "pass" else "pass", "；".join(reasons) or "全部边界通过")
    )

    # ---- 5. 不变量：样本外只评一次 ----
    if sweep.oos_evaluations != 1:
        result.notes.append(f"样本外评估次数为 {sweep.oos_evaluations}（应为 1）：判定不可信")
        result.verdict = INCONCLUSIVE
    elif outcome == "fail":
        result.verdict = REJECTED_BOUNDS
    elif outcome == "undetermined":
        result.verdict = INCONCLUSIVE
    else:
        result.verdict = PASS

    result.honesty = [
        metric.to_dict()
        for name, metric in result.metrics.items()
        if not metric.available
    ]
    if result.honesty:
        result.notes.append(
            f"{len(result.honesty)} 个指标不可计算，已如实标注（未填 0）："
            + "、".join(sorted({item['name'] for item in result.honesty}))
        )
    return result


def _first_params(spec: HypothesisSpec) -> dict[str, Any]:
    combos = spec.grid_points()
    return combos[0] if combos else {}


def _degradation_check(sweep: SweepResult, max_degradation: float) -> BoundCheck:
    """样本外相对样本内的收益衰减。样本内收益 ≤0 时衰减无定义（不能拿负数做分母）。"""
    is_value = None
    oos_value = None
    if sweep.best:
        metric = sweep.best.metrics.get("cumulative_return")
        if metric and metric.available:
            is_value = metric.value
    if sweep.oos and sweep.oos.ok:
        metric = sweep.oos.metrics.get("cumulative_return")
        if metric and metric.available:
            oos_value = metric.value
    if is_value is None or oos_value is None:
        return BoundCheck(
            name="样本外收益衰减",
            actual=None,
            threshold=max_degradation,
            direction=">=",
            passed=None,
            reason="样本内或样本外累计收益不可计算，衰减比无定义",
        )
    if is_value <= 0:
        return BoundCheck(
            name="样本外收益衰减",
            actual=None,
            threshold=max_degradation,
            direction=">=",
            passed=None,
            reason=f"样本内收益为 {is_value:.4f}（≤0），衰减比无定义——应直接由收益边界否决",
        )
    kept = oos_value / is_value
    return BoundCheck(
        name="样本外收益保留率",
        actual=kept,
        threshold=1 - max_degradation,
        direction=">=",
        passed=bool(kept >= 1 - max_degradation),
    )


def _sweep_summary(sweep: SweepResult) -> dict[str, Any]:
    payload = sweep.to_dict()
    payload.pop("points", None)
    payload["points_total"] = len(sweep.points)
    payload["failed_detail"] = [
        {"params": point.params, "error": point.error} for point in sweep.points if not point.ok
    ][:20]
    return payload
