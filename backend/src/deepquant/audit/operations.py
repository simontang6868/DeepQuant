"""核心操作注册表、覆盖率核算与「记录一次操作」的上下文管理器。

为什么需要注册表，而不是「能记就记」
------------------------------------
T1 的验收标准是「核心操作工件覆盖率 100%」。这句话只有在**先枚举核心操作**之后
才有意义——否则覆盖率的分母由实现随手决定，永远是 100%。

因此本模块把操作清单写成显式常量 ``CORE_OPERATIONS``：

- ``in_scope=True`` 的操作必须有 ``instrumented_at``（实现位置），否则覆盖率不足；
- ``in_scope=False`` 的操作必须写明 ``note``（为什么不在本期范围），避免用
  「没做到」冒充「不需要」。

第二部分（委托域）刻意留成 ``not_applicable``
-------------------------------------------
《推进计划》T1 的原始措辞是针对「电话交易平台」的委托录入/审核/改单/撤单，
而本仓库现有的业务面是**研究回测平台**，没有委托域（无券商连接、无订单状态机，
见 ``api/routes`` 只有 backtests/datasets/strategies/portfolio）。这个差异属于
计划第 10 节第 1 项「业务形态确认」未决事项，不是实现缺失。

处理方式：把这些操作登记为 ``in_scope=False`` 并写明原因，既不伪造实现，
也不把它们算进分母。等业务形态确认后，只需把 ``in_scope`` 改成 ``True``
并补上 ``instrumented_at``——机制层不需要改。
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Iterator, Mapping, Sequence

from deepquant.audit.run_card import now_iso
from deepquant.audit.store import AuditStore, AuditStoreError, open_store

logger = logging.getLogger(__name__)

#: 审计根目录环境变量。未设置＝审计关闭（默认），保证既有行为零变化。
AUDIT_DIR_ENV = "DEEPQUANT_AUDIT_DIR"

#: 严格模式：工件写入失败时是否让业务操作失败。默认开启——
#: 「操作发生了但没有留下工件」正是 T1 要消灭的状态，静默降级会把它请回来。
#: 置 ``0`` 可降级为「记录日志 + 响应头标记」，中间件侧使用该开关。
AUDIT_STRICT_ENV = "DEEPQUANT_AUDIT_STRICT"


@dataclass(frozen=True)
class OperationSpec:
    """一个核心操作的登记项。"""

    op_id: str
    title: str
    layer: str
    in_scope: bool
    instrumented_at: str | None = None
    note: str = ""

    @property
    def instrumented(self) -> bool:
        return bool(self.instrumented_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "title": self.title,
            "layer": self.layer,
            "in_scope": self.in_scope,
            "instrumented_at": self.instrumented_at,
            "instrumented": self.instrumented,
            "note": self.note,
        }


#: 本仓库当前的业务面：研究回测平台（无委托域）。
CORE_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        op_id="backtest.run",
        title="单标的回测执行（含数据集解析、策略实例化、引擎运行）",
        layer="api",
        in_scope=True,
        instrumented_at="deepquant.audit.middleware:OPERATION_ROUTES",
    ),
    OperationSpec(
        op_id="backtest.compare",
        title="历史回测结果比较（只读）",
        layer="api",
        in_scope=True,
        instrumented_at="deepquant.audit.middleware:OPERATION_ROUTES",
    ),
    OperationSpec(
        op_id="portfolio.backtest",
        title="多标的组合回测（复用组合记账层）",
        layer="api",
        in_scope=True,
        instrumented_at="deepquant.audit.middleware:OPERATION_ROUTES",
    ),
    OperationSpec(
        op_id="dataset.list",
        title="数据集枚举读取",
        layer="api",
        in_scope=True,
        instrumented_at="deepquant.audit.middleware:OPERATION_ROUTES",
    ),
    OperationSpec(
        op_id="strategy.list",
        title="策略注册表读取",
        layer="api",
        in_scope=True,
        instrumented_at="deepquant.audit.middleware:OPERATION_ROUTES",
    ),
    OperationSpec(
        op_id="audit.replay",
        title="审计工件回放查询（查询审计本身也要留痕）",
        layer="service",
        in_scope=True,
        instrumented_at="deepquant.audit.operations:replay",
    ),
    # ---- 以下是计划原文的委托域操作，本仓库尚无对应业务面 ----
    OperationSpec(
        op_id="order.entry",
        title="委托录入",
        layer="business",
        in_scope=False,
        note="现有系统无委托域（无券商连接、无订单状态机）；待计划 §10 第 1 项业务形态确认后启用",
    ),
    OperationSpec(
        op_id="order.review",
        title="委托审核",
        layer="business",
        in_scope=False,
        note="同上：无审核流，不存在可留痕的审核动作",
    ),
    OperationSpec(
        op_id="order.amend",
        title="委托改单",
        layer="business",
        in_scope=False,
        note="同上：无改单接口",
    ),
    OperationSpec(
        op_id="order.cancel",
        title="委托撤单",
        layer="business",
        in_scope=False,
        note="同上：无撤单接口；T5 的退出清理不变量将复用本工件结构",
    ),
)


class AuditConfigurationError(RuntimeError):
    """注册表自身不自洽（in_scope 却未实现、不在范围却无说明）。"""


def _module_exists(dotted: str) -> bool:
    try:
        import_module(dotted)
    except Exception:  # noqa: BLE001 - 任何导入失败都视为「实现位置不可达」
        return False
    return True


def coverage() -> dict[str, Any]:
    """核算覆盖率并做注册表自洽检查。

    ``ratio`` 的分母只含 ``in_scope=True`` 的操作；``not_applicable`` 单列，
    既不算通过也不算失败——把它算进分母会让覆盖率虚高，把它算进失败会让
    一个未决的业务问题看起来像技术欠账。
    """
    in_scope = [op for op in CORE_OPERATIONS if op.in_scope]
    instrumented = [op for op in in_scope if op.instrumented]
    missing = [op.op_id for op in in_scope if not op.instrumented]

    unreachable: list[str] = []
    for op in instrumented:
        assert op.instrumented_at is not None
        module = op.instrumented_at.split(":", 1)[0]
        if not _module_exists(module):
            unreachable.append(op.op_id)

    unexplained = [op.op_id for op in CORE_OPERATIONS if not op.in_scope and not op.note.strip()]

    ratio = 1.0 if not in_scope else len(instrumented) / len(in_scope)
    return {
        "declared_total": len(CORE_OPERATIONS),
        "in_scope_total": len(in_scope),
        "instrumented_total": len(instrumented),
        "ratio": ratio,
        "missing": missing,
        "unreachable_implementation": unreachable,
        "unexplained_out_of_scope": unexplained,
        "not_applicable": [op.op_id for op in CORE_OPERATIONS if not op.in_scope],
        "operations": [op.to_dict() for op in CORE_OPERATIONS],
    }


def assert_coverage_complete() -> None:
    """覆盖率自检：不达标直接抛错（供测试与启动自检调用）。"""
    report = coverage()
    problems: list[str] = []
    if report["ratio"] < 1.0:
        problems.append(f"核心操作覆盖率 {report['ratio']:.2%} < 100%，缺少实现：{report['missing']}")
    if report["unreachable_implementation"]:
        problems.append(f"登记的实现位置无法导入：{report['unreachable_implementation']}")
    if report["unexplained_out_of_scope"]:
        problems.append(f"不在范围却没有说明：{report['unexplained_out_of_scope']}")
    if problems:
        raise AuditConfigurationError("；".join(problems))


# --------------------------------------------------------------------------- #
# 运行期开关
# --------------------------------------------------------------------------- #


def audit_root() -> str | None:
    """审计根目录；未配置返回 ``None``。"""
    raw = os.environ.get(AUDIT_DIR_ENV, "").strip()
    return raw or None


def audit_enabled() -> bool:
    return audit_root() is not None


def audit_strict() -> bool:
    """严格模式（默认开启）。"""
    return os.environ.get(AUDIT_STRICT_ENV, "1").strip() not in {"0", "false", "False", "no"}


def runtime_env(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """记录工件时的运行环境（代码版本、主机、进程）。

    刻意不记录环境变量全集：其中可能含令牌，而工件不可改写、会被长期保留。
    """
    from deepquant.version import __version__

    env: dict[str, Any] = {
        "package_version": __version__,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "python": ".".join(str(x) for x in __import__("sys").version_info[:3]),
    }
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# 记录一次操作
# --------------------------------------------------------------------------- #


@dataclass
class CardDraft:
    """上下文管理器里可写的草稿。

    只暴露三个位置：``outcome``（结果）、``caveats``（注意事项）、``extra``（附加）。
    其余字段（操作人、时间、依据）在进入上下文时就固定，防止中途被业务代码改写
    成「看起来合规」的样子。
    """

    action: str
    operator: str
    basis: dict[str, Any]
    subject: dict[str, Any] | None = None
    inputs: dict[str, Any] | None = None
    session: str | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def note(self, text: str) -> None:
        """追加一条注意事项（空串忽略，避免制造「写了但没写」）。"""
        if text and text.strip():
            self.caveats.append(text.strip())


@contextmanager
def record_operation(
    action: str,
    *,
    operator: str,
    basis: Mapping[str, Any],
    subject: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    session: str | None = None,
    caveats: Sequence[str] = (),
    store: AuditStore | None = None,
    env_extra: Mapping[str, Any] | None = None,
    occurred_at: str | None = None,
) -> Iterator[CardDraft | None]:
    """在业务代码里记录一次操作的完整工件。

    未启用审计时产出 ``None``（调用方需判空），**不写任何文件**——这是既有
    1997 项测试不受影响的前提。

    异常路径同样落卡：``outcome.status="failed"`` 并带错误类型与消息。失败操作
    往往比成功操作更值得追溯，若只在成功时记录，工件就变成了报喜不报忧的宣传册。
    """
    active = store or open_store(audit_root())
    if active is None:
        yield None
        return

    draft = CardDraft(
        action=action,
        operator=operator,
        basis=dict(basis),
        subject=dict(subject) if subject is not None else None,
        inputs=dict(inputs) if inputs is not None else None,
        session=session,
        caveats=list(caveats),
    )
    started = occurred_at or now_iso()
    try:
        yield draft
    except BaseException as exc:  # noqa: BLE001 - 需要连 KeyboardInterrupt 一起留痕后原样抛出
        draft.outcome.setdefault("status", "failed")
        draft.outcome["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
        _append_quietly(active, draft, started, env_extra)
        raise
    else:
        draft.outcome.setdefault("status", "ok")
        _append_quietly(active, draft, started, env_extra)


def _append_quietly(
    store: AuditStore,
    draft: CardDraft,
    occurred_at: str,
    env_extra: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """写入工件；失败时按严格模式决定抛出还是记日志。"""
    try:
        return store.append(
            action=draft.action,
            operator=draft.operator,
            basis=draft.basis,
            outcome=draft.outcome,
            caveats=draft.caveats,
            subject=draft.subject,
            inputs=draft.inputs,
            env=runtime_env(dict(env_extra) if env_extra else None),
            session=draft.session,
            occurred_at=occurred_at,
            extra=draft.extra or None,
        )
    except AuditStoreError as exc:
        if audit_strict():
            raise
        logger.error("审计工件写入失败（已降级，仅记日志）：%s", exc)
        return None


def replay(
    *,
    store: AuditStore,
    operator: str,
    action: str | None = None,
    session: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """回放查询，并把「有人查了审计」这件事本身也记成一张工件。

    T1 要求「任一历史工件的回放时间 < 5 秒」。为满足这一点，查询本身不能再
    走一次全链扫描；写入一张轻量的 replay 工件代价是一次追加，可接受。

    这里是**服务层能力的唯一入口**，因此 T4 的能力闸门也装在这一层
    （``capabilities.policy.guard``）：``audit.replay`` 没有 HTTP 面，中间件
    管不到它。闸门默认关闭（``DEEPQUANT_CAPABILITY_MODE`` 未设置时不介入），
    因此调用方行为不变。
    """
    # 延迟导入：capabilities 包会间接 import 本模块，模块级导入会形成循环。
    from deepquant.capabilities.policy import guard

    guard("audit.replay", operator)

    with record_operation(
        "audit.replay",
        operator=operator,
        basis={
            "rule": "T1 回放查询留痕",
            "filters": {
                "action": action,
                "session": session,
                "since": since,
                "until": until,
                "limit": limit,
            },
        },
        subject={"action": action, "session": session},
        store=store,
    ) as draft:
        cards = store.query(
            action=action, session=session, since=since, until=until, limit=limit
        )
        if draft is not None:
            draft.outcome["n_returned"] = len(cards)
            if len(cards) >= limit:
                draft.note(f"命中数达到上限 {limit}，结果可能被截断")
    return cards


__all__ = [
    "AUDIT_DIR_ENV",
    "AUDIT_STRICT_ENV",
    "CORE_OPERATIONS",
    "AuditConfigurationError",
    "CardDraft",
    "OperationSpec",
    "assert_coverage_complete",
    "audit_enabled",
    "audit_root",
    "audit_strict",
    "coverage",
    "record_operation",
    "replay",
    "runtime_env",
]
