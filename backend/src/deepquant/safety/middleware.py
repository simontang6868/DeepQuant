"""安全中间件：熔断闸 + 在飞计数 + 排空期拒绝新任务。

为什么熔断必须在中间件层
------------------------
"关闭某个操作"这件事如果只写在某个路由里，就一定会有一条路绕过它（新加的路由、
脚本入口、批量任务）。放在中间件层，判据只按"操作类型"决定，与具体路由无关。

为什么还要统计在飞请求
----------------------
退出清理的第三步是"验证清空"。没有计数时它只能变成"等几秒应该差不多了"——
那不是验证，是猜测，也无法留下证据。计数让"还有没有人在写"成为一个确定答案。

与 T1/T2/T4 的关系
------------------
本层只管"系统整体是否暂停"（熔断）与"进程是否正在退出"（排空），
既不做身份判定（T2/T4），也不做能力授权（T4）。拒绝原因码写进同一份 T1 工件，
因此"被熔断拦下"同样可以按 ``reason_code=breaker_open`` 检索。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from deepquant.audit.middleware import SAFETY_SCOPE_KEY, resolve_operation
from deepquant.safety import breaker
from deepquant.safety.codes import safety_reason
from deepquant.safety.lifecycle import INFLIGHT, safety_state_enabled

logger = logging.getLogger(__name__)

#: 请求作用域键：审计中间件据此把安全判定写进工件。
SCOPE_KEY = SAFETY_SCOPE_KEY

#: 响应头：本次请求的安全层结论（pass / rejected）。
SAFETY_HEADER = "X-DeepQuant-Safety"

#: 5xx 视为"系统失败"计入熔断；4xx 是业务拒绝（用户输入问题），不计入。
#: 把 4xx 也计入，会让几个手抖的错误参数就把整个功能熔断掉。
FAILURE_STATUS_FLOOR = 500


def _error_response(status_code: int, code: str, detail: str, *, retry_after: int = 0) -> JSONResponse:
    """拒绝响应：结构与风控/能力层一致，调用方不必为三层写三套解析。"""
    spec = safety_reason(code)
    headers = {SAFETY_HEADER: "rejected"}
    if retry_after > 0:
        # Retry-After 让客户端知道何时可重试，而不是盲目重试把熔断器喂得更死
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": spec.code,
                "message": detail or spec.summary,
                "details": {"checkpoint": "breaker", "category": spec.category},
            }
        },
        headers=headers,
    )


class SafetyMiddleware(BaseHTTPMiddleware):
    """熔断 + 排空闸门。默认无配置时恒放行（不改变既有行为）。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not safety_state_enabled():
            # 未配置安全目录且处于测试进程：不落盘、不计熔断，避免污染与跨套件干扰
            return await call_next(request)

        if INFLIGHT.draining:
            return _error_response(
                503,
                "cleanup_incomplete",
                "进程正在退出清理，已停止接受新任务",
            )

        operation = resolve_operation(request.method, request.url.path)

        with INFLIGHT.track():
            if operation is None:
                # 未登记的操作不参与熔断（没有稳定的 scope 名），但仍计入在飞数
                return await call_next(request)

            decision = breaker.allow(operation)
            request.scope[SCOPE_KEY] = decision.to_dict()

            if not decision.allowed:
                logger.warning("熔断拒绝：%s（%s）", operation, decision.code)
                return _error_response(
                    decision.status_code or 503,
                    decision.code or "breaker_open",
                    decision.detail,
                    retry_after=decision.retry_after_s,
                )

            try:
                response: Response = await call_next(request)
            except BaseException:
                breaker.record_failure(operation, "执行中抛出异常")
                raise

            if response.status_code >= FAILURE_STATUS_FLOOR:
                breaker.record_failure(operation, f"HTTP {response.status_code}")
            else:
                breaker.record_success(operation)

            response.headers[SAFETY_HEADER] = "pass"
            return response


def decision_from_scope(scope: Mapping[str, Any]) -> dict[str, Any] | None:
    """从 ASGI scope 取安全层结论（供审计中间件与测试使用）。"""
    value = scope.get(SCOPE_KEY)
    return dict(value) if isinstance(value, Mapping) else None


__all__ = [
    "FAILURE_STATUS_FLOOR",
    "SAFETY_HEADER",
    "SCOPE_KEY",
    "SafetyMiddleware",
    "decision_from_scope",
]
