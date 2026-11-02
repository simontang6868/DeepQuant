# -*- coding: utf-8 -*-
"""阶段 2 通用因子检验（参数化）

用法：python test_factor.py F5
      python test_factor.py ALL      # 依次跑全部候选

纪律：
  - 每个因子单独检验、单独判定，不择优
  - 方向双向报告（不预设正负），避免事后挑选方向
  - 多重检验：6 个候选因子构成一个族，族错误率 5% → 单次门槛 z = 2.64
    （单次门槛 1.96 仅作参考，只有 |t| ≥ 2.64 才算稳健发现）
  - 主指标：单边多头 Top5 相对全池等权的 alpha（不用多空价差）
"""
import os, sys, json
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "out")

K = 5
N_RANDOM = 200
SEED = 20261026
T_SINGLE = 1.96
T_FAMILY = 2.64          # 6 次检验的 Bonferroni 门槛
SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026")]

FACTORS = {
    "f1": ("F1 日内收益均值", "散户日内追高→反转"),
    "f2": ("F2 振幅 TR20/close", "分歧大→偏弱"),
    "f3": ("F3 换手加速度 v5/v60", "放量=情绪→偏弱"),
    "f4": ("F4 日收益偏度 20日", "彩票偏好→偏弱"),
    "f5": ("F5 量价相关 corr(ret,vol)", "量增价跌=派发→偏弱"),
    "f6": ("F6 距60日高点回撤", "深回撤→待验"),
}
PEERS = ["f2", "vol20", "ret20", "ret5", "amihud"]


def perf(r):
    r = np.asarray([x for x in r if not np.isnan(x)], float)
    if len(r) == 0:
        return None
    eq = np.cumprod(1 + r)
    yrs = len(r) / 12
    ann = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq)
    return {"n": len(r), "total": float(eq[-1] - 1), "annual": float(ann),
            "mdd": float(np.min(eq / peak - 1)), "vol": float(np.std(r, ddof=1) * np.sqrt(12))}


def tstat(x):
    x = np.asarray([v for v in x if not np.isnan(v)], float)
    n = len(x)
    if n < 3:
        return np.nan, np.nan, np.nan, np.nan
    mu = x.mean(); se = x.std(ddof=1) / np.sqrt(n)
    return mu, (mu / se if se > 0 else np.nan), 2.8016 * se, float((x > 0).mean())


def spearman(a, b):
    ra = pd.Series(np.asarray(a, float)).rank().to_numpy()
    rb = pd.Series(np.asarray(b, float)).rank().to_numpy()
    m = ~(np.isnan(ra) | np.isnan(rb))
    if m.sum() < 30:
        return np.nan
    return float(np.corrcoef(ra[m], rb[m])[0, 1])


