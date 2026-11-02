# -*- coding: utf-8 -*-
"""SP-3：补买入规则（流动性分层）

Part A  效果检验：流动性前 20% 内随机 5 只 + F3 卖出，vs SP-2 的全池随机版
Part B  当前候选清单：用最新可得数据算 F3 与 amt20，输出可执行清单

按 SP3_预登记.md 执行（sha 7b7cb89b…）
"""
import os, sys, json, glob, hashlib
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
EXEC = os.path.join(ROOT, "data", "exec_all")
OUT = os.path.join(RD, "out")

K = 5
THRESH = 0.70
LIQ_TOP = 0.80          # 取 amt20 前 20%
N_PATHS = 200
SEED = 20261026
COST_PER_SWAP = 0.000204
PRICE_MAX, PRICE_MIN, AMT_MIN = 200.0, 1.0, 5e6


def check():
    p = os.path.join(RD, "sp3_prereg.sha256")
    want = open(p, encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD, "SP3_预登记.md"), "rb").read()).hexdigest()
    print(f"预登记校验 {want[:16]}… {'一致' if want==got else '不一致!'}")
    if want != got:
        sys.exit(1)


def perf(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    ann = eq[-1] ** (12 / len(r)) - 1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq)
    return float(ann), float(np.min(eq / peak - 1)), float(np.std(r, ddof=1) * np.sqrt(12))


def part_a():
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]

    by = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        codes = g["code"].to_numpy()
        by[d] = {
            "codes": codes,
            "fwd": g["fwd20"].to_numpy(float),
            "pct": g["f3pct"].to_numpy(float),
            "buy_rand": np.where(g["f3pct"].to_numpy(float) <= THRESH)[0],
            "buy_liq": np.where((g["f3pct"].to_numpy(float) <= THRESH)
                                & (g["liqpct"].to_numpy(float) >= LIQ_TOP))[0],
            "cmap": {c: i for i, c in enumerate(codes)},
        }
    months = sorted(by)
    print(f"Part A：月份 {len(months)}（{months[0]}~{months[-1]}）")

    def sim(mode, rng):
        d0 = months[0]
        b0 = by[d0]
        pool = b0["buy_rand"] if mode == "rand" else b0["buy_liq"]
        hold = [b0["codes"][i] for i in rng.choice(pool, K, replace=False)]
        rets, ns = [], []
        for t, d in enumerate(months):
            b = by[d]
            n_swap = 0
            if t > 0:
                sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                n_swap = len(sell)
                if n_swap:
                    pool_b = b["buy_rand"] if mode == "rand" else b["buy_liq"]
                    held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                    cand = pool_b[~np.isin(pool_b, held)] if len(pool_b) else np.array([], int)
                    if len(cand) >= n_swap:
                        hold = ([c for c in hold if c not in sell]
                                + [b["codes"][i] for i in rng.choice(cand, n_swap, replace=False)])
                    else:
                        n_swap = 0
            vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
            rets.append((float(np.mean(vals)) if vals else 0.0) - n_swap * COST_PER_SWAP)
            ns.append(n_swap)
        return rets, ns

    A = {"rand": [], "liq": []}
    V = {"rand": [], "liq": []}
    M = {"rand": [], "liq": []}
    NS = {"rand": [], "liq": []}
    for path in range(N_PATHS):
        for mode in ("rand", "liq"):
            rng = np.random.default_rng(SEED + path)
            r, ns = sim(mode, rng)
            a, m, v = perf(r)
            A[mode].append(a); M[mode].append(m); V[mode].append(v)
            NS[mode].append(np.mean(ns) / K)

    print("\n" + "=" * 82)
    print(f"{'组':<28}{'年化中位':>10}{'5%':>9}{'95%':>9}{'波动':>9}{'MDD':>9}{'月换手':>8}")
    for mode, nm in (("rand", "SP-2 全池随机"), ("liq", "SP-3 流动性前20%")):
        a = np.array(A[mode])
        print(f"{nm:<28}{np.median(a)*100:>+9.2f}%{np.percentile(a,5)*100:>+8.2f}%"
              f"{np.percentile(a,95)*100:>+8.2f}%{np.median(V[mode])*100:>8.2f}%"
              f"{np.median(M[mode])*100:>+8.2f}%{np.median(NS[mode])*100:>7.1f}%")

    d_ann = np.median(A["liq"]) - np.median(A["rand"])
    d_vol = np.median(V["liq"]) - np.median(V["rand"])
    print(f"\n  流动性版 − 随机版：年化 {d_ann*100:+.2f}pp｜波动 {d_vol*100:+.2f}pp"
          f"｜{(np.array(A['liq'])>np.array(A['rand'])).mean()*100:.1f}% 的路径更好")

    Q1 = d_ann >= -0.01
    Q2 = d_vol <= 0
    Q3 = np.median(M["liq"]) >= -0.6844
    Q4 = np.median(NS["liq"]) <= 0.30
    print(f"\n  Q1 年化不劣于随机（≥−1pp）: {'过' if Q1 else '不过'}（{d_ann*100:+.2f}pp）")
    print(f"  Q2 波动不上升            : {'过' if Q2 else '不过'}（{d_vol*100:+.2f}pp）")
    print(f"  Q3 回撤不恶化            : {'过' if Q3 else '不过'}")
    print(f"  Q4 月换手 ≤30%           : {'过' if Q4 else '不过'}（{np.median(NS['liq'])*100:.1f}%）")

    return {"rand": {k: float(np.median(v)) for k, v in (("annual", A["rand"]), ("vol", V["rand"]), ("mdd", M["rand"]))},
            "liq": {k: float(np.median(v)) for k, v in (("annual", A["liq"]), ("vol", V["liq"]), ("mdd", M["liq"]))},
            "diff_annual_pp": float(d_ann), "diff_vol_pp": float(d_vol),
            "win_rate": float((np.array(A["liq"]) > np.array(A["rand"])).mean()),
            "turnover_liq": float(np.median(NS["liq"])),
            "gates": {"Q1": bool(Q1), "Q2": bool(Q2), "Q3": bool(Q3), "Q4": bool(Q4)}}


