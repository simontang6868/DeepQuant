"""T4 能力契约化验收取证：把四项硬性标准跑成数字，而不是写在文档里。

生成 ``reports/CAPABILITIES_T4_EVIDENCE.json``，覆盖：

1. 核心业务动作 100% schema 化 —— 能力数与 T1 核心操作数的双向覆盖；
2. 新技能上线必经契约校验 —— 声明↔实现双向对账 + 未登记路由拒绝；
3. 可授权、可禁用 —— 真实 HTTP 请求下的 403/409/观察期行为；
4. 契约不可悄然变更 —— 同版本改契约时 lock 工具拒绝落盘；
5. 与 T1 工件联动 —— 拒绝原因码可从工件按等值检索。

每个演示都用**真实请求**（TestClient）或**真实子进程级配置**（环境变量），
不构造"等价但更宽松"的路径——那样验的是脚本，不是系统。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from deepquant.api.app import create_app  # noqa: E402
from deepquant.api.config import get_data_root  # noqa: E402
from deepquant.audit.operations import CORE_OPERATIONS  # noqa: E402
from deepquant.audit.store import AuditStore  # noqa: E402
from deepquant.capabilities import (  # noqa: E402
    all_capabilities,
    assert_codes_complete,
    assert_registry_valid,
    capability_codes,
    default_lock_path,
    load_lock,
    verify_all,
)
from deepquant.capabilities.contract import EXEMPT_ROUTES  # noqa: E402
from deepquant.capabilities.middleware import UNREGISTERED_ACTION  # noqa: E402
from deepquant.capabilities.policy import (  # noqa: E402
    DISABLED_ENV,
    GRANTS_ENV,
    MODE_ENV,
    VERSION_HEADER,
)
from deepquant.capabilities.spec import CapabilityContractError  # noqa: E402

REPORT = ROOT / "reports" / "CAPABILITIES_T4_EVIDENCE.json"

_BAD_BODY = {"initial_cash": 100000}  # 缺三个必填字段：开闸时业务应回 422


@contextmanager
def _env(**kwargs: str | None) -> Iterator[None]:
    """临时改环境变量并完整还原（包括"原本不存在"的情形）。"""
    saved = {k: os.environ.get(k) for k in kwargs}
    try:
        for key, value in kwargs.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _client(tmp: Path) -> TestClient:
    app = create_app()
    data_root = tmp / "data"
    data_root.mkdir(exist_ok=True)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    return TestClient(app)


def _coverage() -> dict[str, Any]:
    """核心业务动作的 schema 化覆盖：两个方向都算。"""
    report = verify_all(create_app())
    core_in_scope = [op.op_id for op in CORE_OPERATIONS if op.in_scope]
    cap_ops = {cap.operation_id for cap in all_capabilities()}
    covered = [op for op in core_in_scope if op in cap_ops]
    uncovered_route = [p.to_dict() for p in report.problems if p.kind == "uncovered_route"]
    return {
        "core_operations_in_scope": len(core_in_scope),
        "core_operations_covered": len(covered),
        "coverage_ratio": round(len(covered) / len(core_in_scope), 4) if core_in_scope else 1.0,
        "http_capabilities": sum(1 for c in all_capabilities() if c.transport == "http"),
        "service_capabilities": sum(1 for c in all_capabilities() if c.transport == "service"),
        "exempt_routes": {f"{m} {p}": reason for (m, p), reason in sorted(EXEMPT_ROUTES.items())},
        "uncovered_routes": uncovered_route,
        "contract_ok": report.ok,
        "contract_problem_counts": report.by_kind(),
    }


def _enforcement_demos(tmp: Path) -> dict[str, Any]:
    """真实 HTTP 请求下的闸门行为。判据用"业务是否被执行"而不是"非 200"。"""
    demos: dict[str, Any] = {}

    with _env(DEEPQUANT_RISK_MODE="off", **{MODE_ENV: None, DISABLED_ENV: None, GRANTS_ENV: None}):
        response = _client(tmp).post("/backtests", json=_BAD_BODY)
        demos["off_baseline"] = {
            "status_code": response.status_code,
            "note": "默认关闭：请求进入业务校验（缺必填字段 → 422），无能力响应头",
            "has_capability_header": any(h.startswith("x-capability") for h in response.headers),
        }

    with _env(**{MODE_ENV: "enforce", DISABLED_ENV: "backtest.run"}):
        response = _client(tmp).post("/backtests", json=_BAD_BODY)
        demos["enforce_disabled"] = {
            "status_code": response.status_code,
            "reason_code": response.json().get("error", {}).get("code"),
            # 与 off_baseline 的 422 对比：403 说明业务**没有**被执行
            "business_executed": response.status_code == 422,
        }

    with _env(**{MODE_ENV: "enforce", DISABLED_ENV: None, GRANTS_ENV: "backtest.run=alice"}):
        response = _client(tmp).post("/backtests", json=_BAD_BODY, headers={"X-Operator": "bob"})
        demos["enforce_not_permitted"] = {
            "status_code": response.status_code,
            "reason_code": response.json().get("error", {}).get("code"),
        }

    with _env(**{MODE_ENV: "enforce", DISABLED_ENV: None, GRANTS_ENV: None}):
        response = _client(tmp).post(
            "/backtests", json=_BAD_BODY, headers={VERSION_HEADER: "2.0.0"}
        )
        demos["enforce_version_mismatch"] = {
            "status_code": response.status_code,
            "reason_code": response.json().get("error", {}).get("code"),
        }
        ok = _client(tmp).get("/strategies")
        demos["enforce_allowed_headers"] = {
            "status_code": ok.status_code,
            "capability": ok.headers.get("X-Capability-Id"),
            "version": ok.headers.get("X-Capability-Version"),
        }

    with _env(**{MODE_ENV: "observe", DISABLED_ENV: "backtest.run", GRANTS_ENV: None}):
        response = _client(tmp).post("/backtests", json=_BAD_BODY)
        demos["observe_would_reject"] = {
            "status_code": response.status_code,
            "would_reject": response.headers.get("X-Capability-Would-Reject"),
            "note": "观察期判定与强制期相同，只是不拦截；业务照常执行（422）",
        }

    with _env(**{MODE_ENV: "strict", DISABLED_ENV: None, GRANTS_ENV: None}):
        response = _client(tmp).get("/strategies")
        demos["invalid_mode_fails_closed"] = {
            "status_code": response.status_code,
            "reason_code": response.json().get("error", {}).get("code"),
        }

    return demos


def _unregistered_route_demo(tmp: Path, audit_dir: Path) -> dict[str, Any]:
    """未登记路由：拒绝 + 审计不留空洞。"""
    with _env(DEEPQUANT_RISK_MODE="off", **{MODE_ENV: "enforce", DISABLED_ENV: None,
                                            GRANTS_ENV: None,
                                            "DEEPQUANT_AUDIT_DIR": str(audit_dir)}):
        app = create_app()
        app.dependency_overrides[get_data_root] = lambda: (tmp / "data").resolve()

        @app.post("/ghost-action")
        def ghost_action() -> dict[str, str]:  # pragma: no cover - 只为构造未登记路由
            return {"ran": "yes"}

        response = TestClient(app).post("/ghost-action", json={})
        store = AuditStore(audit_dir)
        cards = store.query(action=UNREGISTERED_ACTION)
        return {
            "status_code": response.status_code,
            "reason_code": response.json().get("error", {}).get("code"),
            "audit_cards": len(cards),
            "audit_reason_code": (cards[0]["outcome"].get("reason_code") if cards else None),
        }


def _audit_link_demo(tmp: Path, audit_dir: Path) -> dict[str, Any]:
    """拒绝原因码可从 T1 工件按等值检索（T2/T4 共同要求）。"""
    with _env(DEEPQUANT_RISK_MODE="off", **{MODE_ENV: "enforce", DISABLED_ENV: "backtest.run",
                                            GRANTS_ENV: None,
                                            "DEEPQUANT_AUDIT_DIR": str(audit_dir)}):
        client = _client(tmp)
        client.post("/backtests", json=_BAD_BODY, headers={"X-Operator": "alice"})
        store = AuditStore(audit_dir)
        cards = store.query(reason_code="capability_disabled")
        card = cards[0] if cards else None
        return {
            "cards_by_reason_code": len(cards),
            "outcome_status": card["outcome"].get("status") if card else None,
            "capability_block": card["outcome"].get("capability") if card else None,
            "operator": card.get("operator") if card else None,
        }


def _lock_teeth(tmp: Path) -> dict[str, Any]:
    """同版本改契约必须被拒绝——这是"新技能上线必经契约校验"的硬约束。"""
    from deepquant.capabilities.contract import write_lock
    from deepquant.capabilities.registry import get
    from deepquant.capabilities.spec import CapabilitySpec
    import dataclasses

    lock_path = tmp / "capabilities.lock.json"
    write_lock(lock_path, recorded_at="2026-10-27")

    cap = get("backtest.run")
    assert cap is not None
    mutated = dataclasses.replace(
        cap, required_fields=("dataset_symbol", "strategy_id")  # 少两个必填字段
    )
    refused = False
    message = ""
    try:
        write_lock(lock_path, capabilities=[mutated], recorded_at="2026-10-27")
    except CapabilityContractError as exc:
        refused = True
        message = str(exc)

    # 升版本后应被接受，并保留历史
    bumped = dataclasses.replace(mutated, version="1.1.0")
    write_lock(lock_path, capabilities=[bumped], recorded_at="2026-10-27")
    entry = load_lock(lock_path)["capabilities"]["backtest.run"]

    return {
        "refused_same_version_change": refused,
        "message": message[:300],
        "accepts_after_version_bump": entry["current"] == "1.1.0",
        "history_versions": [h["version"] for h in entry["history"]],
    }


def build_evidence() -> dict[str, Any]:
    assert_registry_valid()
    assert_codes_complete()

    lock = load_lock()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        audit_dir = tmp / "audit"

        evidence: dict[str, Any] = {
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "criteria": {},
            "registry": {
                "n_capabilities": len(all_capabilities()),
                "lock_path": str(default_lock_path()),
                "lock_current": {
                    cap_id: entry["current"] for cap_id, entry in lock["capabilities"].items()
                },
                "digests": {cap.cap_id: cap.contract_digest() for cap in all_capabilities()},
                "reason_codes": [c["code"] for c in capability_codes()],
            },
        }

        coverage = _coverage()
        evidence["criteria"]["criterion_1_schema_coverage"] = coverage
        evidence["criteria"]["criterion_2_contract_enforced"] = {
            "app_vs_declaration_ok": coverage["contract_ok"],
            "uncovered_routes": coverage["uncovered_routes"],
            "unregistered_route_demo": _unregistered_route_demo(tmp, audit_dir),
            "lock_teeth": _lock_teeth(tmp),
        }
        evidence["criteria"]["criterion_3_disable_and_authorise"] = _enforcement_demos(tmp)
        evidence["criteria"]["criterion_4_auditable"] = _audit_link_demo(tmp, audit_dir)
    return evidence


def main() -> int:
    evidence = build_evidence()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    REPORT.write_text(text, encoding="utf-8", newline="\n")
    print(f"已写入 {REPORT}")
    print(json.dumps(evidence["criteria"], ensure_ascii=False, indent=2)[:2600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
