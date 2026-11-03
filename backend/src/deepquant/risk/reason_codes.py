"""拒绝原因码表：让「为什么被拒绝」成为一个可枚举、可检索的值，而不是一句日志。

为什么原因码必须是常量表
------------------------
计划 T2 的验收标准是「每次拒绝均有原因码且可在工件中检索」。如果原因码由各处
随手拼字符串（``f"数据集 {sym} 不可用"``），它就无法被统计、无法被前端分流、
也无法在 T1 工件里做等值检索——"可检索"会退化成"可以全文搜索日志"。

因此本模块把原因码收敛成 ``REASON_CODES`` 常量表，并强制：

1. 每个码必须有**类别**（对应计划的四类检查点）、**HTTP 状态码**、**一句人话**；
2. 每个类别至少有一个码（覆盖率的分母就是这四个类别）；
3. 拒绝时只能引用表里的码，拼错即报错，不允许临时造码。

状态码的分配口径
----------------
- ``403``：身份与状态类——「你不能做」或「现在不能做」，重试无意义；
- ``422``：字段与限额类——请求本身不合法，改参数可以重试；
- ``413``：体积超限，客户端需要换传法；
- ``503``：无法校验（依赖数据缺失）或网关自身故障——**按不通过处理**，
  这是「算不出就说算不出」的落地：拿不到判断依据时，不做乐观假设。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 四类检查点（计划原文顺序）。覆盖率以这四个类别为分母。
CATEGORY_IDENTITY = "identity"
CATEGORY_LIMIT = "limit"
CATEGORY_STATE = "state"
CATEGORY_FIELD = "field"

#: 内部故障类（不属于四类业务检查点，但必须有码，否则故障无法归因）。
CATEGORY_INTERNAL = "internal"

#: 计划要求的四类检查点类别（顺序固定，便于报表对齐）。
REQUIRED_CATEGORIES: tuple[str, ...] = (
    CATEGORY_IDENTITY,
    CATEGORY_LIMIT,
    CATEGORY_STATE,
    CATEGORY_FIELD,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_IDENTITY: "身份未核实",
    CATEGORY_LIMIT: "限额超限",
    CATEGORY_STATE: "状态异常",
    CATEGORY_FIELD: "关键字段缺失",
    CATEGORY_INTERNAL: "无法校验或网关故障",
}


@dataclass(frozen=True)
class ReasonCode:
    """一个拒绝原因码。"""

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


REASON_CODES: dict[str, ReasonCode] = {
    # ---------------- 身份未核实 ----------------
    "identity_missing": ReasonCode(
        code="identity_missing",
        category=CATEGORY_IDENTITY,
        status_code=403,
        summary="请求未提供操作人身份，无法确认谁在执行操作",
    ),
    "identity_not_allowed": ReasonCode(
        code="identity_not_allowed",
        category=CATEGORY_IDENTITY,
        status_code=403,
        summary="操作人不在当前允许名单内",
    ),
    # ---------------- 限额超限 ----------------
    "limit_body_too_large": ReasonCode(
        code="limit_body_too_large",
        category=CATEGORY_LIMIT,
        status_code=413,
        summary="请求体超过网关允许的体积上限",
    ),
    "limit_cash_range": ReasonCode(
        code="limit_cash_range",
        category=CATEGORY_LIMIT,
        status_code=422,
        summary="初始资金超出允许区间（只校验上下界，不评价金额是否合理）",
    ),
    "limit_date_span": ReasonCode(
        code="limit_date_span",
        category=CATEGORY_LIMIT,
        status_code=422,
        summary="回测区间跨度超过网关允许上限",
    ),
    # ---------------- 状态异常 ----------------
    "state_dataset_unavailable": ReasonCode(
        code="state_dataset_unavailable",
        category=CATEGORY_STATE,
        status_code=409,
        summary="数据集未登记或数据文件不可用",
    ),
    "state_strategy_unavailable": ReasonCode(
        code="state_strategy_unavailable",
        category=CATEGORY_STATE,
        status_code=409,
        summary="策略未登记在对外注册表中",
    ),
    "state_run_missing": ReasonCode(
        code="state_run_missing",
        category=CATEGORY_STATE,
        status_code=409,
        summary="引用的历史运行记录不存在",
    ),
    # ---------------- 关键字段缺失 ----------------
    "field_missing": ReasonCode(
        code="field_missing",
        category=CATEGORY_FIELD,
        status_code=422,
        summary="请求缺少该操作的必需字段",
    ),
    "field_empty": ReasonCode(
        code="field_empty",
        category=CATEGORY_FIELD,
        status_code=422,
        summary="必需字段存在但为空值（空字符串/空列表），等同于缺失",
    ),
    # ---------------- 无法校验 / 网关故障 ----------------
    "checkpoint_unverifiable": ReasonCode(
        code="checkpoint_unverifiable",
        category=CATEGORY_INTERNAL,
        status_code=503,
        summary="校验所依赖的数据缺失或过期，按不通过处理（不做乐观假设）",
    ),
    "gateway_internal_error": ReasonCode(
        code="gateway_internal_error",
        category=CATEGORY_INTERNAL,
        status_code=503,
        summary="检查点自身抛出异常，网关按不通过处理（fail-closed）",
    ),
}


def reason(code: str) -> ReasonCode:
    """按码取原因；码不存在直接报错（禁止临时造码）。"""
    try:
        return REASON_CODES[code]
    except KeyError as exc:
        raise KeyError(f"未登记的原因码：{code!r}（原因码必须定义在 REASON_CODES 中）") from exc


def coverage() -> dict[str, object]:
    """原因码覆盖率：四类检查点是否都有对应码。"""
    by_category: dict[str, list[str]] = {c: [] for c in REQUIRED_CATEGORIES}
    for item in REASON_CODES.values():
        by_category.setdefault(item.category, []).append(item.code)

    missing = [c for c in REQUIRED_CATEGORIES if not by_category.get(c)]
    return {
        "required_categories": list(REQUIRED_CATEGORIES),
        "n_codes": len(REASON_CODES),
        "by_category": {k: sorted(v) for k, v in by_category.items()},
        "missing_categories": missing,
        "ratio": (len(REQUIRED_CATEGORIES) - len(missing)) / len(REQUIRED_CATEGORIES),
    }


def assert_codes_complete() -> None:
    """自检：四类都有码、码名唯一、状态码合法。"""
    report = coverage()
    if report["missing_categories"]:
        raise ValueError(f"以下类别没有原因码：{report['missing_categories']}")
    for code, item in REASON_CODES.items():
        if code != item.code:
            raise ValueError(f"键与码不一致：{code!r} vs {item.code!r}")
        if not 400 <= item.status_code < 600:
            raise ValueError(f"原因码 {code} 的状态码不在 4xx/5xx：{item.status_code}")
        if not item.summary.strip():
            raise ValueError(f"原因码 {code} 缺少说明")


__all__ = [
    "CATEGORY_FIELD",
    "CATEGORY_IDENTITY",
    "CATEGORY_INTERNAL",
    "CATEGORY_LABELS",
    "CATEGORY_LIMIT",
    "CATEGORY_STATE",
    "REASON_CODES",
    "REQUIRED_CATEGORIES",
    "ReasonCode",
    "assert_codes_complete",
    "coverage",
    "reason",
]