def part_liq_ladder():
    """流动性分位 5 档 × 各档内随机 5 只 + F3 卖出，看收益随流动性的变化（描述性，不择优）"""
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]
    by = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        lp = g["liqpct"].to_numpy(float)
        by[d] = {"codes": g["code"].to_numpy(), "fwd": g["fwd20"].to_numpy(float),
                 "pct": g["f3pct"].to_numpy(float),
                 "liq": lp, "cmap": {c: i for i, c in enumerate(g["code"].to_numpy())}}
    months = sorted(by)
    bands = [(0.0, 0.2, "最小20%"), (0.2, 0.4, "20-40%"), (0.4, 0.6, "40-60%"),
             (0.6, 0.8, "60-80%"), (0.8, 1.0, "最大20%")]
    print("\n" + "=" * 82)
    print("流动性分档：各档内随机 5 只 + F3 卖出（200 路径中位，描述性分析）")
    print("=" * 82)
    print(f"{'流动性档':<14}{'年化中位':>10}{'5%':>9}{'95%':>9}{'波动':>9}{'该档股票数':>11}")
    out = {}
    for lo, hi, nm in bands:
        A, V, cnts = [], [], []
        for path in range(N_PATHS):
            rng = np.random.default_rng(SEED + path)
            b0 = by[months[0]]
            pool0 = np.where((b0["pct"] <= THRESH) & (b0["liq"] >= lo) & (b0["liq"] < hi))[0]
            if len(pool0) < K:
                continue
            hold = [b0["codes"][i] for i in rng.choice(pool0, K, replace=False)]
            rets = []
            for t, d in enumerate(months):
                b = by[d]
                ns = 0
                if t > 0:
                    sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                    ns = len(sell)
                    if ns:
                        pb = np.where((b["pct"] <= THRESH) & (b["liq"] >= lo) & (b["liq"] < hi))[0]
                        held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                        cand = pb[~np.isin(pb, held)] if len(pb) else np.array([], int)
                        if len(cand) >= ns:
                            hold = ([c for c in hold if c not in sell]
                                    + [b["codes"][i] for i in rng.choice(cand, ns, replace=False)])
                        else:
                            ns = 0
                        cnts.append(len(pb))
                vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
                rets.append((float(np.mean(vals)) if vals else 0.0) - ns * COST_PER_SWAP)
            a, m, v = perf(rets)
            A.append(a); V.append(v)
        A = np.array(A)
        print(f"{nm:<14}{np.median(A)*100:>+9.2f}%{np.percentile(A,5)*100:>+8.2f}%"
              f"{np.percentile(A,95)*100:>+8.2f}%{np.median(V)*100:>8.2f}%{int(np.median(cnts)) if cnts else 0:>11}")
        out[nm] = {"annual_median": float(np.median(A)),
                   "p5": float(np.percentile(A, 5)), "vol": float(np.median(V))}
    return out


