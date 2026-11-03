"""风控（T2）：fail-closed 网关、四类检查点、原因码表与旁路检测。

模块分工：

- :mod:`deepquant.risk.reason_codes`：拒绝原因码常量表（四类 + 内部故障）；
- :mod:`deepquant.risk.checkpoints`：身份 / 限额 / 状态 / 字段四类检查点插件；
- :mod:`deepquant.risk.gateway`：检查点执行器与 off/observe/enforce 三模式；
- :mod:`deepquant.risk.middleware`：在业务执行前判定，拒绝带原因码并留痕；
- :mod:`deepquant.risk.bypass`：路由清单对账，证明"没有一条路能绕过检查"。

两条贯穿设计：

1. **算不出就说算不出**：校验依赖的数据缺失/过期 → 判不通过（``checkpoint_unverifiable``），
   不做乐观假设；检查点自身抛异常 → ``gateway_internal_error``，同样不通过。
2. **判定与拦截分离**：``evaluate`` 在所有模式下判定逻辑一致，模式只决定"要不要拦"。
   否则观察期采到的 would_reject 数字就是假的。

本包不负责审计工件（属 T1），也不负责限流与鉴权（本仓库尚无该业务面）。
"""

from __future__ import annotations

from deepquant.risk.bypass import (
    EXEMPT_ROUTES,
    MUTATING_METHODS,
    REQUIRED_MIDDLEWARE,
    assert_no_bypass,
    bypass_report,
)
from deepquant.risk.checkpoints import (
    REQUIRED_FIELDS_BY_OPERATION,
    Checkpoint,
    Decision,
    FieldCheckpoint,
    IdentityCheckpoint,
    LimitCheckpoint,
    OperationContext,
    RiskLimits,
    StateCheckpoint,
    category_coverage,
    default_checkpoints,
)
from deepquant.risk.gateway import (
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODE_OFF,
    RISK_ALLOWLIST_ENV,
    RISK_MODE_ENV,
    VALID_MODES,
    GateResult,
    RiskConfigurationError,
    RiskGateway,
    build_default_gateway,
    operator_allowlist,
    risk_mode,
)
from deepquant.risk.middleware import RISK_HEADER, SCOPE_KEY, RiskGateMiddleware, RiskLookups
from deepquant.risk.reason_codes import (
    CATEGORY_FIELD,
    CATEGORY_IDENTITY,
    CATEGORY_INTERNAL,
    CATEGORY_LABELS,
    CATEGORY_LIMIT,
    CATEGORY_STATE,
    REASON_CODES,
    REQUIRED_CATEGORIES,
    ReasonCode,
    assert_codes_complete,
    reason,
)

__all__ = [
    "CATEGORY_FIELD",
    "CATEGORY_IDENTITY",
    "CATEGORY_INTERNAL",
    "CATEGORY_LABELS",
    "CATEGORY_LIMIT",
    "CATEGORY_STATE",
    "Checkpoint",
    "Decision",
    "EXEMPT_ROUTES",
    "FieldCheckpoint",
    "GateResult",
    "IdentityCheckpoint",
    "LimitCheckpoint",
    "MODE_ENFORCE",
    "MODE_OBSERVE",
    "MODE_OFF",
    "MUTATING_METHODS",
    "OperationContext",
    "REASON_CODES",
    "REQUIRED_CATEGORIES",
    "REQUIRED_FIELDS_BY_OPERATION",
    "REQUIRED_MIDDLEWARE",
    "RISK_ALLOWLIST_ENV",
    "RISK_HEADER",
    "RISK_MODE_ENV",
    "ReasonCode",
    "RiskConfigurationError",
    "RiskGateMiddleware",
    "RiskGateway",
    "RiskLimits",
    "RiskLookups",
    "SCOPE_KEY",
    "StateCheckpoint",
    "VALID_MODES",
    "assert_codes_complete",
    "assert_no_bypass",
    "build_default_gateway",
    "bypass_report",
    "category_coverage",
    "default_checkpoints",
    "operator_allowlist",
    "reason",
    "risk_mode",
]
