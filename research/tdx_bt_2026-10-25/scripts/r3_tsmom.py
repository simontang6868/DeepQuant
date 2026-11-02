# -*- coding: utf-8 -*-
"""阶段 R3：时序动量（绝对趋势判断，不做横截面排序）。

按 docs/TDX_R3_PREREG_2026-10-25.md 执行：
  第一层 信号有效性（T1~T5）→ 不过则直接结题，不跑组合
  第二层 组合表现（仅当第一层全过）

池（与 R2 逐字节相同，7 只）：
  510300 沪深300 / 510500 中证500 / 159915 创业板 / 510880 红利
  513100 纳指 / 518880 黄金 / 511010 国债

信号：对每只独立判断 mom20 > 0 → 持有，等权；全负 → 空仓（收益 0，不许改持国债）
"""
import os, sys, json, glob, hashlib
from statistics import mean

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

# ---- 参数硬编码（预登记 §7）----
CODES = ["510300", "510500", "159915", "510880", "513100", "518880", "511010"]
NAMES = {"510300": "沪深300", "510500": "中证500", "159915": "创业板", "510880": "红利",
         "513100": "纳指", "518880": "黄金", "511010": "国债"}
MOM, STEP = 20, 10
CAP = 100000.0
COMM_RATE = 0.00025      # 万2.5
COMM_MIN = 5.0           # 5 元最低

# ---- 判据阈值（预登记 §4）----
T_T1 = 2.39              # Bonferroni：同问题第 3 次检验
WIN_T2 = 0.55
T_T3 = 1.5
T_T4 = 1.5
CASH_MAX = 0.40


def check_prereg():
    p = os.path.join(RD, "pool", "r3_prereg.sha256")
    if not os.path.exists(p):
        print("!! 未找到预登记哈希文件，终止")
        sys.exit(1)
    want = open(p, encoding="utf-8").read().split()[0]
    f = os.path.join(ROOT, "docs", "TDX_R3_PREREG_2026-10-25.md")
    got = hashlib.sha256(open(f, "rb").read()).hexdigest()
    print(f"预登记校验: {want[:16]}… vs {got[:16]}…  {'一致' if want == got else '不一致!'}")
    if want != got:
        print("!! 预登记已被改动，终止")
        sys.exit(1)


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


def signal_layer(panel, cal, codes):
    """时序动量：返回 (edges, n_hold_each, cash_cnt, picks_rel)。

    picks_rel[c] = 该标的被持有期内「其收益 − 全池等权」的均值（用于 LOO）。
    """
    edges, holds, cash = [], [], 0
    held_rel = {c: [] for c in codes}
    for i in range(MOM, len(cal) - STEP, STEP):
        d0, d1 = cal[i], cal[i + STEP]
        mom, ok = {}, True
        for c in codes:
            p0, p1 = panel[c].get(cal[i - MOM]), panel[c].get(d0)
            if not p0 or not p1 or p0 <= 0:
                ok = False
                break
            mom[c] = p1 / p0 - 1
        if not ok:
            continue
        fwd = {}
        for c in codes:
            a, b = panel[c].get(d0), panel[c].get(d1)
            if a and b and a > 0:
                fwd[c] = b / a - 1
        if len(fwd) < len(codes):
            continue
        full = mean(fwd.values())
        hold = [c for c in codes if mom[c] > 0]
        holds.append(len(hold))
        if hold:
            edges.append(mean(fwd[c] for c in hold) - full)
            for c in hold:
                held_rel[c].append(fwd[c] - full)
        else:
            # 空仓：edge = 0 − 全池等权（必须计入，不得剔除）
            edges.append(-full)
            cash += 1
    rel = {c: (mean(held_rel[c]) if held_rel[c] else 0.0) for c in codes}
    return edges, holds, cash, rel


