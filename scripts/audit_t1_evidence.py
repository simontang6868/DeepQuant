"""T1 验收取证脚本：把四项硬性标准的实测数字导出为可复核的 JSON。

对应《推进计划》5.2 T1 的验收标准：

1. 核心操作工件覆盖率 100%；
2. 五元组字段完整率 100%；
3. 任一历史工件的回放时间 < 5 秒；
4. 工件不可篡改（追加式存储 + 哈希校验）。

用法（必须使用项目约定的解释器）::

    PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"
    PYTHONPATH=backend/src $PY scripts/audit_t1_evidence.py

脚本只写 ``reports/AUDIT_T1_EVIDENCE.json`` 与临时目录，不改动任何既有产物。
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from deepquant.audit.operations import CORE_OPERATIONS, coverage  # noqa: E402
from deepquant.audit.run_card import REQUIRED_FIELDS, completeness  # noqa: E402
from deepquant.audit.store import AuditStore, chain_fingerprint  # noqa: E402

#: 取证规模。5000 张足以暴露索引扫描的开销，又不至于让脚本跑上几分钟。
N_CARDS = 5000

#: 回放次数（取分位数，避免只测一次碰到缓存恰好命中）。
N_REPLAY = 200

OUT_PATH = REPO_ROOT / "reports" / "AUDIT_T1_EVIDENCE.json"


def build_store(root: Path) -> AuditStore:
    store = AuditStore(root)
    base_day = 1
    for i in range(N_CARDS):
        # 分散到 50 个"日"、5 个操作人、3 类操作，便于检验索引过滤的选择性
        day = base_day + (i // 100)
        occurred_at = f"2026-{(day // 28) + 1:02d}-{(day % 28) + 1:02d}T{i % 24:02d}:00:00+00:00"
        store.append(
            action=("backtest.run", "dataset.list", "strategy.list")[i % 3],
            operator=f"ops{i % 5}",
            basis={"rule": "冻结配置", "seq_hint": i},
            outcome={"status": "ok", "status_code": 200},
            caveats=["取证数据：非真实业务操作"],
            subject={"symbol": f"T{i % 40:02d}"},
            session=f"sess-{i % 7}",
            occurred_at=occurred_at,
        )
    return store


def measure_replay(store: AuditStore) -> dict:
    """逐张回放的耗时分布。每次取不同卡片，避免重复命中同一缓存。"""
    cards = store.query(action="backtest.run", limit=N_REPLAY)
    durations: list[float] = []
    for card in cards:
        started = time.perf_counter()
        got = store.get(card["card_id"])
        durations.append(time.perf_counter() - started)
        if got is None:
            raise RuntimeError(f"回放失败：找不到卡片 {card['card_id']}")
    durations.sort()
    return {
        "n": len(durations),
        "p50_ms": round(statistics.median(durations) * 1000, 3),
        "p95_ms": round(durations[int(len(durations) * 0.95) - 1] * 1000, 3),
        "max_ms": round(max(durations) * 1000, 3),
        "under_5s": max(durations) < 5.0,
    }


def measure_query(store: AuditStore) -> dict:
    """整体查询（索引过滤 + 分片加载）耗时。"""
    started = time.perf_counter()
    cards = store.query(action="backtest.run", since="2026-01-01T00:00:00+00:00", limit=1000)
    elapsed = time.perf_counter() - started
    return {"n_returned": len(cards), "elapsed_ms": round(elapsed * 1000, 3), "under_5s": elapsed < 5.0}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="audit_t1_") as tmp:
        root = Path(tmp) / "audit"
        started = time.perf_counter()
        store = build_store(root)
        build_seconds = round(time.perf_counter() - started, 2)

        verify_started = time.perf_counter()
        report = store.verify()
        verify_seconds = round(time.perf_counter() - verify_started, 2)

        all_cards = list(store.iter_all())
        comp = completeness(all_cards)
        replay = measure_replay(store)
        query = measure_query(store)
        fingerprint = chain_fingerprint(store)

    cov = coverage()
    evidence = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").UTC
        ).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "scale": {
            "n_cards": N_CARDS,
            "build_seconds": build_seconds,
            "verify_seconds": verify_seconds,
            "n_shards": report.n_shards,
        },
        "criterion_1_coverage": {
            "ratio": cov["ratio"],
            "in_scope_total": cov["in_scope_total"],
            "instrumented_total": cov["instrumented_total"],
            "missing": cov["missing"],
            "not_applicable": cov["not_applicable"],
            "pass": cov["ratio"] == 1.0 and not cov["missing"],
        },
        "criterion_2_field_completeness": {
            "required_fields": list(REQUIRED_FIELDS),
            "n_cards": comp.n_cards,
            "n_complete": comp.n_complete,
            "ratio": comp.ratio,
            "pass": comp.ratio == 1.0,
        },
        "criterion_3_replay_latency": {**replay, "bulk_query": query, "pass": replay["under_5s"] and query["under_5s"]},
        "criterion_4_tamper_evidence": {
            "chain_ok": report.ok,
            "chain_failures": [f.to_dict() for f in report.failures],
            "last_sha256": fingerprint["last_sha256"],
            "n_shards_in_fingerprint": len(fingerprint["shards"]),
            "pass": report.ok and not report.failures,
        },
        "operations": cov["operations"],
    }
    evidence["overall_pass"] = all(
        evidence[k]["pass"]
        for k in (
            "criterion_1_coverage",
            "criterion_2_field_completeness",
            "criterion_3_replay_latency",
            "criterion_4_tamper_evidence",
        )
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    print("=" * 72)
    print(f"规模：{N_CARDS} 张卡 / {report.n_shards} 个分片，写入 {build_seconds}s，全链校验 {verify_seconds}s")
    print(f"[1] 覆盖率            : {cov['instrumented_total']}/{cov['in_scope_total']} = {cov['ratio']:.1%}  {'PASS' if evidence['criterion_1_coverage']['pass'] else 'FAIL'}")
    print(f"[2] 五元组完整率      : {comp.n_complete}/{comp.n_cards} = {comp.ratio:.1%}  {'PASS' if evidence['criterion_2_field_completeness']['pass'] else 'FAIL'}")
    print(f"[3] 回放耗时          : p50 {replay['p50_ms']}ms / p95 {replay['p95_ms']}ms / max {replay['max_ms']}ms  {'PASS' if evidence['criterion_3_replay_latency']['pass'] else 'FAIL'}")
    print(f"    批量查询          : {query['n_returned']} 张 {query['elapsed_ms']}ms")
    print(f"[4] 链校验            : ok={report.ok} 失败项 {len(report.failures)}  {'PASS' if evidence['criterion_4_tamper_evidence']['pass'] else 'FAIL'}")
    print(f"链尾摘要              : {fingerprint['last_sha256'][:16]}…")
    print(f"总体                  : {'PASS' if evidence['overall_pass'] else 'FAIL'}")
    print(f"证据文件              : {OUT_PATH.relative_to(REPO_ROOT)}")
    print("=" * 72)
    return 0 if evidence["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
