"""窗口数偏斜诊断 — OOS=75 引入的等权重问题是否污染了中位数。

背景：
    口径 v2（OOS=75）把窗口数从「均匀 35」变成 **5~23**。
    只有 5 个窗口的标的，其收益中位数的统计意义几乎为零，
    但在汇总表**与 23 窗口的标的等权重**。

本脚本只回答一个问题：
    **合并中位数能不能代表样本？** 判据是分层后各层的中位数是否一致。
    若层间差异 > 1pp，合并值就不可直接引用，必须报分层值。

不做的事（与所有 analysis 脚本一致）：
    - 不按分层结果去「剔除窗口少的标的」—— 那是挑选样本，会让结论失真；
    - 不排序、不择优（R11）；不把窗口收益中位数当账户收益（R12）。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

ZERO_TOL = 1e-12

# 分层边界（窗口数，左开右闭）。v2 实测窗口数：5,6,8,11,12,13,15,17,19,20,21,22,23。
# 边界必须与标签自洽 —— 曾因 (21,23] 配 "21-22" 把 n=23 的 34 只全塞进 "21-22" 层。
DEFAULT_BINS = ((0, 10), (10, 15), (15, 20), (20, 22), (22, 10**6))
BIN_LABELS = ("5-10", "11-15", "16-20", "21-22", "23")


def bin_label(n: int, bins=DEFAULT_BINS, labels=BIN_LABELS) -> str:
    for (lo, hi), label in zip(bins, labels):
        if lo < n <= hi:
            return label
    return labels[-1]


def rows_by_bin(payload: dict, strategy: str, fee: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in payload.get("results", []):
        if "error" in r or r.get("positive_ratio") is None:
            continue
        if r["strategy"] != strategy or r["fee_source"] != fee:
            continue
        out.setdefault(bin_label(r["n_windows"]), []).append(r)
    return out


def summarize(rows: list[dict]) -> dict:
    meds = [r["median_ret"] for r in rows]
    zeros = sum(1 for m in meds if abs(m) < ZERO_TOL)
    pos = sum(1 for m in meds if m > ZERO_TOL)
    return {
        "n": len(rows),
        "windows": sorted({r["n_windows"] for r in rows}),
        "median_ret": round(st.median(meds), 6),
        "zero_rate": round(zeros / len(rows), 4),
        "positive_n": pos,
        "mean_ret": round(st.fmean(meds), 6),
    }


def analyze(payload: dict, strategies, fees) -> dict:
    design = payload.get("design") or {}
    out = {
        "design_version": design.get("design_version"),
        "min_oos_periods": design.get("min_oos_periods"),
        "strategy": {},
    }
    for strat in strategies:
        for fee in fees:
            by_bin = rows_by_bin(payload, strat, fee)
            if not by_bin:
                continue
            all_rows = [r for rows in by_bin.values() for r in rows]
            layers = {k: summarize(v) for k, v in sorted(by_bin.items())}
            pooled = summarize(all_rows)
            # 判据：层间中位数的极差
            med_list = [v["median_ret"] for v in layers.values() if v["n"] >= 2]
            spread = (max(med_list) - min(med_list)) if len(med_list) >= 2 else 0.0
            out["strategy"].setdefault(strat, {})[fee] = {
                "pooled": pooled,
                "layers": layers,
                "spread_between_layers": round(spread, 6),
                "pooled_is_representative": spread < 0.01,  # < 1pp
            }
    return out


def to_markdown(report: dict) -> str:
    lines = [
        f"> 口径 `{report.get('design_version')}`｜OOS {report.get('min_oos_periods')} 日",
        "",
        "## 分层结果（按每标的的窗口数分层）",
        "",
        "| 策略 | 费率 | 层 | 标的数 | 窗口数 | 收益中位 | 零率 | 正收益数 |",
        "|---|---|---|---:|---|---:|---:|---:|",
    ]
    for strat, fees in report["strategy"].items():
        for fee, blk in fees.items():
            p = blk["pooled"]
            lines.append(
                f"| {strat} | {fee[:22]} | **合并** | {p['n']} | "
                f"{p['windows'][0]}~{p['windows'][-1]} | **{p['median_ret']:+.4f}** | "
                f"{p['zero_rate']:.1%} | {p['positive_n']} |"
            )
            for label, v in blk["layers"].items():
                w = f"{v['windows'][0]}" if len(v["windows"]) == 1 else f"{v['windows'][0]}~{v['windows'][-1]}"
                flag = "" if v["n"] >= 3 else " ⚠️样本过少"
                lines.append(
                    f"| | | {label}{flag} | {v['n']} | {w} | {v['median_ret']:+.4f} | "
                    f"{v['zero_rate']:.1%} | {v['positive_n']} |"
                )
            lines.append("")

    lines.append("## 判据：合并值能否代表样本（层间极差 < 1pp 才算代表）")
    lines.append("")
    lines.append("| 策略 | 费率 | 层间极差 | 合并值可用? |")
    lines.append("|---|---|---:|---|")
    for strat, fees in report["strategy"].items():
        for fee, blk in fees.items():
            ok = "✅ 是" if blk["pooled_is_representative"] else "❌ **否，必须报分层值**"
            lines.append(
                f"| {strat} | {fee[:22]} | {blk['spread_between_layers']*100:.2f}pp | {ok} |"
            )
    lines.append("")
    lines.append("> 只报事实。分层不是剔除 —— 窗口少的标的同样是样本的一部分，")
    lines.append("> 层间差异大说明**这个样本内部不同质**，不是说明该删掉哪一层。")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="窗口数偏斜诊断（OOS=75 等权重问题）")
    ap.add_argument("--summary", required=True, help="step75_research_summary.json")
    ap.add_argument("--fee", action="append", default=None,
                    help="费率档，可重复；默认跑三档")
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)

    payload = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    fees = args.fee or [
        "simulation_placeholder",
        "broker_reference_common_2024",
        "research_scenario_high_cost",
    ]
    report = analyze(payload, ("ma_cross", "mean_reversion"), fees)
    md = to_markdown(report)
    print(md)
    if args.out_md:
        Path(args.out_md).write_text(md, encoding="utf-8")
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
