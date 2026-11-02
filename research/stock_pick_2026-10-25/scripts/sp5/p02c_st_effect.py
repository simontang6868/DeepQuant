# -*- coding: utf-8 -*-
"""SP-5 / P0-2c：历史 ST 对收益的影响（深市子样本对照）

背景
----
`SP5_预登记.md` §5.1 要求三组对照：
  G0 基线（无 ST 过滤）｜G1 深市剔 ST（同市场可比）｜G2 沪市 5% 限价代理
P0-2a 已证明**沪市无带日期的历史 ST 真值**（0 覆盖），故 G2 无法标定 → 沪市只能标「无法验证」。

⚠️ 只在**深市子样本**内比较（同市场、同换手、同成本），否则会把「深市 vs 沪市」的结构差异
混进「ST 效应」。

校验门（写死在代码里，不可跳过）
------------------------------
全市场、不剔 ST 的口径必须复现 P0-3 的 T0/T2（25% 仓位 +10.24%/−20.93%、+9.85%/−20.73%），
否则本脚本的独立实现不可信，结果作废。

实现要点：行情与撮合状态**只算一次**（按 (code,month,side) 建表），三个配置复用，
否则重复读 4,000+ 个 CSV 会把运行拖到十几分钟并可能触发内存交换。

输出：sp5_out/sp5_st_effect.json
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402

ROOT = fe.ROOT
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
OUT = os.path.join(RD, "sp5_out")
K, THRESH, LIQ_MAX = 5, 0.70, 0.20
SEED = 20261026
SIDE_COST = 0.000102
CASH_ANN = 0.03
SZ_PREFIX = ("000", "001", "002", "003", "300", "301", "302")
N_PATHS = int(os.environ.get("SP5_PATHS", "200"))
DIAG = {"nocode": 0, "nodate": 0, "false": 0, "true": 0}
SKIP_GATE = os.environ.get("SP5_SKIP_GATE") == "1"


def check_prereg() -> str:
    want = open(os.path.join(RD, "sp5_prereg.sha256"), encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD, "SP5_预登记.md"), "rb").read()).hexdigest()
    if want != got:
        sys.exit("[abort] 预登记哈希不一致")
    print(f"[prereg] {want[:16]}… OK")
    return want


def build_decisions(panel, st_map, n_paths, market, exclude_st):
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    if market == "SZ":
        p = p[p["code"].str[:3].isin(SZ_PREFIX)]
    if exclude_st:
        keep = ~np.array([st_map.get((c, d), False) for c, d in zip(p["code"], p["date"])], bool)
        p = p[keep]
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]

    by, n_band = {}, []
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        codes = g["code"].to_numpy()
        lq = g["liqpct"].to_numpy(float)
        by[d] = {"codes": codes, "fwd": g["fwd20"].to_numpy(float),
                 "pct": g["f3pct"].to_numpy(float),
                 "pool": np.where((g["f3pct"].to_numpy(float) <= THRESH) & (lq < LIQ_MAX))[0],
                 "cmap": {c: i for i, c in enumerate(codes)},
                 "n_band": int((lq < LIQ_MAX).sum())}
        n_band.append(by[d]["n_band"])
    months = sorted(by)

    c2, c1, c0 = [], [], []
    for path in range(n_paths):

        def sim(mode, ns_fixed=None):
            rng = np.random.default_rng(SEED + path)
            b0 = by[months[0]]
            if len(b0["pool"]) < K:
                return None, None
            hold = [b0["codes"][i] for i in rng.choice(b0["pool"], K, replace=False)]
            rets, ns, holds = [], [], []
            for t, d in enumerate(months):
                b = by[d]
                n = 0
                if t > 0 and mode != "hold":
                    if mode == "f3":
                        sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                    else:
                        k = ns_fixed[t] if ns_fixed else 0
                        csel = [c for c in hold if c in b["cmap"]]
                        sell = list(rng.choice(csel, min(k, len(csel)), replace=False)) if k else []
                    n = len(sell)
                    if n:
                        held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                        cand = b["pool"][~np.isin(b["pool"], held)] if len(b["pool"]) else np.array([], int)
                        if len(cand) >= n:
                            hold = ([c for c in hold if c not in sell]
                                    + [b["codes"][i] for i in rng.choice(cand, n, replace=False)])
                        else:
                            n = 0
                vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
                rets.append((float(np.mean(vals)) if vals else 0.0) - n * 2 * SIDE_COST)
                ns.append(n)
                holds.append(list(hold))
            return rets, holds

        r2, h2 = sim("f3")
        if r2 is None:
            continue
        c2.append((r2, h2))
        ns2 = [0] + [len(set(h2[t - 1]) - set(h2[t])) for t in range(1, len(h2))]
        r1, _ = sim("random", ns2)
        c1.append(r1)
        r0, _ = sim("hold")
        c0.append(r0)
    return {"months": months, "c2": c2, "c1": c1, "c0": c0,
            "band_median": float(np.median(n_band)) if n_band else None}


def perf_monthly(r):
    r = np.asarray(r, float)
    eq = np.concatenate([[1.0], np.cumprod(1 + r)])
    peak = np.maximum.accumulate(eq)
    return float(eq[-1] ** (12 / len(r)) - 1), float(np.min(eq / peak - 1))


def dmetrics(nav):
    n = len(nav) - 1
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1
    rr = nav[1:] / nav[:-1] - 1
    return {"ann": float(nav[-1] ** (244.0 / n) - 1), "mdd": float(dd.min()),
            "vol": float(np.std(rr, ddof=1) * np.sqrt(244))}


def mix_nav(nav, w):
    rr = nav[1:] / nav[:-1] - 1
    return np.concatenate([[1.0], np.cumprod(1 + w * rr + (1 - w) * (CASH_ANN / 244.0))])


def nav_tier(holds_all, months, buy, nxt, cal_pos, px, tabs, tier):
    """独立实现的槽位引擎（与 p03 分开写，用于交叉校验）。

    tabs: {code: (dates, buy_ok, sell_ok)} —— 按股票预烤的成交位图，
    查询走 searchsorted；**查不到即视为不可成交**（与 p03 的 NO_BAR 同义）。
    """
    def ok(c, dt, side):
        t = tabs.get(c)
        if t is None:
            DIAG["nocode"] += 1
            return False
        dd, bo, so = t
        i = np.searchsorted(dd, dt)
        if i >= len(dd) or dd[i] != dt:
            DIAG["nodate"] += 1
            return False
        v = bool(bo[i]) if side == "buy" else bool(so[i])
        DIAG["true" if v else "false"] += 1
        return v
    navs, unf, turns = [], [], []
    for holds in holds_all:
        nav, u, tr = [1.0], [], []
        prev = None
        for t, me in enumerate(months):
            b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
            L = b1 - b0 + 1
            seg = slice(b0, b1 + 1)
            cur = holds[t]
            entries, n_side = [], 0
            if tier == "none":
                entries = [(c, buy[t]) for c in cur]
                n_side = 0 if prev is None else 2 * len([c for c in prev if c not in cur])
            elif prev is None:
                for c in cur:
                    if ok(c, buy[t], "buy"):
                        entries.append((c, buy[t])); n_side += 1
                    else:
                        entries.append((None, None))
            else:
                cont = [c for c in cur if c in prev]
                buys = [c for c in cur if c not in prev]
                sells = [c for c in prev if c not in cur]
                entries = [(c, buy[t]) for c in cont]
                for s, b in zip(sells, buys):
                    if not ok(s, buy[t], "sell"):
                        entries.append((s, buy[t]))
                    elif ok(b, buy[t], "buy"):
                        entries.append((b, buy[t])); n_side += 2
                    else:
                        entries.append((None, None)); n_side += 1
                # 上月有空槽（买入失败留现金）时，买单会比卖单多：
                # 多出的买单必须照常下单，否则空槽永久化、组合逐步退化成现金。
                for b in buys[len(sells):]:
                    if ok(b, buy[t], "buy"):
                        entries.append((b, buy[t])); n_side += 1
                    else:
                        entries.append((None, None))
                for s in sells[len(buys):]:
                    entries.append((s, buy[t]))
            entries = entries[:K] + [(None, None)] * max(0, K - len(entries))
            M = np.zeros((K, L)); neff = 0
            for j, (c, ent) in enumerate(entries):
                if c is None:
                    continue
                v = px.get(c)
                if v is None:
                    DIAG["pxmiss"] = DIAG.get("pxmiss", 0) + 1
                    continue
                v = v[seg]
                if not np.isfinite(v).any():
                    DIAG["pxnan"] = DIAG.get("pxnan", 0) + 1
                    continue
                epos = max(0, min(cal_pos.get(ent, b0) - b0, L - 1))
                good = np.isfinite(v[1:]) & np.isfinite(v[:-1]) & (v[:-1] > 0)
                M[j, 1:] = np.where(good, v[1:] / v[:-1] - 1, 0.0)
                if epos > 0:
                    M[j, 1:epos + 1] = 0.0
                neff += 1
            sl = M.sum(axis=0) / K
            for k in range(1, L):
                nav.append(nav[-1] * (1 + sl[k]))
            nav[-1] *= (1 - n_side * SIDE_COST)
            u.append((K - neff) / K); tr.append(n_side / 2 / K)
            prev = [c for c, _ in entries if c is not None][:K]
        nv = np.asarray(nav)
        navs.append(nv); unf.append(float(np.mean(u))); turns.append(float(np.mean(tr)))
    return navs, float(np.mean(unf)), float(np.mean(turns))


def main() -> int:
    prereg = check_prereg()
    os.makedirs(OUT, exist_ok=True)
    panel = pd.read_parquet(os.path.join(RD, "data", "panel_monthly.parquet"))
    st = pd.read_parquet(os.path.join(RD, "data", "st_sz_monthly.parquet"))
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    st_map = {(c, d): bool(v) for c, d, v in zip(st["code"], st["date"], st["is_st"])}

    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)

    cfgs = {"A": ("all", False), "B": ("SZ", False), "C": ("SZ", True)}
    dec = {k: build_decisions(panel, st_map, N_PATHS, m, ex) for k, (m, ex) in cfgs.items()}
    for k, d in dec.items():
        print(f"[{k}] {cfgs[k]} months={len(d['months'])} paths={len(d['c2'])} "
              f"band中位={d['band_median']}")

    # ---- 需要的股票集合（按股票预烤位图，避免路径依赖导致查表落空） ----
    codes = sorted({c for d in dec.values() for _, holds in d["c2"] for hs in holds for c in hs})
    print(f"[need] {len(codes)} 只股票")

    buy_of = {m: fe.month_buy_date(m, cal_pos, cal) for d in dec.values() for m in d["months"]}

    # ST 月份集合：{code: {'YYYYMM', ...}}
    st_months: dict[str, set[str]] = {}
    for (c, dt), v in st_map.items():
        if v:
            st_months.setdefault(c, set()).add(dt[:6])

    px, tabs, miss = {}, {}, 0
    for i, c in enumerate(codes):
        b = fe.load_bars(c)
        if b is None:
            miss += 1
            continue
        arr = np.full(len(cal), np.nan, dtype=np.float32)
        pos = np.searchsorted(cal_arr, b.d)
        ok = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        arr[pos[ok]] = b.qc[ok].astype(np.float32)
        px[c] = pd.Series(arr).ffill().to_numpy(dtype=np.float32)
        tabs[c] = fe.status_tables(b, st_months.get(c))
        if (i + 1) % 1000 == 0:
            print(f"    已处理 {i+1}/{len(codes)}")
    print(f"[bars] 读到 {len(px)} 只，缺 {miss} 只")

    def evaluate(k):
        d = dec[k]
        months = d["months"]
        buy = [buy_of[m] for m in months]
        nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]
        out = {"months": len(months), "paths": len(d["c2"]), "band_median": d["band_median"]}
        for label, tier in (("T0", "none"), ("T2", "strict")):
            navs, unf, turn = nav_tier([h for _, h in d["c2"]], months, buy, nxt,
                                       cal_pos, px, tabs, tier)
            m100 = [dmetrics(v) for v in navs]
            m25 = [dmetrics(mix_nav(v, 0.25)) for v in navs]
            m20 = [dmetrics(mix_nav(v, 0.20)) for v in navs]
            if label == "T2":
                out["diag"] = dict(DIAG)
            out[label] = {"ann100": float(np.nanmedian([x["ann"] for x in m100])),
                          "mdd100": float(np.nanmedian([x["mdd"] for x in m100])),
                          "ann25": float(np.nanmedian([x["ann"] for x in m25])),
                          "mdd25": float(np.nanmedian([x["mdd"] for x in m25])),
                          "ann20": float(np.nanmedian([x["ann"] for x in m20])),
                          "mdd20": float(np.nanmedian([x["mdd"] for x in m20])),
                          "cash": unf, "turnover": turn}
        a2 = np.array([perf_monthly(r)[0] for r, _ in d["c2"]])
        a1 = np.array([perf_monthly(r)[0] for r in d["c1"]])
        a0 = np.array([perf_monthly(r)[0] for r in d["c0"]])
        out["monthly"] = {"c2": float(np.median(a2)), "c1": float(np.median(a1)),
                          "hold": float(np.median(a0)),
                          "f3_increment_pp": float(np.median(a2) - np.median(a1))}
        return out

    print("[A] 全市场 / 不剔 ST —— 校验门（须复现 P0-3）")
    A = evaluate("A")
    # 校验门：与 P0-3 **同一路径数**的结果逐项比对（路径数不同不可比）
    ref = json.load(open(os.path.join(OUT, "sp5_tradability.json"), encoding="utf-8"))
    same_n = (N_PATHS == ref["n_paths"])
    diffs = {}
    if same_n:
        for lbl, key in (("T0", "none"), ("T2", "strict")):
            for f in ("ann25", "mdd25"):
                diffs[f"{lbl}.{f}"] = A[lbl][f] - ref["tiers"][key][f]
    gate = SKIP_GATE or (same_n and all(abs(v) < 5e-4 for v in diffs.values()))
    print(f"    T0 25% {A['T0']['ann25']*100:+.2f}%/{A['T0']['mdd25']*100:+.2f}%  "
          f"T2 25% {A['T2']['ann25']*100:+.2f}%/{A['T2']['mdd25']*100:+.2f}% cash {np.mean(A['T2']['cash'])*100:.2f}%  "
          f"→ {'PASS' if gate else 'FAIL'} {diffs if diffs else '(路径数不同，跳过逐项比对)'}")
    if not gate:
        print("[abort] 独立实现与 P0-3 不一致，结果不可用")
        return 2

    print("[B] 深市 / 不剔 ST")
    B = evaluate("B")
    print(f"    T0 25% {B['T0']['ann25']*100:+.2f}%/{B['T0']['mdd25']*100:+.2f}%  "
          f"T2 25% {B['T2']['ann25']*100:+.2f}%/{B['T2']['mdd25']*100:+.2f}%")
    print("[C] 深市 / 剔 ST")
    C = evaluate("C")
    print(f"    T0 25% {C['T0']['ann25']*100:+.2f}%/{C['T0']['mdd25']*100:+.2f}%  "
          f"T2 25% {C['T2']['ann25']*100:+.2f}%/{C['T2']['mdd25']*100:+.2f}%")

    d_ann = C["T0"]["ann25"] - B["T0"]["ann25"]
    d_mdd = C["T0"]["mdd25"] - B["T0"]["mdd25"]
    d_f3 = C["monthly"]["f3_increment_pp"] - B["monthly"]["f3_increment_pp"]
    sz_verdict = ("ST_EFFECT_NEGLIGIBLE" if (abs(d_ann) <= 0.01 and abs(d_mdd) <= 0.01)
                  else "ST_EFFECT_MATERIAL")
    verdict = "ST_PARTIAL_CANNOT_VERIFY"

    print("\n" + "=" * 88)
    print(f"深市子样本：剔 ST 后 Δ年化 {d_ann*100:+.2f}pp ｜ ΔMDD {d_mdd*100:+.2f}pp "
          f"｜ΔF3增量 {d_f3*100:+.2f}pp")
    print(f"深市判定 {sz_verdict}（阈值 |Δ年化|≤1.0pp 且 |ΔMDD|≤1.0pp）")
    print(f"总体判定 {verdict}（沪市 43.9% 的 code-month 无 ST 真值）")

    res = {"prereg_sha256": prereg, "verdict": verdict, "verdict_sz": sz_verdict,
           "thresholds": {"d_ann_pp": 0.01, "d_mdd_pp": 0.01},
           "n_paths": N_PATHS,
           "A_all_market": A, "B_sz_no_st": B, "C_sz_ex_st": C,
           "delta": {"ann25_pp": d_ann, "mdd25_pp": d_mdd, "f3_increment_pp": d_f3,
                     "band_median_B": B["band_median"], "band_median_C": C["band_median"]},
           "g2_sh_proxy": "NOT_RUN —— 沪市无历史 ST 表，代理无法标定（0 覆盖），不构造",
           "limitations": [
               "只覆盖深市（000/001/002/003/300/301/302）；沪市结构性不同，不可外推",
               "ST 为月末口径，月内进出 ST 不建模；只按『简称含 ST』判定，不区分 *ST/SST",
               "剔 ST 会缩小池子 → 结果含少量『池子变小』效应，不是纯 ST 效应",
               "T0 为无成交约束口径；T2 为严格档（买入失败留现金、卖出失败继续持有）",
           ]}
    with open(os.path.join(OUT, "sp5_st_effect.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("[saved] sp5_out/sp5_st_effect.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
