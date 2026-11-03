"""能力层拒绝原因码：与风控原因码**字段同构**，但独立一张表、独立一套类别标签。

为什么不复用 ``risk.reason_codes.ReasonCode``
--------------------------------------------
第一版实现直接复用了它，结果 ``to_dict()`` 在 ``CATEGORY_LABELS[self.category]``
上抛 ``KeyError: 'capability'``——复用的不只是"字段形状"，还有它的类别标签表。
两个概念各自演化，共用数据类迟早会在这种地方炸。

所以这里本地定义 ``CapabilityReason``，字段名与风控码完全一致
（``code`` / ``category`` / ``status_code`` / ``summary``），保证写进 T1 工件后
``reason_code`` 仍可等值检索；但类别、标签、码表都独立。

为什么不并进 ``risk.reason_codes.REASON_CODES``
----------------------------------------------
T2 的验收标准是「四类检查点覆盖率 100%」，分母是那四个类别。把能力层的码塞进
同一张表，会让 T2 的覆盖率报表混入不属于风控四类的条目，读报表的人很快就不再
相信那四个数字。

两张表的分工：

- 风控码回答「**这次请求能不能做**」（身份、限额、状态、字段）；
- 能力码回答「**这个能力是否开放、是否授权、契约是否匹配**」。

状态码口径
----------
- ``403``：能力关闭或未授权——重试无意义，要改配置或授权；
- ``404``：调用了不存在的能力（服务层调用拼错 cap_id）；
- ``409``：客户端钉了不兼容的版本——需要改调用方，而不是重试；
- ``503``：能力层自身故障（读到坏配置），按「算不出就说算不出」拒绝。

「契约与真实装配不一致」刻意**不设码**：那是装配期缺陷，由
``contract.assert_app_contract`` 抛 ``CapabilityContractError`` 在 CI/启动自检
阶段拦下。运行期再给它一个码，只会让"带病上线"看起来像一次普通拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 能力层类别（独立于风控四类，不参与 T2 的覆盖率分母）。
CATEGORY_CAPABILITY = "capability"

#: 类别标签。能力层只有一个类别，但仍保留标签字段以保持与风控码同构。
CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_CAPABILITY: "能力未开放、未授权或契约不匹配",
}


@dataclass(frozen=True)
class CapabilityReason:
    """一个能力层拒绝原因码。字段与 ``risk.reason_codes.ReasonCode`` 同构。"""

    code: str
    category: str
    status_code: int
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "category_label": CATEGORY_LABELS[self.category],
            "status_code": self.status_code,
            "summary": self.summary,
        }


CAPABILITY_CODES: dict[str, CapabilityReason] = {
    "capability_unregistered": CapabilityReason(
        code="capability_unregistered",
        category=CATEGORY_CAPABILITY,
        status_code=403,
        summary="该接口未登记为能力：新增业务动作必须先声明契约再上线",
    ),
    "capability_unknown": CapabilityReason(
        code="capability_unknown",
        category=CATEGORY_CAPABILITY,
        status_code=404,
        summary="调用了不存在的能力标识（cap_id 拼错或尚未登记）",
    ),
    "capability_disabled": CapabilityReason(
        code="capability_disabled",
        category=CATEGORY_CAPABILITY,
        status_code=403,
        summary="该能力已被运维显式关闭",
    ),
    "capability_not_permitted": CapabilityReason(
        code="capability_not_permitted",
        category=CATEGORY_CAPABILITY,
        status_code=403,
        summary="该操作人未被授予此能力",
    ),
    "capability_version_unsupported": CapabilityReason(
        code="capability_version_unsupported",
        category=CATEGORY_CAPABILITY,
        status_code=409,
        summary="调用方钉的契约版本与本服务当前 major 不兼容",
    ),
    "capability_internal_error": CapabilityReason(
        code="capability_internal_error",
        category=CATEGORY_CAPABILITY,
        status_code=503,
        summary="能力层无法完成判定（配置不合法或读取失败），按不通过处理",
    ),
}


def capability_reason(code: str) -> CapabilityReason:
    """按码取定义；码不存在立即抛错，禁止临时造码。"""
    try:
        return CAPABILITY_CODES[code]
    except KeyError as exc:  # pragma: no cover - 拼错码属于编程错误
        raise KeyError(
            f"未登记的能力原因码 {code!r}，可选：{sorted(CAPABILITY_CODES)}"
        ) from exc


def capability_codes() -> list[dict[str, object]]:
    """可枚举的码表（供文档与运维查询）。"""
    return [CAPABILITY_CODES[k].to_dict() for k in sorted(CAPABILITY_CODES)]


def assert_codes_complete() -> None:
    """码表自检：键与 ``code`` 一致、状态码为拒绝类、说明非空、类别标签存在。"""
    problems: list[str] = []
    for key, spec in CAPABILITY_CODES.items():
        if spec.code != key:
            problems.append(f"{key}: code 字段为 {spec.code!r}")
        if not (400 <= spec.status_code < 600):
            problems.append(f"{key}: 状态码 {spec.status_code} 不是拒绝类")
        if not spec.summary.strip():
            problems.append(f"{key}: 缺少人话说明")
        if spec.category not in CATEGORY_LABELS:
            problems.append(f"{key}: 类别 {spec.category!r} 没有标签")
    if problems:
        raise ValueError("能力原因码表不自洽：" + "；".join(problems))


__all__ = [
    "CAPABILITY_CODES",
    "CATEGORY_CAPABILITY",
    "CATEGORY_LABELS",
    "CapabilityReason",
    "assert_codes_complete",
    "capability_codes",
    "capability_reason",
]
