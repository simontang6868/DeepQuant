"""L1b：事件驱动精验闸门。

判定逻辑（按"便宜且能一票否决的先算"排序）
--------------------------------------------
1. 契约非法 → ``L1B_REJECTED_SPEC``
2. 数据门禁不过 → ``L1B_REJECTED_DATA``
3. 基线（无摩擦）收益 ≤ 0 → ``L1B_INCONCLUSIVE``
   —— L1a 本该把这个策略筛掉；到这里说明两级口径脱节，不能给"通过"
4. 精验后收益**符号反转** → ``L1B_REJECTED_SIGN_REVERSAL``（表 7 一票否决）
5. 市场性成交率低于阈值 → ``L1B_REJECTED_FILL_RATE``（表 7 一票否决）
6. 收益保留率低于 ``1 - max_return_decay`` → ``L1B_REJECTED_DECAY``
7. 成交价无法由滑点模型解释 → ``L1B_REJECTED_UNEXPLAINABLE_PRICE``
8. 否则 ``L1B_PASSED``

为什么基线必须与精验用**同一资金、同一区间**
--------------------------------------------
L1a 用 10 万跑出来的收益，放到 3000 万上精验，差异里混着"资金规模"和"成交摩擦"两件事，
无法归因。所以本闸门的基线档 = 「同资金 + 摩擦全关」，精验档 = 「同资金 + 摩擦全开」，
两者之差才是摩擦的净效应。

样本外不在本闸门评估（那是 L3 的职责）。L1b 只回答一件事：
**粗筛的收益，在考虑排队、部分成交、滑点与涨跌停之后，还剩多少。**
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import normalize_daily_data
from deepquant.eventloop.impact import SlippageMode, SlippageModel
from deepquant.eventloop.kernel import EventKernel, KernelResult, limits_from_trading_frame
from deepquant.eventloop.matching import MatchingParams
from deepquant.gates.data_gate import check_data, load_frame
from deepquant.gates.spec import HypothesisSpec

# ---------------- 终态 ---------------- #
VERDICT_PASSED = "L1B_PASSED"
VERDICT_SIGN_REVERSAL = "L1B_REJECTED_SIGN_REVERSAL"
VERDICT_FILL_RATE = "L1B_REJECTED_FILL_RATE"
VERDICT_DECAY = "L1B_REJECTED_DECAY"
VERDICT_UNEXPLAINABLE = "L1B_REJECTED_UNEXPLAINABLE_PRICE"
VERDICT_INCONCLUSIVE = "L1B_INCONCLUSIVE"
VERDICT_SPEC = "L1B_REJECTED_SPEC"
VERDICT_DATA = "L1B_REJECTED_DATA"
#: 前置未满足：L1a 没有放行该假设，因此本次精验不构成放行结论。
VERDICT_PREREQUISITE = "L1B_BLOCKED_PREREQUISITE"

ALL_VERDICTS = (
    VERDICT_PASSED,
    VERDICT_SIGN_REVERSAL,
    VERDICT_FILL_RATE,
    VERDICT_DECAY,
    VERDICT_UNEXPLAINABLE,
    VERDICT_INCONCLUSIVE,
    VERDICT_SPEC,
    VERDICT_DATA,
    VERDICT_PREREQUISITE,
)

#: 成交价偏离容差（元）：半个最小报价单位，吸收 tick 取整误差。
PRICE_TOLERANCE = 0.005


class L1bSpecError(ValueError):
    """精验契约非法。"""


@dataclass(frozen=True)
class ExecutionSpec:
    """精验的执行假设。每个字段都是**模型参数**，不是实测标定值。"""

    #: 单日参与率上限（占当日成交量）。
    participation_limit: float = 0.10
    #: 队列位置：0 = 队首，1 = 队尾（触价也拿不到成交）。
    queue_position: float = 0.5
    #: 滑点模型。
    slippage_mode: SlippageMode = SlippageMode.NONE
    slippage_bps: float = 0.0
    slippage_eta: float = 1.0
    #: 涨跌停比例。None 表示不判涨跌停（必须在 limitations 里声明）。
    price_limit_rate: float | None = 0.10
    #: 成交量单位换算（源数据是"手"则 100）。
    volume_multiplier: float = 100.0
    # ---------------- 判定阈值（跑前冻结） ---------------- #
    #: 允许的最大收益衰减（相对基线收益的比例）。0.5 = 收益可以掉一半。
    max_return_decay: float = 0.5
    #: 市场性成交率下限。
    min_fill_rate: float = 0.90
    #: 成交价偏离容差倍数（相对半 tick）。
    price_tolerance_ticks: float = 1.0

    def __post_init__(self) -> None:
        if not (0.0 < self.participation_limit <= 1.0):
            raise L1bSpecError(f"participation_limit 必须在 (0,1]，收到 {self.participation_limit}")
        if not (0.0 <= self.queue_position <= 1.0):
            raise L1bSpecError(f"queue_position 必须在 [0,1]，收到 {self.queue_position}")
        if not (0.0 <= self.max_return_decay < 1.0):
            raise L1bSpecError(f"max_return_decay 必须在 [0,1)，收到 {self.max_return_decay}")
        if not (0.0 <= self.min_fill_rate <= 1.0):
            raise L1bSpecError(f"min_fill_rate 必须在 [0,1]，收到 {self.min_fill_rate}")
        if self.price_limit_rate is not None and not 0 < self.price_limit_rate < 1:
            raise L1bSpecError(f"price_limit_rate 必须在 (0,1) 或 None，收到 {self.price_limit_rate}")
        if self.slippage_mode is SlippageMode.SQRT_IMPACT and self.slippage_eta <= 0:
            raise L1bSpecError("SQRT_IMPACT 需要正的 eta")

    def to_dict(self) -> dict[str, Any]:
        return {
            "participation_limit": self.participation_limit,
            "queue_position": self.queue_position,
            "slippage_mode": self.slippage_mode.value,
            "slippage_bps": self.slippage_bps,
            "slippage_eta": self.slippage_eta,
            "price_limit_rate": self.price_limit_rate,
            "volume_multiplier": self.volume_multiplier,
            "max_return_decay": self.max_return_decay,
            "min_fill_rate": self.min_fill_rate,
            "price_tolerance_ticks": self.price_tolerance_ticks,
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def slippage_for(self, adv: float, sigma: float) -> SlippageModel:
        return SlippageModel(
            mode=self.slippage_mode,
            bps=self.slippage_bps,
            eta=self.slippage_eta,
            sigma=sigma,
            adv=adv,
        )


def parse_execution(payload: Mapping[str, Any] | None) -> ExecutionSpec:
    """从契约 JSON 的 ``execution`` 段解析。缺字段用默认值（默认值即文档口径）。"""
    if payload is None:
        return ExecutionSpec()
    if not isinstance(payload, Mapping):
        raise L1bSpecError("execution 段必须是对象")
    mode_raw = str(payload.get("slippage_mode", "none")).lower()
    try:
        mode = SlippageMode(mode_raw)
    except ValueError as exc:
        raise L1bSpecError(f"未知的 slippage_mode：{mode_raw}") from exc
    rate = payload.get("price_limit_rate", 0.10)
    return ExecutionSpec(
        participation_limit=float(payload.get("participation_limit", 0.10)),
        queue_position=float(payload.get("queue_position", 0.5)),
        slippage_mode=mode,
        slippage_bps=float(payload.get("slippage_bps", 0.0)),
        slippage_eta=float(payload.get("slippage_eta", 1.0)),
        price_limit_rate=None if rate is None else float(rate),
        volume_multiplier=float(payload.get("volume_multiplier", 100.0)),
        max_return_decay=float(payload.get("max_return_decay", 0.5)),
        min_fill_rate=float(payload.get("min_fill_rate", 0.90)),
        price_tolerance_ticks=float(payload.get("price_tolerance_ticks", 1.0)),
    )


@dataclass
class LegResult:
    """一档运行的摘要（基线档或精验档）。"""

    label: str
    cumulative_return: float | None = None
    final_equity: float | None = None
    fill_count: int = 0
    fills_detail: list[dict[str, Any]] = field(default_factory=list)
    orders: int = 0
    fill_rate_market: float | None = None
    quantity_fill_rate: float | None = None
    average_price_deviation: float | None = None
    reasons: dict[str, int] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, with_curve: bool = True) -> dict[str, Any]:
        payload = {
            "label": self.label,
            "cumulative_return": self.cumulative_return,
            "final_equity": self.final_equity,
            "fill_count": self.fill_count,
            "orders": self.orders,
            "fill_rate_market": self.fill_rate_market,
            "quantity_fill_rate": self.quantity_fill_rate,
            "average_price_deviation": self.average_price_deviation,
            "reasons": self.reasons,
            "limitations": self.limitations,
            "data_quality": self.data_quality,
        }
        if with_curve:
            payload["equity_curve"] = self.equity_curve
        return payload


@dataclass
class L1bResult:
    hypothesis_id: str
    verdict: str
    best_params: dict[str, Any] = field(default_factory=dict)
    cash: float = 0.0
    baseline: LegResult | None = None
    stressed: LegResult | None = None
    retention: float | None = None
    decay: float | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    price_attribution: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "verdict": self.verdict,
            "best_params": self.best_params,
            "cash": self.cash,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "stressed": self.stressed.to_dict() if self.stressed else None,
            "retention": self.retention,
            "decay": self.decay,
            "checks": self.checks,
            "price_attribution": self.price_attribution,
            "execution": self.execution,
            "limitations": self.limitations,
            "notes": self.notes,
        }


def _leg(
    label: str,
    kernel: EventKernel,
    cash: float,
    frame: pd.DataFrame,
) -> LegResult:
    result: KernelResult = kernel.run()
    leg = LegResult(label=label)
    leg.final_equity = result.stats.get("final_equity")
    leg.cumulative_return = (
        (leg.final_equity / cash - 1.0) if leg.final_equity is not None and cash else None
    )
    leg.fill_count = int(result.stats.get("fills") or 0)
    leg.fills_detail = list(result.fills)
    leg.orders = int(result.stats.get("orders_total") or 0)
    leg.fill_rate_market = result.stats.get("fill_rate_market")
    leg.quantity_fill_rate = result.stats.get("quantity_fill_rate")
    leg.reasons = dict(result.stats.get("reasons") or {})
    leg.limitations = list(result.limitations)
    leg.data_quality = list(result.data_quality)
    leg.equity_curve = result.equity_curve

    # 成交价相对当日开盘价的平均偏离（用于"成交价是否可解释"）
    deviations: list[float] = []
    for fill in result.fills:
        row = frame.loc[pd.Timestamp(fill["day"])]
        reference = float(row["open"])
        if reference > 0:
            deviations.append(float(fill["price"]) / reference - 1.0)
    leg.average_price_deviation = (
        sum(deviations) / len(deviations) if deviations else None
    )
    return leg


def _sigma_and_adv(frame: pd.DataFrame, window: tuple[pd.Timestamp, pd.Timestamp]) -> tuple[float, float]:
    """从研究层数据估日波动率与日均成交量（仅用于 SQRT 模式，不参与默认口径）。"""
    series = frame.loc[(frame.index >= window[0]) & (frame.index <= window[1])]
    if series.empty:
        return 0.0, 0.0
    returns = series["close"].pct_change().dropna()
    sigma = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    adv = float(series["volume"].mean())
    return sigma, adv


def run_l1b(
    spec_source: HypothesisSpec | Mapping[str, Any],
    *,
    best_params: Mapping[str, Any],
    cash: float | None = None,
    data: pd.DataFrame | None = None,
    trading: pd.DataFrame | None = None,
    strategy_factory: Any = None,
    l1a_verdict: str | None = None,
) -> L1bResult:
    """在事件驱动内核上重跑粗筛存活策略，给出精验终态。"""
    from deepquant.gates.sweep import default_strategy_factory

    factory = strategy_factory or default_strategy_factory

    # ---- 1. 契约 ----
    if isinstance(spec_source, HypothesisSpec):
        spec = spec_source
    else:
        from deepquant.gates.spec import parse_spec

        spec = parse_spec(spec_source)

    payload = spec.to_dict()
    execution = parse_execution(payload.get("execution"))
    result = L1bResult(
        hypothesis_id=spec.hypothesis_id,
        verdict=VERDICT_SPEC,
        best_params=dict(best_params),
        cash=float(cash if cash is not None else spec.initial_cash),
        execution=execution.to_dict(),
    )
    result.execution["digest"] = execution.digest

    # ---- 2. 数据 ----
    window = (pd.Timestamp(spec.in_sample.start), pd.Timestamp(spec.in_sample.end))
    if data is None:
        verdict = check_data(spec.data, spec.in_sample)
        if verdict.frame is None:
            result.verdict = VERDICT_DATA
            result.checks = [
                {"name": c.code, "passed": c.passed, "detail": c.detail}
                for c in verdict.checks
            ]
            result.limitations.append("研究层数据不可读，精验未执行")
            return result
        frame = verdict.frame
    else:
        frame = data
    # 统一规范化：check_data 返回的原始表 index 可能是 RangeIndex，
    # 而内核、成交价归因、波动率估计都要求「日期升序的 DatetimeIndex」。
    frame = normalize_daily_data(frame)

    # ---- 3. 涨跌停（必须来自交易层不复权数据） ----
    limits = None
    limit_note = "未提供交易层数据：本次精验**未执行涨跌停判定**"
    if trading is None and spec.data.trading_path:
        try:
            trading = load_frame(spec.data.trading_path)
        except (FileNotFoundError, ValueError) as exc:
            limit_note = f"交易层数据不可读（{exc}）：本次精验**未执行涨跌停判定**"
    if trading is not None and execution.price_limit_rate is not None:
        limits = limits_from_trading_frame(
            trading, price_limit_rate=execution.price_limit_rate
        )
        limit_note = (
            f"涨跌停判定已启用（比例 {execution.price_limit_rate}，"
            f"依据交易层不复权数据 {len(limits)} 个交易日）"
        )
    result.limitations.append(limit_note)

    # ---- 4. 策略 ----
    try:
        strategy = factory(spec.strategy_id, dict(best_params))
    except Exception as exc:  # noqa: BLE001
        result.verdict = VERDICT_SPEC
        result.limitations.append(f"无法用最优参数构造策略：{type(exc).__name__}: {exc}")
        return result

    # 滑点全部走 SlippageModel（kernel 层），配置里的常数滑点必须置 0，
    # 否则同一笔成交会被扣两次滑点。
    config = BacktestConfig(
        symbol=spec.data.symbol,
        initial_cash=result.cash,
        start_date=window[0],
        end_date=window[1],
        slippage_buy=0.0,
        slippage_sell=0.0,
    )

    sigma, adv = _sigma_and_adv(frame, window)

    # ---- 5. 基线档：同资金、摩擦全关 ----
    baseline = _leg(
        "无摩擦基线（L1a 口径）",
        EventKernel(
            strategy=factory(spec.strategy_id, dict(best_params)),
            frame=frame,
            config=config,
            slippage=SlippageModel(mode=SlippageMode.NONE),
            matching=MatchingParams(participation_limit=1.0, queue_position=0.0),
            limits=None,
            volume_multiplier=execution.volume_multiplier,
        ),
        result.cash,
        frame,
    )

    # ---- 6. 精验档：同资金、摩擦全开 ----
    stressed = _leg(
        "事件驱动精验（摩擦全开）",
        EventKernel(
            strategy=strategy,
            frame=frame,
            config=config,
            slippage=execution.slippage_for(adv=adv, sigma=sigma),
            matching=MatchingParams(
                participation_limit=execution.participation_limit,
                queue_position=execution.queue_position,
            ),
            limits=limits,
            volume_multiplier=execution.volume_multiplier,
        ),
        result.cash,
        frame,
    )
    result.baseline = baseline
    result.stressed = stressed

    # ---- 7. 判定 ----
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool | None, detail: str, **extra: Any) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail, **extra})

    if baseline.cumulative_return is None or stressed.cumulative_return is None:
        add("收益可计算", False, "基线或精验档权益缺失，无法计算收益")
        result.checks = checks
        result.verdict = VERDICT_INCONCLUSIVE
        return result

    base_ret = baseline.cumulative_return
    stress_ret = stressed.cumulative_return
    add("基线收益可计算", True, f"基线累计收益 {base_ret:+.4f}")

    if base_ret <= 0:
        add(
            "基线应已被 L1a 筛除",
            False,
            f"无摩擦基线收益 {base_ret:+.4f} ≤ 0；L1a 不应放行这样的策略，两级口径脱节",
        )
        result.checks = checks
        result.verdict = VERDICT_INCONCLUSIVE
        return result

    result.retention = stress_ret / base_ret
    result.decay = 1.0 - result.retention

    # 7.1 成交率（一票否决，先于符号判定）
    # 顺序有讲究：成交率不足时收益必然退化，先判符号反转会把"没买到"
    # 误报成"策略失效"，归因就错了。
    fill_rate = stressed.fill_rate_market
    if fill_rate is None:
        add("成交率可计算", False, "精验档没有订单，成交率不可计算")
        result.checks = checks
        result.verdict = VERDICT_INCONCLUSIVE
        return result
    fill_ok = fill_rate >= execution.min_fill_rate
    add(
        "市场性成交率达标",
        fill_ok,
        f"成交率 {fill_rate:.4f}（阈值 {execution.min_fill_rate}）",
        actual=fill_rate,
        threshold=execution.min_fill_rate,
    )
    if not fill_ok:
        result.checks = checks
        result.verdict = VERDICT_FILL_RATE
        result.limitations.append("成交率低于预设阈值，按表 7 一票否决")
        return result

    # 7.2 符号反转（一票否决）
    # 用严格 < 0：收益恰好为 0 不是"符号反转"，交给衰减判定处理更准确。
    reversed_sign = stress_ret < 0
    add(
        "收益符号未反转",
        not reversed_sign,
        f"精验收益 {stress_ret:+.4f}（基线 {base_ret:+.4f}）",
        actual=stress_ret,
        threshold=0.0,
    )
    if reversed_sign:
        result.checks = checks
        result.verdict = VERDICT_SIGN_REVERSAL
        result.limitations.append("精验后收益符号反转，按表 7 一票否决")
        return result

    # 7.3 收益衰减
    decay_ok = result.decay <= execution.max_return_decay
    add(
        "收益衰减在阈值内",
        decay_ok,
        f"保留率 {result.retention:.4f}，衰减 {result.decay:.4f}"
        f"（阈值 {execution.max_return_decay}）",
        actual=result.decay,
        threshold=execution.max_return_decay,
    )
    if not decay_ok:
        result.checks = checks
        result.verdict = VERDICT_DECAY
        return result

    # 7.4 成交价可解释
    attribution = explain_fill_prices(
        stressed.fills_detail, frame, tolerance_ticks=execution.price_tolerance_ticks
    )
    result.price_attribution = attribution
    explain_ok = attribution["unexplained"] == 0
    add(
        "成交价可被滑点模型解释",
        explain_ok,
        f"{attribution['fills']} 笔成交中 {attribution['unexplained']} 笔偏离超出容差"
        f"（容差 {attribution['tolerance']:.4f} 元）",
        actual=attribution["max_abs_residual"],
        threshold=attribution["tolerance"],
    )
    if not explain_ok:
        result.checks = checks
        result.verdict = VERDICT_UNEXPLAINABLE
        return result

    result.checks = checks
    result.verdict = VERDICT_PASSED
    if l1a_verdict is not None and l1a_verdict != "L1A_PASSED":
        # 表 7 是逐级闸门：L1a 没放行，L1b 的"通过"不能当作放行依据。
        # 两档仍然跑完，是为了把"摩擦到底吃掉多少"这个数字留下来。
        result.verdict = VERDICT_PREREQUISITE
        result.limitations.append(
            f"前置未满足：L1a 终态为 {l1a_verdict}（非 L1A_PASSED）。"
            "本次精验仅用于量化成交摩擦，**不构成放行结论**。"
        )
    result.limitations.extend(
        [
            "L1b 只评估粗筛所用区间；样本外表现属 L3 职责，本闸门不给结论。",
            "成交模型参数（参与率/队列/滑点）为模型假设，非实测标定值；"
            "结论应表述为「在该组假设下保留 X%」，不得表述为「实盘可成交 X%」。",
        ]
    )
    return result


def explain_fill_prices(
    fills: list[dict[str, Any]],
    frame: pd.DataFrame,
    tolerance_ticks: float = 1.0,
) -> dict[str, Any]:
    """逐笔核对成交价：实际价 vs 理论价（当日开盘）× (1 ± 记录的冲击)。"""
    tolerance = PRICE_TOLERANCE * tolerance_ticks
    residuals: list[float] = []
    unexplained: list[dict[str, Any]] = []
    for fill in fills:
        reference = float(frame.loc[pd.Timestamp(fill["day"]), "open"])
        impact = float(fill.get("unit_impact") or 0.0)
        signed = impact if fill["side"] == "BUY" else -impact
        expected = reference * (1.0 + signed)
        residual = abs(float(fill["price"]) - expected)
        residuals.append(residual)
        if residual > tolerance:
            unexplained.append(
                {
                    "fill_id": fill["fill_id"],
                    "day": fill["day"],
                    "price": fill["price"],
                    "expected": round(expected, 4),
                    "residual": round(residual, 6),
                }
            )
    return {
        "fills": len(fills),
        "unexplained": len(unexplained),
        "max_abs_residual": max(residuals) if residuals else 0.0,
        "tolerance": tolerance,
        "examples": unexplained[:5],
        "basis": "expected = 当日开盘价 × (1 ± 记录的 unit_impact)；容差为半个报价单位",
    }


def render_l1b_markdown(
    result: L1bResult,
    *,
    sensitivity: list[dict[str, Any]] | None = None,
    title: str = "",
) -> str:
    """把精验结果渲染为可交付的报告（数字全部来自 result，不在此处重算）。"""
    base = result.baseline
    stress = result.stressed
    lines: list[str] = []
    lines.append(f"# L1b 事件驱动精验：{title or result.hypothesis_id}")
    lines.append("")
    lines.append(f"- 假设编号：`{result.hypothesis_id}`")
    lines.append(f"- **终态：`{result.verdict}`**")
    lines.append(f"- 资金规模：{result.cash:,.0f} 元（与粗筛同资金）")
    lines.append(f"- 最优参数：`{json.dumps(result.best_params, ensure_ascii=False, sort_keys=True)}`")
    lines.append("")
    if result.verdict == VERDICT_PREREQUISITE:
        lines.append(
            "> **前置未满足**：粗筛（L1a）并未放行该假设。以下数字只用于量化成交摩擦，"
            "**不构成放行结论**。"
        )
        lines.append("")

    lines.append("## 一、执行假设（全部为模型参数，非实测标定值）")
    lines.append("")
    lines.append("| 参数 | 取值 |")
    lines.append("|---|---|")
    for key in (
        "participation_limit",
        "queue_position",
        "slippage_mode",
        "price_limit_rate",
        "volume_multiplier",
        "max_return_decay",
        "min_fill_rate",
    ):
        lines.append(f"| {key} | {result.execution.get(key)} |")
    lines.append(f"| 假设摘要 | `{result.execution.get('digest', '')[:16]}` |")
    lines.append("")

    lines.append("## 二、两档对比（同资金、同区间、同费率）")
    lines.append("")
    lines.append("| 档位 | 累计收益 | 期末权益 | 成交笔数 | 订单数 | 市场性成交率 | 数量成交率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for leg in (base, stress):
        if leg is None:
            continue
        ret = "不可计算" if leg.cumulative_return is None else f"{leg.cumulative_return:+.4f}"
        equity = "不可计算" if leg.final_equity is None else f"{leg.final_equity:,.2f}"
        fr = "—" if leg.fill_rate_market is None else f"{leg.fill_rate_market:.4f}"
        qr = "—" if leg.quantity_fill_rate is None else f"{leg.quantity_fill_rate:.4f}"
        lines.append(
            f"| {leg.label} | {ret} | {equity} | {leg.fill_count} | {leg.orders} | {fr} | {qr} |"
        )
    lines.append("")
    if result.retention is not None:
        lines.append(
            f"- 收益保留率 **{result.retention:.4f}**，衰减 **{result.decay:.4f}**"
            f"（阈值 {result.execution.get('max_return_decay')}）"
        )
    lines.append("")

    if stress is not None and stress.reasons:
        lines.append("### 未成交原因分布")
        lines.append("")
        lines.append("| 原因 | 次数 |")
        lines.append("|---|---|")
        for reason, count in stress.reasons.items():
            lines.append(f"| `{reason}` | {count} |")
        lines.append("")

    lines.append("## 三、判定明细")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for check in result.checks:
        mark = {True: "PASS", False: "FAIL", None: "不可判定"}[check.get("passed")]
        lines.append(f"| {check['name']} | {mark} | {check['detail']} |")
    lines.append("")

    attribution = result.price_attribution
    if attribution:
        lines.append("## 四、成交价归因（「成交价可解释」的取证）")
        lines.append("")
        lines.append(
            f"- 核验口径：{attribution.get('basis', '')}"
        )
        lines.append(
            f"- 成交 {attribution.get('fills')} 笔，偏离超容差 **{attribution.get('unexplained')} 笔**，"
            f"最大残差 {attribution.get('max_abs_residual'):.6f} 元（容差 {attribution.get('tolerance'):.4f} 元）"
        )
        if attribution.get("examples"):
            lines.append("")
            lines.append("| fill_id | 日期 | 成交价 | 期望价 | 残差 |")
            lines.append("|---|---|---|---|---|")
            for item in attribution["examples"]:
                lines.append(
                    f"| {item['fill_id']} | {item['day']} | {item['price']} | "
                    f"{item['expected']} | {item['residual']} |"
                )
        lines.append("")

    if sensitivity:
        lines.append("## 五、资金敏感性（参考，不参与主判定）")
        lines.append("")
        lines.append("| 资金 | 无摩擦收益 | 精验收益 | 保留率 | 市场性成交率 |")
        lines.append("|---|---|---|---|---|")
        for row in sensitivity:
            ret0 = row.get("baseline_return")
            ret1 = row.get("stressed_return")
            retention = row.get("retention")
            fill_rate = row.get("fill_rate_market")
            cell0 = "—" if ret0 is None else f"{ret0:+.4f}"
            cell1 = "—" if ret1 is None else f"{ret1:+.4f}"
            cell2 = "—" if retention is None else f"{retention:.4f}"
            cell3 = "—" if fill_rate is None else f"{fill_rate:.4f}"
            lines.append(f"| {row['cash']:,.0f} | {cell0} | {cell1} | {cell2} | {cell3} |")
        lines.append("")

    lines.append("## 六、口径与限制")
    lines.append("")
    for item in result.limitations:
        lines.append(f"- {item}")
    if base is not None:
        for item in base.limitations:
            lines.append(f"- （基线档）{item}")
    lines.append("")
    return "\n".join(lines)
