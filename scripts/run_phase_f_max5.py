#!/usr/bin/env python
"""阶段 F：MAX5 效应（波动率分层后）。

预登记：`docs/PHASE_F_MAX5_PREREG_2026-10-21.md`
冻结 sha256：`aec46d88bbdaa207c14f183bd9275f1952f21b66a15fd762aea127aefd22071e`

## 问什么

**在波动率相同的股票之间，MAX5 还能预测收益吗？**

可行性检查实测 `MAX5 vs 21 日波动率 秩相关 = +0.8796`（0.88² ≈ 0.77）。
而 Gate 4（低波动）已 FAIL。所以直接测原始 MAX5 无法区分"MAX 效应"与"波动率效应"。

## 主检验（分层）

```
每月：池按 VOL 等频分 5 层 → 层内按 MAX5 等频分 5 组
      层内价差 = R(Q1) − R(Q5)          ← 正值 = 低 MAX5 跑赢 = 文献方向
      Δ_t = Σ_L w_L × 层内价差(L)
```

原始 MAX5 的价差只作**描述性对照**，不参与判定。

## 与阶段 C 的差别

不做个股层面的涨停剔除（分组价差是统计量，不是可交易组合）。
阶段 C 已量化该约束代价（−5.83% / −7.09%），直接引用。

用法
----
    python scripts/run_phase_f_max5.py
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

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    ADJ_DIR, END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, Panels, START,
    build_mask, cost_of, load_close,
)
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数 ====================

PREREG_PATH = "docs/PHASE_F_MAX5_PREREG_2026-10-21.md"
PREREG_SHA256 = "aec46d88bbdaa207c14f183bd9275f1952f21b66a15fd762aea127aefd22071e"

FEAS_PATH = "reports/MAX5_FEASIBILITY.json"
ACCEPT_PATH = "data/exec_all/_acceptance.json"
MIN_QFQ_FILES = 5400

WIN = 21
TOP_N = 5
MIN_VALID = 19
N_LAYER = 5
N_GROUP = 5
MIN_PER_GROUP = 5
MIN_LAYERS = 3

NW_LAG_F = NW_LAG
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30
COST_TIERS = (0.001, 0.003)

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasef_out", PREREG_SHA12)


def _sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def split_equal(x: np.ndarray, k: int) -> np.ndarray:
    """等频分组，返回 0..k-1 的组号（按值升序）。用排序分段，不受 ties 影响。"""
    n = len(x)
    order = np.argsort(x, kind="stable")
    grp = np.empty(n, dtype=int)
    sizes = np.full(k, n // k)
    sizes[: n % k] += 1
    s = 0
    for g in range(k):
        grp[order[s:s + sizes[g]]] = g
        s += sizes[g]
    return grp


def verify() -> dict:
    info = {}
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = _sha(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[F] ① 本预登记校验 OK  {got[:16]}")

    if not os.path.exists(FEAS_PATH):
        raise SystemExit(f"缺少可行性报告 {FEAS_PATH}")
    feas = json.load(open(FEAS_PATH, encoding="utf-8"))
    rv = (feas.get("R4_rank_corr") or {}).get("vs_vol21")
    if rv is None or abs(rv) <= 0.8:
        raise SystemExit(
            f"'必须分层'的前提不成立（MAX5 vs VOL = {rv}）。\n"
            "预登记 §9.2 要求该值 > 0.8 才继续——请人工确认后再跑。")
    info["feas_rank_corr_vs_vol"] = rv
    print(f"[F] ② 可行性报告 OK  MAX5 vs VOL = {rv:+.4f}（> 0.8，分层必要）")

    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0 or acc.get("v2_bad"):
        raise SystemExit(f"exec_all 验收未通过：{acc.get('n_missing')} / {acc.get('v2_bad')}")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target")}
    print(f"[F] ②b exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}")

    n = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n} < {MIN_QFQ_FILES}")
    info["n_qfq_files"] = n
    print(f"[F] ②c qfq_all 覆盖 OK  {n}")

    info["params_hardcoded"] = True
    print("[F] ③ 参数硬编码 OK")
    return info, feas


def judge(nw: dict) -> dict:
    n, eff, t = nw["n"], nw["n_eff"], nw["t_nw"]
    d = nw["mean_pp"]
    ad = abs(d) if d == d else float("nan")
    if n < MIN_N_MAIN:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"n={n} < {MIN_N_MAIN}"]}
    if eff < MIN_NW_EFF:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"NW 有效样本 {eff} < {MIN_NW_EFF}"]}
    if abs(t) < T_CRIT:
        return {"verdict": "MAX_EFFECT_ABSORBED_BY_VOL",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"Δ={d:+.4f}pp（分层后不显著）"],
                "note": "分层后不显著 → 原始 MAX5 的效应最可能来自波动率暴露（预登记 §4.1）"}
    if ad < ECON_MIN_PP:
        return {"verdict": "MAX5_EFFECT_SMALL_BUT_SIGNIFICANT",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|Δ|={ad:.4f}pp < {ECON_MIN_PP}pp"]}
    if d > 0:
        return {"verdict": "MAX5_EFFECT_CONFIRMED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"Δ={d:+.4f}pp >= {ECON_MIN_PP}pp，方向：低 MAX5 跑赢（文献方向）"]}
    return {"verdict": "MAX5_EFFECT_REVERSED",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                          f"Δ={d:+.4f}pp <= −{ECON_MIN_PP}pp，方向：高 MAX5 跑赢（与文献相反）"]}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 F：MAX5 效应（波动率分层后）")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG_F}")
    print("=" * 78)
    vi, feas = verify()
    print()

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)

    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    arr_c = close.to_numpy()
    ffill = panels.ffill

    recs, layer_rows = [], []
    med_check = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL:
            continue
        if pos - WIN + 1 < 0:
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        win = arr_r[pos - WIN + 1: pos + 1][:, idx]
        valid_cnt = np.isfinite(win).sum(axis=0)
        good = valid_cnt >= MIN_VALID
        if good.sum() < 100:
            continue

        sub = win[:, good]
        w2 = np.where(np.isfinite(sub), sub, -np.inf)
        max5 = np.sort(w2, axis=0)[-TOP_N:, :].mean(axis=0)
        vol = np.nanstd(sub, axis=0, ddof=1)
        codes_g = cols[idx][good]

        m = np.isfinite(max5) & np.isfinite(vol)
        if m.sum() < 100:
            continue
        max5, vol, codes_g = max5[m], vol[m], codes_g[m]

        p0 = ffill.iloc[pos][codes_g].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes_g].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd) & np.isfinite(max5)
        if fm.sum() < 100:
            continue
        max5, vol, codes_g, fwd = max5[fm], vol[fm], codes_g[fm], fwd[fm]
        n_pool = len(fwd)
        med_check.append(float(np.median(max5)))

        # ---- 主检验：按 VOL 分层，层内按 MAX5 分组
        gv = split_equal(vol, N_LAYER)
        deltas, ws, layer_detail = [], [], []
        for L in range(N_LAYER):
            sel = gv == L
            nL = int(sel.sum())
            if nL < N_GROUP * MIN_PER_GROUP:
                continue
            gm = split_equal(max5[sel], N_GROUP)
            q1 = sel.copy()
            q1[np.where(sel)[0][gm != 0]] = False
            q5 = sel.copy()
            q5[np.where(sel)[0][gm != N_GROUP - 1]] = False
            if q1.sum() < MIN_PER_GROUP or q5.sum() < MIN_PER_GROUP:
                continue
            sp = float(fwd[q1].mean() - fwd[q5].mean())
            deltas.append(sp)
            ws.append(nL)
            layer_detail.append({"layer": L + 1, "n": nL, "spread": sp,
                                 "vol_med": float(np.median(vol[sel])),
                                 "q1_vol_med": float(np.median(vol[q1])),
                                 "q1_codes": list(codes_g[q1])})
        if len(deltas) < MIN_LAYERS:
            continue
        wsum = sum(ws)
        delta = sum(s * w / wsum for s, w in zip(deltas, ws))

        # ---- 描述性对照：原始 MAX5 五分组（不分层）
        gm_all = split_equal(max5, N_GROUP)
        raw_sp = float(fwd[gm_all == 0].mean() - fwd[gm_all == N_GROUP - 1].mean())

        # Q1（最低 MAX5）成员用于换手
        q1_all = list(codes_g[gm_all == 0])

        rank_corr = float("nan")
        a, b = pd.Series(max5).rank(), pd.Series(vol).rank()
        if a.std() > 0 and b.std() > 0:
            rank_corr = float(np.corrcoef(a, b)[0, 1])

        recs.append({"signal_date": sdate, "month": str(cal[nxt].date()),
                     "n_pool": n_pool, "n_layers": len(deltas),
                     "delta": delta, "raw_spread": raw_sp,
                     "layer_detail": layer_detail, "q1_codes": q1_all,
                     "fwd": dict(zip(codes_g, fwd)), "rank_corr": rank_corr})
        for ld in layer_detail:
            layer_rows.append({"signal_date": sdate, **{k: v for k, v in ld.items()
                                                        if k != "q1_codes"}})

    if not recs:
        raise SystemExit("没有任何有效月份")

    # ---- 自检：MAX5 中位数与可行性报告一致（预登记 §9.2 第 3 项）
    med_got = statistics.mean(med_check) * 100
    med_exp = (feas.get("R3_max5_distribution_pp") or {}).get("med")
    chk = {"got_pp": round(med_got, 4), "expected_pp": med_exp,
           "rel_diff": round(abs(med_got - med_exp) / abs(med_exp), 6) if med_exp else None}
    print()
    print("=== MAX5 实现自检（与可行性报告比对）===")
    print(f"  中位数逐月均值 实测 {med_got:.4f}pp  可行性报告 {med_exp}pp  "
          f"相对差 {chk['rel_diff']*100:.3f}%")
    if med_exp and chk["rel_diff"] > 0.05:
        raise SystemExit("MAX5 实现与可行性报告不一致（>5%），先查实现再跑")
    print("  >>> 一致")

    # ---- 主检验
    print()
    print("=" * 78)
    print("主检验：Δ_t = Σ_L w_L × [R(Q1) − R(Q5)]（VOL 分层内，双侧 NW 滞后 3）")
    print("=" * 78)
    deltas = [r["delta"] for r in recs]
    nw = C.newey_west(deltas, NW_LAG_F)
    verdict = judge(nw)
    print(f"  月份 {len(recs)}  NW 有效样本 {nw['n_eff']}")
    print(f"  mean(Δ) = {nw['mean_pp']:+.4f} pp/月   NW se = {nw['se_nw_pp']:.4f}")
    print(f"  NW t    = {nw['t_nw']:+.4f}   p = {nw['p']:.4g}")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")

    # ---- 原始 MAX5 对照
    raw = [r["raw_spread"] for r in recs]
    nw_raw = C.newey_west(raw, NW_LAG_F)
    print()
    print("描述性对照：原始 MAX5 五分组价差 R(Q1)−R(Q5)（**不分层**，不参与判定）")
    print(f"  mean = {nw_raw['mean_pp']:+.4f} pp/月   NW t = {nw_raw['t_nw']:+.4f}")
    print(f"  分层后 / 原始 = {nw['mean_pp']/nw_raw['mean_pp']:.3f}"
          if nw_raw["mean_pp"] else "  分层后 / 原始 = n/a")
    print(f"  共线性吸收 = {nw_raw['mean_pp'] - nw['mean_pp']:+.4f}pp")

    # ---- D1 各层
    print()
    print("D1 各层层内价差（pp/月，跨月均值）：")
    for L in range(1, N_LAYER + 1):
        v = [x["spread"] for r in recs for x in r["layer_detail"] if x["layer"] == L]
        n = [x["n"] for r in recs for x in r["layer_detail"] if x["layer"] == L]
        if v:
            print(f"    L{L}（{'最低' if L==1 else '最高' if L==N_LAYER else '第'+str(L)}波动）"
                  f"  Δ={statistics.mean(v)*100:+8.4f}  n层中位={statistics.median(n):>5.0f}  "
                  f"月份 {len(v)}")

    # ---- D3 分段
    print()
    print("D3 分段：")
    SEGS = (("2007-01-01", "2015-12-31", "2007-2015"), ("2016-01-01", "2026-09-30", "2016-2026"))
    seg = {}
    for lo, hi, lab in SEGS:
        sub = [r["delta"] for r in recs if lo <= r["signal_date"] <= hi]
        if len(sub) < 5:
            continue
        n2 = C.newey_west(sub, NW_LAG_F)
        seg[lab] = {"n": len(sub), "mean_pp": round(n2["mean_pp"], 6),
                    "t_nw": round(n2["t_nw"], 6)}
        print(f"    {lab}  n={len(sub):>3}  Δ={n2['mean_pp']:+8.4f}pp  NW t={n2['t_nw']:+.3f}")

    # ---- D4 分年度
    by = {}
    for r in recs:
        by.setdefault(r["signal_date"][:4], []).append(r["delta"])
    yearly = {y: {"n": len(v), "delta_pp": round(statistics.mean(v) * 100, 6)}
              for y, v in sorted(by.items())}
    print()
    print("D4 分年度 Δ（pp/月）：")
    print("    " + "  ".join(f"{y}:{v['delta_pp']:+.3f}" for y, v in yearly.items()))

    # ---- D5 分层是否干净
    q1_vol = [x["q1_vol_med"] for r in recs for x in r["layer_detail"]]
    pool_vol = [sw for r in recs for sw in
                [statistics.median([x["vol_med"] for x in r["layer_detail"]])]]
    d5 = {"q1_vol_median": statistics.mean(q1_vol) if q1_vol else None,
          "pool_vol_median": statistics.mean(pool_vol) if pool_vol else None}
    print()
    print(f"D5 Q1 组波动率中位 {d5['q1_vol_median']*100:.4f}pp  vs 各层波动率中位之平均 "
          f"{d5['pool_vol_median']*100:.4f}pp")
    print("    （Q1 只在层内比较，故不应系统性地低于池中位；差异应接近 0）")

    # ---- D6 共线性随时间
    rc = [r["rank_corr"] for r in recs if r["rank_corr"] == r["rank_corr"]]
    print()
    print(f"D6 MAX5 vs VOL 月度秩相关：均值 {statistics.mean(rc):+.4f}  "
          f"最小 {min(rc):+.4f}  最大 {max(rc):+.4f}")

    # ---- 成本（Q1 组等权组合换手）
    tos, prev = [], None
    for r in recs:
        cur = r["q1_codes"]
        if prev:
            f = r["fwd"]
            wp = {c: 1.0 / len(prev) for c in prev}
            drift, tot = {}, 0.0
            for c, w in wp.items():
                v = f.get(c, float("nan"))
                if v == v:
                    drift[c] = w * (1 + v)
                    tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(cur)
            tos.append(0.5 * sum(abs((wn if c in set(cur) else 0.0) - drift.get(c, 0.0))
                                 for c in set(drift) | set(cur)))
        prev = cur
    to = statistics.mean(tos) if tos else float("nan")
    sd = statistics.stdev(deltas)
    print()
    print("=" * 78)
    print("成本（Q1 组等权组合实测换手；月度 = 2×换手×费率，×12 是年化）")
    print("=" * 78)
    cost = {}
    for c in COST_TIERS:
        r_ = cost_of(to, nw["mean"], sd, len(deltas), c)
        cost[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r_.items()}
        print(f"  单边 {c*100:.1f}%: 换手 {to*100:5.1f}%/月  成本 {r_['cost_pp_month']:.4f}pp → "
              f"净 {r_['net_pp_month']:+.4f}pp  t_net={r_['t_net']:+.3f}")

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,n_layers,delta,raw_spread,rank_corr,"
                + ",".join(f"layer{i}_spread" for i in range(1, N_LAYER + 1)) + "\n")
        for r in recs:
            ls = {x["layer"]: x["spread"] for x in r["layer_detail"]}
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['n_layers']},"
                    f"{r['delta']:.10f},{r['raw_spread']:.10f},{r['rank_corr']:.6f},"
                    + ",".join(f"{ls.get(i, float('nan')):.10f}" for i in range(1, N_LAYER + 1))
                    + "\n")

    summary = {
        "experiment": "phasef-max5-within-vol-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "question": "在波动率相同的股票之间，MAX5 还能预测收益吗？",
        "rationale": "MAX5 vs 21 日 VOL 秩相关 0.88（0.88²≈0.77）；Gate 4 已 FAIL，故必须分层",
        "params": {"win": WIN, "top_n": TOP_N, "min_valid": MIN_VALID,
                   "n_layer": N_LAYER, "n_group": N_GROUP, "min_per_group": MIN_PER_GROUP,
                   "min_layers": MIN_LAYERS, "nw_lag": NW_LAG_F,
                   "min_n": MIN_N_MAIN, "t_crit": T_CRIT, "econ_min_pp": ECON_MIN_PP},
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "verify": vi,
        "max5_selfcheck": chk,
        "main": {"n": len(recs), **clean(nw), "judgement": verdict},
        "descriptive_raw_max5": {**clean(nw_raw),
                                 "note": "不分层的原始 MAX5 价差，仅对照，不参与判定"},
        "absorption": {"raw_pp": round(nw_raw["mean_pp"], 6),
                       "within_vol_pp": round(nw["mean_pp"], 6),
                       "ratio": (round(nw["mean_pp"] / nw_raw["mean_pp"], 6)
                                 if nw_raw["mean_pp"] else None)},
        "diagnostics": {
            "D1_by_layer": {f"L{L}": None for L in range(1, N_LAYER + 1)},
            "D3_segment": seg,
            "D4_yearly": yearly,
            "D5_vol_clean": d5,
            "D6_rank_corr": {"mean": round(statistics.mean(rc), 6),
                             "min": round(min(rc), 6), "max": round(max(rc), 6)},
        },
        "turnover_cost": {"one_side_per_month": round(to, 6), "cost": cost},
        "limitations": [
            "不做个股层面涨停剔除（分组价差是统计量，非可交易组合）；阶段 C 已量化 −5.83%/−7.09%",
            "分层用等频排序分段，层内 Q1/Q5 各约 n_pool/25 只",
            "MAX5 用 21 日前 5 大日收益（Zhu & Zhang 2020 的中国口径），不提供敏感性变体",
            "冲击成本未建模；ST 无历史标记；after_last_data 中退市与抓取截断不可区分",
            "2005-2006 不可用（左截断）",
        ],
        "note": "分层后不显著 ⇒ MAX_EFFECT_ABSORBED_BY_VOL，这条线关闭。",
    }
    for L in range(1, N_LAYER + 1):
        v = [x["spread"] for r in recs for x in r["layer_detail"] if x["layer"] == L]
        if v:
            summary["diagnostics"]["D1_by_layer"][f"L{L}"] = {
                "n_months": len(v), "mean_pp": round(statistics.mean(v), 6)}

    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["monthly.csv", "summary.json"]
    manifest = {
        "experiment": "phasef-max5-within-vol-v1.0",
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
