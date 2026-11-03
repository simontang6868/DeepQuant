"""安全运维 CLI：出站体检、熔断操作、退出清理、权限矩阵。

用法
----
::

    PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"
    PYTHONPATH=backend/src $PY scripts/safety.py status
    PYTHONPATH=backend/src $PY scripts/safety.py check-url https://example.com/cb
    PYTHONPATH=backend/src $PY scripts/safety.py scan                # 源码 URL 绊线
    PYTHONPATH=backend/src $PY scripts/safety.py breaker             # 熔断快照
    PYTHONPATH=backend/src $PY scripts/safety.py trip backtest.run --reason "演练"
    PYTHONPATH=backend/src $PY scripts/safety.py reset backtest.run
    PYTHONPATH=backend/src $PY scripts/safety.py cleanup            # 执行清理并留证
    PYTHONPATH=backend/src $PY scripts/safety.py cleanup --verify    # 只校验证据链
    PYTHONPATH=backend/src $PY scripts/safety.py permissions        # 最小权限矩阵

``cleanup`` 是退出清理失败的**恢复入口**：它在同一目录下清掉 ``cleanup_required.json``
与会话标记，并留下一条含理由的取证记录。没跑过它之前，服务启动会被拒绝——
这是设计意图，不是故障。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from deepquant.safety import least_privilege  # noqa: E402
from deepquant.safety.breaker import (  # noqa: E402
    allow,
    config as breaker_config,
    reset as breaker_reset,
    safety_root,
    snapshot as breaker_snapshot,
    trip as breaker_trip,
)
from deepquant.safety.lifecycle import (  # noqa: E402
    cleanup_log_entries,
    pending_cleanup,
    run_cleanup,
    stale_session,
    verify_log_chain,
)
from deepquant.safety.outbound import (  # noqa: E402
    check as outbound_check,
    default_policy,
    scan_url_literals,
)

REPO = Path(__file__).resolve().parents[1]


def cmd_status(_args: argparse.Namespace) -> int:
    payload = {
        "safety_dir": str(safety_root()),
        "pending_cleanup": pending_cleanup(),
        "stale_session": stale_session(),
        "breaker": breaker_snapshot(),
        "permissions": least_privilege.permission_summary(),
        "outbound_whitelist": sorted(default_policy().allowed_hosts),
        "cleanup_log_chain": verify_log_chain(),
        "recent_cleanup": cleanup_log_entries(limit=3),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_check_url(args: argparse.Namespace) -> int:
    decision = outbound_check(args.url)
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    if not decision.allowed:
        print(f"\n拒绝理由：{decision.code}")
    return 0 if decision.allowed else 1


def cmd_scan(args: argparse.Namespace) -> int:
    report = scan_url_literals(REPO / "backend" / "src")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def cmd_breaker(args: argparse.Namespace) -> int:
    snap = breaker_snapshot()
    if args.scope:
        snap["decision"] = allow(args.scope).to_dict()
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 1 if snap.get("error") else 0


def cmd_trip(args: argparse.Namespace) -> int:
    state = breaker_trip(args.scope, args.reason)
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n已熔断 {args.scope}；冷却 {breaker_config()['cooldown_s']}s 后进入半开探测")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    state = breaker_reset(args.scope)
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    if args.verify:
        print(json.dumps(verify_log_chain(), ensure_ascii=False, indent=2))
        return 0
    # 运维手动执行 = 已由人确认"上一个进程是死的"，因此允许清掉会话标记
    report = run_cleanup(args.reason, clear_session=True)
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not report.ok:
        print("\n清理未通过：标记已保留，下次启动会被拒绝。请按上面的 detail 处理。")
        return 1
    print(f"\n清理通过，证据：{report.log_path}")
    return 0


def cmd_permissions(_args: argparse.Namespace) -> int:
    print(json.dumps(least_privilege.matrix_from_registry(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepQuant 安全运维")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="安全层总览").set_defaults(func=cmd_status)

    check = sub.add_parser("check-url", help="判定一个出站地址")
    check.add_argument("url")
    check.set_defaults(func=cmd_check_url)

    sub.add_parser("scan", help="扫描源码 URL 绊线").set_defaults(func=cmd_scan)

    breaker = sub.add_parser("breaker", help="熔断快照")
    breaker.add_argument("scope", nargs="?", default=None)
    breaker.set_defaults(func=cmd_breaker)

    trip = sub.add_parser("trip", help="强制熔断（需理由）")
    trip.add_argument("scope")
    trip.add_argument("--reason", required=True)
    trip.set_defaults(func=cmd_trip)

    reset = sub.add_parser("reset", help="人工解除熔断")
    reset.add_argument("scope")
    reset.set_defaults(func=cmd_reset)

    cleanup = sub.add_parser("cleanup", help="执行退出清理并留证")
    cleanup.add_argument("--reason", default="manual")
    cleanup.add_argument("--verify", action="store_true", help="只校验清理日志链")
    cleanup.set_defaults(func=cmd_cleanup)

    sub.add_parser("permissions", help="最小权限矩阵").set_defaults(func=cmd_permissions)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
