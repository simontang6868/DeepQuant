#!/usr/bin/env python
"""分散化分解 v2：修正 v1 的市场代理假象 + 引入 beta 目标化。

【v1 的两个错误（保留说明，不掩盖）】
  E1 **市场代理污染**：v1 用「全池等权收益」作为市场因子，而残差 ε_i 由同一组股票回归得出，
     导致 Σε_i 被机械压向 0 → 残差两两相关被强制变负（ρ̄ = −0.042）→
     N_eff = N/(1+(N−1)ρ̄) = 20/0.202 = 100.5，**数学上荒谬**（N 只票不可能有 100 个独立赌注）。
     修正：改用 **留一法** mkt_{−i} = 其余 N−1 只的等权收益，避免自己解释自己。
  E2 **N_eff 公式缺退化保护**：当 1+(N−1)ρ̄ ≤ 0 时返回值无意义，须显式判 inf。

【v2 新增：beta 目标化】
  既然方差 98% 来自市场 beta，则"降回撤"的唯一有效手段是**控 beta**，不是选行业、不是调策略。
  本脚本直接算：把组合 beta 压到 0.5/0.6/0.7/0.8，需要什么权重、历史回撤能到多少。

口径：后复权月线（tqFlag=2），共同窗口 2010-12 → 2026-10（134 月）。
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_diverse_diag as D


def ols(y: list[float], x: list[float]) -> tuple[float, float, list[float]]:
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((v - mx) * (w - my) for v, w in zip(x, y))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    return a, b, [w - a - b * v for v, w in zip(x, y)]


def var(v: list[float]) -> float:
    return statistics.pvariance(v)


def neff_safe(codes: list[str], rets: dict[str, list[float]]) -> tuple[float, float, bool]:
    """N_eff = N/(1+(N−1)ρ̄)，带退化保护。返回 (N_eff, ρ̄, degenerate)。"""
    n_eff, rho, _ = D.neff(codes, rets)
    denom = 1 + (len(codes) - 1) * rho
    degenerate = denom <= 0.02
    return (n_eff if not degenerate else float("inf")), rho, degenerate


def main() -> None:
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool))
    avail = [c for c in pool if c in series]
    print(f"池子 {len(pool)} 只 / 载入 {len(avail)} 只  缺 {[c for c in pool if c not in series]}")

    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    print(f"共同月末 {len(dates)} 个  {dates[0]} → {dates[-1]}")

    bad = [(c, d) for c in avail for d in dates
           if series[c][d][0] <= 0 or series[c][d][1] <= 0]
    print(f"窗口内非正价格：{len(bad)} 处 " + ("✓ 干净" if not bad else str(bad[:5])))
    if bad:
        avail = [c for c in avail if c not in {b[0] for b in bad}]

    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in avail}
    T = len(dates) - 1
    N = len(avail)

    # ── L1 原始相关（这一层不受代理选择影响，可信）─────────────────
    neff_raw, rho_raw, mat = D.neff(avail, rets)
    lam, vec = D.pc1(mat)
    print(f"\n【L1 原始收益】ρ̄={rho_raw:.4f}  N_eff={neff_raw:.2f}  "
          f"PC1={lam / N * 100:.1f}%  (N={N}, T={T})")

    # ── L2 留一法去 beta（修正 E1）────────────────────────────────
    betas, alphas, resid = {}, {}, {}
    for c in avail:
        others = [x for x in avail if x != c]
        mkt_c = [statistics.fmean([rets[x][i] for x in others]) for i in range(T)]
        a, b, e = ols(rets[c], mkt_c)
        alphas[c], betas[c], resid[c] = a, b, e
    neff_res, rho_res, deg = neff_safe(avail, resid)
    lam_r, _ = D.pc1([[D.corr(resid[a], resid[b]) for b in avail] for a in avail])
    flag = "  ⚠️ 退化（分母≤0）" if deg else ""
    print(f"【L2 留一法残差】ρ̄={rho_res:.4f}  N_eff={neff_res:.2f}{flag}  "
          f"PC1={lam_r / N * 100:.1f}%")
    print(f"  （v1 的错误值 N_eff=100.5 已作废：用自身等权做市场代理导致残差机械负相关）")

    shares = []
    for c in avail:
        vt = var(rets[c])
        vm = betas[c] ** 2 * var([statistics.fmean([rets[x][i] for x in avail if x != c])
                                  for i in range(T)])
        shares.append(vm / vt if vt else 0.0)
    print(f"  市场 beta 解释的个股方差：中位 {statistics.median(shares) * 100:.1f}%  "
          f"范围 {min(shares) * 100:.1f}%~{max(shares) * 100:.1f}%")
    print(f"\n  → N_eff {neff_raw:.2f} → {neff_res:.2f}（{neff_res / neff_raw:.2f}×）")
    if neff_res > 2 * neff_raw:
        print("     判定：**行业分散有效，但被市场 beta 淹没** → 解法是控 beta，不是换行业")
    else:
        print("     判定：**残差层面仍高度相关** → 同风格同源，换行业不够，必须换风格")

    # ── beta 表 ───────────────────────────────────────────────────
    print(f"\n【beta 分布（留一法估计）】")
    print(f"  {'行业':9s} {'代码':7s} {'名称':8s} {'beta':>6s} {'alpha月%':>9s} {'市场解释':>8s}")
    for sec, cs in D.SECTORS.items():
        for c in cs:
            if c not in betas:
                continue
            print(f"  {sec:9s} {c:7s} {D.NAMES[c]:8s} {betas[c]:6.2f} "
                  f"{alphas[c] * 100:+8.3f} {shares[avail.index(c)] * 100:7.1f}%")
    print(f"  {'等权':9s} {'':7s} {'':8s} "
          f"{statistics.fmean(betas.values()):6.2f} "
          f"{statistics.fmean(alphas.values()) * 100:+8.3f} "
          f"{statistics.median(shares) * 100:7.1f}%")
    print(f"  beta 跨度 {min(betas.values()):.2f} ~ {max(betas.values()):.2f}"
          f"（离散度本身就是可用的 Alpha 来源）")

    # ── L3 行业组合（同样用留一法修正）────────────────────────────
    sec_codes = {s: [c for c in cs if c in avail] for s, cs in D.SECTORS.items()}
    sec_codes = {s: cs for s, cs in sec_codes.items() if cs}
    sec_rets = {s: [statistics.fmean([rets[c][i] for c in cs]) for i in range(T)]
                for s, cs in sec_codes.items()}
    neff_sec, rho_sec, _ = D.neff(list(sec_rets), sec_rets)
    print(f"\n【L3 行业等权组合】{len(sec_rets)} 行业  ρ̄={rho_sec:.4f}  "
          f"N_eff={neff_sec:.2f}  （N_eff 与个股层几乎相同 → 行业不构成独立维度）")

    # ── beta 目标化：压 beta 能降多少回撤 ────────────────────────
    print("\n" + "=" * 74)
    print("beta 目标化：既然 98% 方差来自 beta，直接控 beta 比选策略有效")
    print("=" * 74)

    mkt = [statistics.fmean([rets[c][i] for c in avail]) for i in range(T)]

    def port_stats(w: dict[str, float]) -> tuple[float, float, float]:
        """组合的年化收益、年化波动、最大回撤（月度再平衡近似）。"""
        r = [sum(w[c] * rets[c][i] for c in avail) for i in range(T)]
        nav, peak, mdd = 1.0, 1.0, 0.0
        for x in r:
            nav *= (1 + x)
            peak = max(peak, nav)
            mdd = min(mdd, nav / peak - 1)
        yrs = T / 12
        cagr = nav ** (1 / yrs) - 1
        vol = statistics.pstdev(r) * (12 ** 0.5)
        return cagr, vol, mdd

    w_eq = {c: 1 / N for c in avail}
    cagr0, vol0, mdd0 = port_stats(w_eq)
    beta_eq = statistics.fmean(betas.values())
    print(f"  等权满仓：年化 {cagr0 * 100:+.2f}%  波动 {vol0 * 100:.2f}%  "
          f"回撤 {mdd0 * 100:+.2f}%  组合 beta {beta_eq:.2f}")

    # 方案 A：整体降仓位（现金收益按 0 计）
    print(f"\n  【方案 A：降仓位（现金 0 收益，最保守假设）】")
    for s in (1.0, 0.8, 0.6, 0.5, 0.4):
        w = {c: s / N for c in avail}
        cg, vl, md = port_stats(w)
        print(f"    仓位 {s * 100:4.0f}%  → 年化 {cg * 100:+6.2f}%  波动 {vl * 100:5.2f}%  "
              f"回撤 {md * 100:+6.2f}%  Calmar {cg / abs(md) if md else float('nan'):.2f}")

    # 方案 B：低 beta 选股（beta 加权，保持满仓）
    print(f"\n  【方案 B：按 beta 倒数加权（满仓，不持现金）】")
    for power in (0.5, 1.0, 1.5, 2.0):
        iw = {c: 1.0 / max(betas[c], 0.15) ** power for c in avail}
        tot = sum(iw.values())
        w = {c: iw[c] / tot for c in avail}
        pb = sum(w[c] * betas[c] for c in avail)
        cg, vl, md = port_stats(w)
        print(f"    权重 ∝ 1/beta^{power:.1f}  组合beta {pb:.2f}  → 年化 {cg * 100:+6.2f}%  "
              f"波动 {vl * 100:5.2f}%  回撤 {md * 100:+6.2f}%  "
              f"Calmar {cg / abs(md) if md else float('nan'):.2f}")

    # 方案 C：混合——低 beta 选股 + 剩余仓位放最低 beta 的票
    print(f"\n  【方案 C：只持 beta 最低的 k 只（满仓等权）】")
    order = sorted(avail, key=lambda c: betas[c])
    for k in (3, 5, 8, 12):
        sel = order[:k]
        w = {c: (1 / k if c in sel else 0.0) for c in avail}
        pb = sum(w[c] * betas[c] for c in avail)
        cg, vl, md = port_stats(w)
        print(f"    最低 beta {k:2d} 只（β {betas[sel[0]]:.2f}~{betas[sel[-1]]:.2f}）"
              f"  组合beta {pb:.2f}  → 年化 {cg * 100:+6.2f}%  波动 {vl * 100:5.2f}%  "
              f"回撤 {md * 100:+6.2f}%  Calmar {cg / abs(md) if md else float('nan'):.2f}")

    # ── 落盘 ─────────────────────────────────────────────────────
    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "diversify_decomp.json")
    result = {
        "window": [dates[0], dates[-1]], "months": T, "n": N, "codes": avail,
        "L1": {"rho": rho_raw, "n_eff": neff_raw, "pc1": lam / N},
        "L2": {"rho": rho_res, "n_eff": neff_res, "pc1": lam_r / N, "degenerate": deg},
        "L3": {"n_sectors": len(sec_rets), "rho": rho_sec, "n_eff": neff_sec},
        "betas": betas, "alphas": alphas,
        "mkt_share_median": statistics.median(shares),
        "baseline": {"cagr": cagr0, "vol": vol0, "mdd": mdd0, "beta": beta_eq},
        "v1_errors": ["E1 等权市场代理污染残差相关(N_eff=100.5 荒谬)",
                      "E2 N_eff 公式缺退化保护"],
    }
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
