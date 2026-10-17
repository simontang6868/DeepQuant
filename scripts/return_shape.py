#!/usr/bin/env python
"""窗口级收益分布形状分析（B12）。

回答一个此前所有汇总都回答不了的问题：
    「中位数为负」到底是**大多数窗口都小亏**（假设方向错），
    还是**少数窗口巨亏、其余本来能赚**（幅度/尾部问题）？

这两个成因指向完全相反的下一步：
  - 假设方向错 → 该换信号，不是调参数；
  - 尾部问题   → 该管风险（止损/仓位），信号本身可能还有信息。

## 判据（**在看到数据之前预设，禁止事后调整**）

判据 1 —— loss_window_ratio（亏损窗口占比）：方向性判据
    < 40%      方向性存在（多数窗口方向对）
    40% ~ 50%  方向不明确
    > 50%      方向错（多数窗口亏，假设不成立）

判据 2 —— abs_mean_minus_abs_median（|均值| − |中位数|）：形状判据（**三档，含负值**）
    >= +1pp  尾部主导：少数大盈/大亏窗口撑起均值，中位数不反映全貌
    -1pp~+1pp 均值≈中位数：分布大致对称
    <= -1pp  典型窗口主导：中位数比均值更极端，亏损**不是**少数极端值造成的
              （原设计只定义了非负情况，负值在实测中出现，属定义缺口，已补全；阈值未改）

阈值写死在下面两个常量里，改它们等于改结论，必须先提案。

## 禁止用途（R2/R11/R12）

- 本脚本**只描述形状，不挑选任何东西**：不排序、不比较策略优劣、不推荐参数。
- 全部是**窗口级独立收益**，不是账户连续收益（R12），不可当作可实现回报。
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

# ── 预设判据阈值（见模块 docstring，禁止事后调整） ──────────────────────────
LOSS_RATIO_DIRECTIONAL_MAX = 0.40   # 低于此 = 方向性存在
LOSS_RATIO_AMBIGUOUS_MAX = 0.50     # 低于此 = 方向不明确；高于 = 方向错
TAIL_DOMINANCE_PP = 0.01            # |均值|-|中位数| 超过 1pp = 尾部主导

# 中位数绝对值小于此值视为「零」（与 expansion_report / window_skew 一致）
ZERO_TOL = 1e-12

FEE_ORDER = (
    "simulation_placeholder",
    "broker_reference_common_2024",
    "research_scenario_high_cost",
)


def _rows(payload: dict, strategy: str) -> list[dict]:
    """取某策略下带 window_stats 的结果行。"""
    out = []
    for r in payload.get("results", []):
        if "error" in r:
            continue
        if r.get("strategy") != strategy:
            continue
        if not r.get("window_stats"):
            continue
        out.append(r)
    return out


def _verdict_loss_ratio(v: float) -> str:
    if v < LOSS_RATIO_DIRECTIONAL_MAX:
        return "方向性存在"
    if v < LOSS_RATIO_AMBIGUOUS_MAX:
        return "方向不明确"
    return "方向错（假设不成立）"


def _verdict_tail(v: float) -> str:
    """|均值| − |中位数| 的三档读法（阈值 TAIL_DOMINANCE_PP 预设，不因结果调整）。

    正值（>= 1pp）：少数大盈/大亏窗口把均值拉离中位数 → 中位数不反映全貌。
    负值（<= -1pp）：典型窗口比均值**更极端**，亏损不是少数极端值造成的。
    接近 0：均值与中位数幅度接近，分布大致对称。
    """
    if v >= TAIL_DOMINANCE_PP:
        return "尾部主导（少数窗口撑起均值）"
    if v <= -TAIL_DOMINANCE_PP:
        return "典型窗口主导（非尾部问题）"
    return "均值≈中位数（分布对称）"


def summarize(payload: dict, fee: str | None = None) -> dict:
    """按「策略 × 费率档」汇总分布形状。"""
    out: dict[str, dict] = {}
    strategies = sorted({r["strategy"] for r in payload.get("results", []) if "strategy" in r})
    for strat in strategies:
        rows = _rows(payload, strat)
        if fee:
            rows = [r for r in rows if r.get("fee_source") == fee]
        if not rows:
            continue

        lr = [r["window_stats"]["loss_window_ratio"] for r in rows]
        tail = [r["window_stats"]["abs_mean_minus_abs_median"] for r in rows]
        med = [r["median_ret"] for r in rows]
        nwin = [r["window_stats"]["n_windows_valid"] for r in rows]

        # 盈亏幅度比：|中位盈利| / |中位亏损|（>1 = 赢面幅度大于输面）
        ratios = []
        for r in rows:
            ws = r["window_stats"]
            ml, mg = ws.get("median_loss"), ws.get("median_gain")
            if ml is not None and mg is not None and abs(ml) > ZERO_TOL:
                ratios.append(abs(mg) / abs(ml))

        # 零中位数的标的单独看：它们的方向性由 loss_ratio 直接给出
        zero_syms = [r["symbol"] for r in rows if abs(r["median_ret"]) < ZERO_TOL]
        zero_lr = [
            r["window_stats"]["loss_window_ratio"]
            for r in rows
            if abs(r["median_ret"]) < ZERO_TOL
        ]

        out[strat] = {
            "n_symbols": len(rows),
            "n_windows_valid_median": int(st.median(nwin)),
            "n_windows_valid_range": [min(nwin), max(nwin)],
            # 判据 1
            "loss_ratio_median": round(st.median(lr), 4),
            "loss_ratio_p25": round(_pct(lr, 25), 4),
            "loss_ratio_p75": round(_pct(lr, 75), 4),
            "loss_ratio_verdict": _verdict_loss_ratio(st.median(lr)),
            "symbols_direction_wrong": sum(1 for x in lr if x > LOSS_RATIO_AMBIGUOUS_MAX),
            # 判据 2
            "tail_gap_median": round(st.median(tail), 6),
            "tail_verdict": _verdict_tail(st.median(tail)),
            "symbols_tail_dominated": sum(1 for x in tail if x >= TAIL_DOMINANCE_PP),
            "symbols_typical_dominated": sum(1 for x in tail if x <= -TAIL_DOMINANCE_PP),
            "symbols_symmetric": sum(
                1 for x in tail if -TAIL_DOMINANCE_PP < x < TAIL_DOMINANCE_PP
            ),
            # 幅度结构（描述用）
            "payoff_ratio_median": round(st.median(ratios), 3) if ratios else None,
            "zero_median_symbols": len(zero_syms),
            "zero_median_loss_ratio_median": round(st.median(zero_lr), 4) if zero_lr else None,
            # 描述性（禁止用于择优）
            "median_ret_descriptive_only": round(st.median(med), 6),
        }
    return out


def _pct(values: list[float], q: float) -> float:
    """线性插值分位数，避免额外依赖 numpy。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def to_markdown(payloads: dict[str, dict], design: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("# 窗口级收益分布形状（B12）\n")
    if design:
        lines.append(
            f"> 口径：`{design.get('design_version')}`｜OOS {design.get('min_oos_periods')} 日｜"
            f"min_trades={design.get('min_trades')}｜IS {design.get('min_train_periods')} 日\n"
        )
    lines.append(
        "> 判据**预先固定**：亏损窗口占比 <40% 方向性存在 / 40~50% 不明确 / >50% 方向错；"
        f"|均值|−|中位数| ≥ ±{TAIL_DOMINANCE_PP:.0%} 分别判为尾部主导 / 典型窗口主导。\n"
    )
    lines.append(
        "> 全部是**窗口级独立收益**（R12），不是账户连续收益。本表不排序、不择优。\n"
    )

    fee_labels = {
        "simulation_placeholder": "simulation（最低成本）",
        "broker_reference_common_2024": "broker_reference（常见券商）",
        "research_scenario_high_cost": "high_cost（高成本情景）",
    }

    for fee in FEE_ORDER:
        blocks = payloads.get(fee)
        if not blocks:
            continue
        lines.append(f"\n## {fee_labels.get(fee, fee)}\n")
        lines.append("| 策略 | 标的 | 亏损窗口占比(中位) | 判据1 | 方向错的标的 | |均值|−|中位数| | 判据2 | 尾/典/均(标的数) | 盈亏幅度比 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for name, b in blocks.items():
            pr = b["payoff_ratio_median"]
            pr_txt = "—" if pr is None else f"{pr:.2f}"
            lines.append(
                f"| {name} | {b['n_symbols']} | {b['loss_ratio_median']:.1%} | "
                f"**{b['loss_ratio_verdict']}** | {b['symbols_direction_wrong']}/{b['n_symbols']} | "
                f"{b['tail_gap_median']:+.4f} | {b['tail_verdict']} | "
                f"尾{b['symbols_tail_dominated']}/典{b['symbols_typical_dominated']}/"
                f"均{b['symbols_symmetric']} | {pr_txt} |"
            )

        lines.append("\n### 亏损窗口占比的分位（同一档内跨标的）\n")
        lines.append("| 策略 | p25 | 中位 | p75 | 零中位数标的数 | 零中位数标的的亏损占比(中位) |")
        lines.append("|---|---|---|---|---|---|")
        for name, b in blocks.items():
            zl = b["zero_median_loss_ratio_median"]
            zl_txt = "—" if zl is None else f"{zl:.1%}"
            lines.append(
                f"| {name} | {b['loss_ratio_p25']:.1%} | {b['loss_ratio_median']:.1%} | "
                f"{b['loss_ratio_p75']:.1%} | {b['zero_median_symbols']} | {zl_txt} |"
            )

    lines.append("\n## 读法\n")
    lines.append(
        "1. **判据 1 是主判据**。亏损窗口占比 >50% 意味着：多数 OOS 窗口是亏的，\n"
        "   中位数为负不是被少数极端值拖累，而是**信号方向本身有问题**。\n"
        "   这种情况下调参数（换网格、换阈值）都是无效动作。\n"
        "2. **判据 2 是形状判据**。|均值|−|中位数| 大说明少数大盈/大亏窗口主导均值，\n"
        "   中位数的代表性差，看单一中位数会失真。\n"
        "3. **盈亏幅度比 >1 不等于能赚钱**：它只说明赢的窗口比输的窗口幅度大，\n"
        "   若亏损窗口占比同时很高，频率会吃掉幅度优势。\n"
        "4. **零中位数标的要看它的亏损占比**：中位数为 0 不代表「不亏」，\n"
        "   只代表排序后落在中间的那个恰好是 0。\n"
    )
    lines.append(
        "\n> 本表不得用于挑选策略、参数或费率档（R2/R11）。\n"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="窗口级收益分布形状分析（B12）")
    ap.add_argument("--summary", required=True, help="step75_research_summary.json 路径")
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)

    payload = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    payloads: dict[str, dict] = {}
    for fee in FEE_ORDER:
        s = summarize(payload, fee)
        if s:
            payloads[fee] = s

    if not payloads:
        print("没有带 window_stats 的结果行 —— 需要用含窗口级输出的版本重跑。")
        return 1

    md = to_markdown(payloads, design=payload.get("design"))
    print(md)
    if args.out_md:
        Path(args.out_md).write_text(md, encoding="utf-8")
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(
                {
                    "design": payload.get("design"),
                    "criteria": {
                        "loss_ratio_directional_max": LOSS_RATIO_DIRECTIONAL_MAX,
                        "loss_ratio_ambiguous_max": LOSS_RATIO_AMBIGUOUS_MAX,
                        "tail_dominance_pp": TAIL_DOMINANCE_PP,
                        "note": "预设于看到数据之前，禁止事后调整",
                    },
                    "by_fee": payloads,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
