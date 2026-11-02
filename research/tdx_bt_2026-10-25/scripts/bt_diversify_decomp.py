#!/usr/bin/env python
"""分散化分解：行业分散到底解决了什么、没解决什么？

【为什么要做这一步】
上一版测出：12 行业 20 只，ρ̄ = 0.446、N_eff = 2.10——比 11 只资源池（1.88）只高 12%。
但"跨行业 ρ 比亚行业 ρ 低 0.164"又说行业分散确实有效。两个结论看似矛盾，
唯一解释是：**存在一个与行业无关的共同因子，把大部分相关性吃掉了。**

本脚本把相关性拆成三层：
  L1 原始收益相关      → N_eff_raw
  L2 剔除市场 beta 后的残差相关 → N_eff_resid（"选股层面"真正可分散的部分）
  L3 行业组合层面      → N_eff_sector（12 个行业等权组合之间）

判据：
  · 若 N_eff_resid >> N_eff_raw  → 分散有效，只是被 beta 淹没 → 解法是降 beta/加非权益资产
  · 若 N_eff_resid ≈ N_eff_raw   → 同风格同源，换行业没用 → 必须换风格

口径：后复权月线（tqFlag=2），共同窗口 2010-12 起（受 601098 上市日约束）。
"""
from __future__ import annotations
import json
import os
import statistics

import bt_diverse_diag as D


def ols(y: list[float], x: list[float]) -> tuple[float, float, list[float]]:
    """单变量 OLS：y = a + b·x + e。返回 (a, b, 残差序列)。"""
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((v - mx) * (w - my) for v, w in zip(x, y))
    b = sxy / sxx if sxx else 0.0
    a = my - b * mx
    return a, b, [w - a - b * v for v, w in zip(x, y)]


def var(v: list[float]) -> float:
    return statistics.pvariance(v)


