# -*- coding: utf-8 -*-
"""P0-3：幸存者偏差与历史股票池审计

已确认的事实（前序探查）：
  - `qfq_all` 5,444 只，其中 216 只最后交易日 < 2026（已退市/长期停牌）→ **退市股并非被排除**
  - `data/delist` 262 只，最早数据始于 2016-01-04 → **2016 年前退市的股票缺失**
  - `_all_a_codes` 5,223 只（当前在册），与 delist 交集为 0

因此本脚本审计三个具体问题：
  Q1 退市股在面板里如何被处理？其退市前的损失是否计入组合收益？
  Q2 2016 年前缺失的退市股，影响的是哪一段样本？
  Q3 ST 状态能否回溯？（若无数据，明确列为缺口，不用代理数据）

输出：audit_out/sp4_survivor.json
运行：python scripts/audit/audit_sp4_survivor.py
"""
import os, json, glob
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
QFQ = os.path.join(ROOT, "data", "qfq_all")
AOUT = os.path.join(RD, "audit_out")


def read_tail(p, n=3):
    with open(p, "rb") as f:
        f.seek(0, 2); sz = f.tell()
        f.seek(max(0, sz - 300)); tail = f.read().decode("utf-8", "replace").strip().splitlines()
    return tail[-n:]


def main():
    res = {"data_inventory": {}, "delisted_analysis": {}, "st_gap": {}, "conclusion": {}}

    # ---------- 1) 清单 ----------
    qfq_files = sorted(glob.glob(os.path.join(QFQ, "*.qfq.csv")))
    delist_files = sorted(glob.glob(os.path.join(ROOT, "data", "delist", "*.qfq.csv")))
    qfq_codes = {os.path.basename(p).split(".")[0] for p in qfq_files}
    delist_codes = {os.path.basename(p).split(".")[0] for p in delist_files}
    res["data_inventory"] = {
        "qfq_all_n": len(qfq_codes), "delist_dir_n": len(delist_codes),
        "delist_also_in_qfq": len(delist_codes & qfq_codes),
        "delist_earliest_row": "2016-01-04（据 000004/000005/000018 文件首行）",
        "note": "delist 目录与 qfq_all 高度重叠，说明 qfq_all 已含 2016 年后退市股"
    }

    # ---------- 2) 每只股票的最后交易日 ----------
    last = {}
    fn_of = {}
    for p in qfq_files:
        c = os.path.basename(p).split(".")[0]
        fn_of[c] = p                       # 保留完整路径（含 .SZ/.SH 后缀）
        t = read_tail(p, 1)
        if t:
            last[c] = t[-1].split(",")[0].replace("-", "")
    dl = {c: d for c, d in last.items() if d < "20260101"}
    res["data_inventory"]["n_last_before_2026"] = len(dl)
    res["data_inventory"]["n_before_2016"] = int(sum(1 for d in dl.values() if d < "20160101"))

    # ---------- 3) 退市前收益：最后一年 / 最后 20 日 ----------
    print(f"[scan] {len(dl)} 只已退市/长期停牌，计算退市前收益 ...")
    rows = []
    for c, d in dl.items():
        p = fn_of[c]
        try:
            df = pd.read_csv(p, usecols=["date", "close", "volume"])
        except Exception:
            continue
        df = df.dropna(subset=["close"])
        if len(df) < 260:
            continue
        df["date"] = df["date"].astype(str).str.replace("-", "", regex=False)
        c_arr = df["close"].to_numpy(float)
        v_arr = df["volume"].to_numpy(float)
        # 最后一年（250 交易日）与最后 20 日
        r_1y = c_arr[-1] / c_arr[-251] - 1 if len(c_arr) > 251 and c_arr[-251] > 0 else np.nan
        r_20 = c_arr[-1] / c_arr[-21] - 1 if len(c_arr) > 21 and c_arr[-21] > 0 else np.nan
        amt20 = float((v_arr[-20:] * 100 * c_arr[-20:]).mean()) if len(c_arr) >= 20 else np.nan
        rows.append({"code": c, "last_date": d, "r_last_year": r_1y, "r_last_20d": r_20,
                     "amt20_last_wan": amt20 / 1e4, "n_days": len(c_arr)})
    dd = pd.DataFrame(rows)
    dd = dd.dropna(subset=["r_last_year"])
    # 与同期档内阈值比较（amt20 < 500 万 = 原 ok_buy 门槛；另给分位）
    thr_amt = 500.0      # 万元
    in_low_band = dd["amt20_last_wan"] < thr_amt
    print(f"  可计算 {len(dd)} 只；退市前最后 20 日均额 < {thr_amt:.0f} 万的占 {in_low_band.mean()*100:.1f}%")
    res["delisted_analysis"] = {
        "n_analyzed": int(len(dd)),
        "r_last_year": {"mean": float(dd["r_last_year"].mean()),
                        "median": float(dd["r_last_year"].median()),
                        "p25": float(dd["r_last_year"].quantile(.25)),
                        "p75": float(dd["r_last_year"].quantile(.75)),
                        "share_negative": float((dd["r_last_year"] < 0).mean())},
        "r_last_20d": {"mean": float(dd["r_last_20d"].mean()),
                       "median": float(dd["r_last_20d"].median()),
                       "share_negative": float((dd["r_last_20d"] < 0).mean()),
                       "share_below_minus_20": float((dd["r_last_20d"] < -0.20).mean())},
        "amt20_last_wan": {"median": float(dd["amt20_last_wan"].median()),
                           "share_below_500w": float(in_low_band.mean())},
        "by_year": {y: int((dd["last_date"].str[:4] == y).sum())
                    for y in sorted(set(dd["last_date"].str[:4]))},
    }

    # ---------- 4) 面板如何处理"退市/停牌"：下一月消失的股票 ----------
    print("[panel] 检查下一月消失的股票（= 退市/停牌）如何处理 ...")
    p = pd.read_parquet(os.path.join(RD, "data", "panel_monthly.parquet"))
    p = p.sort_values(["code", "date"])
    p["next_date"] = p.groupby("code")["date"].shift(-1)
    # 在 t 月出现、但 t+1 月不再出现（且 t 不是最后一月）
    months = sorted(p["date"].unique())
    last_month = months[-1]
    vanish = p[(p["next_date"].isna()) & (p["date"] != last_month)]
    # 这些行是否有 fwd20？
    v_ok = vanish[vanish["fwd20"].notna()]
    print(f"  末次出现后消失的股票-月：{len(vanish)}，其中 fwd20 有值 {len(v_ok)} ({len(v_ok)/max(1,len(vanish))*100:.1f}%)")
    res["vanish_handling"] = {
        "n_stock_month": int(len(vanish)),
        "n_with_fwd20": int(len(v_ok)),
        "share_with_fwd20": float(len(v_ok) / max(1, len(vanish))),
        "fwd20_mean_when_present": float(v_ok["fwd20"].mean()) if len(v_ok) else None,
        "explanation": ("fwd20 = P(t+20)/P(t+1)-1 需要 t+20 有价格。退市/长期停牌的股票在最后 20 天内"
                        "拿不到 t+20，fwd20 为 NaN，被 `fwd20.notna()` 过滤掉，因此**不会进入持仓池**。"
                        "若已持有，则在组合里被剔除（收益改由其余股票平均），真实损失未被计入。"),
    }

    # ---------- 5) ST 状态 ----------
    allc = json.load(open(os.path.join(ROOT, "data", "_all_a_codes.json"), encoding="utf-8"))
    st_now = [x for x in allc if "ST" in str(x.get("name", "")).upper()]
    res["st_gap"] = {
        "field_available": False,
        "n_name_contains_ST_now": len(st_now),
        "examples": [x["code"] for x in st_now[:8]],
        "gap": ("项目无历史 ST 状态表（仅当前名称含 ST 可识别）。"
                "`tdx-connector` 有 `stk_st_daily` 类日表可用于回溯，但本轮未接入。"
                "**因此「排除当期 ST」与「排除高退市风险」两项敏感性分析无法执行**，"
                "不以当前名称反推历史状态（会引入前视偏差）。"),
    }

    # ---------- 6) 影响量级估算 ----------
    # 若退市股被正确计入：假设每 N 年退市 n 只，档内约 M 只，5 只持仓
    # 被选中概率 ≈ 5/M；实际损失 = 退市前一年收益（未能卖出）
    n_delist = len(dd)
    res["conclusion"] = {
        "q1_handling": "退市股在退市前 20 天内因 fwd20=NaN 被排除，组合层面的真实损失被替换为其余持仓平均；"
                       "这是一个**向上的偏差**（高估收益），但量级需要通过重跑才能精确化。",
        "q2_coverage": f"2016 年后退市股已纳入（{n_delist} 只有可算数据）；2016 年前退市股**完全缺失**，"
                       "受影响的样本段是 2005-2015，恰好也是 SP-4 早期可执行性最差、收益最极端的一段。",
        "q3_st": "无历史 ST 表，相关敏感性分析未执行，列为缺口。",
        "direction": "幸存者偏差方向明确为**高估**；量级未精确化（需重建含退市损失的 fwd20 并重跑）。",
    }

    with open(os.path.join(AOUT, "sp4_survivor.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    # ---------- 打印 ----------
    print("\n" + "=" * 92)
    print("A. 退市股退市前收益（2016 年后，n=%d）" % len(dd))
    print("=" * 92)
    a = res["delisted_analysis"]
    print(f"  最后一年：均值 {a['r_last_year']['mean']*100:+.2f}%  中位 {a['r_last_year']['median']*100:+.2f}%"
          f"  P25 {a['r_last_year']['p25']*100:+.2f}%  P75 {a['r_last_year']['p75']*100:+.2f}%"
          f"  负收益占比 {a['r_last_year']['share_negative']*100:.1f}%")
    print(f"  最后 20 日：均值 {a['r_last_20d']['mean']*100:+.2f}%  中位 {a['r_last_20d']['median']*100:+.2f}%"
          f"  负收益占比 {a['r_last_20d']['share_negative']*100:.1f}%"
          f"  跌幅>20% 占比 {a['r_last_20d']['share_below_minus_20']*100:.1f}%")
    print(f"  退市前 20 日均额中位 {a['amt20_last_wan']['median']:.0f} 万"
          f"｜< 500 万占比 {a['amt20_last_wan']['share_below_500w']*100:.1f}%")
    print(f"  逐年退市数：{a['by_year']}")
    print("\n" + "=" * 92)
    print("B. 面板对「下一月消失」股票的处理")
    print("=" * 92)
    v = res["vanish_handling"]
    print(f"  末次出现即消失的股票-月：{v['n_stock_month']}，其中 fwd20 有值 {v['n_with_fwd20']}"
          f"（{v['share_with_fwd20']*100:.1f}%）")
    print(f"  有值时平均 fwd20 = {v['fwd20_mean_when_present']*100:+.2f}%" if v["fwd20_mean_when_present"] is not None else "")
    print("\n" + "=" * 92)
    print("C. ST 数据缺口")
    print("=" * 92)
    print(f"  当前名称含 ST：{res['st_gap']['n_name_contains_ST_now']} 只（仅当前状态，不可回溯）")
    print(f"\n[saved] {AOUT}/sp4_survivor.json")


if __name__ == "__main__":
    main()
