"""审计中间件：把「一次接口操作」自动落成 T1 工件（默认关闭，零侵入）。

为什么用中间件而不是在路由里逐处插代码
--------------------------------------
T1 的验收要求是覆盖率 100% 且不可依赖「人工记得写」。逐处插 ``with ...`` 的写法
有两个问题：新加一条路由时没人会记得补；改动核心链路会让既有 1997 项测试的
行为基线变得可疑。中间件把「记录」变成路由之外的横切关注点，新增路由只需在
``OPERATION_ROUTES`` 里加一行（覆盖率检查会提醒）。

默认关闭的含义必须说清
----------------------
只有设置了 ``DEEPQUANT_AUDIT_DIR`` 才记录。未设置时 ``dispatch`` 立刻放行：
不读请求体、不建目录、不加响应头。这样既有部署与测试的行为完全不变，
而不是「多写了一个空目录，反正没影响」。

写入失败：不阻断业务，但绝不静默
--------------------------------
中间件在**业务已经执行完之后**才写工件。此时若因磁盘/权限失败就抛错，会让
客户端收到 500，而实际上交易或回测已经完成——那是在制造新的不确定性。
所以本层的策略是：记 ``self_failures.jsonl``（append-only）＋ 响应头
``X-DeepQuant-Audit: failed`` ＋ 错误日志，三处留痕。

「拒绝执行」的语义属于 T2（fail-closed 风控网关）：它必须在**执行之前**判断，
而不是在执行之后靠审计层补救。两者职责不同，不要混在一个中间件里。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from deepquant.audit.operations import audit_enabled, audit_root, runtime_env
from deepquant.audit.store import AuditStoreError, open_store

logger = logging.getLogger(__name__)

#: 响应头：本次请求的工件状态（recorded / failed / skipped）。
AUDIT_HEADER = "X-DeepQuant-Audit"

#: 操作人请求头。缺省记为 anonymous，并在工件里写明「不具备追责效力」。
OPERATOR_HEADER = "X-Operator"

#: 会话请求头，用于按会话回放。
SESSION_HEADER = "X-Session-Id"

#: 请求体超过该字节数时只记摘要，不记内容（避免工件膨胀与敏感信息扩散）。
_BODY_RECORD_LIMIT = 64 * 1024

#: 路由 → 操作类型。只做**精确路径**匹配：带路径参数的接口若按前缀匹配，
#: 会把 /backtests/{run_id} 误记成 backtest.run，让「执行」与「查询」混淆。
OPERATION_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/backtests", "backtest.run"),
    ("POST", "/backtests/compare", "backtest.compare"),
    ("POST", "/portfolio-backtests", "portfolio.backtest"),
    ("GET", "/datasets", "dataset.list"),
    ("GET", "/strategies", "strategy.list"),
)

#: 风控中间件把判定结果放在这个 ASGI scope 键下。
#: 此处按**字符串**读取而不 import risk 包：risk 已依赖本模块的 ``resolve_operation``，
#: 反向 import 会形成循环；同时让"审计不依赖风控是否安装"这一边界保持干净。
RISK_SCOPE_KEY = "deepquant.risk"

#: 能力闸门（T4）把判定结果放在这个 ASGI scope 键下。理由同上：
#: capabilities 包依赖本模块的 ``resolve_operation``，此处只按字符串读，不反向 import。
CAPABILITY_SCOPE_KEY = "deepquant.capability"

#: 安全层（T5）把熔断判定放在这个 ASGI scope 键下。同上：只按字符串读。
SAFETY_SCOPE_KEY = "deepquant.safety"

#: 自检失败日志文件名（位于审计根目录下，与卡片分片分开）。
SELF_FAILURE_LOG = "self_failures.jsonl"


def resolve_operation(method: str, path: str) -> str | None:
    """把 (方法, 路径) 映射为操作类型；未登记返回 ``None``。"""
    method = method.upper()
    normalized = path.rstrip("/") or "/"
    for route_method, route_path, op_id in OPERATION_ROUTES:
        if method == route_method and normalized == route_path:
            return op_id
    return None


def registered_operations() -> Sequence[str]:
    """中间件实际承担的操作类型（供覆盖率检查交叉核对注册表）。"""
    return tuple(sorted({op for _, _, op in OPERATION_ROUTES}))


def _operator_of(request: Request) -> tuple[str, list[str]]:
    raw = (request.headers.get(OPERATOR_HEADER) or "").strip()
    if raw:
        return raw, []
    return "anonymous", [
        f"请求未带 {OPERATOR_HEADER} 头，操作人记为 anonymous；该工件不具备追责效力"
    ]


def _status_class(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "ok"
    if 400 <= status_code < 500:
        return "rejected"
    return "failed"


async def _request_summary(request: Request) -> tuple[dict[str, Any], list[str]]:
    """请求摘要：方法/路径/查询参数/请求体（含摘要）。

    读请求体是安全的：Starlette 的 ``BaseHTTPMiddleware`` 会缓存并向后重放 body
    （``_CachedRequest``），下游路由仍能正常解析。为防工件膨胀，超限只记摘要。
    """
    caveats: list[str] = []
    body_bytes = await request.body()
    summary: dict[str, Any] = {
        "method": request.method,
        "path": request.url.path,
        "query": dict(request.query_params),
        "client": request.client.host if request.client else None,
        "content_type": request.headers.get("content-type"),
        "body_bytes": len(body_bytes),
        "body_sha256": _sha256(body_bytes) if body_bytes else None,
    }
    if body_bytes:
        if len(body_bytes) > _BODY_RECORD_LIMIT:
            caveats.append(
                f"请求体 {len(body_bytes)} 字节超过 {_BODY_RECORD_LIMIT} 字节上限，"
                "工件只记录摘要，不记录内容"
            )
        else:
            try:
                summary["body"] = json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                caveats.append("请求体不是可解析 JSON，工件只记录摘要")
    return summary, caveats


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _log_self_failure(root: Path, payload: Mapping[str, Any]) -> None:
    """把「工件没写成功」这件事本身写进 append-only 日志。

    这是防止「审计静默空洞」的最后一道保险：即使卡片没落下，也知道**哪次操作**
    没落下，而不是留下一个无人知晓的缺口。
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with (root / SELF_FAILURE_LOG).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
    except Exception as exc:  # noqa: BLE001 - 兜底不得再抛，否则会盖住真正的问题
        logger.error("审计自检失败日志也无法写入：%s", exc)


