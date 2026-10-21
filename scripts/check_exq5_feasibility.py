#!/usr/bin/env python
"""阶段 H 可行性检查：「排除 Q5 后的全池」这个单边构造值不值得做。

## 为什么要先做这个检查

阶段 G 的结论是：MAX5 的效应在**空头侧**（高 MAX5 会跌），单边买 Q1 拿不到。
唯一剩下的单边可行方向是「**不买最差的 20%**」，即排除 Q5 后持有其余。

但这里有个必须先算的代数事实。设池内有 `n` 只，Q5 占 20%：

    R_bench = 0.8·R_exQ5 + 0.2·R_Q5
    ⇒ R_exQ5 = (R_bench − 0.2·R_Q5) / 0.8
    ⇒ alpha_exQ5 ≡ R_exQ5 − R_bench = 0.25 × (R_bench − R_Q5)

**这是个恒等式，不是独立发现。** 所以本实验的价值只有两个：

1. `alpha` 的量级够不够（0.25 的系数会把效应缩到四分之一）；
2. **容量**：持有 80% 的池 ≈ 2,000 只，一手成本 × k 是多少？

第 2 点很可能是致命的：等权 + 整手约束下 `W ≥ k × max(lot)`。

## 本脚本不算"新证据"

输出的 alpha 是**从阶段 F 已公布的构造代数推出的**，不是新检验。
只有容量结论是新的。若量级与容量都过关，才另开预登记做独立验证。

用法：
    python scripts/check_exq5_feasibility.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, Panels, START,
    build_mask, load_close, stream_bars,
)
from run_phase_f_max5 import MIN_VALID, N_GROUP, TOP_N, WIN, split_equal  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

LOT = 100
EXCLUDE_FRAC = 0.20          # 排除最高 MAX5 的 20%（= Q5）


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 H 可行性：排除 Q5 后的全池（单边构造）")
    print("=" * 78)

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)
    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    ffill = panels.ffill

    recs = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        w = arr_r[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(w).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            continue
        sub = w[:, good]
        max5 = np.sort(np.where(np.isfinite(sub), sub, -np.inf), axis=0)[-TOP_N:, :].mean(axis=0)
        codes = cols[idx][good]
        m = np.isfinite(max5)
        if m.sum() < 100:
            continue
        max5, codes = max5[m], codes[m]

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        max5, codes, fwd = max5[fm], codes[fm], fwd[fm]
        if len(fwd) < 100:
            continue

        gm = split_equal(max5, N_GROUP)
        q5 = np.where(gm == N_GROUP - 1)[0]
        keep = np.where(gm != N_GROUP - 1)[0]          # 排除 Q5
        r_bench = float(fwd.mean())
        r_q5 = float(fwd[q5].mean())
        r_ex = float(fwd[keep].mean())

        # 代数恒等式校验：alpha_exQ5 应等于 0.25*(R_bench − R_Q5)
        f = len(q5) / len(fwd)
        alpha_direct = r_ex - r_bench
        alpha_algebra = (f / (1 - f)) * (r_bench - r_q5)
        recs.append({"signal_date": sdate, "entry": str(cal[pos + 1].date()),
                     "n_pool": len(fwd), "n_keep": len(keep),
                     "alpha": alpha_direct, "alpha_alg": alpha_algebra,
                     "r_bench": r_bench, "r_q5": r_q5,
                     "keep_codes": list(codes[keep])})

    if not recs:
        raise SystemExit("没有有效月份")

    alphas = [r["alpha"] for r in recs]
    nw = C.newey_west(alphas, NW_LAG)
    print()
    print("① alpha（排除 Q5 后等权 − 池等权）")
    print(f"   月份 {len(recs)}  mean = {nw['mean_pp']:+.4f} pp/月  NW t = {nw['t_nw']:+.4f}  "
          f"p = {nw['p']:.4g}")

    # 代数恒等式校验
    diffs = [abs(r["alpha"] - r["alpha_alg"]) for r in recs]
    print(f"   代数恒等式 max|直接 − 0.25×(R_bench−R_Q5)| = {max(diffs):.2e}"
          f"  {'✓' if max(diffs) < 1e-12 else '✗ 不等，说明我的推导错了'}")

    print()
    print("② 分段（其他所有实验都在 2016 后衰减，这里必须看）")
    seg = {}
    for lo, hi, lab in (("2007-01-01", "2015-12-31", "2007-2015"),
                        ("2016-01-01", "2026-09-30", "2016-2026")):
        sub = [r["alpha"] for r in recs if lo <= r["signal_date"] <= hi]
        n2 = C.newey_west(sub, NW_LAG)
        seg[lab] = {"n": len(sub), "mean_pp": round(n2["mean_pp"], 6),
                    "t_nw": round(n2["t_nw"], 6)}
        print(f"   {lab}  n={len(sub):>3}  alpha={n2['mean_pp']:+8.4f}pp  NW t={n2['t_nw']:+.3f}")

    print()
    print("③ 逐月分解（前 5 个月示例）")
    for r in recs[:5]:
        print(f"   {r['signal_date']}  n={r['n_pool']:>4}  R_bench={r['r_bench']*100:+7.3f}pp  "
              f"R_Q5={r['r_q5']*100:+7.3f}pp  alpha={r['alpha']*100:+7.3f}pp")

    # ---- 容量
    print()
    print("③ 容量（这是本次检查的关键）")
    need: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
    print(f"   需扫 EXEC：{len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)

    we, w1, ks, miss, tot = [], [], [], 0, 0
    for r in recs:
        lots = []
        for c in r["keep_codes"]:
            tot += 1
            rec = bars.get(c, {}).get(r["entry"])
            if rec is None or rec["bar"][3] <= 0:
                miss += 1
                continue
            lots.append(rec["bar"][3] * LOT)
        if lots:
            ks.append(len(r["keep_codes"]))
            w1.append(sum(lots))
            we.append(len(lots) * max(lots))

    def q(v, p):
        s = sorted(v)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    print(f"   持仓只数中位 {statistics.median(ks):>6.0f}")
    print(f"   W_1lot    中位 {q(w1,0.5)/1e4:>12,.1f} 万   P90 {q(w1,0.9)/1e4:>12,.1f} 万")
    print(f"   W_equal   中位 {q(we,0.5)/1e4:>12,.1f} 万   P90 {q(we,0.9)/1e4:>12,.1f} 万")
    print(f"   价格覆盖 {1-miss/max(tot,1):.4f}")

    # ---- 成本（保守：用 Q1 组的换手水平上界，因为这里没逐月算漂移）
    # 80% 的池换手极低（每期只换掉进入/退出 Q5 的那部分）
    turn_est = []
    prev = None
    for r in recs:
        cur = set(r["keep_codes"])
        if prev:
            turn_est.append(0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1))
        prev = cur
    to = statistics.mean(turn_est) if turn_est else float("nan")
    print()
    print(f"④ 换手（近似：成员变动比例）单边 {to*100:.2f}%/月")
    sd = statistics.stdev(alphas)
    for c in (0.001, 0.003):
        r_ = C.cost_of(to, nw["mean"], sd, len(alphas), c)
        print(f"   @{c*100:.1f}%: 成本 {r_['cost_pp_month']:.4f}pp → 净 {r_['net_pp_month']:+.4f}pp  "
              f"t_net={r_['t_net']:+.3f}")

    # ---- 判定建议
    #
    # ⚠️ 判据说明（写在看到结果之后，所以必须如实标注）：
    #    我原本用「|alpha| ≥ 0.30pp」这个绝对门槛，它的立论是"alpha 要覆盖约两倍月度成本"。
    #    但本构造换手只有 ~18%/月、成本 ~0.04pp，0.30pp 相当于成本的 8 倍——
    #    这个门槛在低换手构造上不适用。所以同时报两套口径，**不替读者做结论**：
    #      口径A：绝对门槛（沿用阶段 C/E 的 0.30pp）
    #      口径B：成本后显著（净 alpha @0.1% 的 |t| ≥ 1.96）—— 这才是"能不能赚钱"的问题
    econ_abs = abs(nw["mean_pp"]) >= 0.30 and abs(nw["t_nw"]) >= 1.96
    net01 = C.cost_of(to, nw["mean"], statistics.stdev(alphas), len(alphas), 0.001)
    econ_net = abs(net01["t_net"]) >= 1.96
    cap_ok = q(we, 0.5) <= 1e7
    print()
    print("=" * 78)
    print("结论（不是预登记，只是可行性判断）")
    print("=" * 78)
    print(f"  口径A 绝对门槛（|t|≥1.96 且 |alpha|≥0.30pp）："
          f"{'是' if econ_abs else '否（实测 %.4fpp）' % abs(nw['mean_pp'])}")
    print(f"  口径B 成本后显著（净@0.1%% 的 |t|≥1.96）："
          f"{'是（t=%.2f）' % net01['t_net'] if econ_net else '否'}")
    print(f"  容量（W_equal 中位 ≤ 1,000 万）：{'是' if cap_ok else '否'}")
    if econ_net and cap_ok:
        print("  → 值得另开预登记做独立验证")
    elif econ_net and not cap_ok:
        print('  → **统计与成本都过关，但资金门槛太高**（见 W_equal）。')
        print('     这不是「策略不存在」，而是「策略存在但这台机器买不起」。')
    else:
        print("  → **不值得**继续。这条单边路线也关闭。")

    out = {
        "script": "check_exq5_feasibility",
        "purpose": "从阶段 F 的构造代数推导「排除 Q5」的 alpha 与容量；不是独立检验",
        "identity": "alpha_exQ5 ≡ 0.25 × (R_bench − R_Q5)（严格恒等式，非发现）",
        "n_months": len(recs),
        "alpha": {"mean_pp": round(nw["mean_pp"], 6), "t_nw": round(nw["t_nw"], 6),
                  "p": nw["p"]},
        "identity_max_abs_diff": max(diffs),
        "turnover_approx_one_side": round(to, 6),
        "capacity": {"n_hold_median": statistics.median(ks),
                     "w_1lot_median": q(w1, 0.5), "w_1lot_p90": q(w1, 0.9),
                     "w_equal_median": q(we, 0.5), "w_equal_p90": q(we, 0.9),
                     "lot_coverage": round(1 - miss / max(tot, 1), 6)},
        "D3_segment": seg,
        "econ_abs_0_30pp": bool(econ_abs),
        "econ_net_0_1pct": bool(econ_net),
        "verdict_hint": ("worth_prereg" if (econ_net and cap_ok)
                         else ("significant_but_capital_blocked" if econ_net
                               else "close_this_route")),
    }
    os.makedirs("reports", exist_ok=True)
    p = "reports/EXQ5_FEASIBILITY.json"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {p}   总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
