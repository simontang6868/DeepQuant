#!/usr/bin/env python
"""分散化分解 v3：修正 N_eff 无界、beta 前视，并做样本内外切分。

【v1/v2 的错误清单（保留，不掩盖）】
  E1 等权市场代理污染残差相关 → v2 改留一法
  E2 **N_eff 公式无界**：ρ̄<0 时 N/(1+(N−1)ρ̄) 可远超 N（v2 给 78.9）。
     数学事实：N 只票最多提供 N 个独立信息源 → N_eff 必须截断在 N。
     v3 修正：N_eff = min(N, N/(1+(N−1)ρ̄))，ρ̄≤0 时直接取 N。
  E3 ⭐ **beta 用全样本估计 = 前视偏差**。v2 按全样本 beta 加权，等于"已经知道谁能抗跌"。
     v3 修正：滚动 36 个月估计 beta（每月只用该月之前的数据），首次可用月 = 第 37 个月。

【本脚本回答的问题】
  Q1 行业分散降低了相关性吗？（L1/L2/L3）
  Q2 分散化改善了组合表现吗？（20 只跨行业 vs 11 只资源池，同窗口同规则）
  Q3 滚动 beta 加权的收益是真的还是前视出来的？（v2 全样本 beta vs v3 滚动 beta）
  Q4 在 20% 回撤约束下，最优可达年化是多少？相对"同等风险买入持有"有超额吗？
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_diverse_diag as D

OLD11 = ["601398", "601668", "601699", "601808", "600583", "600188", "600256",
         "601919", "601225", "600985", "601857"]
BETA_WIN = 36          # 滚动 beta 窗口（月）
MIN_OBS = 24           # 最少观测数
TARGET_MDD = 0.20      # 用户锁定的回撤上限


def ols(y, x):
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((v - mx) * (w - my) for v, w in zip(x, y))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def neff_bounded(codes, rets):
    """N_eff 截断在 N 以内。返回 (N_eff, ρ̄, 是否触顶)。"""
    _, rho, _ = D.neff(codes, rets)
    N = len(codes)
    denom = 1 + (N - 1) * rho
    if denom <= 0:
        return float(N), rho, True
    v = N / denom
    cap = v >= N * 0.999
    return (min(v, float(N)), rho, cap)


def mdd_of(r):
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in r:
        nav *= (1 + x)
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    return mdd, nav


def perf(r):
    mdd, nav = mdd_of(r)
    yrs = len(r) / 12
    cagr = nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    return {"cagr": cagr, "vol": statistics.pstdev(r) * (12 ** 0.5),
            "mdd": mdd, "calmar": cagr / abs(mdd) if mdd else float("nan"),
            "total": nav - 1, "months": len(r)}


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool) | set(OLD11))
    avail = [c for c in pool if c in series]
    old_avail = [c for c in OLD11 if c in series]
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    print(f"分散池 {len(avail)} 只 / 旧资源池 {len(old_avail)} 只 / 共同月末 {len(dates)} 个 "
          f"{dates[0]} → {dates[-1]}")
    bad = [(c, d) for c in avail for d in dates if series[c][d][1] <= 0]
    print(f"非正价格 {len(bad)} 处 " + ("✓" if not bad else str(bad[:3])))

    # dates 已是全部 series 的交集 → 每只都有全部日期，可对 series 全量算收益
    rets_all = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                    for i in range(1, len(dates))] for c in series}
    rets = {c: rets_all[c] for c in avail}
    T, N = len(dates) - 1, len(avail)

    # ── Q1 ────────────────────────────────────────────────────────
    n1, rho1, _ = neff_bounded(avail, rets)
    lam, _ = D.pc1([[D.corr(rets[a], rets[b]) for b in avail] for a in avail])
    print(f"\n【Q1-1 原始】ρ̄={rho1:.4f}  N_eff={n1:.2f} (N={N})  PC1={lam / N * 100:.1f}%")

    resid = {}
    for c in avail:
        others = [x for x in avail if x != c]
        mk = [statistics.fmean([rets[x][i] for x in others]) for i in range(T)]
        a, b = ols(rets[c], mk)
        resid[c] = [w - a - b * v for v, w in zip(rets[c], mk)]
    n2, rho2, cap2 = neff_bounded(avail, resid)
    print(f"【Q1-2 留一法残差】ρ̄={rho2:.4f}  N_eff={n2:.2f}"
          f"{'（触顶=N，残差层面完全独立）' if cap2 else ''}")
    print(f"  v2 的 78.9 已作废：N_eff 必须 ≤ N={N}，v2 缺截断（E2）")

    sec_codes = {s: [c for c in cs if c in avail] for s, cs in D.SECTORS.items()}
    sec_codes = {s: cs for s, cs in sec_codes.items() if cs}
    sec_rets = {s: [statistics.fmean([rets[c][i] for c in cs]) for i in range(T)]
                for s, cs in sec_codes.items()}
    n3, rho3, _ = neff_bounded(list(sec_rets), sec_rets)
    print(f"【Q1-3 行业组合】{len(sec_rets)} 行业  ρ̄={rho3:.4f}  N_eff={n3:.2f}"
          f"  → 行业不是独立维度")

    # ── Q2：分散 vs 集中（同窗口同规则）───────────────────────────
    print("\n" + "=" * 74)
    print("Q2 分散化到底带来了什么：20 只跨行业 vs 11 只资源池（同窗口等权满仓）")
    print("=" * 74)
    r_div = [statistics.fmean([rets[c][i] for c in avail]) for i in range(T)]
    r_old = [statistics.fmean([rets_all[c][i] for c in old_avail]) for i in range(T)]
    p_div, p_old = perf(r_div), perf(r_old)
    print(f"  {'组合':22s} {'累计':>9s} {'年化':>8s} {'波动':>7s} {'回撤':>8s} {'Calmar':>7s}")
    for lab, p in (("20 只 12 行业", p_div), ("11 只资源池", p_old)):
        print(f"  {lab:22s} {p['total'] * 100:+8.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['vol'] * 100:6.2f}% {p['mdd'] * 100:+7.2f}% {p['calmar']:7.2f}")
    print(f"  → 回撤改善 {abs(p_old['mdd']) * 100:.1f}% → {abs(p_div['mdd']) * 100:.1f}% "
          f"（{(abs(p_div['mdd']) / abs(p_old['mdd']) - 1) * 100:+.1f}%），"
          f"年化 {p_div['cagr'] * 100 - p_old['cagr'] * 100:+.2f}pp")
    print(f"  ⚠️ 注意：N_eff 几乎没变（{n1:.2f} vs 11 只的 1.78），但回撤明显改善 →")
    print(f"      N_eff 只测线性相关，**测不到尾部同时性**，不能单独用来判\"分不分散\"。")

    # ── Q3：滚动 beta（禁前视）────────────────────────────────────
    print("\n" + "=" * 74)
    print("Q3 滚动 beta（36 月）——v2 用全样本 beta 是前视偏差（E3）")
    print("=" * 74)
    mkt = r_div
    betas_roll: dict[int, dict[str, float]] = {}
    for t in range(MIN_OBS, T):
        lo = max(0, t - BETA_WIN)
        betas_roll[t] = {}
        for c in avail:
            _, b = ols(rets[c][lo:t], mkt[lo:t])
            betas_roll[t][c] = b
    first = min(betas_roll)
    print(f"  首次可用月 = 第 {first + 1} 个月（{dates[first + 1]}），评估 {T - first} 个月")

    def bt_weights(wfun, start):
        """wfun(t) -> {code: weight}；t 月权重用于 t+1 月收益（禁前视）。"""
        r, b_hist = [], []
        for t in range(start, T - 1):
            w = wfun(t)
            r.append(sum(w[c] * rets[c][t + 1] for c in avail))
            b_hist.append(sum(w[c] * betas_roll[t][c] for c in avail))
        return r, b_hist

    w_eq = lambda t: {c: 1 / N for c in avail}
    # 滚动 beta 倒数加权
    def w_invbeta(power):
        def f(t):
            iw = {c: 1.0 / max(betas_roll[t][c], 0.15) ** power for c in avail}
            s = sum(iw.values())
            return {c: iw[c] / s for c in avail}
        return f
    # 滚动 beta 最低 k 只
    def w_lowbeta(k):
        def f(t):
            order = sorted(avail, key=lambda c: betas_roll[t][c])[:k]
            return {c: (1 / k if c in order else 0.0) for c in avail}
        return f

    r_eq, b_eq = bt_weights(w_eq, first)
    p_eq = perf(r_eq)
    print(f"\n  {'策略':26s} {'年化':>8s} {'波动':>7s} {'回撤':>8s} {'Calmar':>7s} {'平均β':>6s}")
    print(f"  {'等权满仓(滚动期)':26s} {p_eq['cagr'] * 100:+7.2f}% {p_eq['vol'] * 100:6.2f}% "
          f"{p_eq['mdd'] * 100:+7.2f}% {p_eq['calmar']:7.2f} {statistics.fmean(b_eq):6.2f}")
    rows = [("等权满仓(滚动期)", p_eq, statistics.fmean(b_eq))]
    for power in (0.5, 1.0, 1.5, 2.0):
        r, bb = bt_weights(w_invbeta(power), first)
        p = perf(r)
        rows.append((f"1/beta^{power:.1f} 加权", p, statistics.fmean(bb)))
        print(f"  1/beta^{power:.1f} 加权".ljust(28) +
              f"{p['cagr'] * 100:+7.2f}% {p['vol'] * 100:6.2f}% {p['mdd'] * 100:+7.2f}% "
              f"{p['calmar']:7.2f} {statistics.fmean(bb):6.2f}")
    for k in (5, 8, 12):
        r, bb = bt_weights(w_lowbeta(k), first)
        p = perf(r)
        rows.append((f"最低 beta {k} 只", p, statistics.fmean(bb)))
        print(f"  最低 beta {k:2d} 只".ljust(28) +
              f"{p['cagr'] * 100:+7.2f}% {p['vol'] * 100:6.2f}% {p['mdd'] * 100:+7.2f}% "
              f"{p['calmar']:7.2f} {statistics.fmean(bb):6.2f}")

    # ── Q4：20% 回撤约束下的可达年化 + 超额 ──────────────────────
    print("\n" + "=" * 74)
    print(f"Q4 回撤上限 {TARGET_MDD * 100:.0f}% 约束下的可达年化（现金 0 收益假设）")
    print("=" * 74)
    print(f"  {'策略':26s} {'需仓位':>7s} {'年化':>8s} {'回撤':>8s} {'Calmar':>7s}")
    best = []
    for lab, p, beta in rows:
        s = TARGET_MDD / abs(p["mdd"]) if p["mdd"] else 1.0
        s = min(s, 1.0)
        cg = s * p["cagr"]          # 现金 0 收益
        md = s * p["mdd"]
        print(f"  {lab:26s} {s * 100:6.1f}% {cg * 100:+7.2f}% {md * 100:+7.2f}% "
              f"{cg / abs(md) if md else float('nan'):7.2f}")
        best.append((cg, lab))
    best.sort(reverse=True)
    base_cg = next(cg for cg, lab in best if lab.startswith("等权满仓"))
    print(f"\n  最优：{best[0][1]} 年化 {best[0][0] * 100:+.2f}%")
    print(f"  基准：等权满仓压到同回撤 {base_cg * 100:+.2f}%")
    print(f"  → 超额 {best[0][0] * 100 - base_cg * 100:+.2f}pp/年"
          f"（判据要求 ≥ +2.00pp）")

    # ── 样本内外切分 ─────────────────────────────────────────────
    i_split = next(i for i, d in enumerate(dates) if d >= "20210101")
    print(f"\n【样本内外切分：内 {dates[0]}→{dates[i_split - 1]} / "
          f"外 {dates[i_split]}→{dates[-1]}】")
    print(f"  {'策略':26s} {'内年化':>9s} {'外年化':>9s} {'差':>8s}")
    for lab, wf in (("等权满仓", w_eq), ("1/beta^1.0", w_invbeta(1.0)),
                    ("最低 beta 5 只", w_lowbeta(5))):
        seg = []
        for lo, hi in ((first, i_split - 1), (i_split, T - 1)):
            r = [sum(wf(t)[c] * rets[c][t + 1] for c in avail) for t in range(lo, hi)]
            seg.append(perf(r)["cagr"] if len(r) > 5 else float("nan"))
        print(f"  {lab:26s} {seg[0] * 100:+8.2f}% {seg[1] * 100:+8.2f}% "
              f"{(seg[1] - seg[0]) * 100:+7.2f}pp")

    # ── beta 与 alpha 的关系 ─────────────────────────────────────
    print("\n【低 beta 异象检验：corr(beta, alpha)】")
    fb = {c: ols(rets[c], mkt)[1] for c in avail}
    fa = {c: statistics.fmean([rets[c][i] - fb[c] * mkt[i] for i in range(T)]) for c in avail}
    r_ba = D.corr([fb[c] for c in avail], [fa[c] for c in avail])
    print(f"  corr(beta, alpha) = {r_ba:+.4f}  → "
          f"{'低 beta 高 alpha，beta 加权有真实 alpha' if r_ba < -0.2 else '无明显关系，beta 加权只是降风险不增收益'}")
    lo_b = sorted(avail, key=lambda c: fb[c])[:7]
    hi_b = sorted(avail, key=lambda c: fb[c])[-7:]
    print(f"  低 beta 7 只：β均 {statistics.fmean([fb[c] for c in lo_b]):.2f}  "
          f"alpha均 {statistics.fmean([fa[c] for c in lo_b]) * 100:+.3f}%/月")
    print(f"  高 beta 7 只：β均 {statistics.fmean([fb[c] for c in hi_b]):.2f}  "
          f"alpha均 {statistics.fmean([fa[c] for c in hi_b]) * 100:+.3f}%/月")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "diversify_decomp_v3.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "window": [dates[0], dates[-1]], "T": T, "N": N,
            "L1": {"rho": rho1, "n_eff": n1, "pc1": lam / N},
            "L2": {"rho": rho2, "n_eff": n2, "capped": cap2},
            "L3": {"n_sec": len(sec_rets), "rho": rho3, "n_eff": n3},
            "diversified_vs_concentrated": {"diverse": p_div, "concentrated": p_old},
            "rolling_beta": {"win": BETA_WIN, "start": first,
                             "rows": [{"label": l, **p, "beta": b} for l, p, b in rows]},
            "corr_beta_alpha": r_ba,
            "errors": ["E1 等权市场代理污染残差（已改留一法）",
                       "E2 N_eff 无界（已截断 ≤N）",
                       "E3 全样本 beta 前视（已改滚动 36 月）"],
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
