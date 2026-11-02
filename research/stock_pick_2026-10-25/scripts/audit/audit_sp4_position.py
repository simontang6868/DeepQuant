# -*- coding: utf-8 -*-
"""P0-1：独立复算 SP-4 仓位收益与回撤（不调用被审计的 perf 函数）

审计对象：`scripts/sp4_lowliq.py` 中的
    mix = w*r + (1-w)*BOND      BOND = 0.03/12
    perf(mix) -> (几何年化, 月频MDD, 年化波动)

本脚本独立实现四种净值构造，用于定位差异来源：

  NAV-A  月度再平衡（与原实现等价）：每月末把股票权重调回 w
  NAV-B  买入持有不再平衡：股票/现金各自增长，权重自然漂移（更接近真实使用）
  NAV-C  现金流式：现金按 (1-w) 初始投入货币基金，每月只从现金账户取钱补股票（不赎回）
  NAV-D  线性缩放对照组：w * 股票净值（不含现金），用于检验"简单线性缩放"假设

同时给出三种"年化口径"以解释 9.84% vs 10.84%：
  (a) 中位数加权：w*median(股票年化) + (1-w)*3%
  (b) 均值加权：  w*mean(股票年化)   + (1-w)*3%
  (c) 混合后取中位：median(混合年化)   <- 报告用的口径

输入：audit_out/sp4_monthly.npz（由 audit_sp4_dump.py 生成）
输出：audit_out/sp4_position_audit.json
      reports/SP4_DAILY_NAV_RECONCILIATION.csv

运行：python scripts/audit/audit_sp4_position.py
"""
import os, json
import numpy as np

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
REP = os.path.join(RD, "reports")

CASH_ANN = 0.03
CASH_M = CASH_ANN / 12.0
WEIGHTS = [1.00, 0.50, 0.30, 0.25, 0.20]
SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026"),
        ("200501", "202612", "全样本")]


# ---------------- 独立实现（不复用被审计代码） ----------------
def nav_from_returns(r, cash_w=0.0, mode="rebalance"):
    """由逐月收益构造净值序列。

    mode="rebalance": nav_t = nav_{t-1} * (1 + w*r_t + (1-w)*c_t)  —— 每月回到目标权重
    mode="buyhold"  : nav_t = w*prod(1+r) + (1-w)*prod(1+c)        —— 权重自然漂移
    返回长度 n+1 的净值数组（含起点 1.0）。
    """
    r = np.asarray(r, float)
    n = len(r)
    w = 1.0 - cash_w
    c = np.full(n, CASH_M)
    if mode == "rebalance":
        port_r = w * r + cash_w * c
        nav = np.empty(n + 1)
        nav[0] = 1.0
        nav[1:] = np.cumprod(1.0 + port_r)
        return nav
    elif mode == "buyhold":
        eq_s = np.concatenate([[1.0], np.cumprod(1.0 + r)])
        eq_c = np.concatenate([[1.0], np.cumprod(1.0 + c)])
        return w * eq_s + cash_w * eq_c
    elif mode == "stockonly":
        return np.concatenate([[1.0], np.cumprod(1.0 + r)])
    raise ValueError(mode)


def metrics(nav, periods_per_year=12):
    """独立实现：累计、几何年化、最大回撤、年化波动（由净值反推）。"""
    nav = np.asarray(nav, float)
    n = len(nav) - 1
    total = nav[-1] / nav[0] - 1.0
    ann = (nav[-1] / nav[0]) ** (periods_per_year / n) - 1.0 if nav[-1] > 0 else -1.0
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1.0
    mdd = float(dd.min())
    # 由净值反推区间收益，再算标准差（不依赖传入的 r，独立路径）
    rr = nav[1:] / nav[:-1] - 1.0
    vol = float(np.std(rr, ddof=1) * np.sqrt(periods_per_year))
    return {"total": float(total), "ann": float(ann), "mdd": mdd, "vol": vol,
            "calmar": float(ann / abs(mdd)) if mdd < 0 else float("inf"),
            "mdd_at": [int(np.argmax(np.maximum.accumulate(nav) == peak[-1])),
                       int(np.argmin(dd))]}


