# -*- coding: utf-8 -*-
"""阶段 W10 v2：时点化「价/MA12 − 1 < −8%」规则回测。

严格按 docs/TDX_W10_PREREG_2026-10-25.md 执行。v2 相对 v1 的三处修正（均为实现错误，非判据变更）：
  1. **1 个月口径漏扣卖出成本**：v1 的 cohort 内部始终按 12 个月铺路，卖出成本挂在第 12 个月，
     导致 1 个月口径每月少扣 0.051pp。v2 按实际持有月数铺设。
  2. **基准起点未对齐**：v1 基准从 2023-02 月末起，规则从 2023-02 首个交易日起。
  3. **基准不是「买入不动」**：v1 是「每月等权再平衡」，有权重漂移差异。
     v2 改为固定股数的真买入持有；cohort 内部同样是买入持有，不再月度再平衡，符合「买一段就卖」。

口径：
  决策：月末收盘后用「已完结 12 个月末收盘」算 state，选 state < −8%
  建仓：次月第一个交易日收盘；持有 n 个月后在该月末卖出
  成本：单边 0.051%，买入一次 + 卖出一次（未走满持有期的不计卖出）
  主口径 n=12（12 个等额 sleeve，空 sleeve 计现金 0 收益）；对照 n=1
"""
import os, sys, csv, json
from statistics import mean, median

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402
import glob

ASOF = "20261023"
ENTRY_START = "2023-02"
DEC_START, DEC_END = "2023-01", "2026-09"
THRESH = -0.08
COST = 0.00051
N_SLEEVE = 12


def series_from(d):
    out = {}
    for r in d["Rows"]:
        try:
            out[str(r["Data"])] = float(r["Close"])
        except Exception:
            continue
    return out


def load_adj_panels():
    best = {}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period", "")).split(".")[0] != "4":
            continue
        code = str(d.get("Code", "")).strip()
        if not code:
            continue
        ser = series_from(d)
        if len(ser) < 13:
            continue
        ai = d.get("AttachInfo") or {}
        now = ai.get("Now") or ai.get("Close")
        cls = "adj"
        try:
            last = ser[max(ser)]
            if max(ser) == ASOF and now and float(now) > 0:
                if abs(last / float(now) - 1.0) < 0.02:
                    cls = "raw"
        except Exception:
            pass
        slot = best.setdefault(code, {})
        for k, v in ser.items():
            cur = slot.get(k)
            if cur is None or (cls == "adj" and cur[1] == "raw") or (cls == cur[1] and v > cur[0]):
                slot[k] = (v, cls)
    return {c: {k: v[0] for k, v in s.items()} for c, s in best.items()}


def month_end_map(px):
    m = {}
    for d in sorted(px):
        m[d[:4] + "-" + d[4:6]] = (d, px[d])
    return m


def first_in_month(px, ym):
    for d in sorted(px):
        if d[:4] + "-" + d[4:6] == ym:
            return d, px[d]
    return None


def last_in_month(px, ym):
    cand = [d for d in px if d[:4] + "-" + d[4:6] == ym]
    if not cand:
        return None
    d = max(cand)
    return d, px[d]


def next_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    m += 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y}-{m:02d}"


def month_range(a, b):
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur = next_month(cur)
    return out


