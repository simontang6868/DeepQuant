"""T6 影子复盘：语料生成 / 基线构建 / 异常审计 / 验收证据。

四个子命令
----------
- ``corpus``：跑真实 API 生成审计语料（会真实执行回测、真实落 T1 工件）；
- ``build``：用训练切分构建质检基线（``baseline.json``）；
- ``audit``：对留出会话出异常报告（Markdown + JSON）；
- ``evidence``：跑完四项验收标准并产出 ``reports/SHADOW_T6_EVIDENCE.json``。

语料为什么必须真跑
------------------
T6 要复盘的是「操作日志」。如果日志是脚本拼出来的假记录，基线学到的是脚本的
习惯，不是人的习惯，命中率也变成自证。因此 ``corpus`` 走真实 ``TestClient`` +
真实回测引擎 + 真实审计中间件：每个会话、每个操作都是系统实际执行过的，
只是执行时间是本次构建（属引导语料，不是历史积累——见报告口径说明）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pandas as pd  # noqa: E402

from deepquant.shadow import (  # noqa: E402
    RULE_CATALOG,
    audit_sequences,
    baseline_digest,
    build_baseline,
    load_baseline,
    load_sequences,
    render_markdown,
    save_baseline,
)
from deepquant.shadow.evaluate import (  # noqa: E402
    DEFAULT_PER_TYPE,
    DEFAULT_SEED,
    collect_injected,
    evaluate,
)
from deepquant.shadow.sequences import OperationSequence  # noqa: E402
from deepquant.shadow.split import HOLDOUT_FRACTION, split_sequences  # noqa: E402

DEFAULT_CORPUS = ROOT / "reports" / "t6_shadow" / "corpus"
DEFAULT_BASELINE = ROOT / "reports" / "t6_shadow" / "baseline.json"
DEFAULT_REPORT = ROOT / "reports" / "SHADOW_AUDIT_REPORT.md"
DEFAULT_REPORT_JSON = ROOT / "reports" / "t6_shadow" / "audit_report_eval.json"
DEFAULT_PRODUCTION_REPORT = ROOT / "reports" / "t6_shadow" / "audit_report_production.md"
DEFAULT_EVIDENCE = ROOT / "reports" / "SHADOW_T6_EVIDENCE.json"

#: 语料里的操作人池（真实业务里来自账号体系；此处为演练账号）。
OPERATORS = ("alice", "bob", "carol")

#: 数据集清单：价格形态不同，保证不同参数下回测有成交、有差异。
SYMBOLS = ("DQA01", "DQA02", "DQA03", "DQA04", "DQA05", "DQA06")


# --------------------------------------------------------------------------- #
# 一、语料生成
# --------------------------------------------------------------------------- #
def _daily_csv(path: Path, *, seed: int, rows: int = 260) -> Path:
    """生成确定性的日线 CSV（随机游走 + 趋势段，保证均线有交叉）。

    带 ``amount`` 列：组合回测的成交额检查（P2）要求它存在，缺列会直接报错。
    该列由 ``close × volume`` 推出，是**同一份数据的派生量**，不引入新信息。
    """
    rng = random.Random(seed)
    dates = pd.date_range("2021-01-04", periods=rows, freq="B")
    price = 10.0 + rng.random() * 20
    lines = ["date,open,high,low,close,volume,amount"]
    for index, day in enumerate(dates):
        drift = 0.0012 if (index // 40) % 2 == 0 else -0.0011
        price *= 1 + drift + rng.gauss(0, 0.012)
        price = max(price, 1.5)
        open_ = price * (1 + rng.gauss(0, 0.003))
        high = max(open_, price) * (1 + abs(rng.gauss(0, 0.004)))
        low = min(open_, price) * (1 - abs(rng.gauss(0, 0.004)))
        volume = rng.randint(20000, 90000)
        lines.append(
            f"{day:%Y-%m-%d},{open_:.4f},{high:.4f},{low:.4f},{price:.4f},{volume},{price * volume:.2f}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _prepare_data_root(base: Path) -> Path:
    """登记数据集（走既有 ingest 管线，元数据与数据文件都是真实产物）。"""
    from deepquant.data.pipeline import ingest_daily_csv

    data_root = (base / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    for offset, symbol in enumerate(SYMBOLS):
        source = _daily_csv(base / "src" / f"{symbol}.csv", seed=1000 + offset)
        ingest_daily_csv(source, symbol=symbol, data_root=data_root)
    return data_root


def _call(client: Any, method: str, path: str, *, headers: dict[str, str], **kwargs: Any) -> Any:
    response = client.request(method, path, headers=headers, **kwargs)
    return response


def _run_backtest(
    client: Any,
    headers: dict[str, str],
    *,
    symbol: str,
    fast: int,
    slow: int,
    cash: float,
    save: bool = True,
) -> Any:
    return _call(
        client,
        "POST",
        "/backtests",
        headers=headers,
        json={
            "dataset_symbol": symbol,
            "strategy_id": "ma_cross",
            "strategy_params": {"fast_window": fast, "slow_window": slow},
            "initial_cash": cash,
            "save": save,
        },
    )


def _plan(rng: random.Random) -> str:
    """会话类型的真实分布（研究平台的实际用法，不是均匀分布）。"""
    return rng.choices(
        [
            "browse",
            "inspect",
            "single",
            "explore_run",
            "compare",
            "quick_compare",
            "compare_three",
            "retry",
            "missing",
            "portfolio",
            "portfolio_after_run",
        ],
        weights=[15, 8, 16, 8, 16, 6, 5, 9, 7, 4, 6],
    )[0]


def _hit(status_counter: dict[str, int], response: Any) -> Any:
    status_counter[str(response.status_code)] = status_counter.get(str(response.status_code), 0) + 1
    return response


def generate_corpus(
    out_dir: Path,
    *,
    sessions: int = 120,
    seed: int = 20261027,
    verbose: bool = True,
) -> dict[str, Any]:
    """真实执行 API 生成审计语料。返回生成摘要（不伪造任何数字）。"""
    from fastapi.testclient import TestClient

    from deepquant.api.app import app
    from deepquant.api.config import get_data_root

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="t6_corpus_"))
    data_root = _prepare_data_root(workdir)

    os.environ["DEEPQUANT_AUDIT_DIR"] = str(out_dir.resolve())
    os.environ["DEEPQUANT_DB_PATH"] = str((workdir / "backtests.db").resolve())
    os.environ.pop("DEEPQUANT_AUDIT_STRICT", None)

    rng = random.Random(seed)
    app.dependency_overrides[get_data_root] = lambda: data_root
    client = TestClient(app)
    started = time.perf_counter()
    status_counter: dict[str, int] = {}
    plan_counter: dict[str, int] = {}
    try:
        for index in range(sessions):
            operator = OPERATORS[index % len(OPERATORS)]
            session_id = f"s{index:04d}"
            headers = {"X-Operator": operator, "X-Session-Id": session_id}
            kind = _plan(rng)
            plan_counter[kind] = plan_counter.get(kind, 0) + 1
            symbol = rng.choice(SYMBOLS)
            fast, slow = rng.choice([(5, 20), (10, 30), (3, 15), (20, 60)])
            cash = rng.choice([100000.0, 200000.0, 300000.0, 500000.0])

            def run(fast_=fast, slow_=slow, symbol_=symbol, cash_=cash):
                return _hit(
                    status_counter,
                    _run_backtest(
                        client, headers, symbol=symbol_, fast=fast_, slow=slow_, cash=cash_
                    ),
                )

            if kind == "inspect":
                # 先看策略表再看数据集：同一批人常以另一种顺序浏览
                _hit(status_counter, _call(client, "GET", "/strategies", headers=headers))
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
            elif kind == "browse":
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                _hit(status_counter, _call(client, "GET", "/strategies", headers=headers))
            elif kind == "single":
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                run()
            elif kind == "explore_run":
                # 先看有什么策略、再看数据集、最后跑一个：带探索的作业
                _hit(status_counter, _call(client, "GET", "/strategies", headers=headers))
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                run()
            elif kind == "compare_three":
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                runs = [
                    run(),
                    run(fast_=fast + 1, slow_=slow + 5),
                    run(fast_=fast + 3, slow_=slow + 15),
                ]
                run_ids = [item.json().get("run_id") for item in runs if item.status_code == 200]
                if len(run_ids) == 3 and all(run_ids):
                    _hit(
                        status_counter,
                        _call(
                            client,
                            "POST",
                            "/backtests/compare",
                            headers=headers,
                            json={"run_ids": run_ids},
                        ),
                    )
            elif kind == "compare":
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                first = run()
                second = run(fast_=fast + 2, slow_=slow + 10)
                run_ids = [
                    item.json().get("run_id")
                    for item in (first, second)
                    if item.status_code == 200
                ]
                if len(run_ids) == 2 and all(run_ids):
                    _hit(
                        status_counter,
                        _call(
                            client,
                            "POST",
                            "/backtests/compare",
                            headers=headers,
                            json={"run_ids": run_ids},
                        ),
                    )
            elif kind == "quick_compare":
                # 熟手：跳过清单，直接跑两次再对比（前置条件仍满足）
                first = run()
                second = run(fast_=fast + 3, slow_=slow + 6)
                run_ids = [
                    item.json().get("run_id")
                    for item in (first, second)
                    if item.status_code == 200
                ]
                if len(run_ids) == 2 and all(run_ids):
                    _hit(
                        status_counter,
                        _call(
                            client,
                            "POST",
                            "/backtests/compare",
                            headers=headers,
                            json={"run_ids": run_ids},
                        ),
                    )
            elif kind == "retry":
                # 先提交一个非法参数（fast >= slow），再改对重试：真实试错流程。
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                run(fast_=60, slow_=20)
                run()
            elif kind == "missing":
                # 先打一个不存在的数据集，再看清单、再改对：真实排查流程。
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                run(symbol_="NOPE99")
                run()
            elif kind == "portfolio":
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                _hit(
                    status_counter,
                    _call(
                        client,
                        "POST",
                        "/portfolio-backtests",
                        headers=headers,
                        json={
                            "dataset_symbols": list(SYMBOLS[:4]),
                            "initial_cash": 1_000_000.0,
                            "selection": "equal_weight_all",
                            "min_assets": 1,
                        },
                    ),
                )
            elif kind == "portfolio_after_run":
                # 先单标的跑通，再上组合：常见的研究推进路径
                _hit(status_counter, _call(client, "GET", "/datasets", headers=headers))
                run()
                _hit(
                    status_counter,
                    _call(
                        client,
                        "POST",
                        "/portfolio-backtests",
                        headers=headers,
                        json={
                            "dataset_symbols": list(SYMBOLS[:4]),
                            "initial_cash": 1_000_000.0,
                            "selection": "equal_weight_all",
                            "min_assets": 1,
                        },
                    ),
                )
            if verbose and (index + 1) % 20 == 0:
                print(f"  已生成 {index + 1}/{sessions} 个会话", flush=True)
    finally:
        client.close()
        app.dependency_overrides.clear()
        shutil.rmtree(workdir, ignore_errors=True)

    elapsed = time.perf_counter() - started
    cards = sum(
        1
        for line in (out_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    summary = {
        "corpus_dir": str(out_dir),
        "sessions_requested": sessions,
        "cards_written": cards,
        "plan_distribution": plan_counter,
        "status_codes": status_counter,
        "elapsed_s": round(elapsed, 2),
        "seed": seed,
    }
    (out_dir / "corpus_meta.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


# --------------------------------------------------------------------------- #
# 二、切分（与时间无关，避免用未来训练）
# --------------------------------------------------------------------------- #
def _load_split(
    corpus: Path, seed: int
) -> tuple[list[OperationSequence], list[OperationSequence], list[OperationSequence]]:
    sequences = load_sequences(corpus)
    train, holdout = split_sequences(sequences, seed=seed)
    return sequences, train, holdout


# --------------------------------------------------------------------------- #
# 三、子命令
# --------------------------------------------------------------------------- #
def cmd_corpus(args: argparse.Namespace) -> int:
    summary = generate_corpus(Path(args.out), sessions=args.sessions, seed=args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["cards_written"] == 0:
        print("✗ 语料为空：审计中间件没有落任何工件，检查 DEEPQUANT_AUDIT_DIR 与路由映射")
        return 1
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    _, train, holdout = _load_split(Path(args.corpus), args.seed)
    baseline = build_baseline(train)
    save_baseline(Path(args.out), baseline, note=f"由 {Path(args.corpus).name} 训练切分构建")
    print(
        json.dumps(
            {
                "baseline": str(args.out),
                "digest": baseline_digest(baseline),
                "train_sequences": len(train),
                "holdout_sequences": len(holdout),
                "top_sequences": [entry["pattern"] for entry in baseline["top_sequences"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    _, _, holdout = _load_split(Path(args.corpus), args.seed)
    baseline = load_baseline(Path(args.baseline))
    report = audit_sequences(holdout, baseline, baseline_digest=baseline_digest(baseline))
    markdown = render_markdown(report)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(markdown + "\n", encoding="utf-8", newline="\n")
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "scanned": report["scanned_sequences"],
                "flagged": report["flagged_sequences"],
                "by_code": report["summary"]["by_code"],
                "skipped_rules": [item["code"] for item in report["summary"]["skipped_rules"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


# --------------------------------------------------------------------------- #
# 四、验收证据
# --------------------------------------------------------------------------- #
def _independent_top_patterns(corpus: Path, seed: int, *, top_n: int = 10) -> list[list[str]]:
    """**独立实现**重算高频序列：只读 ``index.jsonl``，不 import shadow 包的统计逻辑。

    为什么要有第二份实现：同一份代码跑两次结果一样，只能证明它没有随机性，
    不能证明它算对了。这里用最小路径（索引 → 按 session 分组 → Counter）复算，
    与基线模块的结论比对。
    """
    from collections import Counter

    by_session: dict[str, list[tuple[int, str]]] = {}
    for line in (corpus / "index.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        session = entry.get("session")
        if not session:
            continue  # 与主实现口径一致：无会话头的卡片不参与顺序统计
        digest = hashlib.sha256(f"{seed}:session:{session}".encode("utf-8")).hexdigest()
        if int(digest[:8], 16) / 0xFFFFFFFF < HOLDOUT_FRACTION:
            continue  # 留出集，不参与训练统计
        by_session.setdefault(f"session:{session}", []).append(
            (int(entry["seq"]), str(entry.get("action") or ""))
        )
    counter: Counter[tuple[str, ...]] = Counter()
    for steps in by_session.values():
        ordered = [action for _, action in sorted(steps)]
        counter[tuple(ordered)] += 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [list(pattern) for pattern, _ in ranked]


def _evidence_card_ids(corpus: Path) -> set[str]:
    return {
        json.loads(line)["card_id"]
        for line in (corpus / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def cmd_evidence(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    sequences, train, holdout = _load_split(corpus, args.seed)
    baseline = load_baseline(Path(args.baseline))

    # --- 标准一：Top 10 可复现 ---
    rebuilt = build_baseline(train)
    digest_once = baseline_digest(baseline)
    digest_twice = baseline_digest(rebuilt)
    top_from_baseline = [entry["pattern"] for entry in baseline["top_sequences"]]
    top_independent = _independent_top_patterns(corpus, args.seed)
    top10_reproducible = {
        "same_corpus_same_digest": digest_once == digest_twice,
        "digest": digest_once,
        "top10_from_baseline": top_from_baseline,
        "top10_independent_recompute": top_independent,
        "top10_match": top_from_baseline == top_independent,
        "distinct_patterns_in_corpus": baseline["corpus"]["distinct_patterns"],
        "note": "«同一语料两次构建摘要一致» 证明无隐藏状态；«独立实现 Top10 一致» 证明统计口径正确。",
    }

    # --- 标准二：异常命中率 ---
    evaluation = evaluate(baseline, holdout, per_type=args.per_type, seed=args.seed)

    # --- 标准三：报告可用（字段完整 + 证据可回溯到真实工件）---
    # 生产形态报告：跑全库（质检人员今天就会看到的样子）
    production = audit_sequences(sequences, baseline, baseline_digest=digest_once)
    # 评估形态报告：留出集 + 注入样本（用来展示"命中时长什么样"，样本带注入标记）
    injected, _ = collect_injected(baseline, holdout, per_type=args.per_type, seed=args.seed)
    eval_sequences = list(holdout) + [item for group in injected.values() for item in group]
    evaluation_report = audit_sequences(eval_sequences, baseline, baseline_digest=digest_once)
    markdown = render_markdown(evaluation_report)
    valid_cards = _evidence_card_ids(corpus)
    hits = [hit for finding in evaluation_report["findings"] for hit in finding["hits"]]
    real_hits = [hit for hit in hits if hit.get("card_id") in valid_cards]
    synthetic_hits = [hit for hit in hits if str(hit.get("card_id") or "").startswith("injected:")]
    untraceable = len(hits) - len(real_hits) - len(synthetic_hits)
    tracing = {
        "hits_total": len(hits),
        "hits_on_real_cards": len(real_hits),
        "hits_on_synthetic_steps": len(synthetic_hits),
        "hits_untraceable": untraceable,
        "hits_with_seq": sum(1 for hit in hits if isinstance(hit.get("seq"), int) and hit["seq"] >= 0),
        # 可回溯率只在真实卡片上定义：评估时凭空插入的步骤没有真实工件，
        # 把它们算进分母会让「证据可回溯」这项永远不达标，也会掩盖真正的问题。
        "trace_rate": (len(real_hits) / (len(real_hits) + untraceable)) if (real_hits or untraceable) else 1.0,
    }
    usability = {
        "markdown_sections": ["## 一、", "## 二、", "## 三、", "## 四、"],
        "markdown_has_sections": all(
            section in markdown for section in ("## 一、", "## 二、", "## 三、", "## 四、")
        ),
        "finding_fields": ["sequence_id", "operator", "started_at", "pattern", "severity", "codes", "hits"],
        "finding_fields_complete": all(
            all(field in finding for field in ("sequence_id", "operator", "started_at", "pattern", "severity", "codes", "hits"))
            for finding in evaluation_report["findings"]
        ),
        "tracing": tracing,
        "trace_rate": tracing["trace_rate"],
        "production_report": {
            "scanned": production["scanned_sequences"],
            "flagged": production["flagged_sequences"],
            "by_code": production["summary"]["by_code"],
        },
    }

    # --- 标准四：规则覆盖（启用 / 命中 / 显式停用，不允许静默消失）---
    by_code = evaluation_report["summary"]["by_code"]
    fired = set(by_code) | {code for code, count in production["summary"]["by_code"].items() if count}
    skipped_codes = {item["code"] for item in evaluation_report["summary"]["skipped_rules"]}
    enabled_no_hit = set(evaluation_report["summary"].get("enabled_no_hit") or [])
    catalog_codes = {spec.code for spec in RULE_CATALOG}
    coverage = {
        "catalog": sorted(catalog_codes),
        "fired": sorted(fired),
        "enabled_no_hit": sorted(enabled_no_hit),
        "skipped": sorted(skipped_codes),
        "silent": sorted(catalog_codes - fired - skipped_codes - enabled_no_hit),
    }

    # --- 数据口径说明（写进证据，避免以后被误读成历史语料）---
    wording = {
        "corpus_kind": "bootstrap_synthetic_traffic",
        "explain": (
            "语料由真实 API 调用生成（真实回测引擎、真实审计中间件、真实哈希链），"
            "但生成时间为本次构建，属首次引导语料，不是历史积累；"
            "语料内所有会话时间几乎重合，故小时维度（off_hours）规则在本轮必然停用。"
        ),
        "holdout_split": "按会话 id 的 sha256 前缀切分 30%，与时间无关",
        "injection_scope": "异常注入只作用于内存序列副本，不写回工件库，避免破坏哈希链",
    }

    hit_rate = evaluation["anomalies"]["hit_rate"]
    strict_rate = evaluation["anomalies"]["strict_rate"]
    passed = (
        top10_reproducible["same_corpus_same_digest"]
        and top10_reproducible["top10_match"]
        and hit_rate >= 0.80
        and usability["markdown_has_sections"]
        and usability["finding_fields_complete"]
        and usability["trace_rate"] >= 0.99
        and not coverage["silent"]
    )

    payload = {
        "task": "T6 影子复盘",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus": {
            "dir": str(corpus.relative_to(ROOT)) if corpus.is_relative_to(ROOT) else str(corpus),
            "sequences_total": len(sequences),
            "train_sequences": len(train),
            "holdout_sequences": len(holdout),
            "cards": len(valid_cards),
        },
        "baseline": {
            "version": baseline.get("baseline_version"),
            "digest": digest_once,
            "operators": baseline["corpus"]["operators"],
            "operations": sorted(baseline["operations"]),
        },
        "standard_1_top10_reproducible": top10_reproducible,
        "standard_2_hit_rate": {
            "hit_rate": hit_rate,
            "hit_rate_ci": evaluation["anomalies"]["hit_rate_ci"],
            "strict_rate": strict_rate,
            "strict_rate_ci": evaluation["anomalies"]["strict_rate_ci"],
            "threshold": 0.80,
            "passed": hit_rate >= 0.80,
            "by_type": evaluation["anomalies"]["by_type"],
            "noop_injections": evaluation["anomalies"].get("noop_injections"),
            "false_positive_clean": evaluation["clean"],
            "hard_negatives": evaluation["hard_negatives"],
        },
        "standard_3_report_usable": usability,
        "standard_4_rule_coverage": coverage,
        "wording": wording,
        "verdict": "SHADOW_REVIEW_READY" if passed else "SHADOW_REVIEW_NOT_READY",
        "gaps": _gaps(evaluation, coverage, top10_reproducible, usability),
    }

    (Path(args.report)).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(markdown + "\n", encoding="utf-8", newline="\n")
    Path(args.report_json).write_text(
        json.dumps(evaluation_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # 生产形态报告（全库，无注入样本）：质检人员日常看的那一份
    production_md = render_markdown(production, title="影子复盘异常审计报告（生产形态：全库扫描）")
    Path(args.production_report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.production_report).write_text(production_md + "\n", encoding="utf-8", newline="\n")
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(payload["verdict"], ensure_ascii=False))
    print(f"命中率 {hit_rate:.1%}（严格 {strict_rate:.1%}），误报 {evaluation['clean']['flagged_rate']:.1%}")
    print(f"Top10 可复现：{top10_reproducible['same_corpus_same_digest']} / 独立复算一致：{top10_reproducible['top10_match']}")
    print(f"证据 JSON：{args.out}")
    return 0 if passed else 1


def _gaps(
    evaluation: dict[str, Any],
    coverage: dict[str, Any],
    top10: dict[str, Any],
    usability: dict[str, Any],
) -> list[str]:
    """把「没达到的地方」写清楚，避免验收文件只有通过项。"""
    gaps: list[str] = []
    if evaluation["clean"]["flagged_rate"] > 0.2:
        gaps.append(
            f"干净留出集误报率 {evaluation['clean']['flagged_rate']:.1%}：基线覆盖的会话类型不足，"
            "上线前需扩大语料或放宽对应规则"
        )
    weak = [
        (kind, item)
        for kind, item in evaluation["anomalies"]["by_type"].items()
        if item.get("total") and item.get("flagged_rate", 0) < 0.8
    ]
    for kind, item in weak:
        gaps.append(f"注入类型 {kind} 命中率仅 {item['flagged_rate']:.1%}（n={item['total']}），需补规则")
    if coverage["silent"]:
        gaps.append(f"规则 {coverage['silent']} 既未启用也未登记停用原因")
    if not top10["top10_match"]:
        gaps.append("独立实现与基线模块的 Top10 不一致：统计口径有分歧，先查口径再谈结论")
    if usability["trace_rate"] < 1.0:
        gaps.append(f"有 {usability['tracing']['hits_untraceable']} 条命中无法回溯到真实工件")
    production = usability.get("production_report") or {}
    if production.get("flagged") == 0:
        gaps.append(
            "生产形态全库扫描 0 命中是「基线来自同一语料」的必然结果，不代表系统没有异常；"
            "真实价值在于新会话进入时与基线比对，需要持续语料才有意义"
        )
    gaps.append("语料为单次构建的引导语料，小时维度规则未启用；真实部署后须用积累语料重建基线")
    return gaps


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T6 影子复盘：语料 / 基线 / 审计 / 验收")
    sub = parser.add_subparsers(dest="command", required=True)

    p_corpus = sub.add_parser("corpus", help="跑真实 API 生成审计语料")
    p_corpus.add_argument("--out", default=str(DEFAULT_CORPUS))
    p_corpus.add_argument("--sessions", type=int, default=120)
    p_corpus.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_corpus.set_defaults(func=cmd_corpus)

    p_build = sub.add_parser("build", help="构建质检基线")
    p_build.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    p_build.add_argument("--out", default=str(DEFAULT_BASELINE))
    p_build.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_build.set_defaults(func=cmd_build)

    p_audit = sub.add_parser("audit", help="对留出集出异常报告")
    p_audit.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    p_audit.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p_audit.add_argument("--out", default=str(DEFAULT_REPORT))
    p_audit.add_argument("--json-out", default=str(DEFAULT_REPORT_JSON))
    p_audit.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_audit.set_defaults(func=cmd_audit)

    p_ev = sub.add_parser("evidence", help="产出验收证据")
    p_ev.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    p_ev.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p_ev.add_argument("--out", default=str(DEFAULT_EVIDENCE))
    p_ev.add_argument("--report", default=str(DEFAULT_REPORT))
    p_ev.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    p_ev.add_argument("--production-report", default=str(DEFAULT_PRODUCTION_REPORT))
    p_ev.add_argument("--per-type", type=int, default=DEFAULT_PER_TYPE)
    p_ev.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_ev.set_defaults(func=cmd_evidence)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
