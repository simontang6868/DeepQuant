"""设计探针报告：零中位数是「网格太慢」还是「OOS 太短」。

背景
----
扩样 48 只后仍有 33.3%（ma）/ 29.2%（mr）的标的中位数恰为 0。成因已定位到**事件稀疏**：
slow=60 的均线在 50 天 OOS 里平均只交叉 1.38 次。但「网格太慢」和「OOS 太短」这两个解释
从未被分开验证 —— 不分开就不知道该动哪个，也没有任何依据决定下一步。

变体（单费率 `simulation_placeholder`，其余与正式研究完全一致）
--------------------------------------------------------------
    v0  OOS=50, 慢线≤60   ← 基线，即正式研究口径
    v1  OOS=75, 慢线≤60   ← 只加长 OOS
    v2  OOS=50, 慢线≤40   ← 只压网格上限
    v3  OOS=75, 慢线≤40   ← 两者同时

**判据预先固定（这是本脚本存在的意义）**
--------------------------------------
只看两个量：**零中位数率** 与 **事件密度**（每个参与窗口的平均成交次数）。
收益类指标一律列在最后、仅供描述，**不得**按它挑变体 —— 否则就是用 OOS 收益选设计，
直接踩 R2（不用 OOS 选参）与 R11（不按 OOS 收益择优）。

同时必须接受一个代价：加长 OOS 会减少窗口数（8 年数据下 OOS 50→75 时窗口 35→23）。
窗口数是**独立样本量**，减少它会让"零比例"的置信区间变宽 —— 报告里一并列出。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

ZERO_TOL = 1e-12


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(payload: dict, strategy: str) -> list[dict]:
    out = []
    for r in payload.get("results", []):
        if "error" in r or r.get("positive_ratio") is None:
            continue
        if r["strategy"] != strategy:
            continue
        if r["fee_source"] != "simulation_placeholder":
            continue
        out.append(r)
    return out


def summarize(payload: dict) -> dict:
    """每个策略一组结构指标。

    **口径警告（本轮踩过）**：`positive_ratio` 是**盈利窗口占比**
    （return > 0 的窗口 / 有有效指标的窗口），**不是**交易参与率。
    真正的参与率要窗口级的 `trade_count > 0` 计数，见
    `design_audit.summarize_participation`；summary 里没有这个字段，
    所以这里用 `total_trades / n_windows` 作为**事件密度**的替代口径
    （分母含零交易窗口，不会被稀释成"每次参与的平均笔数"那种假指标）。
    """
    out = {}
    strategies = sorted({r["strategy"] for r in payload.get("results", []) if "strategy" in r})
    for strat in strategies:
        rows = _rows(payload, strat)
        if not rows:
            continue
        meds = [r["median_ret"] for r in rows]
        zeros = sum(1 for m in meds if abs(m) < ZERO_TOL)
        trades_per_window = []
        for r in rows:
            if r.get("n_windows"):
                trades_per_window.append((r.get("total_trades") or 0) / r["n_windows"])
        out[strat] = {
            "n_symbols": len(rows),
            "n_windows_median": st.median([r["n_windows"] for r in rows]),
            "n_windows_min": min(r["n_windows"] for r in rows),
            "zero_median_rate": round(zeros / len(rows), 4),
            # 方向性指标，不是参与率
            "positive_window_ratio_median": round(st.median([r["positive_ratio"] for r in rows]), 4),
            # 主判据：事件密度（每窗口成交笔数，分母含零交易窗口）
            "trades_per_window": round(st.median(trades_per_window), 3) if trades_per_window else None,
            # 仅供描述，禁止择优
            "median_ret_descriptive_only": round(st.median(meds), 6),
            "median_mdd_descriptive_only": round(st.median([r["median_mdd"] for r in rows]), 6),
        }
    return out


def to_markdown(variants: dict[str, dict]) -> str:
    labels = {
        "v0": "v0 基线 OOS50/慢线≤60",
        "v1": "v1 只加长 OOS75",
        "v2": "v2 只压慢线≤40",
        "v3": "v3 两者都改 OOS75/≤40",
    }
    lines = ["## 主判据（只看这两个，禁止看收益选变体）\n"]

    for strat in ("ma_cross", "mean_reversion"):
        lines.append(f"### {strat}\n")
        lines.append("| 变体 | 标的 | 窗口数(中位) | 零中位数率 | 盈利窗口占比(中位) | 每窗口成交笔数 |")
        lines.append("|---|---|---|---|---|---|")
        for key, summary in variants.items():
            s = summary.get(strat)
            if not s:
                continue
            lines.append(
                f"| {labels.get(key, key)} | {s['n_symbols']} | {int(s['n_windows_median'])}"
                f"（最少 {s['n_windows_min']}） | {s['zero_median_rate']:.1%} | "
                f"{s['positive_window_ratio_median']:.1%} | {s['trades_per_window']} |"
            )
        lines.append("")

    lines.append("## 描述性指标（**不得**用于挑变体）\n")
    lines.append("| 变体 | 策略 | 收益中位数 | 回撤中位数 |")
    lines.append("|---|---|---|---|")
    for key, summary in variants.items():
        for strat, s in summary.items():
            lines.append(
                f"| {labels.get(key, key)} | {strat} | "
                f"{s['median_ret_descriptive_only']:+.4f} | {s['median_mdd_descriptive_only']:.4f} |"
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="设计探针报告（主判据：零中位数率 + 事件密度）")
    ap.add_argument("--v0", required=True, help="基线 summary json（正式研究口径）")
    ap.add_argument("--v1", help="OOS 加长")
    ap.add_argument("--v2", help="慢线压缩")
    ap.add_argument("--v3", help="两者都改")
    ap.add_argument("--out-md")
    ap.add_argument("--out-json")
    args = ap.parse_args(argv)

    variants, designs = {}, {}
    for key in ("v0", "v1", "v2", "v3"):
        path = getattr(args, key)
        if path:
            payload = _load(path)
            variants[key] = summarize(payload)
            designs[key] = payload.get("design")

    md = to_markdown(variants)
    print(md)
    payload = {
        "variants": variants,
        "designs": designs,
        "criterion": "零中位数率 + 事件密度（禁用收益择优，R2/R11）",
        "caveat": "加长 OOS 会减少窗口数（独立样本量下降），零比例置信区间随之变宽。",
    }
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(md + "\n", encoding="utf-8")
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
