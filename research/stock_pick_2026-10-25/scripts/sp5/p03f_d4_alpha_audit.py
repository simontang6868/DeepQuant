# -*- coding: utf-8 -*-
"""p03f 的**审计层**（看到结果之后才写，不参与预登记判定）。

回答外部专家第 4 轮提出的六个口径问题，每个问题对应本脚本的一节：

  1. `ok` 的维度：一维 `(n_ME,)` 按股票各自计算（代码形状，本脚本打印证据行）
  2. `readable` 的 +6.19pp 是**绝对**还是**超额**；是 (期初,期末) 还是 (期初,最高)
  3. 缺失的**唯一槽位**口径（89,619）与路径-槽位口径（257,000）的分母差
  4. `readable` 的现代段（2016+）收益：符号与全样本是否一致
  5. F 档对 47,318 的处理是"真实价"还是"回退"（用 A/F 差反推）
  6. 660 个月的方向：实测 g 序列 lag-1 自相关决定 NW 后 t 是升还是降

⚠️ 只读既有产物 + 重算，不改任何既有文件（只写 `audit.json`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import fill_engine as fe  # noqa: E402
import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402

OUT = RD / "d4_alpha_out" / "a1801ece1ad7"
MODERN = "20160101"


def bars_of(code: str, cache: dict):
    if code not in cache:
        try:
            cache[code] = fe.load_bars(code)
        except Exception:  # noqa: BLE001
            cache[code] = None
    return cache[code]


def fwd_own(code: str, me: str, cache: dict) -> tuple[float, str, float]:
    """返回 (r, kind, stale_days)。与 build_panel 同定义：close[pos+21]/close[pos+1]-1。"""
    b = bars_of(code, cache)
    if b is None or len(b.d) == 0:
        return float("nan"), "nobar", float("nan")
    pos = int(np.searchsorted(b.d, me, side="right")) - 1
    if pos < 0:
        return float("nan"), "nobar", float("nan")
    stale = float((pd.Timestamp(me) - pd.Timestamp(str(b.d[pos]))).days)
    buy, sell = pos + 1, pos + 21
    if buy >= len(b.d):
        return float("nan"), "nobar", stale
    if sell < len(b.d):
        return float(b.qc[sell] / b.qc[buy] - 1.0), "readable", stale
    if str(b.d[-1]) > me:
        return float(b.qc[-1] / b.qc[buy] - 1.0), "suspend_resume", stale
    return float(b.qc[-1] / b.qc[buy] - 1.0), "ended", stale


def main() -> int:
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, _ = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)
    cache: dict = {}

    # 面板行索引（避免 1.5 万次全表扫描）
    key2row = {(r.code, r.date): r for r in panel.itertuples(index=False)}

    # ---- 1/2/3. 唯一槽位口径 + 缺失原因 ----
    uniq_hold: set = set()
    path_slots = 0
    miss_uniq: dict = {}
    for p, holds in enumerate(paths):
        for t, d in enumerate(months):
            cm = by[d]["cmap"]
            for c in holds[t]:
                uniq_hold.add((c, d))
                path_slots += 1
                if c in cm:
                    continue
                if (c, d) in miss_uniq:
                    continue
                r, kind, stale = fwd_own(c, d, cache)
                row = key2row.get((c, d))
                if row is None:
                    reason = "panel_no_row"
                elif not np.isfinite(row.close) or row.close < 1.0:
                    reason = "close_lt_1"
                elif row.close > 200.0:
                    reason = "close_gt_200"
                elif not np.isfinite(row.amt20) or row.amt20 <= 5e6:
                    reason = "amt_le_5e6"
                elif not (np.isfinite(row.f2) and np.isfinite(row.vol20)):
                    reason = "factor_nan"
                else:
                    reason = "other"
                miss_uniq[(c, d)] = {
                    "kind": kind, "reason": reason, "stale": stale,
                    "r": r, "bench": float(bench[t]),
                    "exc": (r - float(bench[t])) if r == r else float("nan"),
                }

    reasons: dict = {}
    for v in miss_uniq.values():
        reasons[v["reason"]] = reasons.get(v["reason"], 0) + 1

    # ---- 2/4. readable 的绝对 vs 超额，全样本 vs 现代段 ----
    rd = [(me, v) for (c, me), v in miss_uniq.items() if v["kind"] == "readable"]
    rd_full_r = np.asarray([v["r"] for _, v in rd], float)
    rd_full_e = np.asarray([v["exc"] for _, v in rd], float)
    rd_mod = [(me, v) for me, v in rd if me >= MODERN]
    rd_mod_r = np.asarray([v["r"] for _, v in rd_mod], float)
    rd_mod_e = np.asarray([v["exc"] for _, v in rd_mod], float)

    by_year = {}
    for y in sorted({me[:4] for me, _ in rd_mod}):
        s = np.asarray([v["exc"] for me, v in rd_mod if me[:4] == y], float)
        by_year[y] = {"n": int(len(s)), "exc_mean_pp": float(np.mean(s)) * 100,
                      "exc_median_pp": float(np.median(s)) * 100}

    stale_arr = np.asarray([v["stale"] for _, v in rd], float)
    stale_le15 = float(np.nanmean(stale_arr <= 15)) if len(stale_arr) else float("nan")

    # ---- 6. 自相关 + 观察期（**必须用超额序列**，不是绝对收益） ----
    exc_rows = [np.asarray(rets, float) - bench for rets in monthly]
    E = np.vstack(exc_rows)                      # 每条路径、每月的净超额
    e_series = E.mean(axis=0)                    # 路径平均的净超额序列
    sel = np.asarray([m >= MODERN for m in months])
    es = e_series[sel]
    rho1 = float(np.corrcoef(es[:-1], es[1:])[0, 1])
    iid_t = float(np.mean(es) / (np.std(es, ddof=1) / np.sqrt(len(es))))
    nw = C.newey_west(list(map(float, es)), C.NW_LAG)
    se_ratio = float(nw["se_nw"] * np.sqrt(len(es)) / np.std(es, ddof=1))

    # ---- 波动与观察期（σ 用超额；μ 同时给 C0/C1 两个成本口径） ----
    vol_path = float(np.median([np.std(row[sel], ddof=1) for row in E]))
    vol_expect = float(np.std(e_series[sel], ddof=1))

    # C1 = 实盘成本（0.302% 往返）在 C0 之上再加 (0.302-0.102)% × 换手率
    #   ⚠️ SIDE_COST=0.000102 是"已除以 K 的单边费率"（0.051%/5），
    #      所以额外成本 = 0.2% × (ns/K) = ns × 0.0004，**不是** ns × 0.002（差 5 倍）
    c1_rows = [np.asarray(rets, float) - 0.0004 * np.asarray(ns_list[p], float) - bench
               for p, rets in enumerate(monthly)]
    e1_series = np.vstack(c1_rows).mean(axis=0)
    mu_c0 = float(np.mean(es))
    mu_c1 = float(np.mean(e1_series[sel]))

    obs = {}
    for tag, mu in (("C0_回测成本", mu_c0), ("C1_实盘成本", mu_c1)):
        mu20 = mu * 0.20
        obs[tag] = {
            "mu_100pct_pp": mu * 100, "mu_20pct_pp": mu20 * 100,
            "n_single_path_t2": (2 * vol_path * 0.20 / mu20) ** 2,
            "n_expected_path_t2": (2 * vol_expect * 0.20 / mu20) ** 2,
            "n_single_path_mde": (2.8016 * vol_path * 0.20 / mu20) ** 2,
        }

    # ---- 5. A vs F 的现代段差（反推 F 是否用了真实价） ----
    rob_path = OUT / "robustness.json"
    af = {}
    if rob_path.exists():
        rob = json.loads(rob_path.read_text(encoding="utf-8"))
        for k in ("A_baseline_drop_and_reweight", "D_gone_gets_bench",
                  "F_gone_actual_from_bars"):
            af[k] = {seg: rob[k][seg]["mean_pp"] for seg in ("full", "modern_2016")}
        af["F_minus_A_full_pp"] = af["F_gone_actual_from_bars"]["full"] - af["A_baseline_drop_and_reweight"]["full"]
        af["F_minus_A_modern_pp"] = af["F_gone_actual_from_bars"]["modern_2016"] - af["A_baseline_drop_and_reweight"]["modern_2016"]
        af["F_minus_D_modern_pp"] = af["F_gone_actual_from_bars"]["modern_2016"] - af["D_gone_gets_bench"]["modern_2016"]

    res = {
        "schema": "p03f_d4_alpha_audit/v1",
        "prereg_sha256": "a1801ece1ad7",
        "note": "审计层：回答专家第 4 轮的六个口径问题；不参与预登记判定，不改任何既有产物",
        "ok_dimension": {
            "source": "research/stock_pick_2026-10-25/scripts/build_panel.py:152-169",
            "shape": "(n_ME,)", "per_stock": True,
            "broadcast_to_other_stocks": False,
            "evidence": "ok = pos >= W - 1; ok &= (stale <= MAX_STALE); ok &= (sell < len(dates))，"
                        "其中 ME_arr 是广播常量、dates 是标量长度 ⇒ 一维；每只股票各 append 一段 DataFrame",
            "implication": "偏差是股票级（该股末 1~2 月），不会因一只股退市而删掉整个 ME",
        },
        "slot_denominators": {
            "path_slots": path_slots,
            "unique_hold_slots": len(uniq_hold),
            "compression": path_slots / len(uniq_hold),
            "unique_missing": len(miss_uniq),
            "unique_missing_rate": len(miss_uniq) / len(uniq_hold),
            "path_slot_missing": 51605,
            "path_slot_missing_rate": 51605 / 257000,
        },
        "missing_reasons_unique": reasons,
        "readable": {
            "n_full": int(len(rd_full_r)),
            "abs_mean_pp_full": float(np.mean(rd_full_r)) * 100,
            "exc_mean_pp_full": float(np.mean(rd_full_e)) * 100,
            "exc_median_pp_full": float(np.median(rd_full_e)) * 100,
            "n_modern": int(len(rd_mod_r)),
            "abs_mean_pp_modern": float(np.mean(rd_mod_r)) * 100,
            "exc_mean_pp_modern": float(np.mean(rd_mod_e)) * 100,
            "exc_median_pp_modern": float(np.median(rd_mod_e)) * 100,
            "bench_mean_pp": float(bench.mean()) * 100,
            "stale_le15_share": stale_le15,
            "by_year_modern": by_year,
        },
        "autocorr": {
            "n_months_modern": int(len(es)),
            "lag1_rho": rho1, "t_iid": iid_t, "t_nw3": float(nw["t_nw"]),
            "se_nw_over_iid": se_ratio,
            "verdict": "正自相关 ⇒ NW 后 t 更低 ⇒ 660 是下界" if se_ratio > 1 else "负自相关 ⇒ NW 后 t 更高",
        },
        "observation_months": {
            "note": "σ 为超额（相对池等权）口径；绝对收益口径见 diag.json 的 vol（单路径 11.26%/月）",
            "vol_100pct_single_path": vol_path,
            "vol_100pct_expected_path": vol_expect,
            "vol_20pct_single_path": vol_path * 0.20,
            "vol_20pct_expected_path": vol_expect * 0.20,
            "by_cost": obs,
        },
        "A_F_diff": af,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"[口径] 路径-槽位 {path_slots:,}｜唯一持仓 {len(uniq_hold):,}"
          f"（压缩 {path_slots/len(uniq_hold):.2f}×）｜唯一缺失 {len(miss_uniq):,}"
          f" = {len(miss_uniq)/len(uniq_hold)*100:.2f}%")
    print(f"[原因] {reasons}")
    print(f"[readable 全样本] n={len(rd_full_r):,} 绝对 {np.mean(rd_full_r)*100:+.3f}pp"
          f" 超额 {np.mean(rd_full_e)*100:+.3f}pp 中位 {np.median(rd_full_e)*100:+.3f}pp")
    print(f"[readable 现代段] n={len(rd_mod_r):,} 绝对 {np.mean(rd_mod_r)*100:+.3f}pp"
          f" 超额 {np.mean(rd_mod_e)*100:+.3f}pp 中位 {np.median(rd_mod_e)*100:+.3f}pp")
    print(f"[stale] 可读缺失中 stale<=15 占 {stale_le15*100:.2f}%")
    print(f"[自相关] lag1 {rho1:+.4f}｜iid t {iid_t:+.3f}｜NW3 t {nw['t_nw']:+.3f}"
          f"｜se比 {se_ratio:.4f} ⇒ {'660 是下界' if se_ratio > 1 else '660 是上界'}")
    print(f"[观察期] σ100(超额) 单路径 {vol_path*100:.2f}%/月｜期望 {vol_expect*100:.2f}%/月")
    for tag, v in obs.items():
        print(f"  {tag}: mu={v['mu_20pct_pp']:.4f}pp/月 ⇒ t=2 需 {v['n_single_path_t2']:.0f} 月"
              f"（期望路径 {v['n_expected_path_t2']:.0f}）｜MDE={v['n_single_path_mde']:.0f} 月")
    if af:
        print(f"[A/F] F-A 全样本 {af['F_minus_A_full_pp']:+.4f}pp｜现代段 {af['F_minus_A_modern_pp']:+.4f}pp"
              f"｜F-D 现代段 {af['F_minus_D_modern_pp']:+.4f}pp")
    print(f"\n→ {OUT / 'audit.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