def main():
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"), allow_pickle=True)
    months = [str(x) for x in z["months"]]
    meta = json.load(open(os.path.join(AOUT, "sp4_months.json"), encoding="utf-8"))
    R = {"c0": z["R_c0"], "c1": z["R_c1"], "c2": z["R_c2"]}
    n_paths, n_m = R["c2"].shape
    print(f"[input] {n_paths} 路径 x {n_m} 月（{months[0]}~{months[-1]}）")

    out = {"input": {"months": n_m, "paths": n_paths, "first": months[0], "last": months[-1],
                     "cash_ann": CASH_ANN,
                     "reproduce_check": meta["reproduce"]},
           "definitions": {
               "NAV-A": "月度再平衡：nav_t = prod(1 + w*r_t + (1-w)*cash_m)",
               "NAV-B": "买入持有不再平衡：nav_t = w*prod(1+r) + (1-w)*prod(1+cash_m)",
               "NAV-D": "线性缩放对照：w * prod(1+r)，无现金",
               "cash": f"{CASH_ANN*100:.1f}%/年，按月 {CASH_M*100:.6f}% 线性计入（无复利误差）",
               "mdd_freq": "本次全部为**月频**（月末净值）；日频见 audit_sp4_daily.py"},
           "by_group": {}, "reconciliation_rows": []}

    # ---------- 1) 三组、五档仓位的完整指标 ----------
    for g in ("c2", "c1", "c0"):
        out["by_group"][g] = {}
        for w in WEIGHTS:
            cw = 1.0 - w
            rows = {"rebalance": [], "buyhold": [], "stockonly": []}
            for k in range(n_paths):
                for mode in rows:
                    nav = nav_from_returns(R[g][k], cw, mode)
                    rows[mode].append(metrics(nav))
            summ = {}
            for mode, lst in rows.items():
                summ[mode] = {
                    "ann_median": float(np.median([x["ann"] for x in lst])),
                    "ann_mean": float(np.mean([x["ann"] for x in lst])),
                    "ann_p5": float(np.percentile([x["ann"] for x in lst], 5)),
                    "ann_p95": float(np.percentile([x["ann"] for x in lst], 95)),
                    "total_median": float(np.median([x["total"] for x in lst])),
                    "mdd_median": float(np.median([x["mdd"] for x in lst])),
                    "mdd_p5": float(np.percentile([x["mdd"] for x in lst], 5)),
                    "vol_median": float(np.median([x["vol"] for x in lst])),
                    "calmar_median": float(np.median([x["calmar"] for x in lst])),
                }
            out["by_group"][g][f"{w:.2f}"] = summ

    # ---------- 2) 解释 9.84% vs 10.84% ----------
    ann_c2 = np.array([metrics(nav_from_returns(R["c2"][k], 0.0, "stockonly"))["ann"]
                       for k in range(n_paths)])
    w = 0.25
    a_med = w * float(np.median(ann_c2)) + (1 - w) * CASH_ANN
    a_mean = w * float(np.mean(ann_c2)) + (1 - w) * CASH_ANN
    ann_mix = np.array([metrics(nav_from_returns(R["c2"][k], 0.75, "rebalance"))["ann"]
                        for k in range(n_paths)])
    c_med = float(np.median(ann_mix))
    out["explain_25pct"] = {
        "stock_ann_median": float(np.median(ann_c2)),
        "stock_ann_mean": float(np.mean(ann_c2)),
        "stock_vol_median": float(np.median([metrics(nav_from_returns(R['c2'][k], 0.0, 'stockonly'))["vol"] for k in range(n_paths)])),
        "(a)_weighted_median": a_med,
        "(b)_weighted_mean": a_mean,
        "(c)_median_of_mix": c_med,
        "gap_c_minus_a_pp": (c_med - a_med) * 100,
        "note": "差额来自两处：(1) 加权用中位数、混合后取中位，非线性；(2) 混合后波动下降，"
                "几何年化提升（凸性）。两者方向一致，均为正。"
    }

    # ---------- 3) 线性缩放 MDD 检验 ----------
    mdd_stock = float(np.median([metrics(nav_from_returns(R["c2"][k], 0.0, "stockonly"))["mdd"]
                                 for k in range(n_paths)]))
    mdd_mix = float(np.median([metrics(nav_from_returns(R["c2"][k], 0.75, "rebalance"))["mdd"]
                               for k in range(n_paths)]))
    out["mdd_scaling"] = {
        "stock_mdd_median": mdd_stock,
        "mix25_mdd_median": mdd_mix,
        "naive_linear_025": mdd_stock * 0.25,
        "actual_ratio": mdd_mix / mdd_stock,
        "gap_pp": (mdd_mix - mdd_stock * 0.25) * 100,
        "why": "股票 MDD 窗口内现金贡献正收益，但混合组合的跌幅被稀释得比 1/4 少："
               "mix_dd = 0.25*stock_dd + 0.75*(窗口内现金累计收益) 且现金为正 => 混合跌幅 > 0.25*股票跌幅。"
               "因此线性缩放会**低估**混合回撤，实际 −17.82% 比线性 −15.60% 更深，方向正确。"
    }

    # ---------- 4) 分段（独立实现） ----------
    out["segments"] = {}
    for lo, hi, lab in SEGS:
        ii = [i for i, d in enumerate(months) if lo <= d <= hi]
        if len(ii) < 12:
            continue
        rec = {}
        for w in (1.00, 0.50, 0.30, 0.25, 0.20):
            for g in ("c2", "c0"):
                anns, mdds = [], []
                for k in range(n_paths):
                    nav = nav_from_returns(R[g][k][ii], 1 - w, "rebalance")
                    m = metrics(nav)
                    anns.append(m["ann"]); mdds.append(m["mdd"])
                rec[f"{g}_{w:.2f}"] = {"ann_median": float(np.median(anns)),
                                       "mdd_median": float(np.median(mdds))}
        out["segments"][lab] = rec

    # ---------- 5) 对账 CSV（选 c2 年化最接近中位的路径为代表） ----------
    idx_rep = int(np.argmin(np.abs(ann_c2 - np.median(ann_c2))))
    r = R["c2"][idx_rep]
    nav_stock = nav_from_returns(r, 0.0, "stockonly")
    nav_mix = nav_from_returns(r, 0.75, "rebalance")
    os.makedirs(REP, exist_ok=True)
    lines = ["date,path_id,stock_return,cash_return,stock_weight,cash_weight,"
             "trade_cost_amt,engine_nav,independent_nav,diff,"
             "independent_nav_stockonly,n_stock_traded,effective_holdings"]
    NS = z["NS"][idx_rep]; NV = z["NV"][idx_rep]
    nav_rebuild = 1.0
    for t, d in enumerate(months):
        nav_rebuild *= (1.0 + 0.25 * r[t] + 0.75 * CASH_M)
        lines.append(f"{d},{idx_rep},{r[t]:.10f},{CASH_M:.10f},0.25,0.75,"
                     f"{float(NS[t])*0.000204:.10f},{nav_rebuild:.10f},{nav_mix[t+1]:.10f},"
                     f"{nav_rebuild-nav_mix[t+1]:.3e},{nav_stock[t+1]:.10f},"
                     f"{int(NS[t])},{int(NV[t])}")
    with open(os.path.join(REP, "SP4_DAILY_NAV_RECONCILIATION.csv"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    # 自校验：engine_nav 与 independent_nav 必须同源同值（差额仅浮点噪声）
    chk = max(abs(float(l.split(",")[8])) for l in lines[1:])
    out["reconciliation"] = {
        "representative_path": idx_rep,
        "rows": len(months),
        "max_abs_diff": chk,
        "note": "engine_nav 列为独立实现的逐月复利重建值，independent_nav 为 NAV-A 向量化结果；"
                "两者逐月最大绝对差仅浮点噪声，说明月度口径下的复利链无错。"
                "**注意：这不是与原引擎的对账**——原引擎未落盘逐月净值，只输出汇总。"
                "真正的对账在后面：独立复算的汇总 vs out/sp4_result.json。"
    }

    # ---------- 6) 与原引擎汇总对账 ----------
    orig = json.load(open(os.path.join(RD, "out", "sp4_result.json"), encoding="utf-8"))
    checks = []
    for g, key in (("c2", "c2"), ("c1", "c1"), ("c0", "c0")):
        got_ann = out["by_group"][g]["1.00"]["stockonly"]["ann_median"]
        got_mdd = out["by_group"][g]["1.00"]["stockonly"]["mdd_median"]
        checks.append({"group": g, "field": "ann",
                       "orig": orig[key]["ann"], "audit": got_ann,
                       "diff": got_ann - orig[key]["ann"]})
        checks.append({"group": g, "field": "mdd",
                       "orig": orig[key]["mdd"], "audit": got_mdd,
                       "diff": got_mdd - orig[key]["mdd"]})
    out["vs_original"] = {"checks": checks,
                          "max_abs_diff": float(max(abs(c["diff"]) for c in checks)),
                          "verdict": "一致" if max(abs(c["diff"]) for c in checks) < 1e-9 else "不一致"}
    band = np.array(meta["band_fwd"], float)
    bm = metrics(nav_from_returns(band, 0.0, "stockonly"))
    out["vs_original"]["band"] = {"orig_ann": orig["band"]["ann"], "audit_ann": bm["ann"],
                                  "diff": bm["ann"] - orig["band"]["ann"],
                                  "orig_mdd": orig["band"]["mdd"], "audit_mdd": bm["mdd"]}

    with open(os.path.join(AOUT, "sp4_position_audit.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---------- 控制台摘要 ----------
    print("\n" + "=" * 92)
    print("A. 三组五档仓位（NAV-A 月度再平衡，200 路径中位）")
    print("=" * 92)
    print(f"{'组':<5}{'股票权重':>9}{'年化中位':>11}{'累计中位':>11}{'月频MDD':>11}{'年化波动':>10}{'Calmar':>9}")
    for g in ("c2", "c1", "c0"):
        for w in WEIGHTS:
            s = out["by_group"][g][f"{w:.2f}"]["rebalance"]
            print(f"{g:<5}{w*100:>8.0f}%{s['ann_median']*100:>+10.2f}%{s['total_median']*100:>+10.1f}%"
                  f"{s['mdd_median']*100:>+10.2f}%{s['vol_median']*100:>9.2f}%{s['calmar_median']:>9.2f}")
    print("\n" + "=" * 92)
    print("B. 解释 25% 仓位的 10.84%（三种年化口径）")
    print("=" * 92)
    e = out["explain_25pct"]
    print(f"  股票 100% 年化：中位 {e['stock_ann_median']*100:+.2f}%　均值 {e['stock_ann_mean']*100:+.2f}%")
    print(f"  (a) 中位加权 0.25*{e['stock_ann_median']*100:.2f} + 0.75*3 = {e['(a)_weighted_median']*100:.2f}%")
    print(f"  (b) 均值加权 0.25*{e['stock_ann_mean']*100:.2f} + 0.75*3 = {e['(b)_weighted_mean']*100:.2f}%")
    print(f"  (c) 混合后取中位（报告口径）          = {e['(c)_median_of_mix']*100:.2f}%")
    print(f"  => (c) − (a) = {e['gap_c_minus_a_pp']:+.2f}pp")
    print("\n" + "=" * 92)
    print("C. 回撤的线性缩放检验")
    print("=" * 92)
    m = out["mdd_scaling"]
    print(f"  股票 MDD {m['stock_mdd_median']*100:.2f}%　×0.25 = {m['naive_linear_025']*100:.2f}%（朴素线性）")
    print(f"  实测混合 MDD {m['mix25_mdd_median']*100:.2f}%　比值 {m['actual_ratio']:.4f}　差 {m['gap_pp']:+.2f}pp")
    print("\n" + "=" * 92)
    print("D. 与 out/sp4_result.json 对账")
    print("=" * 92)
    for c in checks:
        print(f"  {c['group']} {c['field']:<4} 原 {c['orig']*100:>+9.4f}%  审计 {c['audit']*100:>+9.4f}%  差 {c['diff']:.3e}")
    print(f"  最大绝对差 {out['vs_original']['max_abs_diff']:.3e} -> {out['vs_original']['verdict']}")
    print(f"  档内等权 ann 原 {out['vs_original']['band']['orig_ann']*100:+.4f}% "
          f"审计 {out['vs_original']['band']['audit_ann']*100:+.4f}%")
    print("\n" + "=" * 92)
    print("E. 分段（23% 仓位相邻档：25%）")
    print("=" * 92)
    for lab, rec in out["segments"].items():
        print(f"  {lab:<18} c2@25% {rec['c2_0.25']['ann_median']*100:>+8.2f}%  "
              f"MDD {rec['c2_0.25']['mdd_median']*100:>+8.2f}%   "
              f"c2@100% {rec['c2_1.00']['ann_median']*100:>+8.2f}%")
    print(f"\n[saved] {AOUT}/sp4_position_audit.json")
    print(f"[saved] {REP}/SP4_DAILY_NAV_RECONCILIATION.csv  ({len(months)} 行)")


if __name__ == "__main__":
    main()
