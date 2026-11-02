#!/usr/bin/env python
"""A3 面板重建 + 独立分解：个股集中度 / ADV 流动性依赖 / 执行诊断（任务 §五、§七）。

**独立路径声明**：
- 信号定义（MAX5=21 日最大 5 个日收益均值、Q5 等频、exQ5=非 Q5 等权）来自冻结预登记，
  本脚本**重新实现**计算：MAX5 用 np.sort（生产用 np.partition），分组用 array_split
  边界（生产用 searchsorted），ADV 分组同理；正确性由复现闸门 G3' 证明
  （重算 alpha 必须逐月复现 phasei monthly.csv 的 alpha_v0_pp，容差 1e-4）。
- 数据加载 / V1 可成交过滤（stream_bars+flags_fast）/ 容量规则定义：复用冻结生产模块
  （这些是数据口径而非被检验对象；重写它们只会引入新 bug —— 与阶段 I 预登记 §2.2 同理由）。
- 容量与成本：用 audit_common 的独立实现（numpy 路径）。
- 输出：out/a3_panel/（monthly_rebuild.csv / per_stock_contrib.csv / adv_groups.csv /
  exec_diagnostics.json / summary.json / run.log）

⚠️ 本脚本只读 data/ 与冻结产物，不写任何历史目录。
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(A_REPO := os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")), "scripts"))

import audit_common as A  # noqa: E402

# —— 冻结生产模块（只读复用）——
import run_phase_c as C              # noqa: E402
from run_phase_c import (MIN_LISTED_DAYS, NW_LAG, START, END_SIGNAL, END_HOLD,  # noqa: E402
                         load_close, Panels, build_mask, stream_bars)
from run_phase_i_exq5 import (WIN, TOP_N, MIN_VALID, N_GROUP, LOT, SEGMENTS,  # noqa: E402
                              filter_v1)
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a3_panel")
ADV_WIN = WIN
ADV_MIN_VALID = MIN_VALID
N_ADV_GROUP = 5
MODERN_LO = "2016-01-01"


# ==================== 独立实现 ====================
def max5_sort(w: np.ndarray) -> np.ndarray:
    """MAX5 = 窗口内最大 TOP_N 个日收益均值。**np.sort 路径**（生产用 np.partition）。"""
    if w.shape[0] < TOP_N:
        return np.full(w.shape[1], np.nan)
    srt = np.sort(w, axis=0)                 # 升序；NaN 排末尾
    top = srt[w.shape[0] - TOP_N:, :]
    return top.mean(axis=0)


def quintile_groups(x: np.ndarray, k: int) -> np.ndarray:
    """等频分 k 组（0 最小）。**array_split 路径**（生产用 searchsorted+cumsum）。
    并列值按 argsort(stable) 的原下标顺序占位 —— 与生产定义一致。"""
    n = x.size
    order = np.argsort(x, kind="stable")
    g = np.empty(n, dtype=int)
    for gi, part in enumerate(np.array_split(order, k)):
        g[part] = gi
    return g


def rebuild():
    """独立重建逐月面板（V0 口径 + ADV + 执行诊断列）。"""
    t0 = time.time()
    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)
    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    ffill = panels.ffill
    raw_close = close.to_numpy(dtype=float)      # 检测信号日是否真有成交（NaN=停牌/无数据）
    col_of = {c: i for i, c in enumerate(cols)}

    dv = load_dollar_volume().reindex(index=close.index, columns=close.columns)
    arr_dv = dv.to_numpy(dtype=float)

    recs = []
    split_mismatch = 0
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        w = arr_r[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(w).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            continue
        sub = np.where(np.isfinite(w[:, good]), w[:, good], -np.inf)
        max5 = max5_sort(sub)                     # 独立 MAX5（sort 路径）
        codes = cols[idx][good]
        m = np.isfinite(max5)
        if m.sum() < 100:
            continue
        max5, codes = max5[m], codes[m]

        grp = quintile_groups(max5, N_GROUP)      # 独立分组（array_split 路径）

        # ADV：与 MAX5 同窗、同有效日门槛，EXEC 真实成交额（volume×100×close）
        dvw = arr_dv[pos - WIN + 1: pos + 1][:, idx][:, good][:, m]
        adv_ok = np.isfinite(dvw).sum(axis=0) >= ADV_MIN_VALID
        with np.errstate(invalid="ignore"):
            adv = np.nanmean(np.where(np.isfinite(dvw), dvw, np.nan), axis=0)

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        pe = ffill.iloc[pos + 1][codes].to_numpy(dtype=float)     # 建仓日（次月首个交易日）
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0                    # 冻结收益期口径
            r1d = pe / p0 - 1.0                    # 首日收益（T+1 缺口诊断用）
            fwd_e = p1 / pe - 1.0                  # 建仓日收盘起算的收益（诊断）
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            continue
        max5, codes, fwd, grp = max5[fm], codes[fm], fwd[fm], grp[fm]
        adv, adv_ok = adv[fm], adv_ok[fm]
        r1d, fwd_e = r1d[fm], fwd_e[fm]

        is_q5 = grp == (N_GROUP - 1)
        # 信号日无成交（停牌/未上市数据）→ ffill 价为陈旧价
        pos_idx = np.array([col_of[c] for c in codes])
        stale = ~np.isfinite(raw_close[pos][pos_idx])

        recs.append({
            "signal_date": sdate, "entry": str(cal[pos + 1].date()),
            "nxt": str(cal[nxt].date()),
            "codes": [str(c) for c in codes],
            "fwd": fwd.tolist(), "r1d": r1d.tolist(), "fwd_e": fwd_e.tolist(),
            "adv": adv.tolist(), "adv_ok": adv_ok.tolist(),
            "is_q5": is_q5.tolist(), "stale": stale.tolist(),
            "n_pool": int(len(fwd)),
        })
    print(f"[A3] 面板重建 {len(recs)} 月，{time.time()-t0:.0f}s")
    return recs


def capacity_decomp(lots, budget: float) -> dict:
    """现金残留分解：未投资 = 整手取整残差（恒等）；另报反事实（全量买入）与被剔股票信息。

    返回：uninvested / rounding_pp（=uninvested）/ counterfactual_uninvested（全部 keep 都买，
    per=budget/k_total，不足一手得 0 手）/ n_excluded / excluded_lot_share。
    """
    a = np.asarray([l for l in lots if l == l and l > 0], dtype=float)
    k_total = int(a.size)
    if k_total == 0 or budget <= 0:
        return {"uninvested": 1.0, "n_excluded": k_total, "excluded_lot_share": None,
                "counterfactual_uninvested": 1.0}
    base = A.capacity_indep(a, budget)
    order = np.argsort(a, kind="stable")
    s = a[order]
    ks = np.arange(1, k_total + 1, dtype=float)
    k = int(ks[ks * s <= budget].max()) if (ks * s <= budget).any() else 0
    if k == 0:
        return {"uninvested": 1.0, "n_excluded": k_total, "excluded_lot_share": 1.0,
                "counterfactual_uninvested": 1.0}
    picked = s[:k]
    per = budget / k
    n_lot = np.floor(per / picked)
    rounding = float((per - n_lot * picked).sum())
    # 反事实：全部 keep 都买（per = budget/k_total，不足一手 → 0 手，整笔 allocation 变现金）
    per_all = budget / k_total
    n_lot_all = np.floor(per_all / a)
    inv_all = float((n_lot_all * a).sum())
    return {"uninvested": base["uninvested"], "rounding": rounding / budget,
            "counterfactual_uninvested": 1.0 - inv_all / budget,
            "n_excluded": k_total - k,
            "excluded_lot_share": float(s[k:].sum() / s.sum()) if k_total > k else 0.0,
            "k": k, "k_total": k_total}


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []

    def say(s):
        print(s)
        log.append(s)

    t_start = time.time()
    say("A3 面板重建 + 独立分解（ADV / 个股 / 执行诊断）")

    # ---- 0. 读取阶段 I 记录值（复现闸门基准） ----
    pi_rows = A.read_csv_rows(A.FROZEN["phasei_monthly"])
    rec_v0 = {r["signal_date"]: float(r["alpha_v0_pp"]) for r in pi_rows}

    recs = rebuild()

    # ---- G3' 复现闸门：独立 alpha 必须逐月复现阶段 I ----
    say("\n[G3'] 复现闸门：独立重算 V0 alpha vs phasei monthly.csv")
    diffs = []
    for r in recs:
        fwd = np.asarray(r["fwd"])
        q5 = np.asarray(r["is_q5"])
        alpha = float(fwd[~q5].mean() - fwd.mean())
        diffs.append(abs(alpha * 100.0 - rec_v0[r["signal_date"]]))
    mx = max(diffs)
    say(f"  {len(diffs)} 月最大差 {mx:.2e}pp {'✓' if mx < 1e-4 else '✗'}")
    if not mx < 1e-4:
        raise SystemExit("G3' 未过 → 独立路径与冻结产物不一致，终止")

    # ---- 1. V1 可成交过滤 + 换手 + 一手金额（复用冻结过滤定义） ----
    say("\n[1] V1 过滤（复用冻结 filter_v1/stream_bars）")
    need = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["codes"]:
            pass  # keep_codes 需要先有 is_q5；下面直接从 codes 里取非 Q5
    for i, r in enumerate(recs):
        q5 = np.asarray(r["is_q5"])
        for c in np.asarray(r["codes"])[~q5]:
            need[str(c)].add(r["entry"])
            if i + 1 < len(recs):
                need[str(c)].add(recs[i + 1]["entry"])
            need[str(c)].add(r["nxt"])      # 平仓日真实价：台账对账用（A5）
    bars, ex_al, span = stream_bars(dict(need))
    say(f"  需扫 {len(need)} 只")

    keep_rows = []   # 逐月 keep 清单（signal_date, entry, nxt, code, lot_yuan, px_entry, px_exit）

    for r in recs:
        rec = {"codes": r["codes"], "fwd": r["fwd"], "is_q5": r["is_q5"],
               "keep_codes": [c for c, q in zip(r["codes"], r["is_q5"]) if not q],
               "entry": r["entry"]}
        pos_map = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(rec, bars, ex_al, span, pos_map)
        r["keep_v1"] = f1["keep_codes"]
        r["n_drop"] = f1["n_drop"]
        r["drop_reasons"] = f1["drop"]
        fwd = np.asarray(r["fwd"])
        if f1["keep_codes"]:
            kv = [pos_map[c] for c in f1["keep_codes"]]
            r["alpha_v1"] = float(fwd[kv].mean() - fwd.mean())
        else:
            r["alpha_v1"] = float("nan")
        # 一手金额（建仓日收盘 × 100）
        lots, lot_codes = [], []
        for c in f1["keep_codes"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
                lot_codes.append(c)
                xe = bars.get(c, {}).get(r["nxt"])
                keep_rows.append([r["signal_date"], r["entry"], r["nxt"], c,
                                  round(rb["bar"][3] * LOT, 4),
                                  round(float(rb["bar"][3]), 4),
                                  round(float(xe["bar"][3]), 4) if xe is not None else ""])
        r["lots"], r["lot_codes"] = lots, lot_codes
        # 序列终止日（退市）诊断：**持有期内**退市 = 最后数据日早于本期月末（nxt）
        # 注意：建仓日当天已终止的会被 filter_v1 挡掉（after_last_data），不会到这里
        r["n_terminated_in_holding"] = sum(
            1 for c in f1["keep_codes"]
            if span.get(c, {}).get("last", "9999-99-99") < r["nxt"])
        r["fwd_terminated"] = [
            float(r["fwd"][pos_map[c]]) for c in f1["keep_codes"]
            if span.get(c, {}).get("last", "9999-99-99") < r["nxt"]]

    # 换手（独立重算，定义同生产：0.5×对称差/max|cur|）
    prev = None
    for r in recs:
        cur = set(r["keep_v1"])
        r["to_mine"] = 0.0 if prev is None else 0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1)
        prev = cur

    # 与阶段 O 记录的 to / alpha_v1 对账
    po_rows = A.read_csv_rows(A.FROZEN["phaseo_monthly"])
    rec_to = {r["signal_date"]: float(r["to"]) for r in po_rows}
    rec_a1 = {r["signal_date"]: float(r["alpha_pp"]) for r in po_rows}
    d_to = max(abs(r["to_mine"] - rec_to[r["signal_date"]]) for r in recs if r["signal_date"] in rec_to)
    d_a1 = max(abs(r["alpha_v1"] * 100 - rec_a1[r["signal_date"]]) for r in recs
               if r["signal_date"] in rec_a1 and r["alpha_v1"] == r["alpha_v1"])
    say(f"  换手对账（vs 阶段 O 记录 to）最大差 {d_to:.2e}  alpha_v1 对账最大差 {d_a1:.2e}")

    A.write_csv(os.path.join(OUT, "keep_lists.csv"),
                ["signal_date", "entry", "nxt", "code", "lot_yuan", "px_entry", "px_exit"],
                keep_rows)
    say(f"  逐月 keep 清单 {len(keep_rows)} 行（含建仓/平仓真实价）→ keep_lists.csv")

    # ---- 2. 容量（独立 capacity_indep）与三口径成本 ----
    say("\n[2] 容量与成本（独立实现）")
    CAPITALS = [1.5e6, 3.0e6, 5.0e6, 1.0e7, 3.0e7, 5.0e7, 1.0e8]
    cap_rows = []
    cash_decomp = {}
    for w in CAPITALS:
        unin, mae, cov, ks = [], [], [], []
        dec_round, dec_cf, dec_excl, dec_lotshare = [], [], [], []
        for r in recs:
            if not r["lots"]:
                continue
            cs = A.capacity_indep(r["lots"], w)
            unin.append(cs["uninvested"]); mae.append(cs["weight_mae_pp"])
            cov.append(cs["covered"]); ks.append(cs["k"])
            dc = capacity_decomp(r["lots"], w)
            dec_round.append(dc["rounding"]); dec_cf.append(dc["counterfactual_uninvested"])
            dec_excl.append(dc["n_excluded"]); dec_lotshare.append(dc["excluded_lot_share"] or 0.0)
        row = {"w_wan": w / 1e4,
               "uninvested_median": statistics.median(unin), "uninvested_mean": statistics.mean(unin),
               "weight_mae_pp_median": statistics.median(mae),
               "covered_median": statistics.median(cov), "k_median": statistics.median(ks)}
        cap_rows.append(row)
        cash_decomp[f"{w/1e4:.0f}万"] = {
            "uninvested_median": statistics.median(unin),
            "rounding_residual_median": statistics.median(dec_round),
            "counterfactual_all_buy_median": statistics.median(dec_cf),
            "n_excluded_median": statistics.median(dec_excl),
            "excluded_lot_share_median": statistics.median(dec_lotshare)}
        say(f"  W={w/1e4:>7.0f}万 未投资中位 {row['uninvested_median']*100:5.2f}% "
            f"均值 {row['uninvested_mean']*100:5.2f}% 覆盖 {row['covered_median']*100:5.1f}% "
            f"k中位 {row['k_median']:.0f}"
            f"｜取整残差 {statistics.median(dec_round)*100:.2f}% "
            f"反事实全买 {statistics.median(dec_cf)*100:.2f}% "
            f"剔除 {statistics.median(dec_excl):.0f} 只（占一手额 {statistics.median(dec_lotshare)*100:.1f}%）")

    # 逐月净值的独立重算（BASE/STRESS/CONS × 1000万/3000万，冲击用 O 的常数 0.000784）
    IMP = 0.000784
    nets_mine = {}
    for w in (1.0e7, 3.0e7):
        for fee in ("BASE", "STRESS", "CONSERVATIVE"):
            vals = []
            for r in recs:
                if not r["lots"] or r["alpha_v1"] != r["alpha_v1"]:
                    continue
                cs = A.capacity_indep(r["lots"], w)
                if cs["k"] <= 0:
                    continue
                finv = 1.0 - cs["uninvested"]
                if fee == "BASE":
                    cst = A.cost_base_indep(r["to_mine"])
                elif fee == "STRESS":
                    cst = A.cost_stress_indep(r["to_mine"])
                else:
                    cst = A.cost_cons_indep(r["to_mine"], w, cs["k"])
                vals.append(finv * r["alpha_v1"] * 100.0 - cst - IMP)
            nets_mine[(w, fee)] = vals
    say("\n  独立净值 vs 阶段 O 记录：")
    rec_o = json.load(open(A.FROZEN["phaseo_summary"], encoding="utf-8"))
    net_checks = []
    for (w, fee), vals in nets_mine.items():
        pr = A.nw_t(vals, 3)
        r = rec_o["results"][f"W_{w/1e4:.0f}万|{fee if fee != 'CONSERVATIVE' else 'CONSERVATIVE'}"]
        say(f"   W_{w/1e4:.0f}万|{fee:<12} mean={pr['mean']:+.6f}pp t={pr['t_nw']:+.4f}"
            f"（记录 {r['net_pp']:+.6f}/{r['t_nw']:+.4f}）")
        net_checks.append({"w_wan": w / 1e4, "fee": fee, "mean_pp": pr["mean"], "t_nw": pr["t_nw"],
                           "recorded_net_pp": r["net_pp"], "recorded_t_nw": r["t_nw"],
                           "ok": abs(pr["mean"] - r["net_pp"]) < 5e-4 and abs(pr["t_nw"] - r["t_nw"]) < 5e-3})
    # 注意：我的换手是独立重算的，若与记录 to 有差，净值会有小差 → 对账
    bad = [c for c in net_checks if not c["ok"]]
    say(f"  净值对账：{len(net_checks)-len(bad)}/{len(net_checks)} 通过"
        + ("" if not bad else f"；未过：{[(c['w_wan'], c['fee']) for c in bad]}（换手/容量口径差）"))

    # ---- 3. ADV 五组分解（独立分组 + 预登记公式） ----
    say("\n[3] ADV 五分位分解（V0 口径，独立分组）")
    contrib_rows = []
    per_stock = defaultdict(float)          # 个股累计贡献（小数）
    per_stock_n = defaultdict(int)
    disagree_q = 0
    ident_max = 0.0
    g_stats = {g: {"n_stocks": [], "keep_share": [], "fwd_pool": [], "fwd_keep": [],
                   "adv_median": [], "persistence": []} for g in range(N_ADV_GROUP)}
    prev_keep_v1 = None
    for r in recs:
        fwd = np.asarray(r["fwd"])
        adv = np.asarray(r["adv"])
        adv_ok = np.asarray(r["adv_ok"])
        is_q5 = np.asarray(r["is_q5"])
        v = adv_ok
        if v.sum() < N_ADV_GROUP * 20:
            prev_keep_v1 = set(r["keep_v1"])
            continue
        pv = np.where(v)[0]
        kv = pv[~is_q5[pv]]
        n_k, n_p = len(kv), len(pv)
        if n_k < 20:
            prev_keep_v1 = set(r["keep_v1"])
            continue
        a = adv[pv]
        g = quintile_groups(a, N_ADV_GROUP)                    # 独立分组
        qs = np.quantile(a, [0.2, 0.4, 0.6, 0.8])              # 交叉校验：分位阈值法
        g2 = np.searchsorted(qs, a, side="right")
        disagree_q += int((g != g2).sum())
        codes_v = np.asarray(r["codes"])[pv]
        contrib = np.zeros(N_ADV_GROUP)
        within = np.zeros(N_ADV_GROUP)
        for gi in range(N_ADV_GROUP):
            in_p = g == gi
            in_k = in_p & ~is_q5[pv]
            n_pg = int(in_p.sum())
            contrib[gi] = fwd[pv][in_k].sum() / n_k - fwd[pv][in_p].sum() / n_p
            within[gi] = (fwd[pv][in_k].mean() if in_k.any() else float("nan")) \
                - (fwd[pv][in_p].mean() if n_pg else float("nan"))
            g_stats[gi]["n_stocks"].append(n_pg)
            g_stats[gi]["keep_share"].append(float(in_k.sum()) / max(n_k, 1))
            g_stats[gi]["fwd_pool"].append(float(fwd[pv][in_p].mean()))
            g_stats[gi]["fwd_keep"].append(float(fwd[pv][in_k].mean()) if in_k.any() else float("nan"))
            g_stats[gi]["adv_median"].append(float(np.median(a[in_p])))
            if prev_keep_v1 is not None:
                cur_g_keep = set(codes_v[in_k].tolist())
                g_stats[gi]["persistence"].append(
                    len(cur_g_keep & prev_keep_v1) / max(len(cur_g_keep), 1))
        ident_max = max(ident_max, abs(float(contrib.sum() - (fwd[kv].mean() - fwd[pv].mean()))))
        contrib_rows.append({"signal_date": r["signal_date"],
                             "n_pool_v": n_p, "n_keep_v": n_k,
                             **{f"contrib_g{gi+1}": contrib[gi] for gi in range(N_ADV_GROUP)},
                             **{f"within_g{gi+1}": within[gi] for gi in range(N_ADV_GROUP)}})
        # 个股累计贡献（精确可加）：[1_K/n_K − 1_P/n]×r_i
        w_i = np.where(is_q5[pv], 0.0, 1.0 / n_k) - 1.0 / n_p
        for j, code in enumerate(np.asarray(r["codes"])[pv]):
            per_stock[str(code)] += float(w_i[j] * fwd[pv][j])
            per_stock_n[str(code)] += 1
        prev_keep_v1 = set(r["keep_v1"])   # ⚠️ 主路径也必须更新，否则持续性恒为 None
    say(f"  分解恒等式最大偏差 {ident_max:.2e}（容差 1e-12）")
    say(f"  分组交叉校验不一致股票-月 {disagree_q}（array_split vs 分位阈值）")
    adv_tbl = []
    for gi in range(N_ADV_GROUP):
        cs = [row[f"contrib_g{gi+1}"] for row in contrib_rows]
        pr = A.nw_t([c * 100 for c in cs], 3)      # 转 pp 后统计
        st = g_stats[gi]
        adv_tbl.append({"group": f"g{gi+1}", "n_months": len(cs),
                        "mean_contrib_pp": pr["mean"], "t_nw": pr["t_nw"],
                        "share_of_total": pr["mean"] / sum(x["mean_contrib_pp"] for x in adv_tbl) if adv_tbl else float("nan"),
                        "n_stocks_median": statistics.median(st["n_stocks"]),
                        "keep_share_mean": statistics.mean(st["keep_share"]),
                        "keep_persistence_mean": (statistics.mean(st["persistence"])
                                                  if st["persistence"] else None),
                        "fwd_pool_mean_pp": statistics.mean(st["fwd_pool"]) * 100,
                        "fwd_keep_mean_pp": statistics.mean(st["fwd_keep"]) * 100,
                        "adv_median_mean": statistics.mean(st["adv_median"])})
        pers = adv_tbl[-1]["keep_persistence_mean"]
        say(f"  g{gi+1}: 贡献 {pr['mean']:+.6f}pp t={pr['t_nw']:+.4f} "
            f"股数中位 {adv_tbl[-1]['n_stocks_median']:.0f} 保留占比 {adv_tbl[-1]['keep_share_mean']:.3f} "
            f"持续性 {pers:.3f}" if pers is not None else
            f"  g{gi+1}: 贡献 {pr['mean']:+.6f}pp t={pr['t_nw']:+.4f} "
            f"股数中位 {adv_tbl[-1]['n_stocks_median']:.0f} 保留占比 {adv_tbl[-1]['keep_share_mean']:.3f} 持续性 n/a")
    tot_contrib = sum(x["mean_contrib_pp"] for x in adv_tbl)
    for x in adv_tbl:
        x["share_of_total"] = x["mean_contrib_pp"] / tot_contrib
    ratio_c1_c5 = adv_tbl[0]["mean_contrib_pp"] / adv_tbl[4]["mean_contrib_pp"]
    say(f"  g1 占总 alpha {adv_tbl[0]['share_of_total']*100:.1f}%  c1/c5={ratio_c1_c5:.4f}"
        f"（阶段 T 记录 44.1% / 2.3072）")

    # ---- 4. 个股集中度 ----
    say("\n[4] 个股收益贡献集中度（sum of monthly alpha 的可加归因）")
    vals = sorted(per_stock.values(), key=abs, reverse=True)
    tot_abs = sum(abs(v) for v in vals)
    tot_sum = sum(per_stock.values())
    n_stock = len(per_stock)
    top10 = sum(abs(v) for v in vals[:10]) / tot_abs
    top50 = sum(abs(v) for v in vals[:50]) / tot_abs
    top1pct = sum(abs(v) for v in vals[:max(1, n_stock // 100)]) / tot_abs
    pos_sum = sum(v for v in per_stock.values() if v > 0)
    neg_sum = sum(v for v in per_stock.values() if v < 0)
    say(f"  个股数 {n_stock}  累计贡献合计 {tot_sum*100:+.4f}pp（=Σ月 alpha）")
    say(f"  top10 占 |贡献| {top10*100:.2f}%  top50 {top50*100:.2f}%  top1% {top1pct*100:.2f}%")
    say(f"  正贡献个股合计 {pos_sum*100:+.4f}pp  负贡献合计 {neg_sum*100:+.4f}pp")
    # 高频出现股票（每月都在池）
    freq = sorted(per_stock_n.values(), reverse=True)
    say(f"  出现月份数：中位 {statistics.median(freq):.0f}  最大 {freq[0]}")

    # ---- 5. 执行诊断：T+1 缺口 / 陈旧价 / 持有期退市 ----
    say("\n[5] 执行诊断（诊断性，不参与任何判定）")
    gaps, stale_cnt, stale_fwd, fresh_fwd = [], 0, [], []
    term_cnt, term_fwd, kept_fwd = [], [], []
    for r in recs:
        fwd = np.asarray(r["fwd"]); r1d = np.asarray(r["r1d"]); fwd_e = np.asarray(r["fwd_e"])
        q5 = np.asarray(r["is_q5"]); keep = ~q5
        stale = np.asarray(r["stale"])
        # T+1 缺口：V0 alpha − 建仓日收盘起算的 alpha（同一批持仓）
        alpha_frozen = float(fwd[keep].mean() - fwd.mean())
        alpha_entry = float(fwd_e[keep].mean() - fwd_e.mean())
        gaps.append(alpha_frozen - alpha_entry)
        stale_cnt += int((stale & keep).sum())
        if stale[keep].any():
            stale_fwd.append(float(fwd[keep][stale[keep]].mean()))
        if (~stale[keep]).any():
            fresh_fwd.append(float(fwd[keep][~stale[keep]].mean()))
        term_cnt.append(r["n_terminated_in_holding"])
        term_fwd.extend(r["fwd_terminated"])
        kept_fwd.append(float(fwd[keep].mean()))
    term_total = sum(term_cnt)
    say(f"  T+1 缺口（冻结收益期 − 建仓日收盘起算）月均 {statistics.mean(gaps)*100:+.4f}pp "
        f"（中位 {statistics.median(gaps)*100:+.4f}pp）")
    say(f"  信号日无成交（ffill 陈旧价）的持仓股票-月 {stale_cnt}"
        f"（as-of 掩码要求信号日有有效收盘 → 该值为 0 是设计结果）")
    if stale_fwd:
        say(f"    陈旧价组月均收益 {statistics.mean(stale_fwd)*100:+.4f}pp vs 新鲜价组 "
            f"{statistics.mean(fresh_fwd)*100:+.4f}pp（n={len(stale_fwd)} 月有陈旧组）")
    say(f"  持有期内退市（最后数据日早于本期月末）的持仓股票-月 {term_total}"
        f"（月均 {statistics.mean(term_cnt):.1f}；其收益按最后可得价计量，持有期后段缺失）")
    if term_fwd:
        say(f"    这些股票的持有期收益均值 {statistics.mean(term_fwd)*100:+.4f}pp"
            f" vs 全部持仓 {statistics.mean(kept_fwd)*100:+.4f}pp")
    # 停牌/跌停卖出侧（V2 计数器口径）
    sell_blocked = sell_total = 0
    for i, r in enumerate(recs[:-1]):
        ex_date = recs[i + 1]["entry"]
        for c in r["keep_v1"]:
            rec_b = bars.get(c, {}).get(ex_date)
            f = C.flags_fast(rec_b, c, ex_date, ex_al.get(c, set()))
            reason = C._reason_of(f, c, ex_date, span)
            sell_total += 1
            if reason in {"limit_down", "no_bar", "after_last_data"} or \
               (reason == "one_word_board" and f.get("dir") == "down"):
                sell_blocked += 1
    say(f"  调仓日不可卖出 {sell_blocked}/{sell_total}（{sell_blocked/max(sell_total,1)*100:.2f}%）"
        f"（阶段 I 记录 10,296/533,733=1.93%）")

    # ---- 产物 ----
    A.write_csv(os.path.join(OUT, "adv_groups.csv"),
                ["group", "n_months", "mean_contrib_pp", "t_nw", "share_of_total",
                 "n_stocks_median", "keep_share_mean", "keep_persistence_mean",
                 "fwd_pool_mean_pp", "fwd_keep_mean_pp", "adv_median_mean"],
                [[x["group"], x["n_months"], round(x["mean_contrib_pp"], 8), round(x["t_nw"], 4),
                  round(x["share_of_total"], 6), round(x["n_stocks_median"], 1),
                  round(x["keep_share_mean"], 4),
                  round(x["keep_persistence_mean"], 4) if x["keep_persistence_mean"] is not None else "",
                  round(x["fwd_pool_mean_pp"], 4),
                  round(x["fwd_keep_mean_pp"], 4), round(x["adv_median_mean"], 0)]
                 for x in adv_tbl])
    A.write_csv(os.path.join(OUT, "monthly_rebuild.csv"),
                ["signal_date", "entry", "n_pool", "n_keep_v1", "n_drop", "to_mine",
                 "alpha_v0_pp", "alpha_v1_pp", "t1_gap_pp"]
                + [f"contrib_g{g+1}" for g in range(N_ADV_GROUP)],
                [[r["signal_date"], r["entry"], r["n_pool"], len(r["keep_v1"]), r["n_drop"],
                  round(r["to_mine"], 6),
                  round((np.asarray(r["fwd"])[~np.asarray(r["is_q5"])].mean()
                         - np.asarray(r["fwd"]).mean()) * 100, 6),
                  round(r["alpha_v1"] * 100, 6) if r["alpha_v1"] == r["alpha_v1"] else "",
                  round(g * 100, 6)]
                 + [round(c * 100, 8) for c in
                    ([row for row in contrib_rows if row["signal_date"] == r["signal_date"]][0]
                     .get(f"contrib_g{g+1}", float("nan")) for g in range(N_ADV_GROUP))]
                 for r, g in zip(recs, gaps)])
    A.write_csv(os.path.join(OUT, "per_stock_contrib.csv"),
                ["code", "cum_contrib_dec", "months_present"],
                [[c, round(v, 10), n] for c, v, n in
                 sorted(((c, v, per_stock_n[c]) for c, v in per_stock.items()),
                        key=lambda t: -abs(t[1]))])
    A.write_json(os.path.join(OUT, "summary.json"), {
        "gate_g3_prime": {"max_abs_diff_pp": mx, "n_months": len(recs), "ok": bool(mx < 1e-4)},
        "recon_phase_o": {"to_max_diff": d_to, "alpha_v1_max_diff": d_a1,
                          "net_checks": net_checks},
        "capacity_indep": cap_rows,
        "cash_decomposition": cash_decomp,
        "adv_decomposition": {"groups": adv_tbl, "ratio_c1_over_c5": ratio_c1_c5,
                              "identity_max_abs_err": ident_max,
                              "split_crosscheck_disagreements": disagree_q},
        "stock_concentration": {"n_stocks": n_stock, "total_contrib_pp": tot_sum * 100,
                                "top10_share_abs": top10, "top50_share_abs": top50,
                                "top1pct_share_abs": top1pct,
                                "pos_sum_pp": pos_sum * 100, "neg_sum_pp": neg_sum * 100,
                                "note": "归因对象=Σ月 alpha（t 检验的量），非复利财富"},
        "exec_diagnostics": {
            "t1_gap_mean_pp": statistics.mean(gaps) * 100,
            "t1_gap_median_pp": statistics.median(gaps) * 100,
            "stale_price_keep_stock_months": stale_cnt,
            "stale_fwd_mean_pp": statistics.mean(stale_fwd) * 100 if stale_fwd else None,
            "fresh_fwd_mean_pp": statistics.mean(fresh_fwd) * 100 if fresh_fwd else None,
            "terminated_in_holding_stock_months": term_total,
            "terminated_in_holding_per_month": statistics.mean(term_cnt),
            "terminated_fwd_mean_pp": statistics.mean(term_fwd) * 100 if term_fwd else None,
            "all_keep_fwd_mean_pp": statistics.mean(kept_fwd) * 100,
            "sell_blocked": sell_blocked, "sell_total": sell_total,
            "sell_blocked_share": sell_blocked / max(sell_total, 1)},
        "runtime_sec": round(time.time() - t_start, 1),
    })
    with open(os.path.join(OUT, "run.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[产物] {OUT}/  总耗时 {time.time()-t_start:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
