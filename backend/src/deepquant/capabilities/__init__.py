"""能力契约化（T4）：声明、对账、准入三层。

三层分工必须分清，否则很容易做成"又一份会过时的清单"：

============================  ==========================================
模块                            职责
============================  ==========================================
``spec``                       一个能力"承诺了什么"（含版本与摘要规则）
``registry``                   本仓库有哪些能力（显式清单，不自动扫描）
``contract``                   声明 vs 真实装配的双向对账 + lock 版本约束
``policy``                     这个能力是否开放、这个人是否被授权、版本是否兼容
``middleware``                 把 policy 装到 HTTP 入口；服务层能力用 ``policy.guard``
============================  ==========================================

与既有三套清单的关系（刻意不合并）：

- ``api/registry.py``：策略**插件**参数契约，与 HTTP 无关。能力层引用它产出的
  ``strategy.list`` 响应，但不复制它的参数表；
- ``audit/operations.py``：留痕口径的操作清单，是能力层 ``operation_id`` 的来源；
  两者由 ``contract.verify_app`` 双向核对（T1 每个核心操作都要有对应能力）；
- ``risk/checkpoints.py``：单次请求能不能做。能力层管"这个功能是否对你开放"，
  两者判定依据不同，拒绝原因码因此分成两张表。

典型用法::

    from deepquant.capabilities import assert_app_contract, describe

    app = create_app()
    assert_app_contract(app)     # 装配层自检：声明与实现不一致即失败
    describe()                   # 枚举能力（供 /docs 之外的运维查询）
"""

from __future__ import annotations

from typing import Any

from deepquant.capabilities.codes import (
    CAPABILITY_CODES,
    assert_codes_complete,
    capability_codes,
    capability_reason,
)
from deepquant.capabilities.contract import (
    EXEMPT_ROUTES,
    ContractReport,
    assert_app_contract,
    compute_lock,
    default_lock_path,
    format_report,
    load_lock,
    verify_all,
    verify_app,
    verify_lock,
    write_lock,
)
from deepquant.capabilities.policy import (
    CapabilityConfigError,
    CapabilityDenied,
    Decision,
    check_version,
    disabled,
    evaluate,
    grants,
    guard,
    mode,
    policy_snapshot,
)
from deepquant.capabilities.registry import (
    CAPABILITIES,
    all_capabilities,
    assert_registry_valid,
    by_operation,
    by_route,
    get,
    http_capabilities,
    service_capabilities,
)
from deepquant.capabilities.spec import (
    CapabilityContractError,
    CapabilitySpec,
    compare_versions,
    same_major,
)


def describe(*, include_empty_grants: bool = True) -> dict[str, Any]:
    """枚举当前能力与策略（运维与文档共用的唯一出口）。

    ``include_empty_grants`` 保留参数是为了让调用方能显式选择"只看能力清单"；
    默认把策略快照一起给出，因为"某个能力为什么调不通"九成是策略问题，
    分两次查询反而容易漏看。
    """
    payload: dict[str, Any] = {
        "capabilities": [cap.to_dict() for cap in all_capabilities()],
        "n_http": len(http_capabilities()),
        "n_service": len(service_capabilities()),
        "reason_codes": capability_codes(),
    }
    if include_empty_grants:
        payload["policy"] = policy_snapshot()
    return payload


def audit_surface() -> dict[str, Any]:
    """能力层的自检面（供 ``scripts/capabilities.py verify`` 与测试使用）。"""
    assert_codes_complete()
    assert_registry_valid()
    from deepquant.api.app import create_app

    report: ContractReport = verify_all(create_app())
    return report.to_dict()


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_CODES",
    "EXEMPT_ROUTES",
    "CapabilityConfigError",
    "CapabilityContractError",
    "CapabilityDenied",
    "CapabilitySpec",
    "ContractReport",
    "Decision",
    "all_capabilities",
    "assert_app_contract",
    "assert_codes_complete",
    "assert_registry_valid",
    "audit_surface",
    "by_operation",
    "by_route",
    "capability_codes",
    "capability_reason",
    "check_version",
    "compare_versions",
    "compute_lock",
    "default_lock_path",
    "describe",
    "disabled",
    "evaluate",
    "format_report",
    "get",
    "grants",
    "guard",
    "http_capabilities",
    "load_lock",
    "mode",
    "policy_snapshot",
    "same_major",
    "service_capabilities",
    "verify_all",
    "verify_app",
    "verify_lock",
    "write_lock",
]
