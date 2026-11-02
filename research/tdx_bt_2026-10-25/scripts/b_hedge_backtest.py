# -*- coding: utf-8 -*-
"""阶段 B：「远高于均线」当减仓信号的回测。

严格按 docs/TDX_B_PREREG_2026-10-25.md 执行：
  · 信号：价格指数 000922 的 state = 月末收盘 / 已完结 12 个月末均值 − 1
         （用价格而非全收益：全收益因分红再投使 state 系统性上移 2.80pp，实测）
  · 决策：月末收盘；调仓：次月第一个交易日收盘
  · 规则：state > θ → 仓位 w；否则 100%
  · 风险资产收益：中证红利全收益 H00922（= 买 ETF 且分红再投）
  · 现金收益 0；每次调仓按变动金额收单边 0.051%
  · 主口径 θ=+5%, w=0%

内部校验：w=100% 情形必须精确复现 W11 的 +28.83% / 年化 +7.04% / 日频 MDD −14.53%。
"""
import os, sys, json
from statistics import mean

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from w11_pool_buyhold import load_series, IDX_TR, IDX_PX  # noqa: E402

START, END = "20230201", "20261023"
DEC_START, DEC_END = "2023-01", "2026-09"
COST = 0.00051
PRIMARY = (0.05, 0.0)
VARIANTS = [(0.05, 0.0), (0.05, 0.5), (0.08, 0.0), (0.08, 0.5),
            (0.10, 0.0), (0.12, 0.0)]


def month_key(d):
    return d[:4] + "-" + d[4:6]


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}-{m:02d}"


def next_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    m += 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y}-{m:02d}"


def month_range(a, b):
    out, c = [], a
    while c <= b:
        out.append(c)
        c = next_month(c)
    return out


def build_state(px):
    """{ym: state}，state 用已完结 12 个月。"""
    me = {}
    for d in sorted(px):
        me[month_key(d)] = (d, px[d])
    ms = sorted(me)
    st = {}
    for i, k in enumerate(ms):
        if i < 12:
            continue
        win = [me[x][1] for x in ms[i - 12:i]]
        if any(v <= 0 for v in win):
            continue
        st[k] = me[k][1] / (sum(win) / 12.0) - 1.0
    return st


