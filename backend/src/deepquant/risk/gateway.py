"""Fail-closed 网关：按顺序执行检查点，把「不通过」翻译成可检索的拒绝。

三种模式，解决计划里两条互相拉扯的要求
--------------------------------------
计划 T2 要求「默认拒绝执行」，同一份计划的风险登记又要求「先告警不拦截运行
两周再转强制」。把二者硬塞进一个开关里，结果一定是有人为了跑通业务把开关关掉，
然后忘了打开。

所以拆成三种模式：

- ``off``：完全不启用（默认）。既有部署与测试行为零变化。
- ``observe``：**照常判定、照常留痕、但不拦**。响应头给出 ``would-reject``，
  T1 工件里带 ``would_reject=true``。观察期结束可以直接数出"上线后会拦掉多少"，
  而不是凭感觉决定要不要开。
- ``enforce``：真的拦。

判定逻辑（``evaluate``）在三种模式下**完全相同**——模式只影响"要不要拦"，
不影响"该不该拒"。这一点是灰度能用的前提：如果 observe 模式下判定被弱化，
观察期采到的数字就是假的。

为什么遇到第一个拒绝就停
------------------------
继续跑后面的检查点会产出"缺字段"与"数据集不存在"两条原因，调用方按第一条改了
字段再试，又收到第二条，体验上像打地鼠。停在第一处，并把已通过的检查点记进
工件，既够定位又不会误导。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from deepquant.risk.checkpoints import (
    Checkpoint,
    Decision,
    OperationContext,
    default_checkpoints,
    reject,
)
from deepquant.risk.reason_codes import CATEGORY_INTERNAL

logger = logging.getLogger(__name__)

#: 模式环境变量。未设置 = off。
RISK_MODE_ENV = "DEEPQUANT_RISK_MODE"

#: 操作人允许名单（逗号分隔）。未设置 = 只校验"有没有身份"，不校验"是谁"。
RISK_ALLOWLIST_ENV = "DEEPQUANT_RISK_OPERATOR_ALLOWLIST"

MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"

VALID_MODES = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)


class RiskConfigurationError(RuntimeError):
    """风控配置非法。宁可整体拒绝服务，也不猜一个"差不多"的模式。"""


def risk_mode() -> str:
    """读取当前模式。非法值直接报错（不允许静默降级成 off）。"""
    raw = os.environ.get(RISK_MODE_ENV, "").strip().lower()
    if not raw:
        return MODE_OFF
    if raw not in VALID_MODES:
        raise RiskConfigurationError(
            f"{RISK_MODE_ENV}={raw!r} 非法，合法值：{', '.join(VALID_MODES)}"
        )
    return raw


def operator_allowlist() -> frozenset[str] | None:
    raw = os.environ.get(RISK_ALLOWLIST_ENV, "").strip()
    if not raw:
        return None
    items = frozenset(x.strip() for x in raw.split(",") if x.strip())
    return items or None


@dataclass(frozen=True)
class GateResult:
    """一次闸门判定的完整结果。"""

    mode: str
    allowed: bool
    would_reject: bool
    rejection: Decision | None
    decisions: tuple[Decision, ...] = field(default_factory=tuple)

    @property
    def reason_code(self) -> str | None:
        return None if self.rejection is None else self.rejection.code

    @property
    def status_code(self) -> int:
        return 200 if self.rejection is None else self.rejection.status_code

    def to_audit_dict(self) -> dict[str, Any]:
        """写进 T1 工件的摘要（保持扁平、可检索）。"""
        return {
            "mode": self.mode,
            "allowed": self.allowed,
            "would_reject": self.would_reject,
            "reason_code": self.reason_code,
            "checkpoint": None if self.rejection is None else self.rejection.checkpoint,
            "category": None if self.rejection is None else self.rejection.category,
            "status_code": self.status_code,
            "n_checked": len(self.decisions),
            "unverifiable": bool(self.rejection and self.rejection.unverifiable),
            "details": {} if self.rejection is None else dict(self.rejection.details),
        }

    def to_response_body(self) -> dict[str, Any]:
        """错误响应体，字段结构与 ``api.errors`` 的既有契约一致。

        刻意不 import ``api.errors``：风控层不应反向依赖 API 层。契约由测试钉住
        （见 ``tests/risk/test_risk_gateway_api.py`` 对响应体结构的断言）。
        """
        assert self.rejection is not None
        return {
            "error": {
                "code": self.rejection.code,
                "message": self.rejection.message,
                "details": {
                    **dict(self.rejection.details),
                    "checkpoint": self.rejection.checkpoint,
                    "category": self.rejection.category,
                    "mode": self.mode,
                },
            }
        }


class RiskGateway:
    """检查点执行器。无状态，可复用。"""

    def __init__(self, checkpoints: Sequence[Checkpoint] | None = None) -> None:
        self.checkpoints: tuple[Checkpoint, ...] = tuple(checkpoints or ())

    @classmethod
    def build(
        cls,
        *,
        allowlist=None,
        limits=None,
        dataset_lookup=None,
        strategy_lookup=None,
        run_lookup=None,
    ) -> "RiskGateway":
        return cls(
            default_checkpoints(
                allowlist=allowlist,
                limits=limits,
                dataset_lookup=dataset_lookup,
                strategy_lookup=strategy_lookup,
                run_lookup=run_lookup,
            )
        )

    def evaluate(self, ctx: OperationContext, *, mode: str) -> GateResult:
        """按顺序执行检查点；判定逻辑与 mode 无关。"""
        if mode not in VALID_MODES:
            raise RiskConfigurationError(f"未知模式：{mode!r}")
        if mode == MODE_OFF:
            return GateResult(mode=mode, allowed=True, would_reject=False, rejection=None)

        decisions: list[Decision] = []
        for checkpoint in self.checkpoints:
            if not checkpoint.applies_to(ctx.operation):
                continue
            try:
                decision = checkpoint.check(ctx)
            except Exception as exc:  # noqa: BLE001 - 检查点自身故障必须 fail-closed
                logger.error("风控检查点 %s 执行失败：%s", checkpoint.name, exc)
                decision = reject(
                    checkpoint.name,
                    CATEGORY_INTERNAL,
                    "gateway_internal_error",
                    message=f"检查点 {checkpoint.name} 抛出 {type(exc).__name__}，按不通过处理",
                    details={"exception": type(exc).__name__},
                )
            decisions.append(decision)
            if not decision.allowed:
                would_reject = True
                return GateResult(
                    mode=mode,
                    allowed=mode == MODE_OBSERVE,  # 观察期放行，但记录"本应拒绝"
                    would_reject=would_reject,
                    rejection=decision,
                    decisions=tuple(decisions),
                )

        return GateResult(mode=mode, allowed=True, would_reject=False, rejection=None, decisions=tuple(decisions))


def build_default_gateway(
    *,
    dataset_lookup=None,
    strategy_lookup=None,
    run_lookup=None,
    limits=None,
) -> RiskGateway:
    """按环境变量装配默认网关（供 API 装配层调用）。"""
    return RiskGateway.build(
        allowlist=operator_allowlist(),
        limits=limits,
        dataset_lookup=dataset_lookup,
        strategy_lookup=strategy_lookup,
        run_lookup=run_lookup,
    )


__all__ = [
    "GateResult",
    "MODE_ENFORCE",
    "MODE_OBSERVE",
    "MODE_OFF",
    "RISK_ALLOWLIST_ENV",
    "RISK_MODE_ENV",
    "RiskConfigurationError",
    "RiskGateway",
    "VALID_MODES",
    "build_default_gateway",
    "operator_allowlist",
    "risk_mode",
]
