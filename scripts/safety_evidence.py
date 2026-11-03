"""T5 安全边界验收取证：四条标准跑成数字，全部为真实执行。

生成 ``reports/SAFETY_T5_EVIDENCE.json``。四条标准对应计划 §5.2 T5 的验收措辞：

1. 非白名单回调 100% 拒绝（含内网地址穿透防护）
2. 权限默认最小化，提权需显式授予
3. 熔断开关演练一次通过
4. 模拟崩溃：挂单在超时窗内撤销 / 清理日志完整 / 未确认清空前重启被拒

第 4 条必须说清差异：本仓库没有委托域，"撤单"映射为"在飞请求在超时窗内排空"，
并把无对应业务面的步骤登记为 not_applicable（见 ``NOT_APPLICABLE_STEPS``）。
取证里同时给出"超时窗内排空成功"与"超时未排空即失败"两种结果，避免只报喜。

每条演示都用真实请求或真实文件状态，不构造"等价但更宽松"的路径。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.safety import least_privilege  # noqa: E402
from deepquant.safety.breaker import (  # noqa: E402
    allow,
    breaker_scope,
    load,
    record_failure,
    record_success,
    trip,
)
from deepquant.safety.lifecycle import (  # noqa: E402
    INFLIGHT,
    NOT_APPLICABLE_STEPS,
    CleanupStep,
    DirtyShutdown,
    assert_clean_start,
    cleanup_log_entries,
    end_session,
    pending_cleanup,
    run_cleanup,
    snapshot,
    stale_session,
    verify_log_chain,
)
from deepquant.safety.outbound import (  # noqa: E402
    UrlPolicy,
    check,
    scan_url_literals,
)

REPORT = ROOT / "reports" / "SAFETY_T5_EVIDENCE.json"
PUBLIC = ["93.184.216.34"]


def _policy() -> UrlPolicy:
    return UrlPolicy(
        allowed_hosts=frozenset({"example.com", "*.example.com"}),
        resolver=lambda host, port: PUBLIC,
    )


# --------------------------------------------------------------------------- #
# 1. 出站：穿透用例全表
# --------------------------------------------------------------------------- #

#: (URL, 期望原因码)。``None`` 表示应当放行（对照组）。
BYPASS_CASES: list[tuple[str, str | None]] = [
    # 对照组：白名单内公网地址必须放行，否则"全拒"毫无意义
    ("https://example.com/callback", None),
    ("https://api.example.com/callback", None),
    # 白名单外
    ("https://evil.io/callback", "outbound_not_whitelisted"),
    ("https://example.com.evil.io/", "outbound_not_whitelisted"),
    # 协议走私
    ("file:///etc/passwd", "outbound_scheme_forbidden"),
    ("ftp://example.com/x", "outbound_scheme_forbidden"),
    ("gopher://example.com/", "outbound_scheme_forbidden"),
    # 环回与私网（IP 字面量）
    ("http://127.0.0.1/", "outbound_loopback"),
    ("http://[::1]/", "outbound_loopback"),
    ("http://10.0.0.5/", "outbound_private_address"),
    ("http://192.168.1.1/", "outbound_private_address"),
    ("http://172.16.0.1/", "outbound_private_address"),
    # 云元数据
    ("http://169.254.169.254/latest/meta-data/", "outbound_metadata_endpoint"),
    ("http://100.100.100.200/latest/meta-data/", "outbound_metadata_endpoint"),
    # 歧义数字写法（浏览器/inet_aton 都指向 127.0.0.1）
    ("http://2130706433/", "outbound_loopback"),
    ("http://0x7f000001/", "outbound_loopback"),
    ("http://0x7f.0.0.1/", "outbound_loopback"),
    ("http://0177.0.0.1/", "outbound_loopback"),
    ("http://127.1/", "outbound_loopback"),
    ("http://017700000001/", "outbound_loopback"),
    # IPv6 映射
    ("http://[::ffff:127.0.0.1]/", "outbound_loopback"),
    # userinfo / 反斜杠
    ("http://example.com@127.0.0.1/", "outbound_ambiguous_host"),
    ("http://user:pw@example.com/", "outbound_ambiguous_host"),
    ("http://example.com\\@127.0.0.1/", "outbound_ambiguous_host"),
    # 保留/未指定
    ("http://0.0.0.0/", "outbound_reserved_address"),
    ("http://224.0.0.1/", "outbound_reserved_address"),
    # 结构错误
    ("example.com/callback", "outbound_malformed_url"),
    ("https:///callback", "outbound_malformed_url"),
]


def _outbound_evidence() -> dict[str, object]:
    policy = _policy()
    rows: list[dict[str, object]] = []
    blocked = 0
    must_block = 0
    mismatches: list[dict[str, object]] = []
    for url, expected in BYPASS_CASES:
        decision = check(url, policy)
        rows.append(
            {
                "url": url,
                "expected": expected,
                "allowed": decision.allowed,
                "code": decision.code,
            }
        )
        if expected is None:
            if not decision.allowed:
                mismatches.append({"url": url, "why": "对照组被误拦", "code": decision.code})
            continue
        must_block += 1
        if not decision.allowed:
            blocked += 1
        if decision.code != expected:
            mismatches.append(
                {"url": url, "why": "原因码不符", "expected": expected, "got": decision.code}
            )

    # DNS 解析到内网 + 多记录（前公网后私网）
    dns_cases = {
        "resolves_private": check(
            "https://example.com/x", UrlPolicy(frozenset({"example.com"}), resolver=lambda h, p: ["10.0.0.5"])
        ).code,
        "second_record_private": check(
            "https://example.com/x",
            UrlPolicy(frozenset({"example.com"}), resolver=lambda h, p: PUBLIC + ["127.0.0.1"]),
        ).code,
        "resolver_error": check(
            "https://example.com/x",
            UrlPolicy(frozenset({"example.com"}), resolver=lambda h, p: (_ for _ in ()).throw(OSError("dns down"))),
        ).code,
    }

    scan = scan_url_literals(ROOT / "backend" / "src")

    return {
        "must_block_total": must_block,
        "blocked": blocked,
        "block_rate": round(blocked / must_block, 4) if must_block else 1.0,
        "control_allowed": all(r["allowed"] for r in rows if r["expected"] is None),
        "mismatches": mismatches,
        "dns_cases": dns_cases,
        "source_scan": {
            "ok": scan.ok,
            "n_urls": scan.n_urls,
            "note": "当前 backend/src 无 URL 字面量；绊线本身可用（见测试用 tmp 源码树验证）",
        },
        "cases": rows,
    }


# --------------------------------------------------------------------------- #
# 2. 最小权限
# --------------------------------------------------------------------------- #


def _privilege_evidence() -> dict[str, object]:
    mutating = ("mutating", "reads_dataset")
    read_only = ("read_only", "reads_db")

    def run(operator: str, tags: tuple[str, ...], granted, mode: str = "minimal") -> dict[str, object]:
        decision = least_privilege.check(
            "backtest.run" if "mutating" in tags else "dataset.list",
            tags,
            operator,
            granted=granted,
            current_mode=mode,
        )
        return {"allowed": decision.allowed, "code": decision.code}

    matrix = least_privilege.matrix_from_registry()
    return {
        "default_mode": least_privilege.mode(),
        "minimal_anonymous_mutating": run("anonymous", mutating, frozenset({"alice"})),
        "minimal_mutating_no_grant": run("alice", mutating, None),
        "minimal_mutating_granted": run("alice", mutating, frozenset({"alice"})),
        "minimal_mutating_not_listed": run("bob", mutating, frozenset({"alice"})),
        "minimal_readonly_identified": run("alice", read_only, None),
        "open_mode_allows": run("anonymous", mutating, None, mode="open"),
        "matrix": {
            "n_capabilities": matrix["n_capabilities"],
            "n_mutating": matrix["n_mutating"],
            "caveat": matrix["caveat"],
        },
    }


# --------------------------------------------------------------------------- #
# 3. 熔断演练
# --------------------------------------------------------------------------- #


def _breaker_drill(safety_dir: Path) -> dict[str, object]:
    os.environ["DEEPQUANT_SAFETY_DIR"] = str(safety_dir)
    drill: dict[str, object] = {}

    drill["initial"] = allow("backtest.run").allowed

    start = 1_700_000_000.0
    for _ in range(3):
        record_failure("backtest.run", "演练：连续失败", now=start)
    opened = allow("backtest.run", now=start + 1)
    drill["after_3_failures"] = {
        "allowed": opened.allowed,
        "code": opened.code,
        "retry_after_s": opened.retry_after_s,
    }

    # 重启不清零：状态只从磁盘读，等价于新进程第一次请求
    drill["survives_restart"] = {
        "allowed": allow("backtest.run", now=start + 2).allowed,
        "state": load("backtest.run").state,
    }

    # 冷却过后进入半开并允许探测
    half = allow("backtest.run", now=start + 61)
    drill["half_open"] = {"allowed": half.allowed, "state": half.state}

    # 探测成功 → 闭合
    record_success("backtest.run", now=start + 62)
    drill["after_probe_success"] = load("backtest.run").state

    # 第二条路径（CLI/脚本）同样被拦
    trip("backtest.run", "演练：人工强制熔断")
    try:
        with breaker_scope("backtest.run"):
            drill["cli_path"] = "unexpected_pass"
    except Exception as exc:  # noqa: BLE001 - 演练要的就是这个
        drill["cli_path"] = f"blocked:{type(exc).__name__}"

    # HTTP 路径：真实请求被 503 拦下（业务未执行）
    from fastapi.testclient import TestClient

    from deepquant.api.app import create_app
    from deepquant.api.config import get_data_root

    app = create_app()
    data_root = safety_dir.parent / "data"
    data_root.mkdir(exist_ok=True)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    client = TestClient(app)
    response = client.post("/backtests", json={"initial_cash": 100000})
    drill["http_path"] = {
        "status_code": response.status_code,
        "reason_code": response.json().get("error", {}).get("code"),
        "retry_after": response.headers.get("Retry-After"),
        "business_executed": response.status_code == 422,
    }
    return drill


# --------------------------------------------------------------------------- #
# 4. 崩溃与清理
# --------------------------------------------------------------------------- #


def _crash_evidence(safety_dir: Path) -> dict[str, object]:
    os.environ["DEEPQUANT_SAFETY_DIR"] = str(safety_dir)
    out: dict[str, object] = {}

    # 4a. 模拟崩溃：写一个**属于别的进程**的会话标记（崩溃的是上一个进程）。
    #     不能直接用 begin_session()——那会把本进程 pid 写进去，与"本进程正在运行"无法区分。
    safety_dir.mkdir(parents=True, exist_ok=True)
    (safety_dir / "session.json").write_text(
        json.dumps({"pid": 999999, "started_at": "2026-10-27T00:00:00", "host": "crashed-host"}),
        encoding="utf-8",
        newline="\n",
    )
    out["stale_session_detected"] = stale_session() is not None
    try:
        assert_clean_start()
        out["dirty_restart"] = "unexpected_pass"
    except DirtyShutdown as exc:
        out["dirty_restart"] = {"blocked": True, "message": str(exc)[:200]}

    # 4b. 超时窗内排空：正常路径（计数归零）
    report_ok = run_cleanup("drill:clean")
    out["cleanup_ok"] = report_ok.ok
    out["marker_cleared"] = report_ok.marker_cleared
    out["session_removed"] = stale_session() is None
    out["snapshot_before_after"] = {
        "before_in_flight": report_ok.before["in_flight"],
        "after_in_flight": report_ok.after["in_flight"],
        "not_applicable": report_ok.before["not_applicable"],
    }
    out["steps"] = [s.to_dict() for s in report_ok.steps]

    # 4c. 超时窗内没排空 → 清理失败 → 保留标记（这是"未确认清空"的来源）
    holder = threading.Event()
    started = threading.Event()

    def hold() -> None:
        with INFLIGHT.track():
            started.set()
            holder.wait(3.0)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    started.wait(2.0)
    os.environ["DEEPQUANT_DRAIN_TIMEOUT_S"] = "0.05"
    failed = run_cleanup("drill:timeout")
    holder.set()
    thread.join(timeout=3.0)
    INFLIGHT.reset()
    os.environ.pop("DEEPQUANT_DRAIN_TIMEOUT_S", None)

    out["drain_timeout"] = {
        "ok": failed.ok,
        "marker_kept": pending_cleanup() is not None,
        "drain_detail": next((s.detail for s in failed.steps if s.name == "drain_in_flight"), ""),
    }
    try:
        assert_clean_start()
        out["restart_after_failed_cleanup"] = "unexpected_pass"
    except DirtyShutdown:
        out["restart_after_failed_cleanup"] = "blocked"

    # 4d. 显式知情后恢复：强制清理 → 启动恢复
    recovered = run_cleanup("drill:recover")
    out["recovered"] = recovered.ok and assert_clean_start()["clean"] is True
    end_session()

    # 4e. 证据完整性
    chain = verify_log_chain()
    entries = cleanup_log_entries(limit=10)
    out["log_chain"] = chain
    out["log_events"] = [e.get("event") for e in entries]
    out["not_applicable_register"] = NOT_APPLICABLE_STEPS
    return out


def build_evidence() -> dict[str, object]:
    from deepquant.safety.codes import assert_codes_complete, category_coverage

    assert_codes_complete()
    INFLIGHT.reset()

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        evidence: dict[str, object] = {
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "category_coverage": category_coverage(),
        }
        evidence["criteria"] = {}
        evidence["criteria"]["criterion_1_outbound_denial"] = _outbound_evidence()
        evidence["criteria"]["criterion_2_least_privilege"] = _privilege_evidence()
        evidence["criteria"]["criterion_3_breaker_drill"] = _breaker_drill(base / "breaker")
        evidence["criteria"]["criterion_4_crash_and_cleanup"] = _crash_evidence(base / "cleanup")
        evidence["snapshot_after"] = snapshot()
    return evidence


def main() -> int:
    evidence = build_evidence()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"已写入 {REPORT}")
    print(json.dumps(evidence["criteria"], ensure_ascii=False, indent=2)[:3200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