def main():
    check_prereg()
    panel = load_panel()
    cal = sorted(set.intersection(*[set(panel[c]) for c in CODES]))
    print(f"共同交易日 {len(cal)}：{cal[0]} ~ {cal[-1]}")
    print(f"标的 {len(CODES)} 只：{' '.join(NAMES[c] for c in CODES)}")

    edges, holds, cash, rel = signal_layer(panel, cal, CODES)
    n = len(edges)
    mu, t, se = tstat(edges)
    mde = 2.8016 * se
    win = sum(1 for x in edges if x > 0) / n
    cash_share = cash / n
    avg_hold = mean(holds)

    print("\n" + "=" * 78)
    print("第一层：时序动量 → 持仓组 下一期 10 日收益 − 全池等权")
    print("=" * 78)
    print(f"  期数 {n}｜edge 均值 {mu*100:+.3f}pp｜t {t:+.2f}｜胜率 {win*100:.1f}%｜MDE {mde*100:.3f}pp")
    print(f"  平均持仓 {avg_hold:.2f} 只｜空仓 {cash} 期（{cash_share*100:.1f}%）")

    order = sorted(range(n), key=lambda k: -edges[k])
    drop3 = [edges[k] for k in range(n) if k not in order[:3]]
    mu3, t3, _ = tstat(drop3)
    print(f"  剔掉 edge 最大 3 期后：均值 {mu3*100:+.3f}pp｜t {t3:+.2f}（n={len(drop3)}）")

    T1 = (mu > 0 and abs(t) >= T_T1)
    T2 = (win >= WIN_T2)
    T3 = (mu3 > 0 and abs(t3) >= T_T3)
    T5 = (cash_share <= CASH_MAX)

    print(f"\n  T1 方向与量级（>0 且 |t|≥{T_T1}）    : {'过' if T1 else '不过'}")
    print(f"  T2 胜率（≥{WIN_T2*100:.0f}%）               : {'过' if T2 else '不过'}（{win*100:.1f}%）")
    print(f"  T3 剔除极端 3 期后仍成立         : {'过' if T3 else '不过'}")
    print(f"  T5 空仓率 ≤{CASH_MAX*100:.0f}%              : {'过' if T5 else '不过'}（{cash_share*100:.1f}%）")

    out_c = max(rel, key=lambda c: rel[c])
    sub = [c for c in CODES if c != out_c]
    e_sub, h_sub, cash_sub, rel_sub = signal_layer(panel, cal, sub)
    mu_b, t_b, se_b = tstat(e_sub)
    T4 = (mu_b > 0 and abs(t_b) >= T_T4)
    print(f"  T4 剔除贡献最大者（{NAMES[out_c]}，{rel[out_c]*100:+.3f}pp/期）后重跑：")
    print(f"        edge {mu_b*100:+.3f}pp｜t {t_b:+.2f}（n={len(e_sub)}）→ {'过' if T4 else '不过'}")

    print("\n  各标的被持有期数 / 持有期内平均相对收益：")
    for c in sorted(CODES, key=lambda x: -rel[x]):
        print(f"    {NAMES[c]:<8} {rel[c]*100:+.3f}pp/期")

    signal_ok = T1 and T2 and T3 and T4 and T5

    out = {
        "prereg_sha256": open(os.path.join(RD, "pool", "r3_prereg.sha256"), encoding="utf-8").read().split()[0],
        "window": [cal[0], cal[-1]], "n_periods": n, "codes": CODES,
        "layer1": {"edge_mean": mu, "t": t, "win": win, "mde": mde, "se": se,
                   "mean_drop3": mu3, "t_drop3": t3,
                   "avg_hold": avg_hold, "cash_cnt": cash, "cash_share": cash_share,
                   "T1": T1, "T2": T2, "T3": T3, "T4": T4, "T5": T5,
                   "rel_by_etf": rel, "loo_drop": out_c, "loo_edge_mean": mu_b, "loo_t": t_b,
                   "passed": signal_ok},
        "edges": edges, "holds": holds,
    }

    if not signal_ok:
        print("\n" + "=" * 78)
        fail = [k for k in ("T1", "T2", "T3", "T4", "T5") if not out["layer1"][k]]
        print(f"判定：TSMOM_NO_EDGE —— 未通过 {', '.join(fail)}")
        print("按预登记 §4.6 / §7.6 直接结题，不跑组合回测。主动轮动方向关闭。")
        print("=" * 78)
        out["verdict"] = "TSMOM_NO_EDGE"
        with open(os.path.join(RD, "pool", "r3_tsmom.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("saved pool/r3_tsmom.json")
        return

    print("\n第一层全部通过 → 进入第二层组合回测")

    # ---------- 第二层：组合表现（成本按预登记 §3.3 硬编码公式）----------
    def commission(amount):
        return max(COMM_MIN, amount * COMM_RATE)

    def backtest(step):
        j = MOM
        mom0 = {c: panel[c][cal[j]] / panel[c][cal[j - MOM]] - 1 for c in CODES}
        cur = {c for c in CODES if mom0[c] > 0}
        nav, cost_sum, n_trades = 1.0, 0.0, 0
        curve, dates, cash_days = [], [], 0
        for k in range(j, len(cal) - 1):
            d, nd = cal[k], cal[k + 1]
            if k > j and (k - j) % step == 0:
                mom = {c: panel[c][d] / panel[c][cal[k - MOM]] - 1 for c in CODES}
                tgt = {c for c in CODES if mom[c] > 0}
                sold, bought = len(cur - tgt), len(tgt - cur)
                if sold or bought:
                    n_hold = len(tgt) if tgt else len(cur)
                    if n_hold == 0:
                        n_hold = 1
                    per = CAP / n_hold
                    cst = (sold + bought) * commission(per) / CAP
                    nav *= (1 - cst)
                    cost_sum += cst
                    n_trades += sold + bought
                cur = tgt
            if not cur:
                cash_days += 1
                r = 0.0
            else:
                w = 1.0 / len(cur)
                r = 0.0
                for c in cur:
                    a, b = panel[c].get(d), panel[c].get(nd)
                    if a and b and a > 0:
                        r += w * (b / a - 1)
            nav *= (1 + r)
            curve.append(nav)
            dates.append(nd)
        return curve, dates, cost_sum, n_trades, cash_days

    def bench():
        w = 1.0 / len(CODES)
        nav, curve, dates = 1.0, [], []
        for k in range(MOM, len(cal) - 1):
            d, nd = cal[k], cal[k + 1]
            r = 0.0
            for c in CODES:
                a, b = panel[c].get(d), panel[c].get(nd)
                if a and b and a > 0:
                    r += w * (b / a - 1)
            nav *= (1 + r)
            curve.append(nav)
            dates.append(nd)
        return curve, dates

    def stats(curve, dates, label):
        from datetime import date
        rr = [curve[k] / curve[k - 1] - 1 for k in range(1, len(curve))]
        d0 = date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:]))
        d1 = date(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:]))
        yrs = (d1 - d0).days / 365.25
        tot = curve[-1] / curve[0] - 1
        ann = curve[-1] ** (1 / yrs) - 1
        peak, mdd, mdd_at, pk = curve[0], 0.0, None, dates[0]
        for dd_, v in zip(dates, curve):
            if v > peak:
                peak, pk = v, dd_
            dd = v / peak - 1
            if dd < mdd:
                mdd, mdd_at = dd, (pk, dd_)
        vol = (sum((x - mean(rr)) ** 2 for x in rr) / (len(rr) - 1)) ** 0.5 * 252 ** 0.5
        return {"label": label, "years": yrs, "total": tot, "annual": ann, "mdd": mdd,
                "mdd_at": mdd_at, "vol": vol,
                "calmar": ann / abs(mdd) if mdd < 0 else None}

    c2, d2, cost2, tr2, cd2 = backtest(STEP)
    c1, d1_, cost1, tr1, cd1 = backtest(21)
    cb, db = bench()
    s2 = stats(c2, d2, "时序动量(双周)")
    s1 = stats(c1, d1_, "时序动量(月频)")
    sb = stats(cb, db, "等权持有7只(基准)")

    print("\n" + "=" * 78)
    print("第二层：组合表现（扣费后）")
    print("=" * 78)
    print(f"{'策略':<20}{'年数':>6}{'累计':>11}{'年化':>9}{'日频MDD':>10}{'波动':>9}{'Calmar':>8}")
    for s in (s2, s1, sb):
        print(f"{s['label']:<20}{s['years']:>6.2f}{s['total']*100:>10.2f}%{s['annual']*100:>8.2f}%"
              f"{s['mdd']*100:>9.2f}%{s['vol']*100:>8.2f}%{s['calmar']:>8.2f}")
    print(f"\n  双周：成交 {tr2} 笔，累计成本 {cost2*100:.2f}%，空仓 {cd2}/{len(c2)} 日")
    print(f"  月频：成交 {tr1} 笔，累计成本 {cost1*100:.2f}%，空仓 {cd1}/{len(c1)} 日")

    G1 = s2["annual"] > sb["annual"]
    G2 = s2["mdd"] >= -0.20
    G3 = s2["annual"] > 0
    if G1 and G2 and G3:
        verdict = "TSMOM_USABLE"
    elif not G1:
        verdict = "TSMOM_LOSES_TO_PASSIVE"
    elif not G2:
        verdict = "TSMOM_DRAWDOWN_BREACH"
    else:
        verdict = "TSMOM_LOSES_MONEY"
    print(f"\n【判定】G1 跑赢基准({'过' if G1 else '不过'}) G2 回撤≥−20%({'过' if G2 else '不过'}) "
          f"G3 年化>0({'过' if G3 else '不过'}) → {verdict}")

    out.update({"verdict": verdict, "gates": {"G1": G1, "G2": G2, "G3": G3},
                "stats": {"biweekly": s2, "monthly": s1, "bench": sb},
                "curve": {"biweekly": c2, "bench": cb, "dates": d2},
                "cost": {"biweekly": cost2, "monthly": cost1, "trades": tr2,
                         "cash_days": cd2}})
    with open(os.path.join(RD, "pool", "r3_tsmom.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/r3_tsmom.json")


if __name__ == "__main__":
    main()
