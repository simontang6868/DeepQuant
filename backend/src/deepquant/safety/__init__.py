"""安全边界编码（T5）：出站目标、最小权限、熔断、退出清理。

四块各自回答一个问题，且都不依赖"人工记得"：

============================  ==========================================
模块                            回答的问题
============================  ==========================================
``outbound``                   这个地址能不能连？（白名单 + 内网穿透防护）
``least_privilege``            这个人能不能做这个会改状态的操作？（默认拒绝）
``breaker``                    这类操作现在还能不能跑？（状态落盘，重启不清零）
``lifecycle``                  进程退出时资源清干净了吗？下次能不能启动？
``middleware``                 把熔断与在飞计数装到 HTTP 入口
============================  ==========================================

**必须先说清的一件事**：本仓库没有认证。``X-Operator`` 是调用方自称的，
所以 ``least_privilege`` 在当前形态下防的是**误操作**，不是伪造身份的越权。
把它当成完整的权限系统，会出现"把敏感能力挂在人人可用的接口后面"这种事。
"""

from __future__ import annotations

from typing import Any

from deepquant.safety.breaker import (
    BreakerDecision,
    BreakerState,
    BreakerStateError,
    allow,
    breaker_scope,
    config as breaker_config,
    record_failure,
    record_success,
    reset,
    safety_root,
    snapshot as breaker_snapshot,
    trip,
)
from deepquant.safety.codes import (
    REQUIRED_CATEGORIES,
    SAFETY_CODES,
    assert_codes_complete,
    category_coverage,
    safety_codes,
    safety_reason,
)
from deepquant.safety.least_privilege import (
    PERMISSION_MODE_ENV,
    PrivilegeConfigError,
    PrivilegeDecision,
    audit_matrix,
    check as privilege_check,
    matrix_from_registry,
    mode as permission_mode,
    permission_summary,
)
from deepquant.safety.lifecycle import (
    INFLIGHT,
    NOT_APPLICABLE_STEPS,
    CleanupReport,
    DirtyShutdown,
    assert_clean_start,
    begin_session,
    cleanup_log_entries,
    end_session,
    install_hooks,
    pending_cleanup,
    run_cleanup,
    snapshot as lifecycle_snapshot,
    stale_session,
    verify_log_chain,
)
from deepquant.safety.outbound import (
    OutboundBlocked,
    OutboundDecision,
    UrlPolicy,
    assert_allowed,
    check as outbound_check,
    default_policy,
    enforce_scan,
    scan_url_literals,
)


def describe() -> dict[str, Any]:
    """安全层自检面（CLI 与取证脚本共用的唯一出口）。"""
    return {
        "reason_codes": safety_codes(),
        "category_coverage": category_coverage(),
        "required_categories": list(REQUIRED_CATEGORIES),
        "not_applicable_steps": NOT_APPLICABLE_STEPS,
        "breaker": breaker_snapshot(),
        "permissions": permission_summary(),
        "pending_cleanup": pending_cleanup(),
        "stale_session": stale_session(),
    }


__all__ = [
    "CleanupReport",
    "DirtyShutdown",
    "INFLIGHT",
    "NOT_APPLICABLE_STEPS",
    "OutboundBlocked",
    "OutboundDecision",
    "PERMISSION_MODE_ENV",
    "PrivilegeConfigError",
    "PrivilegeDecision",
    "REQUIRED_CATEGORIES",
    "SAFETY_CODES",
    "UrlPolicy",
    "BreakerDecision",
    "BreakerState",
    "BreakerStateError",
    "allow",
    "assert_allowed",
    "assert_clean_start",
    "assert_codes_complete",
    "audit_matrix",
    "begin_session",
    "breaker_config",
    "breaker_scope",
    "breaker_snapshot",
    "category_coverage",
    "cleanup_log_entries",
    "default_policy",
    "describe",
    "end_session",
    "enforce_scan",
    "install_hooks",
    "lifecycle_snapshot",
    "matrix_from_registry",
    "outbound_check",
    "pending_cleanup",
    "permission_mode",
    "permission_summary",
    "privilege_check",
    "record_failure",
    "record_success",
    "reset",
    "run_cleanup",
    "safety_codes",
    "safety_reason",
    "safety_root",
    "scan_url_literals",
    "stale_session",
    "trip",
    "verify_log_chain",
]
