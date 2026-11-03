"""四类检查点插件：身份、限额、状态、字段。

插件化的意义
------------
计划要求「四类检查点插件化」。这不是为了好看，而是为了让**新增检查不改网关**：
网关只负责按顺序调用、把「不通过」翻译成原因码；新增一类约束（例如"该策略已下线"）
只需写一个 ``Checkpoint`` 实现并登记，``gateway.py`` 一行不动。

统一的失败语义
--------------
每个检查点只返回三种结果之一：

- ``允许``：本检查点没有理由拒绝；
- ``拒绝``：给出 ``REASON_CODES`` 里的码；
- ``无法校验``：判断所依赖的数据缺失/过期 → 同样按**不通过**处理。

第三种是最容易被写成"先放过"的地方。本模块把它统一归到 ``checkpoint_unverifiable``，
并在 ``Decision.unverifiable=True`` 上留痕，方便统计「有多少拒绝其实是因为拿不到数据」
——这类拒绝量高，说明数据链路有问题，而不是策略有问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence

from deepquant.risk.reason_codes import (
    CATEGORY_FIELD,
    CATEGORY_IDENTITY,
    CATEGORY_LIMIT,
    CATEGORY_STATE,
    REQUIRED_CATEGORIES,
    reason,
)


@dataclass(frozen=True)
class OperationContext:
    """一次待校验的操作。字段刻意只放"网关能拿到的东西"，不塞业务对象。"""

    operation: str
    method: str
    path: str
    operator: str | None
    session: str | None = None
    payload: Mapping[str, Any] | None = None
    query: Mapping[str, str] = field(default_factory=dict)
    body_bytes: int = 0
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def get(self, key: str) -> Any:
        if not self.payload:
            return None
        return self.payload.get(key)


@dataclass(frozen=True)
class Decision:
    """单个检查点的判定。"""

    checkpoint: str
    category: str
    allowed: bool
    code: str | None = None
    message: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    unverifiable: bool = False

    @property
    def status_code(self) -> int:
        if self.code is None:
            return 200
        return reason(self.code).status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "category": self.category,
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
            "unverifiable": self.unverifiable,
        }


def allow(checkpoint: str, category: str) -> Decision:
    return Decision(checkpoint=checkpoint, category=category, allowed=True)


def reject(
    checkpoint: str,
    category: str,
    code: str,
    *,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
    unverifiable: bool = False,
) -> Decision:
    spec = reason(code)  # 码不存在会在此报错，避免造出无法检索的临时码
    return Decision(
        checkpoint=checkpoint,
        category=category,
        allowed=False,
        code=spec.code,
        message=message or spec.summary,
        details=dict(details or {}),
        unverifiable=unverifiable,
    )


class Checkpoint(Protocol):
    """检查点协议。"""

    name: str
    category: str

    def applies_to(self, operation: str) -> bool: ...

    def check(self, ctx: OperationContext) -> Decision: ...


class _BaseCheckpoint:
    name: str = "base"
    category: str = ""
    #: 若为 ``None`` 表示对全部操作生效；否则只对这些 op_id 生效。
    operations: tuple[str, ...] | None = None

    def applies_to(self, operation: str) -> bool:
        return self.operations is None or operation in self.operations


# --------------------------------------------------------------------------- #
# 1. 身份未核实
# --------------------------------------------------------------------------- #
class IdentityCheckpoint(_BaseCheckpoint):
    """身份检查：操作人必须存在；配置了允许名单时必须命中。

    为什么不默认拒绝"未知操作人"
    ----------------------------
    计划要求 fail-closed，但同一份计划的风险登记又明确要求「先告警不拦截运行两周
    再转强制」。这两条不矛盾：**默认值由灰度模式（observe）承担，而不是由检查点
    偷偷放水**。检查点始终判定"应当拒绝"，是否真的拦下来由网关模式决定——
    这样观察期采集到的 would_reject 计数就是上线后真实的拦截量，可直接用于决策。
    """

    name = "identity"
    category = CATEGORY_IDENTITY

    def __init__(self, allowlist: Iterable[str] | None = None) -> None:
        self.allowlist = frozenset(a.strip() for a in allowlist) if allowlist else None

    def check(self, ctx: OperationContext) -> Decision:
        operator = (ctx.operator or "").strip()
        if not operator:
            return reject(self.name, self.category, "identity_missing")
        if self.allowlist is not None and operator not in self.allowlist:
            return reject(
                self.name,
                self.category,
                "identity_not_allowed",
                details={"operator": operator, "allowlist_size": len(self.allowlist)},
            )
        return allow(self.name, self.category)


# --------------------------------------------------------------------------- #
# 2. 限额超限
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskLimits:
    """资源限额。刻意只做**上界保护**，不对业务口径表达意见。

    取值理由：这些值都比"正常研究请求"宽一到两个数量级，只拦明显误输入
    （例如把 100 万填成 1000 亿）或会把机器跑爆的请求。把限额设在业务口径附近，
    就会变成"用风控否决研究设计"——那不是风控该做的事。
    """

    max_body_bytes: int = 2 * 1024 * 1024
    min_initial_cash: float = 1_000.0
    max_initial_cash: float = 1_000_000_000.0
    max_date_span_days: int = 365 * 30


class LimitCheckpoint(_BaseCheckpoint):
    """限额检查：请求体体积、初始资金上下界、回测区间跨度。"""

    name = "limit"
    category = CATEGORY_LIMIT

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def check(self, ctx: OperationContext) -> Decision:
        if ctx.body_bytes > self.limits.max_body_bytes:
            return reject(
                self.name,
                self.category,
                "limit_body_too_large",
                details={"body_bytes": ctx.body_bytes, "max": self.limits.max_body_bytes},
            )

        cash = ctx.get("initial_cash")
        if cash is not None:
            if not isinstance(cash, (int, float)) or isinstance(cash, bool):
                # 类型错误交给字段校验与 schema，不在限额里替它判断
                pass
            elif not (self.limits.min_initial_cash <= float(cash) <= self.limits.max_initial_cash):
                return reject(
                    self.name,
                    self.category,
                    "limit_cash_range",
                    details={
                        "initial_cash": float(cash),
                        "min": self.limits.min_initial_cash,
                        "max": self.limits.max_initial_cash,
                    },
                )

        span = _date_span_days(ctx.get("start_date"), ctx.get("end_date"))
        if span is not None and span > self.limits.max_date_span_days:
            return reject(
                self.name,
                self.category,
                "limit_date_span",
                details={"span_days": span, "max": self.limits.max_date_span_days},
            )

        return allow(self.name, self.category)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _date_span_days(start: Any, end: Any) -> int | None:
    """区间跨度；任一端缺失或不可解析时返回 ``None``（不臆造跨度）。"""
    s, e = _parse_date(start), _parse_date(end)
    if s is None or e is None:
        return None
    return abs((e - s).days)


# --------------------------------------------------------------------------- #
# 3. 状态异常
# --------------------------------------------------------------------------- #
#: 各操作需要校验状态的字段与校验方式。
#: ``dataset`` → 逐个调 ``dataset_lookup``；``strategy`` → 调 ``strategy_lookup``；
#: ``runs`` → 逐个调 ``run_lookup``。
_STATE_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "backtest.run": (("dataset_symbol", "dataset"), ("strategy_id", "strategy")),
    "portfolio.backtest": (("dataset_symbols", "datasets"),),
    "backtest.compare": (("run_ids", "runs"),),
}


class StateCheckpoint(_BaseCheckpoint):
    """状态检查：数据集是否可用、策略是否登记、引用的运行记录是否存在。

    依赖注入而非直接 import
    -----------------------
    ``dataset_lookup`` / ``strategy_lookup`` / ``run_lookup`` 由调用方（API 装配层）
    传入。这样风控层不依赖 API 层的异常类型与数据库细节，也能在测试里用假实现
    精确构造"登记了但文件坏了""策略被下线"这类场景。
    """

    name = "state"
    category = CATEGORY_STATE

    def __init__(
        self,
        *,
        dataset_lookup=None,
        strategy_lookup=None,
        run_lookup=None,
        rules: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    ) -> None:
        self.dataset_lookup = dataset_lookup
        self.strategy_lookup = strategy_lookup
        self.run_lookup = run_lookup
        self.rules = dict(rules or _STATE_RULES)

    def applies_to(self, operation: str) -> bool:
        return operation in self.rules

    def check(self, ctx: OperationContext) -> Decision:
        for field_name, kind in self.rules.get(ctx.operation, ()):
            value = ctx.get(field_name)
            if value is None:
                continue  # 缺失由字段检查点负责，这里不重复报同一个问题
            if kind == "datasets":
                items = value if isinstance(value, (list, tuple)) else [value]
                for item in items:
                    decision = self._check_dataset(item)
                    if not decision.allowed:
                        return decision
            elif kind == "dataset":
                decision = self._check_dataset(value)
                if not decision.allowed:
                    return decision
            elif kind == "strategy":
                decision = self._check_strategy(value)
                if not decision.allowed:
                    return decision
            elif kind == "runs":
                items = value if isinstance(value, (list, tuple)) else [value]
                for item in items:
                    decision = self._check_run(item)
                    if not decision.allowed:
                        return decision
        return allow(self.name, self.category)

    # -- 单个对象的检查 -------------------------------------------------- #
    def _check_dataset(self, symbol: Any) -> Decision:
        if self.dataset_lookup is None:
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message="未注入数据集查询能力，无法确认数据集状态",
                details={"symbol": symbol},
                unverifiable=True,
            )
        try:
            found = self.dataset_lookup(str(symbol))
        except Exception as exc:  # noqa: BLE001 - 任何异常都不允许乐观放行
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message=f"数据集状态查询失败：{type(exc).__name__}",
                details={"symbol": symbol},
                unverifiable=True,
            )
        if found is None:
            return reject(
                self.name,
                self.category,
                "state_dataset_unavailable",
                details={"symbol": symbol},
            )
        return allow(self.name, self.category)

    def _check_strategy(self, strategy_id: Any) -> Decision:
        if self.strategy_lookup is None:
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message="未注入策略注册表，无法确认策略是否对外提供",
                details={"strategy_id": strategy_id},
                unverifiable=True,
            )
        try:
            registry = self.strategy_lookup()
        except Exception as exc:  # noqa: BLE001
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message=f"策略注册表查询失败：{type(exc).__name__}",
                details={"strategy_id": strategy_id},
                unverifiable=True,
            )
        ids = set(registry.keys()) if isinstance(registry, Mapping) else set(registry)
        if str(strategy_id) not in ids:
            return reject(
                self.name,
                self.category,
                "state_strategy_unavailable",
                details={"strategy_id": strategy_id, "available": sorted(ids)},
            )
        return allow(self.name, self.category)

    def _check_run(self, run_id: Any) -> Decision:
        if self.run_lookup is None:
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message="未注入运行记录查询能力，无法确认 run_id 是否存在",
                details={"run_id": run_id},
                unverifiable=True,
            )
        try:
            found = self.run_lookup(str(run_id))
        except Exception as exc:  # noqa: BLE001
            return reject(
                self.name,
                self.category,
                "checkpoint_unverifiable",
                message=f"运行记录查询失败：{type(exc).__name__}",
                details={"run_id": run_id},
                unverifiable=True,
            )
        if found is None:
            return reject(
                self.name,
                self.category,
                "state_run_missing",
                details={"run_id": run_id},
            )
        return allow(self.name, self.category)


# --------------------------------------------------------------------------- #
# 4. 关键字段缺失
# --------------------------------------------------------------------------- #
#: 各操作的必需字段。与 Pydantic schema 的关系：schema 负责类型与范围（422），
#: 本表负责"到底缺了什么"（可检索的原因码 + 字段名），两者不互相替代。
REQUIRED_FIELDS_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "backtest.run": ("dataset_symbol", "strategy_id", "strategy_params", "initial_cash"),
    "portfolio.backtest": ("dataset_symbols", "initial_cash"),
    "backtest.compare": ("run_ids",),
    "dataset.list": (),
    "strategy.list": (),
    "audit.replay": (),
}


class FieldCheckpoint(_BaseCheckpoint):
    """字段检查：必需字段必须存在且非空。"""

    name = "field"
    category = CATEGORY_FIELD

    def __init__(self, required: Mapping[str, Sequence[str]] | None = None) -> None:
        self.required = dict(required or REQUIRED_FIELDS_BY_OPERATION)

    def applies_to(self, operation: str) -> bool:
        return operation in self.required

    def check(self, ctx: OperationContext) -> Decision:
        if not ctx.payload:
            missing = list(self.required.get(ctx.operation, ()))
            if missing:
                return reject(
                    self.name,
                    self.category,
                    "field_missing",
                    details={"missing": missing, "reason": "请求体为空或不是 JSON 对象"},
                )
            return allow(self.name, self.category)

        missing: list[str] = []
        empty: list[str] = []
        for name in self.required.get(ctx.operation, ()):
            if name not in ctx.payload:
                missing.append(name)
                continue
            value = ctx.payload[name]
            # 空字符串与空列表视为"写了但没写"。**空字典不算**：``strategy_params={}``
            # 在本系统里表示"全部用默认参数"，把它判成缺失会误拒合法请求——
            # 风控误拒正常业务比漏掉一次检查更难被发现（调用方只会以为系统坏了）。
            if value is None or (isinstance(value, (str, list)) and len(value) == 0):
                empty.append(name)

        if missing:
            return reject(
                self.name, self.category, "field_missing", details={"missing": missing}
            )
        if empty:
            return reject(self.name, self.category, "field_empty", details={"empty": empty})
        return allow(self.name, self.category)


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def default_checkpoints(
    *,
    allowlist: Iterable[str] | None = None,
    limits: RiskLimits | None = None,
    dataset_lookup=None,
    strategy_lookup=None,
    run_lookup=None,
) -> tuple[Checkpoint, ...]:
    """按计划的四类顺序装配默认检查点。

    顺序有意义：先确认"你是谁"，再看"请求是否越界"，再看"对象是否存在"，
    最后看"字段是否齐全"。若顺序反过来，一个缺字段的请求会先被报成"状态异常"，
    排查时会把字段问题误读成数据问题。
    """
    return (
        IdentityCheckpoint(allowlist),
        LimitCheckpoint(limits),
        StateCheckpoint(
            dataset_lookup=dataset_lookup,
            strategy_lookup=strategy_lookup,
            run_lookup=run_lookup,
        ),
        FieldCheckpoint(),
    )


def category_coverage(checkpoints: Sequence[Checkpoint]) -> dict[str, Any]:
    """四类检查点的覆盖率（分母固定为计划的四个类别）。"""
    present = {c.category for c in checkpoints}
    missing = [c for c in REQUIRED_CATEGORIES if c not in present]
    return {
        "required_categories": list(REQUIRED_CATEGORIES),
        "present": sorted(present),
        "missing": missing,
        "n_checkpoints": len(checkpoints),
        "ratio": (len(REQUIRED_CATEGORIES) - len(missing)) / len(REQUIRED_CATEGORIES),
    }


__all__ = [
    "Checkpoint",
    "Decision",
    "FieldCheckpoint",
    "IdentityCheckpoint",
    "LimitCheckpoint",
    "OperationContext",
    "REQUIRED_FIELDS_BY_OPERATION",
    "RiskLimits",
    "StateCheckpoint",
    "allow",
    "category_coverage",
    "default_checkpoints",
    "reject",
]
