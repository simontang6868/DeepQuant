"""中国海油 vs 其余 7 只：择时有效性与选股可区分性。

**本脚本是预登记的机械执行器**，不是探索工具。
判据、参数、窗口、费率档全部来自 `docs/cnooc_prereg_2026-10-20.json`（先冻结后跑）。

它**不预测未来收益**，也不给出买卖建议（R3）。
它只回答三件可测量的事：

  J1  在这 8 只上，walk-forward 择时的样本外表现是否优于「同窗口买入持有」
  J2  8 只之间能否区分出「最好」（预期：不能，样本量太小）
  J3  这 8 只相关性多高 —— 若高，「选哪只」这个动作本身价值就有限

红线 R12：窗口级统计 ≠ 连续账户收益。本脚本输出的窗口收益**不得**被
表述为「策略能赚 X%」。
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import FEE_PRESET_HIGH_COST, FEE_PRESET_REFERENCE
from deepquant.market.rules import a_share_price_limit_rate, a_share_rules
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2

ROOT = Path(__file__).resolve().parent.parent
QDIR = ROOT / "data" / "qfq_mult" / "listed"

PREREG = ROOT / "docs" / "cnooc_prereg_2026-10-20.json"

# --- 冻结参数：逐项复制 data/step75/research.py，禁止在此调整（R2/R11）---
INITIAL_CASH = 100_000.0
MA_GRID = GridSearchConfig(
    fast_candidates=(3, 5, 10, 20),
    slow_candidates=(15, 25, 40, 60),
    min_train_periods=150,
    min_oos_periods=75,
    min_trades=2,
    max_combinations=50,
)

NAMES = {
    "600938.SH": "中国海油",
    "600968.SH": "海油发展",
    "601808.SH": "中海油服",
    "600583.SH": "海油工程",
    "600871.SH": "石化油服",
    "601699.SH": "潞安环能",
    "002165.SZ": "红宝丽",
    "601668.SH": "中国建筑",
}
OIL5 = ("600938.SH", "600968.SH", "601808.SH", "600583.SH", "600871.SH")


# ---------- 统计工具（环境无 scipy，自实现）----------

def sign_test_binom(diffs: list[float]) -> dict:
    """二项精确符号检验：择时 vs 买入持有的逐窗配对差。

    只统计非零差（差为 0 的窗口不构成任何方向的证据，剔除是标准做法）。
    """
    nz = [d for d in diffs if abs(d) > 1e-12]
    n = len(nz)
    if n == 0:
        return {"n_nonzero": 0, "n_positive": 0, "p_two_sided": None,
                "p_one_sided_positive": None, "verdict": "NO_DATA"}
    k = sum(1 for d in nz if d > 0)
    # P(X = i), X ~ Bin(n, 0.5)
    pmf = [math.comb(n, i) * 0.5 ** n for i in range(n + 1)]
    p_one_pos = sum(pmf[k:])                      # P(X >= k)：正差更多
    tail = min(k, n - k)
    p_two = min(1.0, 2.0 * sum(pmf[: tail + 1]))  # 双侧：对称尾部
    pos_ratio = k / n
    if pos_ratio > 0.5 and p_two < 0.05:
        verdict = "TIMING_ADDS_VALUE"
    elif pos_ratio < 0.5 and p_two < 0.05:
        verdict = "TIMING_DESTROYS_VALUE"
    else:
        verdict = "INCONCLUSIVE"
    return {"n_nonzero": n, "n_positive": k, "pos_ratio": round(pos_ratio, 4),
            "p_one_sided_positive": round(p_one_pos, 6),
            "p_two_sided": round(p_two, 6), "verdict": verdict}


def binom_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """二项检验双侧 p（对称尾部，截断到 1）。"""
    if n == 0:
        return 1.0
    pmf = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    tail = min(k, n - k)
    return min(1.0, 2.0 * sum(pmf[: tail + 1]))


def aggregate_by_stock(stocks: list[dict], field: str, better_if: str) -> dict:
    """以「票」为单位做符号检验，而不是以「窗口」为单位。

    为什么不能用窗口：8 只票在同一时间段的窗口**高度相关**（油气 5 只内部
    日收益相关 0.63），把 56 个窗口当独立样本会严重高估显著性。
    以票为单位（n=8）是更保守也更正确的做法 —— 每只票只贡献一个观测。
    """
    vals = []
    for s in stocks:
        v = s.get(field)
        if v is None:
            continue
        if better_if == "lower":
            vals.append(1 if v < 0 else 0)
        else:
            vals.append(1 if v > 0 else 0)
    n = len(vals)
    k = sum(vals)
    p = binom_two_sided(k, n)
    return {
        "unit": "stock",
        "n": n, "k_favorable": k,
        "ratio": round(k / n, 4) if n else None,
        "p_two_sided": round(p, 6),
        "significant": bool(p < 0.05),
        "note": "以票为单位（n=8），避免把同时间的高相关窗口当作独立样本",
    }


def bootstrap_median_ci(arr: np.ndarray, n_boot: int = 10_000,
                        seed: int = 20261020, ci: float = 0.95) -> dict:
    """中位数的 bootstrap 置信区间（seed 固定，可复现）。"""
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return {"median": None, "lo": None, "hi": None, "n": 0}
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(a, size=(n_boot, a.size), replace=True), axis=1)
    lo, hi = np.percentile(meds, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {"median": round(float(np.median(a)), 6),
            "lo": round(float(lo), 6), "hi": round(float(hi), 6), "n": int(a.size)}


def ci_disjoint(a: dict, b: dict) -> bool:
    """两个 bootstrap CI 是否完全不重叠（严格不重叠，不含端点相切）。"""
    if None in (a.get("lo"), a.get("hi"), b.get("lo"), b.get("hi")):
        return False
    return a["lo"] > b["hi"] or b["lo"] > a["hi"]


# ---------- 数据 ----------

def load(code: str, start: str, end: str) -> pd.DataFrame:
    p = QDIR / f"{code}.qfq.csv"
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    out = df.set_index("date")[["open", "high", "low", "close", "volume"]].copy()
    out.index.name = None
    return out


def bh_window(df: pd.DataFrame, d0: pd.Timestamp, d1: pd.Timestamp, preset) -> dict | None:
    """窗口 [d0, d1] 的买入持有**净**收益 + 窗口内最大回撤（与策略端同费率口径）。

    不加费率会让买入持有凭空占便宜 —— 择时交易次数多、付的费也多，
    成本正是两者差异的主要来源之一，必须两边都扣。
    """
    seg = df.loc[(df.index >= d0) & (df.index <= d1), "close"].dropna()
    if len(seg) < 2:
        return None
    # 用「价格比」而非「已减 1 的收益率」，否则下面再减一次 1 会得到 ≈-1
    px_ratio = float(seg.iloc[-1]) / float(seg.iloc[0])
    comm = max(preset.commission_rate, preset.min_commission / INITIAL_CASH)
    adj = ((1 - preset.slippage_sell) / (1 + preset.slippage_buy)
           * (1 - comm) ** 2 * (1 - preset.transfer_fee_rate) ** 2
           * (1 - preset.stamp_duty_rate))
    peak = seg.cummax()
    # 符号必须与策略端 max_drawdown_ratio 一致（正幅度）。
    # 策略端给的是 0.074 表示「回撤 7.4%」，这里若返回负值会让差值凭空大一圈。
    mdd = abs(float((seg / peak - 1.0).min()))
    return {"ret": px_ratio * adj - 1.0, "mdd": mdd}


# ---------- 主流程 ----------

def run_one(code: str, start: str, end: str, preset) -> dict:
    df = load(code, start, end)
    rec: dict = {"code": code, "name": NAMES.get(code, ""), "n_days": int(len(df))}
    if len(df) < 260:
        rec["status"] = "too_short"
        return rec

    rules = a_share_rules(price_limit_rate=a_share_price_limit_rate(code))
    cfg = preset.apply(BacktestConfig(symbol=code, initial_cash=INITIAL_CASH, market_rules=rules))

    specs, wf, _summary = run_walkforward_v2(
        df, cfg,
        strategy_factory=lambda p: MaCrossStrategy(
            fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"])),
        grid_config=MA_GRID,
    )

    # 只取口径一致的窗口（与 research.py window_return_stats 同一套过滤条件）
    valid = [r for r in wf
             if r.selection_success and r.oos_success
             and r.final_equity is not None and r.oos_is_complete
             and r.cumulative_return is not None and np.isfinite(r.cumulative_return)]

    pairs = []
    for r in valid:
        bh = bh_window(df, r.oos_start, r.oos_end, preset)
        if bh is None:
            continue
        pairs.append({
            "window_index": r.window_index,
            "oos_start": str(r.oos_start.date()), "oos_end": str(r.oos_end.date()),
            "timing": round(float(r.cumulative_return), 6),
            "buy_hold": round(float(bh["ret"]), 6),
            "diff": round(float(r.cumulative_return) - float(bh["ret"]), 6),
            "timing_mdd": round(float(r.max_drawdown_ratio), 6) if r.max_drawdown_ratio is not None else None,
            "buy_hold_mdd": round(float(bh["mdd"]), 6),
            "mdd_diff": (round(float(r.max_drawdown_ratio) - float(bh["mdd"]), 6)
                         if r.max_drawdown_ratio is not None else None),
            "trades": r.trade_count,
            "fees": round(float(r.total_fees), 2) if r.total_fees is not None else None,
        })

    rec["status"] = "ok" if pairs else "no_valid_window"
    rec["n_windows_requested"] = len(specs)
    rec["n_windows_valid"] = len(pairs)
    rec["pairs"] = pairs

    t = np.array([p["timing"] for p in pairs], dtype=float)
    b = np.array([p["buy_hold"] for p in pairs], dtype=float)
    d = np.array([p["diff"] for p in pairs], dtype=float)

    rec["timing_median"] = round(float(np.median(t)), 6) if t.size else None
    rec["buyhold_median"] = round(float(np.median(b)), 6) if b.size else None
    rec["diff_median"] = round(float(np.median(d)), 6) if d.size else None
    rec["mean_trades_per_window"] = round(float(np.mean([p["trades"] or 0 for p in pairs])), 2) if pairs else None
    rec["total_fees"] = round(float(sum(p["fees"] or 0 for p in pairs)), 2)
    rec["J1"] = sign_test_binom(d.tolist()) if d.size else {"verdict": "NO_DATA"}
    rec["ci_timing"] = bootstrap_median_ci(t)
    rec["ci_buyhold"] = bootstrap_median_ci(b)

    # --- J4（探索性补充，非预登记主判据）：择时是否降低窗口内回撤 ---
    # mdd_diff < 0 表示择时回撤更小。符号检验复用同一函数，但方向反了，
    # 因此这里显式按「负差比例」判定，不复用 J1 的 verdict 字段。
    md = np.array([p["mdd_diff"] for p in pairs if p["mdd_diff"] is not None], dtype=float)
    if md.size:
        nz = [x for x in md.tolist() if abs(x) > 1e-12]
        k_neg = sum(1 for x in nz if x < 0)
        n = len(nz)
        pmf = [math.comb(n, i) * 0.5 ** n for i in range(n + 1)] if n else []
        p_two = min(1.0, 2.0 * sum(pmf[: min(k_neg, n - k_neg) + 1])) if n else None
        rec["j4_mdd_diff_median"] = round(float(np.median(md)), 6)  # 供「以票为单位」聚合
        rec["J4_drawdown"] = {
            "n_nonzero": n, "n_timing_lower": k_neg,
            "lower_ratio": round(k_neg / n, 4) if n else None,
            "p_two_sided": round(p_two, 6) if p_two is not None else None,
            "mdd_diff_median": round(float(np.median(md)), 6),
            "timing_mdd_median": round(float(np.median(
                [p["timing_mdd"] for p in pairs if p["timing_mdd"] is not None])), 6),
            "buyhold_mdd_median": round(float(np.median([p["buy_hold_mdd"] for p in pairs])), 6),
            "verdict": ("TIMING_LOWERS_DRAWDOWN" if (n and k_neg / n > 0.5 and p_two < 0.05)
                        else "INCONCLUSIVE"),
        }
    return rec


def correlation_matrix(codes, start, end) -> dict:
    rets = {}
    for c in codes:
        df = load(c, start, end)
        r = df["close"].pct_change().dropna()
        rets[c] = r
    # 只保留所有票都有数据的交易日
    idx = None
    for c in codes:
        idx = rets[c].index if idx is None else idx.intersection(rets[c].index)
    mat = pd.DataFrame({c: rets[c].loc[idx] for c in codes})
    corr = mat.corr()
    labels = [f"{NAMES.get(c, c)}({c[:6]})" for c in codes]
    off = corr.values[~np.eye(len(codes), dtype=bool)]
    oil_idx = [codes.index(c) for c in OIL5]
    sub = corr.values[np.ix_(oil_idx, oil_idx)]
    off_oil = sub[~np.eye(len(oil_idx), dtype=bool)]
    return {
        "codes": list(codes),
        "labels": labels,
        "matrix": [[round(float(v), 4) for v in row] for row in corr.values],
        "mean_corr_all": round(float(np.mean(off)), 4),
        "mean_corr_oil5": round(float(np.mean(off_oil)), 4),
        "n_days": int(len(idx)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-10-16")
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--out", default=str(ROOT / "docs" / "CNOOC_COMPARE_2026-10-20.json"))
    ap.add_argument("--fees", default="reference", choices=["reference", "high_cost"],
                    help="费率档：reference=主口径，high_cost=压力测试（探索性）")
    args = ap.parse_args()

    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    codes = prereg["universe"]["codes"]
    preset = FEE_PRESET_REFERENCE if args.fees == "reference" else FEE_PRESET_HIGH_COST

    print(f"[窗口] {args.start} ~ {args.end}")
    print(f"[费率] {args.fees}")

    corr = correlation_matrix(codes, args.start, args.end)
    print(f"\n[J3] 全池平均相关 = {corr['mean_corr_all']:.4f}  油气5只内部 = {corr['mean_corr_oil5']:.4f}"
          f"  (n={corr['n_days']} 天)")
    j3 = "SELECTION_LOW_VALUE" if corr["mean_corr_all"] >= 0.5 else "SELECTION_MATERIALS"
    print(f"     判定: {j3}")

    stocks = []
    for c in codes:
        r = run_one(c, args.start, args.end, preset)
        stocks.append(r)
        if r["status"] == "ok":
            print(f"  {r['name']}({c[:6]}): 窗口 {r['n_windows_valid']}/{r['n_windows_requested']} "
                  f"择时中位 {r['timing_median']:+.4f} 买入持有中位 {r['buyhold_median']:+.4f} "
                  f"差 {r['diff_median']:+.4f} → {r['J1']['verdict']}")
        else:
            print(f"  {NAMES.get(c,c)}({c[:6]}): {r['status']}")

    # --- J2：中国海油 vs 其余 7 只，CI 是否不重叠 ---
    cnooc = next((s for s in stocks if s["code"] == "600938.SH"), None)
    j2_pairs = []
    for s in stocks:
        if s["code"] == "600938.SH" or s["status"] != "ok":
            continue
        disjoint = ci_disjoint(cnooc["ci_timing"], s["ci_timing"])
        j2_pairs.append({
            "vs": s["code"], "vs_name": s["name"],
            "cnooc_better": bool(disjoint and cnooc["ci_timing"]["lo"] > s["ci_timing"]["hi"]),
            "disjoint": disjoint,
            "verdict": "DISTINGUISHABLE" if disjoint else "NOT_DISTINGUISHABLE",
            "cnooc_ci": cnooc["ci_timing"], "vs_ci": s["ci_timing"],
        })
    all_disjoint = bool(j2_pairs) and all(p["disjoint"] for p in j2_pairs)
    cnooc_best = bool(j2_pairs) and all(p["cnooc_better"] for p in j2_pairs)
    j2 = "BEST_CLEAR" if cnooc_best else ("ALL_DISJOINT" if all_disjoint else "UNDISTINGUISHABLE")

    print(f"\n[J2] 中国海油 vs 其余 7 只 → {j2}")
    for p in j2_pairs:
        print(f"     vs {p['vs_name']}: {p['verdict']}  "
              f"中海[{p['cnooc_ci']['lo']:+.4f},{p['cnooc_ci']['hi']:+.4f}] "
              f"对方[{p['vs_ci']['lo']:+.4f},{p['vs_ci']['hi']:+.4f}]")

    j1_counts = {}
    for s in stocks:
        if s["status"] == "ok":
            v = s["J1"]["verdict"]
            j1_counts[v] = j1_counts.get(v, 0) + 1

    agg_ret = aggregate_by_stock(stocks, "diff_median", "higher")
    agg_mdd = aggregate_by_stock(stocks, "j4_mdd_diff_median", "lower")
    print(f"\n[聚合·收益] 择时中位跑赢买入持有的票数 {agg_ret['k_favorable']}/{agg_ret['n']} "
          f"p={agg_ret['p_two_sided']} 显著={agg_ret['significant']}")
    print(f"[聚合·回撤] 择时回撤更低的票数        {agg_mdd['k_favorable']}/{agg_mdd['n']} "
          f"p={agg_mdd['p_two_sided']} 显著={agg_mdd['significant']}")

    out = {
        "aggregate_by_stock": {
            "return_diff": agg_ret,
            "drawdown_diff": agg_mdd,
            "reading": (
                "收益维度与回撤维度给出**相反的画面**：若前者不显著而后者显著，"
                "说明这套择时的作用更接近「控回撤」而非「提收益」。"
            ),
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prereg_sha256": (PREREG.parent / "cnooc_prereg_2026-10-20.sha256").read_text(encoding="utf-8").strip(),
        "window": {"start": args.start, "end": args.end},
        "fees_primary": prereg["fees"]["primary"],
        "J3_correlation": corr,
        "J3_verdict": j3,
        "stocks": stocks,
        "J2_pairs": j2_pairs,
        "J2_verdict": j2,
        "J1_verdict_counts": j1_counts,
        "disclaimer": (
            "本研究不预测未来收益，不构成任何买卖建议。"
            "所有数字均为历史样本外窗口的描述性统计，窗口级统计不等于连续账户收益（R12）。"
        ),
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[J1] 逐票判定分布: {j1_counts}")
    print(f"[OUT] {args.out}")


if __name__ == "__main__":
    main()
