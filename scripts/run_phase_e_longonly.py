#!/usr/bin/env python
"""阶段 E：单边多头 + 等权基准（绕开 M4 结构阻塞）。

预登记：`docs/PHASE_E_LONGONLY_PREREG_2026-10-21.md`
冻结 sha256：`2a3893f3f5b1fee006477b920fe93d40de72e61783460b49d7a6c635460c023b`

## 问什么

Gate 6 的 `d` 需要做空腿，A 股个股做空基本不可得。本实验只买多头腿，
用**池内等权**作基准减掉市场收益：

    alpha_t = R_long,t − R_bench,t

## 与 Gate 6 的唯一差别

没有空头腿。多头持仓与阶段 C 的 D 格**必须一致**——预登记要求逐位复现
`mean(d)=0.65818965 / spread=1.31637930 / t=4.11104858`。

（预登记 §1.1 原写"逐月比对 longs_c 集合"，但阶段 C 的产物只存了逐月汇总、
没存持仓明细。改用统计量逐位复现等价——`d` 是持仓的确定性函数，d 一致即持仓一致。
这个替代写在报告里。）

## 不做的事

beta 中性化、行业中性化、市值加权基准、期货对冲、权重优化。全部在预登记 §5 划了界。

用法
----
    python scripts/run_phase_e_longonly.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    ADJ_DIR, END_HOLD, END_SIGNAL, K_FRAC, MIN_LISTED_DAYS, NW_LAG, Panels, START,
    apply_constraints, build_mask, cost_of, load_close, stream_bars, turnover_of,
)
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数 ====================

PREREG_PATH = "docs/PHASE_E_LONGONLY_PREREG_2026-10-21.md"
PREREG_SHA256 = "2a3893f3f5b1fee006477b920fe93d40de72e61783460b49d7a6c635460c023b"

PHASE_C_PREREG = "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md"
PHASE_C_PREREG_SHA256 = "3a01759a37f03c57ec19a6090f52bbda11ed5a284271edfc4328504de64d1ab3"

ACCEPT_PATH = "data/exec_all/_acceptance.json"
MIN_QFQ_FILES = 5400

NW_LAG_E = NW_LAG
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30
COST_TIERS = (0.001, 0.003)
LOT = 100

# 阶段 C D 格复现（单位 pp，从产物抄）
EXPECT_D = {"n": 237, "mean_d": 0.65818965, "mean_spread": 1.31637930, "t_stat": 4.1110485798}
REPRO_TOL = {"n": 0, "mean_d": 1e-6, "mean_spread": 1e-6, "t_stat": 1e-6}

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasee_out", PREREG_SHA12)


def _sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def verify() -> dict:
    info = {}
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = _sha(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[E] ① 本预登记校验 OK  {got[:16]}")

    c1 = _sha(PHASE_C_PREREG)
    if c1 != PHASE_C_PREREG_SHA256:
        raise SystemExit(f"阶段 C 预登记 sha256 不符：{c1}")
    info["phase_c_prereg_sha256"] = c1
    print(f"[E] ② 阶段 C 预登记 OK  {c1[:16]}")

    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0 or acc.get("v2_bad"):
        raise SystemExit(f"exec_all 验收未通过：{acc.get('n_missing')} / {acc.get('v2_bad')}")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target")}
    print(f"[E] ③ exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}")

    n = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n} < {MIN_QFQ_FILES}")
    info["n_qfq_files"] = n
    print(f"[E] ④ qfq_all 覆盖 OK  {n}")

    info["params_hardcoded"] = True
    print("[E] ⑤ 参数硬编码 OK")
    return info


def judge(nw: dict) -> dict:
    n, eff, t = nw["n"], nw["n_eff"], nw["t_nw"]
    mp = abs(nw["mean_pp"]) if nw["mean_pp"] == nw["mean_pp"] else float("nan")
    if n < MIN_N_MAIN:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"n={n} < {MIN_N_MAIN}"]}
    if eff < MIN_NW_EFF:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"NW 有效样本 {eff} < {MIN_NW_EFF}"]}
    sig, big = abs(t) >= T_CRIT, mp >= ECON_MIN_PP
    if sig and big:
        return {"verdict": "LONGONLY_ALPHA_CONFIRMED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|alpha|={mp:.4f}pp >= {ECON_MIN_PP}pp"],
                "direction": "多头跑赢基准" if nw["mean"] > 0 else "多头跑输基准"}
    if not sig:
        return {"verdict": "LONGONLY_ALPHA_NOT_DETECTED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"|alpha|={mp:.4f}pp"]}
    return {"verdict": "LONGONLY_ALPHA_SMALL_BUT_SIGNIFICANT",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                          f"|alpha|={mp:.4f}pp < {ECON_MIN_PP}pp"]}


def ols_slope(y: list[float], x: list[float]) -> tuple[float, float]:
    """返回 (斜率, 相关系数)。"""
    n = len(y)
    if n < 3:
        return float("nan"), float("nan")
    my, mx = statistics.mean(y), statistics.mean(x)
    sxy = sum((a - my) * (b - mx) for a, b in zip(y, x)) / n
    sxx = sum((b - mx) ** 2 for b in x) / n
    syy = sum((a - my) ** 2 for a in y) / n
    if sxx == 0:
        return float("nan"), float("nan")
    beta = sxy / sxx
    corr = sxy / math.sqrt(sxx * syy) if syy > 0 else float("nan")
    return beta, corr


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 E：单边多头 + 等权基准（绕开 M4）")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG_E}")
    print("=" * 78)
    vi = verify()
    print()

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    rows_d = panels.month_rows(mask, positions, "frac")
    print(f"[E] 选股 {len(rows_d)} 月")

    need = C.collect_needed({"D": rows_d})
    bars, ex_aligned, span = stream_bars(need)

    v0 = apply_constraints(rows_d, bars, ex_aligned, span)
    st0 = C.stats_of([r["d"] for r in v0])

    print()
    print("=== 前置：阶段 C D 格复现 ===")
    repro = {}
    for k, exp in EXPECT_D.items():
        got = st0[k] * (100 if k in ("mean_d", "mean_spread") else 1)
        tol = REPRO_TOL[k]
        ok = abs(got - exp) <= tol if tol else got == exp
        repro[k] = {"expected": exp, "actual": round(got, 8), "ok": ok}
        print(f"  {k:<12} 实测 {got:+.6f}  期望 {exp:+.6f}  {'OK' if ok else 'FAIL'}")
    if not all(v["ok"] for v in repro.values()):
        raise SystemExit("未能复现阶段 C 的 D 格，拒绝继续")
    print("  >>> 复现通过：多头持仓与阶段 C 同源")

    # ---- 逐月 R_long / R_bench / alpha
    recs = []
    lot_missing = 0
    lot_total = 0
    for r in v0:
        longs = r["longs_c"]
        if not longs:
            continue
        f = r["fwd"]
        r_long = float(f.loc[longs].mean())
        r_bench = float(f.mean())          # 池内全部等权
        # 容量：一手成本（EXEC 收盘 × 100）
        lots = []
        for c in longs:
            lot_total += 1
            rec = bars.get(c, {}).get(r["entry"])
            if rec is None:
                lot_missing += 1
                continue
            px = rec["bar"][3]
            if px > 0:
                lots.append(px * LOT)
            else:
                lot_missing += 1
        w1 = sum(lots) if lots else float("nan")
        we = (len(lots) * max(lots)) if lots else float("nan")
        recs.append({
            "signal_date": r["signal_date"], "month": r["month"],
            "n_pool": r["n_pool"], "k": len(longs),
            "r_long": r_long, "r_bench": r_bench, "alpha": r_long - r_bench,
            "d_pair": r["d"], "n_drop_long": r["n_drop_long"],
            "w_1lot": w1, "w_equal": we, "n_lot": len(lots),
            "longs_c": longs, "fwd": f,
        })

    print()
    print("=" * 78)
    print("主检验：alpha_t = R_long,t − R_bench,t   （双侧，NW 滞后 3）")
    print("=" * 78)
    alphas = [r["alpha"] for r in recs]
    nw = C.newey_west(alphas, NW_LAG_E)
    verdict = judge(nw)
    print(f"  配对月份 {len(recs)}  NW 有效样本 {nw['n_eff']}")
    print(f"  mean(alpha) = {nw['mean_pp']:+.4f} pp/月   NW se = {nw['se_nw_pp']:.4f}")
    print(f"  NW t        = {nw['t_nw']:+.4f}   p = {nw['p']:.4g}")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    if verdict.get("direction"):
        print(f"      · 方向：{verdict['direction']}")

    # ---- D1 / D2
    m_long = statistics.mean([r["r_long"] for r in recs])
    m_bench = statistics.mean([r["r_bench"] for r in recs])
    win = sum(1 for r in recs if r["alpha"] > 0) / len(recs)
    print()
    print(f"D1 多头 {m_long*100:+.4f}pp  基准 {m_bench*100:+.4f}pp  "
          f"差 {nw['mean_pp']:+.4f}pp")
    print(f"D2 月度胜率（R_long > R_bench）：{win*100:.2f}%")

    # ---- D3 分段
    SEGS = (("2007-01-01", "2015-12-31", "2007-2015"), ("2016-01-01", "2026-09-30", "2016-2026"))
    print()
    print("D3 分段 alpha：")
    seg = {}
    for lo, hi, lab in SEGS:
        sub = [r["alpha"] for r in recs if lo <= r["signal_date"] <= hi]
        s = C.stats_of(sub)
        s2 = C.newey_west(sub, NW_LAG_E)
        seg[lab] = {**C.stats_of(sub), "nw": s2}
        print(f"    {lab}  n={s['n']:>3}  alpha={s['mean_d']*100:+.4f}pp  "
              f"t={s['t_stat']:+.3f}  NW t={s2['t_nw']:+.3f}")

    # ---- D4 分年度
    by: dict[str, list[float]] = defaultdict(list)
    for r in recs:
        by[r["signal_date"][:4]].append(r["alpha"])
    yearly = {y: {"n": len(v), "alpha_pp": round(statistics.mean(v) * 100, 6)}
              for y, v in sorted(by.items())}
    print()
    print("D4 分年度 alpha（pp/月）：")
    print("    " + "  ".join(f"{y}:{v['alpha_pp']:+.3f}" for y, v in yearly.items()))

    # ---- D5 beta
    beta, corr = ols_slope([r["r_long"] for r in recs], [r["r_bench"] for r in recs])
    print()
    print(f"D5 beta（R_long ~ R_bench）= {beta:+.4f}   相关系数 = {corr:+.4f}")

    # ---- D6 与空头腿对照
    d_pair = statistics.mean([r["d_pair"] for r in recs])
    print(f"D6 多头−基准 {nw['mean_pp']:+.4f}pp  多空价差 2d = {2*d_pair*100:+.4f}pp  "
          f"比值 {(nw['mean_pp']/(2*d_pair*100)) if d_pair else float('nan'):.3f}")

    # ---- 成本
    print()
    print("=" * 78)
    print("成本（月度 = 2 × 单边换手 × 费率；×12 是年化，不入月度扣减）")
    print("=" * 78)
    # 多头腿换手：用阶段 C 的 turnover_of，但只有多头侧 → 单独算
    tos, prev = [], None
    for r in recs:
        f = r["fwd"]
        cur = r["longs_c"]
        if prev:
            wp = {c: 1.0 / len(prev) for c in prev}
            drift, tot = {}, 0.0
            for c, w in wp.items():
                v = f.get(c, float("nan"))
                if v != v:
                    continue
                drift[c] = w * (1 + v)
                tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(cur)
            tos.append(0.5 * sum(abs((wn if c in set(cur) else 0.0) - drift.get(c, 0.0))
                                 for c in set(drift) | set(cur)))
        prev = cur
    to = statistics.mean(tos) if tos else float("nan")
    # ⚠️ 必须走 cost_of：它内部把小数 mean/sd 统一转成 pp 再除。
    #    第一版在这里自己算 `net / se_a`，而 net 是 pp、se_a 是小数 → t 被放大 100 倍
    #    （出现 t_net=+73.98 / −140.27 这种荒谬值）。成本这块本项目已经错过两次。
    sd_a = statistics.stdev(alphas)
    cost = {}
    for c in COST_TIERS:
        r_ = cost_of(to, nw["mean"], sd_a, len(alphas), c)
        cost[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r_.items()}
        print(f"  单边 {c*100:.1f}%: 换手 {to*100:.1f}%/月  成本 "
              f"{r_['cost_pp_month']:.4f}pp → 净 {r_['net_pp_month']:+.4f}pp  "
              f"t_net={r_['t_net']:+.3f}")

    # ---- 容量
    w1 = [r["w_1lot"] for r in recs if r["w_1lot"] == r["w_1lot"]]
    we = [r["w_equal"] for r in recs if r["w_equal"] == r["w_equal"]]
    def q(v, p):
        if not v:
            return float("nan")
        s = sorted(v)
        i = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return s[i]
    cap = {"w_1lot_median": q(w1, 0.5), "w_1lot_p90": q(w1, 0.9),
           "w_equal_median": q(we, 0.5), "w_equal_p90": q(we, 0.9),
           "lot_coverage": round(1 - lot_missing / max(lot_total, 1), 6)}
    print()
    print("=" * 78)
    print("容量（一手成本口径，元）")
    print("=" * 78)
    print(f"  W_1lot（每只 1 手）    中位 {cap['w_1lot_median']/1e4:>10,.1f} 万   "
          f"P90 {cap['w_1lot_p90']/1e4:>10,.1f} 万")
    print(f"  W_equal（等权且≥1手）  中位 {cap['w_equal_median']/1e4:>10,.1f} 万   "
          f"P90 {cap['w_equal_p90']/1e4:>10,.1f} 万")
    print(f"  价格覆盖 {cap['lot_coverage']*100:.2f}%")
    if cap["w_equal_median"] > 1e7:
        print("  ⚠ W_equal 中位 > 1,000 万 → 等权+整手在小资金下不可实现")

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,k,r_long,r_bench,alpha,d_pair,"
                "n_drop_long,w_1lot,w_equal,n_lot\n")
        for r in recs:
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['k']},"
                    f"{r['r_long']:.10f},{r['r_bench']:.10f},{r['alpha']:.10f},"
                    f"{r['d_pair']:.10f},{r['n_drop_long']},"
                    f"{r['w_1lot']:.2f},{r['w_equal']:.2f},{r['n_lot']}\n")

    summary = {
        "experiment": "phasee-longonly-alpha-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "phase_c_prereg_sha256": PHASE_C_PREREG_SHA256,
        "question": "去掉空头腿后，反转还剩多少 alpha？",
        "benchmark": "池内等权（当月 n_pool 全部标的，同 fwd 定义）",
        "alpha_definition": "R_long − R_bench，算术差",
        "v0_reproduction": repro,
        "reproduction_note": ("预登记 §1.1 原写'逐月比对 longs_c 集合'，但阶段 C 产物只存逐月汇总、"
                              "未存持仓明细。改用 mean(d)/spread/t 逐位复现——d 是持仓的确定性函数，"
                              "d 一致即持仓一致。"),
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "params": {"k_frac": K_FRAC, "min_listed_days": MIN_LISTED_DAYS, "nw_lag": NW_LAG_E,
                   "min_n": MIN_N_MAIN, "t_crit": T_CRIT, "econ_min_pp": ECON_MIN_PP,
                   "beta_neutralized": False, "lot": LOT},
        "verify": vi,
        "main": {"n": len(recs), **clean(nw), "judgement": verdict},
        "turnover_cost": {"one_side_per_month": round(to, 6), "cost": cost},
        "capacity": cap,
        "diagnostics": {
            "D1_mean_r_long_pp": round(m_long * 100, 6),
            "D1_mean_r_bench_pp": round(m_bench * 100, 6),
            "D2_win_ratio": round(win, 6),
            "D3_segment": {k: {"n": v["n"], "alpha_pp": round(v["mean_d"] * 100, 6),
                               "t": round(v["t_stat"], 6), "nw_t": round(v["nw"]["t_nw"], 6)}
                           for k, v in seg.items()},
            "D4_yearly": yearly,
            "D5_beta": round(beta, 6), "D5_corr": round(corr, 6),
            "D6_alpha_vs_spread": {"alpha_pp": round(nw["mean_pp"], 6),
                                   "spread_pp": round(2 * d_pair * 100, 6)},
        },
        "limitations": [
            "等权基准不是可投资指数（无对应 ETF），系统性偏小盘",
            "不做 beta 中性化（预登记 §5 划界）；beta>1 时 alpha 含风险补偿",
            "冲击成本未建模；T+1 未建模",
            "容量按一手成本算，整手约束下等权需 W≥k×max(lot)",
            "ST 无历史标记；after_last_data 中退市与抓取截断不可区分",
            "2005-2006 不可用于跨期比较（左截断）",
        ],
        "note": "本实验的 alpha 不是可实现收益；M4 在本实验通过且成本后仍显著之前仍为 ❌。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["monthly.csv", "summary.json"]
    manifest = {
        "experiment": "phasee-longonly-alpha-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "script_sha256": _sha(os.path.abspath(__file__)),
        "files": {n: {"sha256": _sha(os.path.join(OUT_DIR, n)),
                      "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print()
    print("=" * 78)
    print(f"产物 -> {OUT_DIR}/")
    print(f"主判定：{verdict['verdict']}")
    print(f"总耗时 {time.time()-t0:.0f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
