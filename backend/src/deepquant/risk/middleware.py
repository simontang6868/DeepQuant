"""风控中间件：在执行之前判定，拒绝时给出原因码并留痕。

为什么必须在中间件层，且必须装在审计中间件的**内层**
----------------------------------------------------
1. **之前**：fail-closed 的意义是"不执行"，等到路由里发现不对已经执行了一半
   （回测被拒绝但数据库已写入、面板已刷新），那就只是"事后报错"。
2. **内层**：审计中间件要能看到网关返回的 403/409 并落成工件。若网关在外层，
   被拒绝的请求根本不会经过审计中间件，工件里会留下一片"没有记录"的空白——
   而缺口和"没人操作过"长得一模一样。

装配顺序由 ``api/app.py`` 保证（审计后添加 → 位于外层），并由
``tests/risk/test_risk_gateway_api.py`` 用真实请求断言（拒绝也会落卡）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from deepquant.audit.middleware import RISK_SCOPE_KEY, resolve_operation
from deepquant.risk.checkpoints import OperationContext, RiskLimits
from deepquant.risk.gateway import (
    MODE_OFF,
    GateResult,
    RiskConfigurationError,
    build_default_gateway,
    risk_mode,
)

logger = logging.getLogger(__name__)

#: 请求作用域里存放判定结果的键名。**常量定义在审计层**（``RISK_SCOPE_KEY``）：
#: 审计中间件读它，若两处各写一份字面量，改一处就会静默读不到，工件里永远没有原因码。
SCOPE_KEY = RISK_SCOPE_KEY

#: 响应头：本次请求的风控结论（pass / would-reject / rejected）。
RISK_HEADER = "X-DeepQuant-Risk"

#: 请求体解析上限：超过即不解析（由限额检查点负责拒绝），避免为了检查而吃掉内存。
_PARSE_LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class RiskLookups:
    """注入给状态检查点的查询能力。全部返回 ``None`` 表示"不存在"，而不是抛异常。"""

    dataset_lookup: Callable[[str], Any] | None = None
    strategy_lookup: Callable[[], Any] | None = None
    run_lookup: Callable[[str], Any] | None = None
    limits: RiskLimits | None = None


def _parse_payload(raw: bytes) -> Mapping[str, Any] | None:
    if not raw or len(raw) > _PARSE_LIMIT:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """网关自身故障时也要给出与既有契约一致的结构，而不是裸 500。"""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": {"checkpoint": "gateway", "category": "internal"},
            }
        },
        headers={RISK_HEADER: "rejected"},
    )


class RiskGateMiddleware(BaseHTTPMiddleware):
    """在业务执行之前跑四类检查点。"""

    def __init__(self, app, *, lookups: RiskLookups | None = None) -> None:
        super().__init__(app)
        self.lookups = lookups or RiskLookups()

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        try:
            mode = risk_mode()
        except RiskConfigurationError as exc:
            logger.error("风控配置非法，按不通过处理：%s", exc)
            return _error_response(503, "gateway_internal_error", str(exc))

        if mode == MODE_OFF:
            return await call_next(request)

        operation = resolve_operation(request.method, request.url.path)
        if operation is None:
            return await call_next(request)

        raw = await request.body()
        payload = _parse_payload(raw)
        ctx = OperationContext(
            operation=operation,
            method=request.method.upper(),
            path=request.url.path,
            operator=(request.headers.get("X-Operator") or "").strip() or None,
            session=(request.headers.get("X-Session-Id") or "").strip() or None,
            payload=payload,
            query=dict(request.query_params),
            body_bytes=len(raw),
        )

        gateway = build_default_gateway(
            dataset_lookup=self.lookups.dataset_lookup,
            strategy_lookup=self.lookups.strategy_lookup,
            run_lookup=self.lookups.run_lookup,
            limits=self.lookups.limits,
        )
        try:
            result = gateway.evaluate(ctx, mode=mode)
        except Exception as exc:  # noqa: BLE001 - 网关自身异常不得放行
            logger.error("风控判定过程异常，按不通过处理：%s", exc)
            return _error_response(503, "gateway_internal_error", f"风控判定异常：{type(exc).__name__}")

        # 无论通过与否都写进作用域：审计中间件据此把原因码写进 T1 工件。
        request.scope[SCOPE_KEY] = result.to_audit_dict()

        if not result.allowed:
            return JSONResponse(
                status_code=result.status_code,
                content=result.to_response_body(),
                headers={RISK_HEADER: "rejected"},
            )

        response: Response = await call_next(request)
        response.headers[RISK_HEADER] = "would-reject" if result.would_reject else "pass"
        if result.would_reject:
            # 观察期的核心产物：这条日志与工件里的 would_reject 就是"上线后会拦多少"。
            logger.warning(
                "风控观察期：%s %s 本应被拒绝（%s / %s），当前未拦截",
                request.method.upper(),
                request.url.path,
                result.reason_code,
                None if result.rejection is None else result.rejection.checkpoint,
            )
        return response


def decision_from_scope(scope: Mapping[str, Any]) -> dict[str, Any] | None:
    """从 ASGI scope 取出风控结论（供审计中间件使用）。"""
    value = scope.get(SCOPE_KEY)
    return dict(value) if isinstance(value, Mapping) else None


__all__ = [
    "RISK_HEADER",
    "SCOPE_KEY",
    "RiskGateMiddleware",
    "RiskLookups",
    "decision_from_scope",
]