class AuditMiddleware(BaseHTTPMiddleware):
    """把已登记接口的每次调用落成 T1 工件。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not audit_enabled():
            return await call_next(request)

        op_id = resolve_operation(request.method, request.url.path)
        if op_id is None:
            return await call_next(request)

        root = audit_root()
        assert root is not None  # audit_enabled 已保证
        operator, caveats = _operator_of(request)
        session = (request.headers.get(SESSION_HEADER) or "").strip() or None

        request_summary, body_caveats = await _request_summary(request)
        caveats.extend(body_caveats)

        basis = {
            "rule": "接口入参由 Pydantic 模型校验后执行；本工件记录请求摘要与响应状态",
            "request": request_summary,
            "route_operation_map": f"{request.method.upper()} {request.url.path} → {op_id}",
        }

        env_extra: dict[str, Any] = {}
        try:  # 构建指纹开销小（lru_cache），但不可用时不应影响业务
            from deepquant.api.buildinfo import build_id

            env_extra["build_id"] = build_id()
        except Exception:  # noqa: BLE001
            caveats.append("未能获取代码构建指纹（build_id 不可用）")

        store = open_store(root)
        assert store is not None

        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except BaseException as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            failure_outcome: dict[str, Any] = {
                "status": "failed",
                "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
                "duration_ms": duration_ms,
            }
            risk_failure, risk_caveats = _risk_annotation(request)
            if risk_failure is not None:
                failure_outcome["risk"] = risk_failure
                if risk_failure.get("reason_code"):
                    failure_outcome["reason_code"] = risk_failure["reason_code"]
            capability_failure, capability_caveats = _capability_annotation(request)
            if capability_failure is not None:
                failure_outcome["capability"] = capability_failure
                if capability_failure.get("reason_code"):
                    failure_outcome.setdefault("reason_code", capability_failure["reason_code"])
            safety_failure, safety_caveats = _safety_annotation(request)
            if safety_failure is not None:
                failure_outcome["safety"] = safety_failure
                if safety_failure.get("reason_code"):
                    failure_outcome.setdefault("reason_code", safety_failure["reason_code"])
            _record(
                store=store,
                root=Path(root),
                action=op_id,
                operator=operator,
                basis=basis,
                outcome=failure_outcome,
                caveats=caveats
                + risk_caveats
                + capability_caveats
                + safety_caveats
                + ["处理过程中抛出异常，业务结果未返回给调用方"],
                session=session,
                env_extra=env_extra,
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        outcome: dict[str, Any] = {
            "status": _status_class(response.status_code),
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        # 风控结论（T2）：拒绝原因码写进工件，使「谁被什么理由拒绝」可等值检索。
        # 该键由 risk 中间件写入；未装风控时不存在，工件里如实缺省。
        risk_decision, risk_caveats = _risk_annotation(request)
        if risk_decision is not None:
            outcome["risk"] = risk_decision
            if risk_decision.get("reason_code"):
                outcome["reason_code"] = risk_decision["reason_code"]
        caveats.extend(risk_caveats)

        # 能力结论（T4）：关闭/未授权/版本不兼容的原因码同样进工件，可等值检索。
        capability_decision, capability_caveats = _capability_annotation(request)
        if capability_decision is not None:
            outcome["capability"] = capability_decision
            if capability_decision.get("reason_code"):
                outcome.setdefault("reason_code", capability_decision["reason_code"])
        caveats.extend(capability_caveats)

        # 安全层结论（T5）：熔断拒绝同样进工件，可按 reason_code=breaker_open 检索。
        safety_decision, safety_caveats = _safety_annotation(request)
        if safety_decision is not None:
            outcome["safety"] = safety_decision
            if safety_decision.get("reason_code"):
                outcome.setdefault("reason_code", safety_decision["reason_code"])
        caveats.extend(safety_caveats)

        if response.status_code >= 400:
            caveats.append(
                "响应状态码 ≥400：业务被拒绝或失败，具体原因见服务端错误响应与日志"
            )

        result = _record(
            store=store,
            root=Path(root),
            action=op_id,
            operator=operator,
            basis=basis,
            outcome=outcome,
            caveats=caveats,
            session=session,
            env_extra=env_extra,
        )
        response.headers[AUDIT_HEADER] = "recorded" if result else "failed"
        return response


def _risk_annotation(request: Request) -> tuple[dict[str, Any] | None, list[str]]:
    """读取风控中间件写入的判定结果，转成「工件字段 + 注意事项」。

    未装风控时返回 ``(None, [])``，工件里就不出现 ``risk`` 字段——如实缺省，
    而不是填一个"看起来通过了"的空壳。
    """
    raw = request.scope.get(RISK_SCOPE_KEY)
    if not isinstance(raw, Mapping):
        return None, []

    decision = dict(raw)
    caveats: list[str] = []
    if decision.get("would_reject"):
        caveats.append(
            "风控观察期：本次操作在强制模式下会被拒绝，当前未拦截"
            f"（reason_code={decision.get('reason_code')}，"
            f"checkpoint={decision.get('checkpoint')}）"
        )
    if decision.get("unverifiable"):
        caveats.append("风控拒绝理由为「无法校验」：判断所依赖的数据缺失或过期，按不通过处理")
    return decision, caveats


def _capability_annotation(request: Request) -> tuple[dict[str, Any] | None, list[str]]:
    """读取能力闸门写入的判定结果，转成「工件字段 + 注意事项」。

    与 ``_risk_annotation`` 同构：未装能力层时返回 ``(None, [])``，工件里不出现
    ``capability`` 字段——如实缺省，而不是填一个"看起来通过了"的空壳。

    能力结论单独一个键而不是并进 ``risk``：两者的判定依据不同（一个看请求是否
    合法，一个看功能是否对你开放），合并会让"被哪个层拒绝"变得不可区分。
    """
    raw = request.scope.get(CAPABILITY_SCOPE_KEY)
    if not isinstance(raw, Mapping):
        return None, []

    decision = dict(raw)
    caveats: list[str] = []
    if decision.get("would_reject"):
        caveats.append(
            "能力观察期：本次操作在强制模式下会被拒绝，当前未拦截"
            f"（reason_code={decision.get('reason_code')}，"
            f"capability={decision.get('capability')}）"
        )
    if decision.get("reason_code") == "capability_version_unsupported":
        caveats.append("调用方钉的契约版本与本服务 major 不兼容：需要调用方改代码，重试无效")
    return decision, caveats


def _safety_annotation(request: Request) -> tuple[dict[str, Any] | None, list[str]]:
    """读取安全层（熔断）写入的判定结果。

    与另外两层的注解同构：未装安全层时返回 ``(None, [])``，工件里如实缺省。
    """
    raw = request.scope.get(SAFETY_SCOPE_KEY)
    if not isinstance(raw, Mapping):
        return None, []

    decision = dict(raw)
    caveats: list[str] = []
    if decision.get("reason_code") == "breaker_open":
        caveats.append(
            "本次操作被熔断拦下：该类操作近期连续失败，冷却结束后才会放行"
            f"（scope={decision.get('scope')}，剩余 {decision.get('retry_after_s')}s）"
        )
    return decision, caveats


def _record(
    *,
    store,
    root: Path,
    action: str,
    operator: str,
    basis: Mapping[str, Any],
    outcome: Mapping[str, Any],
    caveats: Sequence[str],
    session: str | None,
    env_extra: Mapping[str, Any],
) -> dict[str, Any] | None:
    """写一张工件；失败时降级为自检日志，不抛给业务。"""
    try:
        return store.append(
            action=action,
            operator=operator,
            basis=basis,
            outcome=outcome,
            caveats=list(caveats) + ["中间件层工件：仅覆盖请求摘要与响应状态，不含业务内部判据"],
            subject={"path": basis.get("request", {}).get("path")},
            inputs={
                "body_sha256": basis.get("request", {}).get("body_sha256"),
                "body_bytes": basis.get("request", {}).get("body_bytes"),
            },
            env=runtime_env(dict(env_extra)),
            session=session,
            extra={"source": "middleware"},
        )
    except AuditStoreError as exc:
        logger.error("审计工件写入失败：%s", exc)
        _log_self_failure(
            root,
            {
                "event": "audit_write_failed",
                "action": action,
                "operator": operator,
                "session": session,
                "error": f"{type(exc).__name__}: {exc}",
                "occurred_at": datetime.now(UTC).isoformat(timespec="microseconds"),
            },
        )
        return None


__all__ = [
    "AUDIT_HEADER",
    "CAPABILITY_SCOPE_KEY",
    "OPERATION_ROUTES",
    "OPERATOR_HEADER",
    "RISK_SCOPE_KEY",
    "SAFETY_SCOPE_KEY",
    "SESSION_HEADER",
    "SELF_FAILURE_LOG",
    "AuditMiddleware",
    "registered_operations",
    "resolve_operation",
]
