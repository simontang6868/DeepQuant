"""能力注册表：本仓库全部对外业务能力的唯一清单。

清单用显式常量而不是自动扫描
----------------------------
自动扫描（"凡是 ``@router.post`` 就注册成能力"）看着省事，但会得到一份**没有输入
输出契约**的清单：schema 靠从路由反推出 ``Any``，前置条件靠猜。这跟 T1 拒绝
"能记就记"是同一个理由——清单的价值在于**有人明确声明过**，而不是在于它自动填满。

所以新增业务动作的正确顺序是：先在 ``CAPABILITIES`` 里写声明（含输入输出模型、
前置条件、风控标签、版本），再写路由。顺序反了会被 ``contract.verify_app`` 抓出来。

覆盖范围与 T1 同分母
--------------------
``CAPABILITIES`` 的 6 项与 ``audit.operations.CORE_OPERATIONS`` 里 ``in_scope=True``
的 6 项**一一对应**（由 ``contract.py`` 双向核对）。不把 ``/health``、
``/backtests/{run_id}`` 这类只读查询端点纳入"核心"分母——那会把分母做大、
让 100% 变得容易，而这正是覆盖率指标最常见的自欺方式。这些端点被显式列入
``contract.EXEMPT_ROUTES`` 并写明理由，是"已知不需要"而不是"忘了"。
"""

from __future__ import annotations

from typing import Iterable

from deepquant.api.schemas import (
    BacktestCompareRequest,
    BacktestCompareResponse,
    BacktestRequest,
    BacktestResponse,
    DatasetListResponse,
    PortfolioBacktestRequest,
    PortfolioBacktestResponse,
    StrategyListResponse,
)
from deepquant.capabilities.spec import (
    TRANSPORT_HTTP,
    TRANSPORT_SERVICE,
    CapabilityContractError,
    CapabilitySpec,
)

#: 能力清单。改任何一条都要同步升 ``version``（见 ``scripts/capabilities.py lock``）。
CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        cap_id="backtest.run",
        title="单标的回测执行",
        version="1.0.0",
        summary="按数据集、策略与初始资金跑一次同步回测，落库并返回绩效与交易明细。",
        transport=TRANSPORT_HTTP,
        method="POST",
        path="/backtests",
        operation_id="backtest.run",
        input_model=BacktestRequest,
        output_model=BacktestResponse,
        required_fields=("dataset_symbol", "strategy_id", "strategy_params", "initial_cash"),
        preconditions=("identity", "limit", "state", "field", "operator_grant"),
        risk_tags=("mutating", "reads_dataset", "compute_heavy", "writes_artifact"),
    ),
    CapabilitySpec(
        cap_id="backtest.compare",
        title="历史回测结果比较",
        version="1.0.0",
        summary="读取已落库的多次回测，按指标并列比较并给出两两关联度。",
        transport=TRANSPORT_HTTP,
        method="POST",
        path="/backtests/compare",
        operation_id="backtest.compare",
        input_model=BacktestCompareRequest,
        output_model=BacktestCompareResponse,
        required_fields=("run_ids",),
        preconditions=("identity", "state", "field", "operator_grant"),
        # 用 POST 只是为了能用请求体传 run_ids 列表；它不写任何东西。
        risk_tags=("read_only", "reads_db"),
        note="POST 仅因请求体传参；语义为只读查询，不产生任何持久变更",
    ),
    CapabilitySpec(
        cap_id="portfolio.backtest",
        title="多标的组合回测",
        version="1.0.0",
        summary="多标的共享现金池、连续净值的月度调仓组合回测。",
        transport=TRANSPORT_HTTP,
        method="POST",
        path="/portfolio-backtests",
        operation_id="portfolio.backtest",
        input_model=PortfolioBacktestRequest,
        output_model=PortfolioBacktestResponse,
        required_fields=("dataset_symbols", "initial_cash"),
        preconditions=("identity", "limit", "state", "field", "operator_grant"),
        risk_tags=("mutating", "reads_dataset", "compute_heavy", "writes_artifact"),
    ),
    CapabilitySpec(
        cap_id="dataset.list",
        title="数据集枚举",
        version="1.0.0",
        summary="列出可用数据集及其覆盖区间、质量问题与不可用原因。",
        transport=TRANSPORT_HTTP,
        method="GET",
        path="/datasets",
        operation_id="dataset.list",
        output_model=DatasetListResponse,
        preconditions=("identity", "field", "operator_grant"),
        risk_tags=("read_only", "reads_dataset", "external_io"),
    ),
    CapabilitySpec(
        cap_id="strategy.list",
        title="策略注册表枚举",
        version="1.0.0",
        summary="列出对外可用的策略及参数说明（类型/默认值/约束）。",
        transport=TRANSPORT_HTTP,
        method="GET",
        path="/strategies",
        operation_id="strategy.list",
        output_model=StrategyListResponse,
        preconditions=("identity", "field", "operator_grant"),
        risk_tags=("read_only", "reads_registry"),
    ),
    CapabilitySpec(
        cap_id="audit.replay",
        title="审计工件回放",
        version="1.0.0",
        summary="按操作类型/会话/时间区间回放历史工件，并把「谁查了审计」本身也留痕。",
        transport=TRANSPORT_SERVICE,
        operation_id="audit.replay",
        preconditions=("identity", "operator_grant"),
        risk_tags=("read_only", "reads_db"),
        note="无 HTTP 面，经 deepquant.audit.operations.replay 调用；闸门由 guard() 执行",
    ),
)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #


