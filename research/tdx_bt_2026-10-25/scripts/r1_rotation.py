# -*- coding: utf-8 -*-
"""阶段 R1：跨风格 ETF 双周动量轮动。

按 docs/TDX_R1_PREREG_2026-10-25.md 执行，顺序不可颠倒：
  第一层 信号有效性（edge 时序检验）→ 不过则直接结题，不跑组合
  第二层 组合表现（仅当 S1/S2/S3 全过）

标的池（7 只，按风格类别完整性选，见预登记 §1）：
  510050 上证50 / 510300 沪深300 / 510500 中证500 / 159915 创业板
  510880 红利 / 513100 纳指 / 511010 国债

成本（10 万资金，万2.5 + 5 元最低）：换掉 1 只 = 卖 5 万 + 买 5 万 = 25 元 = 0.025% 总资金
"""
import os, sys, json, glob
from statistics import mean, median

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

CODES = ["510050", "510300", "510500", "159915", "510880", "513100", "511010"]
NAMES = {"510050": "上证50", "510300": "沪深300", "510500": "中证500",
         "159915": "创业板", "510880": "红利", "513100": "纳指", "511010": "国债"}
MOM, STEP, TOPK = 20, 10, 2
COST_PER_SWAP = 0.00025      # 0.025% 总资金 / 换掉 1 只
CAP = 100000.0


def load_panel():
    panel = {c: {} for c in CODES}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period", "")).split(".")[0] != "4":
            continue
        c = str(d.get("Code", "")).strip()
        if c not in panel:
            continue
        for r in d["Rows"]:
            try:
                panel[c][str(r["Data"])] = float(r["Close"])
            except Exception:
                continue
    return panel


def tstat(xs):
    n = len(xs)
    mu = mean(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / (n - 1)) ** 0.5
    se = sd / n ** 0.5
    return mu, (mu / se if se > 0 else 0.0), se


