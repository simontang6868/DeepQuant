"""扩样结果对照分析 — 只做事实汇总，不做择优、不给结论。

输入两份 research.py 的 step75_research_summary.json：
  --base   6 只手挑标的（原口径）
  --wide   扩样池（build_universe.py 产出）

输出：
  1. 闸门拦截率与拦截原因（**这是扩样最重要的产出**：可用率本身就是结论）；
  2. 零中位数占比（原研究的核心症状，扩样后是否仍是「结构性」的）；
  3. 参与率 / 收益中位数 / 回撤的分布（分位数，不看均值 —— 均值被极端值主导）；
  4. base 与 wide 在同一策略同一费率下的对照。

明确不做的事：
  - 不按收益排序挑「好标的」（R11）；
  - 不把窗口收益中位数当成账户连续收益（R12）；
  - 不对 wide 池的收益下总体结论 —— 该池是抓取当日成分股，缺退市样本（幸存者偏差）。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import numpy as np

ZERO_TOL = 1e-12


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(payload: dict, strategy: str | None = None, fee: str | None = None) -> list[dict]:
    out = []
    for r in payload.get("results", []):
        if "error" in r or r.get("positive_ratio") is None:
            continue
        if strategy is not None and r["strategy"] != strategy:
            continue
        if fee is not None and r["fee_source"] != fee:
            continue
        out.append(r)
    return out


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "min": round(float(arr.min()), 6),
        "p25": round(float(np.percentile(arr, 25)), 6),
        "median": round(float(np.percentile(arr, 50)), 6),
        "p75": round(float(np.percentile(arr, 75)), 6),
        "max": round(float(arr.max()), 6),
        "mean": round(float(arr.mean()), 6),
    }


def gate_summary(payload: dict) -> dict:
    """可用率与拦截原因分布。可用率 = 通过闸门且行数足够的标的 / 全部拉取成功的标的。"""
    stocks = payload.get("stocks", [])
    fetched = [s for s in stocks if s.get("ok")]
    ok = [s for s in fetched if s.get("data_ok", True)]
    skipped = payload.get("skipped", [])
    reason_counter: dict[str, int] = {}
    for s in skipped:
        for code in s.get("error_codes") or ["unknown"]:
            reason_counter[code] = reason_counter.get(code, 0) + 1
    return {
        "n_requested": len(stocks),
        "n_fetched": len(fetched),
        "n_usable": len(ok),
        "n_skipped": len(skipped),
        "usable_rate": round(len(ok) / len(fetched), 4) if fetched else None,
        "skip_reasons": reason_counter,
        "skipped_codes": [s.get("code") for s in skipped],
        "universe_bias": (payload.get("universe") or {}).get("bias_survivorship"),
    }


def strategy_block(payload: dict, fee: str = "simulation_placeholder") -> dict:
    out = {}
    for strat in sorted({r["strategy"] for r in payload.get("results", []) if "strategy" in r}):
        rows = _rows(payload, strategy=strat, fee=fee)
        if not rows:
            continue
        meds = [r["median_ret"] for r in rows]
        zeros = sum(1 for m in meds if abs(m) < ZERO_TOL)
        out[strat] = {
            "n_symbols": len(rows),
            "n_windows": sorted({r["n_windows"] for r in rows}),
            "zero_median_symbols": zeros,
            "zero_median_rate": round(zeros / len(rows), 4),
            "participation": _quantiles([r["positive_ratio"] for r in rows]),
            "median_ret": _quantiles(meds),
            "median_mdd": _quantiles([r["median_mdd"] for r in rows]),
            # 非零中位数标的的收益中位数：剔除零参与后的真实收益形状
            "median_ret_excl_zero": _quantiles([m for m in meds if abs(m) >= ZERO_TOL]),
        }
    return out


def fee_sensitivity(payload: dict, fee_order: list[str]) -> dict:
    """同一策略在三种费率下的收益中位数；差额 = 费率对结论的影响幅度。"""
    out = {}
    for strat in sorted({r["strategy"] for r in payload.get("results", []) if "strategy" in r}):
        per_fee = {}
        for fee in fee_order:
            rows = _rows(payload, strategy=strat, fee=fee)
            if rows:
                per_fee[fee] = round(st.median([r["median_ret"] for r in rows]), 6)
        if len(per_fee) >= 2:
            vals = list(per_fee.values())
            per_fee["_spread"] = round(max(vals) - min(vals), 6)
        out[strat] = per_fee
    return out


def compare(base: dict, wide: dict, fee: str) -> dict:
    out = {}
    for strat in sorted({r["strategy"] for r in base.get("results", []) if "strategy" in r}):
        b = _rows(base, strategy=strat, fee=fee)
        w = _rows(wide, strategy=strat, fee=fee)
        if not b or not w:
            continue
        bm = [r["median_ret"] for r in b]
        wm = [r["median_ret"] for r in w]
        out[strat] = {
            "base_n": len(b),
            "wide_n": len(w),
            "base_zero_rate": round(sum(1 for m in bm if abs(m) < ZERO_TOL) / len(b), 4),
            "wide_zero_rate": round(sum(1 for m in wm if abs(m) < ZERO_TOL) / len(w), 4),
            "base_median_of_medians": round(st.median(bm), 6),
            "wide_median_of_medians": round(st.median(wm), 6),
        }
    return out


def to_markdown(report: dict) -> str:
    lines = []
    g = report["gate"]
    lines.append("## 1. 数据闸门：可用率就是结论\n")
    lines.append(
        f"请求 {g['n_requested']} 只｜拉取成功 {g['n_fetched']} 只｜通过闸门 {g['n_usable']} 只｜"
        f"**可用率 {g['usable_rate']:.1%}**"
    )
    if g["skip_reasons"]:
        lines.append("\n拦截原因：" + "，".join(f"`{k}` × {v}" for k, v in g["skip_reasons"].items()))
    if g["universe_bias"]:
        lines.append(f"\n> 偏差声明：{g['universe_bias']}")
    lines.append("")

    lines.append("## 2. 零中位数是否仍是结构性的\n")
    lines.append("| 策略 | 标的数 | 窗口数 | 零中位数标的 | 占比 |")
    lines.append("|---|---|---|---|---|")
    for strat, v in report["strategy"].items():
        wins = v["n_windows"]
        win_txt = str(wins[0]) if len(wins) == 1 else "/".join(str(w) for w in wins)
        lines.append(
            f"| {strat} | {v['n_symbols']} | {win_txt} | "
            f"{v['zero_median_symbols']} | {v['zero_median_rate']:.1%} |"
        )
    lines.append("")

    lines.append("## 3. 分布（模拟费率）\n")
    lines.append("| 策略 | 指标 | p25 | 中位 | p75 | 剔除零参与后中位 |")
    lines.append("|---|---|---|---|---|---|")
    for strat, v in report["strategy"].items():
        ex = v["median_ret_excl_zero"].get("median")
        ex_txt = "—" if ex is None else f"{ex:+.4f}"
        lines.append(
            f"| {strat} | 参与率 | {v['participation']['p25']:.1%} | "
            f"{v['participation']['median']:.1%} | {v['participation']['p75']:.1%} | — |"
        )
        lines.append(
            f"| {strat} | 收益中位数 | {v['median_ret']['p25']:+.4f} | "
            f"{v['median_ret']['median']:+.4f} | {v['median_ret']['p75']:+.4f} | {ex_txt} |"
        )
    lines.append("")

    lines.append("## 4. 原口径 vs 扩样（同策略同费率）\n")
    lines.append("| 策略 | 原 n | 扩样 n | 原零中位数率 | 扩样零中位数率 | 原中位 | 扩样中位 |")
    lines.append("|---|---|---|---|---|---|---|")
    for strat, v in report["compare"].items():
        lines.append(
            f"| {strat} | {v['base_n']} | {v['wide_n']} | {v['base_zero_rate']:.1%} | "
            f"{v['wide_zero_rate']:.1%} | {v['base_median_of_medians']:+.4f} | "
            f"{v['wide_median_of_medians']:+.4f} |"
        )
    lines.append("")

    lines.append("## 5. 费率敏感度（收益中位数）\n")
    for strat, per in report["fee_sensitivity"].items():
        parts = [f"{k}={v:+.4f}" for k, v in per.items() if not k.startswith("_")]
        spread = per.get("_spread")
        tail = f"｜跨费率极差 {spread:+.4f}" if spread is not None else ""
        lines.append(f"- **{strat}**：" + "，".join(parts) + tail)
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="扩样结果对照分析（只汇总事实）")
    ap.add_argument("--base", required=True, help="原 6 只标的的 summary json")
    ap.add_argument("--wide", required=True, help="扩样池的 summary json")
    ap.add_argument("--fee", default="simulation_placeholder")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args(argv)

    base = _load(args.base)
    wide = _load(args.wide)
    fee_order = ["simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost"]

    report = {
        "base_range": base.get("request_range"),
        "wide_range": wide.get("request_range"),
        "gate": gate_summary(wide),
        "strategy": strategy_block(wide, fee=args.fee),
        "compare": compare(base, wide, args.fee),
        "fee_sensitivity": fee_sensitivity(wide, fee_order),
        "caveats": [
            "扩样池为抓取当日指数成分股 → 幸存者偏差，收益类指标偏乐观，不得用于总体结论。",
            "窗口收益中位数 ≠ 账户连续收益（R12）。",
            "参与率是数量指标，禁用于择优（R11）。",
        ],
    }

    md = to_markdown(report)
    print(md)
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
