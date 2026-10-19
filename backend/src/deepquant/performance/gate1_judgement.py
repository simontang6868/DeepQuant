"""Gate 1 判定规则（预登记第 10 节）的机械实现。

为什么单独成模块，而不是写在脚本里：

1. **判据必须在看到结果之前就已经是可执行代码。** 写在脚本里的 if/else 也能跑，
   但它和「结果」在同一处产生，事后改动无人能发现；独立模块 + 测试可以先冻结。
2. **阈值只能有一处。** 预登记写 `mean(d) >= 0.0030`、`t >= 2.0`、`>= 8/12 组为正`，
   代码里若出现第二份字面量，改了一处忘另一处就是协议失效。
3. **t 分布不能近似。** 本机没有 scipy，也不能为此加依赖。用正态近似会让
   p 值在 df=48 时系统性偏小（t 分布尾部更厚），直接影响 BH 校正下「有几组能存活」。
   故此处自带正则化不完全贝塔函数计算精确 p 值。

三档判定（预登记 10.2，逐字实现）：

- **成功**：`mean(d) >= 0.0030` **且** `t >= 2.0` **且** 12 组敏感性中 `>= 8` 组 `mean(d) > 0`
- **失败**：`mean(d) <= 0` **或** `t < 1.0` **或** 12 组中 `<= 4` 组为正
- **证据不足**：其余全部情形

三档互斥是构造性的：成功要求 `mean > 0` 且 `t >= 2`，失败要求 `mean <= 0` 或 `t < 1`，
二者不可能同时成立；成功要求 `>= 8` 组为正、失败要求 `<= 4` 组为正，中间的 5~7 组两边都不满足。
``judge()`` 里保留了互斥断言——若未来有人改了阈值导致重叠，会直接抛错而不是悄悄取先命中的那档。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


# --------------------------------------------------------------------------- #
# 阈值：与 docs/GATE1_PREREGISTRATION.md 第 10.2 节一一对应
# --------------------------------------------------------------------------- #

SUCCESS_MEAN_D = 0.0030          # 月均超越基准 0.30pp
SUCCESS_T_STAT = 2.0
SUCCESS_MIN_POSITIVE = 8         # 12 组中至少 8 组为正

FAILURE_MEAN_D = 0.0             # mean(d) <= 0 即失败
FAILURE_T_STAT = 1.0             # t < 1.0 即失败
FAILURE_MAX_POSITIVE = 4         # 12 组中 <= 4 组为正即失败

N_SENSITIVITY = 12               # 预登记第 11 节列明的组数

VERDICT_SUCCESS = "success"
VERDICT_FAILURE = "failure"
VERDICT_INSUFFICIENT = "insufficient"

VERDICT_LABELS = {
    VERDICT_SUCCESS: "成功",
    VERDICT_FAILURE: "失败",
    VERDICT_INSUFFICIENT: "证据不足",
}


# --------------------------------------------------------------------------- #
# t 分布（无 scipy）
# --------------------------------------------------------------------------- #


def _betacf(a: float, b: float, x: float, *, max_iter: int = 300, eps: float = 3e-14) -> float:
    """不完全贝塔函数的连分数展开（Lentz 算法）。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < eps:
        d = eps
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < eps:
            d = eps
        c = 1.0 + aa / c
        if abs(c) < eps:
            c = eps
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < eps:
            d = eps
        c = 1.0 + aa / c
        if abs(c) < eps:
            c = eps
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p(t_stat: float, df: int) -> float:
    """单样本 t 检验的双侧 p 值。

    对 ``H0: E[d] = 0``，``P(|T_df| >= |t|) = I_{df/(df+t^2)}(df/2, 1/2)``。
    """
    if df < 1:
        raise ValueError(f"自由度必须 >= 1，收到 {df}")
    if not math.isfinite(t_stat):
        raise ValueError(f"t 统计量必须有限，收到 {t_stat}")
    t2 = t_stat * t_stat
    return _betai(df / 2.0, 0.5, df / (df + t2))


