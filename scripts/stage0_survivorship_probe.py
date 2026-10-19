"""阶段 0a：幸存者偏差量化探针 —— 退市组 vs 在市组。

预登记：`docs/stage0_prereg_2026-10-19.json` + `docs/STAGE0_PREREG_2026-10-19.md`
（**先冻结、后跑**，判据在 json 里，本脚本只执行不发明）。

它回答什么
----------
一个纯观测问题：把 2017 年后退市的 262 只补进池之后，策略在这批失败样本上的表现
是否显著差于 csi300 样本中的在市股？

它**不**回答什么（预登记 §6 已钉死）
-----------------------------------
- 不是策略有效性检验；不据此说"策略在全市场无效"
- 不是 PIT 池；不是全市场研究（在市值只有 50 只）
- 不触发 PIT 采购（T1 闸门仍关闭）

为什么两组并集就是"时点无关池"
------------------------------
每只标的只在"自己第一根 K 线到最后一根 K 线"上跑，退市股的最后一行就是退市日。
"某时点池里该有谁"由行情文件自身的边界回答，不需要额外装配层。这是阶段 0 零成本成立的原因。

运行
----
    PYTHONPATH=backend/src python scripts/stage0_survivorship_probe.py
    ... --only A --limit 5      # 试跑估算耗时
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import FEE_PRESET_SIMULATION
from deepquant.data.validation.checks_extended import validate_daily_extended
from deepquant.market.rules import a_share_price_limit_rate, a_share_rules
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2

PREREG = ROOT / "docs" / "stage0_prereg_v1.1_2026-10-19.json"
PREREG_V1 = ROOT / "docs" / "stage0_prereg_2026-10-19.json"
LISTED_MANIFEST = ROOT / "docs" / "STAGE0_LISTED_QUOTES_2026-10-19.json"
DELIST_MANIFEST = ROOT / "docs" / "DELIST_UNIVERSE_2026-10-19.json"
OUT = ROOT / "docs" / "STAGE0_RESULTS_2026-10-19.json"

# 行情口径布局：legacy = 腾讯加性前复权原文件；mult = 乘性前复权（scripts/repair_qfq_mult.py 生成）
LAYOUT_MAP = {
    "legacy": {"data/stage0/listed/": "data/stage0/listed/", "data/delist/": "data/delist/"},
    "mult": {"data/stage0/listed/": "data/qfq_mult/listed/", "data/delist/": "data/qfq_mult/delist/"},
}
LAYOUT = "mult"


def map_path(rel: str) -> str:
    for old, new in LAYOUT_MAP[LAYOUT].items():
        if rel.startswith(old):
            return new + rel[len(old):]
    return rel

# --- 冻结参数：逐项复制 data/step75/research.py 的 MA_GRID 与常量（R2/R11） ---
START = "2017-01-01"
END = "2024-12-31"
INITIAL_CASH = 100_000.0
DESIGN_VERSION = "oos75-v3"
OOS_WINDOW = 75
MA_GRID = GridSearchConfig(
    fast_candidates=(3, 5, 10, 20),
    slow_candidates=(15, 25, 40, 60),
    min_train_periods=150,
    min_oos_periods=OOS_WINDOW,
    min_trades=2,
    max_combinations=50,
)
PRICE_LIMIT_MODE = "board"


def _ma_factory(p):
    return MaCrossStrategy(fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"]))


def market_rules_for(code: str):
    return a_share_rules(price_limit_rate=a_share_price_limit_rate(code, mode=PRICE_LIMIT_MODE))


# ---------------------------------------------------------------- 成员装配


def load_members() -> tuple[list[dict], list[str], list[dict]]:
    """返回 (成员列表, 装配告警, 因行情不可得而整只缺席的清单)。

    缺席清单是预登记 §4-1「不许静默省略」的落点：文件缺失必须逐条进 `skipped`，
    不能只写在 notes 里。
    """
    notes: list[str] = []
    members: list[dict] = []
    missing: list[dict] = []

    listed = json.loads(LISTED_MANIFEST.read_text(encoding="utf-8"))
    n_listed_ok = 0
    for s in listed["symbols"]:
        if s["status"] not in ("downloaded", "cached") or not s.get("file"):
            notes.append(f"A 组跳过 {s['code']}：行情状态 {s['status']}")
            missing.append({"code": s["code"], "group": "A_listed", "status": "no_quote",
                            "reason": f"清单状态 {s['status']}", "data_errors": []})
            continue
        path = ROOT / map_path(s["file"])
        if not path.exists():
            notes.append(f"A 组跳过 {s['code']}：清单存在但文件缺失 {map_path(s['file'])}")
            missing.append({"code": s["code"], "group": "A_listed", "status": "file_missing",
                            "reason": f"{map_path(s['file'])} 不存在（乘性复权修复失败或无数据）",
                            "data_errors": []})
            continue
        members.append({
            "code": s["code"], "group": "A_listed", "name": s.get("name", ""),
            "file": map_path(s["file"]), "delist_date": None,
            "list_date": None, "market": s["code"].split(".")[-1].lower(),
        })
        n_listed_ok += 1
    notes.append(f"A 组装配 {n_listed_ok} 只（名单 {len(listed['symbols'])}）")

    delist = json.loads(DELIST_MANIFEST.read_text(encoding="utf-8"))
    n_delist_ok = 0
    for s in delist["symbols"]:
        # 单只状态是 downloaded / cached（"usable" 只出现在 counts 汇总里，不是单只状态）
        if s["status"] not in ("downloaded", "cached", "usable"):
            notes.append(f"B 组跳过 {s['code']}：状态 {s['status']}")
            continue
        if not s.get("delist_date") or s["delist_date"] < START:
            continue
        # 清单里 code 是 6 位（000748），文件名带市场后缀（000748.SZ.qfq.csv）；
        # 统一成 "000748.SZ" 形式，与 A 组 code 口径一致（板块解析要吃后缀/前缀）
        full = f"{s['code']}.{s['market'].upper()}"
        rel = map_path(f"data/delist/{full}.qfq.csv")
        if not (ROOT / rel).exists():
            notes.append(f"B 组跳过 {s['code']}：清单存在但文件缺失 {rel}")
            missing.append({"code": full, "group": "B_delisted", "status": "file_missing",
                            "reason": f"{rel} 不存在（乘性复权修复失败或无数据）",
                            "data_errors": []})
            continue
        members.append({
            "code": full, "group": "B_delisted", "name": s.get("name", ""),
            "file": rel, "delist_date": s["delist_date"],
            "list_date": s.get("list_date"), "market": s.get("market", ""),
        })
        n_delist_ok += 1
    notes.append(f"B 组装配 {n_delist_ok} 只（清单 usable {delist['counts']['usable']}）")

    return members, notes, missing


# ---------------------------------------------------------------- 单只回测


def run_symbol(m: dict) -> dict:
    rec = {
        "code": m["code"], "group": m["group"], "name": m["name"],
        "delist_date": m["delist_date"], "status": "ok",
        "windows": [], "skip_reason": None,
        "data_errors": [], "data_warnings": [],
    }
    try:
        df = pd.read_csv(ROOT / m["file"])
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "read_failed"
        rec["skip_reason"] = str(exc)[:200]
        return rec

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df = df[(df["date"] >= START) & (df["date"] <= END)].reset_index(drop=True)
    rec["n_rows"] = len(df)
    if len(df) < 400:
        rec["status"] = "too_short"
        rec["skip_reason"] = f"区间内仅 {len(df)} 行（<400）"
        return rec

    report = validate_daily_extended(df, adjust_status="qfq")
    rec["data_errors"] = [i.code for i in report.issues if i.level == "error"]
    rec["data_warnings"] = [i.code for i in report.issues if i.level == "warning"]
    if not report.is_valid:
        rec["status"] = "data_quality_gate"
        rec["skip_reason"] = "数据质量闸门拦截"
        return rec

    df_wf = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    df_wf.index.name = None
    cfg = FEE_PRESET_SIMULATION.apply(
        BacktestConfig(symbol=m["code"], initial_cash=INITIAL_CASH, market_rules=market_rules_for(m["code"]))
    )
    try:
        _specs, wf_results, summary = run_walkforward_v2(
            df_wf, cfg, strategy_factory=_ma_factory, grid_config=MA_GRID, capture_trades=False
        )
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "backtest_failed"
        rec["skip_reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return rec

    rec["n_windows_total"] = len(wf_results)
    rec["n_selection_failed"] = summary.n_selection_failed
    rec["n_oos_failed"] = summary.n_oos_failed
    for r in wf_results:
        rec["windows"].append({
            "oos_start": str(r.oos_start) if getattr(r, "oos_start", None) is not None else None,
            "oos_end": str(r.oos_end) if getattr(r, "oos_end", None) is not None else None,
            "selection_success": bool(r.selection_success),
            "oos_success": bool(r.oos_success),
            "is_complete": bool(r.oos_is_complete),
            "ret": (round(float(r.cumulative_return), 8)
                    if r.cumulative_return is not None and np.isfinite(r.cumulative_return) else None),
            "trades": int(r.trade_count or 0),
            "fee_source": cfg.fee_source if hasattr(cfg, "fee_source") else None,
        })
    rec["fee_source"] = getattr(cfg, "fee_source", None)
    return rec


# ---------------------------------------------------------------- 统计


def valid_rets(rec: dict) -> list[float]:
    """口径与 research.py::window_return_stats / summarize_v2 完全一致。"""
    return [
        w["ret"] for w in rec["windows"]
        if w["selection_success"] and w["oos_success"] and w["is_complete"] and w["ret"] is not None
    ]


def desc(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    arr = np.array(vals, dtype=float)
    return {
        "n": int(arr.size),
        "median": round(float(np.median(arr)), 6),
        "mean": round(float(arr.mean()), 6),
        "p25": round(float(np.percentile(arr, 25)), 6),
        "p75": round(float(np.percentile(arr, 75)), 6),
        "worst": round(float(arr.min()), 6),
        "best": round(float(arr.max()), 6),
        "loss_ratio": round(float((arr < 0).sum() / arr.size), 4),
        "pos_ratio": round(float((arr > 0).sum() / arr.size), 4),
    }


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mannwhitney(x: list[float], y: list[float]) -> dict:
    """Mann-Whitney U（含 tie 校正的正态近似）。

    x = B 组（退市），y = A 组（在市）。返回 U_x 与其相对期望的偏离：
    p_lower 是「x 整体偏小」的单侧 p，即预登记要的那个方向。
    无 scipy，用正态近似；n=312 量级下近似误差可忽略，且与预登记写法一致（未换检验）。
    """
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return {"error": "empty group", "n1": n1, "n2": n2}
    allv = np.array(x + y, dtype=float)
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1, dtype=float)
    # 平均秩处理并列
    s = pd.Series(allv)
    for _v, idx in s.groupby(s).groups.items():
        if len(idx) > 1:
            m = float(np.mean(ranks[list(idx)]))
            ranks[list(idx)] = m
    r1 = float(ranks[:n1].sum())
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    vc = s.value_counts()
    tie = float(sum((t ** 3 - t) for t in vc if t > 1))
    n = n1 + n2
    var = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return {"error": "zero variance", "n1": n1, "n2": n2}
    z = (u1 - mu) / math.sqrt(var)
    p_lower = _phi(z)           # x 偏小（U 偏小）
    p_upper = 1.0 - p_lower
    return {
        "n_B": n1, "n_A": n2,
        "U_B": round(u1, 3), "expected_U": round(mu, 3),
        "z": round(z, 4),
        "p_one_sided_B_less": round(p_lower, 6),
        "p_two_sided": round(min(1.0, 2 * min(p_lower, p_upper)), 6),
        "rank_biserial_B_minus_A": round(2 * u1 / (n1 * n2) - 1, 4),
        "note": "rank_biserial > 0 表示 B 组整体偏大；p_one_sided_B_less 是『B 偏小』方向",
    }


def decide(stats: dict, mw: dict) -> tuple[str, str]:
    """按预登记 decision_rules 机械判定，顺序即优先级。"""
    nA = stats["A_listed"].get("n", 0)
    nB = stats["B_delisted"].get("n", 0)
    if nA < 30 or nB < 30:
        return "S0", f"INSUFFICIENT_DATA：A 组 {nA} / B 组 {nB} 有效窗口，任一组 <30"
    if "error" in mw:
        return "S0", f"INSUFFICIENT_DATA：{mw['error']}"
    d_med = stats["B_delisted"]["median"] - stats["A_listed"]["median"]
    if mw["p_one_sided_B_less"] < 0.05 and d_med < 0:
        return "S1", (f"SURVIVORSHIP_MATERIAL：p_one={mw['p_one_sided_B_less']:.4f} < 0.05 且 "
                      f"median(B)-median(A)={d_med:+.6f} < 0")
    if mw["p_two_sided"] < 0.05 and d_med > 0:
        return "S2", (f"SURVIVORSHIP_REVERSED：p_two={mw['p_two_sided']:.4f} < 0.05 且 "
                      f"median(B)-median(A)={d_med:+.6f} > 0")
    return "S3", (f"SURVIVORSHIP_IMMATERIAL：p_one={mw['p_one_sided_B_less']:.4f}, "
                  f"p_two={mw['p_two_sided']:.4f}, Δmedian={d_med:+.6f}；"
                  f"不显著≠偏差不存在，只代表这批数据测不出来")


# ---------------------------------------------------------------- 主流程


def main() -> None:
    global LAYOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["A", "B"], default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--prereg", default=str(PREREG), help="预登记 json 路径")
    ap.add_argument("--layout", choices=["legacy", "mult"], default=LAYOUT,
                    help="行情口径：legacy=加性前复权原文件；mult=乘性前复权（默认）")
    ap.add_argument("--jobs", type=int, default=1, help="并行进程数（结果与串行逐位一致）")
    args = ap.parse_args()
    LAYOUT = args.layout

    prereg_path = Path(args.prereg)
    prereg_raw = prereg_path.read_bytes()
    prereg = json.loads(prereg_raw)
    prereg_sha = hashlib.sha256(prereg_raw).hexdigest()

    members, notes, missing = load_members()
    if args.only == "A":
        members = [m for m in members if m["group"] == "A_listed"]
    elif args.only == "B":
        members = [m for m in members if m["group"] == "B_delisted"]
    if args.limit:
        members = members[: args.limit]
    print(f"[POOL] 待跑 {len(members)} 只  layout={LAYOUT}  jobs={args.jobs}", flush=True)

    records: list[dict] = []
    if args.jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for i, rec in enumerate(pool.map(run_symbol, members, chunksize=2), 1):
                records.append(rec)
                if i % 10 == 0 or rec["status"] != "ok":
                    print(f"[{i:>3}/{len(members)}] {rec['group'][:1]} {rec['code']} {rec['status']} "
                          f"win={len(valid_rets(rec))}", flush=True)
    else:
        for i, m in enumerate(members, 1):
            rec = run_symbol(m)
            records.append(rec)
            if i % 10 == 0 or rec["status"] != "ok":
                print(f"[{i:>3}/{len(members)}] {m['group'][:1]} {m['code']} {rec['status']} "
                      f"win={len(valid_rets(rec))}", flush=True)

    out_recs = [r for r in records if r["status"] == "ok"]
    rets = {g: [v for r in out_recs if r["group"] == g for v in valid_rets(r)]
            for g in ("A_listed", "B_delisted")}
    stats = {g: desc(v) for g, v in rets.items()}
    active = {g: sum(1 for r in out_recs if r["group"] == g and valid_rets(r)) for g in rets}
    zero_trade = {
        g: sum(1 for r in out_recs if r["group"] == g
               for w in r["windows"]
               if w["selection_success"] and w["oos_success"] and w["trades"] == 0)
        for g in rets
    }

    mw = mannwhitney(rets["B_delisted"], rets["A_listed"])
    verdict, reason = decide(stats, mw)

    # 按窗口起始年份分层（预登记 §4-4）
    by_year: dict[str, dict[str, list[float]]] = {}
    for r in out_recs:
        for w in r["windows"]:
            if not (w["selection_success"] and w["oos_success"] and w["is_complete"] and w["ret"] is not None):
                continue
            y = (w["oos_start"] or "")[:4] or "unknown"
            by_year.setdefault(r["group"], {}).setdefault(y, []).append(w["ret"])

    # 按退市年份分层（预登记 §4-3）
    by_delist_year: dict[str, list[float]] = {}
    for r in out_recs:
        if r["group"] != "B_delisted":
            continue
        y = (r["delist_date"] or "unknown")[:4]
        by_delist_year.setdefault(y, []).extend(valid_rets(r))

    skip_table = [
        {"code": r["code"], "group": r["group"], "status": r["status"],
         "reason": r["skip_reason"], "data_errors": r["data_errors"]}
        for r in records if r["status"] != "ok"
    ] + missing

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prereg": {
            "file": prereg_path.name,
            "prereg_id": prereg["prereg_id"],
            "version": prereg["version"],
            "frozen_at": prereg["frozen_at"],
            "sha256": prereg_sha,
        },
        "data_layout": {
            "name": LAYOUT,
            "note": ("乘性前复权 qfq_mult = hfq × (raw_anchor / hfq_anchor)，"
                     "修复加性前复权在累计分红超过历史价位时产生的近零/负价"
                     if LAYOUT == "mult" else "腾讯/东财加性前复权原文件（qfq_t = raw_t − 未来累计分红_t）"),
            "repaired_manifest": "docs/QFQ_MULT_MANIFEST_2026-10-19.json" if LAYOUT == "mult" else None,
        },
        "design": {
            "strategy": "ma_cross",
            "params_grid": {
                "fast_candidates": [3, 5, 10, 20],
                "slow_candidates": [15, 25, 40, 60],
                "min_train_periods": 150,
                "min_oos_periods": OOS_WINDOW,
                "min_trades": 2,
                "max_combinations": 50,
            },
            "fee_preset": "simulation_placeholder",
            "initial_cash": INITIAL_CASH,
            "date_range": [START, END],
            "price_limit_mode": PRICE_LIMIT_MODE,
            "design_version": DESIGN_VERSION,
        },
        "assembly": {
            "requested": len(members),
            "ran_ok": len(out_recs),
            "by_group": {g: sum(1 for r in out_recs if r["group"] == g) for g in rets},
            "symbols_with_valid_windows": active,
            "notes": notes,
        },
        "stats": stats,
        "zero_trade_windows": zero_trade,
        "mannwhitney": mw,
        "by_oos_start_year": {g: {y: desc(v) for y, v in sorted(d.items())} for g, d in by_year.items()},
        "by_delist_year": {y: desc(v) for y, v in sorted(by_delist_year.items())},
        "skipped": skip_table,
        "verdict": verdict,
        "verdict_reason": reason,
        "scope_note": (
            "在市值仅 50 只（csi300 当日成分等距抽样），退市侧为 2017 年后全量 262 只；"
            "并集 312 只不得称为『全市场池』。本结果不构成策略有效性结论，也不触发 PIT 采购。"
        ),
        "records": [
            {k: v for k, v in r.items() if k != "windows"} | {
                "valid_windows": len(valid_rets(r)),
                "n_windows": len(r["windows"]),
            }
            for r in records
        ],
        "windows": [
            {"code": r["code"], "group": r["group"], **w}
            for r in out_recs for w in r["windows"]
        ],
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n[VERDICT] {verdict} — {reason}")
    print(f"[A] {stats['A_listed']}")
    print(f"[B] {stats['B_delisted']}")
    print(f"[MWU] {mw}")
    print(f"[OUT] {args.out}")


if __name__ == "__main__":
    main()
