"""契约对账：把"声明的能力"和"真实的装配"逐项比对，不一致就失败。

为什么声明还不够
----------------
``registry.py`` 里的 ``CapabilitySpec`` 是一份手写声明。手写声明唯一的失效方式是
**漂移**：代码改了、声明没改。它不会报错——接口照常工作，声明继续声称旧契约，
直到有人按声明去对接才发现对不上，而那时排查方向通常已经偏了（"你们的文档说
返回 X"而不是"我们的契约登记过期了"）。

所以本模块做的是**双向对账**，两条方向都查：

1. 声明 → 实现：声明的能力在应用里真的有那条路由吗？输入输出模型真的是它吗？
   T1 的操作映射真的指向同一个操作类型吗？风控的必填字段真的与声明一致吗？
2. 实现 → 声明：应用里每一个**会改变状态**的路由，都在注册表里吗？
   T1 每个 ``in_scope`` 的核心操作，都有对应能力吗？

只查一个方向会漏掉最典型的两种事故：改了路由名（声明指向空气）和加了新接口
（新接口无人管）。前者靠方向 1，后者靠方向 2。

版本与摘要的牙齿
----------------
``lock`` 文件按 ``(cap_id, version)`` 记录契约摘要。规则很简单但很硬：

- 摘要变了而版本没变 → 失败。因为同一版本必须对应同一契约，否则"版本号"失去意义；
- 想改契约 → 升版本 → ``scripts/capabilities.py lock`` 追加一条历史记录。

追加历史而不是覆盖，使"这份契约什么时候变过"可查。工具本身**拒绝**用新摘要覆盖
同一版本的历史条目——这道门必须由工具把关，而不是靠人记得。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from deepquant.audit.middleware import OPERATION_ROUTES
from deepquant.audit.operations import CORE_OPERATIONS
from deepquant.capabilities.registry import (
    CAPABILITIES,
    all_capabilities,
    by_route,
    validate_registry,
)
from deepquant.capabilities.spec import (
    MUTATING_METHODS,
    TRANSPORT_HTTP,
    TRANSPORT_SERVICE,
    CapabilityContractError,
    CapabilitySpec,
    compare_versions,
)
from deepquant.risk.checkpoints import REQUIRED_FIELDS_BY_OPERATION

#: 已知且**故意**不登记为能力的路由，附理由。
#: 与 T2 的 ``EXEMPT_ROUTES`` 同一口径：没有理由的豁免，三个月后没人敢删，
#: 也说不清当初为什么加。
EXEMPT_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/health"): "健康检查，不触碰任何业务对象",
    ("GET", "/backtests"): "历史回测列表：只读查询，未纳入 T1 核心操作分母",
    ("GET", "/backtests/{run_id}"): "回测详情：只读查询，同上",
    ("GET", "/backtests/{run_id}/integrity"): "完整性报告：只读派生视图",
    ("GET", "/backtests/{run_id}/reproducibility"): "可复现性报告：只读派生视图",
}

#: 框架自带路由前缀，不参与契约对账（不是业务能力）。
_FRAMEWORK_PREFIXES: tuple[str, ...] = ("/openapi.json", "/docs", "/redoc")

LOCK_VERSION = 1


@dataclass
class Problem:
    """一条对账失败项。``kind`` 是稳定的机器可读标识，便于统计与豁免。"""

    kind: str
    capability: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "capability": self.capability, "detail": self.detail}


@dataclass
class ContractReport:
    """对账结果。"""

    ok: bool
    problems: list[Problem] = field(default_factory=list)
    checked_capabilities: int = 0
    checked_routes: int = 0
    lock_path: str | None = None

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for problem in self.problems:
            counts[problem.kind] = counts.get(problem.kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked_capabilities": self.checked_capabilities,
            "checked_routes": self.checked_routes,
            "lock_path": self.lock_path,
            "problem_counts": self.by_kind(),
            "problems": [p.to_dict() for p in self.problems],
        }


# --------------------------------------------------------------------------- #
# 路由探测
# --------------------------------------------------------------------------- #


def _route_objects(app) -> list[Any]:
    """展开应用的真实路由对象。

    复用 ``risk.bypass._iter_routes``：新版 FastAPI 把 ``include_router`` 包成惰性
    ``_IncludedRouter``，直接读 ``app.routes`` 只会看到 4 条框架路由。T2 已经踩过
    这个坑（检测器自己瞎了却报告通过），这里沿用同一实现，避免两处各修一遍。
    """
    from deepquant.risk.bypass import _iter_routes

    return _iter_routes(app)


def _model_of_body(route: Any) -> type | None:
    """路由声明的请求体模型；没有请求体返回 ``None``。"""
    dependant = getattr(route, "dependant", None)
    body_params = list(getattr(dependant, "body_params", None) or [])
    if not body_params:
        return None
    field_info = getattr(body_params[0], "field_info", body_params[0])
    annotation = getattr(field_info, "annotation", None)
    return annotation if isinstance(annotation, type) else None


def _response_model_of(route: Any) -> type | None:
    model = getattr(route, "response_model", None)
    return model if isinstance(model, type) else None


def _same_model(left: type | None, right: type | None) -> bool:
    """模型相等判定。

    先用对象同一性（同一进程内 import 同一个类必然是同一对象）；退回限定名比较，
    用于"模块被重复导入"这类边缘情况——此时对象不同但语义相同，报不一致会制造
    假故障，而假故障会让整套对账被当成噪声忽略。
    """
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if left is right:
        return True
    return f"{left.__module__}.{left.__qualname__}" == f"{right.__module__}.{right.__qualname__}"


def _route_index(app) -> dict[tuple[str, str], Any]:
    index: dict[tuple[str, str], Any] = {}
    for route in _route_objects(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method.upper() in {"HEAD", "OPTIONS"}:
                continue
            index[(method.upper(), path.rstrip("/") or "/")] = route
    return index


def _is_framework_route(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _FRAMEWORK_PREFIXES)


# --------------------------------------------------------------------------- #
# 应用对账
# --------------------------------------------------------------------------- #


def verify_app(
    app,
    *,
    capabilities: Sequence[CapabilitySpec] | None = None,
    operation_routes: Sequence[tuple[str, str, str]] | None = None,
    exempt: Mapping[tuple[str, str], str] | None = None,
) -> ContractReport:
    """把声明与实际装配逐项对账。返回报告而不抛错，便于一次看全部问题。"""
    caps = tuple(capabilities if capabilities is not None else CAPABILITIES)
    routes = tuple(operation_routes if operation_routes is not None else OPERATION_ROUTES)
    exempt_map = dict(exempt if exempt is not None else EXEMPT_ROUTES)

    problems: list[Problem] = []

    try:
        validate_registry(caps)
    except CapabilityContractError as exc:
        problems.append(Problem("registry_invalid", "-", str(exc)))

    route_index = _route_index(app)
    op_map = {(m.upper(), p.rstrip("/") or "/"): op for m, p, op in routes}
    core_ops = {op.op_id: op for op in CORE_OPERATIONS}

    for cap in caps:
        if cap.transport == TRANSPORT_HTTP:
            key = ((cap.method or "").upper(), (cap.path or "").rstrip("/") or "/")
            route = route_index.get(key)
            if route is None:
                problems.append(
                    Problem(
                        "route_missing",
                        cap.cap_id,
                        f"声明了 {key[0]} {key[1]}，但应用里没有这条路由",
                    )
                )
                continue

            actual_in = _model_of_body(route)
            if not _same_model(actual_in, cap.input_model):
                problems.append(
                    Problem(
                        "input_model_drift",
                        cap.cap_id,
                        f"输入模型不一致：声明 {_name(cap.input_model)}，实际 {_name(actual_in)}",
                    )
                )

            actual_out = _response_model_of(route)
            if not _same_model(actual_out, cap.output_model):
                problems.append(
                    Problem(
                        "output_model_drift",
                        cap.cap_id,
                        f"输出模型不一致：声明 {_name(cap.output_model)}，实际 {_name(actual_out)}",
                    )
                )

            mapped = op_map.get(key)
            if mapped != cap.operation_id:
                problems.append(
                    Problem(
                        "operation_mapping_drift",
                        cap.cap_id,
                        f"审计操作映射为 {mapped!r}，能力声明为 {cap.operation_id!r}"
                        "（两者不一致会让拒绝事件记到别的能力名下）",
                    )
                )
        else:
            core = core_ops.get(cap.operation_id)
            if core is None:
                problems.append(
                    Problem(
                        "operation_unregistered",
                        cap.cap_id,
                        f"操作 {cap.operation_id!r} 未登记在 CORE_OPERATIONS",
                    )
                )
            elif not core.in_scope:
                problems.append(
                    Problem(
                        "operation_out_of_scope",
                        cap.cap_id,
                        f"操作 {cap.operation_id!r} 在 T1 里标为不在范围，"
                        "能力层却声称覆盖它（口径不一致）",
                    )
                )

        # 必填字段：契约声明与风控实际执行必须一致。
        # 两处故意重复写，靠这里对账保持一致——像校验和，重复本身就是检查手段。
        risk_required = REQUIRED_FIELDS_BY_OPERATION.get(cap.operation_id)
        if risk_required is None:
            if "field" in cap.preconditions:
                problems.append(
                    Problem(
                        "precondition_unimplemented",
                        cap.cap_id,
                        f"声明了 field 前置条件，但风控对 {cap.operation_id!r} 没有必填字段表",
                    )
                )
        elif tuple(risk_required) != tuple(cap.required_fields):
            problems.append(
                Problem(
                    "required_fields_drift",
                    cap.cap_id,
                    f"必填字段不一致：契约 {list(cap.required_fields)}，"
                    f"风控 {list(risk_required)}",
                )
            )

    # 方向 2：实现 → 声明。未登记的会改变状态的路由即为"可绕过面"。
    for (method, path), _route in sorted(route_index.items()):
        if _is_framework_route(path):
            continue
        if (method, path) in exempt_map:
            continue
        if by_route(method, path) is not None:
            continue
        if method in MUTATING_METHODS:
            problems.append(
                Problem(
                    "uncovered_route",
                    f"{method} {path}",
                    "会改变状态的路由未登记为能力（新业务动作必须先声明契约）",
                )
            )
        elif (method, path) not in exempt_map:
            problems.append(
                Problem(
                    "uncovered_route",
                    f"{method} {path}",
                    "只读路由既未登记为能力，也未列入豁免清单（需显式选择其一）",
                )
            )

    # 方向 2 的另一半：T1 的核心操作必须有对应能力。
    covered_ops = {cap.operation_id for cap in caps}
    for op in CORE_OPERATIONS:
        if op.in_scope and op.op_id not in covered_ops:
            problems.append(
                Problem(
                    "uncovered_operation",
                    op.op_id,
                    "T1 统计为核心操作，却没有对应能力契约",
                )
            )

    # 豁免清单也要对账：路由改名/删除后，残留的豁免会变成"永远不再被检查"的隐形洞。
    for (method, path), reason in sorted(exempt_map.items()):
        if not (reason or "").strip():
            problems.append(
                Problem("exemption_without_reason", f"{method} {path}", "豁免条目未写明理由")
            )
        if (method.upper(), path.rstrip("/") or "/") not in route_index:
            problems.append(
                Problem(
                    "stale_exemption",
                    f"{method} {path}",
                    "豁免清单里的路由在应用中不存在（改名或删除后未清理豁免）",
                )
            )

    return ContractReport(
        ok=not problems,
        problems=problems,
        checked_capabilities=len(caps),
        checked_routes=len(route_index),
    )


def _name(model: type | None) -> str:
    return "None" if model is None else f"{model.__module__}.{model.__qualname__}"


def assert_app_contract(app, **kwargs: Any) -> None:
    """断言版本：不通过直接把所有问题拼成一条异常。"""
    report = verify_app(app, **kwargs)
    if not report.ok:
        detail = "；".join(f"[{p.kind}] {p.capability}: {p.detail}" for p in report.problems)
        raise CapabilityContractError(f"能力契约对账失败（{len(report.problems)} 项）：{detail}")


# --------------------------------------------------------------------------- #
# lock 文件
# --------------------------------------------------------------------------- #


def default_lock_path() -> Path:
    """默认 lock 路径：``backend/contracts/capabilities.lock.json``。

    放在源码树外一层（``backend/contracts``）而不是包内：它是**评审对象**
    （契约变更就是版本变更），和锁文件、迁移脚本同类，不该跟着包被 import。
    """
    override = os.environ.get("DEEPQUANT_CAPABILITY_LOCK", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "contracts" / "capabilities.lock.json"


def load_lock(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else default_lock_path()
    if not target.exists():
        return {"lock_version": LOCK_VERSION, "capabilities": {}}
    with target.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("lock_version") != LOCK_VERSION:
        raise CapabilityContractError(
            f"{target} 的 lock_version={data.get('lock_version')!r}，本代码只认 {LOCK_VERSION}"
        )
    return data


def compute_lock(
    capabilities: Iterable[CapabilitySpec] | None = None,
    *,
    previous: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """计算当前应写入的 lock 内容（在 ``previous`` 之上追加历史）。

    规则：同一版本只允许一个摘要。若 ``previous`` 里已有该版本但摘要不同，
    **拒绝生成**并抛错——必须由人升版本再跑落盘，这一步不能自动化。
    """
    caps = tuple(capabilities if capabilities is not None else CAPABILITIES)
    prev_caps: Mapping[str, Any] = dict((previous or {}).get("capabilities") or {})
    stamp = recorded_at or date.today().isoformat()

    out: dict[str, Any] = {}
    problems: list[str] = []
    for cap in caps:
        digest = cap.contract_digest()
        history: list[dict[str, Any]] = []
        prev_entry = prev_caps.get(cap.cap_id) or {}
        for item in prev_entry.get("history") or []:
            history.append(dict(item))

        same_version = next((h for h in history if h.get("version") == cap.version), None)
        if same_version is not None:
            if same_version.get("digest") != digest:
                problems.append(
                    f"{cap.cap_id}@{cap.version} 的契约摘要已变化"
                    f"（{same_version.get('digest')} → {digest}），但版本号没变；"
                    "请先升版本（不兼容改 major，兼容新增改 minor，纯实现修复改 patch）"
                )
            else:
                same_version["last_seen_at"] = stamp
        else:
            history.append(
                {
                    "version": cap.version,
                    "digest": digest,
                    "schema_digest": cap.input_schema_digest(),
                    "recorded_at": stamp,
                }
            )

        # 历史顺序校验：版本号严格递增，否则"什么时候改的"无法读。
        versions = [h.get("version", "0.0.0") for h in history]
        for before, after in zip(versions, versions[1:]):
            if compare_versions(before, after) >= 0:
                problems.append(
                    f"{cap.cap_id} 的历史版本未严格递增：{before} → {after}"
                )

        out[cap.cap_id] = {
            "current": cap.version,
            "history": history,
        }

    if problems:
        raise CapabilityContractError("lock 生成被拒绝：" + "；".join(problems))

    removed = sorted(set(prev_caps) - {c.cap_id for c in caps})
    return {
        "lock_version": LOCK_VERSION,
        "note": (
            "能力契约锁：每个 (cap_id, version) 对应唯一摘要。"
            "改契约必须升版本后重跑 scripts/capabilities.py lock；工具拒绝同版本覆盖。"
        ),
        "capabilities": {k: out[k] for k in sorted(out)},
        "removed_capabilities": removed,
    }


def write_lock(path: str | Path | None = None, **kwargs: Any) -> Path:
    """落盘 lock（显式 LF，避免 Windows 文本模式写入 CRLF 导致逐字节复核失败）。"""
    target = Path(path) if path is not None else default_lock_path()
    previous = load_lock(target) if target.exists() else None
    payload = compute_lock(previous=previous, **kwargs)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return target


def verify_lock(
    lock: Mapping[str, Any],
    capabilities: Iterable[CapabilitySpec] | None = None,
) -> list[Problem]:
    """核对 lock 与当前契约。空 lock（首次引入）视为不通过——没锁＝没约束。"""
    caps = tuple(capabilities if capabilities is not None else CAPABILITIES)
    entries: Mapping[str, Any] = dict(lock.get("capabilities") or {})
    problems: list[Problem] = []
    current_ids = {cap.cap_id for cap in caps}

    for cap in caps:
        entry = entries.get(cap.cap_id)
        if entry is None:
            problems.append(
                Problem("lock_missing", cap.cap_id, "lock 中没有该能力，无法证明契约未被改动")
            )
            continue
        if entry.get("current") != cap.version:
            problems.append(
                Problem(
                    "lock_version_mismatch",
                    cap.cap_id,
                    f"lock 记录当前版本 {entry.get('current')}，代码为 {cap.version}"
                    "（改了版本却没更新 lock）",
                )
            )
            continue
        digest = cap.contract_digest()
        recorded = next(
            (h for h in (entry.get("history") or []) if h.get("version") == cap.version), None
        )
        if recorded is None:
            problems.append(
                Problem("lock_no_digest", cap.cap_id, f"lock 中缺少 {cap.version} 的摘要记录")
            )
        elif recorded.get("digest") != digest:
            problems.append(
                Problem(
                    "contract_changed_without_version_bump",
                    cap.cap_id,
                    f"{cap.version} 的契约摘要已变化（lock {recorded.get('digest')} → "
                    f"实际 {digest}），必须升版本后重跑 lock",
                )
            )

    for cap_id in sorted(set(entries) - current_ids):
        problems.append(
            Problem("lock_stale", cap_id, "lock 里有该能力，注册表里已不存在（需重跑 lock）")
        )

    return problems


def verify_all(
    app,
    *,
    lock_path: str | Path | None = None,
    capabilities: Sequence[CapabilitySpec] | None = None,
) -> ContractReport:
    """应用对账 + lock 对账，合成一份报告（CI 与启动自检的唯一入口）。"""
    report = verify_app(app, capabilities=capabilities)
    target = Path(lock_path) if lock_path is not None else default_lock_path()
    try:
        lock = load_lock(target)
    except Exception as exc:  # noqa: BLE001 - lock 读不了同样是"无法证明"
        report.problems.append(Problem("lock_unreadable", "-", f"{type(exc).__name__}: {exc}"))
        report.ok = False
        report.lock_path = str(target)
        return report

    for problem in verify_lock(lock, capabilities=capabilities):
        report.problems.append(problem)

    report.ok = not report.problems
    report.lock_path = str(target)
    return report


def format_report(report: ContractReport) -> str:
    """人读格式（CLI 输出）。"""
    lines = [
        f"能力契约对账：{'PASS' if report.ok else 'FAIL'}",
        f"  能力数 {report.checked_capabilities}｜路由数 {report.checked_routes}"
        f"｜lock {report.lock_path or '-'}",
    ]
    if report.problems:
        lines.append(f"  失败 {len(report.problems)} 项，按类型：{report.by_kind()}")
        for problem in report.problems:
            lines.append(f"  - [{problem.kind}] {problem.capability}: {problem.detail}")
    return "\n".join(lines)


__all__ = [
    "EXEMPT_ROUTES",
    "LOCK_VERSION",
    "ContractReport",
    "Problem",
    "assert_app_contract",
    "compute_lock",
    "default_lock_path",
    "format_report",
    "load_lock",
    "verify_all",
    "verify_app",
    "verify_lock",
    "write_lock",
]
