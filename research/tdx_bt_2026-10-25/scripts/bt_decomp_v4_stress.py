#!/usr/bin/env python
"""v4：给 v3 的"1/beta^2.0 通过四条指标"做压力测试——它很可能是押中一只票。

【v3 的可疑结论】
  v3 报告 1/beta^2.0 加权：年化 +12.28%、回撤 −14.15%、超额 +3.97pp"通过"。
  但 beta 跨度是 0.15~2.24，权重 ∝ 1/beta^2 时 1/0.15² : 1/2.24² = 223 : 1
  → 实际权重高度集中在最低 beta 的 1~2 只票上。**这不是分散策略，是集中押注。**
  同时 v3 没对 1/beta^1.5 与 1/beta^2.0 做样本外切分。

【v4 做四件事】
  T1 权重集中度诊断：HHI、最大单票权重、有效持仓数 1/HHI
  T2 权重上限约束：单票 ≤5%/10%/15%，重算 1/beta^k —— 若收益大幅下降 = v3 的结论靠集中
  T3 **剔除最大权重票**（leave-max-out）：若结果崩塌，说明是单票驱动
  T4 全方案样本内外切分（v3 只做了三个）

判据（沿用用户四条）：年化≥3% 且 |MDD|≤20% 且 超额≥+2pp/年 且 样本外不翻转。
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_diverse_diag as D
import bt_diversify_decomp_v3 as V3

BETA_WIN = 36
MIN_OBS = 24
TARGET_MDD = 0.20


def perf(r):
    mdd, nav = V3.mdd_of(r)
    yrs = len(r) / 12
    cagr = nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    return {"cagr": cagr, "vol": statistics.pstdev(r) * (12 ** 0.5), "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd else float("nan"), "total": nav - 1,
            "months": len(r)}


def cap_weights(w: dict[str, float], cap: float) -> dict[str, float]:
    """迭代封顶：把超过 cap 的权重削平并再分配，直到全部 ≤ cap 或达最大迭代。"""
    w = dict(w)
    for _ in range(200):
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
        rest = {k: v for k, v in w.items() if v < cap - 1e-12}
        if not rest:
            break
        tot = sum(rest.values())
        for k in rest:
            w[k] += excess * (rest[k] / tot)
    s = sum(w.values())
    return {k: v / s for k, v in w.items() if v > 1e-12}


def hhi(w: dict[str, float]) -> tuple[float, float, float]:
    """返回 (HHI, 最大权重, 有效持仓数 1/HHI)。"""
    s = sum(w.values())
    p = [v / s for v in w.values()]
    h = sum(x * x for x in p)
    return h, max(p), 1 / h


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool))
    avail = [c for c in pool if c in series]
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in avail}
    T, N = len(dates) - 1, len(avail)
    print(f"池 {N} 只  窗口 {dates[0]} → {dates[-1]}  T={T}")

    mkt = [statistics.fmean([rets[c][i] for c in avail]) for i in range(T)]
    betas_roll = {}
    for t in range(MIN_OBS, T):
        lo = max(0, t - BETA_WIN)
        betas_roll[t] = {c: V3.ols(rets[c][lo:t], mkt[lo:t])[1] for c in avail}
    first = min(betas_roll)
    print(f"滚动 beta 首次可用 {dates[first + 1]}，评估 {T - first} 个月")

    def run(wfun):
        r = []
        for t in range(first, T - 1):
            w = wfun(t)
            r.append(sum(w[c] * rets[c][t + 1] for c in avail))
        return r

    def w_invbeta(power, cap=None):
        def f(t):
            iw = {c: 1.0 / max(betas_roll[t][c], 0.15) ** power for c in avail}
            s = sum(iw.values())
            w = {c: iw[c] / s for c in avail}
            return cap_weights(w, cap) if cap else w
        return f

    # ── T1 权重集中度 ────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("T1 权重集中度：1/beta^k 到底押在几只票上")
    print("=" * 76)
    print(f"  {'方案':18s} {'HHI':>7s} {'最大权重':>9s} {'有效持仓':>9s}  最大权重持有者（中位）")
    for power in (1.0, 1.5, 2.0):
        hs, ms, es, top = [], [], [], []
        for t in range(first, T - 1):
            w = w_invbeta(power)(t)
            h, m, e = hhi(w)
            hs.append(h); ms.append(m); es.append(e)
            top.append(max(w, key=w.get))
        cnt = statistics.mode(top)
        print(f"  1/beta^{power:.1f}        {statistics.median(hs):7.3f} "
              f"{statistics.median(ms) * 100:8.1f}% {statistics.median(es):9.2f}  "
              f"{D.NAMES[cnt]}({cnt}) 出现 {top.count(cnt)}/{len(top)} 月")
    print("  ⚠️ 有效持仓数 = 1/HHI；若远小于 20，则所谓「分散」名不副实")

    # ── T2 权重上限 ──────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("T2 加单票权重上限后，1/beta^k 的历史表现")
    print("=" * 76)
    print(f"  {'方案':26s} {'年化':>8s} {'波动':>7s} {'回撤':>8s} {'Calmar':>7s}  {'有效持仓':>8s}")
    results = {}
    cand = [("等权满仓", lambda t: {c: 1 / N for c in avail}, None)]
    for power in (1.0, 1.5, 2.0):
        cand.append((f"1/beta^{power:.1f}", w_invbeta(power), None))
    for power in (1.5, 2.0):
        for cap in (0.05, 0.10, 0.15):
            cand.append((f"1/beta^{power:.1f} cap{cap * 100:.0f}%",
                         w_invbeta(power, cap), cap))
    for lab, wf, cap in cand:
        r = run(wf)
        p = perf(r)
        es = [hhi(wf(t))[2] for t in range(first, min(T - 1, first + 24))]
        print(f"  {lab:26s} {p['cagr'] * 100:+7.2f}% {p['vol'] * 100:6.2f}% "
              f"{p['mdd'] * 100:+7.2f}% {p['calmar']:7.2f}  {statistics.median(es):8.2f}")
        results[lab] = p

    # ── T3 剔除最大权重票 ────────────────────────────────────────
    print("\n" + "=" * 76)
    print("T3 剔除最大权重标的后的表现（检验是否单票驱动）")
    print("=" * 76)
    top_name = statistics.mode([max(w_invbeta(2.0)(t), key=w_invbeta(2.0)(t).get)
                                for t in range(first, T - 1)])
    print(f"  1/beta^2.0 的最常最大权重标的：{D.NAMES[top_name]} ({top_name})")
    keep = [c for c in avail if c != top_name]
    print(f"  剔除后剩 {len(keep)} 只")
    for power in (1.0, 1.5, 2.0):
        def wf(t, power=power, keep=keep):
            iw = {c: 1.0 / max(betas_roll[t][c], 0.15) ** power for c in keep}
            s = sum(iw.values())
            return {c: iw[c] / s for c in keep}
        r = []
        for t in range(first, T - 1):
            w = wf(t)
            r.append(sum(w[c] * rets[c][t + 1] for c in keep))
        p = perf(r)
        full = results.get(f"1/beta^{power:.1f}")
        delta = p["cagr"] - full["cagr"] if full else float("nan")
        print(f"  1/beta^{power:.1f} 剔 {D.NAMES[top_name]}: 年化 {p['cagr'] * 100:+7.2f}% "
              f"(原 {full['cagr'] * 100:+7.2f}%, 差 {delta * 100:+6.2f}pp)  "
              f"回撤 {p['mdd'] * 100:+7.2f}%")

    # ── T4 样本内外切分（全方案）─────────────────────────────────
    i_split = next(i for i, d in enumerate(dates) if d >= "20210101")
    print("\n" + "=" * 76)
    print(f"T4 样本内外切分：内 → {dates[i_split - 1]} / 外 {dates[i_split]} →")
    print("=" * 76)
    print(f"  {'方案':26s} {'内年化':>9s} {'外年化':>9s} {'差':>8s} {'翻转':>5s}")
    splits = {}
    for lab, wf, _ in cand:
        seg = []
        for lo, hi in ((first, i_split - 1), (i_split, T - 1)):
            r = [sum(wf(t)[c] * rets[c][t + 1] for c in avail) for t in range(lo, hi)]
            seg.append(perf(r)["cagr"] if len(r) > 6 else float("nan"))
        flip = "是" if seg[1] < seg[0] - 0.02 else "否"
        splits[lab] = seg
        print(f"  {lab:26s} {seg[0] * 100:+8.2f}% {seg[1] * 100:+8.2f}% "
              f"{(seg[1] - seg[0]) * 100:+7.2f}pp {flip:>4s}")

    # ── 判定 ─────────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("判定（用户四条：年化≥3% / 回撤≤20% / 超额≥+2pp / 样本外不翻转）")
    print("=" * 76)
    base = results["等权满仓"]
    base_cg20 = TARGET_MDD / abs(base["mdd"]) * base["cagr"]
    print(f"  基准：等权满仓压到 {TARGET_MDD * 100:.0f}% 回撤 → 年化 {base_cg20 * 100:+.2f}%")
    print(f"\n  {'方案':26s} {'20%回撤年化':>11s} {'超额':>8s} {'年化':>7s} "
          f"{'回撤':>8s} {'外翻':>5s} {'全过':>5s}")
    verdicts = {}
    for lab, p in results.items():
        s = min(1.0, TARGET_MDD / abs(p["mdd"])) if p["mdd"] else 1.0
        cg = s * p["cagr"]
        ex = cg - base_cg20
        seg = splits.get(lab, [float("nan")] * 2)
        flip = seg[1] < seg[0] - 0.02
        ok = (cg >= 0.03 and abs(p["mdd"]) <= TARGET_MDD + 1e-9
              and ex >= 0.02 and not flip)
        verdicts[lab] = {"cagr_at_20mdd": cg, "excess": ex, "ok": ok,
                         "splits": seg, "flip": flip, "mdd": p["mdd"], "cagr": p["cagr"]}
        print(f"  {lab:26s} {cg * 100:+10.2f}% {ex * 100:+7.2f}pp {p['cagr'] * 100:+6.2f}% "
              f"{p['mdd'] * 100:+7.2f}% {'是' if flip else '否':>4s} {'✓' if ok else '✗':>4s}")

    winners = [k for k, v in verdicts.items() if v["ok"]]
    print(f"\n  通过全部四条的方案：{winners if winners else '**无**'}")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "decomp_v4_stress.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"top_weight_stock": {"code": top_name, "name": D.NAMES[top_name]},
                   "results": results, "verdicts": verdicts, "splits": splits,
                   "base_cagr_at_20mdd": base_cg20, "winners": winners},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
