# -*- coding: utf-8 -*-
"""阶段 R2：修池后重测「跨风格 ETF 双周动量轮动」。

按 docs/TDX_R2_PREREG_2026-10-25.md 执行，顺序不可颠倒：
  第一层 信号有效性（S1/S2/S3/S4）→ 不过则直接结题，不跑组合
  第二层 组合表现（仅当第一层全过）

标的池（7 只，按类别完整性选，见预登记 §1）：
  510300 沪深300 / 510500 中证500 / 159915 创业板 / 510880 红利
  513100 纳指 / 518880 黄金 / 511010 国债

相比 R1：去掉 510050 上证50（与 510300 相关 0.93），加入 518880 黄金（与其余 6 只相关 0.04~0.13）。

成本（10 万资金，万2.5 + 5 元最低，ETF 无印花税）：换掉 1 只 = 25 元 = 总资金 0.025%
"""
import os, sys, json, glob, hashlib
from statistics import mean

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

# ---- 参数硬编码，不得调参择优（预登记 §7）----
CODES = ["510300", "510500", "159915", "510880", "513100", "518880", "511010"]
NAMES = {"510300": "沪深300", "510500": "中证500", "159915": "创业板", "510880": "红利",
         "513100": "纳指", "518880": "黄金", "511010": "国债"}
MOM, STEP, TOPK = 20, 10, 2
COST_PER_SWAP = 0.00025
CAP = 100000.0

# ---- 判据阈值（预登记 §4）----
T_S1 = 2.24          # Bonferroni：同窗口第 2 次检验
WIN_S2 = 0.55
T_S3 = 1.5
SEL_SHARE_MAX = 0.60
T_S4B = 1.5


def check_prereg():
    p = os.path.join(RD, "pool", "r2_prereg.sha256")
    if not os.path.exists(p):
        print("!! 未找到预登记哈希文件，终止")
        sys.exit(1)
    want = open(p, encoding="utf-8").read().split()[0]
    f = os.path.join(ROOT, "docs", "TDX_R2_PREREG_2026-10-25.md")
    got = hashlib.sha256(open(f, "rb").read()).hexdigest()
    print(f"预登记校验: {want[:16]}… vs {got[:16]}…  {'一致' if want == got else '不一致!'}")
    if want != got:
        print("!! 预登记已被改动，终止（参数不得事后调整）")
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
    """返回 (edges, sel_cnt, rel_by_etf, n_periods)。codes 可变，供 leave-one-out 复用。"""
    edges = []
    sel_cnt = {c: 0 for c in codes}
    per_etf = {c: [] for c in codes}
    i = MOM
    while i + STEP < len(cal):
        d0, d1 = cal[i], cal[i + STEP]
        mom, ok = {}, True
        for c in codes:
            p0, p1 = panel[c].get(cal[i - MOM]), panel[c].get(d0)
            if not p0 or not p1 or p0 <= 0:
                ok = False
                break
            mom[c] = p1 / p0 - 1
        if not ok:
            i += STEP
            continue
        fwd = {}
        for c in codes:
            a, b = panel[c].get(d0), panel[c].get(d1)
            if a and b and a > 0:
                fwd[c] = b / a - 1
        if len(fwd) < len(codes):
            i += STEP
            continue
        top = sorted(mom, key=lambda c: -mom[c])[:TOPK]
        full = mean(fwd.values())
        edges.append(mean(fwd[c] for c in top) - full)
        for c in codes:
            per_etf[c].append(fwd[c] - full)
        for c in top:
            sel_cnt[c] += 1
        i += STEP
    rel = {c: (mean(per_etf[c]) if per_etf[c] else 0.0) for c in codes}
    return edges, sel_cnt, rel


