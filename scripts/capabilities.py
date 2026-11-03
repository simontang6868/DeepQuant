"""能力契约运维 CLI：枚举、对账、锁版本、看策略。

用法
----
::

    PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"
    PYTHONPATH=backend/src $PY scripts/capabilities.py list
    PYTHONPATH=backend/src $PY scripts/capabilities.py show backtest.run
    PYTHONPATH=backend/src $PY scripts/capabilities.py verify      # 对账，失败退出码 1
    PYTHONPATH=backend/src $PY scripts/capabilities.py lock        # 升版本后落盘
    PYTHONPATH=backend/src $PY scripts/capabilities.py policy      # 当前模式/禁用/授权
    PYTHONPATH=backend/src $PY scripts/capabilities.py evidence    # 生成验收取证 JSON

``lock`` 的语义必须说清：它**不会**帮你升版本。若契约摘要变了而版本没变，
``lock`` 会拒绝写入并指出该升哪个号——这道门刻意设成"必须有人做决定"，
而不是一条命令自动把不一致抹平。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from deepquant.capabilities import (  # noqa: E402
    all_capabilities,
    assert_codes_complete,
    assert_registry_valid,
    capability_reason,
    default_lock_path,
    describe,
    format_report,
    get,
    load_lock,
    policy_snapshot,
    verify_all,
    write_lock,
)
from deepquant.capabilities.spec import CapabilityContractError  # noqa: E402


def _build_app():
    from deepquant.api.app import create_app

    return create_app()


def cmd_list(_args: argparse.Namespace) -> int:
    caps = all_capabilities()
    print(f"{'cap_id':22s} {'method':7s} {'path':26s} {'ver':7s} 标签")
    for cap in caps:
        print(
            f"{cap.cap_id:22s} {cap.method or '-':7s} {cap.path or '-':26s} "
            f"{cap.version:7s} {','.join(cap.risk_tags)}"
        )
    print(f"\n共 {len(caps)} 项（HTTP {sum(1 for c in caps if c.transport == 'http')}、"
          f"服务层 {sum(1 for c in caps if c.transport == 'service')}）")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    cap = get(args.cap_id)
    if cap is None:
        print(f"能力 {args.cap_id!r} 不存在。已登记：{[c.cap_id for c in all_capabilities()]}")
        return 2
    print(json.dumps(cap.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify_all(_build_app(), lock_path=args.lock)
    print(format_report(report))
    return 0 if report.ok else 1


def cmd_lock(args: argparse.Namespace) -> int:
    assert_registry_valid()
    assert_codes_complete()
    try:
        path = write_lock(args.lock, recorded_at=args.date)
    except CapabilityContractError as exc:
        print(f"拒绝落盘：{exc}")
        return 1
    payload = load_lock(path)
    print(f"已写入 {path}")
    for cap_id, entry in payload["capabilities"].items():
        current = entry["current"]
        digest = next(h["digest"] for h in entry["history"] if h["version"] == current)
        print(f"  {cap_id:22s} v{current:8s} {digest} (历史 {len(entry['history'])} 条)")
    if payload.get("removed_capabilities"):
        print(f"  已移除：{payload['removed_capabilities']}")
    return 0


def cmd_policy(_args: argparse.Namespace) -> int:
    snapshot = policy_snapshot()
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    if snapshot.get("error"):
        print("\n配置不合法：闸门会按 503 拒绝（不静默降级）")
        return 1
    return 0


def cmd_codes(_args: argparse.Namespace) -> int:
    for item in sorted(describe()["reason_codes"], key=lambda x: str(x["code"])):
        print(f"{item['code']:34s} {item['status_code']}  {item['summary']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepQuant 能力契约运维")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="枚举能力").set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="查看单个能力")
    show.add_argument("cap_id")
    show.set_defaults(func=cmd_show)

    verify = sub.add_parser("verify", help="契约对账（含 lock）")
    verify.add_argument("--lock", default=None, help="lock 文件路径（默认 backend/contracts）")
    verify.set_defaults(func=cmd_verify)

    lock = sub.add_parser("lock", help="落盘契约锁（升版本后执行）")
    lock.add_argument("--lock", default=None)
    lock.add_argument("--date", default=None, help="记录日期，默认今天")
    lock.set_defaults(func=cmd_lock)

    sub.add_parser("policy", help="当前模式/禁用/授权").set_defaults(func=cmd_policy)
    sub.add_parser("codes", help="枚举能力原因码").set_defaults(func=cmd_codes)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