def all_capabilities() -> tuple[CapabilitySpec, ...]:
    """全部能力（按 cap_id 排序，输出确定）。"""
    return tuple(sorted(CAPABILITIES, key=lambda c: c.cap_id))


def capability_index() -> dict[str, CapabilitySpec]:
    return {cap.cap_id: cap for cap in CAPABILITIES}


def get(cap_id: str) -> CapabilitySpec | None:
    """按标识取能力；不存在返回 ``None``（调用方决定是 404 还是 403）。"""
    return capability_index().get(cap_id)


def by_operation(operation_id: str) -> CapabilitySpec | None:
    """按 T1 操作类型反查能力。"""
    for cap in CAPABILITIES:
        if cap.operation_id == operation_id:
            return cap
    return None


def by_route(method: str, path: str | None) -> CapabilitySpec | None:
    """按 (方法, 路径) 反查能力。路径只做**精确**匹配。

    带路径参数的接口若按前缀匹配，``/backtests/{run_id}`` 会被误认成
    ``backtest.run``——把"查询历史"记成"执行回测"，工件与授权都会跟着错。
    """
    if path is None:
        return None
    normalized = path.rstrip("/") or "/"
    upper = (method or "").upper()
    for cap in CAPABILITIES:
        if cap.transport != TRANSPORT_HTTP:
            continue
        if (cap.method or "").upper() == upper and (cap.path or "").rstrip("/") == normalized:
            return cap
    return None


def http_capabilities() -> tuple[CapabilitySpec, ...]:
    return tuple(c for c in all_capabilities() if c.transport == TRANSPORT_HTTP)


def service_capabilities() -> tuple[CapabilitySpec, ...]:
    return tuple(c for c in all_capabilities() if c.transport == TRANSPORT_SERVICE)


# --------------------------------------------------------------------------- #
# 注册表自检
# --------------------------------------------------------------------------- #


def validate_registry(capabilities: Iterable[CapabilitySpec] | None = None) -> None:
    """注册表自检：条目自身合法、标识唯一、路由唯一、操作唯一、后继存在。

    这是**跨条目**的检查，与 ``CapabilitySpec.validate``（单条目）互补。
    重复的 (方法, 路径) 尤其危险：后写入的条目会静默覆盖前一条的授权与禁用状态。
    """
    caps = tuple(capabilities if capabilities is not None else CAPABILITIES)
    problems: list[str] = []

    seen_ids: dict[str, int] = {}
    seen_routes: dict[tuple[str, str], str] = {}
    seen_ops: dict[str, str] = {}
    for cap in caps:
        try:
            cap.validate()
        except CapabilityContractError as exc:
            problems.append(str(exc))

        if cap.cap_id in seen_ids:
            problems.append(f"cap_id 重复：{cap.cap_id}")
        seen_ids[cap.cap_id] = 1

        if cap.transport == TRANSPORT_HTTP:
            key = ((cap.method or "").upper(), (cap.path or "").rstrip("/"))
            if key in seen_routes:
                problems.append(f"路由重复：{key} 同时属于 {seen_routes[key]} 与 {cap.cap_id}")
            seen_routes[key] = cap.cap_id

        if cap.operation_id in seen_ops:
            problems.append(
                f"operation_id 重复：{cap.operation_id} 同时被 {seen_ops[cap.operation_id]} "
                f"与 {cap.cap_id} 使用（工件将无法区分是谁产生的）"
            )
        seen_ops[cap.operation_id] = cap.cap_id

    for cap in caps:
        if cap.successor and cap.successor not in seen_ids:
            problems.append(f"{cap.cap_id} 的 successor={cap.successor!r} 不存在")

    if problems:
        raise CapabilityContractError("能力注册表不自洽：" + "；".join(problems))


def assert_registry_valid() -> None:
    """供测试与启动自检调用的断言版本。"""
    validate_registry()


__all__ = [
    "CAPABILITIES",
    "all_capabilities",
    "assert_registry_valid",
    "by_operation",
    "by_route",
    "capability_index",
    "get",
    "http_capabilities",
    "service_capabilities",
    "validate_registry",
]