def metrics(vals, dates, label):
    from datetime import date
    tot = vals[-1] / vals[0] - 1
    d0 = date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:]))
    d1 = date(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:]))
    yrs = (d1 - d0).days / 365.25
    ann = (vals[-1] / vals[0]) ** (1 / yrs) - 1 if yrs > 0 else None
    peak, mdd, mdd_at = vals[0], 0.0, None
    pk_d = dates[0]
    for d, v in zip(dates, vals):
        if v > peak:
            peak, pk_d = v, d
        dd = v / peak - 1
        if dd < mdd:
            mdd, mdd_at = dd, (pk_d, d)
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1] > 0]
    vol = (sum((r - mean(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * (252 ** 0.5)
    return {"label": label, "days": len(vals), "years": yrs, "total": tot, "annual": ann,
            "mdd": mdd, "mdd_at": mdd_at, "vol": vol,
            "calmar": (ann / abs(mdd)) if mdd < 0 else None}


def run(theta, w, idx_tr, st, cal, dates_by_month):
    """返回 (equity list, diagnostics)。"""
    nav, weights, cost_total = 1.0, [], 0.0
    cur_w = 1.0
    rebal, out_dates, out_nav = 0, [], []
    prev_d = None
    days_since = None
    for d in cal:
        mk = month_key(d)
        # 每月第一个交易日：按上月 state 调仓
        if days_since != mk:
            days_since = mk
            pm = prev_month(mk)
            if pm in st:
                tgt = w if st[pm] > theta else 1.0
                if abs(tgt - cur_w) > 1e-12:
                    cost_total += abs(tgt - cur_w) * COST
                    nav *= (1 - abs(tgt - cur_w) * COST)
                    rebal += 1
                cur_w = tgt
        if prev_d is not None:
            r = idx_tr[d] / idx_tr[prev_d] - 1.0
            nav *= (1 + cur_w * r)
        out_dates.append(d)
        out_nav.append(nav)
        weights.append(cur_w)
        prev_d = d
    pos = sum(1 for x in weights if x < 0.999) / len(weights)
    return out_nav, out_dates, {"rebal": rebal, "cost": cost_total,
                                "weight_out_share": pos,
                                "avg_weight": mean(weights)}


def main():
    px = load_series(IDX_PX)
    tr = load_series(IDX_TR)
    st = build_state(px)
    cal = sorted(d for d in tr if START <= d <= END)
    print(f"交易日 {len(cal)}｜{cal[0]} ~ {cal[-1]}")
    print(f"state 可用月份 {len([k for k in st if DEC_START <= k <= DEC_END])} 个")

    # --- 基准：始终 100% ---
    nav_bh, dts, d_bh = run(9.99, 1.0, tr, st, cal, None)
    m_bh = metrics(nav_bh, dts, "BH 100% 全收益指数")
    print("\n【内部校验】BH 应复现 W11：+28.83% / 年化 +7.04% / MDD −14.53%")
    print(f"  实测 {m_bh['total']*100:+.2f}% / 年化 {m_bh['annual']*100:+.2f}% / MDD {m_bh['mdd']*100:+.2f}%"
          f" / Calmar {m_bh['calmar']:.2f}")

    rows = []
    print("\n" + "=" * 84)
    print(f"{'θ':>6}{'w':>6}{'累计':>10}{'年化':>9}{'日频MDD':>10}{'波动':>9}{'Calmar':>8}"
          f"{'调仓':>6}{'空仓日占比':>10}{'平均仓位':>9}")
    for th, w in VARIANTS:
        nav, dts2, dg = run(th, w, tr, st, cal, None)
        m = metrics(nav, dts2, f"θ={th:.0%} w={w:.0%}")
        m.update(dg)
        rows.append(m)
        print(f"{th*100:>5.0f}%{w*100:>5.0f}%{m['total']*100:>9.2f}%{m['annual']*100:>8.2f}%"
              f"{m['mdd']*100:>9.2f}%{m['vol']*100:>8.2f}%{m['calmar']:>8.2f}"
              f"{m['rebal']:>6}{m['weight_out_share']*100:>9.1f}%{m['avg_weight']*100:>8.1f}%")

    # --- 判据 ---
    th, w = PRIMARY
    pm = next(r for r in rows if abs(r["label"].find("") ) >= 0 and r is rows[0])
    m = rows[0]
    G1 = m["calmar"] >= 0.576
    G2 = m["annual"] >= 0.0604
    G3 = m["mdd"] >= -0.1253
    if G1 and G2 and G3:
        verdict = "HEDGE_SIGNAL_USABLE"
    elif G1 and G3 and not G2:
        verdict = "HEDGE_COST_EXCEEDS_BENEFIT"
    elif not G1:
        verdict = "HEDGE_SIGNAL_NO_EDGE"
    else:
        verdict = "HEDGE_PARTIAL"
    if m["weight_out_share"] > 0.5:
        verdict += " / MOSTLY_OUT_OF_MARKET"

    print("\n【判定】主口径 θ=+5%, w=0%")
    print(f"  G1 Calmar ≥ 0.576 : {'过' if G1 else '不过'}（{m['calmar']:.2f}）")
    print(f"  G2 年化 ≥ 6.04%   : {'过' if G2 else '不过'}（{m['annual']*100:.2f}%）")
    print(f"  G3 MDD ≥ −12.53%  : {'过' if G3 else '不过'}（{m['mdd']*100:.2f}%）")
    print(f"  → {verdict}")

    # --- 诊断：state>θ 的月份占比与分组收益 ---
    print("\n【诊断】各阈值下 state 高位月份与分组收益：")
    print(f"{'θ':>6}{'高位月数':>9}{'占比':>8}{'最长连续':>9}{'高位月次月':>11}{'其余月次月':>11}{'差':>9}")
    for th in (0.05, 0.08, 0.10, 0.12):
        ms = [k for k in month_range(DEC_START, DEC_END) if k in st]
        hi = [k for k in ms if st[k] > th]
        # 最长连续
        best = cur = 0
        for k in month_range(ms[0], ms[-1]):
            if k in st and st[k] > th:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        g_hi, g_lo = [], []
        for k in ms:
            nk = next_month(k)
            d0 = [d for d in sorted(tr) if month_key(d) == k]
            d1 = [d for d in sorted(tr) if month_key(d) == nk]
            if not d0 or not d1:
                continue
            r = tr[d1[-1]] / tr[d0[-1]] - 1.0
            (g_hi if st[k] > th else g_lo).append(r)
        a = mean(g_hi) if g_hi else float("nan")
        b = mean(g_lo) if g_lo else float("nan")
        print(f"{th*100:>5.0f}%{len(hi):>9}{len(hi)/len(ms)*100:>7.1f}%{best:>9}"
              f"{a*100:>10.2f}%{b*100:>10.2f}%{(a-b)*100:>8.2f}pp")

    out = {"window": [cal[0], cal[-1]], "trading_days": len(cal), "cost": COST,
           "bh": m_bh, "variants": rows,
           "verdict": verdict,
           "gates": {"G1": G1, "G2": G2, "G3": G3},
           "primary": {"theta": th, "w": w}}
    with open(os.path.join(RD, "pool", "b_hedge.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/b_hedge.json")


if __name__ == "__main__":
    main()