def main():
    check_prereg()
    panel = load_panel()
    cal = sorted(set.intersection(*[set(panel[c]) for c in CODES]))
    print(f"共同交易日 {len(cal)}：{cal[0]} ~ {cal[-1]}")
    print(f"标的 {len(CODES)} 只：{' '.join(NAMES[c] for c in CODES)}")

    # ---------- 第一层：信号有效性 ----------
    edges, sel_cnt, rel = signal_layer(panel, cal, CODES)
    mu, t, se = tstat(edges)
    mde = 2.8016 * se
    win = sum(1 for x in edges if x > 0) / len(edges)
    n = len(edges)

    print("\n" + "=" * 78)
    print("第一层：20 日动量 → 未来 10 日相对收益（动量前 2 只 − 全池等权）")
    print("=" * 78)
    print(f"  期数 {n}｜edge 均值 {mu*100:+.3f}pp｜t {t:+.2f}｜胜率 {win*100:.1f}%｜MDE {mde*100:.3f}pp")

    order = sorted(range(n), key=lambda k: -edges[k])
    drop3 = [edges[k] for k in range(n) if k not in order[:3]]
    mu3, t3, _ = tstat(drop3)
    print(f"  剔掉 edge 最大 3 期后：均值 {mu3*100:+.3f}pp｜t {t3:+.2f}（n={len(drop3)}）")

    S1 = (mu > 0 and abs(t) >= T_S1)
    S2 = (win >= WIN_S2)
    S3 = (mu3 > 0 and abs(t3) >= T_S3)

    print(f"\n  S1 方向与量级（>0 且 |t|≥{T_S1}）     : {'过' if S1 else '不过'}")
    print(f"  S2 胜率（≥{WIN_S2*100:.0f}%）              : {'过' if S2 else '不过'}（{win*100:.1f}%）")
    print(f"  S3 剔除极端 3 期后仍成立        : {'过' if S3 else '不过'}")

    print("\n  各标的被选中次数 / 平均相对收益：")
    for c in sorted(CODES, key=lambda x: -sel_cnt[x]):
        print(f"    {NAMES[c]:<8} 选中 {sel_cnt[c]:>3}/{n} 次（{sel_cnt[c]/n*100:>5.1f}%）"
              f"   平均相对收益 {rel[c]*100:+.3f}pp/期")

    # S4-a 分散性
    top_c, top_share = max(sel_cnt.items(), key=lambda kv: kv[1])
    S4a = (top_share / n) <= SEL_SHARE_MAX
    print(f"\n  S4-a 单只被选中 ≤{SEL_SHARE_MAX*100:.0f}% : {'过' if S4a else '不过'}"
          f"（最高 {NAMES[top_c]} {top_share/n*100:.1f}%）")

    # S4-b leave-one-out：剔除平均相对收益最高的那只
    out_c = max(rel, key=lambda c: rel[c])
    sub = [c for c in CODES if c != out_c]
    e_sub, sc_sub, rel_sub = signal_layer(panel, cal, sub)
    mu_b, t_b, se_b = tstat(e_sub)
    S4b = (mu_b > 0 and abs(t_b) >= T_S4B)
    print(f"  S4-b 剔除贡献最大者（{NAMES[out_c]}，{rel[out_c]*100:+.3f}pp/期）后重跑：")
    print(f"        edge {mu_b*100:+.3f}pp｜t {t_b:+.2f}（n={len(e_sub)}）→ {'过' if S4b else '不过'}")

    signal_ok = S1 and S2 and S3 and S4a and S4b

    out = {
        "prereg_sha256": open(os.path.join(RD, "pool", "r2_prereg.sha256"), encoding="utf-8").read().split()[0],
        "window": [cal[0], cal[-1]], "n_periods": n, "codes": CODES,
        "layer1": {"edge_mean": mu, "t": t, "win": win, "mde": mde, "se": se,
                   "mean_drop3": mu3, "t_drop3": t3,
                   "S1": S1, "S2": S2, "S3": S3, "S4a": S4a, "S4b": S4b,
                   "sel_cnt": sel_cnt, "rel_by_etf": rel,
                   "loo_drop": out_c, "loo_edge_mean": mu_b, "loo_t": t_b,
                   "passed": signal_ok},
        "edges": edges,
    }

    if not signal_ok:
        print("\n" + "=" * 78)
        fail = [k for k in ("S1", "S2", "S3", "S4a", "S4b")
                if not out["layer1"][k]]
        print(f"判定：MOMENTUM_NO_EDGE —— 未通过 {', '.join(fail)}")
        print("按预登记 §4.6 直接结题，不跑组合回测。")
        print("=" * 78)
        out["verdict"] = "MOMENTUM_NO_EDGE"
        with open(os.path.join(RD, "pool", "r2_rotation.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("saved pool/r2_rotation.json")
        return

    print("\n第一层全部通过 → 进入第二层组合回测")

    # ---------- 第二层：组合表现 ----------
    def backtest(step):
        j = MOM
        mom0 = {c: panel[c][cal[j]] / panel[c][cal[j - MOM]] - 1 for c in CODES}
        cur = {c: 1.0 / TOPK for c in sorted(mom0, key=lambda x: -mom0[x])[:TOPK]}
        nav, swaps, cost_sum = 1.0, 0, 0.0
        curve, dates = [], []
        for k in range(j, len(cal) - 1):
            d, nd = cal[k], cal[k + 1]
            if k > j and (k - j) % step == 0:
                mom = {c: panel[c][d] / panel[c][cal[k - MOM]] - 1 for c in CODES}
                tgt = set(sorted(mom, key=lambda x: -mom[x])[:TOPK])
                ns = len(tgt.symmetric_difference(set(cur))) // 2
                if ns:
                    cst = ns * COST_PER_SWAP
                    nav *= (1 - cst)
                    cost_sum += cst
                    swaps += ns
                cur = {c: 1.0 / TOPK for c in tgt}
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
        w = 1.0 / len(CODES)
        nav = 1.0
        curve, dates = [], []
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
        for d, v in zip(dates, curve):
            if v > peak:
                peak, pk = v, d
            dd = v / peak - 1
            if dd < mdd:
                mdd, mdd_at = dd, (pk, d)
        vol = (sum((x - mean(rr)) ** 2 for x in rr) / (len(rr) - 1)) ** 0.5 * 252 ** 0.5
        return {"label": label, "years": yrs, "total": tot, "annual": ann, "mdd": mdd,
                "mdd_at": mdd_at, "vol": vol,
                "calmar": ann / abs(mdd) if mdd < 0 else None}

    c2, d2, sw2, cost2 = backtest(STEP)
    c1, d1_, sw1, cost1 = backtest(21)
    cb, db = bench()
    s2 = stats(c2, d2, "双周轮动(20日动量前2)")
    s1 = stats(c1, d1_, "月频轮动(对照)")
    sb = stats(cb, db, "等权持有7只(基准)")

    print("\n" + "=" * 78)
    print("第二层：组合表现（扣费后）")
    print("=" * 78)
    print(f"{'策略':<22}{'年数':>6}{'累计':>11}{'年化':>9}{'日频MDD':>10}{'波动':>9}{'Calmar':>8}")
    for s in (s2, s1, sb):
        print(f"{s['label']:<22}{s['years']:>6.2f}{s['total']*100:>10.2f}%{s['annual']*100:>8.2f}%"
              f"{s['mdd']*100:>9.2f}%{s['vol']*100:>8.2f}%{s['calmar']:>8.2f}")
    print(f"\n  双周口径：换手 {sw2} 次，累计成本 {cost2*100:.2f}%")
    print(f"  月频口径：换手 {sw1} 次，累计成本 {cost1*100:.2f}%")

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
                "cost": {"biweekly": cost2, "monthly": cost1, "swaps": sw2}})
    with open(os.path.join(RD, "pool", "r2_rotation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/r2_rotation.json")


if __name__ == "__main__":
    main()
