# -*- coding: utf-8 -*-
"""P0-1c：日频净值重建（月频 MDD 低估的量化）

原引擎的 perf() 用**月度**收益序列算 MDD。对波动 40% 的 5 只小盘股组合，
月频会系统性低估回撤。本脚本用落盘持仓 + 前复权日线重建日频净值。

口径：
  持仓期 = [ME_m 之后第 1 个交易日, 该日之后第 20 个交易日]，与 build_panel 的
  fwd20 定义完全一致（buy = ME+1，sell = buy+20）。
  月与月之间的空档（sell_m -> buy_{m+1}）按**0 收益**填充（视为空仓 1 日，保守）。
  持仓期内某只股票缺价 -> 前向填充（停牌价格不变，日收益 0）。
  换手成本仍在月末扣（与月频口径一致，金额 = n_swap * 0.0204%）。

输出：audit_out/sp4_daily_audit.json
      audit_out/sp4_daily_nav_p{path}.csv（前 3 条路径的日频净值）

运行：python scripts/audit/audit_sp4_daily.py
"""
import os, json, glob
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
QFQ = os.path.join(ROOT, "data", "qfq_all")
AOUT = os.path.join(RD, "audit_out")

CASH_M = 0.03 / 12
COST = 0.000204
HOLD = 20
CAL_BASE = ["000001.SZ", "600036.SH"]     # 交易日历基准（上市早、停牌少）


def build_code_index():
    idx = {}
    for p in glob.glob(os.path.join(QFQ, "*.qfq.csv")):
        base = os.path.basename(p).split(".")[0]
        idx[base] = p
    return idx