def part_b():
    """当前候选：用 exec_all 最新数据算 F3 与 amt20"""
    rows = []
    files = sorted(glob.glob(os.path.join(EXEC, "*.exec.csv")))
    for f in files:
        cw = os.path.basename(f).replace(".exec.csv", "")
        try:
            df = pd.read_csv(f, usecols=["date", "close", "volume"])
        except Exception:
            continue
        if len(df) < 61:
            continue
        df = df.dropna()
        df = df[df["close"] > 0]
        if len(df) < 61:
            continue
        tail = df.tail(61)
        v = tail["volume"].to_numpy(float)
        c = tail["close"].to_numpy(float)
        if v[-60:].mean() <= 0:
            continue
        f3 = v[-5:].mean() / v[-60:].mean()
        amt20 = float((v[-20:] * 100.0 * c[-20:]).mean())
        rows.append({"code": cw, "last_date": str(tail["date"].iloc[-1]).replace("-", ""),
                     "close": float(c[-1]), "f3": float(f3), "amt20": amt20})
    df = pd.DataFrame(rows)
    print(f"\n\nPart B：扫描 {len(df)} 只，数据截止 {df['last_date'].max()}")
    ok = df[(df["close"].between(PRICE_MIN, PRICE_MAX)) & (df["amt20"] > AMT_MIN)].copy()
    print(f"  通过可交易性（价 1~200、amt20>500万）：{len(ok)} 只")
    ok["f3pct"] = ok["f3"].rank(pct=True, method="average")
    ok["liqpct"] = ok["amt20"].rank(pct=True, method="average")
    cand = ok[(ok["f3pct"] <= THRESH) & (ok["liqpct"] >= LIQ_TOP)].copy()
    print(f"  F3 分位 ≤70% 且 流动性前 20%：{len(cand)} 只")
    cand = cand.sort_values("amt20", ascending=False)
    cand["每手金额"] = (cand["close"] * 100).round(0)
    cand["20日均额(万)"] = (cand["amt20"] / 1e4).round(0)
    cand["F3分位"] = (cand["f3pct"] * 100).round(1)
    cand["流动性分位"] = (cand["liqpct"] * 100).round(1)
    out = cand.head(60)[["code", "last_date", "close", "每手金额", "20日均额(万)", "f3", "F3分位", "流动性分位"]]
    path = os.path.join(OUT, "sp3_current_candidates.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  候选池前 60 只已存 {path}")
    print(f"\n  候选池前 15 只（按 20 日均额降序）：")
    print(f"    {'代码':<12}{'收盘':>9}{'每手金额':>10}{'日均额万':>10}{'F3':>8}{'F3分位':>9}")
    for _, r in out.head(15).iterrows():
        print(f"    {r['code']:<12}{r['close']:>9.2f}{r['每手金额']:>10.0f}"
              f"{r['20日均额(万)']:>10.0f}{r['f3']:>8.3f}{r['F3分位']:>8.1f}%")
    return {"n_scanned": int(len(df)), "n_tradable": int(len(ok)), "n_candidates": int(len(cand)),
            "data_asof": str(df["last_date"].max()), "csv": path}


if __name__ == "__main__":
    check()
    la = part_a()
    ladder = part_liq_ladder()
    pb = part_b()
    res = {"part_a": la, "liq_ladder": ladder, "part_b": pb}
    os.makedirs(OUT, exist_ok=True)
    json.dump(res, open(os.path.join(OUT, "sp3_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nsaved out/sp3_result.json")
