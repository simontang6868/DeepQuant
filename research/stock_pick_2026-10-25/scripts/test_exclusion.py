# -*- coding: utf-8 -*-
"""阶段 3：排除式组合检验

阶段 2 发现：6 个因子的「低端」均无 alpha，「高端」全部显著为负
⇒ 这些是「排除型」信号，不是「选择型」信号。

检验三个问题：
  Q1 排除「最热 X%」后等权持有，能否提升组合表现？
  Q2 提升来自因子，还是来自「排除」这个动作本身？
      → 关键对照：随机排除同样比例（200 次重采样）
  Q3 只能买 5 只时还剩多少效果？

成本：月频全换 5 只 = 1.02%o × 12 ≈ 1.22%/年（毛收益口径另报）
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "out")

FACTOR = "f3"
XS = [0.10, 0.20, 0.30]
K = 5
N_RAND = 200
SEED = 20261026
SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026"),
        ("200501", "202612", "全样本")]


def perf(r):
    r = np.asarray([x for x in r if not np.isnan(x)], float)
    eq = np.cumprod(1 + r)
    yrs = len(r) / 12
    ann = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq)
    return {"n": len(r), "total": float(eq[-1] - 1), "annual": float(ann),
            "mdd": float(np.min(eq / peak - 1)),
            "vol": float(np.std(r, ddof=1) * np.sqrt(12))}


def main():
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel[FACTOR].notna() & panel["fwd20"].notna()]
    # 预分组（关键优化：避免在 200 次重采样里反复过滤 DataFrame）
    groups = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        groups[d] = (g["fwd20"].to_numpy(float), g[FACTOR].to_numpy(float))
    months = sorted(groups)
    print(f"样本 {len(months)} 月｜{sum(len(v[0]) for v in groups.values()):,} 行")
    print(f"排除因子 = {FACTOR}（换手加速度 v5/v60，越高越差）")

    rng = np.random.default_rng(SEED)

    base, top5 = {}, {}
    ex = {x: {} for x in XS}
    for d in months:
        fwd, f = groups[d]
        order = np.argsort(f)          # 升序：f 低 → 好
        base[d] = fwd.mean()
        top5[d] = fwd[order[:K]].mean()
        for x in XS:
            cut = int(np.floor(len(fwd) * (1 - x)))
            ex[x][d] = fwd[order[:cut]].mean()

    st_base = perf([base[d] for d in months])
    st_top5 = perf([top5[d] for d in months])
    print(f"\nBASE 全池等权：年化 {st_base['annual']*100:+.2f}%｜累计 {st_base['total']*100:+.1f}%"
          f"｜月频MDD {st_base['mdd']*100:.2f}%｜{st_base['n']} 月")

    # ---- 随机基线：随机选 5 只 ----
    r5 = []
    for _ in range(N_RAND):
        rr = [groups[d][0][rng.integers(0, len(groups[d][0]), K)].mean() for d in months]
        r5.append(perf(rr)["annual"])
    r5 = np.array(r5)

    # ---- 关键对照：随机排除同比例 ----
    print("\n" + "=" * 92)
    print("Q1/Q2 排除式组合 vs 随机排除（关键对照）")
    print("=" * 92)
    print(f"{'排除比例':>8}{'排除F3后':>12}{'随机排除中位':>14}{'随机5%~95%':>24}{'超过随机95%?':>14}")
    exstat = {}
    for x in XS:
        st = perf([ex[x][d] for d in months])
        anns = []
        for _ in range(N_RAND):
            rr = []
            for d in months:
                fwd = groups[d][0]
                cut = int(np.floor(len(fwd) * (1 - x)))
                idx = rng.choice(len(fwd), cut, replace=False)
                rr.append(fwd[idx].mean())
            anns.append(perf(rr)["annual"])
        anns = np.array(anns)
        p5, p95 = np.percentile(anns, 5), np.percentile(anns, 95)
        beat = st["annual"] > p95
        print(f"{int(x*100):>7}%{st['annual']*100:>11.2f}%{np.median(anns)*100:>13.2f}%"
              f"{f'[{p5*100:+.2f}%, {p95*100:+.2f}%]':>24}{'是 ✅' if beat else '否':>13}")
        exstat[str(x)] = {"annual": st["annual"], "total": st["total"], "mdd": st["mdd"],
                          "rand_median": float(np.median(anns)), "rand_p5": float(p5),
                          "rand_p95": float(p95), "beat": bool(beat)}

    print("\n" + "=" * 92)
    print("Q3 只买 5 只时还剩多少")
    print("=" * 92)
    print(f"  F3 最低 5 只 ：年化 {st_top5['annual']*100:+.2f}%｜MDD {st_top5['mdd']*100:.2f}%"
          f"｜累计 {st_top5['total']*100:+.1f}%")
    print(f"  随机 5 只    ：中位 {np.median(r5)*100:+.2f}%"
          f"｜5%~95% [{np.percentile(r5,5)*100:+.2f}%, {np.percentile(r5,95)*100:+.2f}%]")
    print(f"  全池等权     ：年化 {st_base['annual']*100:+.2f}%")
    print(f"  ⇒ F3 最低 5 只 vs 随机 5 只：{'超过 95% 分位 ✅' if st_top5['annual']>np.percentile(r5,95) else '未超过 95% 分位 ❌'}")

    print("\n" + "=" * 92)
    print("分段：BASE vs 排除 F3 最高 30%")
    print("=" * 92)
    print(f"{'区间':<20}{'BASE年化':>11}{'BASE MDD':>11}{'EX30年化':>11}{'EX30 MDD':>11}{'差':>10}")
    seg = {}
    for lo, hi, lab in SEGS:
        ds = [d for d in months if lo <= d <= hi]
        if len(ds) < 12:
            continue
        sb = perf([base[d] for d in ds])
        se = perf([ex[0.30][d] for d in ds])
        print(f"{lab:<20}{sb['annual']*100:>10.2f}%{sb['mdd']*100:>10.2f}%"
              f"{se['annual']*100:>10.2f}%{se['mdd']*100:>10.2f}%"
              f"{(se['annual']-sb['annual'])*100:>+9.2f}pp")
        seg[lab] = {"base_ann": sb["annual"], "base_mdd": sb["mdd"],
                    "ex30_ann": se["annual"], "ex30_mdd": se["mdd"],
                    "diff_pp": se["annual"] - sb["annual"]}

    os.makedirs(OUT, exist_ok=True)
    json.dump({"factor": FACTOR, "K": K, "base": st_base, "top5": st_top5,
               "exclusion": exstat,
               "random5": {"median": float(np.median(r5)),
                           "p5": float(np.percentile(r5, 5)),
                           "p95": float(np.percentile(r5, 95))},
               "segments": seg},
              open(os.path.join(OUT, "exclusion_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nsaved out/exclusion_result.json")


if __name__ == "__main__":
    main()