def main():
    comps = list(csv.DictReader(open(os.path.join(RD, "pool", "000922_components.csv"),
                                     encoding="utf-8-sig")))
    codes = [c["code"] for c in comps]
    wt = {c["code"]: float(c["weight"]) for c in comps}

    panels = load_adj_panels()
    me = {c: month_end_map(panels[c]) for c in codes if c in panels}
    print(f"载入 {len(me)} 只后复权日线；基准起点 = {ENTRY_START} 首个交易日（与规则对齐）")

    all_months = month_range("2022-01", "2026-10")
    states = {}
    for ym in all_months:
        idx = all_months.index(ym)
        if idx < 12:
            continue
        win_m = all_months[idx - 12:idx]
        cur = {}
        for c in me:
            if ym not in me[c]:
                continue
            try:
                vals = [me[c][m][1] for m in win_m]
            except KeyError:
                continue
            if any(v <= 0 for v in vals):
                continue
            cur[c] = me[c][ym][1] / (sum(vals) / 12.0) - 1.0
        states[ym] = cur

    dec_months = [m for m in month_range(DEC_START, DEC_END) if m in states]
    cohorts = []
    for ym in dec_months:
        sel = sorted([c for c, v in states[ym].items() if v < THRESH], key=lambda c: states[ym][c])
        hold = [next_month(ym)]
        for _ in range(11):
            hold.append(next_month(hold[-1]))
        cohorts.append({"dec": ym, "sel": sel, "hold": hold})
    print(f"决策月 {len(dec_months)} 个：{dec_months[0]} ~ {dec_months[-1]}")

    def cohort_path(co, nmonths):
        """买入持有：首日等额买入，之后固定股数，中途不再平衡。"""
        sel = co["sel"]
        if not sel:
            return {}
        entry = {}
        for c in sel:
            fi = first_in_month(panels[c], co["hold"][0])
            if fi and fi[1] > 0:
                entry[c] = fi[1]
        if not entry:
            return {}
        base = 1.0 / len(entry)
        prev_val, out = 1.0, {}
        months = co["hold"][:nmonths]
        for j, hm in enumerate(months):
            tot, cnt = 0.0, 0
            for c, p0 in entry.items():
                x = last_in_month(panels[c], hm) if j == 0 else me[c].get(hm)
                if x and p0 > 0:
                    tot += base * (x[1] / p0)
                    cnt += 1
            if cnt == 0:
                continue
            val = tot + base * (len(entry) - cnt)      # 停牌按不变价 carry
            r = val / prev_val - 1.0
            if j == 0:
                r -= COST
            if j == len(months) - 1 and j + 1 == nmonths:
                r -= COST
            out[hm] = r
            prev_val = val
        return out

    cp12 = [cohort_path(co, N_SLEEVE) for co in cohorts]
    cp1 = [cohort_path(co, 1) for co in cohorts]

    port_months = month_range(ENTRY_START, "2026-10")
    ret12, n_active = {}, {}
    for t in port_months:
        s, n = 0.0, 0
        for cp in cp12:
            if t in cp:
                s += cp[t]
                n += 1
        ret12[t] = s / N_SLEEVE
        n_active[t] = n
    ret1 = {}
    for co, cp in zip(cohorts, cp1):
        hm = co["hold"][0]
        if hm in cp:
            ret1[hm] = cp[hm]

    def buy_hold(mode):
        base_ym = ENTRY_START
        sh, p0 = {}, {}
        for c in codes:
            if c not in panels:
                continue
            fi = first_in_month(panels[c], base_ym)
            if not fi or fi[1] <= 0:
                continue
            p0[c] = fi[1]
            sh[c] = (wt[c] if mode == "w" else 1.0) / fi[1]
        tot_w = sum((wt[c] if mode == "w" else 1.0) for c in sh)
        out, prev = {}, 1.0
        for ym in month_range(base_ym, "2026-10"):
            v = 0.0
            for c in sh:
                x = me[c].get(ym)
                v += sh[c] * (x[1] if x else p0[c])
            v = v / tot_w
            r = v / prev - 1.0
            if not out:
                r -= COST
            out[ym] = r
            prev = v
        return out

    b_ew, b_w = buy_hold("ew"), buy_hold("w")

    def stats(ret, label):
        ms = sorted(ret)
        eq, peak, mdd = 1.0, 1.0, 0.0
        for m in ms:
            eq *= (1 + ret[m])
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
        n = len(ms)
        return {"label": label, "n_months": n, "total": eq - 1,
                "annual": (eq ** (12.0 / n) - 1) if n else None,
                "mdd": mdd, "final": eq,
                "first": ms[0] if ms else None, "last": ms[-1] if ms else None}

    s12, s1 = stats(ret12, "rule-12M"), stats(ret1, "rule-1M")
    sew, sw = stats(b_ew, "BH-EW-买入不动"), stats(b_w, "BH-指数权重-买入不动")

    print("\n" + "=" * 80)
    print(f"{'策略':<24}{'月数':>6}{'累计':>11}{'年化':>10}{'最大回撤':>11}{'期末净值':>10}")
    for s in (s12, s1, sew, sw):
        print(f"{s['label']:<24}{s['n_months']:>6}{s['total']*100:>10.2f}%"
              f"{s['annual']*100:>9.2f}%{s['mdd']*100:>10.2f}%{s['final']:>10.3f}")

    n_hold = sum(1 for co in cohorts if co["sel"])
    print(f"\n有持仓决策月 {n_hold}/{len(cohorts)}；持仓只数 中位 "
          f"{median([len(c['sel']) for c in cohorts])} / 最少 {min(len(c['sel']) for c in cohorts)}"
          f" / 最多 {max(len(c['sel']) for c in cohorts)}")
    print(f"12M 口径平均同时持有 cohort 数 {mean([v for v in n_active.values()]):.2f}（满仓=12）")

    diffs, g_ret, a_ret = [], [], []
    for ym in dec_months:
        nxt = next_month(ym)
        sel = set(c for c, v in states[ym].items() if v < THRESH)
        rs_all, rs_sel = [], []
        for c in states[ym]:
            if ym in me[c] and nxt in me[c] and me[c][ym][1] > 0:
                rr = me[c][nxt][1] / me[c][ym][1] - 1.0
                rs_all.append(rr)
                if c in sel:
                    rs_sel.append(rr)
        if rs_sel and rs_all:
            diffs.append(mean(rs_sel) - mean(rs_all))
            g_ret.append(mean(rs_sel))
            a_ret.append(mean(rs_all))

    mu = mean(diffs)
    sd = (sum((x - mu) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5
    t = mu / (sd / len(diffs) ** 0.5) if sd > 0 else 0.0
    win = sum(1 for x in diffs if x > 0) / len(diffs)
    mde = 2.8016 * sd / len(diffs) ** 0.5
    print(f"\n机制检验（次月收益，state<−8% 组 − 全池）：")
    print(f"  月均差 {mu*100:+.3f}pp｜t(iid) {t:+.2f}｜n={len(diffs)}｜胜率 {win*100:.1f}%｜MDE {mde*100:.3f}pp")
    print(f"  组均值 {mean(g_ret)*100:+.3f}%/月 vs 全池 {mean(a_ret)*100:+.3f}%/月")

    out = {"asof": ASOF, "thresh": THRESH, "cost": COST, "n_sleeve": N_SLEEVE,
           "cohorts": [{"dec": c["dec"], "n": len(c["sel"]), "sel": c["sel"]} for c in cohorts],
           "ret12": ret12, "ret1": ret1, "bh_ew": b_ew, "bh_w": b_w,
           "stats": {"r12": s12, "r1": s1, "ew": sew, "w": sw},
           "mech": {"mu": mu, "t": t, "win": win, "n": len(diffs), "mde": mde,
                    "g": mean(g_ret), "a": mean(a_ret), "diffs": diffs}}
    with open(os.path.join(RD, "pool", "w10_rule.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/w10_rule.json")


if __name__ == "__main__":
    main()
