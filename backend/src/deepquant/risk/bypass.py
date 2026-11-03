"""网关旁路检测：证明"没有一条路能绕过检查"，而不是"我们写了中间件"。

为什么需要单独一层检测
----------------------
中间件是全局的，所以直觉上"不可能绕过"。但真实项目里绕过从来不靠删中间件，
而是靠**新加一条路由忘了登记操作类型**：网关按 op_id 决定跑哪些检查点，
未登记的路由 op_id 为 ``None``，检查点 ``applies_to`` 全为假 → 一路放行，
而日志里看不出任何异常（因为"没有被拒绝"和"没有被检查"长得一样）。

因此本模块做的是**清单对账**，不是运行时拦截：

1. 扫描应用真实路由表（含方法），找出所有会改变状态的接口（POST/PUT/PATCH/DELETE）；
2. 与操作映射表对账，未映射的即为"可绕过面"，必须显式列入豁免并写明理由；
3. 反向核对：映射表里登记的路径在应用里真的存在（防止改了路由名而映射失效）。

豁免清单必须带理由
------------------
``EXEMPT_ROUTES`` 是"已知且可接受的不受检接口"。写成 ``{路径: 理由}`` 而不是集合：
一个没有理由的豁免，三个月后没人敢删，也说不清当初为什么加。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from deepquant.audit.middleware import OPERATION_ROUTES

#: 会改变状态的方法。只对这些做必检要求——只读接口不产生副作用，
#: 把它们全纳入会稀释"核心操作"的定义。
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 允许不受检的接口，附理由。新增条目必须写清"为什么它不需要检查"。
EXEMPT_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/health"): "健康检查，不触碰任何业务对象",
    ("GET", "/openapi.json"): "框架自带契约文档",
    ("GET", "/docs"): "框架自带文档页",
    ("GET", "/redoc"): "框架自带文档页",
}

#: 中间件类名（按名字核对，避免 import 顺序造成的类对象不一致）。
REQUIRED_MIDDLEWARE = ("RiskGateMiddleware", "AuditMiddleware")


def _iter_routes(router) -> list[Any]:
    """递归展开路由树。

    为什么不能直接读 ``app.routes``
    ------------------------------
    新版 FastAPI（0.143 / Starlette 1.9）把 ``include_router`` 的结果包成
    ``_IncludedRouter`` 惰性对象，路径**不会**出现在 ``app.routes`` 里。直接扫
    ``app.routes`` 会得到"只有 4 条框架路由、零个可变更接口"的假结论——那正是
    「没检出 ≠ 没问题」的翻版：检测器自己瞎了，报告却显示"通过"。

    因此这里沿 ``original_router`` 递归展开，并用 ``openapi()`` 的路径做交叉校验
    （见 :func:`_openapi_entries`），两条来源取并集，宁可多报也不漏报。
    """
    found: list[Any] = []
    for route in getattr(router, "routes", []) or []:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            found.extend(_iter_routes(nested))
            continue
        if getattr(route, "methods", None) is None and hasattr(route, "routes"):
            found.extend(_iter_routes(route))  # Mount
            continue
        found.append(route)
    return found


def _openapi_entries(app) -> list[tuple[str, str]]:
    """从 OpenAPI 文档取 (方法, 路径)；取不到时返回空列表。"""
    try:
        schema = app.openapi()
    except Exception:  # noqa: BLE001 - 文档生成失败不应让检测器崩掉
        return []
    entries: list[tuple[str, str]] = []
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.upper() in {"HEAD", "OPTIONS"}:
                continue
            entries.append((method.upper(), path))
    return entries


def _route_entries(app) -> list[tuple[str, str]]:
    """展开应用的 (方法, 路径) 清单：路由树 + OpenAPI 取并集。"""
    entries: set[tuple[str, str]] = set()
    for route in _iter_routes(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            entries.add((method.upper(), path))
    entries.update(_openapi_entries(app))
    return sorted(entries)


def _middleware_names(app) -> list[str]:
    names: list[str] = []
    for item in getattr(app, "user_middleware", []):
        cls = getattr(item, "cls", None)
        if cls is not None:
            names.append(getattr(cls, "__name__", str(cls)))
    return names


def bypass_report(
    app,
    *,
    operation_routes: Sequence[tuple[str, str, str]] | None = None,
    exempt: Mapping[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """对账报告。``ok=True`` 表示：无未登记的可变更接口、映射没有指向不存在的路由、
    两个中间件都已装配。"""
    routes = operation_routes if operation_routes is not None else OPERATION_ROUTES
    exempt_map = dict(exempt if exempt is not None else EXEMPT_ROUTES)

    entries = _route_entries(app)
    entry_set = set(entries)
    mapped = {(m.upper(), p) for m, p, _ in routes}

    mutating = {e for e in entries if e[0] in MUTATING_METHODS}
    uncovered = sorted(e for e in mutating if e not in mapped and e not in exempt_map)

    # 映射指向了不存在的路由（路由改名/删除后映射没跟着改）
    dangling = sorted(e for e in mapped if e not in entry_set)

    names = _middleware_names(app)
    missing_middleware = [n for n in REQUIRED_MIDDLEWARE if n not in names]

    # 映射里出现重复 op_id 对应的同一条路由，会导致记录重复
    duplicated: list[tuple[str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    for method, path, _op in routes:
        key = (method.upper(), path)
        seen[key] = seen.get(key, 0) + 1
    duplicated = sorted(k for k, v in seen.items() if v > 1)

    return {
        "ok": not uncovered and not dangling and not missing_middleware and not duplicated,
        "n_routes": len(entries),
        "n_mutating_routes": len(mutating),
        "n_mapped": len(mapped),
        "uncovered_mutating": [{"method": m, "path": p} for m, p in uncovered],
        "dangling_mappings": [{"method": m, "path": p} for m, p in dangling],
        "missing_middleware": missing_middleware,
        "duplicated_mappings": [{"method": m, "path": p} for m, p in duplicated],
        "exempt": [{"method": m, "path": p, "reason": r} for (m, p), r in sorted(exempt_map.items())],
        "middleware": names,
    }


def assert_no_bypass(app, **kwargs: Any) -> dict[str, Any]:
    """``bypass_report`` 的断言版本，供启动自检与测试调用。"""
    report = bypass_report(app, **kwargs)
    if report["ok"]:
        return report
    problems: list[str] = []
    if report["uncovered_mutating"]:
        problems.append(
            "存在未登记的可变更接口（网关不会检查它们）："
            + ", ".join(f"{i['method']} {i['path']}" for i in report["uncovered_mutating"])
        )
    if report["dangling_mappings"]:
        problems.append(
            "操作映射指向不存在的路由："
            + ", ".join(f"{i['method']} {i['path']}" for i in report["dangling_mappings"])
        )
    if report["missing_middleware"]:
        problems.append(f"未装配必需中间件：{report['missing_middleware']}")
    if report["duplicated_mappings"]:
        problems.append(
            "操作映射重复登记："
            + ", ".join(f"{i['method']} {i['path']}" for i in report["duplicated_mappings"])
        )
    raise AssertionError("；".join(problems))


def operation_ids(app, *, operation_routes: Iterable[tuple[str, str, str]] | None = None) -> set[str]:
    """中间件实际会覆盖的 op_id 集合（用于与核心操作注册表交叉核对）。"""
    routes = operation_routes if operation_routes is not None else OPERATION_ROUTES
    return {op for _, _, op in routes}


__all__ = [
    "EXEMPT_ROUTES",
    "MUTATING_METHODS",
    "REQUIRED_MIDDLEWARE",
    "assert_no_bypass",
    "bypass_report",
    "operation_ids",
]
