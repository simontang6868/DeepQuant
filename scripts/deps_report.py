"""T3 运维入口：探活、切换事件、延迟分位数导出、验收取证。

子命令：

    health     主动探测各通道（会真的发请求；本机被出口代理拦截时东财会失败）
    events     查看切换事件（内存 + 落盘）
    latency    查看分通道延迟分位数（默认全部日期）
    export     导出「按日 × 按通道」P50/P90/P99（给执行成本模型用）
    evidence   跑一遍验收场景并把结果写成 JSON（不依赖网络）

用法::

    PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"
    PYTHONPATH=backend/src $PY scripts/deps_report.py health
    PYTHONPATH=backend/src $PY scripts/deps_report.py export --out reports/DEPS_LATENCY.json
    PYTHONPATH=backend/src $PY scripts/deps_report.py evidence

``evidence`` 用**假调用**演示故障切换与恢复，不触碰网络：
取证要证明的是"机制在故障时按预期动作"，而不是"今天某个第三方站点活着"。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from deepquant.deps.chain import (  # noqa: E402
    ChainState,
    DependencyUnavailableError,
    execute_chain,
)
from deepquant.deps.events import EventLog  # noqa: E402
from deepquant.deps.health import probe_all  # noqa: E402
from deepquant.deps.latency import LatencyArchive, percentile  # noqa: E402
from deepquant.deps.market_data import DEFAULT_DEPS_DIR, build_runtime, channel_probes  # noqa: E402
from deepquant.deps.registry import build_registry, coverage, deps_dir  # noqa: E402

EVIDENCE_PATH = REPO_ROOT / "reports" / "DEPS_T3_EVIDENCE.json"


def _deeps_root() -> Path:
    raw = deps_dir()
    return Path(raw) if raw else DEFAULT_DEPS_DIR


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def cmd_health(args: argparse.Namespace) -> int:
    registry = build_registry()
    report = probe_all(registry, channel_probes(), timeout_s=args.timeout)
    print(f"探测时间 {report.to_dict()['checked_at']}")
    for result in report.results:
        flag = "OK  " if result.ok else "FAIL"
        print(f"  [{flag}] {result.kind}.{result.channel:10s} {result.latency_ms:8.1f}ms  {result.detail[:60]}")
    if not report.results:
        print("  （没有可探测的通道）")
        return 2
    print(f"健康 {report.n_ok}/{len(report.results)}")
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- #
# events / latency / export
# --------------------------------------------------------------------------- #
def cmd_events(args: argparse.Namespace) -> int:
    root = _deeps_root()
    log = EventLog(root)
    items = log.read_file(kind=args.kind, limit=args.limit)
    if not items:
        print(f"没有事件记录（{root / 'events.jsonl'}）")
        return 0
    for item in items:
        print(
            f"{item.get('occurred_at')}  {item.get('kind')}.{item.get('src')} → "
            f"{item.get('kind')}.{item.get('dst')}  [{item.get('direction')}]  "
            f"原因={item.get('reason')}  尝试={len(item.get('attempts') or [])}"
        )
    return 0


def cmd_latency(args: argparse.Namespace) -> int:
    archive = LatencyArchive(_deeps_root(), min_interval_s=0)
    if not archive.days():
        print(f"没有延迟档案（{_deeps_root() / 'latency'}）")
        return 0
    for day in archive.days() if args.day is None else [args.day]:
        items = archive.samples(day=day)
        channels = sorted({(i["kind"], i["channel"]) for i in items})
        print(f"== {day} ==")
        for kind, channel in channels:
            stats = archive.percentiles(day=day, kind=kind, channel=channel)
            if stats["n"] == 0:
                continue
            print(
                f"  {kind}.{channel:10s} n={stats['n']:4d} "
                f"p50={stats['p50']:8.1f} p90={stats['p90']:8.1f} p99={stats['p99']:8.1f}"
            )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    archive = LatencyArchive(_deeps_root(), min_interval_s=0)
    payload = archive.export()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"已导出 {len(payload['days'])} 天到 {out}")
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #
def cmd_evidence(args: argparse.Namespace) -> int:
    registry = build_registry()
    report = coverage(registry)
    with tempfile.TemporaryDirectory(prefix="deps_t3_") as tmp:
        root = Path(tmp)
        archive = LatencyArchive(root, min_interval_s=0)
        events = EventLog(root)
        state = ChainState()

        def _fail_primary(dependency):
            if dependency.name == "eastmoney":
                raise ConnectionError("Unable to connect to proxy")
            return {"channel": dependency.name, "rows": 2}

        # 1) 主源故障 → 自动切换
        failover = execute_chain(
            kind="market_data", call=_fail_primary, registry=registry,
            archive=archive, events=events, state=state,
        )
        # 2) 主源恢复 → 自动回切
        recovered = execute_chain(
            kind="market_data", call=lambda d: {"channel": d.name}, registry=registry,
            archive=archive, events=events, state=state,
        )
        # 3) 全部失败 → 抛异常（不返回空数据）
        all_fail_error = None
        try:
            execute_chain(
                kind="market_data",
                call=lambda d: (_ for _ in ()).throw(TimeoutError("timeout")),
                registry=build_registry(lock={}), archive=LatencyArchive(None),
                events=EventLog(None), state=ChainState(),
            )
        except DependencyUnavailableError as exc:
            all_fail_error = {"n_attempts": len(exc.attempts), "message": str(exc)[:200]}

        # 4) 人工锁定
        locked = execute_chain(
            kind="market_data", call=lambda d: d.name, registry=build_registry(lock={}),
            pinned="tencent", archive=LatencyArchive(None), events=EventLog(None),
            state=ChainState(),
        )

        # 5) 造两天样本，验证「按日回溯 + 导出分位数」
        base = datetime.now(UTC).replace(microsecond=0)
        for offset, values in ((0, (10.0, 12.0, 15.0, 99.0)), (1, (20.0, 25.0))):
            day = (base - timedelta(days=offset)).isoformat()
            for value in values:
                archive.record(
                    kind="market_data", channel="eastmoney",
                    latency_ms=value, ok=True, occurred_at=day, force=True,
                )
        exported = archive.export()
        percentiles_ok = all(
            stats["p50"] is not None and stats["p99"] is not None
            for day_table in exported["table"].values()
            for stats in day_table.values()
        )

        persisted_events = events.read_file()
        event_dirs = [e["direction"] for e in persisted_events]

        criteria = {
            "criterion_1_registry_coverage": {
                "required_kinds": report["required_kinds"],
                "by_kind": report["by_kind"],
                "market_data_has_fallback": report["market_data_has_fallback"],
                "empty_kinds_reported_honestly": report["empty_kinds"],
                "pass": report["market_data_channels"] >= 2,
            },
            "criterion_2_failover_transparent": {
                "failover_channel": failover.channel,
                "failover_attempts": [a.to_dict() for a in failover.attempts],
                "degraded": failover.degraded,
                "value_returned": failover.value is not None,
                "pass": failover.channel == "tencent" and failover.value is not None,
            },
            "criterion_3_switch_events": {
                "directions": event_dirs,
                "n_events": len(persisted_events),
                "events_file": str(root / "events.jsonl"),
                "first_event_attempts": len(persisted_events[0]["attempts"]) if persisted_events else 0,
                "pass": event_dirs == ["failover", "recover"] and len(persisted_events) == 2,
            },
            "criterion_4_manual_lock": {
                "channel": locked.channel,
                "pinned": locked.pinned,
                "pass": locked.channel == "tencent" and locked.pinned is True,
            },
            "criterion_5_latency_archive": {
                "days": exported["days"],
                "method": exported["method"],
                "sample_table": exported["table"],
                "percentiles_present": percentiles_ok,
                "p50_of_ten": percentile([10.0, 12.0, 15.0, 99.0], 50),
                "pass": len(exported["days"]) >= 2 and percentiles_ok,
            },
            "all_fail_semantics": {
                "raises": all_fail_error is not None,
                "detail": all_fail_error,
                "pass": all_fail_error is not None and all_fail_error["n_attempts"] == 2,
            },
        }
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "criteria": criteria,
        "overall_pass": all(v["pass"] for v in criteria.values()),
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    print("=" * 72)
    for name, item in criteria.items():
        print(f"[{'PASS' if item['pass'] else 'FAIL'}] {name}")
    print("-" * 72)
    print(f"切换事件方向 : {event_dirs}")
    print(f"延迟档案天数 : {exported['days']}（{exported['method']}）")
    print(f"总体         : {'PASS' if evidence['overall_pass'] else 'FAIL'}")
    print(f"证据文件     : {EVIDENCE_PATH.relative_to(REPO_ROOT)}")
    return 0 if evidence["overall_pass"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="依赖降级链运维入口")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="主动探测各通道")
    p_health.add_argument("--timeout", type=float, default=5.0)
    p_health.set_defaults(func=cmd_health)

    p_events = sub.add_parser("events", help="查看切换事件")
    p_events.add_argument("--limit", type=int, default=50)
    p_events.add_argument("--kind", default=None)
    p_events.set_defaults(func=cmd_events)

    p_latency = sub.add_parser("latency", help="查看延迟分位数")
    p_latency.add_argument("--day", default=None, help="YYYYMMDD；缺省查看全部日期")
    p_latency.set_defaults(func=cmd_latency)

    p_export = sub.add_parser("export", help="导出按日分位数")
    p_export.add_argument("--out", default=None)
    p_export.set_defaults(func=cmd_export)

    p_evidence = sub.add_parser("evidence", help="跑验收场景并输出 JSON")
    p_evidence.set_defaults(func=cmd_evidence)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
