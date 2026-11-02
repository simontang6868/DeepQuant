#!/usr/bin/env python
"""2023-01 起的短窗口重算：贴合作者的实际持有场景（买一段就走，非永久持有）。

【窗口口径】
  建仓 2022-12-30（2022 年最后交易日），评估期 **2023-01 → 2026-10**，约 46 个月 / 3.83 年。
  数据预热从 2021-12 起（给低波/动量类信号 13 个月历史），但收益与指标只算 2023-01 之后。

【为什么不再用 19 年】
  作者明确：持有一段就卖，不是永久持有。19 年窗口里 2008/2015 那两次崩塌与"买 1~3 年"的场景无关，
  用它算出来的最大回撤和年化并不能代表作者会经历的东西。
  ⚠️ 但短窗口的代价必须写明：只剩约 46 个月，且 2023-2026 是红利风格顺风期，结论不可外推。

【本脚本输出】
  P1 池子表现（19 只 / 11 行业）
  P2 逐只表现与排名
  P3 策略对比（买入持有 / 月度再平衡 / 低波 / 动量 / 趋势 / MA 择时）
  P4 ⭐ 持有期分布：任意时点买入、持有 12/24/36 个月后卖出的收益分布
  P5 判定（四条硬指标）
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import bt_diverse_diag as D
import panel_fix as PF

WARM_START = "20211201"      # 预热起点（信号用）
EVAL_START = "20221230"      # 建仓时点（2022 年最后交易日）
TARGET_MDD = 0.20
BUY_COST, SELL_COST = 0.00051, 0.00101


def perf(r: list[float]) -> dict:
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in r:
        nav *= (1 + x)
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    yrs = len(r) / 12
    cagr = nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    return {"total": nav - 1, "cagr": cagr, "mdd": mdd,
            "vol": statistics.pstdev(r) * (12 ** 0.5) if len(r) > 1 else float("nan"),
            "calmar": cagr / abs(mdd) if mdd else float("nan"), "months": len(r)}


def sma(v, k, i):
    return statistics.fmean(v[i + 1 - k:i + 1]) if i + 1 >= k else None


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs if c != "601098"]
    series = D.load_monthly(set(pool))
    codes = [c for c in pool if c in series]
    dates, panel = PF.build_panel({c: series[c] for c in codes}, start=WARM_START)
    i0 = next(i for i, d in enumerate(dates) if d >= EVAL_START)
    print(f"池 {len(codes)} 只  预热 {dates[0]}  建仓 {dates[i0]}  末 {dates[-1]}  "
          f"评估 {len(dates) - 1 - i0} 个月")
    print(f"日历年度覆盖：", end="")
    PF.coverage_report(dates)

    px = {c: [panel[c][d] for d in dates] for c in codes}
    # 逐月收益（只在 i0 之后评估）
    rets = {}
    for c in codes:
        r = []
        for i in range(len(dates) - 1):
            a, b = px[c][i], px[c][i + 1]
            r.append(None if (a is None or b is None or a <= 0) else b / a - 1)
        rets[c] = r

    def port(sel_fn, rebal=True, t_from=None):
        """sel_fn(i) -> 持有的 code 列表；rebal=False 时权重随价格漂移。"""
        st = i0 if t_from is None else t_from
        if not rebal:
            # 期初等权买入、此后不动
            w0 = {c: 1 / len(sel_fn(st)) for c in sel_fn(st)}
            shares = {c: w0[c] / px[c][st] for c in w0}
            eq = []
            for i in range(st, len(dates) - 1):
                v = sum(sh * px[c][i + 1] for c, sh in shares.items())
                eq.append(v)
            nav0 = sum(sh * px[c][st] for c, sh in shares.items())
            nav = 1.0
            rs = []
            prev = nav0
            for v in eq:
                rs.append(v / prev - 1)
                prev = v
            return rs
        # 月度再平衡 + 成本
        rs, prev_sel = [], None
        for i in range(st, len(dates) - 1):
            sel = [c for c in sel_fn(i) if rets[c][i] is not None]
            if not sel:
                rs.append(0.0)
                prev_sel = []
                continue
            r = statistics.fmean([rets[c][i] for c in sel])
            cost = 0.0
            if prev_sel is not None and set(sel) != set(prev_sel):
                if prev_sel:
                    turn = (len(set(sel) - set(prev_sel)) / len(sel)
                            + len(set(prev_sel) - set(sel)) / len(prev_sel)) / 2
                else:
                    turn = 1.0
                cost = turn * (BUY_COST + SELL_COST)
            rs.append(r - cost)
            prev_sel = sel
        return rs

    # ── P1 池子 ──────────────────────────────────────────────────
    all_codes = lambda i: codes
    r_bh = port(all_codes, rebal=False)
    r_reb = port(all_codes, rebal=True)
    p_bh, p_reb = perf(r_bh), perf(r_reb)
    print("\n" + "=" * 76)
    print("P1 池子表现（2023-01 → 2026-10，后复权月末，税前）")
    print("=" * 76)
    print(f"  {'方式':22s} {'累计':>10s} {'年化':>8s} {'波动':>7s} {'最大回撤':>9s} {'Calmar':>7s}")
    for lab, p in (("期初买入不动", p_bh), ("月度等权再平衡", p_reb)):
        print(f"  {lab:22s} {p['total'] * 100:+9.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['vol'] * 100:6.2f}% {p['mdd'] * 100:+8.2f}% {p['calmar']:7.2f}")

    # ── P2 逐只 ─────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("P2 逐只表现（2023-01 → 2026-10，期初买入不动）")
    print("=" * 76)
    rows = []
    for c in codes:
        r = [rets[c][i] for i in range(i0, len(dates) - 1) if rets[c][i] is not None]
        p = perf(r)
        rows.append((p["cagr"], c, p))
    rows.sort(reverse=True)
    print(f"  {'名称':8s} {'累计':>10s} {'年化':>8s} {'最大回撤':>9s} {'Calmar':>7s}")
    for cagr, c, p in rows:
        print(f"  {D.NAMES[c]:8s} {p['total'] * 100:+9.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['mdd'] * 100:+8.2f}% {p['calmar']:7.2f}")
    pos = sum(1 for _, _, p in rows if p["total"] > 0)
    print(f"\n  正收益 {pos}/{len(rows)} 只；"
          f"年化中位 {statistics.median([p['cagr'] for _, _, p in rows]) * 100:+.2f}%")

    # ── P3 策略 ─────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("P3 策略对比（同窗口，含交易成本；低波/动量用预热期数据算信号）")
    print("=" * 76)

    def lowvol(k_frac=0.5, lb=12):
        def f(i):
            if i < lb:
                return codes
            v = {}
            for c in codes:
                r = [rets[c][j] for j in range(i - lb + 1, i + 1) if rets[c][j] is not None]
                if len(r) >= lb // 2:
                    v[c] = statistics.pstdev(r)
            if not v:
                return codes
            k = max(1, int(len(v) * k_frac))
            return sorted(v, key=lambda c: v[c])[:k]
        return f

    def momentum(k_frac=0.5, lb=6):
        def f(i):
            if i < lb:
                return codes
            m = {}
            for c in codes:
                a, b = px[c][i - lb], px[c][i]
                if a and b and a > 0:
                    m[c] = b / a - 1
            if not m:
                return codes
            k = max(1, int(len(m) * k_frac))
            return sorted(m, key=lambda c: -m[c])[:k]
        return f

    def trend(i):
        out = []
        for c in codes:
            cl = [v for v in px[c][:i + 1] if v]
            ma = sma(cl, 12, len(cl) - 1)
            if ma and px[c][i] and px[c][i] > ma:
                out.append(c)
        return out or codes

    tests = [
        ("买入持有(不动)", None),
        ("月度再平衡", all_codes),
        ("低波 50% (lb12)", lowvol(0.5, 12)),
        ("低波 30% (lb12)", lowvol(0.3, 12)),
        ("动量 50% (6月)", momentum(0.5, 6)),
        ("close>MA12 过滤", trend),
    ]
    print(f"  {'策略':20s} {'累计':>10s} {'年化':>8s} {'回撤':>9s} {'Calmar':>7s} {'vs 不动':>9s}")
    res = {}
    for lab, fn in tests:
        r = r_bh if fn is None else port(fn, rebal=True)
        p = perf(r)
        res[lab] = p
        ex = p["cagr"] - p_bh["cagr"]
        print(f"  {lab:20s} {p['total'] * 100:+9.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['mdd'] * 100:+8.2f}% {p['calmar']:7.2f} {ex * 100:+8.2f}pp")

    # ── P4 持有期分布 ───────────────────────────────────────────
    print("\n" + "=" * 76)
    print("P4 持有期分布：任一时点买入、持 N 个月后卖出（期初买入不动口径）")
    print("=" * 76)
    hold = {}
    for N in (6, 12, 24, 36):
        xs = []
        for i in range(i0, len(dates) - N):
            v0 = sum(px[c][i] for c in codes) / len(codes)
            v1 = sum(px[c][i + N] for c in codes) / len(codes)
            xs.append(v1 / v0 - 1)
        if not xs:
            continue
        xs.sort()
        hold[N] = {"n": len(xs), "min": xs[0], "p25": xs[len(xs) // 4],
                   "median": statistics.median(xs), "p75": xs[len(xs) * 3 // 4],
                   "max": xs[-1], "neg": sum(1 for x in xs if x < 0) / len(xs)}
        h = hold[N]
        print(f"  持有 {N:2d} 月 (n={h['n']:2d}): 最差 {h['min'] * 100:+7.2f}%  "
              f"P25 {h['p25'] * 100:+7.2f}%  中位 {h['median'] * 100:+7.2f}%  "
              f"P75 {h['p75'] * 100:+7.2f}%  最好 {h['max'] * 100:+7.2f}%  "
              f"亏损概率 {h['neg'] * 100:4.0f}%")
    print("  ⚠️ 短窗口下 24/36 月的样本很少（n<25），分布仅作方向参考")

    # ── P5 判定 ─────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print(f"P5 判定（回撤上限 {TARGET_MDD * 100:.0f}%）")
    print("=" * 76)
    print(f"  {'策略':20s} {'年化':>8s} {'回撤':>9s} {'20%回撤年化':>11s} {'超额':>9s} {'判定':>5s}")
    base20 = min(1.0, TARGET_MDD / abs(p_bh["mdd"])) * p_bh["cagr"]
    verdict = {}
    for lab, p in res.items():
        s = min(1.0, TARGET_MDD / abs(p["mdd"])) if p["mdd"] else 1.0
        cg = s * p["cagr"]
        ex = cg - base20
        ok = (cg >= 0.03) and (abs(p["mdd"]) <= TARGET_MDD + 1e-9) and (ex >= 0.02)
        verdict[lab] = {"cagr20": cg, "excess": ex, "ok": ok}
        print(f"  {lab:20s} {p['cagr'] * 100:+7.2f}% {p['mdd'] * 100:+8.2f}% "
              f"{cg * 100:+10.2f}% {ex * 100:+8.2f}pp {'✓' if ok else '✗':>4s}")
    print(f"\n  （基准 = 买入持有压到 20% 回撤：{base20 * 100:+.2f}%）")
    win = [k for k, v in verdict.items() if v["ok"]]
    print(f"  通过：{win if win else '**无**'}")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "short_2023.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"window": [dates[i0], dates[-1]], "months": len(dates) - 1 - i0,
                   "n": len(codes), "buyhold": p_bh, "rebalance": p_reb,
                   "strategies": res, "hold": {str(k): v for k, v in hold.items()},
                   "verdict": verdict, "winners": win, "base20": base20,
                   "per_stock": [{"code": c, "name": D.NAMES[c], **p}
                                 for _, c, p in rows]},
                  f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
