"""L2：蒙特卡洛闸门（收益分布与路径依赖）。

表 7 的 L2 只有两句要求：「收益分布尾部风险在容忍区间」「分布形态显示策略依赖极端路径」即否决。
第一句容易实现，第二句如果不落成可计算的判据，就永远无法执行——本模块的主要工作是把第二句变成
两条**可复算**的判据。

方法选择（每条都有排除掉的替代方案）
------------------------------------
1. **重抽样单位是日收益，不是权益曲线。**
   对净值水平做重抽样会拼出"昨天亏 30%、今天从 100 万开始"这种不存在的路径。
   正确做法是对 ``r_t = E_t / E_{t-1} - 1`` 重抽样，再累乘重建净值。

2. **必须用块重抽样。**
   IID 重抽样会打散波动聚集、也会打散"连续持仓 / 连续空仓"的节律，
   结果就是尾部被系统性低估。这里用 **stationary bootstrap**（Politis & Romano 1994）：
   块长服从几何分布、期望为 ``mean_block_length``，块之间首尾相接，
   既能保留序列相关性，又不产生"块边界跳变"。

3. **块长必须做敏感性。**
   块长是最主观的参数。只报一个块长下的结论，等于把结论押在这个选择上。
   本模块默认同时跑 (10, 20, 40, 60) 四个块长，任何一项判据在四个块长下方向不一致，
   就记 ``block_length_stable = False``，并在报告里显式提示。

两条"依赖极端路径"的判据
------------------------
- **E1 极端日依赖**：把收益最好的 ``top_day_fraction``（默认 1%）的交易日**置零后重算累计收益**。
  若去掉这极少数几天收益就翻负，说明策略靠运气而不是靠可重复的边际。
  （"置零"比"删掉"更保守：删掉会让剩余天数变少、复合次数变少，反而更容易显得正收益。）
- **E2 可重复性**：``repeatable_ratio = bootstrap 收益中位数 / 实际收益``。
  该比值衡量"如果历史重来一次，还能拿到多少比例的收益"。
  比值接近 1 说明收益来自广泛可重复的日度优势；接近 0 甚至为负说明实际路径是幸运样本。

这两条都是**否定式**判据：它们不证明策略好，只负责证明策略是否只在历史那一条路径上成立。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

# ---------------- 终态 ---------------- #
VERDICT_PASSED = "L2_PASSED"
VERDICT_EXTREME_DEPENDENCE = "L2_REJECTED_EXTREME_DEPENDENCE"
VERDICT_PATH_DEPENDENCE = "L2_REJECTED_PATH_DEPENDENCE"
VERDICT_TAIL = "L2_REJECTED_TAIL_RISK"
VERDICT_INCONCLUSIVE = "L2_INCONCLUSIVE"
VERDICT_SPEC = "L2_REJECTED_SPEC"
VERDICT_PREREQUISITE = "L2_BLOCKED_PREREQUISITE"

ALL_VERDICTS = (
    VERDICT_PASSED,
    VERDICT_EXTREME_DEPENDENCE,
    VERDICT_PATH_DEPENDENCE,
    VERDICT_TAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_SPEC,
    VERDICT_PREREQUISITE,
)

#: 计算重抽样统计量时，净值路径的最短长度。太短则尾部估计没有意义。
MIN_OBSERVATIONS = 250


class MonteCarloSpecError(ValueError):
    """蒙特卡洛契约非法。"""


@dataclass(frozen=True)
class MonteCarloSpec:
    """重抽样参数与判定阈值。所有阈值在跑之前写定，不因结果调整。"""

    # ---------------- 重抽样 ---------------- #
    replications: int = 2000
    #: stationary bootstrap 的平均块长（交易日）。
    mean_block_length: int = 20
    seed: int = 20261027
    #: 块长敏感性网格（第一个元素必须等于 mean_block_length）。
    block_lengths: tuple[int, ...] = (20, 10, 40, 60)

    # ---------------- 判据阈值 ---------------- #
    #: E1：剔除收益最好的 top_day_fraction 交易日后，累计收益下限。
    top_day_fraction: float = 0.01
    min_trimmed_return: float = 0.0
    #: E2：可重复性下限（bootstrap 收益中位数 / 实际收益）。
    min_repeatable_ratio: float = 0.50
    #: E3：期末收益 5% 分位下限（负数，表示可接受的最大亏损）。
    max_terminal_loss_p5: float = -0.30
    #: E4：最大回撤 95% 分位上限。
    max_mdd_p95: float = 0.40
    #: 分布形态：CVaR5 下限（最差 5% 路径的平均期末收益）。
    max_cvar5: float = -0.45
    #: 剔除剖面：只用于刻画依赖程度，不参与判定。
    trim_profile_fractions: tuple[float, ...] = (0.002, 0.005, 0.01, 0.02, 0.05)

    def __post_init__(self) -> None:
        if self.replications < 100:
            raise MonteCarloSpecError(f"replications 至少 100，收到 {self.replications}")
        if self.mean_block_length < 1:
            raise MonteCarloSpecError(f"mean_block_length 必须 >= 1，收到 {self.mean_block_length}")
        if not 0 < self.top_day_fraction < 1:
            raise MonteCarloSpecError(f"top_day_fraction 必须在 (0,1)，收到 {self.top_day_fraction}")
        if not 0 <= self.min_repeatable_ratio <= 1:
            raise MonteCarloSpecError(
                f"min_repeatable_ratio 必须在 [0,1]，收到 {self.min_repeatable_ratio}"
            )
        if not self.block_lengths:
            raise MonteCarloSpecError("block_lengths 不能为空")
        if self.mean_block_length not in self.block_lengths:
            raise MonteCarloSpecError(
                f"block_lengths 必须包含主块长 {self.mean_block_length}，收到 {self.block_lengths}"
            )
        if any(value < 1 for value in self.block_lengths):
            raise MonteCarloSpecError(f"块长必须为正整数，收到 {self.block_lengths}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "replications": self.replications,
            "mean_block_length": self.mean_block_length,
            "seed": self.seed,
            "block_lengths": list(self.block_lengths),
            "top_day_fraction": self.top_day_fraction,
            "min_trimmed_return": self.min_trimmed_return,
            "min_repeatable_ratio": self.min_repeatable_ratio,
            "max_terminal_loss_p5": self.max_terminal_loss_p5,
            "max_mdd_p95": self.max_mdd_p95,
            "max_cvar5": self.max_cvar5,
            "trim_profile_fractions": list(self.trim_profile_fractions),
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_monte_carlo(payload: dict[str, Any] | None) -> MonteCarloSpec:
    if payload is None:
        return MonteCarloSpec()
    if not isinstance(payload, dict):
        raise MonteCarloSpecError("monte_carlo 段必须是对象")
    block_lengths = payload.get("block_lengths")
    kwargs: dict[str, Any] = {}
    for key in (
        "replications",
        "mean_block_length",
        "seed",
        "top_day_fraction",
        "min_trimmed_return",
        "min_repeatable_ratio",
        "max_terminal_loss_p5",
        "max_mdd_p95",
        "max_cvar5",
    ):
        if key in payload:
            value = payload[key]
            kwargs[key] = int(value) if key in {"replications", "mean_block_length", "seed"} else float(value)
    if block_lengths is not None:
        kwargs["block_lengths"] = tuple(int(item) for item in block_lengths)
    profile = payload.get("trim_profile_fractions")
    if profile is not None:
        kwargs["trim_profile_fractions"] = tuple(float(item) for item in profile)
    return MonteCarloSpec(**kwargs)


# --------------------------------------------------------------------------- #
# 序列工具
# --------------------------------------------------------------------------- #
def returns_from_equity(equity: Sequence[float]) -> tuple[np.ndarray, int]:
    """从权益序列算日收益。返回 (收益率数组, 被剔除的缺失日数)。

    缺失估值日**必须剔除并计数**，不能用 0 顶替——填 0 等于宣称"那天没涨没跌"，
    会人为压低波动率并让尾部看起来更安全。
    """
    values = [float(item) for item in equity]
    if not values:
        raise MonteCarloSpecError("权益序列为空")
    if any(not math.isfinite(item) or item <= 0 for item in values):
        raise MonteCarloSpecError("权益序列含非法值（非有限或非正）")
    cleaned = np.asarray(values, dtype=float)
    returns = cleaned[1:] / cleaned[:-1] - 1.0
    if returns.size == 0:
        raise MonteCarloSpecError("权益序列长度为 1，无法计算收益")
    return returns, 0


def returns_from_curve(curve: Sequence[dict[str, Any]]) -> tuple[np.ndarray, int]:
    """从内核的 equity_curve 提取日收益，跳过估值缺失日并计数。"""
    values: list[float] = []
    missing = 0
    for row in curve:
        equity = row.get("equity")
        if equity is None:
            missing += 1
            continue
        values.append(float(equity))
    returns, _ = returns_from_equity(values)
    return returns, missing


def max_drawdown(equity: np.ndarray) -> float:
    """最大回撤（正数）。输入是净值序列，不是收益率。"""
    if equity.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    return float(-drawdown.min())


def cumulative_return(returns: np.ndarray) -> float:
    return float(np.prod(1.0 + returns) - 1.0)


def stationary_bootstrap(
    returns: np.ndarray, mean_block_length: int, rng: np.random.Generator
) -> np.ndarray:
    """Politis & Romano (1994) stationary bootstrap（向量化实现）。

    块长服从成功概率 ``p = 1/L`` 的几何分布，块首尾相接、索引循环回绕，
    因此重抽样序列与原序列同长且近似平稳。

    实现说明：逐元素循环写法直观但在 2000×1200 的规模下要跑一分多钟，
    这里改成"先定位每次块重启的位置，再按块内偏移传播"的向量化写法：
    ``last_reset`` 给出每个位置所属块的起点，``base`` 是该块的随机起始索引，
    ``base + offset`` 即块内连续取值。
    """
    n = returns.size
    if n == 0:
        raise MonteCarloSpecError("收益序列为空，无法重抽样")
    if mean_block_length < 1:
        raise MonteCarloSpecError(f"mean_block_length 必须 >= 1，收到 {mean_block_length}")
    probability = 1.0 / mean_block_length

    positions = np.arange(n)
    restart = rng.random(n) < probability
    restart[0] = True  # 第一个位置必然是新块起点
    starts = rng.integers(0, n, size=n)

    last_reset = np.maximum.accumulate(np.where(restart, positions, 0))
    offsets = positions - last_reset
    index = (starts[last_reset] + offsets) % n
    return returns[index]


def simulate(
    returns: np.ndarray, spec: MonteCarloSpec, rng: np.random.Generator | None = None
) -> dict[str, Any]:
    """跑 ``spec.replications`` 次重抽样，返回期末收益与最大回撤的分布。"""
    generator = rng or np.random.default_rng(spec.seed)
    terminal = np.empty(spec.replications, dtype=float)
    drawdown = np.empty(spec.replications, dtype=float)
    for index in range(spec.replications):
        sample = stationary_bootstrap(returns, spec.mean_block_length, generator)
        path = np.cumprod(1.0 + sample)
        terminal[index] = path[-1] - 1.0
        drawdown[index] = max_drawdown(path)
    return {
        "terminal": terminal,
        "drawdown": drawdown,
        "mean_block_length": spec.mean_block_length,
        "replications": spec.replications,
    }


def summarise_distribution(terminal: np.ndarray, drawdown: np.ndarray) -> dict[str, Any]:
    """分布摘要。分位数用线性插值（与 numpy 默认一致，写明以便复算）。"""
    worst = np.sort(terminal)[: max(1, int(math.ceil(0.05 * terminal.size)))]
    return {
        "terminal_mean": float(terminal.mean()),
        "terminal_median": float(np.median(terminal)),
        "terminal_p5": float(np.percentile(terminal, 5)),
        "terminal_p25": float(np.percentile(terminal, 25)),
        "terminal_p95": float(np.percentile(terminal, 95)),
        "terminal_std": float(terminal.std(ddof=1)) if terminal.size > 1 else None,
        "cvar5": float(worst.mean()),
        # 尾部质量：负收益路径占比
        "loss_probability": float((terminal < 0).mean()),
        "mdd_median": float(np.median(drawdown)),
        "mdd_p95": float(np.percentile(drawdown, 95)),
        "mdd_max": float(drawdown.max()),
        "skew": float(_skew(terminal)),
        "excess_kurtosis": float(_excess_kurtosis(terminal)),
        "samples": int(terminal.size),
        "percentile_method": "linear",
    }


def _skew(values: np.ndarray) -> float:
    if values.size < 3:
        return 0.0
    centred = values - values.mean()
    std = centred.std(ddof=0)
    if std == 0:
        return 0.0
    return float((centred**3).mean() / std**3)


def _excess_kurtosis(values: np.ndarray) -> float:
    if values.size < 4:
        return 0.0
    centred = values - values.mean()
    std = centred.std(ddof=0)
    if std == 0:
        return 0.0
    return float((centred**4).mean() / std**4 - 3.0)


def trimmed_return(returns: np.ndarray, fraction: float) -> dict[str, Any]:
    """把收益最高的 ``fraction`` 比例交易日**置零**后重算累计收益。

    置零而不是删除：删除会让复合次数减少，剩下的日子更容易凑出正收益，
    方向恰好相反。置零等价于"那几天放假，钱没动"。
    """
    n = returns.size
    count = max(1, int(round(n * fraction)))
    order = np.argsort(returns)[::-1][:count]
    trimmed = returns.copy()
    removed = trimmed[order].copy()
    trimmed[order] = 0.0
    return {
        "removed_days": int(count),
        "fraction": fraction,
        "original_return": cumulative_return(returns),
        "trimmed_return": cumulative_return(trimmed),
        "removed_total_return": float(np.prod(1.0 + removed) - 1.0),
        "top_day_returns": [float(item) for item in removed[:10]],
    }


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #
@dataclass
class L2Result:
    hypothesis_id: str
    verdict: str
    observations: int = 0
    missing_valuation_days: int = 0
    actual_return: float | None = None
    actual_max_drawdown: float | None = None
    distribution: dict[str, Any] = field(default_factory=dict)
    trimmed: dict[str, Any] = field(default_factory=dict)
    trim_profile: list[dict[str, Any]] = field(default_factory=list)
    repeatable_ratio: float | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    block_sensitivity: list[dict[str, Any]] = field(default_factory=list)
    block_length_stable: bool | None = None
    spec: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "verdict": self.verdict,
            "observations": self.observations,
            "missing_valuation_days": self.missing_valuation_days,
            "actual_return": self.actual_return,
            "actual_max_drawdown": self.actual_max_drawdown,
            "distribution": self.distribution,
            "trimmed": self.trimmed,
            "trim_profile": self.trim_profile,
            "repeatable_ratio": self.repeatable_ratio,
            "checks": self.checks,
            "block_sensitivity": self.block_sensitivity,
            "block_length_stable": self.block_length_stable,
            "spec": self.spec,
            "limitations": self.limitations,
            "notes": self.notes,
        }


def run_l2(
    equity: Sequence[float] | None = None,
    *,
    curve: Sequence[dict[str, Any]] | None = None,
    hypothesis_id: str = "H-L2",
    spec: MonteCarloSpec | None = None,
    l1a_verdict: str | None = None,
    l1b_verdict: str | None = None,
) -> L2Result:
    """在给定权益路径上跑蒙特卡洛并给出终态。

    输入二选一：``equity``（净值序列）或 ``curve``（内核 equity_curve，含估值缺失标记）。
    """
    settings = spec or MonteCarloSpec()
    result = L2Result(
        hypothesis_id=hypothesis_id,
        verdict=VERDICT_SPEC,
        spec=settings.to_dict(),
    )
    result.spec["digest"] = settings.digest

    try:
        if curve is not None:
            returns, missing = returns_from_curve(curve)
        elif equity is not None:
            returns, missing = returns_from_equity(equity)
        else:
            raise MonteCarloSpecError("必须提供 equity 或 curve 之一")
    except MonteCarloSpecError as exc:
        result.limitations.append(f"输入序列不可用：{exc}")
        return result

    result.observations = int(returns.size)
    result.missing_valuation_days = int(missing)
    # 这条对两个判据的关系必须无条件说明，否则读者会以为它们是替代关系
    result.limitations.append(
        "E1（剔除极端日）与 E2（可重复性）互补而非冗余：块重抽样会**保留**极端日，"
        "因此 E2 可能在中位数不低于实际值时仍给出『可重复』的假象；"
        "反过来 E1 无法识别『收益依赖特定顺序』。"
        "本仓库真实用例就出现了这种分离：可重复性 1.04（>1）但剔除 1% 交易日后收益转负。"
    )

    if returns.size < MIN_OBSERVATIONS:
        result.verdict = VERDICT_INCONCLUSIVE
        result.limitations.append(
            f"样本仅 {returns.size} 个收益观测（门槛 {MIN_OBSERVATIONS}），尾部估计不可信"
        )
        return result

    result.actual_return = cumulative_return(returns)
    path = np.cumprod(1.0 + returns)
    result.actual_max_drawdown = max_drawdown(path)

    # ---------------- 主块长下的分布 ---------------- #
    simulated = simulate(returns, settings)
    distribution = summarise_distribution(simulated["terminal"], simulated["drawdown"])
    result.distribution = distribution

    # ---------------- E1 极端日依赖 ---------------- #
    trimmed = trimmed_return(returns, settings.top_day_fraction)
    result.trimmed = trimmed
    # 剖面：单一阈值只能回答"是/否"，剖面能回答"依赖到什么程度、临界点在哪"
    result.trim_profile = [
        trimmed_return(returns, fraction) for fraction in settings.trim_profile_fractions
    ]

    # ---------------- E2 可重复性 ---------------- #
    if result.actual_return and result.actual_return > 0:
        result.repeatable_ratio = distribution["terminal_median"] / result.actual_return
    else:
        result.repeatable_ratio = None

    # ---------------- 块长敏感性 ---------------- #
    sensitivity: list[dict[str, Any]] = []
    for block in settings.block_lengths:
        variant = MonteCarloSpec(
            replications=settings.replications,
            mean_block_length=block,
            seed=settings.seed,
            block_lengths=(block,),
            top_day_fraction=settings.top_day_fraction,
            min_trimmed_return=settings.min_trimmed_return,
            min_repeatable_ratio=settings.min_repeatable_ratio,
            max_terminal_loss_p5=settings.max_terminal_loss_p5,
            max_mdd_p95=settings.max_mdd_p95,
            max_cvar5=settings.max_cvar5,
        )
        run = simulate(returns, variant)
        summary = summarise_distribution(run["terminal"], run["drawdown"])
        ratio = (
            summary["terminal_median"] / result.actual_return
            if result.actual_return and result.actual_return > 0
            else None
        )
        sensitivity.append(
            {
                "mean_block_length": block,
                "terminal_median": summary["terminal_median"],
                "terminal_p5": summary["terminal_p5"],
                "cvar5": summary["cvar5"],
                "mdd_p95": summary["mdd_p95"],
                "repeatable_ratio": ratio,
                "loss_probability": summary["loss_probability"],
            }
        )
    result.block_sensitivity = sensitivity
    if result.repeatable_ratio is not None:
        ratios = [item["repeatable_ratio"] for item in sensitivity if item["repeatable_ratio"] is not None]
        result.block_length_stable = bool(
            ratios and all(item >= settings.min_repeatable_ratio for item in ratios)
        )

    # ---------------- 判定（一票否决优先） ---------------- #
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str, **extra: Any) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail, **extra})

    # 1) E1 极端日依赖
    e1_ok = trimmed["trimmed_return"] > settings.min_trimmed_return
    add(
        "剔除极端交易日后仍为正收益",
        e1_ok,
        f"剔除最好的 {trimmed['removed_days']} 天后，累计收益由 "
        f"{trimmed['original_return']:+.4f} 变为 {trimmed['trimmed_return']:+.4f}"
        f"（这 {trimmed['removed_days']} 天合计贡献 {trimmed['removed_total_return']:+.4f}）",
        actual=trimmed["trimmed_return"],
        threshold=settings.min_trimmed_return,
    )
    if not e1_ok:
        result.checks = checks
        result.verdict = VERDICT_EXTREME_DEPENDENCE
        result.limitations.append(
            "去掉极少数最好的交易日后收益即不成立：收益依赖个别日子，不构成可重复的边际。"
        )
        return result

    # 2) E2 可重复性
    if result.repeatable_ratio is None:
        add("可重复性可计算", False, "实际收益非正，可重复性比值无意义")
        result.checks = checks
        result.verdict = VERDICT_INCONCLUSIVE
        return result
    e2_ok = result.repeatable_ratio >= settings.min_repeatable_ratio
    add(
        "收益可重复性达标",
        e2_ok,
        f"重抽样收益中位 {distribution['terminal_median']:+.4f} / 实际 "
        f"{result.actual_return:+.4f} = {result.repeatable_ratio:.4f}"
        f"（阈值 {settings.min_repeatable_ratio}）",
        actual=result.repeatable_ratio,
        threshold=settings.min_repeatable_ratio,
    )
    if not e2_ok:
        result.checks = checks
        result.verdict = VERDICT_PATH_DEPENDENCE
        result.limitations.append(
            "重抽样后收益中位数远低于实际值：实际路径是幸运样本，期望收益应向下修正。"
        )
        return result

    # 3) 尾部风险（表 7 的通过标准）
    tail_checks = [
        (
            "期末收益 5% 分位在容忍区间",
            distribution["terminal_p5"] >= settings.max_terminal_loss_p5,
            distribution["terminal_p5"],
            settings.max_terminal_loss_p5,
            f"{distribution['terminal_p5']:+.4f}（下限 {settings.max_terminal_loss_p5}）",
        ),
        (
            "最大回撤 95% 分位在容忍区间",
            distribution["mdd_p95"] <= settings.max_mdd_p95,
            distribution["mdd_p95"],
            settings.max_mdd_p95,
            f"{distribution['mdd_p95']:.4f}（上限 {settings.max_mdd_p95}）",
        ),
        (
            "CVaR5 在容忍区间",
            distribution["cvar5"] >= settings.max_cvar5,
            distribution["cvar5"],
            settings.max_cvar5,
            f"{distribution['cvar5']:+.4f}（下限 {settings.max_cvar5}）",
        ),
    ]
    tail_ok = True
    for name, passed, actual, threshold, detail in tail_checks:
        add(name, passed, detail, actual=actual, threshold=threshold)
        tail_ok = tail_ok and passed

    if not tail_ok:
        result.checks = checks
        result.verdict = VERDICT_TAIL
        return result

    result.checks = checks
    result.verdict = VERDICT_PASSED

    if l1a_verdict is not None and l1a_verdict != "L1A_PASSED":
        result.verdict = VERDICT_PREREQUISITE
        result.limitations.append(
            f"前置未满足：L1a 终态为 {l1a_verdict}。本次蒙特卡洛仅用于刻画该路径的分布，"
            "**不构成放行结论**。"
        )
    if l1b_verdict is not None and l1b_verdict not in {"L1B_PASSED", VERDICT_PREREQUISITE}:
        result.limitations.append(
            f"提醒：L1b 终态为 {l1b_verdict}，精验未放行；此处分布不代表可执行策略的风险。"
        )

    result.limitations.extend(
        [
            "stationary bootstrap 假设收益序列平稳：它保留块内相关结构，但**不建模波动率聚集的时变**，"
            "对结构性断点（制度变更、流动性枯竭）无能为力。",
            "重抽样只重排历史日收益，**不会产生历史上没出现过的亏损幅度**——"
            "真实尾部可能比这里的估计更厚。",
            "块长是最主观的参数，已给出敏感性区间；若四个块长下结论方向不一致，"
            "应视为结论不稳而非择一采用。",
        ]
    )
    return result


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def render_l2_markdown(result: L2Result, *, title: str = "") -> str:
    dist = result.distribution
    lines: list[str] = []
    lines.append(f"# L2 蒙特卡洛：{title or result.hypothesis_id}")
    lines.append("")
    lines.append(f"- 假设编号：`{result.hypothesis_id}`")
    lines.append(f"- **终态：`{result.verdict}`**")
    lines.append(f"- 观测数：{result.observations} 个交易日收益（估值缺失日 {result.missing_valuation_days} 个）")
    if result.actual_return is not None:
        lines.append(
            f"- 实际路径：累计收益 **{result.actual_return:+.4f}**，"
            f"最大回撤 **{result.actual_max_drawdown:.4f}**"
        )
    lines.append(f"- 抽样设定：{result.spec.get('replications')} 次，"
                 f"主块长 {result.spec.get('mean_block_length')} 日，"
                 f"种子 {result.spec.get('seed')}，摘要 `{result.spec.get('digest', '')[:16]}`")
    lines.append("")
    if result.verdict == VERDICT_PREREQUISITE:
        lines.append("> **前置未满足**：L1a 未放行该假设。以下分布只用于刻画这条路径的风险，"
                     "**不构成放行结论**。")
        lines.append("")

    if dist:
        lines.append("## 一、重抽样后的收益分布")
        lines.append("")
        lines.append("| 统计量 | 取值 |")
        lines.append("|---|---|")
        # 收益类给正负号；比率/倍数类不带号——"+0.0910 的负收益占比"读起来是错的
        signed_keys = {
            "terminal_median",
            "terminal_mean",
            "terminal_p5",
            "terminal_p25",
            "terminal_p95",
            "cvar5",
        }
        for key, label in (
            ("terminal_median", "期末收益中位数"),
            ("terminal_mean", "期末收益均值"),
            ("terminal_p5", "期末收益 5% 分位"),
            ("terminal_p25", "期末收益 25% 分位"),
            ("terminal_p95", "期末收益 95% 分位"),
            ("cvar5", "CVaR5（最差 5% 平均）"),
            ("loss_probability", "负收益路径占比"),
            ("mdd_median", "最大回撤中位数"),
            ("mdd_p95", "最大回撤 95% 分位"),
            ("mdd_max", "最大回撤最大值"),
            ("skew", "偏度"),
            ("excess_kurtosis", "超额峰度"),
        ):
            value = dist.get(key)
            if value is None:
                cell = "—"
            elif key in signed_keys:
                cell = f"{value:+.4f}"
            else:
                cell = f"{value:.4f}"
            lines.append(f"| {label} | {cell} |")
        lines.append("")
        lines.append(
            f"- 实际收益在分布中的分位：**{_percentile_of(dist, result.actual_return):.1f}%**"
        )
        lines.append("")

    if result.trimmed:
        trimmed = result.trimmed
        lines.append("## 二、极端日依赖（E1）")
        lines.append("")
        lines.append(
            f"- 剔除最好的 {trimmed['removed_days']} 个交易日（{trimmed['fraction']:.1%}）后，"
            f"累计收益 {trimmed['original_return']:+.4f} → **{trimmed['trimmed_return']:+.4f}**"
        )
        lines.append(
            f"- 这 {trimmed['removed_days']} 天合计贡献 {trimmed['removed_total_return']:+.4f}"
        )
        lines.append(f"- 被剔除的日收益（前 10）：{trimmed['top_day_returns']}")
        lines.append("")

    if result.repeatable_ratio is not None:
        lines.append("## 三、可重复性（E2）")
        lines.append("")
        lines.append(
            f"- 重抽样收益中位数 / 实际收益 = **{result.repeatable_ratio:.4f}**"
            f"（阈值 {result.spec.get('min_repeatable_ratio')}）"
        )
        lines.append("")

    if result.trim_profile:
        lines.append("## 三之二、剔除剖面（依赖到什么程度）")
        lines.append("")
        lines.append("| 剔除最好的比例 | 天数 | 剔除后累计收益 | 被剔除日合计贡献 |")
        lines.append("|---|---|---|---|")
        for row in result.trim_profile:
            lines.append(
                f"| {row['fraction']:.1%} | {row['removed_days']} | "
                f"{row['trimmed_return']:+.4f} | {row['removed_total_return']:+.4f} |"
            )
        lines.append("")

    if result.block_sensitivity:
        lines.append("## 四、块长敏感性")
        lines.append("")
        lines.append("| 平均块长 | 收益中位 | 5% 分位 | CVaR5 | 回撤 95% | 可重复性 | 负收益占比 |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in result.block_sensitivity:
            ratio = row.get("repeatable_ratio")
            lines.append(
                f"| {row['mean_block_length']} | {row['terminal_median']:+.4f} | "
                f"{row['terminal_p5']:+.4f} | {row['cvar5']:+.4f} | "
                f"{row['mdd_p95']:.4f} | {'—' if ratio is None else f'{ratio:.4f}'} | "
                f"{row['loss_probability']:.4f} |"
            )
        lines.append("")
        stable = result.block_length_stable
        lines.append(
            f"- 四个块长下可重复性是否都达标：**{'是' if stable else '否'}**"
            + ("" if stable else "（结论不稳，不应择一采用）")
        )
        lines.append("")

    lines.append("## 五、判定明细")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for check in result.checks:
        mark = {True: "PASS", False: "FAIL", None: "不可判定"}.get(check.get("passed"))
        lines.append(f"| {check['name']} | {mark} | {check['detail']} |")
    lines.append("")

    lines.append("## 六、口径与限制")
    lines.append("")
    for item in result.limitations:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _percentile_of(distribution: dict[str, Any], value: float | None) -> float:
    """用已知分位点做线性插值，估计 ``value`` 在分布中的位置（近似）。"""
    if value is None:
        return float("nan")
    points = [
        (5.0, distribution.get("terminal_p5")),
        (25.0, distribution.get("terminal_p25")),
        (50.0, distribution.get("terminal_median")),
        (95.0, distribution.get("terminal_p95")),
    ]
    known = [(p, v) for p, v in points if v is not None]
    if len(known) < 2:
        return float("nan")
    if value <= known[0][1]:
        return known[0][0]
    if value >= known[-1][1]:
        return known[-1][0]
    for (left_p, left_v), (right_p, right_v) in zip(known, known[1:]):
        if left_v <= value <= right_v:
            span = right_v - left_v
            if span == 0:
                return left_p
            return left_p + (value - left_v) / span * (right_p - left_p)
    return float("nan")
