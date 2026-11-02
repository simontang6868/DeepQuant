# -*- coding: utf-8 -*-
"""SP-5 / P1-2：行业暴露审计（2023-01 起）

要回答的问题
------------
P1-1 的归因显示：2023-01 起 44 个月里，收益的 **45.9% 来自「低流动性档」beta**，
F3 筛选只贡献 1.4%。但「低流动性档」如果集中在少数行业，这个 beta 可能只是
**某个行业行情的伪装**。本脚本检三件事：

1. **集中度**：策略持仓（每月 5 只）的行业分布有多集中；
2. **行业中性化后还剩多少**：把每只股票的超额换成「减去同月同行业均值」后，
   BM1/BM2/BM3 的超额是否还在；
3. **策略是不是押中了行业**：策略持仓的行业构成 vs 低流动性档的行业构成。

判据（预登记 §5.x 口径的延伸）
------------------------------
* `INDUSTRY_CONCENTRATED`：最大行业占组合权重 > 40%，或 HHI > 0.25；
* `INDUSTRY_NOT_DRIVER`：BM2 在行业中性口径下仍 > 0.3pp/月且 t > 1.5；
* `INDUSTRY_IS_DRIVER`：BM2 行业中性后 < 0.1pp/月，或衰减 > 70%；
* `INDUSTRY_CANNOT_VERIFY`：行业映射覆盖率 < 85%（数据不足，不得推断）。

⚠️ 前视偏差声明
--------------
行业分类是**当前时点**的东财细分行业，没有历史行业映射。
用它做 2023-2026 的归属存在前视偏差；缓解理由是行业归属相对稳定，
且本脚本只用于**集中度与中性化**，不用于择时。引用必须带上这一句。

输出：sp5_out/sp5_industry.json
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402
import p03_tradability as p03  # noqa: E402
import p11_exec_window as p11  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parents[2]
OUT = BASE / "sp5_out"
START = "20230131"
N_PATHS = int(os.environ.get("SP5_PATHS", "200"))
K = 5
MIN_GROUP = 3          # 行业内少于 3 只时，该行业当月均值噪声过大，标为 SPARSE


def load_ind() -> tuple[dict[str, str], dict]:
    d = json.loads((OUT / "industry_map.json").read_text(encoding="utf-8"))
    return d["code2ind"], d


def main() -> int:
    code2ind, meta = load_ind()
    panel = pd.read_parquet(p03.PANEL)
    panel = panel[panel["date"] >= START].copy()
    panel["ind"] = panel["code"].map(code2ind).fillna("__UNKNOWN__")

    cov = float((panel["ind"] != "__UNKNOWN__").mean())
    print(f"[coverage] 面板行覆盖 {cov*100:.2f}%（{panel['code'].nunique()} 只股票）")

    # ---------------- 1) 池与持仓的行业分布 ----------------
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")

    liq = p[p["liqpct"] < p03.LIQ_MAX]
    pool = liq[liq["f3pct"] <= p03.THRESH]
    print(f"[pool] 低流动性档 {len(liq):,} 行｜F3 池 {len(pool):,} 行")

    ind_share_liq = liq["ind"].value_counts(normalize=True)
    ind_share_pool = pool["ind"].value_counts(normalize=True)
    top_liq = ind_share_liq.head(10)

    # ---------------- 2) 行业中性化 ----------------
    # 每月每行业均值；组内 <MIN_GROUP 只时该行业标记 SPARSE（不进中性化样本）
    grp = p.groupby(["date", "ind"])["fwd20"]
    p["ind_mean"] = grp.transform("mean")
    p["ind_n"] = grp.transform("size")
    p["excess"] = np.where(p["ind_n"] >= MIN_GROUP, p["fwd20"] - p["ind_mean"], np.nan)

    p["f3pct2"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct2"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    liq2 = p[p["liqpct2"] < p03.LIQ_MAX]
    pool2 = liq2[liq2["f3pct2"] <= p03.THRESH]

    def month_means(df: pd.DataFrame, col: str) -> np.ndarray:
        return df.groupby("date")[col].mean().dropna().to_numpy(float)

    res: dict = {
        "window": {"start": panel["date"].min(), "end": panel["date"].max(),
                   "n_months": int(panel["date"].nunique())},
        "industry_source": meta.get("source"),
        "coverage_row": cov,
        "n_industries": int(p["ind"].nunique()),
        "verdict": [],
        "caveat": "行业分类为当前时点东财细分行业，无历史映射，存在前视偏差；仅用于集中度与中性化，不用于择时",
    }

    # 原始 vs 行业中性，三个口径对照
    table = {}
    for name, df in (("BM1_market", p), ("BM2_lowliq", liq2), ("BM3_pool", pool2)):
        raw = month_means(df, "fwd20")
        neu = month_means(df, "excess")
        s_raw, s_neu = p11.stats(raw), p11.stats(neu)
        table[name] = {"raw": s_raw, "industry_neutral": s_neu,
                       "decay": (1 - s_neu["mean"] / s_raw["mean"]) if s_raw["mean"] else None}
        print(f"[{name}] 原始 {s_raw['mean']*100:+.3f}pp (t={s_raw['t_iid']:+.2f})  →  "
              f"行业中性 {s_neu['mean']*100:+.3f}pp (t={s_neu['t_iid']:+.2f})  "
              f"衰减 {table[name]['decay']*100:.1f}%")
    res["table"] = table
    res["bm1_note"] = ("BM1 的全市场等权减去同月同行业均值后必然≈0（恒等式：全市场等权即各行业均值的加权），"
                       "t=−0.80 只是行业权重差异的残差。**这一格不是发现，是构造性质**，不得引用为「个股选择为零」。")

    # ---------------- 3) 策略持仓的行业集中度 ----------------
    by, months, paths, _, _ = p03.build_decisions(panel, N_PATHS)
    rows = []
    for t, m in enumerate(months):
        # 每路径当月的持仓行业
        hhis, maxs, unk = [], [], []
        for holds in paths:
            inds = [code2ind.get(c, "__UNKNOWN__") for c in holds[t]]
            vc = pd.Series(inds).value_counts(normalize=True)
            hhis.append(float((vc ** 2).sum()))
            maxs.append(float(vc.iloc[0]))
            unk.append(sum(1 for x in inds if x == "__UNKNOWN__") / len(inds))
        rows.append({"month": m, "hhi_med": float(np.median(hhis)),
                     "max_ind_share_med": float(np.median(maxs)),
                     "unknown_share": float(np.mean(unk))})
    ind_df = pd.DataFrame(rows)
    res["concentration"] = {
        "hhi_med": float(ind_df["hhi_med"].median()),
        "hhi_p90": float(ind_df["hhi_med"].quantile(0.9)),
        "max_ind_share_med": float(ind_df["max_ind_share_med"].median()),
        "max_ind_share_p90": float(ind_df["max_ind_share_med"].quantile(0.9)),
        "notes": "HHI 用 5 只等权组合的行业权重；1 只独大 = 1.0，5 个行业各 20% = 0.2",
    }
    print(f"[concentration] HHI 中位 {res['concentration']['hhi_med']:.3f}"
          f"（p90 {res['concentration']['hhi_p90']:.3f}）｜最大行业占比中位 "
          f"{res['concentration']['max_ind_share_med']*100:.1f}%")

    # 策略持仓的行业构成（全部路径 × 全部月的能力池）
    all_hold = [c for holds in paths for hs in holds for c in hs]
    hold_ind = pd.Series([code2ind.get(c, "__UNKNOWN__") for c in all_hold]).value_counts(normalize=True)
    res["top_industries_held"] = {k: float(v) for k, v in hold_ind.head(10).items()}
    res["top_industries_lowliq"] = {k: float(v) for k, v in top_liq.items()}

    # 与低流动性档的分布差异（总变差距离）
    common = set(hold_ind.index) | set(ind_share_liq.index)
    tvd = 0.5 * sum(abs(hold_ind.get(k, 0.0) - ind_share_liq.get(k, 0.0)) for k in common)
    res["tvd_vs_lowliq"] = float(tvd)
    print(f"[dist] 策略持仓 vs 低流动性档 总变差距离 {tvd:.4f}"
          f"（0=完全一致，1=完全不重叠）")

    # 有效判据：策略相对全市场的行业偏离，是否与「低流动性筛本身」造成的偏离一样多？
    # 若两者接近 ⇒ 行业暴露完全继承自流动性筛选，F3 没有额外押行业。
    mk_share = p["ind"].value_counts(normalize=True)
    all_k = set(mk_share.index) | set(ind_share_liq.index) | set(hold_ind.index)
    tvd_str_mkt = 0.5 * sum(abs(hold_ind.get(k, 0.0) - mk_share.get(k, 0.0)) for k in all_k)
    tvd_liq_mkt = 0.5 * sum(abs(ind_share_liq.get(k, 0.0) - mk_share.get(k, 0.0)) for k in all_k)
    res["tvd"] = {"strategy_vs_market": float(tvd_str_mkt),
                  "lowliq_vs_market": float(tvd_liq_mkt),
                  "strategy_vs_lowliq": float(tvd),
                  "note": "策略相对市场的行业偏离若≈低流动性档相对市场的偏离，说明行业暴露来自流动性筛选，F3 未额外押注"}
    extra = tvd_str_mkt - tvd_liq_mkt
    res["f3_extra_industry_tilt"] = float(extra)
    print(f"[dist] 策略 vs 全市场 {tvd_str_mkt:.4f}｜低流动性档 vs 全市场 {tvd_liq_mkt:.4f}"
          f"｜F3 额外行业偏离 {extra:+.4f}")

    # ---------------- 判定 ----------------
    v = res["verdict"]
    if cov < 0.85:
        v.append("INDUSTRY_CANNOT_VERIFY")
    else:
        # 用「F3 相对流动性筛选的额外行业偏离」判定，不用 HHI（HHI 在细分行业下无判别力）
        if abs(extra) > 0.15:
            v.append("INDUSTRY_CONCENTRATED")
        else:
            v.append("INDUSTRY_DIVERSIFIED")
        bm2 = table["BM2_lowliq"]
        neu = bm2["industry_neutral"]["mean"]
        dec = bm2["decay"]
        if neu > 0.003 and bm2["industry_neutral"]["t_iid"] > 1.5:
            v.append("INDUSTRY_NOT_DRIVER")
        elif neu < 0.001 or (dec is not None and dec > 0.70):
            v.append("INDUSTRY_IS_DRIVER")
        else:
            v.append("INDUSTRY_PARTIAL")
    res["verdict"] = "|".join(v)

    (OUT / "sp5_industry.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[verdict] {res['verdict']}")
    print(f"[save] {OUT / 'sp5_industry.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
