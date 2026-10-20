#!/usr/bin/env python
"""Gate 3 运行脚本：短期反转（月度）在 2007-01 ~ 2015-12 长样本上的复现检验。

预登记：docs/GATE3_REVERSAL_LONGSAMPLE_PREREG_2026-10-20.md
冻结 sha256：9b7235d77689f7d4ee59e3485439d9c126e45384e67bf82d0e8b55820956aeda

## 纪律

- 参数**硬编码**，不提供命令行覆盖（预登记 §8 第 1 条）。要改设计 → 新预登记、重新冻结、重新编号。
- 启动时校验预登记 sha256；不一致**拒绝运行**（防"跑完回改预登记"）。
- 主判定只看 §4 判据；分年/敏感性/成本档**只作描述**，不得推翻主判定。

## 本脚本额外做一件事：Gate 2 的数据修复回归（预登记 §7）

D-1（249 只退市股 ADJ 未复权）修复后，Gate 2 的 2016-2026 结果必然变化。
按路线图「修 bug 后的重跑不是新实验」，用**同一批参数 + Gate 2 原剔除清单**重跑一次并并列报告。
该结果**不改变** Gate 2 的既有结论标签，也**不影响**本实验主判定。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import (  # noqa: E402
    MIN_VALID_RATIO,
    load_close_matrix,
    month_end_positions,
    period_return,
    scores_at,
)

# ==================== 冻结参数（逐字照抄预登记 §2/§3/§4） ====================

PREREG_PATH = "docs/GATE3_REVERSAL_LONGSAMPLE_PREREG_2026-10-20.md"
PREREG_SHA256 = "9b7235d77689f7d4ee59e3485439d9c126e45384e67bf82d0e8b55820956aeda"

POOL_DIR = "data/qfq_rebuilt_broad_long"

# §2.1 研究池：549 - 17（R1 已知缺陷 / R2 窗口内超限 / R3 覆盖不足 / R4 判据不过）
EXCLUDED_SYMBOLS = [
    # R1 已知缺陷
    "600515.SH", "000725.SZ",
    # R2 窗口内（2007-2015）涨跌停违规
    "000301.SZ", "000425.SZ", "000776.SZ", "600066.SH", "600115.SH", "600660.SH",
    "000760.SZ", "600077.SH", "600213.SH", "600225.SH", "600277.SH", "600401.SH", "600705.SH",
    # R3 窗口内覆盖率 < 60%
    "000038.SZ",
    # R4 判据无法通过
    "900956.SH",
]

# §2 主检验窗口：信号日 2007-01-31 ~ 2015-12-31，持有至次月末
#   为让 2015-12-31 的信号也能取到收益，月末位置取到 2016-01，再按持有月过滤。
START = "2007-01-01"
END_SIGNAL = "2015-12-31"
END_HOLD = "2016-02-29"

LOOKBACK = 21
SKIP = 1
K_MAIN = 110
DIRECTION = "reversal"
REBALANCE = "monthly"

# §4 判定阈值
SUCCESS_T = 1.645
SUCCESS_MIN_N = 100
MDE_MAX_PP = 0.0142          # 1.42pp：文献效应量，超过它说明"设计检不出"
Z_MDE = 2.8016               # z(1-α/2, α=0.05) + z(power=80%)，与 Gate 2 同一约定

# §4 补充分组（不参与判定）
SENS_K = [50, 110, 200]
SENS_SKIP = [0, 1, 5]
COST_TIERS = [0.001, 0.003]

# §7 Gate 2 数据修复回归：窗口与剔除清单均沿用 Gate 2 v2.0
G2_START, G2_END = "2016-05-01", "2026-10-31"
G2_EXCLUDED = ["600515.SH"]
G2_RECORDED = {"mean_d_pp": -0.3846, "t_stat": -1.6415, "n": 125, "verdict": "FAILURE"}

OUT_DIR = os.path.join("gate3_out", PREREG_SHA256[:12])


def verify_prereg() -> None:
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = hashlib.sha256(open(PREREG_PATH, "rb").read()).hexdigest()
    if got != PREREG_SHA256:
        raise SystemExit(
            "预登记 sha256 与冻结值不符，拒绝运行。\n"
            f"  期望 {PREREG_SHA256}\n  实际 {got}\n"
            "若确需改设计，请新开预登记并重新冻结（预登记 §8 第 4 条）。"
        )
    print(f"[gate3] 预登记校验 OK  {got[:16]}")


def run_combo(close_df, close_ffill, calendar, positions, lookback, skip, k):
    min_valid = math.ceil(lookback * MIN_VALID_RATIO)
    rows = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sc = scores_at(pos, close_df, lookback=lookback, skip=skip, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close_ffill)
        pool = sc.index.intersection(rets.dropna().index)
        kk = min(k, len(pool) // 2)
        if kk < 1:
            continue
        r_pool = rets.loc[pool]
        ranked = sc.loc[pool].sort_values(ascending=(DIRECTION == "reversal"))
        r_long = float(r_pool.loc[ranked.index[:kk]].mean())
        r_short = float(r_pool.loc[ranked.index[-kk:]].mean())
        rows.append(
            {
                "month": str(calendar[nxt].date()),
                "signal_date": str(calendar[pos].date()),
                "n_pool": int(len(pool)),
                "k_used": int(kk),
                "k_frac": round(kk / len(pool), 4),
                "r_long": r_long,
                "r_short": r_short,
                "d": 0.5 * (r_long - r_short),
            }
        )
    return rows


def stats_of(d):
    n = len(d)
    if n < 2:
        return {"n": n}
    mean = sum(d) / n
    sd = statistics.stdev(d)
    se = sd / math.sqrt(n)
    t = mean / se if se > 0 else float("nan")
    p_one = 0.5 * (1 - math.erf(t / math.sqrt(2))) if t == t else float("nan")
    return {
        "n": n,
        "mean_d": mean,
        "sd_d": sd,
        "se": se,
        "t_stat": t,
        "p_one_sided": p_one,
        "mde_pp": Z_MDE * se,
        "win_ratio": sum(1 for x in d if x > 0) / n,
        "min_d": min(d),
        "max_d": max(d),
    }


def judge(st):
    """预登记 §4 的机械判定。顺序不可调换：先判"检不出"，再判成败。"""
    n, mde = st["n"], st["mde_pp"]
    if n < SUCCESS_MIN_N or mde > MDE_MAX_PP:
        reasons = []
        if n < SUCCESS_MIN_N:
            reasons.append(f"n={n} < {SUCCESS_MIN_N}")
        if mde > MDE_MAX_PP:
            reasons.append(f"MDE={mde*100:.3f}pp > {MDE_MAX_PP*100:.2f}pp")
        return {"verdict": "INCONCLUSIVE", "label": "证据不足", "rule_hits": reasons}
    if st["mean_d"] > 0 and st["t_stat"] >= SUCCESS_T:
        return {
            "verdict": "SUCCESS",
            "label": "通过",
            "rule_hits": [
                f"n={n} >= {SUCCESS_MIN_N}",
                f"MDE={mde*100:.3f}pp <= {MDE_MAX_PP*100:.2f}pp",
                f"mean(d)={st['mean_d']:.6f} > 0",
                f"t={st['t_stat']:.4f} >= {SUCCESS_T}",
            ],
        }
    hits = []
    if st["mean_d"] <= 0:
        hits.append(f"mean(d)={st['mean_d']:.6f} <= 0")
    if st["t_stat"] < SUCCESS_T:
        hits.append(f"t={st['t_stat']:.4f} < {SUCCESS_T}")
    hits.append(f"n={n}, MDE={mde*100:.3f}pp（均满足前提）")
    return {"verdict": "FAILURE", "label": "失败", "rule_hits": hits}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def yearly(rows):
    by = {}
    for r in rows:
        by.setdefault(r["signal_date"][:4], []).append(r["d"])
    out = {}
    for y in sorted(by):
        v = by[y]
        out[y] = {
            "n": len(v),
            "mean_d_pp": round(sum(v) / len(v) * 100, 4),
            "win_ratio": round(sum(1 for x in v if x > 0) / len(v), 4),
        }
    return out


def main() -> int:
    verify_prereg()

    close_all, _ = load_close_matrix(POOL_DIR)
    n_all = close_all.shape[1]
    drop = [c for c in EXCLUDED_SYMBOLS if c in close_all.columns]
    close_df = close_all.drop(columns=drop)
    calendar = close_df.index
    close_ffill = close_df.ffill()
    print(f"[gate3] 目录 {n_all} 只 | 按 §2.1 剔除 {len(drop)} 只 | 研究池 {close_df.shape[1]} 只")
    if len(drop) != len(EXCLUDED_SYMBOLS):
        print(f"[gate3] 注意：清单里有 {len(EXCLUDED_SYMBOLS)-len(drop)} 只在目录中不存在")

    positions = month_end_positions(calendar, START, END_HOLD)
    # 过滤按 **signal_date**（预登记 §2：信号日 2007-01-31 ~ 2015-12-31）。
    # 首次运行误按持有月末过滤，把 2015-12-31 的信号（持有到 2016-01）丢掉了 → n=107 而非 108。
    # 这是执行偏差，不是设计变更；修正后与预登记的 n=108 一致。
    rows = [r for r in run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, SKIP, K_MAIN)
            if r["signal_date"] <= END_SIGNAL]
    d = [r["d"] for r in rows]
    st = stats_of(d)
    verdict = judge(st)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly_paired.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("month,signal_date,n_pool,k_used,k_frac,r_long,r_short,d\n")
        for r in rows:
            f.write(f"{r['month']},{r['signal_date']},{r['n_pool']},{r['k_used']},{r['k_frac']},"
                    f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f}\n")

    print()
    print("=== 主检验（2007-01 ~ 2015-12，预登记 §4 判据）===")
    print(f"  观测数 n     = {st['n']}")
    print(f"  mean(d)      = {st['mean_d']*100:+.4f} pp/月")
    print(f"  sd(d)        = {st['sd_d']*100:.4f} pp/月")
    print(f"  se           = {st['se']*100:.4f} pp")
    print(f"  t            = {st['t_stat']:+.4f}   (单侧 5% 临界 {SUCCESS_T})")
    print(f"  p(单侧)      = {st['p_one_sided']:.6f}")
    print(f"  MDE          = {st['mde_pp']*100:.4f} pp  (上限 {MDE_MAX_PP*100:.2f}pp)")
    print(f"  胜率         = {st['win_ratio']*100:.2f}%")
    print(f"  区间         = [{st['min_d']*100:+.2f}, {st['max_d']*100:+.2f}] pp")
    kf = [r["k_frac"] for r in rows]
    npool = [r["n_pool"] for r in rows]
    print(f"  k/n_pool     = 中位 {statistics.median(kf)*100:.1f}%  [{min(kf)*100:.1f}%, {max(kf)*100:.1f}%]")
    print(f"  n_pool       = 中位 {statistics.median(npool):.0f}  [{min(npool)}, {max(npool)}]")
    print()
    print(f"  >>> 判定：{verdict['verdict']}（{verdict['label']}）")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    print()

    # ---------- 分年（描述性） ----------
    y = yearly(rows)
    print("=== 分年度（描述性，不参与判定）===")
    for yy, v in y.items():
        print(f"  {yy}: n={v['n']:>2}  mean={v['mean_d_pp']:+7.3f}pp  胜率={v['win_ratio']*100:5.1f}%")
    print()

    # ---------- 2005-2006 对照（股改污染，不入判定） ----------
    ctrl_rows = []
    pos2 = month_end_positions(calendar, "2005-01-01", "2006-12-31")
    ctrl_rows = run_combo(close_df, close_ffill, calendar, pos2, LOOKBACK, SKIP, K_MAIN)
    ctrl = stats_of([r["d"] for r in ctrl_rows]) if ctrl_rows else {"n": 0}
    print("=== 2005-2006 对照（预登记 §0.1 D-2：股改对价未登记，**不入判定**）===")
    if ctrl["n"] >= 2:
        print(f"  n={ctrl['n']}  mean={ctrl['mean_d']*100:+.4f}pp  t={ctrl['t_stat']:+.3f}  "
              f"sd={ctrl['sd_d']*100:.3f}pp  （仅展示污染量级，不作任何结论）")
    print()

    # ---------- 敏感性（描述性） ----------
    sens = []
    print("=== 敏感性（描述性，不参与判定）===")
    for k in SENS_K:
        rr = run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, SKIP, k)
        rr = [x for x in rr if x["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "k", "value": k, **{k2: s.get(k2) for k2 in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  k={k:>4}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    for sk in SENS_SKIP:
        rr = run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, sk, K_MAIN)
        rr = [x for x in rr if x["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "skip", "value": sk, **{k2: s.get(k2) for k2 in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  skip={sk}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    print()

    with open(os.path.join(OUT_DIR, "sensitivity.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("param,value,n,mean_d,t_stat,sd_d,mde_pp\n")
        for r in sens:
            f.write(f"{r['param']},{r['value']},{r['n']},{r['mean_d']:.10f},{r['t_stat']:.6f},"
                    f"{r['sd_d']:.10f},{r['mde_pp']:.10f}\n")

    # ---------- 成本档（描述性） ----------
    print("=== 成本档（描述性；月度全换手，双边=2c）===")
    cost = {}
    for c in COST_TIERS:
        s = stats_of([x - 2 * c for x in d])
        cost[f"one_way_{c*100:.1f}pct"] = {"mean_net_pp": round(s["mean_d"] * 100, 4), "t_net": round(s["t_stat"], 4)}
        print(f"  单边 {c*100:.1f}%: mean_net={s['mean_d']*100:+7.4f}pp  t_net={s['t_stat']:+.3f}")
    print()

    # ---------- §7 Gate 2 数据修复回归 ----------
    print("=== §7 Gate 2（2016-2026）数据修复回归：同参数 + 原剔除清单 ===")
    g2_df = close_all.drop(columns=[c for c in G2_EXCLUDED if c in close_all.columns])
    g2_ffill = g2_df.ffill()
    g2_pos = month_end_positions(g2_df.index, G2_START, G2_END)
    g2_rows = run_combo(g2_df, g2_ffill, g2_df.index, g2_pos, LOOKBACK, SKIP, K_MAIN)
    g2_st = stats_of([r["d"] for r in g2_rows])
    g2_v = judge(g2_st)
    print(f"  池 {g2_df.shape[1]} 只 | n={g2_st['n']} | mean={g2_st['mean_d']*100:+.4f}pp | "
          f"t={g2_st['t_stat']:+.4f} | sd={g2_st['sd_d']*100:.3f}pp | 判定={g2_v['verdict']}")
    print(f"  Gate 2 记录值: mean={G2_RECORDED['mean_d_pp']:+.4f}pp | t={G2_RECORDED['t_stat']:+.4f} | "
          f"n={G2_RECORDED['n']} | {G2_RECORDED['verdict']}")
    print(f"  差异: Δmean={((g2_st['mean_d']*100)-G2_RECORDED['mean_d_pp']):+.4f}pp | "
          f"Δt={(g2_st['t_stat']-G2_RECORDED['t_stat']):+.4f}")
    print()
    with open(os.path.join(OUT_DIR, "gate2_rerun_2016_2026.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("month,signal_date,n_pool,k_used,k_frac,r_long,r_short,d\n")
        for r in g2_rows:
            f.write(f"{r['month']},{r['signal_date']},{r['n_pool']},{r['k_used']},{r['k_frac']},"
                    f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f}\n")

    # ---------- summary ----------
    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    summary = {
        "prereg_version": "gate3-reversal-longsample-v3.0",
        "prereg_sha256": PREREG_SHA256,
        "pool": {"dir": POOL_DIR, "n_in_dir": int(n_all), "n_excluded": len(drop), "n_used": int(close_df.shape[1])},
        "excluded": drop,
        "window": [START, END_SIGNAL],
        "params": {"lookback": LOOKBACK, "skip": SKIP, "k": K_MAIN, "direction": DIRECTION,
                   "rebalance": REBALANCE, "min_valid_ratio": MIN_VALID_RATIO},
        "main": clean(st),
        "judgement": verdict,
        "k_frac_median": round(statistics.median(kf), 4),
        "n_pool_median": statistics.median(npool),
        "yearly_descriptive": y,
        "control_2005_2006_descriptive": clean(ctrl) if ctrl.get("n", 0) >= 2 else {"n": ctrl.get("n")},
        "cost_descriptive": cost,
        "sensitivity_descriptive": [
            {k: (round(v, 10) if isinstance(v, float) else v) for k, v in r.items()} for r in sens
        ],
        "gate2_rerun_descriptive": {
            "pool_n": int(g2_df.shape[1]),
            "stats": clean(g2_st),
            "verdict_by_gate3_rule": g2_v,
            "gate2_recorded": G2_RECORDED,
            "note": "数据修复后的重跑，属路线图『修 bug 后重跑不是新实验』；不改变 Gate 2 既有结论标签。",
        },
        "note": "分年/敏感性/成本档/2005-2006 对照均为描述性，不参与判定（预登记 §4）。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["monthly_paired.csv", "sensitivity.csv", "gate2_rerun_2016_2026.csv", "summary.json"]
    manifest = {
        "prereg_version": "gate3-reversal-longsample-v3.0",
        "prereg_sha256": PREREG_SHA256,
        "files": {n: {"sha256": sha256_file(os.path.join(OUT_DIR, n)), "bytes": os.path.getsize(os.path.join(OUT_DIR, n))}
                  for n in files},
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"[gate3] 产物 -> {OUT_DIR}/")
    print(f"[gate3] 最终判定：{verdict['verdict']}（{verdict['label']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
