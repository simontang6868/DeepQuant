# -*- coding: utf-8 -*-
"""审计用：重跑 SP-4 模拟并把逐月收益/持仓落盘。

**本脚本不修改任何原有文件**，逻辑与 `scripts/sp4_lowliq.py` 严格一致，
唯一区别是额外保存：
  audit_out/sp4_monthly.npz     逐月收益矩阵 (200 x 257) x {c0,c1,c2} + 换手/换股次数
  audit_out/sp4_months.json     月份列表、band_fwd、pool 大小
  audit_out/sp4_holdings_p*.json 前 N 条路径的逐月持仓（供日频重建）

用途：为 `audit_sp4_position.py` / `audit_sp4_daily.py` 提供独立复算的输入，
避免审计脚本调用被审计的函数。

运行：
  python scripts/audit/audit_sp4_dump.py
"""
import os, sys, json, hashlib
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
AOUT = os.path.join(RD, "audit_out")

K, THRESH, LIQ_MAX = 5, 0.70, 0.20
N_PATHS, SEED = 200, 20261026
COST = 0.000204
N_DUMP_PATHS = 12          # 落盘持仓的路径数（够日频重建抽样用）

def check_prereg():
    want = open(os.path.join(RD, "sp4_prereg.sha256"), encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD, "SP4_预登记.md"), "rb").read()).hexdigest()
    print(f"[prereg] {want[:16]}... {'OK' if want == got else 'MISMATCH'}")
    assert want == got, "预登记哈希不一致，停止"
    return want

def annualize_monthly(r):
    """原引擎口径：几何年化，按 12/len 次幂。此处仅用于复现校验。"""
    eq = np.cumprod(1 + np.asarray(r, float))
    return eq[-1] ** (12.0 / len(r)) - 1.0

