"""L3 Walk-Forward 闸门：滚动窗口下的样本外一致性。

计划依据：《Vibe-Trading 调研结论落地推进计划 v1.1》§6.1 表 7。
表 7 给 L3 的判据只有一句：**「样本外表现与样本内系统性背离」一票否决**。
这句话不落成算式，每次都得靠感觉判断；本模块把它拆成可算的判据，并保证三件事：

1. **选参与打分严格分离**。每一折先用训练窗跑完整网格选参（只按训练窗指标），
   再用该参数在紧随其后的测试窗跑**一次**。没有任何「用测试窗结果回头改参数」的路径。
2. **测试窗不重叠**（默认 `step_days == test_days`）。折数若来自重叠窗口，
   同一段行情会被重复计入，折数就不再是稳健性证据——这种情况本模块**不允许判 PASSED**。
3. **不把折收益拼成连续净值**。每折资金重置、起始空仓，折间没有资金连续性；
   把折收益复利相乘得到的数不是任何真实账户的收益。项目 R12 的教训就是这一条。

与 L1a/L1b/L2 的关系
-------------------
L3 是**派生闸门**：它不新增数据源，直接在 L1a 的契约上按折切窗，复用
:func:`deepquant.gates.sweep.run_sweep` 的选参逻辑。所以 L3 的"样本外"只在
**参数选择**意义上是样本外——整段历史在 L1a/L2 阶段早被看过。报告里必须带这句，
否则 L3 的数字会被读成"未见过的未来数据"。
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

import pandas as pd

from deepquant.gates.data_gate import check_data, check_frame
from deepquant.gates.metrics import Metric, unavailable
from deepquant.gates.spec import HypothesisSpec, SpecError, Window, parse_spec
from deepquant.gates.sweep import PointOutcome, SweepResult, default_strategy_factory, run_sweep

# --------------------------------------------------------------------------- #
# 一、终态枚举
# --------------------------------------------------------------------------- #
VERDICT_PASSED = "L3_PASSED"
#: 样本内为正、样本外为负的系统性背离（表 7 的一票否决项）。
VERDICT_DIVERGENCE = "L3_REJECTED_SYSTEMATIC_DIVERGENCE"
#: 折间一致性不达标（正收益折占比或中位 WFE 不足），但未构成"系统性背离"。
VERDICT_INCONSISTENCY = "L3_REJECTED_INCONSISTENT_FOLDS"
#: 窗口本身不成立：历史长度不够切出最小折数。
VERDICT_WINDOW = "L3_REJECTED_WINDOW"
#: 数据门禁未通过（含 Walk-Forward 实际跨度上的覆盖不足）。
VERDICT_DATA = "L3_REJECTED_DATA"
VERDICT_INCONCLUSIVE = "L3_INCONCLUSIVE"
VERDICT_SPEC = "L3_REJECTED_SPEC"
#: 前序闸门未放行：本次结果只用于量化，不构成放行结论。
VERDICT_PREREQUISITE = "L3_BLOCKED_PREREQUISITE"

ALL_VERDICTS: tuple[str, ...] = (
    VERDICT_PASSED,
    VERDICT_DIVERGENCE,
    VERDICT_INCONSISTENCY,
    VERDICT_WINDOW,
    VERDICT_DATA,
    VERDICT_INCONCLUSIVE,
    VERDICT_SPEC,
    VERDICT_PREREQUISITE,
)


class WalkForwardSpecError(ValueError):
    """Walk-Forward 参数不自洽。"""


# --------------------------------------------------------------------------- #
# 二、窗口契约
# --------------------------------------------------------------------------- #
MODE_ROLLING = "rolling"
MODE_ANCHORED = "anchored"


@dataclass(frozen=True)
class WalkForwardSpec:
    """切窗规则与判定阈值。所有阈值在跑之前写定，不因结果调整。

    长度单位统一用**交易日根数**而不是自然日或月份：日历长度会因停牌、长假、
    数据缺口而漂移，导致同一条规则在不同标的上切出不同长度的窗口。
    """

    # ---------------- 切窗 ---------------- #
    #: 训练窗长度（交易日）。
    train_days: int = 504
    #: 测试窗长度（交易日）。
    test_days: int = 126
    #: 相邻折的起点间隔（交易日）。小于 test_days 会产生重叠的测试窗。
    step_days: int = 126
    #: rolling = 训练窗固定长度滚动；anchored = 训练窗起点固定、长度递增。
    mode: str = MODE_ROLLING
    #: 预热长度：为 0 时按「参数网格里最大的整数 × warmup_multiple」自动推算。
    warmup_days: int = 0
    warmup_multiple: int = 2
    #: 折尾不足这么多个交易日就丢弃（避免用半截窗口下结论）。
    min_test_days: int = 20
    #: Walk-Forward 的实际跨度。留空时取契约的 [in_sample.start, out_sample.end]。
    #: 跨度必须显式可查：数据门禁与折覆盖都要以它为准，而不是以 in_sample 为准
    #: （in_sample 只是契约声明的一段，L3 走的跨度可能更长）。
    span_start: str = ""
    span_end: str = ""
    #: 阈值取值依据（写进报告，避免"这个数是怎么定的"事后无从追溯）。
    basis: str = ""

    # ---------------- 判定阈值 ---------------- #
    #: 少于这么多折，一律不可判定（2–3 折不能宣称"稳健"）。
    min_folds: int = 6
    #: 测试窗正收益折占比下限。
    min_positive_fold_ratio: float = 0.60
    #: 中位 Walk-Forward Efficiency（测试收益 / 训练收益）下限。
    min_median_wfe: float = 0.30
    #: 「训练正、测试负」折占比上限，超过即视为系统性背离。
    max_divergent_fold_ratio: float = 0.50
    #: 判定"样本内普遍为正"的下限：训练窗正收益折占比达到它，才谈得上"背离"。
    min_train_positive_ratio: float = 0.60

    def __post_init__(self) -> None:
        if self.mode not in (MODE_ROLLING, MODE_ANCHORED):
            raise WalkForwardSpecError(f"mode 只能是 rolling / anchored，收到 {self.mode!r}")
        for name in ("train_days", "test_days", "step_days", "min_test_days"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 1:
                raise WalkForwardSpecError(f"{name} 必须是正整数，收到 {value!r}")
        if self.test_days < self.min_test_days:
            raise WalkForwardSpecError(
                f"test_days({self.test_days}) < min_test_days({self.min_test_days})：窗口自相矛盾"
            )
        if self.train_days < 2:
            raise WalkForwardSpecError(f"train_days 至少 2，收到 {self.train_days}")
        if self.warmup_days < 0:
            raise WalkForwardSpecError(f"warmup_days 不能为负，收到 {self.warmup_days}")
        if self.warmup_multiple < 1:
            raise WalkForwardSpecError(f"warmup_multiple 至少 1，收到 {self.warmup_multiple}")
        if self.min_folds < 1:
            raise WalkForwardSpecError(f"min_folds 至少 1，收到 {self.min_folds}")
        for name in (
            "min_positive_fold_ratio",
            "max_divergent_fold_ratio",
            "min_train_positive_ratio",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise WalkForwardSpecError(f"{name} 必须在 [0,1]，收到 {value}")
        if not -1.0 <= self.min_median_wfe <= 3.0:
            raise WalkForwardSpecError(f"min_median_wfe 超出合理区间，收到 {self.min_median_wfe}")
        for name in ("span_start", "span_end"):
            value = getattr(self, name)
            if value and not isinstance(value, str):
                raise WalkForwardSpecError(f"{name} 必须是 'YYYY-MM-DD' 字符串，收到 {value!r}")
        if self.span_start and self.span_end:
            if pd.Timestamp(self.span_start) >= pd.Timestamp(self.span_end):
                raise WalkForwardSpecError(
                    f"跨度非法：span_start({self.span_start}) ≥ span_end({self.span_end})"
                )

    @property
    def overlaps(self) -> bool:
        """测试窗之间是否重叠（由 step < test 决定）。"""
        return self.step_days < self.test_days

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,
            "mode": self.mode,
            "warmup_days": self.warmup_days,
            "warmup_multiple": self.warmup_multiple,
            "min_test_days": self.min_test_days,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "basis": self.basis,
            "min_folds": self.min_folds,
            "min_positive_fold_ratio": self.min_positive_fold_ratio,
            "min_median_wfe": self.min_median_wfe,
            "max_divergent_fold_ratio": self.max_divergent_fold_ratio,
            "min_train_positive_ratio": self.min_train_positive_ratio,
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_walk_forward(payload: Mapping[str, Any] | None) -> WalkForwardSpec:
    if payload is None:
        return WalkForwardSpec()
    if not isinstance(payload, Mapping):
        raise WalkForwardSpecError("walk_forward 段必须是对象")
    known = set(WalkForwardSpec().to_dict())
    unknown = sorted(set(payload) - known)
    if unknown:
        # 拼错的键会被静默忽略，让阈值看起来"生效了"其实没生效——必须拒绝
        raise WalkForwardSpecError(f"walk_forward 段含未知键：{unknown}")
    kwargs = {key: payload[key] for key in payload}
    for name in ("train_days", "test_days", "step_days", "warmup_days", "min_test_days", "min_folds"):
        if name in kwargs:
            kwargs[name] = int(kwargs[name])
    for name in ("min_positive_fold_ratio", "min_median_wfe", "max_divergent_fold_ratio", "min_train_positive_ratio"):
        if name in kwargs:
            kwargs[name] = float(kwargs[name])
    if "mode" in kwargs:
        kwargs["mode"] = str(kwargs["mode"])
    return WalkForwardSpec(**kwargs)


# --------------------------------------------------------------------------- #
# 三、折
# --------------------------------------------------------------------------- #
@dataclass
class Fold:
    """一折：训练窗选参，测试窗只评一次。"""

    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_days: int
    test_days: int
    #: 训练窗用到的预热起点（仅用于说明窗口边界，不参与收益统计）。
    warmup_start: pd.Timestamp | None = None
    best_params: dict[str, Any] = field(default_factory=dict)
    train_return: float | None = None
    test_return: float | None = None
    test_max_drawdown: float | None = None
    test_closed_trades: int | None = None
    train_closed_trades: int | None = None
    #: Walk-Forward Efficiency = 测试收益 / 训练收益（训练收益 <= 0 时不可计算）。
    wfe: float | None = None
    wfe_reason: str = ""
    params_changed: bool | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.train_return is not None and self.test_return is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold": self.index,
            "train": {
                "start": _fmt(self.train_start),
                "end": _fmt(self.train_end),
                "trading_days": self.train_days,
            },
            "test": {
                "start": _fmt(self.test_start),
                "end": _fmt(self.test_end),
                "trading_days": self.test_days,
            },
            "warmup_start": _fmt(self.warmup_start),
            "best_params": self.best_params,
            "train_return": self.train_return,
            "test_return": self.test_return,
            "test_max_drawdown": self.test_max_drawdown,
            "test_closed_trades": self.test_closed_trades,
            "train_closed_trades": self.train_closed_trades,
            "wfe": self.wfe,
            "wfe_reason": self.wfe_reason,
            "params_changed": self.params_changed,
            "error": self.error,
        }


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    return str(pd.Timestamp(value).date())


def _max_grid_int(param_grid: Mapping[str, Sequence[Any]]) -> int:
    """网格里最大的整数参数值（用于推算预热长度）。"""
    best = 0
    for values in param_grid.values():
        for value in values:
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and not pd.isna(value):
                best = max(best, int(abs(value)))
    return best


def resolve_warmup(spec: WalkForwardSpec, hypothesis: HypothesisSpec) -> int:
    """预热长度。显式给了就用显式值，否则按网格最大参数 × 倍数推算。"""
    if spec.warmup_days:
        return spec.warmup_days
    return _max_grid_int(hypothesis.param_grid) * spec.warmup_multiple


def build_folds(
    days: Sequence[pd.Timestamp],
    spec: WalkForwardSpec,
) -> tuple[list[Fold], dict[str, Any]]:
    """按交易日序列切折。

    训练窗与测试窗**严格相邻且不共用任何一天**：训练窗末根之后的第一根才是测试窗首根。
    边界上一根共用会让训练窗最后一天同时被当成样本外第一天，这是最隐蔽的泄漏。
    """
    index = pd.DatetimeIndex(days)
    if index.has_duplicates:
        raise WalkForwardSpecError("交易日序列存在重复日期")
    if not index.is_monotonic_increasing:
        raise WalkForwardSpecError("交易日序列必须升序")
    n = len(index)

    folds: list[Fold] = []
    cursor = 0
    previous_test_end = -1
    while True:
        if spec.mode == MODE_ANCHORED:
            # 锚定：训练窗**起点**固定，长度逐折增加 step_days
            train_start_idx = 0
            train_end_idx = cursor * spec.step_days + spec.train_days - 1
        else:
            # 滚动：训练窗长度固定，整窗逐折后移 step_days
            train_start_idx = cursor * spec.step_days
            train_end_idx = train_start_idx + spec.train_days - 1
        test_start_idx = train_end_idx + 1
        test_end_idx = test_start_idx + spec.test_days - 1
        if test_end_idx >= n:
            break
        if test_end_idx <= previous_test_end:
            # 守卫：测试窗不再前进说明切窗规则让指针原地踏步（anchored 首版就是
            # 这么死循环的——cursor 没参与窗口计算）。宁可报错也不能挂死。
            raise WalkForwardSpecError(
                f"第 {len(folds) + 1} 折的测试窗未推进（上一折止于索引 {previous_test_end}，"
                f"本折止于 {test_end_idx}）；切窗规则无法前进"
            )
        previous_test_end = test_end_idx
        folds.append(
            Fold(
                index=len(folds) + 1,
                train_start=index[train_start_idx],
                train_end=index[train_end_idx],
                test_start=index[test_start_idx],
                test_end=index[test_end_idx],
                train_days=train_end_idx - train_start_idx + 1,
                test_days=spec.test_days,
            )
        )
        cursor += 1

    # 折尾剩余不足 min_test_days：如实报出来，不悄悄吞掉
    tail_days = n - (index.get_loc(folds[-1].test_end) + 1) if folds else n
    meta = {
        "trading_days_total": n,
        "folds": len(folds),
        "unused_tail_days": int(tail_days),
        "first_day": _fmt(index[0]) if n else None,
        "last_day": _fmt(index[-1]) if n else None,
        "span_start": _fmt(folds[0].train_start) if folds else None,
        "span_end": _fmt(folds[-1].test_end) if folds else None,
    }
    return folds, meta


def overlap_report(folds: Sequence[Fold]) -> dict[str, Any]:
    """检查测试窗是否互不重叠。重叠 ⇒ 折数不是独立证据。"""
    overlapping: list[dict[str, Any]] = []
    for left, right in zip(folds, folds[1:]):
        if right.test_start <= left.test_end:
            overlap_days = int(
                len(pd.date_range(left.test_start, min(left.test_end, right.test_end), freq="B"))
            )
            overlapping.append(
                {
                    "left": left.index,
                    "right": right.index,
                    "left_test_end": _fmt(left.test_end),
                    "right_test_start": _fmt(right.test_start),
                    "approx_overlap_trading_days": overlap_days,
                }
            )
    return {
        "overlap_pairs": len(overlapping),
        "details": overlapping,
        "independent": not overlapping,
    }


# --------------------------------------------------------------------------- #
# 四、结果
# --------------------------------------------------------------------------- #
@dataclass
class L3Result:
    hypothesis_id: str
    verdict: str
    spec: dict[str, Any] = field(default_factory=dict)
    window_meta: dict[str, Any] = field(default_factory=dict)
    folds: list[Fold] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    overlap: dict[str, Any] = field(default_factory=dict)
    param_selection: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 前置门禁覆盖前的判定链结论。被 BLOCKED_PREREQUISITE 覆盖时必须留下它——
    #: 否则读者只看到"前置未满足"，不知道本闸门自己会怎么判。
    preliminary_verdict: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "verdict": self.verdict,
            "spec": self.spec,
            "window_meta": self.window_meta,
            "summary": self.summary,
            "checks": self.checks,
            "overlap": self.overlap,
            "param_selection": self.param_selection,
            "stages": self.stages,
            "folds": [fold.to_dict() for fold in self.folds],
            "limitations": self.limitations,
            "notes": self.notes,
            "preliminary_verdict": self.preliminary_verdict,
        }


def _metric_value(point: PointOutcome | None, name: str) -> Metric | None:
    if point is None:
        return None
    return point.metrics.get(name)


def _describe(checks: Sequence[dict[str, Any]]) -> str:
    if not checks:
        return "无检查项"
    text = "；".join(
        f"{check['name']}={'PASS' if check['passed'] else ('不可判定' if check['passed'] is None else 'FAIL')}"
        for check in checks
    )
    return text


# --------------------------------------------------------------------------- #
# 五、主流程
# --------------------------------------------------------------------------- #
def run_l3(
    spec_or_payload: HypothesisSpec | Mapping[str, Any] | None,
    *,
    data: pd.DataFrame | None = None,
    factory: Any = default_strategy_factory,
    walk_forward: WalkForwardSpec | Mapping[str, Any] | None = None,
    l1_verdict: str | None = None,
    prior_verdicts: Mapping[str, str] | None = None,
) -> L3Result:
    """执行 L3 Walk-Forward。

    ``spec_or_payload`` 为 L1a 契约（含 ``walk_forward`` 段）。``data`` 为空时按契约
    的数据源加载。``prior_verdicts`` 用于逐级闸门：任一前置终态不是 PASSED 时，
    结果仍会算完，但终态降为 ``L3_BLOCKED_PREREQUISITE``——**算出来的数字不构成放行依据**。
    """
    result = L3Result(hypothesis_id="", verdict=VERDICT_SPEC)

    # ---- 0. 契约 ----
    try:
        if isinstance(spec_or_payload, HypothesisSpec):
            hypothesis = spec_or_payload
            raw_wf = spec_or_payload.walk_forward
        elif isinstance(spec_or_payload, Mapping):
            raw_wf = spec_or_payload.get("walk_forward")
            hypothesis = parse_spec(spec_or_payload)
        else:
            raise SpecError(f"契约必须是 HypothesisSpec 或字典，收到 {type(spec_or_payload)!r}")
        if walk_forward is None:
            wf_spec = parse_walk_forward(raw_wf)
        elif isinstance(walk_forward, WalkForwardSpec):
            wf_spec = walk_forward
        else:
            wf_spec = parse_walk_forward(walk_forward)
    except (SpecError, WalkForwardSpecError) as exc:
        result.stages.append({"name": "spec", "status": "fail", "detail": str(exc)})
        result.limitations.append(f"契约不成立：{exc}")
        return result

    result.hypothesis_id = hypothesis.hypothesis_id
    result.spec = {
        "hypothesis": hypothesis.to_dict(),
        "walk_forward": wf_spec.to_dict(),
        "digest": wf_spec.digest,
    }
    result.stages.append(
        {"name": "spec", "status": "pass", "detail": f"切窗规则摘要 {wf_spec.digest[:16]}…"}
    )

    # ---- 1. 数据 ----
    # 门禁窗口用 **Walk-Forward 的实际跨度**，不用契约的 in_sample：
    # L3 会走过比 in_sample 更长的历史，只校验 in_sample 等于让后半段裸奔。
    span = Window(
        start=wf_spec.span_start or hypothesis.in_sample.start,
        end=wf_spec.span_end or hypothesis.out_sample.end,
    )
    result.window_meta["declared_span"] = {"start": span.start, "end": span.end}
    try:
        if data is not None:
            verdict = check_frame(data, window=span, label="调用方传入的内存数据")
        else:
            verdict = check_data(hypothesis.data, span)
    except Exception as exc:  # noqa: BLE001
        result.stages.append({"name": "data", "status": "fail", "detail": f"数据门禁异常：{exc}"})
        result.limitations.append(f"数据门禁异常：{type(exc).__name__}: {exc}")
        result.verdict = VERDICT_DATA
        return result

    frame = verdict.frame
    if not verdict.ok or frame is None:
        detail = "；".join(f"{item.code}: {item.detail}" for item in verdict.blocking_failures)
        result.stages.append({"name": "data", "status": "fail", "detail": detail or "数据为空"})
        result.verdict = VERDICT_DATA
        result.limitations.append(f"数据门禁未通过：{detail or '数据为空'}")
        return result
    from deepquant.backtest.engine import normalize_daily_data

    frame = normalize_daily_data(frame)
    rows_before = len(frame)
    frame = frame.loc[(frame.index >= pd.Timestamp(span.start)) & (frame.index <= pd.Timestamp(span.end))]
    if frame.empty:
        result.stages.append(
            {"name": "data", "status": "fail", "detail": f"跨度 {span.start}~{span.end} 内没有行情"}
        )
        result.limitations.append("跨度内没有可用行情，未做任何评估")
        result.verdict = VERDICT_DATA
        return result
    result.stages.append(
        {
            "name": "data",
            "status": "pass",
            "detail": (
                f"跨度内 {len(frame)} 行（全量 {rows_before} 行），"
                f"{_fmt(frame.index[0])} → {_fmt(frame.index[-1])}"
            ),
        }
    )

    # ---- 2. 切窗 ----
    try:
        folds, meta = build_folds(frame.index, wf_spec)
    except WalkForwardSpecError as exc:
        result.stages.append({"name": "windows", "status": "fail", "detail": str(exc)})
        result.limitations.append(f"切窗失败：{exc}")
        result.verdict = VERDICT_WINDOW
        return result

    warmup = resolve_warmup(wf_spec, hypothesis)
    result.window_meta = {
        **meta,
        "warmup_days": warmup,
        "mode": wf_spec.mode,
        "train_days": wf_spec.train_days,
        "test_days": wf_spec.test_days,
        "step_days": wf_spec.step_days,
    }
    result.overlap = overlap_report(folds)
    if not folds:
        result.stages.append(
            {
                "name": "windows",
                "status": "fail",
                "detail": (
                    f"历史仅 {meta['trading_days_total']} 个交易日，"
                    f"按 train={wf_spec.train_days}/test={wf_spec.test_days} 切不出任何完整折"
                ),
            }
        )
        result.limitations.append("窗口不足：无法切出完整折，未做任何评估")
        result.verdict = VERDICT_WINDOW
        return result

    result.stages.append(
        {
            "name": "windows",
            "status": "pass" if result.overlap["independent"] else "undetermined",
            "detail": (
                f"{len(folds)} 折，{_fmt(folds[0].train_start)} → {_fmt(folds[-1].test_end)}；"
                f"测试窗{'互不重叠' if result.overlap['independent'] else '存在重叠'}"
            ),
        }
    )

    # ---- 3. 逐折执行 ----
    previous_params: dict[str, Any] | None = None
    for fold in folds:
        assigned = frame.index
        train_start_pos = int(assigned.get_loc(fold.train_start))
        warm_pos = max(0, train_start_pos - warmup)
        fold.warmup_start = assigned[warm_pos]

        # 用 replace 派生折内契约：选参只看训练窗，样本外只在测试窗评一次
        fold_spec = replace(
            hypothesis,
            in_sample=Window(start=_fmt(fold.train_start) or "", end=_fmt(fold.train_end) or ""),
            out_sample=Window(start=_fmt(fold.test_start) or "", end=_fmt(fold.test_end) or ""),
        )
        try:
            sweep = run_sweep(fold_spec, frame, factory=factory)
        except Exception as exc:  # noqa: BLE001 - 单折失败不能拖垮整轮，但必须记下来
            fold.error = f"网格扫描异常：{type(exc).__name__}: {exc}"
            result.folds.append(fold)
            continue

        if sweep.best is None:
            fold.error = "训练窗内没有任何参数点产出可用指标"
            result.folds.append(fold)
            continue

        fold.best_params = dict(sweep.best.params)
        fold.train_return = _scalar(_metric_value(sweep.best, "cumulative_return"))
        fold.train_closed_trades = _as_int(_metric_value(sweep.best, "closed_trades"))
        fold.test_return = _scalar(_metric_value(sweep.oos, "cumulative_return"))
        fold.test_max_drawdown = _scalar(_metric_value(sweep.oos, "max_drawdown"))
        fold.test_closed_trades = _as_int(_metric_value(sweep.oos, "closed_trades"))
        if sweep.oos is None or not sweep.oos.ok:
            fold.error = (
                f"测试窗评估失败：{sweep.oos.error}" if sweep.oos is not None else "测试窗未评估"
            )

        if fold.train_return is None or fold.train_return <= 0:
            fold.wfe = None
            fold.wfe_reason = (
                f"训练收益 {fold.train_return} 非正，测试/训练之比没有经济含义"
                if fold.train_return is not None
                else "训练收益不可计算"
            )
        elif fold.test_return is None:
            fold.wfe = None
            fold.wfe_reason = "测试收益不可计算"
        else:
            fold.wfe = fold.test_return / fold.train_return

        if previous_params is not None:
            fold.params_changed = dict(fold.best_params) != dict(previous_params)
        previous_params = dict(fold.best_params)
        result.folds.append(fold)

    usable = [fold for fold in result.folds if fold.ok]
    failed = [fold for fold in result.folds if not fold.ok]

    # ---- 4. 汇总 ----
    result.summary = _summarise(result.folds, usable, wf_spec)
    result.param_selection = _param_stats(usable)

    # ---- 5. 判定 ----
    verdict, checks = _decide(result, wf_spec, usable, failed)
    result.checks = checks
    result.verdict = verdict
    result.stages.append(
        {
            "name": "decide",
            "status": "pass" if verdict == VERDICT_PASSED else "fail",
            "detail": _describe(checks),
        }
    )

    # ---- 6. 前置与限制 ----
    gating = dict(prior_verdicts or {})
    if l1_verdict is not None:
        gating.setdefault("L1a", l1_verdict)
    blocked = {name: value for name, value in gating.items() if value and value != _passed_name(name)}
    if blocked:
        result.preliminary_verdict = result.verdict
        result.verdict = VERDICT_PREREQUISITE
        detail = "、".join(f"{name}={value}" for name, value in sorted(blocked.items()))
        result.stages.append({"name": "prerequisite", "status": "fail", "detail": detail})
        result.limitations.append(
            f"前置未满足：{detail}。本次 Walk-Forward 仅用于量化折间一致性，**不构成放行结论**。"
        )
        result.limitations.append(
            f"若**不计**前置，本闸门判定链给出的终态是 `{result.preliminary_verdict}`"
            "（该结论同样不构成放行依据，此处只用于说明本闸门自身的判断）。"
        )

    result.limitations.extend(_standard_limitations(wf_spec, result, hypothesis))
    if failed:
        result.notes.append(
            f"{len(failed)} 折未能完成：" + "；".join(f"#{fold.index} {fold.error}" for fold in failed)
        )
    return result


def _passed_name(name: str) -> str:
    return {"L1a": "L1A_PASSED", "L1b": "L1B_PASSED", "L2": "L2_PASSED"}.get(name, f"{name}_PASSED")


def _scalar(metric: Metric | None) -> float | None:
    if metric is None or not metric.available or metric.value is None:
        return None
    return float(metric.value)


def _as_int(metric: Metric | None) -> int | None:
    value = _scalar(metric)
    return None if value is None else int(round(value))


def _summarise(folds: Sequence[Fold], usable: Sequence[Fold], wf_spec: WalkForwardSpec) -> dict[str, Any]:
    """折级汇总。**不构造连续净值**——见 limitations 的第一条。"""
    test_returns = [fold.test_return for fold in usable if fold.test_return is not None]
    train_returns = [fold.train_return for fold in usable if fold.train_return is not None]
    wfes = [fold.wfe for fold in usable if fold.wfe is not None]
    drawdowns = [fold.test_max_drawdown for fold in usable if fold.test_max_drawdown is not None]

    summary: dict[str, Any] = {
        "folds_total": len(folds),
        "folds_usable": len(usable),
        "folds_failed": len(folds) - len(usable),
    }
    if not test_returns:
        summary["note"] = "没有任何可用折，全部汇总指标不可计算"
        return summary

    summary.update(
        {
            "test_return_mean": statistics.fmean(test_returns),
            "test_return_median": statistics.median(test_returns),
            "test_return_min": min(test_returns),
            "test_return_max": max(test_returns),
            "test_return_std": (statistics.stdev(test_returns) if len(test_returns) > 1 else None),
            "test_positive_folds": sum(1 for value in test_returns if value > 0),
            "test_positive_fold_ratio": sum(1 for value in test_returns if value > 0) / len(test_returns),
            "train_return_mean": statistics.fmean(train_returns) if train_returns else None,
            "train_positive_folds": sum(1 for value in train_returns if value > 0),
            "train_positive_fold_ratio": (
                sum(1 for value in train_returns if value > 0) / len(train_returns)
                if train_returns
                else None
            ),
            # 「训练正、测试负」——表 7 一票否决项的直接读数
            "divergent_folds": sum(
                1
                for fold in usable
                if fold.train_return is not None
                and fold.test_return is not None
                and fold.train_return > 0
                and fold.test_return < 0
            ),
            "median_wfe": statistics.median(wfes) if wfes else None,
            "wfe_samples": len(wfes),
            "test_drawdown_median": statistics.median(drawdowns) if drawdowns else None,
            "test_drawdown_max": max(drawdowns) if drawdowns else None,
            # 粗参照，**不可复利**：折间资金重置、测试窗不保证连续
            "naive_sum_of_fold_returns": sum(test_returns),
        }
    )
    summary["divergent_fold_ratio"] = summary["divergent_folds"] / len(usable)
    return summary


def summarise_folds(
    folds: Sequence[Fold], usable: Sequence[Fold], wf_spec: WalkForwardSpec
) -> dict[str, Any]:
    """折级汇总的对外入口（L3 与 L3p 共用）。"""
    return _summarise(folds, usable, wf_spec)


def param_stats(usable: Sequence[Fold]) -> dict[str, Any]:
    """参数选择稳定性的对外入口（L3 与 L3p 共用）。"""
    return _param_stats(usable)


def _param_stats(usable: Sequence[Fold]) -> dict[str, Any]:
    if not usable:
        return {"note": "没有可用折，参数选择统计不可计算"}
    keys = sorted({json.dumps(fold.best_params, sort_keys=True) for fold in usable})
    counts: dict[str, int] = {}
    for fold in usable:
        key = json.dumps(fold.best_params, sort_keys=True)
        counts[key] = counts.get(key, 0) + 1
    top_key, top_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    changes = [fold.params_changed for fold in usable if fold.params_changed is not None]
    return {
        "distinct_param_sets": len(keys),
        "most_common": json.loads(top_key),
        "most_common_count": top_count,
        "param_stability": top_count / len(usable),
        "changes": sum(1 for item in changes if item),
        "comparisons": len(changes),
        "sequence": [
            {"fold": fold.index, "params": fold.best_params} for fold in usable
        ],
    }


# --------------------------------------------------------------------------- #
# 六、判定链
# --------------------------------------------------------------------------- #
def _check(name: str, passed: bool | None, actual: Any, threshold: Any, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "actual": actual,
        "threshold": threshold,
        "detail": detail,
    }


def decide_from_summary(
    summary: Mapping[str, Any],
    overlap: Mapping[str, Any],
    wf_spec: WalkForwardSpec,
    *,
    usable_folds: int,
    failed_folds: int,
) -> tuple[str, list[dict[str, Any]]]:
    """判定链本体：只吃「折级汇总」，不关心折是怎么跑出来的。

    单标的（L3）与组合级（L3p）必须共用这一套判据——各写一套就会漂移，
    最后变成"哪个闸门宽松就引用哪个"。所以这里只接收汇总数字与窗口重叠情况。
    """
    checks: list[dict[str, Any]] = []

    # 1) 可用折数
    enough = usable_folds >= wf_spec.min_folds
    checks.append(
        _check(
            "可用折数达标",
            enough,
            usable_folds,
            wf_spec.min_folds,
            f"完成 {usable_folds} 折（要求 ≥{wf_spec.min_folds}）；失败 {failed_folds} 折",
        )
    )
    if usable_folds == 0:
        return VERDICT_INCONCLUSIVE, checks

    # 2) 折的独立性
    checks.append(
        _check(
            "测试窗互不重叠",
            bool(overlap.get("independent")),
            overlap.get("overlap_pairs"),
            0,
            (
                "测试窗两两不重叠，折数可作为独立证据"
                if overlap.get("independent")
                else f"存在 {overlap.get('overlap_pairs')} 对重叠测试窗，折数不能作为稳健性证据"
            ),
        )
    )

    if not enough:
        # 折数不足 → 不可判定；但已算出的数字如实保留
        return VERDICT_INCONCLUSIVE, checks
    if not overlap.get("independent"):
        # 重叠窗口下的"通过"是假通过，直接降级
        return VERDICT_INCONCLUSIVE, checks

    train_positive_ratio = summary.get("train_positive_fold_ratio")
    test_mean = summary.get("test_return_mean")

    # 3) 系统性背离（一票否决）——两条独立读数，任一触发即否决
    train_universally_positive = (
        train_positive_ratio is not None and train_positive_ratio >= wf_spec.min_train_positive_ratio
    )
    sign_flip = bool(train_universally_positive and test_mean is not None and test_mean <= 0)
    checks.append(
        _check(
            "样本内外未出现符号背离",
            not sign_flip,
            {"train_positive_ratio": train_positive_ratio, "test_return_mean": test_mean},
            {"min_train_positive_ratio": wf_spec.min_train_positive_ratio, "test_return_mean": ">0"},
            (
                f"训练正收益折占比 {train_positive_ratio:.2%}、测试均值 {test_mean:+.4f}："
                "训练普遍为正而测试平均为负，属系统性背离"
                if sign_flip
                else f"训练正收益折占比 {_pct(train_positive_ratio)}、测试均值 {_sgn(test_mean)}"
            ),
        )
    )
    divergent_ratio = summary.get("divergent_fold_ratio")
    too_many_divergent = divergent_ratio is not None and divergent_ratio > wf_spec.max_divergent_fold_ratio
    checks.append(
        _check(
            "「训练正、测试负」折占比未超限",
            not too_many_divergent,
            divergent_ratio,
            wf_spec.max_divergent_fold_ratio,
            f"{summary.get('divergent_folds')}/{summary.get('folds_usable')} 折训练正而测试负"
            f"（占比 {_pct(divergent_ratio)}）",
        )
    )
    if sign_flip or too_many_divergent:
        return VERDICT_DIVERGENCE, checks

    # 4) 折间一致性
    positive_ratio = summary.get("test_positive_fold_ratio")
    positive_ok = positive_ratio is not None and positive_ratio >= wf_spec.min_positive_fold_ratio
    checks.append(
        _check(
            "正收益折占比达标",
            positive_ok,
            positive_ratio,
            wf_spec.min_positive_fold_ratio,
            f"{summary.get('test_positive_folds')}/{summary.get('folds_usable')} 折正收益"
            f"（占比 {_pct(positive_ratio)}）",
        )
    )
    if not positive_ok:
        return VERDICT_INCONSISTENCY, checks

    median_wfe = summary.get("median_wfe")
    if median_wfe is None:
        checks.append(
            _check(
                "中位 WFE 可计算",
                None,
                None,
                wf_spec.min_median_wfe,
                f"没有可算 WFE 的折（要求训练收益 > 0），样本 {summary.get('wfe_samples')} 个",
            )
        )
        return VERDICT_INCONCLUSIVE, checks
    wfe_ok = median_wfe >= wf_spec.min_median_wfe
    checks.append(
        _check(
            "中位 WFE 达标",
            wfe_ok,
            median_wfe,
            wf_spec.min_median_wfe,
            f"中位 WFE {median_wfe:+.4f}（{summary.get('wfe_samples')} 个可算折）",
        )
    )
    if not wfe_ok:
        return VERDICT_INCONSISTENCY, checks

    return VERDICT_PASSED, checks


def _decide(
    result: L3Result,
    wf_spec: WalkForwardSpec,
    usable: Sequence[Fold],
    failed: Sequence[Fold],
) -> tuple[str, list[dict[str, Any]]]:
    """``run_l3`` 的内部入口：把结果对象摊平成判定链的入参。"""
    return decide_from_summary(
        result.summary,
        result.overlap,
        wf_spec,
        usable_folds=len(usable),
        failed_folds=len(failed),
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _sgn(value: float | None) -> str:
    return "—" if value is None else f"{value:+.4f}"


# --------------------------------------------------------------------------- #
# 七、限制（每次运行都无条件写入）
# --------------------------------------------------------------------------- #
def _standard_limitations(
    wf_spec: WalkForwardSpec,
    result: L3Result,
    hypothesis: HypothesisSpec,
) -> list[str]:
    lines = [
        "**折收益不可拼成连续净值**：每折资金重置、从空仓起步，折间没有资金连续性；"
        "把折收益复利相乘得到的数不是任何真实账户的收益。`naive_sum_of_fold_returns` "
        "只是粗参照，不可用于谈「年化」或「累计收益」。",
        "**本闸门的「样本外」只针对参数选择**：整段历史在 L1a/L2 阶段已被看过，"
        "所以折不是真正的「未见过的未来数据」，它测的是**参数随时间的稳健性**，"
        "不构成前瞻样本外证据。",
        f"预热 {result.window_meta.get('warmup_days')} 个交易日：训练窗起点之前的数据仅用于"
        "让滚动指标有值，不计入任何收益；每折测试窗仍从空仓起步，"
        "因此「折首踏空/踏错」的成本由该折承担。",
    ]
    if wf_spec.mode == MODE_ROLLING:
        lines.append(
            f"训练窗固定 {wf_spec.train_days} 个交易日滚动：早期折可用的历史少于后期折，"
            "折间训练样本量不等（这是 rolling 的固有特征，不是数据问题）。"
        )
    else:
        lines.append(
            "训练窗起点固定、长度递增（anchored）：越晚的折训练样本越多，"
            "早期折的选参可能不稳，需结合参数稳定性一并读。"
        )
    if not hypothesis.walk_forward:
        lines.append("切窗规则来自默认值（契约未提供 walk_forward 段）：阈值未针对该假设校准。")
    if result.summary.get("folds_usable", 0) < 10:
        lines.append(
            f"仅 {result.summary.get('folds_usable')} 个可用折："
            "折级统计的功效很低，正收益占比的一个折差异就能改变结论。"
        )
    return lines


# --------------------------------------------------------------------------- #
# 八、报告
# --------------------------------------------------------------------------- #
def render_l3_markdown(result: L3Result, *, title: str = "") -> str:
    """把 L3 结果渲染成可交付报告。数字全部来自 result，不在此处重算。"""
    summary = result.summary
    meta = result.window_meta
    lines: list[str] = []
    lines.append(f"# L3 Walk-Forward：{title or result.hypothesis_id}")
    lines.append("")
    lines.append(f"- 假设编号：`{result.hypothesis_id}`")
    lines.append(f"- **终态：`{result.verdict}`**")
    if result.verdict == VERDICT_PREREQUISITE:
        lines.append("")
        lines.append(
            "> **前置未满足**：前序闸门未放行该假设。以下数字只用于量化折间一致性，"
            "**不构成放行结论**。"
        )
        if result.preliminary_verdict:
            lines.append(">")
            lines.append(
                f"> 若不计前置，本闸门判定链自身的结论是 "
                f"`{result.preliminary_verdict}`。"
            )
    lines.append("")

    lines.append("## 一、切窗规则")
    lines.append("")
    lines.append("| 参数 | 取值 |")
    lines.append("|---|---|")
    for key in ("mode", "train_days", "test_days", "step_days", "warmup_days"):
        lines.append(f"| {key} | {meta.get(key)} |")
    lines.append(f"| 交易日起止 | {meta.get('first_day')} → {meta.get('last_day')} |")
    lines.append(f"| 折覆盖区间 | {meta.get('span_start')} → {meta.get('span_end')} |")
    lines.append(f"| 未使用尾部 | {meta.get('unused_tail_days')} 个交易日 |")
    basis = (result.spec.get("walk_forward") or {}).get("basis")
    if basis:
        lines.append(f"| 阈值依据 | {basis} |")
    digest = (result.spec.get("walk_forward") or {}).get("digest") or ""
    lines.append(f"| 规则摘要 | `{digest[:16]}` |")
    lines.append("")

    if result.stages:
        lines.append("## 二、阶段状态")
        lines.append("")
        lines.append("| 阶段 | 状态 | 说明 |")
        lines.append("|---|---|---|")
        for stage in result.stages:
            lines.append(f"| {stage.get('name')} | {stage.get('status')} | {stage.get('detail')} |")
        lines.append("")

    lines.append("## 三、逐折明细")
    lines.append("")
    if result.folds:
        lines.append(
            "| 折 | 训练窗 | 测试窗 | 最优参数 | 训练收益 | 测试收益 | WFE | 测试回撤 | 成交 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for fold in result.folds:
            params = json.dumps(fold.best_params, ensure_ascii=False, sort_keys=True)
            changed = " *" if fold.params_changed else ""
            lines.append(
                f"| {fold.index} | {_fmt(fold.train_start)} → {_fmt(fold.train_end)} "
                f"| {_fmt(fold.test_start)} → {_fmt(fold.test_end)} "
                f"| `{params}`{changed} "
                f"| {_sgn(fold.train_return)} | {_sgn(fold.test_return)} "
                f"| {_sgn(fold.wfe)} | {_ratio(fold.test_max_drawdown)} "
                f"| {fold.test_closed_trades if fold.test_closed_trades is not None else '—'} |"
            )
        if any(fold.params_changed for fold in result.folds):
            lines.append("")
            lines.append("标 `*` 的折表示最优参数与上一折不同（参数漂移，不是缺陷，但需一并读）。")
    else:
        lines.append("无折被执行。")
    lines.append("")

    if summary.get("folds_usable"):
        lines.append("## 四、折级汇总（注意：不是连续账户）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 可用折 / 总折 | {summary.get('folds_usable')} / {summary.get('folds_total')} |")
        lines.append(f"| 测试收益均值 | {_sgn(summary.get('test_return_mean'))} |")
        lines.append(f"| 测试收益中位 | {_sgn(summary.get('test_return_median'))} |")
        lines.append(
            f"| 测试收益范围 | {_sgn(summary.get('test_return_min'))} ~ "
            f"{_sgn(summary.get('test_return_max'))} |"
        )
        lines.append(f"| 测试收益标准差 | {_ratio(summary.get('test_return_std'))} |")
        lines.append(
            f"| 正收益折占比 | {summary.get('test_positive_folds')}/{summary.get('folds_usable')}"
            f"（{_pct(summary.get('test_positive_fold_ratio'))}） |"
        )
        lines.append(f"| 训练收益均值 | {_sgn(summary.get('train_return_mean'))} |")
        lines.append(
            f"| 训练正收益折占比 | {_pct(summary.get('train_positive_fold_ratio'))} |"
        )
        lines.append(
            f"| 「训练正、测试负」折 | {summary.get('divergent_folds')} 折"
            f"（{_pct(summary.get('divergent_fold_ratio'))}） |"
        )
        lines.append(
            f"| 中位 WFE | {_sgn(summary.get('median_wfe'))}"
            f"（{summary.get('wfe_samples')} 个可算折） |"
        )
        lines.append(f"| 测试回撤中位 / 最大 | {_ratio(summary.get('test_drawdown_median'))} / "
                     f"{_ratio(summary.get('test_drawdown_max'))} |")
        lines.append(
            f"| 折收益算术和（**不可复利**） | {_sgn(summary.get('naive_sum_of_fold_returns'))} |"
        )
        lines.append("")

    if result.param_selection.get("distinct_param_sets") is not None:
        stats = result.param_selection
        lines.append("## 五、参数选择稳定性")
        lines.append("")
        lines.append(f"- 不同参数集：**{stats.get('distinct_param_sets')}** 个")
        lines.append(
            f"- 最常用参数：`{json.dumps(stats.get('most_common'), ensure_ascii=False, sort_keys=True)}`"
            f"，出现 {stats.get('most_common_count')} 次（稳定性 {_pct(stats.get('param_stability'))}）"
        )
        lines.append(
            f"- 参数变动次数：{stats.get('changes')} / {stats.get('comparisons')} 次相邻比较"
        )
        lines.append("")

    if result.checks:
        lines.append("## 六、判定明细")
        lines.append("")
        lines.append("| 检查项 | 结果 | 实际值 | 阈值 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for check in result.checks:
            mark = {True: "PASS", False: "FAIL", None: "不可判定"}[check.get("passed")]
            lines.append(
                f"| {check['name']} | {mark} | {_cell(check.get('actual'))} | "
                f"{_cell(check.get('threshold'))} | {check.get('detail')} |"
            )
        lines.append("")

    if result.limitations:
        lines.append("## 七、口径与限制")
        lines.append("")
        for item in result.limitations:
            lines.append(f"- {item}")
        lines.append("")
    if result.notes:
        lines.append("## 八、备注")
        lines.append("")
        for item in result.notes:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