def load_calendar():
    ds = set()
    for b in CAL_BASE:
        p = os.path.join(QFQ, f"{b}.qfq.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, usecols=["date"])
        ds.update(df["date"].astype(str).str.replace("-", "", regex=False).tolist())
    return sorted(ds)


def load_prices(codes, idx):
    """{code: (dates_list, close_array)}，只读所需股票。"""
    out = {}
    miss = []
    for c in codes:
        p = idx.get(c)
        if p is None:
            miss.append(c); continue
        try:
            df = pd.read_csv(p, usecols=["date", "close"])
        except Exception:
            miss.append(c); continue
        df = df.dropna(subset=["close"])
        if len(df) == 0:
            miss.append(c); continue
        d = df["date"].astype(str).str.replace("-", "", regex=False).to_numpy()
        v = df["close"].to_numpy(float)
        out[c] = (d, v)
    return out, miss


def main():
    meta = json.load(open(os.path.join(AOUT, "sp4_months.json"), encoding="utf-8"))
    NS_arr = np.load(os.path.join(AOUT, "sp4_monthly.npz"))["NS"]     # (200, 257) 换手次数
    months = meta["months"]
    cal = load_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    print(f"[cal] {len(cal)} 交易日（{cal[0]} ~ {cal[-1]}），基准 {CAL_BASE}")

    # ---- 收集所有落盘路径的持仓 ----
    paths = {}
    for f in sorted(glob.glob(os.path.join(AOUT, "sp4_holdings_p*.json"))):
        pid = int(os.path.basename(f).split("_p")[1].split(".")[0])
        paths[pid] = json.load(open(f, encoding="utf-8"))
    print(f"[hold] {len(paths)} 条路径")

    need = set()
    for pid, h in paths.items():
        for g in ("c2", "c0"):
            for mh in h[g]:
                need.update(mh)
    print(f"[need] 去重后 {len(need)} 只股票")
    idx = build_code_index()
    px, miss = load_prices(sorted(need), idx)
    print(f"[px] 读到 {len(px)} 只，缺失 {len(miss)} 只"
          + (f"（例：{miss[:5]}）" if miss else ""))

    # ---- 月末 -> buy 索引 ----
    me_pos = {}
    for m in months:
        if m in cal_pos:
            me_pos[m] = cal_pos[m]
        else:                       # 该日不在基准日历（极端情况）：取之前最近日
            cand = [d for d in cal if d <= m]
            me_pos[m] = cal_pos[cand[-1]] if cand else None

    def sleeve_daily(holdings, NS_path):
        """由逐月持仓重构日频组合净值（100% 股票口径，含换手成本）。

        持仓期口径：月 m 的持仓覆盖 [buy_m, buy_{m+1})，无缝衔接。
        这与月频 fwd20（buy -> buy+20）只差"20 天 vs 到下月建仓日"，
        但保证 (1) 覆盖 100% 交易日、(2) 不丢弃月间 gap 收益，使日频与月频可比。
        """
        nav = [1.0]
        n_m = len(months)
        b_first = me_pos[months[0]]
        dates_out = [cal[min(b_first + 1, len(cal) - 1)]]
        for t, m in enumerate(months):
            b0 = me_pos[m]
            if b0 is None:
                continue
            buy = b0 + 1
            if buy >= len(cal) - 1:
                break
            if t + 1 < n_m and me_pos[months[t + 1]] is not None:
                nxt = min(me_pos[months[t + 1]] + 1, len(cal) - 1)
            else:
                nxt = len(cal) - 1
            seg_end = max(nxt, buy + 1)
            hold = [c for c in holdings[t] if c in px]
            if not hold:
                for gd in range(buy + 1, seg_end + 1):
                    nav.append(nav[-1]); dates_out.append(cal[gd])
                continue
            mat = np.full((len(hold), seg_end - buy + 1), np.nan)
            for j, c in enumerate(hold):
                d, v = px[c]
                arr = pd.Series(v, index=d).reindex(cal[buy:seg_end + 1]).ffill().to_numpy(float)
                mat[j, :] = arr
            ok = ~np.isnan(mat[:, 0])
            if ok.sum() == 0:
                for gd in range(buy + 1, seg_end + 1):
                    nav.append(nav[-1]); dates_out.append(cal[gd])
                continue
            mat = mat[ok]
            with np.errstate(divide="ignore", invalid="ignore"):
                rets = mat[:, 1:] / mat[:, :-1] - 1.0
            rets = np.where(np.isnan(rets), 0.0, rets)
            port_daily = rets.mean(axis=0)
            cost = NS_path[t] * COST
            for k, r in enumerate(port_daily):
                f = (1.0 + r) * (1.0 - cost) if k == 0 else (1.0 + r)
                nav.append(nav[-1] * f)
                dates_out.append(cal[buy + k + 1])
        return np.array(nav), dates_out

    def dmetrics(nav):
        n = len(nav) - 1
        if n < 20:
            return None
        ann = nav[-1] ** (244.0 / n) - 1
        peak = np.maximum.accumulate(nav)
        dd = nav / peak - 1
        rr = nav[1:] / nav[:-1] - 1
        return {"ann": float(ann), "mdd": float(dd.min()),
                "vol": float(np.std(rr, ddof=1) * np.sqrt(244)),
                "calmar": float(ann / abs(dd.min())) if dd.min() < 0 else float("inf"),
                "days": n,
                "mdd_peak_idx": int(np.argmax(peak == peak.max())),
                "mdd_trough_idx": int(np.argmin(dd))}

    def mix(nav_stock, w):
        """日频 25% 仓位：每日按 w 权重再平衡（等价于每日调回目标权重）。"""
        rr = nav_stock[1:] / nav_stock[:-1] - 1
        pr = w * rr + (1 - w) * (CASH_M / 21.0)    # 现金按日摊
        return np.concatenate([[1.0], np.cumprod(1 + pr)])

    res = {"calendar_days": len(cal), "paths": {}, "missing_codes": len(miss),
           "note": ("日频 NAV：持仓期内按日等权复利；月间空档按 0 收益；"
                    "月末换手成本一次性扣除；年化按 244 交易日/年。")}
    per_path = {"c2": [], "c0": []}
    all_nav = {}
    mdd_detail = {}
    for pid, h in sorted(paths.items()):
        # 落盘持仓 JSON 未存 NS，从 npz 取对应路径的换手次数
        res["paths"][str(pid)] = {}
        for g in ("c2", "c0"):
            if not h.get(g):
                continue
            nav, dts = sleeve_daily(h[g], NS_arr[pid])
            m = dmetrics(nav)
            m25 = dmetrics(mix(nav, 0.25))
            res["paths"][str(pid)][g] = {
                "stock_100": m,
                "mix25": m25,
                "mdd_window": [dts[m["mdd_peak_idx"]] if m else None,
                               dts[m["mdd_trough_idx"]] if m else None] if m else None,
            }
            per_path[g].append((m, m25))
            all_nav.setdefault(g, []).append((np.array(dts), nav, mix(nav, 0.25)))
            if pid < 3:
                pd.DataFrame({"date": dts, "nav": nav, "nav25": mix(nav, 0.25)}).to_csv(
                    os.path.join(AOUT, f"sp4_daily_nav_p{pid}_{g}.csv"),
                    index=False, encoding="utf-8-sig")

    print("\n" + "=" * 96)
    print("日频重建结果（12 条路径）")
    print("=" * 96)
    print(f"{'组':<4}{'100%股票年化':>14}{'100%MDD':>11}{'100%波动':>11}"
          f"{'25%年化':>10}{'25%MDD':>10}{'25%波动':>10}")
    for g in ("c2", "c0"):
        a = [x[0]["ann"] for x in per_path[g] if x[0]]
        md = [x[0]["mdd"] for x in per_path[g] if x[0]]
        v = [x[0]["vol"] for x in per_path[g] if x[0]]
        a2 = [x[1]["ann"] for x in per_path[g] if x[1]]
        m2 = [x[1]["mdd"] for x in per_path[g] if x[1]]
        v2 = [x[1]["vol"] for x in per_path[g] if x[1]]
        print(f"{g:<4}{np.median(a)*100:>+13.2f}%{np.median(md)*100:>+10.2f}%{np.median(v)*100:>10.2f}%"
              f"{np.median(a2)*100:>+9.2f}%{np.median(m2)*100:>+9.2f}%{np.median(v2)*100:>9.2f}%")
        res[f"{g}_daily"] = {"ann_median": float(np.median(a)), "mdd_median": float(np.median(md)),
                            "vol_median": float(np.median(v)),
                            "mix25_ann_median": float(np.median(a2)),
                            "mix25_mdd_median": float(np.median(m2)),
                            "mix25_vol_median": float(np.median(v2))}

    # ---- 与月频对比 ----
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"), allow_pickle=True)
    def mperf(r):
        eq = np.cumprod(1 + r); peak = np.maximum.accumulate(eq)
        return (eq[-1] ** (12 / len(r)) - 1), float((eq / peak - 1).min())
    m_ann, m_mdd = mperf(z["R_c2"][0])
    d = res["c2_daily"]
    print("\n" + "=" * 96)
    print("月频 vs 日频（路径 0 与中位对照）")
    print("=" * 96)
    print(f"  月频 100%：年化 {m_ann*100:+.2f}%  MDD {m_mdd*100:+.2f}%")
    print(f"  日频 100%：年化 {d['ann_median']*100:+.2f}%  MDD {d['mdd_median']*100:+.2f}%"
          f"  （12 路径中位）")
    print(f"  => 日频 MDD 比月频深 {abs(d['mdd_median']-m_mdd)*100:.2f}pp")
    res["monthly_vs_daily"] = {"monthly_ann_p0": float(m_ann), "monthly_mdd_p0": float(m_mdd),
                               "daily_mdd_median": d["mdd_median"],
                               "mdd_understatement_pp": float(abs(d["mdd_median"]) - abs(m_mdd))}

    # ---- 分段日频指标（关键：判断回撤是否只由 2008 造成）----
    print("\n" + "=" * 96)
    print("分段日频（100% 与 25% 仓位，12 路径中位）")
    print("=" * 96)
    print(f"{'区间':<14}{'路径数':>7}{'100%年化':>11}{'100%MDD':>10}{'25%年化':>10}{'25%MDD':>10}{'25%MDD最差':>12}")
    seg_out = {}
    for lo, lab in (("2005", "全样本"), ("2015", "2015起"), ("2018", "2018起"), ("2022", "2022起")):
        for g in ("c2",):
            anns, mdds, m25a, m25d = [], [], [], []
            for dts, nav, nav25 in all_nav.get(g, []):
                mask = np.array([d >= lo + "0101" for d in dts])
                if mask.sum() < 250:
                    continue
                for arr, aa, mm in ((nav[mask], anns, mdds), (nav25[mask], m25a, m25d)):
                    base = arr / arr[0]
                    aa.append(base[-1] ** (244.0 / (len(base) - 1)) - 1)
                    mm.append(float(np.min(base / np.maximum.accumulate(base) - 1)))
            if not anns:
                continue
            seg_out[lab] = {"n_paths": len(anns),
                            "stock_ann": float(np.median(anns)), "stock_mdd": float(np.median(mdds)),
                            "mix25_ann": float(np.median(m25a)), "mix25_mdd": float(np.median(m25d)),
                            "mix25_mdd_worst": float(np.min(m25d))}
            print(f"{lab:<14}{len(anns):>7}{np.median(anns)*100:>+10.2f}%{np.median(mdds)*100:>+9.2f}%"
                  f"{np.median(m25a)*100:>+9.2f}%{np.median(m25d)*100:>+9.2f}%{np.min(m25d)*100:>+11.2f}%")
    res["segments_daily"] = seg_out

    with open(os.path.join(AOUT, "sp4_daily_audit.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {AOUT}/sp4_daily_audit.json + sp4_daily_nav_p*.csv")


if __name__ == "__main__":
    main()
