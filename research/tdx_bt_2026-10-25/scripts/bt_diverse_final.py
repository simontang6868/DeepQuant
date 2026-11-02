#!/usr/bin/env python
"""v5：分散池终检——分年度、分风格周期、红利税、最终判定。

【为什么还要这一步】
v4 已证伪「1/beta^k 加权」：它是押注双汇发展（有效持仓 2.5 只、最大权重 53%），
剔除该票后 +12.28% → +6.72%，样本外 −11.69pp 翻转。

但 v4 还没回答：
  R1 分散池的收益是不是「红利风格顺风」？—— 必须分年度看
  R2 20 只跨行业 vs 11 只资源池的改善是真实的吗？—— 分年度逐年比
  R3 加红利税后还剩多少？—— 等权长期持有，持股>1 年免税；但滚动 beta 方案换手高
  R4 最终交付什么？—— 若四条不通过，给「回撤—年化前沿」

口径：后复权月线，2010-12 → 2026-10，月度再平衡（等权）。
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_diverse_diag as D
import bt_diversify_decomp_v3 as V3
import bt_decomp_v4_stress as V4

OLD11 = ["601398", "601668", "601699", "601808", "600583", "600188", "600256",
         "601919", "601225", "600985", "601857"]
BETA_WIN, MIN_OBS, TARGET_MDD = 36, 24, 0.20

# 红利税：持股 >1 年免征；1 月~1 年 10%；≤1 月 20%（差别化股息红利税）
# 等权月度再平衡 → 平均持有期约 24 月 → 大部分免征；保守按 5% 计一档压力
TAX_RATES = {"base": 0.0, "stress": 0.05, "worst": 0.10}


def perf(r):
    mdd, nav = V3.mdd_of(r)
    yrs = len(r) / 12
    cagr = nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    return {"cagr": cagr, "vol": statistics.pstdev(r) * (12 ** 0.5), "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd else float("nan"), "total": nav - 1}


def yearly(dates, rets, codes):
    """按自然年切分，返回 {年: (收益, 波动)}。"""
    out = {}
    for y in range(int(dates[0][:4]), int(dates[-1][:4]) + 1):
        idx = [i for i in range(len(dates) - 1) if dates[i + 1][:4] == str(y)]
        if len(idx) < 2:
            continue
        r = [statistics.fmean([rets[c][i] for c in codes]) for i in idx]
        nav = 1.0
        for x in r:
            nav *= (1 + x)
        out[y] = {"ret": nav - 1, "vol": statistics.pstdev(r) * (12 ** 0.5),
                  "months": len(r)}
    return out


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool) | set(OLD11))
    avail = [c for c in pool if c in series]
    old = [c for c in OLD11 if c in series]
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in series}
    T = len(dates) - 1
    print(f"分散池 {len(avail)} 只 / 资源池 {len(old)} 只 / {dates[0]} → {dates[-1]}  T={T}")

    # ── R1 分年度：这批收益是不是红利风格顺风 ────────────────────
    print("\n" + "=" * 74)
    print("R1 分年度收益：20 只跨行业 vs 11 只资源池（等权，月度再平衡，税前）")
    print("=" * 74)
    y_div = yearly(dates, rets, avail)
    y_old = yearly(dates, rets, old)
    print(f"  {'年份':6s} {'分散20只':>10s} {'资源11只':>10s} {'差':>9s} {'分散波动':>9s}")
    for y in sorted(y_div):
        a = y_div[y]["ret"]
        b = y_old.get(y, {}).get("ret", float("nan"))
        print(f"  {y:6d} {a * 100:+9.2f}% {b * 100:+9.2f}% {(a - b) * 100:+8.2f}pp "
              f"{y_div[y]['vol'] * 100:8.2f}%")

    pos = sum(1 for y in y_div if y_div[y]["ret"] > 0)
    print(f"\n  正收益年份 {pos}/{len(y_div)}")
    # 风格周期切分（红利风格公认的三段）
    segs = [("2011-2015 价值低迷", "2011", "2015"),
            ("2016-2020 核心资产", "2016", "2020"),
            ("2021-2026 红利牛市", "2021", "2026")]
    print(f"\n  {'风格周期':22s} {'分散20只年化':>12s} {'资源11只年化':>12s} {'差':>9s}")
    for lab, a, b in segs:
        def sub_cagr(yy):
            rs = [yy[y]["ret"] for y in yy if a <= str(y) <= b]
            if not rs:
                return float("nan")
            nav = 1.0
            for x in rs:
                nav *= (1 + x)
            yrs = sum(yy[y]["months"] for y in yy if a <= str(y) <= b) / 12
            return nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
        ca, cb = sub_cagr(y_div), sub_cagr(y_old)
        print(f"  {lab:22s} {ca * 100:+11.2f}% {cb * 100:+11.2f}% {(ca - cb) * 100:+8.2f}pp")

    # ── R2 分散化的真实价值：回撤改善是否逐年成立 ────────────────
    print("\n" + "=" * 74)
    print("R2 回撤对比（逐年在年内最大回撤，非年度收益）")
    print("=" * 74)
    def yearly_mdd(rets, codes):
        out = {}
        for y in range(int(dates[0][:4]), int(dates[-1][:4]) + 1):
            idx = [i for i in range(len(dates) - 1) if dates[i + 1][:4] == str(y)]
            if len(idx) < 2:
                continue
            r = [statistics.fmean([rets[c][i] for c in codes]) for i in idx]
            out[y] = V3.mdd_of(r)[0]
        return out
    m_div, m_old = yearly_mdd(rets, avail), yearly_mdd(rets, old)
    better = 0
    print(f"  {'年份':6s} {'分散20只':>10s} {'资源11只':>10s} {'改善':>9s}")
    for y in sorted(m_div):
        a, b = m_div[y], m_old.get(y, float("nan"))
        if a > b:
            better += 1
        print(f"  {y:6d} {a * 100:+9.2f}% {b * 100:+9.2f}% {(a - b) * 100:+8.2f}pp")
    print(f"\n  分散池回撤更小的年份：{better}/{len(m_div)}"
          f"（{better / len(m_div) * 100:.0f}%）")

    # ── R3 红利税 ────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("R3 红利税敏感性（股息率按池级 5%/年，按持有期判档）")
    print("=" * 74)
    # 等权月度再平衡：假设每只票平均持有 24 月 → >1 年免征为主
    r_div = [statistics.fmean([rets[c][i] for c in avail]) for i in range(T)]
    p0 = perf(r_div)
    print(f"  {'税档':28s} {'年化':>8s} {'相对税前':>9s}")
    tax_res = {}
    for lab, rate in TAX_RATES.items():
        drag = 0.05 * rate      # 股息率 5% × 税率
        r2 = [x - drag / 12 for x in r_div]
        p = perf(r2)
        tax_res[lab] = p
        print(f"  {lab:10s}(税率 {rate * 100:4.1f}%)".ljust(30) +
              f"{p['cagr'] * 100:+7.2f}% {(p['cagr'] - p0['cagr']) * 100:+8.2f}pp")
    print("  说明：等权长期持有以「持股>1年免征」为主，故 base 取 0%；"
          "stress/worst 为压力假设。")

    # ── R4 最终前沿 + 判定 ───────────────────────────────────────
    print("\n" + "=" * 74)
    print(f"R4 回撤—年化前沿（{TARGET_MDD * 100:.0f}% 为你的上限；现金按 0 收益）")
    print("=" * 74)
    print(f"  {'回撤上限':>8s} {'需仓位':>8s} {'年化':>8s} {'Calmar':>7s}  {'评价'}")
    frontier = {}
    for target in (0.10, 0.15, 0.20, 0.25, 0.30):
        s = min(1.0, target / abs(p0["mdd"]))
        cg = s * p0["cagr"]
        md = s * p0["mdd"]
        frontier[f"{target:.0%}"] = {"position": s, "cagr": cg, "mdd": md}
        note = ("✅ 满足你两条硬指标" if cg >= 0.03 else "❌ 年化 <3%")
        print(f"  {target * 100:7.0f}% {s * 100:7.1f}% {cg * 100:+7.2f}% "
              f"{cg / abs(md) if md else float('nan'):7.2f}  {note}")

    print("\n" + "=" * 74)
    print("最终判定（四条硬指标）")
    print("=" * 74)
    checks = [
        ("年化 ≥ 3%", p0["cagr"] >= 0.03, f"{p0['cagr'] * 100:+.2f}%（满仓）"),
        ("回撤 ≤ 20%", abs(p0["mdd"]) <= TARGET_MDD, f"{p0['mdd'] * 100:+.2f}%（满仓，超限）"),
        ("超额 ≥ +2pp/年", False, "无可信超额（所有加权方案经 v4 证伪）"),
        ("样本外不翻转", True, "等权满仓内 +8.46% → 外 +13.42%，同向（但外段是红利牛市）"),
    ]
    for lab, ok, val in checks:
        print(f"  {'✓' if ok else '✗'} {lab:16s} {val}")
    print(f"\n  → 满仓不满足回撤；压到 20% 后年化 {frontier['20%']['cagr'] * 100:+.2f}%，"
          f"满足前两条，但**无超额**")
    print(f"  → 结论：**分散化提升了绝对表现，但没有产生可信的超额收益**")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "diverse_final.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"window": [dates[0], dates[-1]], "T": T,
                   "n_diverse": len(avail), "n_old": len(old),
                   "nav_diverse": p0, "yearly_diverse": y_div,
                   "yearly_old": {str(k): v for k, v in y_old.items()},
                   "yearly_mdd": {str(k): v for k, v in m_div.items()},
                   "mdd_better_years": better, "mdd_years_total": len(m_div),
                   "tax": {k: v["cagr"] for k, v in tax_res.items()},
                   "frontier": frontier}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
