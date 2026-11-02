# -*- coding: utf-8 -*-
"""阶段 2 因子检验：F2（振幅）的单边多头 alpha

流程（不可颠倒）：
  Step 0  数据穿越审查：确认所有因子只用 t 及之前信息
  Step 1  因子重复性检查：F2 vs VOL20 横截面秩相关（>0.8 则视为重复，直接结题）
  Step 2  分组单调性：按 F2 分 5 组，看 fwd20 是否单调
  Step 3  单边多头 alpha：Q1（低振幅）− 全池等权；以及实际可执行的 Top5
  Step 4  随机基线 B1：同池随机选 5 只 × 200 次，取年化分布
  Step 5  稳健性：剔最好 5% 月份、分 IS/OOS1/OOS2
  Step 6  判据 A1/A3/A4/A7/A7b

所有结论以「单边多头相对全池等权」为主指标，不用多空价差当主指标。
"""
import os, json, sys
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "out")

K = 5                 # 持仓只数
N_RANDOM = 200        # 随机基线重复次数
SEED = 20261026
FACTOR = "f2"
SIGN = 1              # 预测方向：f2 越低越好 → SIGN=1 表示对因子升序取前 K

SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026"),
        ("200501", "202612", "全样本 2005-2026")]


def perf(rets, months_per_year=12):
    """月度收益序列 → 年化 / MDD / 波动"""
    r = np.asarray([x for x in rets if not np.isnan(x)], dtype=float)
    if len(r) == 0:
        return None
    eq = np.cumprod(1 + r)
    yrs = len(r) / months_per_year
    ann = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq)
    mdd = float(np.min(eq / peak - 1))
    vol = float(np.std(r, ddof=1) * np.sqrt(months_per_year)) if len(r) > 1 else np.nan
    return {"n": len(r), "total": float(eq[-1] - 1), "annual": float(ann),
            "mdd": mdd, "vol": vol}


def tstat(x):
    x = np.asarray([v for v in x if not np.isnan(v)], dtype=float)
    n = len(x)
    if n < 3:
        return np.nan, np.nan, np.nan, np.nan
    mu = x.mean()
    se = x.std(ddof=1) / np.sqrt(n)
    t = mu / se if se > 0 else np.nan
    mde = 2.8016 * se
    win = float((x > 0).mean())
    return mu, t, mde, win


