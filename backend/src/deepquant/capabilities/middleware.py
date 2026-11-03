"""能力闸门中间件：执行前判定「这个能力是否开放、是否授权、版本是否兼容」。

装在风控网关的**外层**、审计中间件的**内层**
--------------------------------------------
顺序（由内到外）：``路由 → 风控 → 能力 → 审计``。

- 在风控外层：能力关闭/未授权属于"这个功能对你不可用"，比"请求本身不合法"
  更前置。若反过来，一个被关掉的能力仍会先跑一遍数据库查询（状态检查点），
  既浪费也不必要地把内部状态暴露在错误信息里。
- 在审计内层：拒绝必须留痕。审计在外层才能记录到能力层的 403——
  与 T2 同一理由：「没有记录」和「没人操作过」长得一模一样。

未登记路由的拒绝怎么留痕
------------------------
审计中间件只对 ``OPERATION_ROUTES`` 里登记过的路径落卡；未登记路由的拒绝如果
不额外处理，就会成为审计里的一个空洞——而这恰恰是"新接口忘了登记"最容易发生
的场合。因此本中间件在拒绝未登记路由时，用与 T1 相同的写入通道补一张
``capability.unregistered`` 工件。它不是业务动作，因此**不进** T1 的覆盖率分母，
但它必须可检索。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from deepquant.audit.middleware import (
    CAPABILITY_SCOPE_KEY,
    OPERATOR_HEADER,
    resolve_operation,
)
from deepquant.audit.operations import record_operation
from deepquant.capabilities import policy
from deepquant.capabilities.codes import capability_reason
from deepquant.capabilities.contract import EXEMPT_ROUTES
from deepquant.capabilities.policy import (
    DEPRECATED_HEADER,
    MODE_ENFORCE,
    MODE_OFF,
    RESPONSE_ID_HEADER,
    RESPONSE_VERSION_HEADER,
    VERSION_HEADER,
    WOULD_REJECT_HEADER,
    CapabilityConfigError,
    Decision,
)
from deepquant.capabilities.registry import by_operation
from deepquant.capabilities.spec import CapabilitySpec

logger = logging.getLogger(__name__)

#: 请求作用域键：审计中间件据此把能力判定写进 T1 工件。
SCOPE_KEY = CAPABILITY_SCOPE_KEY

#: 响应头：本次请求的能力结论（pass / would-reject / rejected）。
CAPABILITY_HEADER = "X-DeepQuant-Capability"

#: 未登记路由被拒时写入工件的 action 名。
UNREGISTERED_ACTION = "capability.unregistered"

_FRAMEWORK_PREFIXES: tuple[str, ...] = ("/openapi.json", "/docs", "/redoc")


def _is_framework_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _FRAMEWORK_PREFIXES)


def _error_response(decision: Decision, *, header_value: str) -> JSONResponse:
    """拒绝响应：结构与风控层一致，调用方不必为两层写两套解析。"""
    spec = capability_reason(decision.code or "capability_internal_error")
    return JSONResponse(
        status_code=spec.status_code,
        content={
            "error": {
                "code": spec.code,
                "message": decision.detail or spec.summary,
                "details": {
                    "checkpoint": "capability",
                    "category": spec.category,
                    "capability": decision.capability,
                },
            }
        },
        headers={CAPABILITY_HEADER: header_value},
    )


def _operator_of(request: Request) -> str:
    return (request.headers.get(OPERATOR_HEADER) or "").strip() or "anonymous"


def _record_unregistered(request: Request, decision: Decision) -> None:
    """未登记路由的拒绝补一张工件（审计层不会为它落卡）。"""
    try:
        with record_operation(
            UNREGISTERED_ACTION,
            operator=_operator_of(request),
            basis={
                "rule": "能力闸门：会改变状态的路由必须先登记能力契约",
                "request": {
                    "method": request.method.upper(),
                    "path": request.url.path,
                },
            },
            subject={"capability": decision.capability},
            caveats=["该路径未登记在 OPERATION_ROUTES，审计中间件不会为它落常规工件"],
        ) as draft:
            if draft is not None:
                draft.outcome.update(
                    {
                        "status": "rejected",
                        "status_code": decision.status_code,
                        "reason_code": decision.code,
                        "capability": decision.capability,
                    }
                )
    except Exception as exc:  # noqa: BLE001 - 留痕失败不得改变拒绝本身
        logger.error("未登记路由的拒绝工件写入失败：%s", exc)


def _apply_success_headers(
    response: Response, cap: CapabilitySpec, decision: Decision, mode: str
) -> None:
    response.headers[CAPABILITY_HEADER] = (
        "would-reject" if not decision.allowed else "pass"
    )
    response.headers[RESPONSE_ID_HEADER] = cap.cap_id
    response.headers[RESPONSE_VERSION_HEADER] = cap.version
    if not decision.allowed:
        response.headers[WOULD_REJECT_HEADER] = decision.code or ""
    if cap.deprecated:
        # 弃用信息必须能被机器读到：调用方据此排期迁移，而不是靠读文档。
        response.headers[DEPRECATED_HEADER] = (
            f"true; successor={cap.successor}" if cap.successor else "true"
        )


class CapabilityGateMiddleware(BaseHTTPMiddleware):
    """按能力契约放行或拒绝请求。默认 ``off``（未配置环境变量时不介入）。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        try:
            current = policy.mode()
        except CapabilityConfigError as exc:
            # 配置写错时拒绝而不是放行：放行会让"以为关了其实没关"持续存在。
            logger.error("能力层配置非法，按不通过处理：%s", exc)
            return _error_response(
                Decision(
                    capability="-",
                    allowed=False,
                    code="capability_internal_error",
                    detail=str(exc),
                ),
                header_value="rejected",
            )

        if current == MODE_OFF:
            return await call_next(request)

        path = request.url.path
        if _is_framework_path(path):
            return await call_next(request)

        method = request.method.upper()
        operation = resolve_operation(method, path)
        cap = by_operation(operation) if operation is not None else None

        if cap is None:
            # 未登记：豁免清单里的只读端点放行（如实"未纳入契约"），其余拒绝。
            if (method, path.rstrip("/") or "/") in EXEMPT_ROUTES:
                return await call_next(request)

            decision = Decision(
                capability="-",
                allowed=False,
                code="capability_unregistered",
                detail=(
                    f"{method} {path} 未登记为能力；"
                    "会改变状态的新接口必须先声明契约再上线"
                ),
            )
            if current == MODE_ENFORCE:
                _record_unregistered(request, decision)
                return _error_response(decision, header_value="rejected")

            logger.warning(
                "能力观察期：%s %s 未登记为能力，强制模式下会被拒绝", method, path
            )
            response: Response = await call_next(request)
            response.headers[CAPABILITY_HEADER] = "would-reject"
            response.headers[WOULD_REJECT_HEADER] = decision.code or ""
            return response

        operator = _operator_of(request)
        try:
            decision = policy.evaluate(cap, operator)
            if decision.allowed:
                version_problem = policy.check_version(
                    cap, request.headers.get(VERSION_HEADER)
                )
                if version_problem is not None:
                    decision = version_problem
        except CapabilityConfigError as exc:
            logger.error("能力层配置非法，按不通过处理：%s", exc)
            return _error_response(
                Decision(
                    capability=cap.cap_id,
                    allowed=False,
                    code="capability_internal_error",
                    detail=str(exc),
                ),
                header_value="rejected",
            )

        # 无论通过与否都写进作用域：审计中间件据此把能力结论与原因码写进 T1 工件。
        # ``would_reject`` 与风控层同义：仅在观察期出现，表示"强制模式下会被拒"。
        annotation = decision.to_dict()
        annotation["would_reject"] = (not decision.allowed) and current != MODE_ENFORCE
        annotation["mode"] = current
        request.scope[SCOPE_KEY] = annotation

        if not decision.allowed:
            if current == MODE_ENFORCE:
                return _error_response(decision, header_value="rejected")
            logger.warning(
                "能力观察期：%s 本应被拒绝（%s：%s），当前未拦截",
                cap.cap_id,
                decision.code,
                decision.detail,
            )

        response = await call_next(request)
        _apply_success_headers(response, cap, decision, current)
        return response


def decision_from_scope(scope: Mapping[str, Any]) -> dict[str, Any] | None:
    """从 ASGI scope 取能力结论（供审计中间件与测试使用）。"""
    value = scope.get(SCOPE_KEY)
    return dict(value) if isinstance(value, Mapping) else None


__all__ = [
    "CAPABILITY_HEADER",
    "SCOPE_KEY",
    "UNREGISTERED_ACTION",
    "CapabilityGateMiddleware",
    "decision_from_scope",
]
