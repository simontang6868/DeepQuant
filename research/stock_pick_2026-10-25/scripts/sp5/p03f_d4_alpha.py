# -*- coding: utf-8 -*-
"""SP5-D4 超额收益分解：d4 相对「同月 d4 合格池等权」的 alpha。

预登记 `SP5_D4_ALPHA_PREREG_2026-11-22.md`（sha256 `a1801ece1ad7…`，跑前冻结）。
本脚本只做一件事：把 `p03d.build_decisions` 的月度组合收益减去同池等权基准，
报告全样本与现代段的 mean / NW(3) t / MDE，两档成本（回测 0.102% ↔ 实盘 0.302% 往返）。

⚠️ 口径边界（预登记 §6，引用时必须带）：
  * 组合收益取自 `build_decisions` 的月度 `fwd20`，**不含撮合约束**（T0 口径）
    ⇒ 衡量"选股 alpha"，不是"账户收益"；
  * 该序列仍有幸存者偏差（持仓若从当月面板消失，收益按其余股票平均）；
  * 样本内，无前瞻证据。

用法：
    python p03f_d4_alpha.py
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402  NW(3) 走项目标准实现，不另写

PREREG = RD / "SP5_D4_ALPHA_PREREG_2026-11-22.md"
PREREG_SHA = "a1801ece1ad7bfd0e8b7ced5275f55ce3a7ff5fe195d4402640da34dd7d3ad76"
SHA12 = PREREG_SHA[:12]
OUT = RD / "d4_alpha_out" / SHA12

SIDE_COST_BACKTEST = p03d.SIDE_COST      # 0.000102（往返 0.102% / K=5）
SIDE_COST_LIVE = 0.000302                # 往返 0.302% / K=5（5 元佣金下限 + 4,000 元/笔）
MDE_K = 2.8016
MODERN_FROM = "20160101"


def verify() -> None:
    got = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if got != PREREG_SHA:
        raise SystemExit(f"[abort] 预登记哈希不符：期望 {PREREG_SHA[:12]} 实际 {got[:12]}")


def stats(x: np.ndarray, months: list[str], lo: str | None = None) -> dict:
    if lo:
        x = np.asarray([v for v, m in zip(x, months) if m >= lo], float)
        n_ms = sum(1 for m in months if m >= lo)
    else:
        n_ms = len(months)
    r = C.newey_west(list(map(float, x)), C.NW_LAG)
    se = float(r["se_nw"])
    return {"n_months": n_ms, "mean_pp": float(r["mean"]) * 100.0,
            "se_nw_pp": se * 100.0, "t_nw": float(r["t_nw"]),
            "mde_pp": MDE_K * se * 100.0,
            "p_nw": float(r["p"]),
            "variance_fell_back_to_g0": bool(r.get("variance_fell_back_to_g0", False))}


def main() -> int:
    verify()
    os.makedirs(OUT, exist_ok=True)
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, pool_counts = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)
    print(f"[data] 面板 {len(panel):,} 行｜月份 {len(months)}｜路径 {len(paths)}")

    # 逐路径：还原毛收益 → 两种成本
    ex0, ex1, perpath = [], [], []
    for p, rets in enumerate(monthly):
        r = np.asarray(rets, float)
        ns = np.asarray(ns_list[p], float)
        gross = r + ns * 2.0 * SIDE_COST_BACKTEST          # 加回回测成本项
        net0 = gross - ns * 2.0 * SIDE_COST_BACKTEST       # = r（自校验）
        net1 = gross - ns * 2.0 * SIDE_COST_LIVE
        if not np.allclose(net0, r, atol=1e-12):
            raise SystemExit("[abort] 成本还原自校验失败：net0 != rets")
        ex0.append(net0 - bench)
        ex1.append(net1 - bench)
        perpath.append(float(np.mean(net1 - bench)))

    ex0 = np.vstack(ex0)
    ex1 = np.vstack(ex1)
    exp0, exp1 = ex0.mean(axis=0), ex1.mean(axis=0)        # 期望组合（路径平均）

    res = {"experiment": "sp5-d4-alpha-decomp-v1", "prereg_sha256": PREREG_SHA,
           "design": {"k": p03d.K, "thresh": p03d.THRESH, "liq_max": p03d.LIQ_MAX,
                      "seed": p03d.SEED, "n_paths": len(paths), "n_months": len(months),
                      "bench": "同月 d4 合格池 fwd20 等权均值",
                      "cost_roundtrip": {"C0_backtest": 0.00102, "C1_live_10w": 0.00302},
                      "mde_k": MDE_K, "modern_from": MODERN_FROM},
           "path_distribution_C1": {"min": float(np.min(perpath)),
                                    "p05": float(np.percentile(perpath, 5)),
                                    "median": float(np.median(perpath)),
                                    "p95": float(np.percentile(perpath, 95)),
                                    "max": float(np.max(perpath)),
                                    "mean": float(np.mean(perpath)),
                                    "share_mean_positive": float(np.mean(np.asarray(perpath) > 0))},
           "cases": {}}

    label = {"C0_backtest": exp0, "C1_live_10w": exp1}
    for cname, series in label.items():
        for seg, lo in (("full", None), ("modern_2016", MODERN_FROM)):
            res["cases"][f"{cname}|{seg}"] = stats(series, months, lo)

    # 单路径 t≥1.96 占比（描述"单账户体验"，不是显著性检验）
    n_sig = 0
    for p in range(ex1.shape[0]):
        st = C.newey_west(list(map(float, ex1[p])), C.NW_LAG)
        if float(st["t_nw"]) >= 1.96:
            n_sig += 1
    res["path_distribution_C1"]["share_t_ge_1.96"] = n_sig / ex1.shape[0]

    main_case = res["cases"]["C1_live_10w|modern_2016"]
    if (main_case["mean_pp"] > 0 and main_case["t_nw"] >= 1.96
            and main_case["mde_pp"] <= abs(main_case["mean_pp"])):
        verdict = "D4_ALPHA_ESTABLISHED"
    elif main_case["mean_pp"] < 0 and main_case["t_nw"] <= -1.96:
        verdict = "D4_ALPHA_NEGATIVE"
    else:
        verdict = "D4_ALPHA_NOT_ESTABLISHED"
    res["verdict"] = verdict
    res["verdict_basis"] = "C1_live_10w|modern_2016（预登记 §4）"

    (OUT / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8", newline="\n")
    pd.DataFrame({"month": months, "excess_C0": exp0, "excess_C1": exp1,
                  "bench": bench}).to_csv(OUT / "monthly_excess.csv", index=False,
                                          encoding="utf-8-sig")

    print("\n[超额收益] 单位 pp/月；基准=同池等权；MDE=2.8016×se")
    for k, v in res["cases"].items():
        print(f"  {k:<24} n={v['n_months']:>3}  {v['mean_pp']:+.4f}  "
              f"t={v['t_nw']:+.2f}  se={v['se_nw_pp']:.4f}  MDE={v['mde_pp']:.4f}")
    d = res["path_distribution_C1"]
    print(f"\n[单路径分布 C1] 5%={d['p05']*100:+.3f}pp 中位={d['median']*100:+.3f}pp "
          f"95%={d['p95']*100:+.3f}pp｜t≥1.96 占比 {d['share_t_ge_1.96']*100:.1f}%")
    print(f"\n[verdict] {verdict}\n[out] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