# --------------------------------------------------------------------------- #
# 配对统计
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairedStats:
    """月度配对超额收益 ``d_t = r_strategy,t - r_benchmark,t`` 的统计量。"""

    n: int
    mean_d: float
    std_d: float
    t_stat: float
    p_value: float
    win_ratio: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n": self.n,
            "mean_d": self.mean_d,
            "std_d": self.std_d,
            "t_stat": self.t_stat,
            "p_value": self.p_value,
            "win_ratio": self.win_ratio,
        }


def paired_stats(diffs: Sequence[float]) -> PairedStats:
    """由配对差额序列算 ``mean / t / p / win_ratio``。

    ``std_d == 0`` 时 t 统计量无定义（分母为 0），返回 ``inf``/``-inf`` 而不是
    编一个有限数——全部相同的差额要么是常数套利（不存在），要么是数据出错。
    """
    values = [float(v) for v in diffs if v is not None]
    if not values:
        raise ValueError("配对差额为空：没有观测就没有统计量")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("配对差额含非有限值")

    n = len(values)
    mean = sum(values) / n
    if n == 1:
        return PairedStats(n=1, mean_d=mean, std_d=0.0, t_stat=float("nan"),
                           p_value=float("nan"), win_ratio=1.0 if mean > 0 else 0.0)

    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(var)
    win = sum(1 for v in values if v > 0) / n

    if std == 0.0:
        t = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
        p = 0.0 if t != 0.0 else 1.0
    else:
        t = mean / (std / math.sqrt(n))
        p = student_t_two_sided_p(t, n - 1)

    return PairedStats(n=n, mean_d=mean, std_d=std, t_stat=t, p_value=p, win_ratio=win)


def benjamini_hochberg(p_values: Sequence[float], *, alpha: float = 0.05) -> list[float]:
    """BH 校正，返回与输入同序的 q 值。

    实现要点：先按 p 升序，``q_(i) = min_{j>=i} (p_(j) * m / j)`` —— 必须取
    右侧最小值来保证单调，否则会出现「p 更小但 q 更大」的自相矛盾输出。
    """
    m = len(p_values)
    if m == 0:
        return []
    for p in p_values:
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"p 值必须在 [0, 1]，收到 {p}")

    order = sorted(range(m), key=lambda i: p_values[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, p_values[idx] * m / rank)
        q[idx] = min(running, 1.0)
    del alpha  # alpha 不参与 q 的计算；是否显著由调用方比较 q <= alpha
    return q


# --------------------------------------------------------------------------- #
# 敏感性
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SensitivityOutcome:
    """一组敏感性的结果。``name`` 用预登记的 S1~S12 编号。"""

    name: str
    variable: str
    value: str
    stats: PairedStats
    q_value: float | None = None
    note: str | None = None

    @property
    def is_positive(self) -> bool:
        return self.stats.mean_d > 0


def attach_bh(sensitivity: Sequence[SensitivityOutcome]) -> tuple[SensitivityOutcome, ...]:
    """给 12 组敏感性挂 BH 校正后的 q 值（保持输入顺序）。"""
    qs = benjamini_hochberg([s.stats.p_value for s in sensitivity])
    return tuple(
        SensitivityOutcome(name=s.name, variable=s.variable, value=s.value,
                           stats=s.stats, q_value=q, note=s.note)
        for s, q in zip(sensitivity, qs)
    )


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Judgement:
    """判定结果。``rule_hits`` 记录命中了哪几条规则，便于报告逐条引用。"""

    verdict: str
    n_positive_sensitivity: int
    rule_hits: tuple[str, ...] = ()
    insufficient_flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return VERDICT_LABELS[self.verdict]

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "label": self.label,
            "n_positive_sensitivity": self.n_positive_sensitivity,
            "rule_hits": list(self.rule_hits),
            "insufficient_flags": list(self.insufficient_flags),
        }