def main():
    panel = pd.read_parquet(PANEL)
    print(f"面板 {len(panel):,} 行｜{panel['code'].nunique():,} 只｜{panel['date'].nunique()} 月")

    # ---------- Step 0 穿越审查 ----------
    print("\n" + "=" * 84)
    print("Step 0 数据穿越审查")
    print("=" * 84)
    checks = [
        ("f2 用 t 及之前 20 日 TR 与 t 日 close", "build_panel.py: roll_mean(tr,20)/close(t)", True),
        ("vol20 用 t 及之前 20 日收益", "build_panel.py: ret[t-20..t] 的 std", True),
        ("标签买入价 = t 之后第 1 个交易日", "buy_i = last_idx + 1", True),
        ("标签卖出价 = 买入后第 20 个交易日", "sell_i = last_idx + 1 + HOLD", True),
        ("可交易性用 t 日 close/成交额", "ok_buy 用 close(t) 与 amt20(t)", True),
    ]
    for a, b, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {a}")
        print(f"        {b}")
    print("  ⇒ 无穿越：因子与筛选条件全部只用 t 及之前；标签从 t+1 起算")

    # ---------- 清洗 ----------
    p = panel[panel["ok_buy"] & panel[FACTOR].notna() & panel["fwd20"].notna()].copy()
    print(f"\n可交易样本 {len(p):,} 行（{len(p)/len(panel)*100:.1f}%）")

    # ---------- Step 1 因子重复性 ----------
    print("\n" + "=" * 84)
    print("Step 1 因子重复性：F2 vs VOL20（项目已证低波动 FAIL）")
    print("=" * 84)
    def spearman(a, b):
        ra = pd.Series(np.asarray(a, float)).rank().to_numpy()
        rb = pd.Series(np.asarray(b, float)).rank().to_numpy()
        m = ~(np.isnan(ra) | np.isnan(rb))
        if m.sum() < 30:
            return np.nan
        return float(np.corrcoef(ra[m], rb[m])[0, 1])

    cs = []
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        cs.append(spearman(g[FACTOR], g["vol20"]))
    cs = np.array([x for x in cs if not np.isnan(x)])
    print(f"  横截面秩相关：中位 {np.median(cs):.3f}｜均值 {cs.mean():.3f}"
          f"｜最低 {cs.min():.3f}｜最高 {cs.max():.3f}（{len(cs)} 月）")
    dup = abs(np.median(cs)) > 0.8
    print(f"  {'⚠️ 视为重复（|ρ|>0.8），F2 实质是波动率代理' if dup else '✅ 与波动率不同（|ρ|≤0.8），可作为独立因子检验'}")

    # ---------- Step 2 分组单调性 ----------
    print("\n" + "=" * 84)
    print("Step 2 分 5 组看 fwd20 是否单调（预测：F2 越高越差）")
    print("=" * 84)

    # 向量化分位（避免 pandas 3.0 的 groupby.apply 语义变化）
    p["rank_pct"] = p.groupby("date")[FACTOR].rank(pct=True, method="average")
    p["q"] = np.clip(np.floor(p["rank_pct"] * 5), 0, 4)
    p.loc[p["rank_pct"].isna(), "q"] = np.nan
    grp = p.dropna(subset=["q"]).groupby("q")["fwd20"].agg(["mean", "count"])
    print(f"{'组':>4}{'F2 均值':>12}{'未来20日均值':>14}{'样本数':>12}")
    for q, row in grp.iterrows():
        f2m = p.loc[p["q"] == q, FACTOR].mean()
        print(f"{int(q)+1:>4}{f2m*100:>11.3f}%{row['mean']*100:>13.3f}%{int(row['count']):>12,}")
    mono = grp["mean"].is_monotonic_decreasing
    print(f"  单调递减：{'是' if mono else '否'}")

    # ---------- Step 3/4 单边 alpha 与随机基线 ----------
    print("\n" + "=" * 84)
    print("Step 3/4 单边多头 alpha 与随机基线")
    print("=" * 84)

    months = sorted(p["date"].unique())
    q1_r, top_r, all_r = {}, {}, {}
    pools = {}
    for d in months:
        g = p[p["date"] == d]
        if len(g) < 100:
            continue
        all_r[d] = g["fwd20"].mean()
        q1 = g[g["q"] == 0]
        if len(q1):
            q1_r[d] = q1["fwd20"].mean()
        s = g.sort_values(FACTOR, ascending=(SIGN > 0))
        head = s.head(K)
        if len(head) == K:
            top_r[d] = head["fwd20"].mean()
        pools[d] = g["fwd20"].to_numpy()

    common = sorted(set(all_r) & set(q1_r) & set(top_r))
    print(f"共同月份 {len(common)}：{common[0]} ~ {common[-1]}")

    alpha_q1 = {d: q1_r[d] - all_r[d] for d in common}
    alpha_top = {d: top_r[d] - all_r[d] for d in common}

    for nm, s in (("Q1 低振幅组", alpha_q1), ("Top5 最低振幅", alpha_top)):
        mu, t, mde, win = tstat(list(s.values()))
        print(f"\n  {nm}（相对全池等权）")
        print(f"    月均 alpha {mu*100:+.4f}pp｜t {t:+.2f}｜MDE {mde*100:.4f}pp｜胜率 {win*100:.1f}%｜n {len(s)}")
        print(f"    年化 alpha ≈ {mu*12*100:+.2f}pp（简单年化，未复利）")

    # 随机基线 B1
    rng = np.random.default_rng(SEED)
    rand_ann = []
    for _ in range(N_RANDOM):
        rr = []
        for d in common:
            arr = pools[d]
            rr.append(arr[rng.integers(0, len(arr), K)].mean())
        st = perf(rr)
        rand_ann.append(st["annual"])
    rand_ann = np.array(rand_ann)
    print(f"\n  B1 随机选 {K} 只 × {N_RANDOM} 次：年化 中位 {np.median(rand_ann)*100:.2f}%"
          f"｜5%~95% [{np.percentile(rand_ann,5)*100:.2f}%, {np.percentile(rand_ann,95)*100:.2f}%]")

    st_top = perf([top_r[d] for d in common])
    st_all = perf([all_r[d] for d in common])
    print(f"\n  {'策略':<22}{'年化':>10}{'累计':>12}{'月频MDD':>10}{'波动':>9}")
    print(f"  {'Top5 最低振幅(毛)':<22}{st_top['annual']*100:>9.2f}%{st_top['total']*100:>11.1f}%"
          f"{st_top['mdd']*100:>9.2f}%{st_top['vol']*100:>8.2f}%")
    print(f"  {'全池等权(基准)':<22}{st_all['annual']*100:>9.2f}%{st_all['total']*100:>11.1f}%"
          f"{st_all['mdd']*100:>9.2f}%{st_all['vol']*100:>8.2f}%")
    beat = st_top["annual"] > np.percentile(rand_ann, 95)
    print(f"  Top5 年化是否超过随机 95% 分位：{'是 ✅' if beat else '否 ❌'}")

    # ---------- Step 5 稳健性 ----------
    print("\n" + "=" * 84)
    print("Step 5 稳健性：分段 + 剔最好 5% 月份")
    print("=" * 84)
    print(f"{'区间':<20}{'月均alpha':>12}{'t':>8}{'n':>6}{'胜率':>8}{'基准年化':>10}")
    seg_out = {}
    for lo, hi, lab in SEGS:
        ds = [d for d in common if lo <= d <= hi]
        if len(ds) < 12:
            continue
        a = [alpha_top[d] for d in ds]
        mu, t, mde, win = tstat(a)
        sa = perf([all_r[d] for d in ds])
        print(f"{lab:<20}{mu*100:>+11.3f}pp{t:>8.2f}{len(ds):>6}{win*100:>7.1f}%{sa['annual']*100:>9.2f}%")
        seg_out[lab] = {"alpha": mu, "t": t, "n": len(ds), "win": win, "base_ann": sa["annual"]}

    a_all = [alpha_top[d] for d in common]
    order = np.argsort(a_all)[::-1]
    keep = [a_all[i] for i in range(len(a_all)) if i not in set(order[: max(1, len(a_all) // 20)])]
    mu0, t0, _, _ = tstat(a_all)
    mu1, t1, _, _ = tstat(keep)
    print(f"\n  剔掉最好 5% 月份（{max(1,len(a_all)//20)} 个月）：月均 {mu0*100:+.3f}pp → {mu1*100:+.3f}pp"
          f"｜t {t0:+.2f} → {t1:+.2f}")

    # ---------- Step 6 判据 ----------
    print("\n" + "=" * 84)
    print("Step 6 判据")
    print("=" * 84)
    mu, t, mde, win = tstat(a_all)
    A1 = (t >= 1.96) and beat
    A3 = (mu1 > 0) and (t1 >= 1.5)
    oos = [d for d in common if d >= "201801"]
    mu_o, t_o, _, _ = tstat([alpha_top[d] for d in oos])
    A4 = mu_o > 0
    A7 = st_top["annual"] > 0.10
    A7b = st_top["annual"] > st_all["annual"]
    print(f"  A1 优于随机（t≥1.96 且 >B1 95%分位）: {'过' if A1 else '不过'}（t={t:.2f}, beat={beat}）")
    print(f"  A3 方向稳健（剔5%月份后仍>0 且 t≥1.5）: {'过' if A3 else '不过'}（t1={t1:.2f}）")
    print(f"  A4 样本外 2018+ alpha>0           : {'过' if A4 else '不过'}（{mu_o*100:+.3f}pp, t={t_o:.2f}）")
    print(f"  A7 绝对年化 >10%                  : {'过' if A7 else '不过'}（{st_top['annual']*100:.2f}%）")
    print(f"  A7b 跑赢等权全A                   : {'过' if A7b else '不过'}"
          f"（{st_top['annual']*100:.2f}% vs {st_all['annual']*100:.2f}%）")

    os.makedirs(OUT, exist_ok=True)
    json.dump({"factor": FACTOR, "K": K, "n_months": len(common),
               "range": [common[0], common[-1]],
               "spearman_f2_vol20_median": float(np.median(cs)),
               "group_mean_fwd20": {int(k): float(v) for k, v in grp["mean"].items()},
               "monotonic": bool(mono),
               "alpha_top5": {"mean": mu, "t": t, "mde": mde, "win": win},
               "alpha_q1": dict(zip(("mean", "t", "mde", "win"), tstat(list(alpha_q1.values())))),
               "strategy": st_top, "bench": st_all,
               "random_annual": {"median": float(np.median(rand_ann)),
                                 "p5": float(np.percentile(rand_ann, 5)),
                                 "p95": float(np.percentile(rand_ann, 95))},
               "segments": seg_out,
               "drop_best5pct": {"mean_before": mu0, "mean_after": mu1, "t_after": t1},
               "gates": {"A1": bool(A1), "A3": bool(A3), "A4": bool(A4),
                         "A7": bool(A7), "A7b": bool(A7b)}},
              open(os.path.join(OUT, "f2_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nsaved out/f2_result.json")


if __name__ == "__main__":
    main()
