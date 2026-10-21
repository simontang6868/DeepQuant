#!/usr/bin/env python
"""阶段 O：费率口径声明与双口径复核（exQ5）。

预登记 `docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md`，冻结 `ac7aa2830ab0…`。

## 为什么做这个
阶段 N 审计发现：**0.3% 是压力测试参数**（为 A 股真实等价值 0.051% 的 5.9 倍），
而原"不可用"结论主要由该假设驱动。本阶段把费率口径**写死并同时报告三套**。

⚠️ **费率是外生事实，不是拟合参数**。同时报告多套费率**不是调参择优**，
而是把结论对外部假设的敏感性显式化。

## 三套口径（跑前写死）
- **BASE**：佣金 0.025% 双边 + 印花税 0.05% **仅卖出** + 过户费 0.001% 双边
- **STRESS**：单边综合 0.30%（对称计两侧）
- **CONSERVATIVE**：BASE + 最低佣金 5 元/笔（按 W 分档）

⚠️ 印花税**仅卖出方**征收 → BASE 的成本公式**不对称**，不许为对称而按双边计。

用法：
    python scripts/run_phase_o_fee_convention.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import NW_LAG, stream_bars  # noqa: E402
from run_phase_i_exq5 import LOT, SEGMENTS, capacity_of, filter_v1  # noqa: E402
from run_phase_i_exq5 import build_records as build_i  # noqa: E402

PREREG_SHA256 = "ac7aa2830ab0c80bb08a4443d348ce32040ba306eb97de2bb32ba6539eda435f"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaseo_out", PREREG_SHA12)

# ── 费率口径（预登记 §2，全部写死） ───────────────────────────────
COMM_RATE = 0.00025        # 佣金，双边
STAMP_RATE = 0.0005        # 印花税，**仅卖出**
TRANSFER_RATE = 0.00001    # 过户费，双边
STRESS_ONE_SIDE = 0.003    # 压力口径：单边综合（对称）
MIN_COMM_YUAN = 5.0        # 最低佣金（元/笔）
CAPITALS = (1.0e7, 3.0e7)
T_CRIT = 1.96
IMPACT_UPPER_PP = 0.000784     # 阶段 J 在 W=3,000 万 的 Amihud 冲击（上界）

FEES = ("BASE", "STRESS", "CONSERVATIVE")


def verify() -> None:
    p = "docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cost_base(to: float, w: float, k: int, min_comm: float = MIN_COMM_YUAN) -> float:
    """BASE：佣金双边 + 印花税**仅卖出** + 过户费双边。**不对称**。"""
    return 2.0 * to * (COMM_RATE + TRANSFER_RATE) * 100.0 + to * STAMP_RATE * 100.0


def cost_conservative(to: float, w: float, k: int,
                      min_comm: float = MIN_COMM_YUAN) -> float:
    """CONSERVATIVE：BASE + 最低佣金（单笔取 max(从价, 下限)）。"""
    if k <= 0:
        return cost_base(to, w, k)
    per_trade = w / k
    n_trades = 2.0 * to * k                       # 每月买卖笔数
    eff = max(per_trade * COMM_RATE, min_comm)    # 单笔实收佣金
    c_comm = n_trades * eff / w * 100.0           # 折成组合的 pp
    return c_comm + 2.0 * to * TRANSFER_RATE * 100.0 + to * STAMP_RATE * 100.0


def cost_stress(to: float, w: float, k: int, **_kw) -> float:
    """STRESS：单边综合费率，对称计两侧。"""
    return 2.0 * to * STRESS_ONE_SIDE * 100.0


COST_FN = {"BASE": cost_base, "STRESS": cost_stress, "CONSERVATIVE": cost_conservative}


def judge(nw_base, segs_base, net_cons) -> dict:
    a = abs(nw_base["t_nw"]) >= T_CRIT
    sign = 1.0 if nw_base["mean_pp"] > 0 else -1.0
    b = all(s.get("enough", True) and (s["net_pp"] > 0) == (sign > 0)
            and abs(s["t_nw"]) >= T_CRIT for s in segs_base.values())
    c = net_cons > 0
    v = ("EXECUTABLE_UNDER_BASE_FEES" if (a and b and c)
         else "MARGINAL_UNDER_BASE_FEES" if (a and c) else
         "NOT_EXECUTABLE_UNDER_ANY_FEE")
    return {"A_stat": bool(a), "B_stable": bool(b), "C_conservative_positive": bool(c),
            "D_disclosure_required": True, "verdict": v}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 O：费率口径声明与双口径复核   预登记 {PREREG_SHA12}")
    print("=" * 78)
    verify()
    print("  预登记哈希校验 ✓")

    recs, meta = build_i()
    print(f"  月份 {len(recs)}   分组独立校验不一致 {meta['split_mismatch_months']}")

    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    print(f"  需扫 EXEC {len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)

    # ── 逐月：V1 持仓、alpha、换手、各 W 的 f_inv 与一手金额
    prev = None
    for r in recs:
        pm = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pm)
        r["keep_v1"] = f1["keep_codes"]
        r["alpha"] = ((float(np.mean([r["fwd"][pm[c]] for c in f1["keep_codes"]]))
                       - r["r_bench"]) if f1["keep_codes"] else float("nan"))
        cur = set(r["keep_v1"])
        r["to"] = (0.0 if prev is None else
                   0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1))
        prev = cur
        lots = []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
        r["lots"], r["k"] = lots, len(r["keep_v1"])

    valid = [r for r in recs if np.isfinite(r["alpha"]) and r["lots"]]
    print(f"  有效月份 {len(valid)}")

    # 复现闸门：V1 alpha 必须逐月复现阶段 I
    ref = {}
    p = "phasei_out/8f5c35121fdb/monthly.csv"
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["alpha_v1_pp"]:
                    ref[row["signal_date"]] = float(row["alpha_v1_pp"])
        diffs = [abs(r["alpha"] * 100 - ref[r["signal_date"]])
                 for r in valid if r["signal_date"] in ref]
        mx = max(diffs) if diffs else float("nan")
        print(f"  复现闸门（vs 阶段 I monthly.csv）：{len(diffs)} 月，最大差 {mx:.2e} "
              f"{'✓' if mx < 1e-6 else '✗ 作废'}")
        if not mx < 1e-6:
            raise SystemExit("未复现阶段 I → 后续结论不可信")

    # ── 三口径 × 两资金
    out = {}
    for w in CAPITALS:
        for fee in FEES:
            nets, costs, finvs = [], [], []
            for r in valid:
                cs = capacity_of(r["lots"], w)
                if cs["k"] <= 0:
                    continue
                finv = 1.0 - cs["uninvested"]
                cst = COST_FN[fee](r["to"], w, cs["k"])
                nets.append(finv * r["alpha"] * 100.0 - cst - IMPACT_UPPER_PP)
                costs.append(cst)
                finvs.append(finv)
            nw = C.newey_west(nets, NW_LAG)
            # ⚠️ 单位：`newey_west` 的 `se_nw_pp = se_nw × 100`，**假设入参是小数**。
            #    这里 `nets` 已经是 **pp**，所以必须用 `se_nw`（不是 `se_nw_pp`）。
            #    第一版用了 `se_nw_pp` → CI 宽度放大 100 倍（[−6.02, +6.27]，荒谬）。
            #    这是本项目**第二次**踩同一个坑（第一次在阶段 J 的 mean_pp）。
            #    自检：CI 半宽 ≈ mean / t × 1.96。
            m_net = statistics.mean(nets)
            se_nw = nw["se_nw"]
            half = T_CRIT * se_nw
            sd = statistics.stdev(nets)
            se_iid = sd / math.sqrt(len(nets))
            out[(w, fee)] = {
                "n": len(nets), "finv_mean": round(statistics.mean(finvs), 6),
                "cost_pp": round(statistics.mean(costs), 6),
                "net_pp": round(m_net, 6),
                "se_nw_pp_input": round(se_nw, 6), "se_iid_pp_input": round(se_iid, 6),
                "t_nw": round(nw["t_nw"], 4), "t_iid": round(m_net / se_iid, 4),
                "ci95_nw": [round(m_net - half, 6), round(m_net + half, 6)],
                "ci95_iid": [round(m_net - T_CRIT * se_iid, 6),
                             round(m_net + T_CRIT * se_iid, 6)],
            }

    print()
    print("① 三口径 × 两资金（净值 = f_inv×alpha − 成本 − 冲击上界 0.000784）")
    print("   资金        口径            成本     净值       t(NW)   t(iid)   95%CI(NW)")
    for w in CAPITALS:
        for fee in FEES:
            v = out[(w, fee)]
            print(f"   {w/1e4:>6.0f}万   {fee:<13} {v['cost_pp']:.4f}  "
                  f"{v['net_pp']:+.4f}  {v['t_nw']:+.2f}  {v['t_iid']:+.2f}  "
                  f"[{v['ci95_nw'][0]:+.4f},{v['ci95_nw'][1]:+.4f}]")

    # ── BASE 口径分段（W=3,000 万）
    w_main = 3.0e7
    segs = {}
    for lo, hi, lab in SEGMENTS:
        sub = []
        for r in valid:
            if not (lo <= r["signal_date"] <= hi):
                continue
            cs = capacity_of(r["lots"], w_main)
            if cs["k"] <= 0:
                continue
            sub.append((1 - cs["uninvested"]) * r["alpha"] * 100.0
                       - cost_base(r["to"], w_main, cs["k"]) - IMPACT_UPPER_PP)
        nw = C.newey_west(sub, NW_LAG)
        segs[lab] = {"n": len(sub), "net_pp": round(statistics.mean(sub), 6),
                     "t_nw": round(nw["t_nw"], 4), "enough": len(sub) >= NW_LAG + 3}
        print(f"   分段（BASE, W=3000万）{lab}  n={len(sub):>3}  "
              f"净 {statistics.mean(sub):+.4f}pp  t={nw['t_nw']:+.2f}")

    # ── 判据
    base_main = out[(w_main, "BASE")]
    cons_main = out[(w_main, "CONSERVATIVE")]
    jd = judge({"t_nw": base_main["t_nw"], "mean_pp": base_main["net_pp"]},
               segs, cons_main["net_pp"])
    print()
    print("② 判定（主口径 = BASE, W=3,000 万）")
    print(f"   A 统计 |t_NW|≥1.96      : {base_main['t_nw']:+.2f}  "
          f"→ {'过' if jd['A_stat'] else '不过'}")
    print(f"   B 稳定 两段显著同号      : → {'过' if jd['B_stable'] else '不过'}")
    print(f"   C 保守口径净值 > 0       : {cons_main['net_pp']:+.4f}  "
          f"→ {'过' if jd['C_conservative_positive'] else '不过'}")
    print(f"   D STRESS 强制披露        : 净 {out[(w_main,'STRESS')]['net_pp']:+.4f}pp  "
          f"t={out[(w_main,'STRESS')]['t_nw']:+.2f}（**必须同时报告**）")
    print(f"   >>> {jd['verdict']}")

    # ── 盈亏平衡费率（BASE 结构下，把佣金率当成未知数）
    def net_at_comm(cr):
        vals = []
        for r in valid:
            cs = capacity_of(r["lots"], w_main)
            if cs["k"] <= 0:
                continue
            cst = 2.0 * r["to"] * (cr + TRANSFER_RATE) * 100.0 + r["to"] * STAMP_RATE * 100.0
            vals.append((1 - cs["uninvested"]) * r["alpha"] * 100.0 - cst - IMPACT_UPPER_PP)
        return statistics.mean(vals)
    lo_, hi_ = 0.0, 0.02
    for _ in range(60):
        mid = (lo_ + hi_) / 2
        if net_at_comm(mid) > 0:
            lo_ = mid
        else:
            hi_ = mid
    be_comm = (lo_ + hi_) / 2
    be_sym = (2 * (be_comm + TRANSFER_RATE) + STAMP_RATE) / 2
    print()
    print(f"③ 盈亏平衡（BASE 结构，W=3,000 万，含现金残留与冲击）")
    print(f"   盈亏平衡**佣金** = {be_comm*100:.4f}%（双边）")
    print(f"   折算等价**对称单边综合费率** = {be_sym*100:.4f}%")

    # ── 容量描述
    caps = {w: [capacity_of(r["lots"], w) for r in valid] for w in CAPITALS}
    capd = {f"{w/1e4:.0f}万": {
        "uninvested_median": round(statistics.median([c["uninvested"] for c in cs]), 6),
        "weight_mae_pp_median": round(statistics.median(
            [c["weight_mae_pp"] for c in cs]), 6),
        "covered_median": round(statistics.median([c["covered"] for c in cs]), 6)}
        for w, cs in caps.items()}
    print()
    print("④ 容量（两个指标都报）")
    for k_, v_ in capd.items():
        print(f"   W={k_}: 未投资 {v_['uninvested_median']*100:.2f}%  "
              f"weight_mae {v_['weight_mae_pp_median']:.4f}pp  "
              f"覆盖 {v_['covered_median']*100:.1f}%")

    # ── 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["signal_date", "n_keep", "to", "alpha_pp", "finv_1000w", "finv_3000w",
                     "cost_BASE_pp", "cost_STRESS_pp", "cost_CONS_pp",
                     "net_BASE_1000w", "net_BASE_3000w",
                     "net_STRESS_3000w", "net_CONS_1000w", "net_CONS_3000w"])
        for r in valid:
            c1 = capacity_of(r["lots"], 1.0e7)
            c3 = capacity_of(r["lots"], 3.0e7)
            f1_ = 1 - c1["uninvested"]
            f3_ = 1 - c3["uninvested"]
            a = r["alpha"] * 100.0
            wr.writerow([
                r["signal_date"], r["k"], round(r["to"], 6), round(a, 6),
                round(f1_, 6), round(f3_, 6),
                round(cost_base(r["to"], 3.0e7, c3["k"]), 6),
                round(cost_stress(r["to"], 3.0e7, c3["k"]), 6),
                round(cost_conservative(r["to"], 3.0e7, c3["k"]), 6),
                round(f1_ * a - cost_base(r["to"], 1.0e7, c1["k"]) - IMPACT_UPPER_PP, 6),
                round(f3_ * a - cost_base(r["to"], 3.0e7, c3["k"]) - IMPACT_UPPER_PP, 6),
                round(f3_ * a - cost_stress(r["to"], 3.0e7, c3["k"]) - IMPACT_UPPER_PP, 6),
                round(f1_ * a - cost_conservative(r["to"], 1.0e7, c1["k"]) - IMPACT_UPPER_PP, 6),
                round(f3_ * a - cost_conservative(r["to"], 3.0e7, c3["k"]) - IMPACT_UPPER_PP, 6),
            ])

    summary = {
        "experiment": "phaseo-fee-convention-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "正式预登记实验（费率口径为**外生事实**，多口径同时报告非调参）",
        "frozen_from_phase_I": ["信号 MAX5", "池 as-of全A+252日", "窗口 2007-2026",
                                "构造 排除最高MAX5 20%", "基准 池内等权", "V1 成交约束",
                                "NW(3) 双侧 1.96"],
        "fee_conventions": {
            "BASE": {"commission_bilateral": COMM_RATE,
                     "stamp_duty_sell_only": STAMP_RATE,
                     "transfer_bilateral": TRANSFER_RATE,
                     "equivalent_symmetric_one_side": round(
                         (2 * (COMM_RATE + TRANSFER_RATE) + STAMP_RATE) / 2, 6),
                     "note": "不对称：印花税仅卖出方"},
            "STRESS": {"one_side_symmetric": STRESS_ONE_SIDE},
            "CONSERVATIVE": {"min_commission_yuan_per_trade": MIN_COMM_YUAN},
        },
        "results": {f"W_{w/1e4:.0f}万|{fee}": v for (w, fee), v in out.items()},
        "segments_BASE_3000w": segs,
        "criteria": jd,
        "verdict": jd["verdict"],
        "breakeven": {"commission_bilateral_pct": round(be_comm * 100, 4),
                      "equivalent_symmetric_one_side_pct": round(be_sym * 100, 4)},
        "capacity": capd,
        "reproduction_gate": "V1 alpha 逐月复现阶段 I（max|diff| < 1e-6）",
        "honest_limits": [
            "BASE 的 0.025% 佣金是**情景值**，非所有账户可得的报价",
            "样本内，无前瞻样本外验证",
            "A∧B∧C 通过**不等于**可交易（纪律 7：显著 ≠ 可交易）",
            "STRESS 口径下仍不显著，必须同时披露",
        ],
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    files = ["monthly.csv", "summary.json"]
    man = {"experiment": "phaseo-fee-convention-v1", "prereg_sha256": PREREG_SHA256,
           "script_sha256": _sha256_file(os.path.abspath(__file__)),
           "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                         "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
           "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {OUT_DIR}/  脚本 {man['script_sha256'][:12]}  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
