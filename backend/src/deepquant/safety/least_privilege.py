"""最小权限：默认拒绝会改变状态的操作，提权必须显式授予。

先说清一件事：这**不是认证**，因此也不是完整的越权防护
------------------------------------------------------
本仓库没有登录、没有令牌、没有签名。``X-Operator`` 头是调用方**自称**的，
任何人都能填任意名字。所以在当前形态下，最小权限能做到的是：

- 防止**误操作**：默认不允许任何人跑会写库的操作，必须显式把名字加进名单；
- 让"谁被允许做什么"从口头约定变成可查询、可测试的配置；
- 为将来接入真实身份（令牌/证书/OIDC）预留唯一的判定入口，届时只换身份来源。

它**不能**做到：阻止知道 API 地址的人伪造一个已授权操作人。把这一点写进代码注释
和文档，是因为"以为有权限控制"比"知道没有权限控制"危险得多——前者会导致把
敏感能力挂在一个实际上人人可用的接口后面。

两种模式
--------
- ``open``（默认）：保持既有行为，任何已识别操作人都能调用（T4 的名单制仍生效）；
- ``minimal``：会改变状态的能力必须**显式授予**；未提供身份一律拒绝。

默认保持 ``open`` 的理由与 T4 一致：引入安全机制的第一天就把现有调用方全挡住，
通常的结局是有人关掉它然后忘开。切换路径应为：先用 ``audit_matrix()`` 看清
"切到 minimal 后谁会失去什么"，再改配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Mapping

from deepquant.safety.codes import safety_reason

#: 权限模式环境变量。
PERMISSION_MODE_ENV = "DEEPQUANT_PERMISSION_MODE"

MODE_OPEN = "open"
MODE_MINIMAL = "minimal"
VALID_MODES: tuple[str, ...] = (MODE_OPEN, MODE_MINIMAL)

#: 缺省操作人（与 T1 审计的记法一致）。
ANONYMOUS = "anonymous"


class PrivilegeConfigError(RuntimeError):
    """权限配置不合法。按不通过处理，不静默降级为 open。"""


@dataclass(frozen=True)
class PrivilegeDecision:
    allowed: bool
    capability: str
    mode: str
    code: str | None = None
    detail: str = ""
    checkpoint: str = "privilege"

    @property
    def status_code(self) -> int | None:
        return None if self.code is None else safety_reason(self.code).status_code

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "allowed": self.allowed,
            "capability": self.capability,
            "mode": self.mode,
            "checkpoint": self.checkpoint,
        }
        if self.code is not None:
            payload["reason_code"] = self.code
            payload["status_code"] = self.status_code
            payload["detail"] = self.detail
        return payload


def mode() -> str:
    raw = os.environ.get(PERMISSION_MODE_ENV, "").strip().lower()
    if not raw:
        return MODE_OPEN
    if raw not in VALID_MODES:
        raise PrivilegeConfigError(
            f"{PERMISSION_MODE_ENV}={raw!r} 非法，合法值：{', '.join(VALID_MODES)}"
        )
    return raw


def is_mutating(risk_tags: Iterable[str]) -> bool:
    return "mutating" in set(risk_tags)


def check(
    cap_id: str,
    risk_tags: Iterable[str],
    operator: str,
    *,
    granted: frozenset[str] | None = None,
    current_mode: str | None = None,
) -> PrivilegeDecision:
    """判定某个操作人能否调用该能力。

    ``granted`` 为该能力的显式授权名单（``None`` 表示没有配置名单）。
    ``minimal`` 下 ``None`` 视为"没人被授予"，而不是"所有人都行"——
    "没配置"与"配置成所有人"必须区分，否则忘配置就等于全开。
    """
    active = current_mode if current_mode is not None else mode()
    if active == MODE_OPEN:
        return PrivilegeDecision(True, cap_id, active)

    who = (operator or "").strip()
    if not who or who == ANONYMOUS:
        return PrivilegeDecision(
            False,
            cap_id,
            active,
            code="privilege_unidentified",
            detail="最小权限模式要求明确的操作人身份（请求未带 X-Operator）",
        )

    if not is_mutating(risk_tags):
        return PrivilegeDecision(True, cap_id, active)

    if granted is None or (who not in granted and "*" not in granted):
        listed = "未配置授权名单" if granted is None else f"授权名单 {sorted(granted)}"
        return PrivilegeDecision(
            False,
            cap_id,
            active,
            code="privileged_operation_denied",
            detail=f"{cap_id} 会改变状态，{who} 未被显式授予（{listed}）",
        )
    return PrivilegeDecision(True, cap_id, active)


def audit_matrix(capabilities: Iterable[object]) -> dict[str, object]:
    """统计"切到 minimal 后谁会失去什么"。供上线前评估与文档。

    输入用鸭子类型（需要 ``cap_id`` / ``risk_tags``），避免本模块反向依赖能力包。
    """
    rows: list[dict[str, object]] = []
    for cap in capabilities:
        cap_id = str(getattr(cap, "cap_id"))
        tags = tuple(getattr(cap, "risk_tags", ()) or ())
        mutating = is_mutating(tags)
        rows.append(
            {
                "capability": cap_id,
                "mutating": mutating,
                "open_mode": "anonymous_allowed",
                "minimal_mode": "requires_explicit_grant" if mutating else "identified_operator",
            }
        )
    return {
        "mode": mode() if _safe_mode() else "invalid",
        "n_capabilities": len(rows),
        "n_mutating": sum(1 for r in rows if r["mutating"]),
        "rows": rows,
        "caveat": (
            "无认证：X-Operator 为调用方自称，本机制防误操作，不防伪造身份。"
            "接入真实身份后才是完整的越权防护。"
        ),
    }


def _safe_mode() -> bool:
    try:
        mode()
        return True
    except PrivilegeConfigError:
        return False


def assert_config_valid() -> None:
    mode()


def matrix_from_registry() -> dict[str, object]:
    """便捷入口：直接用能力注册表算矩阵（CLI 与取证脚本用）。"""
    from deepquant.capabilities.registry import all_capabilities

    return audit_matrix(all_capabilities())


def permission_summary(grants: Mapping[str, frozenset[str]] | None = None) -> dict[str, object]:
    """当前权限策略快照：模式 + 每能力的授权人数（不含人名，避免泄露名单）。"""
    from deepquant.capabilities.policy import grants as policy_grants

    table = dict(grants) if grants is not None else dict(policy_grants())
    return {
        "mode": mode() if _safe_mode() else "invalid",
        "grants_configured": sorted(table),
        "n_grants": len(table),
        "caveat": "无认证环境：授权名单可被伪造身份绕过，仅用于防误操作",
    }


__all__ = [
    "ANONYMOUS",
    "MODE_MINIMAL",
    "MODE_OPEN",
    "PERMISSION_MODE_ENV",
    "PrivilegeConfigError",
    "PrivilegeDecision",
    "VALID_MODES",
    "assert_config_valid",
    "audit_matrix",
    "check",
    "is_mutating",
    "matrix_from_registry",
    "mode",
    "permission_summary",
]