def main():
    sha = check_prereg()
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
        f3p = g["f3pct"].to_numpy(float); lq = g["liqpct"].to_numpy(float)
        by[d] = {"codes": codes,
                 "fwd": g["fwd20"].to_numpy(float),
                 "pct": f3p,
                 "pool": np.where((f3p <= THRESH) & (lq < LIQ_MAX))[0],
                 "cmap": {c: i for i, c in enumerate(codes)},
                 "band_fwd": float(g["fwd20"].to_numpy(float)[lq < LIQ_MAX].mean())
                             if (lq < LIQ_MAX).sum() > 0 else np.nan,
                 "n_band": int((lq < LIQ_MAX).sum()),
                 "n_pool": int(((f3p <= THRESH) & (lq < LIQ_MAX)).sum()),
                 "n_valid": int(len(g))}
    months = sorted(by)
    print(f"[panel] 有效月 {len(months)}（{months[0]} ~ {months[-1]}）")

    # ---- 与原脚本逐行一致的模拟 ----
    def sim(mode, rng, ns_fixed=None, record=False):
        b0 = by[months[0]]
        pool0 = b0["pool"]
        if len(pool0) < K:
            return None, None, None, None
        hold = [b0["codes"][i] for i in rng.choice(pool0, K, replace=False)]
        rets, ns, holds, nvalid = [], [], [], []
        for t, d in enumerate(months):
            b = by[d]; n = 0
            if t > 0 and mode != "hold":
                if mode == "f3":
                    sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                else:
                    k = ns_fixed[t] if ns_fixed is not None else 0
                    csel = [c for c in hold if c in b["cmap"]]
                    sell = list(rng.choice(csel, min(k, len(csel)), replace=False)) if k else []
                n = len(sell)
                if n:
                    held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                    cand = b["pool"][~np.isin(b["pool"], held)] if len(b["pool"]) else np.array([], int)
                    if len(cand) >= n:
                        hold = ([c for c in hold if c not in sell]
                                + [b["codes"][i] for i in rng.choice(cand, n, replace=False)])
                    else:
                        n = 0
            vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
            nvalid.append(len(vals))
            rets.append((float(np.mean(vals)) if vals else 0.0) - n * COST)
            ns.append(n)
            if record:
                holds.append(list(hold))
        return rets, ns, (holds if record else None), nvalid

    R = {"c0": np.zeros((N_PATHS, len(months))),
         "c1": np.zeros((N_PATHS, len(months))),
         "c2": np.zeros((N_PATHS, len(months)))}
    NS = np.zeros((N_PATHS, len(months)))
    NV = np.zeros((N_PATHS, len(months)), dtype=np.int16)
    ann = {"c0": [], "c1": [], "c2": []}
    holds_dump = {}

    print("[sim] 开始 200 路径 ...")
    for path in range(N_PATHS):
        rng = np.random.default_rng(SEED + path)
        rec = path < N_DUMP_PATHS
        r2, ns2, h2, v2 = sim("f3", rng, None, rec)
        r1, _, h1, _ = sim("random", np.random.default_rng(SEED + path), ns2, rec)
        r0, _, h0, _ = sim("hold", np.random.default_rng(SEED + path), None, rec)
        for key, r in (("c2", r2), ("c1", r1), ("c0", r0)):
            R[key][path] = r
            ann[key].append(annualize_monthly(r))
        NS[path] = ns2
        NV[path] = v2          # c2 路径的有效持股数（缺价被剔除后的实际参与平均的只数）
        if rec:
            holds_dump[path] = {"c0": h0, "c1": h1, "c2": h2}
        if (path + 1) % 50 == 0:
            print(f"  {path+1}/{N_PATHS}  c2 年化中位 {np.median(ann['c2'])*100:.4f}%")

    # ---- 复现校验：必须与 sp4_result.json 对齐 ----
    orig = json.load(open(os.path.join(RD, "out", "sp4_result.json"), encoding="utf-8"))
    got = float(np.median(ann["c2"]))
    print("\n[reproduce] 原 out/sp4_result.json c2.ann = %.10f" % orig["c2"]["ann"])
    print("[reproduce] 本次复算           c2.ann = %.10f" % got)
    diff = abs(got - orig["c2"]["ann"])
    print("[reproduce] 绝对差 = %.3e -> %s" % (diff, "复现成功" if diff < 1e-9 else "!! 未复现"))

    # ---- 落盘 ----
    os.makedirs(AOUT, exist_ok=True)
    np.savez_compressed(os.path.join(AOUT, "sp4_monthly.npz"),
                        months=np.array(months),
                        R_c0=R["c0"], R_c1=R["c1"], R_c2=R["c2"],
                        NS=NS, NV=NV,
                        ann_c0=np.array(ann["c0"]), ann_c1=np.array(ann["c1"]),
                        ann_c2=np.array(ann["c2"]))
    meta = {"prereg_sha256": sha, "seed": SEED, "n_paths": N_PATHS,
            "months": months, "K": K, "THRESH": THRESH, "LIQ_MAX": LIQ_MAX, "COST": COST,
            "band_fwd": [by[d]["band_fwd"] for d in months],
            "n_band": [by[d]["n_band"] for d in months],
            "n_pool": [by[d]["n_pool"] for d in months],
            "n_valid": [by[d]["n_valid"] for d in months],
            "reproduce": {"orig_c2_ann": orig["c2"]["ann"], "got_c2_ann": got, "abs_diff": diff}}
    with open(os.path.join(AOUT, "sp4_months.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    for path, h in holds_dump.items():
        with open(os.path.join(AOUT, f"sp4_holdings_p{path:03d}.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump({"months": months, **h}, f, ensure_ascii=False)
    print(f"\n[saved] {AOUT}/sp4_monthly.npz  +  sp4_months.json  +  {len(holds_dump)} 份持仓")
    print("[stats] 月换手中位 %.4f｜年成本 %.4f%%" % (
        float(np.median(NS.mean(axis=1) / K)), float(np.median(NS.mean(axis=1) / K)) * K * COST * 12 * 100))
    print("[stats] 有效持股数（c2）中位 %.2f｜<5 的月-路径占比 %.2f%%" % (
        float(np.median(NV)), float((NV < K).mean() * 100)))

if __name__ == "__main__":
    main()
