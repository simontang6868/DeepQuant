# -*- coding: utf-8 -*-
"""P0-3 前置：可执行性审计（SP-4 的低流动性档在历史上真的能买到 5 只吗）

已发现线索：`ok_buy` 的 `amt20 > 500 万` 门槛在 2011-2012 把整个最低流动性档 100% 剔除。
本脚本量化：
  1) 分年度：档内只数、ok_buy 通过数、pool 大小、pool>=K 的月数、空仓月数
  2) 空仓月（vals 为空 -> 收益记 0）的实际占比与分布
  3) 分年度 C2 收益（剔除空仓月前后对比）
  4) "可用股票数" 与 "实际档内分位" 的关系（能买到的票是否仍是真·最小档）

运行：python scripts/audit/audit_sp4_tradability.py
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
K, THRESH, LIQ_MAX = 5, 0.70, 0.20


def main():
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"), allow_pickle=True)
    months = [str(x) for x in z["months"]]
    meta = json.load(open(os.path.join(AOUT, "sp4_months.json"), encoding="utf-8"))
    NS, NV, R2 = z["NS"], z["NV"], z["R_c2"]
    n_paths, n_m = R2.shape

    # ---------- 用面板复算每月 pool 大小与档内可用性 ----------
    p = pd.read_parquet(os.path.join(RD, "data", "panel_monthly.parquet"))
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    rows = []
    for d, g in p.groupby("date"):
        if d not in set(months):
            continue
        g2 = g[g["f3pct"].notna() & g["liqpct"].notna()]
        band = g2[g2["liqpct"] < LIQ_MAX]
        buyable = band[band["ok_buy"] & band["fwd20"].notna()]
        pool = buyable[buyable["f3pct"] <= THRESH]
        rows.append({"date": d,
                     "all_n": len(g2), "band_n": len(band), "buyable_n": len(buyable),
                     "pool_n": len(pool),
                     "band_ok_rate": (len(buyable) / len(band)) if len(band) else np.nan,
                     # 买得到的这批，其流动性分位的中位（越接近 0 越"真小盘"）
                     "buyable_liqpct_med": float(buyable["liqpct"].median()) if len(buyable) else np.nan,
                     "band_amt20_med_wan": float(band["amt20"].median() / 1e4) if len(band) else np.nan})
    tf = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    tf["yr"] = tf["date"].str[:4]
    tf["pool_short"] = tf["pool_n"] < K
    # 与已落盘的 pool_n 交叉校验
    mismatch = int((tf["pool_n"].to_numpy() != np.array(meta["n_pool"])).sum())
    print(f"[check] pool_n 与 meta 不一致的月数：{mismatch}")

    # ---------- 分年度汇总 ----------
    ann = []
    for yr, g in tf.groupby("yr"):
        idx = g.index.to_numpy()
        # 该年 c2 收益：跨路径中位
        r_yr = R2[:, idx]
        cagr = np.median([(np.prod(1 + r_yr[k])) ** (12 / len(idx)) - 1 for k in range(n_paths)])
        ann.append({"yr": yr, "months": len(g),
                    "band_n_med": int(g["band_n"].median()),
                    "buyable_n_med": int(g["buyable_n"].median()),
                    "band_ok_rate_med": float(g["band_ok_rate"].median()),
                    "pool_n_med": int(g["pool_n"].median()),
                    "pool_n_min": int(g["pool_n"].min()),
                    "months_pool_lt_K": int(g["pool_short"].sum()),
                    "c2_ann": float(cagr),
                    "band_amt20_med_wan": float(g["band_amt20_med_wan"].median())})
    adf = pd.DataFrame(ann)

    print("\n" + "=" * 100)
    print("A. 分年度可执行性（低流动性档 liq<20%）")
    print("=" * 100)
    print(f"{'年':<6}{'月数':>5}{'档内只数':>9}{'可买只数':>9}{'通过率':>8}{'pool中位':>9}{'pool最小':>9}"
          f"{'pool<5月':>9}{'档内日均额(万)':>15}{'C2年化':>9}")
    for _, r in adf.iterrows():
        print(f"{r['yr']:<6}{r['months']:>5}{r['band_n_med']:>9}{r['buyable_n_med']:>9}"
              f"{r['band_ok_rate_med']*100:>7.1f}%{r['pool_n_med']:>9}{r['pool_n_min']:>9}"
              f"{r['months_pool_lt_K']:>9}{r['band_amt20_med_wan']:>15.0f}{r['c2_ann']*100:>+8.2f}%")

    # ---------- 空仓月统计 ----------
    zero_mask = (NV == 0)          # 该月无任何有效持仓 -> 收益被记为 0.0
    print("\n" + "=" * 100)
    print("B. 空仓月（vals 为空，收益记为 0.0）的实际影响")
    print("=" * 100)
    n_zero_mp = int(zero_mask.sum())
    print(f"  月-路径层面：{n_zero_mp}/{zero_mask.size} = {n_zero_mp/zero_mask.size*100:.2f}%")
    zero_by_month = zero_mask.mean(axis=0)
    full_zero = [months[i] for i in range(n_m) if zero_by_month[i] > 0.5]
    print(f"  >=50% 路径空仓的月份：{len(full_zero)} 个")
    print(f"    {full_zero[:20]}")
    # 空仓月的市场收益对照
    mkt = []
    for d in months:
        g = p[(p["date"] == d) & p["fwd20"].notna()]
        mkt.append(float(g["fwd20"].mean()) if len(g) else 0.0)
    mkt = np.array(mkt)
    zm = zero_by_month > 0.5
    print(f"  空仓月的全市场等权月均收益：{mkt[zm].mean()*100:+.2f}%（n={zm.sum()}）")
    print(f"  非空仓月的全市场等权月均收益：{mkt[~zm].mean()*100:+.2f}%（n={(~zm).sum()}）")
    print(f"  => 若空仓月按市场收益计，累计影响 ≈ {((1+mkt[zm].mean())**zm.sum()-1)*100:+.2f}%")

    # ---------- 剔除早期不可执行期后的收益 ----------
    print("\n" + "=" * 100)
    print("C. 剔除「pool>=K 的月数不足」的早期年份后的 C2 收益")
    print("=" * 100)
    # 以 2015 年为界（2015-2026 通过率>99%）
    for lo, lab in (("2005", "全样本 2005-2026"), ("2015", "2015-2026"), ("2018", "2018-2026"), ("2022", "2022-2026")):
        ii = [i for i, d in enumerate(months) if d >= lo + "0101"]
        rr = R2[:, ii]
        cagr = np.median([(np.prod(1 + rr[k])) ** (12 / len(ii)) - 1 for k in range(n_paths)])
        eq = np.cumprod(1 + rr, axis=1)
        mdd = np.median([float(np.min(eq[k] / np.maximum.accumulate(eq[k]) - 1)) for k in range(n_paths)])
        print(f"  {lab:<22} 月数 {len(ii):>3}  C2 年化 {cagr*100:>+8.2f}%  月频MDD {mdd*100:>+8.2f}%")

    out = {"cross_check_pool_mismatch": mismatch,
           "by_year": adf.to_dict(orient="records"),
           "zero_position": {"share_month_path": float(zero_mask.mean()),
                             "n_full_zero_months": len(full_zero),
                             "full_zero_months": full_zero,
                             "market_ret_in_zero_months": float(mkt[zm].mean()),
                             "market_ret_otherwise": float(mkt[~zm].mean())},
           "segments_c2": {}}
    for lo, lab in (("2005", "full"), ("2015", "y2015"), ("2018", "y2018"), ("2022", "y2022")):
        ii = [i for i, d in enumerate(months) if d >= lo + "0101"]
        rr = R2[:, ii]
        out["segments_c2"][lab] = {
            "months": len(ii),
            "ann": float(np.median([(np.prod(1 + rr[k])) ** (12 / len(ii)) - 1 for k in range(n_paths)]))}
    with open(os.path.join(AOUT, "sp4_tradability.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    tf.to_csv(os.path.join(AOUT, "sp4_tradability_by_month.csv"), index=False, encoding="utf-8-sig")
    print(f"\n[saved] {AOUT}/sp4_tradability.json + sp4_tradability_by_month.csv")


if __name__ == "__main__":
    main()
