#!/usr/bin/env python
"""v6：用正确数据对齐（并集+前向填充）重跑分散池全部结论。

【本脚本取代 v3/v4/v5 的所有数值结论】
  v3/v4/v5 用严格交集取日历 → 2015 股灾月份（5-12 月）整段缺失 →
  2015 年最大回撤被算成 0.00%、全部回撤类结论系统性低估。
  v6 用 panel_fix 的并集+ffill 重建，停牌月收益按 0 计（价格不变）。

【主分析口径】
  池 = 20 只 / 12 行业，但剔除 601098 中南传媒（2010-10 才上市，会把窗口砍到 2011）
  → 19 只 / 11 行业，窗口 2007-11 → 2026-10（约 19 年，含 2008 危机 + 2015 股灾）
  稳健性对照：含 601098 的 20 只 / 2011 起

【判定】沿用用户四条：年化≥3% / 回撤≤20% / 超额≥+2pp / 样本外不翻转
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_diverse_diag as D
import panel_fix as PF

OLD11 = ["601398", "601668", "601699", "601808", "600583", "600188", "600256",
         "601919", "601225", "600985", "601857"]
EXCLUDE_V6 = ["601098"]          # 上市太晚
BETA_WIN, MIN_OBS, TARGET_MDD = 36, 24, 0.20


def perf(r: list[float]) -> dict:
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in r:
        nav *= (1 + x)
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    yrs = len(r) / 12
    cagr = nav ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    return {"cagr": cagr, "vol": statistics.pstdev(r) * (12 ** 0.5), "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd else float("nan"),
            "total": nav - 1, "months": len(r)}


def ols(y, x):
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((v - mx) * (w - my) for v, w in zip(x, y))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def corr(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = (sum((x - ma) ** 2 for x in a)) ** 0.5
    db = (sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / (da * db) if da and db else float("nan")


def cap_weights(w: dict[str, float], cap: float) -> dict[str, float]:
    w = dict(w)
    for _ in range(200):
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
        rest = {k: v for k, v in w.items() if v < cap - 1e-12}
        if not rest:
            break
        tot = sum(rest.values())
        for k in rest:
            w[k] += excess * (rest[k] / tot)
    s = sum(w.values())
    return {k: v / s for k, v in w.items() if v > 1e-12}


def hhi(w: dict[str, float]) -> tuple[float, float, float]:
    s = sum(w.values())
    p = [v / s for v in w.values()]
    h = sum(x * x for x in p)
    return h, max(p), 1 / h


def analyze(series, pool, dates_from, label, verbose=True):
    """对一个池做完整分析，返回结果字典。"""
    codes = [c for c in pool if c in series]
    dates, panel = PF.build_panel({c: series[c] for c in codes}, start=dates_from)
    rets = PF.month_rets(panel, dates)
    T = len(dates) - 1

    if verbose:
        print(f"\n{'=' * 78}")
        print(f"{label}   {len(codes)} 只   {dates[0]} → {dates[-1]}  T={T}")
        print("=" * 78)
        PF.coverage_report(dates)

    r = PF.port_rets(rets, codes)
    p = perf(r)

    # N_eff（仅用每只票自己完整的序列，避免 None）
    rr = {c: [0.0 if v is None else v for v in rets[c]] for c in codes}
    pairs = [corr(rr[a], rr[b]) for i, a in enumerate(codes) for b in codes[i + 1:]]
    rho = statistics.fmean(pairs)
    N = len(codes)
    denom = 1 + (N - 1) * rho
    n_eff = min(N, N / denom) if denom > 0 else N

    if verbose:
        print(f"\n  【全窗口】累计 {p['total'] * 100:+.1f}%  年化 {p['cagr'] * 100:+.2f}%  "
              f"波动 {p['vol'] * 100:.2f}%  回撤 {p['mdd'] * 100:+.2f}%  Calmar {p['calmar']:.2f}")
        print(f"  【分散度】N={N}  ρ̄={rho:.4f}  N_eff={n_eff:.2f}")

    return {"codes": codes, "dates": dates, "rets": rets, "r": r, "perf": p,
            "rho": rho, "n_eff": n_eff, "T": T}


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs]
    series = D.load_monthly(set(pool) | set(OLD11))

    # ── 主分析：19 只 11 行业，2007-11 起 ─────────────────────────
    pool_main = [c for c in pool if c not in EXCLUDE_V6]
    A = analyze(series, pool_main, "20071101", "【主分析】分散池（剔除中南传媒）")

    # ── 对照 1：11 只资源池（同窗口）─────────────────────────────
    old = [c for c in OLD11 if c in series]
    B = analyze(series, old, "20071101", "【对照 1】11 只资源池")

    # ── 对照 2：含中南传媒的 20 只（2011 起）─────────────────────
    C = analyze(series, pool, "20110101", "【对照 2】20 只 12 行业（含中南传媒, 2011 起）")

    # ── 分散 vs 集中 ─────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("① 分散化到底带来了什么（同窗口 2007-11 → 2026-10，等权月度再平衡）")
    print("=" * 78)
    print(f"  {'组合':24s} {'累计':>10s} {'年化':>8s} {'波动':>7s} {'回撤':>9s} {'Calmar':>7s}")
    for lab, R in (("19 只 11 行业", A), ("11 只资源池", B)):
        p = R["perf"]
        print(f"  {lab:24s} {p['total'] * 100:+9.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['vol'] * 100:6.2f}% {p['mdd'] * 100:+8.2f}% {p['calmar']:7.2f}")
    pa, pb = A["perf"], B["perf"]
    print(f"  → 年化 {pa['cagr'] * 100 - pb['cagr'] * 100:+.2f}pp  "
          f"回撤 {pb['mdd'] * 100:.1f}% → {pa['mdd'] * 100:.1f}% "
          f"（改善 {(1 - abs(pa['mdd']) / abs(pb['mdd'])) * 100:+.1f}%）")

    # ── 分年度 ───────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("② 分年度：分散 vs 集中（月度再平衡，税前）")
    print("=" * 78)
    print(f"  {'年份':6s} {'19只分散':>10s} {'11只资源':>10s} {'差':>9s} "
          f"{'分散年内回撤':>12s} {'资源年内回撤':>12s}")
    years = sorted({d[:4] for d in A["dates"][1:]})
    y_win_div = y_win_old = 0
    yearly = {}
    for y in years:
        ia = [i for i in range(A["T"]) if A["dates"][i + 1][:4] == y]
        ib = [i for i in range(B["T"]) if B["dates"][i + 1][:4] == y]
        if len(ia) < 2 or len(ib) < 2:
            continue
        ra = [A["r"][i] for i in ia]
        rb = [B["r"][i] for i in ib]
        na = nb = 1.0
        for x in ra:
            na *= (1 + x)
        for x in rb:
            nb *= (1 + x)
        ma, mb = perf(ra)["mdd"], perf(rb)["mdd"]
        if na > nb:
            y_win_div += 1
        if ma > mb:
            y_win_old += 1
        yearly[y] = {"div": na - 1, "old": nb - 1, "mdd_div": ma, "mdd_old": mb,
                     "months": len(ia)}
        print(f"  {y:6s} {(na - 1) * 100:+9.2f}% {(nb - 1) * 100:+9.2f}% "
              f"{((na - 1) - (nb - 1)) * 100:+8.2f}pp {ma * 100:+11.2f}% {mb * 100:+11.2f}%")
    n = len(yearly)
    print(f"\n  分散池收益更高的年份 {y_win_div}/{n}；回撤更小的年份 {y_win_old}/{n}")

    # ── 关键年份检验（股灾是否被补回）────────────────────────────
    print("\n" + "=" * 78)
    print("③ 关键下跌段（数据对齐修正后，股灾月份应已补回）")
    print("=" * 78)
    for lab, a, b in (("2008 金融危机", "20080101", "20081231"),
                      ("2015 股灾", "20150601", "20160229"),
                      ("2018 全年", "20180101", "20181231"),
                      ("2022 全年", "20220101", "20221231"),
                      ("2024-01 微盘崩", "20240101", "20240229")):
        idx = [i for i in range(A["T"]) if a <= A["dates"][i + 1] <= b]
        if len(idx) < 2:
            print(f"  {lab:16s} 数据不足")
            continue
        ra = [A["r"][i] for i in idx]
        nav = 1.0
        for x in ra:
            nav *= (1 + x)
        print(f"  {lab:16s} 分散 {(nav - 1) * 100:+7.2f}%  "
              f"年内回撤 {perf(ra)['mdd'] * 100:+7.2f}%  ({len(idx)} 月)")

    # ── 滚动 beta 策略（修正后重做 v3/v4 的结论）─────────────────
    print("\n" + "=" * 78)
    print("④ 滚动 beta（36 月）策略——v3/v4 的「通过」在修正后是否还成立")
    print("=" * 78)
    codes = A["codes"]
    rets = A["rets"]
    T, N = A["T"], len(codes)
    rr = {c: [0.0 if v is None else v for v in rets[c]] for c in codes}
    mkt = [statistics.fmean([rr[c][i] for c in codes]) for i in range(T)]
    betas_roll = {}
    for t in range(MIN_OBS, T):
        lo = max(0, t - BETA_WIN)
        betas_roll[t] = {c: ols(rr[c][lo:t], mkt[lo:t])[1] for c in codes}
    first = min(betas_roll)
    print(f"  滚动 beta 首次可用 {A['dates'][first + 1]}，评估 {T - first} 个月")

    def run(wfun):
        return [sum(wfun(t)[c] * rr[c][t + 1] for c in codes)
                for t in range(first, T - 1)]

    def w_invbeta(power, cap=None):
        def f(t):
            iw = {c: 1.0 / max(betas_roll[t][c], 0.15) ** power for c in codes}
            s = sum(iw.values())
            w = {c: iw[c] / s for c in codes}
            return cap_weights(w, cap) if cap else w
        return f

    i_split = next(i for i, d in enumerate(A["dates"]) if d >= "20210101")
    print(f"  {'方案':22s} {'年化':>8s} {'回撤':>8s} {'Calmar':>7s} {'有效持仓':>8s} "
          f"{'内年化':>8s} {'外年化':>8s} {'外翻':>5s}")
    res = {}
    cands = [("等权满仓", lambda t: {c: 1 / N for c in codes})]
    for k in (1.0, 1.5, 2.0):
        cands.append((f"1/beta^{k:.1f}", w_invbeta(k)))
    for k in (1.5, 2.0):
        for cap in (0.10, 0.15):
            cands.append((f"1/beta^{k:.1f} cap{cap * 100:.0f}%", w_invbeta(k, cap)))
    for lab, wf in cands:
        r = run(wf)
        p = perf(r)
        es = statistics.median([hhi(wf(t))[2] for t in range(first, min(T - 1, first + 24))])
        seg = []
        for lo, hi in ((first, i_split - 1), (i_split, T - 1)):
            rr2 = [sum(wf(t)[c] * rr[c][t + 1] for c in codes) for t in range(lo, hi)]
            seg.append(perf(rr2)["cagr"] if len(rr2) > 6 else float("nan"))
        flip = seg[1] < seg[0] - 0.02
        res[lab] = {"perf": p, "eff_hold": es, "splits": seg, "flip": flip}
        print(f"  {lab:22s} {p['cagr'] * 100:+7.2f}% {p['mdd'] * 100:+7.2f}% "
              f"{p['calmar']:7.2f} {es:8.2f} {seg[0] * 100:+7.2f}% {seg[1] * 100:+7.2f}% "
              f"{'是' if flip else '否':>4s}")

    # ── 最终判定 ─────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"⑤ 判定（回撤上限 {TARGET_MDD * 100:.0f}%，现金 0 收益）")
    print("=" * 78)
    base = res["等权满仓"]["perf"]
    base20 = TARGET_MDD / abs(base["mdd"]) * base["cagr"]
    print(f"  基准（等权满仓压到 20% 回撤）：年化 {base20 * 100:+.2f}%")
    print(f"\n  {'方案':22s} {'20%回撤年化':>11s} {'超额':>9s} {'外翻':>5s} {'全过':>5s}")
    verdict = {}
    for lab, v in res.items():
        p = v["perf"]
        s = min(1.0, TARGET_MDD / abs(p["mdd"]))
        cg = s * p["cagr"]
        ex = cg - base20
        ok = (cg >= 0.03) and (abs(p["mdd"]) <= TARGET_MDD + 1e-9) and (ex >= 0.02) and not v["flip"]
        verdict[lab] = {"cagr_at_20": cg, "excess": ex, "ok": ok}
        print(f"  {lab:22s} {cg * 100:+10.2f}% {ex * 100:+8.2f}pp "
              f"{'是' if v['flip'] else '否':>4s} {'✓' if ok else '✗':>4s}")
    winners = [k for k, v in verdict.items() if v["ok"]]
    print(f"\n  通过全部四条：{winners if winners else '**无**'}")

    # ── 回撤前沿 ─────────────────────────────────────────────────
    print(f"\n⑥ 回撤—年化前沿（等权满仓，现金 0 收益）")
    print(f"  {'回撤上限':>8s} {'需仓位':>8s} {'年化':>8s} {'Calmar':>7s}")
    frontier = {}
    for target in (0.10, 0.15, 0.20, 0.25, 0.30):
        s = min(1.0, target / abs(base["mdd"]))
        cg, md = s * base["cagr"], s * base["mdd"]
        frontier[f"{target:.0%}"] = {"pos": s, "cagr": cg, "mdd": md,
                                     "calmar": cg / abs(md) if md else float("nan")}
        print(f"  {target * 100:7.0f}% {s * 100:7.1f}% {cg * 100:+7.2f}% "
              f"{cg / abs(md) if md else float('nan'):7.2f}")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "diverse_v6_fixed.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "main": {"n": len(codes), "window": [A["dates"][0], A["dates"][-1]],
                     "T": A["T"], "perf": A["perf"], "rho": A["rho"], "n_eff": A["n_eff"]},
            "old11": {"n": len(B["codes"]), "perf": B["perf"], "rho": B["rho"],
                      "n_eff": B["n_eff"]},
            "with_601098": {"n": len(C["codes"]), "perf": C["perf"]},
            "yearly": yearly, "y_win_div": y_win_div, "y_win_mdd": y_win_old,
            "rolling": {k: {"cagr": v["perf"]["cagr"], "mdd": v["perf"]["mdd"],
                            "eff_hold": v["eff_hold"], "splits": v["splits"],
                            "flip": v["flip"]} for k, v in res.items()},
            "verdict": verdict, "winners": winners,
            "base_at_20": base20, "frontier": frontier,
            "data_fix": "并集日历+ffill；旧 v3/v4/v5 数值作废",
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
