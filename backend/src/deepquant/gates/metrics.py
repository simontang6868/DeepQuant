"""诚实指标：算不出来就写「不可计算」，绝不填 0。

《推进计划》表 9 的规范原文：「凡样本不足、无法计算的指标，必须显式标注
"不可计算"，禁止填充看似合理的数值——这是五级闸门公信力的基础。」

实现上的两条硬约束
------------------
1. ``Metric.value`` 为 ``None`` 时，``status`` 必须是 ``not_computable`` 且带 ``reason``。
   构造时校验，写不出理由的"不可计算"会被拒绝——理由本身是证据的一部分。
2. 边界判定只接受 ``status == "ok"`` 的指标。``not_computable`` 既不通过也不否决，
   而是把整个闸门降级为 ``INCONCLUSIVE``：**没判断**不等于**没问题**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

OK = "ok"
NOT_COMPUTABLE = "not_computable"


class MetricError(ValueError):
    """指标构造不自洽（例如没有数值却没给理由）。"""


@dataclass(frozen=True)
class Metric:
    """一个可入报告的指标。"""

    name: str
    value: float | None
    unit: str = ""
    status: str = OK
    reason: str = ""
    #: 该指标依赖的样本量（如成交笔数），用于解释"为什么算不出"。
    samples: int | None = None

    def __post_init__(self) -> None:
        if self.value is None and self.status != NOT_COMPUTABLE:
            raise MetricError(f"指标 {self.name} 无数值却标记为 {self.status!r}（应为 not_computable）")
        if self.status == NOT_COMPUTABLE and not self.reason.strip():
            raise MetricError(f"指标 {self.name} 标为不可计算但没写理由")
        if self.status not in (OK, NOT_COMPUTABLE):
            raise MetricError(f"指标 {self.name} 状态非法：{self.status!r}")

    @property
    def available(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "reason": self.reason,
            "samples": self.samples,
        }


def ok(name: str, value: float, unit: str = "", *, samples: int | None = None) -> Metric:
    return Metric(name=name, value=float(value), unit=unit, samples=samples)


def unavailable(name: str, reason: str, *, samples: int | None = None) -> Metric:
    return Metric(name=name, value=None, status=NOT_COMPUTABLE, reason=reason, samples=samples)


@dataclass(frozen=True)
class BoundCheck:
    """一条边界判定：名字 / 实际值 / 门槛 / 方向 / 结论。"""

    name: str
    actual: float | None
    threshold: float | None
    direction: str  # ">=" 或 "<="
    passed: bool | None  # None 表示不可判定
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "actual": self.actual,
            "threshold": self.threshold,
            "direction": self.direction,
            "passed": self.passed,
            "reason": self.reason,
        }


def check_bound(metric: Metric, *, threshold: float, name: str, direction: str) -> BoundCheck:
    """按方向比较；指标不可计算时返回 ``passed=None``（既非通过也非否决）。"""
    if direction not in (">=", "<="):
        raise MetricError(f"未知比较方向 {direction!r}")
    if not metric.available or metric.value is None:
        return BoundCheck(
            name=name,
            actual=None,
            threshold=threshold,
            direction=direction,
            passed=None,
            reason=f"{metric.name} 不可计算：{metric.reason}",
        )
    passed = metric.value >= threshold if direction == ">=" else metric.value <= threshold
    return BoundCheck(
        name=name,
        actual=metric.value,
        threshold=threshold,
        direction=direction,
        passed=bool(passed),
    )


def summarise(checks: Sequence[BoundCheck]) -> tuple[str, list[str]]:
    """把一组边界判定汇总成 ``(结论, 失败说明)``。

    结论三态：``pass`` / ``fail`` / ``undetermined``。
    ``undetermined`` 优先于 ``pass``——只要有一条边界没能力判定，就不能说"通过"。
    """
    failures = [
        f"{item.name}：实际 {item.actual}，要求 {item.direction} {item.threshold}"
        for item in checks
        if item.passed is False
    ]
    if failures:
        return "fail", failures
    if any(item.passed is None for item in checks):
        reasons = [f"{item.name}：{item.reason}" for item in checks if item.passed is None]
        return "undetermined", reasons
    return "pass", []


def metrics_digest(metrics: Mapping[str, Metric]) -> str:
    """指标集合的摘要（按名字排序，供报告引用具体一次评估）。"""
    import hashlib
    import json

    payload = {name: metric.to_dict() for name, metric in sorted(metrics.items())}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