def judge(
    primary: PairedStats,
    *,
    n_positive_sensitivity: int,
    insufficient_flags: Sequence[str] = (),
) -> Judgement:
    """按预登记 10.2 判三档。

    Args:
        primary: 主实验的配对统计量（OOS 48 个月）。
        n_positive_sensitivity: 12 组敏感性中 OOS ``mean(d) > 0`` 的组数。
        insufficient_flags: 已成立的「证据不足」触发项（预登记 10.2 末三条），
            由调用方判定后传入——例如「排除创业板后符号翻转」。

    Note:
        **协议未规定的一处，在此显式声明解读**：预登记 10.2 写「证据不足 = 不属于上述
        两档，**或** 任一情形发生」，字面上没说这些情形与「失败」档谁优先。本实现取
        **失败 > 证据不足 > 成功** 的优先级，即：

        - 满足失败条件时一律判失败，``insufficient_flags`` 不改变它。理由：近似项
          （涨跌停偏松、停牌近似）方向都是让策略**看起来更好**；在这种偏差下仍然显著
          跑输，真实无效的证据只会更强，不该被「近似不可靠」稀释。
        - 满足成功条件但 ``insufficient_flags`` 非空 → **降级为证据不足**。理由：
          协议明确要求「无法实现的约束被证明有实质影响时不得下结论」，而成功结论
          恰恰最需要这个约束。
        - 其余 → 证据不足。

        这个方向是保守的：``flags`` 只能降级成功，不能升级失败，也不会把证据不足
        变成任何一档。它写在代码里而不是文档里，是为了避免事后按结果挑解读。
    """
    if not (0 <= n_positive_sensitivity <= N_SENSITIVITY):
        raise ValueError(
            f"n_positive_sensitivity 必须在 0~{N_SENSITIVITY}，收到 {n_positive_sensitivity}"
        )

    success_hits: list[str] = []
    if primary.mean_d >= SUCCESS_MEAN_D:
        success_hits.append(f"mean(d)={primary.mean_d:.6f} >= {SUCCESS_MEAN_D}")
    if primary.t_stat >= SUCCESS_T_STAT:
        success_hits.append(f"t={primary.t_stat:.4f} >= {SUCCESS_T_STAT}")
    if n_positive_sensitivity >= SUCCESS_MIN_POSITIVE:
        success_hits.append(
            f"敏感性正组数={n_positive_sensitivity} >= {SUCCESS_MIN_POSITIVE}"
        )
    is_success = len(success_hits) == 3

    failure_hits: list[str] = []
    if primary.mean_d <= FAILURE_MEAN_D:
        failure_hits.append(f"mean(d)={primary.mean_d:.6f} <= {FAILURE_MEAN_D}")
    if primary.t_stat < FAILURE_T_STAT:
        failure_hits.append(f"t={primary.t_stat:.4f} < {FAILURE_T_STAT}")
    if n_positive_sensitivity <= FAILURE_MAX_POSITIVE:
        failure_hits.append(
            f"敏感性正组数={n_positive_sensitivity} <= {FAILURE_MAX_POSITIVE}"
        )
    is_failure = bool(failure_hits)

    # 互斥是协议的性质，不是巧合：成功要求 mean>0 且 t>=2，失败要求 mean<=0 或 t<1。
    # 阈值一旦被改成重叠，这里必须炸掉，而不是悄悄按顺序取第一个命中。
    if is_success and is_failure:
        raise AssertionError(
            "判定规则重叠：同时满足成功与失败条件。阈值已被改动，协议失效。"
            f" success={success_hits} failure={failure_hits}"
        )

    if is_failure:
        # 失败优先：近似偏差都是「让策略显得更好」的方向，带偏差仍跑输则证据更强。
        return Judgement(verdict=VERDICT_FAILURE, n_positive_sensitivity=n_positive_sensitivity,
                         rule_hits=tuple(failure_hits),
                         insufficient_flags=tuple(insufficient_flags))
    if is_success and not insufficient_flags:
        return Judgement(verdict=VERDICT_SUCCESS, n_positive_sensitivity=n_positive_sensitivity,
                         rule_hits=tuple(success_hits))
    return Judgement(
        verdict=VERDICT_INSUFFICIENT,
        n_positive_sensitivity=n_positive_sensitivity,
        rule_hits=tuple(success_hits),
        insufficient_flags=tuple(insufficient_flags),
    )