def main():
    panel = load_panel()
    cal = sorted(set.intersection(*[set(panel[c]) for c in CODES]))
    print(f"共同交易日 {len(cal)}：{cal[0]} ~ {cal[-1]}")
    print(f"标的 {len(CODES)} 只：{' '.join(NAMES[c] for c in CODES)}")

    # ---------- 第一层：信号有效性 ----------
    edges, picks, sel_cnt = [], [], {c: 0 for c in CODES}
    per_etf_fwd = {c: [] for c in CODES}
    sig_dates = []
    i = MOM
    while i + STEP < len(cal):
        d0, d1 = cal[i], cal[i + STEP]
        mom = {}
        ok = True
        for c in CODES:
            p0, p1 = panel[c].get(cal[i - MOM]), panel[c].get(d0)
            if not p0 or not p1 or p0 <= 0:
                ok = False
                break
            mom[c] = p1 / p0 - 1
        if not ok:
            i += STEP
            continue
        fwd = {}
        for c in CODES:
            a, b = panel[c].get(d0), panel[c].get(d1)
            if a and b and a > 0:
                fwd[c] = b / a - 1
        if len(fwd) < len(CODES):
            i += STEP
            continue
        top = sorted(mom, key=lambda c: -mom[c])[:TOPK]
        e = mean(fwd[c] for c in top) - mean(fwd.values())
        edges.append(e)
        picks.append((d0, top, e))
        sig_dates.append(d0)
        for c in CODES:
            per_etf_fwd[c].append(fwd[c] - mean(fwd.values()))
        for c in top:
            sel_cnt[c] += 1
        i += STEP

    mu, t, se = tstat(edges)
    mde = 2.8016 * se
    win = sum(1 for x in edges if x > 0) / len(edges)

    print("\n" + "=" * 76)
    print("第一层：20 日动量 → 未来 10 日相对收益（动量前 2 只 − 全池等权）")
    print("=" * 76)
    print(f"  期数 {len(edges)}｜edge 均值 {mu*100:+.3f}pp｜t {t:+.2f}｜胜率 {win*100:.1f}%｜MDE {mde*100:.3f}pp")

    order = sorted(range(len(edges)), key=lambda k: -edges[k])
    drop3 = [edges[k] for k in range(len(edges)) if k not in order[:3]]
    mu3, t3, _ = tstat(drop3)
    print(f"  剔掉 edge 最大 3 期后：均值 {mu3*100:+.3f}pp｜t {t3:+.2f}（n={len(drop3)}）")

    S1 = (mu > 0 and abs(t) >= 1.96)
    S2 = (win >= 0.55)
    S3 = (mu3 > 0 and abs(t3) >= 1.5)
    print(f"\n  S1 方向与量级（>0 且 |t|≥1.96）: {'过' if S1 else '不过'}")
    print(f"  S2 胜率（≥55%）            : {'过' if S2 else '不过'}（{win*100:.1f}%）")
    print(f"  S3 剔除极端 3 期后仍成立     : {'过' if S3 else '不过'}")

    print("\n  各标的被选中次数 / 平均相对收益：")
    for c in sorted(CODES, key=lambda x: -sel_cnt[x]):
        rel = mean(per_etf_fwd[c]) if per_etf_fwd[c] else 0.0
        print(f"    {NAMES[c]:<8} 选中 {sel_cnt[c]:>3}/{len(edges)} 次   平均相对收益 {rel*100:+.3f}pp/期")

    signal_ok = S1 and S2 and S3
    out = {"window": [cal[0], cal[-1]], "n_periods": len(edges), "codes": CODES,
           "layer1": {"edge_mean": mu, "t": t, "win": win, "mde": mde,
                      "mean_drop3": mu3, "t_drop3": t3,
                      "S1": S1, "S2": S2, "S3": S3, "passed": signal_ok,
                      "sel_cnt": sel_cnt,
                      "rel_by_etf": {c: mean(per_etf_fwd[c]) for c in CODES}},
           "edges": edges, "picks": [[d, t2, e] for d, t2, e in picks]}

    if not signal_ok:
        print("\n" + "=" * 76)
        print("判定：MOMENTUM_NO_EDGE —— 信号层未通过，按预登记 §4.1 直接结题，不跑组合回测。")
        print("=" * 76)
        out["verdict"] = "MOMENTUM_NO_EDGE"
        with open(os.path.join(RD, "pool", "r1_rotation.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("saved pool/r1_rotation.json")
        return

    # ---------- 第二层：组合表现（仅信号通过才跑）----------
    def backtest(step):
        # 从第一个可算信号开始，按 step 个交易日调仓
        start_i = MOM
        w = {}
        cur = {}
        nav = 1.0
        curve, dates = [], []
        swaps = 0
        cost_sum = 0.0
        prev_d = None
        last_pick = None
        j = start_i
        # 先定初始持仓
        d0 = cal[j]
        mom = {c: panel[c][d0] / panel[c][cal[j - MOM]] - 1 for c in CODES}
        cur = {c: 1.0 / TOPK for c in sorted(mom, key=lambda x: -mom[x])[:TOPK]}
        last_pick = d0
        for k in range(j, len(cal) - 1):
            d = cal[k]
            nd = cal[k + 1]
            # 调仓
            if k > j and (k - j) % step == 0:
                mom = {c: panel[c][d] / panel[c][cal[k - MOM]] - 1 for c in CODES}
                tgt = set(sorted(mom, key=lambda x: -mom[x])[:TOPK])
                n_swap = len(tgt.symmetric_difference(set(cur))) // 2
                if n_swap:
                    cst = n_swap * COST_PER_SWAP
                    nav *= (1 - cst)
                    cost_sum += cst
                    swaps += n_swap
                cur = {c: 1.0 / TOPK for c in tgt}
            # 当日收益
            r = 0.0
            for c, wt in cur.items():
                a, b = panel[c].get(d), panel[c].get(nd)
                if a and b and a > 0:
                    r += wt * (b / a - 1)
            nav *= (1 + r)
            curve.append(nav)
            dates.append(nd)
        return curve, dates, swaps, cost_sum

    def bench():
        # 等权持有 7 只，固定股数
        d0 = cal[MOM]
        w = {c: 1.0 / len(CODES) for c in CODES}
        nav = 1.0
        curve, dates = [], []
        base = {c: panel[c][d0] for c in CODES}
        for k in range(MOM, len(cal) - 1):
            d, nd = cal[k], cal[k + 1]
            r = 0.0
            for c in CODES:
                # 从 d0 起累计
                v0 = base[c]
                vd = panel[c].get(d, v0)
                vn = panel[c].get(nd, vd)
                r += w[c] * (vn / vd - 1) if vd > 0 else 0.0
            nav *= (1 + r)
            curve.append(nav)
            dates.append(nd)
        return curve, dates

    def stats(curve, dates, label):
        from datetime import date
        n = len(curve)
        d0 = date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:]))
        d1 = date(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:]))
        yrs = (d1 - d0).days / 365.25
        tot = curve[-1] / curve[0] - 1
        ann = (curve[-1] / curve[0]) ** (1 / yrs) - 1
        peak, mdd, mdd_at = curve[0], 0.0, None
        pk = dates[0]
        for d, v in zip(dates, curve):
            if v > peak:
                peak, pk = v, d
            dd = v / peak - 1
            if dd < mdd:
                mdd, mdd_at = dd, (pk, d)
        rets = [curve[k] / curve[k - 1] - 1 for k in range(1, n) if curve[k - 1] > 0]
        vol = (sum((x - mean(rets)) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
        return {"label": label, "years": yrs, "total": tot, "annual": ann, "mdd": mdd,
                "mdd_at": mdd_at, "vol": vol,
                "calmar": ann / abs(mdd) if mdd < 0 else None}

    c2, d2, sw2, cost2 = backtest(STEP)
    c1, d1_, sw1, cost1 = backtest(21)      # 近似月频
    cb, db = bench()

    s2 = stats(c2, d2, "双周轮动(20日动量前2)")
    s1 = stats(c1, d1_, "月频轮动(对照)")
    sb = stats(cb, db, "等权持有7只(基准)")

    print("\n" + "=" * 76)
    print("第二层：组合表现（扣费后）")
    print("=" * 76)
    print(f"{'策略':<24}{'年数':>6}{'累计':>11}{'年化':>9}{'日频MDD':>10}{'波动':>9}{'Calmar':>8}{'换手':>6}")
    for s in (s2, s1, sb):
        print(f"{s['label']:<24}{s['years']:>6.2f}{s['total']*100:>10.2f}%{s['annual']*100:>8.2f}%"
              f"{s['mdd']*100:>9.2f}%{s['vol']*100:>8.2f}%{s['calmar']:>8.2f}")
    print(f"\n  双周口径：换掉 {sw2} 次，累计成本 {cost2*100:.2f}%（毛收益 {s2['total']*100:.2f}%）")
    print(f"  月频口径：换掉 {sw1} 次，累计成本 {cost1*100:.2f}%")

    G1 = s2["annual"] > sb["annual"]
    G2 = s2["mdd"] >= -0.20
    G3 = s2["annual"] > 0
    if G1 and G2 and G3:
        verdict = "ROTATION_USABLE"
    elif not G1:
        verdict = "ROTATION_LOSES_TO_PASSIVE"
    elif not G2:
        verdict = "ROTATION_DRAWDOWN_BREACH"
    else:
        verdict = "ROTATION_LOSES_MONEY"
    print(f"\n【判定】G1 跑赢基准({'过' if G1 else '不过'}) G2 回撤≥−20%({'过' if G2 else '不过'}) "
          f"G3 年化>0({'过' if G3 else '不过'}) → {verdict}")

    out.update({"verdict": verdict, "gates": {"G1": G1, "G2": G2, "G3": G3},
                "stats": {"biweekly": s2, "monthly": s1, "bench": sb},
                "curve": {"biweekly": c2, "bench": cb, "dates": d2},
                "cost": {"biweekly": cost2, "monthly": cost1, "swaps_biweekly": sw2}})
    with open(os.path.join(RD, "pool", "r1_rotation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/r1_rotation.json")


if __name__ == "__main__":
    main()