def run_one(panel, fac, verbose=True):
    nm, mech = FACTORS.get(fac, (fac, ""))
    p = panel[panel["ok_buy"] & panel[fac].notna() & panel["fwd20"].notna()].copy()
    if len(p) < 10000:
        print(f"{nm}: 样本不足 {len(p)}")
        return None

    # 重复性
    peers = {}
    for q in PEERS:
        if q == fac or q not in p.columns:
            continue
        cs = [spearman(g[fac], g[q]) for _, g in p.groupby("date") if len(g) >= 100]
        cs = [x for x in cs if not np.isnan(x)]
        if cs:
            peers[q] = float(np.median(cs))
    worst = max(peers.items(), key=lambda kv: abs(kv[1])) if peers else ("-", 0.0)

    # 分组
    p["rp"] = p.groupby("date")[fac].rank(pct=True, method="average")
    p["q"] = np.clip(np.floor(p["rp"] * 5), 0, 4)
    p.loc[p["rp"].isna(), "q"] = np.nan
    grp = p.dropna(subset=["q"]).groupby("q")["fwd20"].mean()

    # 双向 TopK + 全池
    lo_r, hi_r, all_r, pools = {}, {}, {}, {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        all_r[d] = g["fwd20"].mean()
        s = g.sort_values(fac)
        a = s.head(K)["fwd20"].mean()
        b = s.tail(K)["fwd20"].mean()
        if len(s) >= 2 * K:
            lo_r[d] = a
            hi_r[d] = b
        pools[d] = g["fwd20"].to_numpy()
    common = sorted(set(all_r) & set(lo_r) & set(hi_r))
    if len(common) < 60:
        print(f"{nm}: 共同月份仅 {len(common)}")
        return None

    a_lo = {d: lo_r[d] - all_r[d] for d in common}
    a_hi = {d: hi_r[d] - all_r[d] for d in common}
    mu_l, t_l, mde_l, w_l = tstat(list(a_lo.values()))
    mu_h, t_h, mde_h, w_h = tstat(list(a_hi.values()))

    # 随机基线
    rng = np.random.default_rng(SEED)
    ra = []
    for _ in range(N_RANDOM):
        rr = [pools[d][rng.integers(0, len(pools[d]), K)].mean() for d in common]
        ra.append(perf(rr)["annual"])
    ra = np.array(ra)

    st_lo = perf([lo_r[d] for d in common])
    st_hi = perf([hi_r[d] for d in common])
    st_all = perf([all_r[d] for d in common])

    # 分段
    seg = {}
    for lo, hi, lab in SEGS:
        ds = [d for d in common if lo <= d <= hi]
        if len(ds) < 12:
            continue
        seg[lab] = {"lo": tstat([a_lo[d] for d in ds]), "hi": tstat([a_hi[d] for d in ds])}

    # 剔最好 5%
    arr = np.array(list(a_lo.values())) if abs(mu_l) > abs(mu_h) else np.array(list(a_hi.values()))
    side = "低端Top5" if abs(mu_l) > abs(mu_h) else "高端Top5"
    ncut = max(1, len(arr) // 20)
    if arr.mean() > 0:
        keep = np.sort(arr)[:-ncut] if ncut else arr
    else:
        keep = np.sort(arr)[ncut:]
    _, t_drop, _, _ = tstat(keep)

    res = {"factor": fac, "name": nm, "mech": mech, "n_months": len(common),
           "range": [common[0], common[-1]], "peers": peers, "worst_peer": worst[0],
           "worst_corr": worst[1], "group": {int(k): float(v) for k, v in grp.items()},
           "lo": {"mu": mu_l, "t": t_l, "mde": mde_l, "win": w_l, "stat": st_lo},
           "hi": {"mu": mu_h, "t": t_h, "mde": mde_h, "win": w_h, "stat": st_hi},
           "bench": st_all, "random": {"median": float(np.median(ra)),
                                       "p95": float(np.percentile(ra, 95))},
           "segments": seg, "drop_side": side, "t_drop_best5": float(t_drop)}

    if verbose:
        print(f"\n{'='*80}")
        print(f"{nm}   （机制预期：{mech}）")
        print("=" * 80)
        print(f"  样本 {len(common)} 月（{common[0]}~{common[-1]}）")
        print(f"  与已有因子秩相关：" + "｜".join(f"{k} {v:+.2f}" for k, v in peers.items())
              + f"  → 最高 |ρ| {abs(worst[1]):.2f}（{worst[0]}）")
        print(f"  分5组 fwd20：" + "｜".join(f"Q{int(k)+1} {v*100:+.2f}%" for k, v in grp.items()))
        print(f"  低端Top5 alpha {mu_l*100:+.3f}pp  t={t_l:+.2f}  MDE {mde_l*100:.3f}pp  胜率 {w_l*100:.1f}%")
        print(f"  高端Top5 alpha {mu_h*100:+.3f}pp  t={t_h:+.2f}  MDE {mde_h*100:.3f}pp  胜率 {w_h*100:.1f}%")
        print(f"  年化：低端 {st_lo['annual']*100:.2f}%｜高端 {st_hi['annual']*100:.2f}%"
              f"｜全池 {st_all['annual']*100:.2f}%｜随机中位 {np.median(ra)*100:.2f}%"
              f" (p95 {np.percentile(ra,95)*100:.2f}%)")
        for lab, v in seg.items():
            print(f"    {lab:<18} 低端 {(v['lo'][0])*100:+.3f}pp t={(v['lo'][1]):+.2f}"
                  f"｜高端 {(v['hi'][0])*100:+.3f}pp t={(v['hi'][1]):+.2f}")
        print(f"  剔最好5%月份后（{side}）t = {t_drop:+.2f}")
        ok_single = max(abs(t_l), abs(t_h)) >= T_SINGLE
        ok_family = max(abs(t_l), abs(t_h)) >= T_FAMILY
        print(f"  单次门槛 1.96：{'过' if ok_single else '不过'}｜"
              f"族门槛 2.64（6 因子 Bonferroni）：{'过' if ok_family else '不过'}")
        print(f"  ⇒ 判定：{'有信号' if ok_family else ('边缘，不足以采信' if ok_single else 'NO_EDGE')}")
    return res


def main():
    panel = pd.read_parquet(PANEL)
    targets = sys.argv[1:] or ["f5"]
    if targets[0].upper() == "ALL":
        targets = list(FACTORS.keys())
    out = {}
    for f in targets:
        r = run_one(panel, f)
        if r:
            out[f] = r
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "factor_results.json")
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except Exception:
            old = {}
    old.update(out)
    json.dump(old, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
