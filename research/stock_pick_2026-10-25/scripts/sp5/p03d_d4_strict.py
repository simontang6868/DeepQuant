# -*- coding: utf-8 -*-
"""SP5-D4：d4 严格口径（沪深主板）下的 sp4-v1 回测（R3-2，预登记实验）

复制自 p03_tradability.py。与原版的**全部差异**（逐条见 SP5_D4_PREREG.md §2）：
  1. build_decisions 增加可选池过滤：pool=="d4" 时在档内分母重算**之前**把
     池限定为 BOARD_D4_STRICT（沪深主板）——这是本实验唯一的方法学改动；
  2. 预登记校验指向 SP5_D4_PREREG.md / sp5_d4_prereg.sha256；
  3. 输出目录改为 d4_strict_out/，文件名带池标签；
  4. JSON 增加每月档内可买数序列（pool_counts，R3-2 指定指标）；
  5. 引擎自检（审计口径复现）只在 --pool wide 臂生效。
K/THRESH/LIQ_MAX/SEED/SIDE_COST/CASH_ANN/三档建模/撮合逻辑：**逐字未动**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402

ROOT = fe.ROOT
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "d4_strict_out")

K, THRESH, LIQ_MAX = 5, 0.70, 0.20
SEED = 20261026
SIDE_COST = 0.000102          # 单边成本（= 原口径 0.0204%/换 1 只 的一半）
CASH_ANN = 0.03
POOL_BOARDS = frozenset({"SH_MAIN", "SZ_MAIN"})   # R3-2：d4 严格口径（唯一方法学改动）


def check_prereg() -> str:
    want = open(os.path.join(RD, "sp5_d4_prereg.sha256"), encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD, "SP5_D4_PREREG.md"), "rb").read()).hexdigest()
    print(f"[prereg] {want[:16]}… {'OK' if want == got else 'MISMATCH'}")
    if want != got:
        sys.exit("[abort] 预登记哈希不一致，拒绝运行")
    return want


def perf_monthly(r):
    r = np.asarray(r, float)
    eq = np.concatenate([[1.0], np.cumprod(1 + r)])
    ann = eq[-1] ** (12 / len(r)) - 1 if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    return float(ann), float(np.min(eq / peak - 1))


def dmetrics(nav: np.ndarray) -> dict:
    n = len(nav) - 1
    ann = nav[-1] ** (244.0 / n) - 1 if n > 20 and nav[-1] > 0 else np.nan
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1
    rr = nav[1:] / nav[:-1] - 1
    return {"ann": float(ann), "mdd": float(dd.min()),
            "vol": float(np.std(rr, ddof=1) * np.sqrt(244)) if n > 2 else np.nan}


def mix_nav(nav_stock: np.ndarray, w: float) -> np.ndarray:
    rr = nav_stock[1:] / nav_stock[:-1] - 1
    pr = w * rr + (1 - w) * (CASH_ANN / 244.0)
    return np.concatenate([[1.0], np.cumprod(1 + pr)])


def build_decisions(panel: pd.DataFrame, n_paths: int, pool: str = "d4"):
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    if pool == "d4":
        # R3-2 唯一方法学改动：过滤在档内分母重算**之前**，f3pct/liqpct 是 d4 档内分母
        p = p[p["code"].map(lambda c: fe.board_of(c) in POOL_BOARDS)]
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]

    by = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        codes = g["code"].to_numpy()
        f3p = g["f3pct"].to_numpy(float)
        lq = g["liqpct"].to_numpy(float)
        by[d] = {
            "codes": codes,
            "fwd": g["fwd20"].to_numpy(float),
            "pct": f3p,
            "pool": np.where((f3p <= THRESH) & (lq < LIQ_MAX))[0],
            "cmap": {c: i for i, c in enumerate(codes)},
        }
    months = sorted(by)

    paths, ns_list, monthly = [], [], []
    for path in range(n_paths):
        rng = np.random.default_rng(SEED + path)
        b0 = by[months[0]]
        if len(b0["pool"]) < K:
            continue
        hold = [b0["codes"][i] for i in rng.choice(b0["pool"], K, replace=False)]
        rets, ns, holds = [], [], []
        for t, d in enumerate(months):
            b = by[d]
            n = 0
            if t > 0:
                sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
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
        paths.append(holds)
        ns_list.append(ns)
        monthly.append(rets)
    pool_counts = {d: int(len(b["pool"])) for d, b in by.items()}
    return by, months, paths, ns_list, monthly, pool_counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=200)
    ap.add_argument("--pool", choices=["wide", "d4"], default="d4")
    args = ap.parse_args()
    prereg = check_prereg()
    os.makedirs(OUT, exist_ok=True)

    panel = pd.read_parquet(PANEL)
    by, months, paths, ns_list, monthly, pool_counts = build_decisions(panel, args.paths, args.pool)

    # ---------- 步骤 0：先复现审计口径（不通过就停） ----------
    ann0 = np.array([perf_monthly(r)[0] for r in monthly])
    mdd0 = np.array([perf_monthly(r)[1] for r in monthly])
    print(f"[repro] 月频 C2 年化中位 {np.median(ann0)*100:+.2f}%（审计 30.35%）"
          f"｜月频 MDD 中位 {np.median(mdd0)*100:+.2f}%（审计 −62.38%）"
          f"｜{len(paths)} 路径 / {len(months)} 月")
    expect_audit = (args.pool == "wide")
    ok_repro = ((abs(np.median(ann0) - 0.3035) < 0.005)
                if (expect_audit and len(paths) >= 50) else True)
    print(f"[repro] {'PASS' if ok_repro else 'FAIL —— 引擎与审计口径不一致，停止'}"
          + ("" if expect_audit else "（d4 臂：本自检在 wide 臂执行）")
          + ("" if len(paths) >= 50 else "（路径数 < 50，仅打印不复核）"))
    if not ok_repro:
        return 2

    # ---------- 步骤 1：日历 / 建仓日 ----------
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]
    print(f"[cal] {len(cal)} 交易日；建仓日 {buy[0]} ~ {buy[-1]}")

    # ---------- 步骤 2：ST 与价格 ----------
    st = pd.read_parquet(os.path.join(RD, "data", "st_sz_monthly.parquet"))
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    st_map = {(c, d): bool(v) for c, d, v in zip(st["code"], st["date"], st["is_st"])}

    needed = set()
    for holds in paths:
        for h in holds:
            needed.update(h)
    needed = sorted(needed)
    bars, missing = {}, []
    for c in needed:
        b = fe.load_bars(c)
        (bars.__setitem__(c, b) if b is not None else missing.append(c))
    print(f"[bars] 需要 {len(needed)} 只，读到 {len(bars)} 只，缺 {len(missing)} 只")

    # 日历对齐的前复权收盘价（一次算好，切片复用）
    px = {}
    for c, b in bars.items():
        arr = np.full(len(cal), np.nan)
        pos = np.searchsorted(cal_arr, b.d)
        ok = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        arr[pos[ok]] = b.qc[ok]
        px[c] = pd.Series(arr).ffill().to_numpy(float)

    # ---------- 步骤 3：逐单撮合 ----------
    ledger = {}
    buy_of = dict(zip(months, buy))

    def st_of(code, me):
        return st_map.get((code, me), False)

    def judge_pair(code, me, side, sh_mode="base"):
        key = (me, code, side, sh_mode)
        if key in ledger:
            return ledger[key]
        b = bars.get(code)
        d = buy_of[me]
        if b is None:
            rec = {"status": "NO_BAR", "defer": None, "limit": np.nan, "is_st": False}
        else:
            s, lim = fe.judge(b, d, st_of(code, me), side, sh_mode)
            dfr = None
            if s != "FILLED":
                dfr, _ = fe.deferred(b, d, cal, cal_pos, st_of(code, me), side, sh_mode)
            rec = {"status": s, "defer": dfr, "limit": lim, "is_st": st_of(code, me)}
        ledger[key] = rec
        return rec

    order_count = defaultdict(int)
    for pid, holds in enumerate(paths):
        prev = None
        for t, me in enumerate(months):
            cur = holds[t]
            if prev is None:
                for c in cur:
                    order_count[(me, c, "buy")] += 1
            else:
                for c in cur:
                    if c not in prev:
                        order_count[(me, c, "buy")] += 1
                for c in prev:
                    if c not in cur:
                        order_count[(me, c, "sell")] += 1
            prev = cur
    print(f"[orders] 去重后 {len(order_count)} 笔（含各路径重复计数）")

    for (me, c, side), _ in order_count.items():
        judge_pair(c, me, side)

    # 沪市 5% 双界敏感性（只统计失败率，不重跑净值）
    sh5_diff = 0
    for (me, c, side), n in order_count.items():
        a = judge_pair(c, me, side, "base")["status"]
        b5 = judge_pair(c, me, side, "sh5")["status"]
        if a == "FILLED" and b5 != "FILLED":
            sh5_diff += n

    # ---------- 步骤 4：三档净值 ----------
    def run_tier(tier: str) -> dict:
        """槽位模型：始终 5 个槽，每槽或持股（含入场日）或现金。

        * 卖不掉 → 该槽继续持有原股，对应的替代买入取消（**保守**：不建模顺延资金到账）；
        * 买不到 → 该槽留现金（宽松档允许顺延到 ≤10 交易日的第一可成交日）；
        * 最坏档 → 任一笔失败则当月整笔调仓作废，维持上月持仓。
        """
        navs, m100, m25, m20, cash_res, turn = [], [], [], [], [], []
        for pid, holds in enumerate(paths):
            nav = [1.0]
            unfilled, swaps = [], []
            prev = None
            for t, me in enumerate(months):
                b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
                seg = slice(b0, b1 + 1)
                L = b1 - b0 + 1
                cur = holds[t]
                entries: list[tuple[str | None, str | None]] = []
                n_side = 0

                if tier == "none":
                    # 基准 T0：无约束，全部按建仓日成交（用来复现审计口径）
                    entries = [(c, buy[t]) for c in cur]
                    n_side = 0 if prev is None else 2 * len([c for c in prev if c not in cur])
                elif prev is None:
                    for c in cur:
                        r = judge_pair(c, me, "buy")
                        if r["status"] == "FILLED":
                            entries.append((c, buy[t])); n_side += 1
                        elif tier == "lenient" and r["defer"]:
                            entries.append((c, r["defer"])); n_side += 1
                        else:
                            entries.append((None, None))
                else:
                    cont = [c for c in cur if c in prev]
                    buys = [c for c in cur if c not in prev]
                    sells = [c for c in prev if c not in cur]
                    void = False
                    if tier == "worst":
                        void = any(judge_pair(s, me, "sell")["status"] != "FILLED" for s in sells) \
                            or any(judge_pair(b, me, "buy")["status"] != "FILLED" for b in buys)
                    if void:
                        entries = [(c, buy[t]) for c in prev]
                        n_side = 0
                    else:
                        entries = [(c, buy[t]) for c in cont]
                        for s, b in zip(sells, buys):
                            rs, rb = judge_pair(s, me, "sell"), judge_pair(b, me, "buy")
                            if rs["status"] != "FILLED":
                                entries.append((s, buy[t]))            # 卖不掉 → 继续持有
                            elif rb["status"] == "FILLED":
                                entries.append((b, buy[t])); n_side += 2
                            elif tier == "lenient" and rb["defer"]:
                                entries.append((b, rb["defer"])); n_side += 2
                            else:
                                entries.append((None, None)); n_side += 1
                        for b in buys[len(sells):]:                    # 异常：多出的买入
                            rb = judge_pair(b, me, "buy")
                            if rb["status"] == "FILLED":
                                entries.append((b, buy[t])); n_side += 1
                            elif tier == "lenient" and rb["defer"]:
                                entries.append((b, rb["defer"])); n_side += 1
                            else:
                                entries.append((None, None))

                entries = entries[:K] + [(None, None)] * max(0, K - len(entries))
                slot_ret = np.zeros((K, L))
                n_eff = 0
                for j, (c, ent) in enumerate(entries):
                    if c is None:
                        continue
                    arr = px.get(c)
                    if arr is None:
                        continue
                    p = arr[seg]
                    if not np.isfinite(p).any():
                        continue
                    epos = max(0, min(cal_pos.get(ent, b0) - b0, L - 1))
                    good = np.isfinite(p[1:]) & np.isfinite(p[:-1]) & (p[:-1] > 0)
                    slot_ret[j, 1:] = np.where(good, p[1:] / p[:-1] - 1, 0.0)
                    if epos > 0:
                        slot_ret[j, 1:epos + 1] = 0.0                 # 入场前持现金
                    n_eff += 1
                sleeve = slot_ret.sum(axis=0) / K
                for k in range(1, L):
                    nav.append(nav[-1] * (1 + sleeve[k]))
                if len(nav) > 1:
                    nav[-1] *= (1 - n_side * SIDE_COST)
                unfilled.append((K - n_eff) / K)
                swaps.append(n_side / 2 / K)
                prev = [c for c, _ in entries if c is not None][:K]
            nv = np.asarray(nav)
            navs.append(nv)
            m100.append(dmetrics(nv))
            m25.append(dmetrics(mix_nav(nv, 0.25)))
            m20.append(dmetrics(mix_nav(nv, 0.20)))
            cash_res.append(float(np.mean(unfilled)))
            turn.append(float(np.mean(swaps)))
        return {"navs": navs, "m100": m100, "m25": m25, "m20": m20,
                "cash": cash_res, "turn": turn}

    tiers = {}
    for tier in ("none", "lenient", "strict", "worst"):
        r = run_tier(tier)
        tiers[tier] = r
        a100 = np.nanmedian([x["ann"] for x in r["m100"]])
        d100 = np.nanmedian([x["mdd"] for x in r["m100"]])
        a25 = np.nanmedian([x["ann"] for x in r["m25"]])
        d25 = np.nanmedian([x["mdd"] for x in r["m25"]])
        a20 = np.nanmedian([x["ann"] for x in r["m20"]])
        d20 = np.nanmedian([x["mdd"] for x in r["m20"]])
        print(f"[{tier:8s}] 100% {a100*100:+6.2f}%/{d100*100:+7.2f}%  "
              f"25% {a25*100:+6.2f}%/{d25*100:+7.2f}%  20% {a20*100:+6.2f}%/{d20*100:+7.2f}%  "
              f"现金残留 {np.mean(r['cash'])*100:5.2f}%  月换手 {np.mean(r['turn'])*100:5.1f}%")

    # ---------- 步骤 5：失败率统计 ----------
    rows = []
    for (me, c, side), n in order_count.items():
        rec = judge_pair(c, me, side)
        rows.append({"month": me, "date": buy[months.index(me)], "code": c, "side": side,
                     "status": rec["status"], "defer": rec["defer"] or "",
                     "limit_pct": rec["limit"], "is_st": rec["is_st"], "n_orders": n})
    led = pd.DataFrame(rows).sort_values(["date", "code", "side"])
    led["yr"] = led["date"].str[:4]
    led.to_csv(os.path.join(OUT, f"sp5_fill_ledger_{args.pool}.csv"), index=False, encoding="utf-8-sig")

    stat = {}
    for side in ("buy", "sell"):
        s = led[led["side"] == side]
        tot = int(s["n_orders"].sum())
        fail = int(s.loc[s["status"] != "FILLED", "n_orders"].sum())
        stat[side] = {"orders": tot, "failed": fail,
                      "fail_rate": round(fail / tot, 5) if tot else None,
                      "by_status": s.groupby("status")["n_orders"].sum().to_dict()}
    by_year = (led.assign(bad=led["status"] != "FILLED", w=led["n_orders"])
                 .groupby("yr").apply(lambda g: pd.Series({
                     "orders": int(g["w"].sum()),
                     "fail": int(g.loc[g["bad"], "w"].sum()),
                     "rate": round(g.loc[g["bad"], "w"].sum() / max(g["w"].sum(), 1), 5)}),
                     include_groups=False)
                 .reset_index().to_dict(orient="records"))

    months_with_fail = int((led[led["status"] != "FILLED"]
                            .groupby("month")["n_orders"].sum() > 0).sum())

    # ---------- 步骤 6：判定 ----------
    def med(tier, key, field):
        return float(np.nanmedian([x[field] for x in tiers[tier][key]]))

    net25 = {t: med(t, "m25", "ann") for t in ("lenient", "strict", "worst")}
    mdd25 = {t: med(t, "m25", "mdd") for t in ("lenient", "strict", "worst")}
    if min(net25.values()) > 0.08:
        verdict = "FILL_ABSORBED"
    elif net25["worst"] <= 0:
        verdict = "FILL_KILLS"
    else:
        verdict = "FILL_PARTIAL_CANNOT_VERIFY" if sh5_diff > 0.05 * sum(order_count.values()) else "FILL_KILLS"

    res = {
        "prereg_sha256": prereg, "pool": args.pool,
        "pool_counts_monthly": pool_counts,
        "pool_counts_median": float(np.median(list(pool_counts.values()))),
        "n_paths": len(paths), "months": len(months),
        "repro": {"ann_median": float(np.median(ann0)), "mdd_median": float(np.median(mdd0)),
                  "match_audit": bool(ok_repro)},
        "tiers": {t: {"ann100": med(t, "m100", "ann"), "mdd100": med(t, "m100", "mdd"),
                      "ann25": med(t, "m25", "ann"), "mdd25": med(t, "m25", "mdd"),
                      "ann20": med(t, "m20", "ann"), "mdd20": med(t, "m20", "mdd"),
                      "cash_residual": float(np.mean(tiers[t]["cash"])),
                      "turnover": float(np.mean(tiers[t]["turn"]))}
                  for t in tiers},
        "orders": stat,
        "by_year": by_year,
        "months_with_any_fail": months_with_fail,
        "sh5_extra_fails": int(sh5_diff),
        "verdict": verdict,
        "limitations": [
            "无委托簿与逐笔成交：排队位置、盘中撤单、集合竞价撮合无法验证",
            "部分成交未建模：按「理论可成交」处理，依据是参与率中位 0.0054%（W/ADV）",
            "决策固定：三档共用同一路径的原始持仓，未做「没买上→改变下月选择」的反馈",
            "沪市 ST 无真值：base 用 10%、sh5 给失败率上界，两者都不等于真值",
            "一字板判定用 high==low 且前复权收益达限（TOL 0.2pp）；真实排队可能仍能成交",
            "顺延上限 10 个交易日；超出即视为该槽位为空",
        ],
        "note": ("T0(none) 必须复现审计口径；不通过则整个 P0-3 结果作废。"
                 "成交约束是否被吸收（净年化）与回撤是否达标（P0-4）是两件事，不得互相暗示。"),
    }
    with open(os.path.join(OUT, f"sp5_tradability_{args.pool}.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 92)
    print("失败订单统计")
    print("=" * 92)
    for side in ("buy", "sell"):
        s = stat[side]
        print(f"  {side}: {s['orders']} 笔，失败 {s['failed']}（{s['fail_rate']*100:.2f}%） {s['by_status']}")
    print(f"  有失败的月份 {months_with_fail}/{len(months)}；沪市 5% 双界额外失败 {sh5_diff} 笔")
    print(f"\n【判定】{verdict}")
    if verdict == "FILL_ABSORBED":
        print("  ⚠️ 仅表示「成交约束未吃掉 8% 目标」；回撤是否达标归 P0-4，不得据此外推策略可用。")
    print(f"[saved] d4_strict_out/sp5_tradability_{args.pool}.json + sp5_fill_ledger_{args.pool}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