def main() -> None:
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool))
    avail = [c for c in pool if c in series]
    missing = [c for c in pool if c not in series]
    print(f"池子 {len(pool)} 只 / 载入 {len(avail)} 只  缺 {missing}")

    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    print(f"共同月末 {len(dates)} 个  {dates[0]} → {dates[-1]}")

    # ── 窗口内数据自检（负值/零值会毁掉收益率计算）────────────────
    bad = []
    for c in avail:
        for d in dates:
            o, cl = series[c][d]
            if cl <= 0 or o <= 0:
                bad.append((c, d, o, cl))
    print(f"窗口内非正价格：{len(bad)} 处" + (f"  {bad[:5]}" if bad else "  ✓ 干净"))
    if bad:
        drop = sorted({b[0] for b in bad})
        print(f"  → 剔除数据受损标的 {drop}")
        avail = [c for c in avail if c not in drop]

    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in avail}
    n_obs = len(dates) - 1

    # ── 市场代理：等权池月收益 ────────────────────────────────────
    mkt = [statistics.fmean([rets[c][i] for c in avail]) for i in range(n_obs)]

    # ── L1 原始相关 ───────────────────────────────────────────────
    neff_raw, rho_raw, mat = D.neff(avail, rets)
    lam, vec = D.pc1(mat)
    print(f"\n【L1 原始收益】ρ̄={rho_raw:.4f}  N_eff={neff_raw:.2f}  "
          f"PC1={lam / len(avail) * 100:.1f}%  (N={len(avail)}, T={n_obs})")

    # ── L2 剔除市场 beta ──────────────────────────────────────────
    betas, alphas, resid = {}, {}, {}
    for c in avail:
        a, b, e = ols(rets[c], mkt)
        alphas[c], betas[c], resid[c] = a, b, e
    neff_res, rho_res, mat_res = D.neff(avail, resid)
    lam_r, _ = D.pc1(mat_res)
    print(f"【L2 残差(去 beta)】ρ̄={rho_res:.4f}  N_eff={neff_res:.2f}  "
          f"PC1={lam_r / len(avail) * 100:.1f}%")

    # 方差分解：个股方差有多少来自市场
    shares = []
    for c in avail:
        vt = var(rets[c])
        vm = betas[c] ** 2 * var(mkt)
        shares.append(vm / vt if vt else 0.0)
    print(f"  市场 beta 解释的个股方差：中位 {statistics.median(shares) * 100:.1f}%  "
          f"范围 {min(shares) * 100:.1f}%~{max(shares) * 100:.1f}%")

    print(f"\n  → N_eff 从 {neff_raw:.2f} 升到 {neff_res:.2f}"
          f"（{neff_res / neff_raw:.2f}×）")
    if neff_res > 2 * neff_raw:
        print("     判定：**行业分散有效，但被市场 beta 淹没**。解法是降 beta 或引入非权益资产。")
    else:
        print("     判定：**同风格同源，换行业没用**。残差层面依然高度相关 → 必须换风格。")

    # ── beta 分布 ─────────────────────────────────────────────────
    print("\n【beta 与 alpha（月）】")
    print(f"  {'行业':8s} {'代码':7s} {'名称':7s} {'beta':>6s} {'alpha月':>9s} {'市场解释':>8s}")
    for sec, cs in D.SECTORS.items():
        for c in cs:
            if c not in betas:
                continue
            print(f"  {sec:8s} {c:7s} {D.NAMES[c]:7s} {betas[c]:6.2f} "
                  f"{alphas[c] * 100:+8.3f}% {shares[avail.index(c)] * 100:7.1f}%")
    print(f"  {'等权':8s} {'':7s} {'':7s} {statistics.fmean(betas.values()):6.2f} "
          f"{statistics.fmean(alphas.values()) * 100:+8.3f}%")

    # ── L3 行业组合层面 ───────────────────────────────────────────
    sec_codes = {s: [c for c in cs if c in avail] for s, cs in D.SECTORS.items()}
    sec_codes = {s: cs for s, cs in sec_codes.items() if cs}
    sec_rets = {s: [statistics.fmean([rets[c][i] for c in cs]) for i in range(n_obs)]
                for s, cs in sec_codes.items()}
    neff_sec, rho_sec, _ = D.neff(list(sec_rets), sec_rets)
    print(f"\n【L3 行业等权组合】{len(sec_rets)} 个行业  ρ̄={rho_sec:.4f}  "
          f"N_eff={neff_sec:.2f}")
    # 行业组合也要去 beta
    mkt_sec = [statistics.fmean([sec_rets[s][i] for s in sec_rets]) for i in range(n_obs)]
    sec_res = {s: ols(sec_rets[s], mkt_sec)[2] for s in sec_rets}
    neff_sec_res, rho_sec_res, _ = D.neff(list(sec_res), sec_res)
    print(f"        去 beta 后       ρ̄={rho_sec_res:.4f}  N_eff={neff_sec_res:.2f}")

    # ── 结论：可分散 vs 不可分散 ──────────────────────────────────
    print("\n" + "=" * 70)
    print("分散化预算（把组合方差拆成三块）")
    print("=" * 70)
    port_var = var(mkt)          # 等权组合（近似市场）的方差
    avg_res_var = statistics.fmean([var(resid[c]) for c in avail])
    # 组合残差方差 ≈ 平均残差方差 / N * (1+(N-1)ρ_res)；再除以 N 得到"组合层面"
    contrib_beta = var(mkt)
    contrib_resid = avg_res_var / len(avail) * (1 + (len(avail) - 1) * rho_res)
    total = contrib_beta + contrib_resid
    print(f"  组合月方差 {total * 1e4:.1f} (bp²)")
    print(f"    市场 beta 贡献  {contrib_beta * 1e4:8.1f} bp²  {contrib_beta / total * 100:5.1f}%")
    print(f"    个股残差贡献    {contrib_resid * 1e4:8.1f} bp²  {contrib_resid / total * 100:5.1f}%")
    print(f"  → N 从 {len(avail)} 增加到无穷，最多只能消掉残差那 {contrib_resid / total * 100:.1f}%，")
    print(f"    剩余 {(1 - contrib_resid / total) * 100:.1f}% 是市场 beta，**加股票永远消不掉**。")

    # 年化
    print(f"\n  年化波动：组合 {total ** 0.5 * (12 ** 0.5) * 100:.2f}%  "
          f"（若残差部分归零则 {contrib_beta ** 0.5 * (12 ** 0.5) * 100:.2f}%）")

    outp = os.path.join("research", "tdx_bt_2026-10-25", "pool", "diversify_decomp.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "window": [dates[0], dates[-1]], "months": n_obs, "n": len(avail),
            "codes": avail, "missing": missing, "bad_prices": len(bad),
            "L1": {"rho": rho_raw, "n_eff": neff_raw, "pc1": lam / len(avail)},
            "L2": {"rho": rho_res, "n_eff": neff_res, "pc1": lam_r / len(avail)},
            "L3": {"n_sectors": len(sec_rets), "rho": rho_sec, "n_eff": neff_sec,
                   "rho_res": rho_sec_res, "n_eff_res": neff_sec_res},
            "betas": betas, "alphas": alphas,
            "mkt_share_median": statistics.median(shares),
            "var_decomp": {"total_bp2": total * 1e4, "beta_bp2": contrib_beta * 1e4,
                           "resid_bp2": contrib_resid * 1e4},
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
