# -*- coding: utf-8 -*-
"""SP-5 / P1-1：五套成本实现的逐项对齐（只读核算，不改任何代码）。

输出 `reports/SP5_COST_MODEL_ALIGNMENT.csv`：
  每套实现的费率分解、最低佣金处理、建仓是否计往返、不同资金规模下的
  实际单边/往返费率、是否可与其他实现直接比较。

口径来源（全部从源码提取，不引用报告文字）：
  A 回测账本      scripts/sp5/p04_position_dd.py      spread_side=0.0（默认）
  B 前瞻决策估算  scripts/sp5/p13_forward.py:244-245  SPREAD=0.001，建仓计往返
  C 前瞻实际回填  scripts/sp5/p13b_forward_track.py:89-99  SPREAD=0.001，单边
  D V-B 配置驱动  scripts/audit/sp4_forward.py:158    n_swap*0.000204（魔数）
  E 横截面因子    scripts/run_phase_c.py:628 cost_of()  2*换手*费率，无最低佣金/无价差
"""
from __future__ import annotations

import csv
import json
import os

CAPITAL = 100_000.0
WEIGHT = 0.25
K = 5
COMM_RATE, COMM_MIN = 0.00025, 5.0
TRANSFER, STAMP = 1e-05, 0.0005
SPREAD_FWD = 0.001

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "reports")


def eff(notional: float, spread: float, side: str) -> dict:
    comm = max(COMM_MIN, notional * COMM_RATE)
    tr = notional * TRANSFER
    stp = notional * STAMP if side == "sell" else 0.0
    sp = notional * spread * 0.5          # 半边价差
    tot = comm + tr + stp + sp
    return {"comm": comm, "transfer": tr, "stamp": stp, "spread": sp,
            "total": tot, "pct": tot / notional * 100}


def row(**kw):
    return kw


def main():
    per_pos = CAPITAL * WEIGHT / K
    rows = []

    # ---- A 回测（spread=0，最低佣金有处理）----
    b = eff(per_pos, 0.0, "buy"); s = eff(per_pos, 0.0, "sell")
    rows.append(row(impl="A 回测账本", file="scripts/sp5/p04_position_dd.py",
                    spread="0（默认 spread_side=0.0）", min_comm="有 max(5,…)",
                    roundtrip_on_build="否（按实际买卖方向）",
                    buy_pct=round(b["pct"], 4), sell_pct=round(s["pct"], 4),
                    roundtrip_pct=round(b["pct"] + s["pct"], 4),
                    eff_comm_pct=round(b["comm"] / per_pos * 100, 4),
                    comparable="基准口径", note="机制阶段全部数字由此产生"))

    # ---- B 前瞻决策估算（建仓计完整往返）----
    b = eff(per_pos, SPREAD_FWD, "buy"); s = eff(per_pos, SPREAD_FWD, "sell")
    est = b["total"] + s["total"]
    rows.append(row(impl="B 前瞻决策估算", file="scripts/sp5/p13_forward.py:244-245",
                    spread="0.001（S2 档，半边 0.05%）", min_comm="有 max(5,…)",
                    roundtrip_on_build="**是**（建仓即计买+卖）",
                    buy_pct=round(b["pct"], 4), sell_pct=round(s["pct"], 4),
                    roundtrip_pct=round(b["pct"] + s["pct"], 4),
                    eff_comm_pct=round(b["comm"] / per_pos * 100, 4),
                    comparable="偏保守（建仓不卖出却计卖出费）",
                    note=f"台账 est_total=75.5 元 = 5×{est:.2f}"))

    # ---- C 前瞻实际回填（单边）----
    rows.append(row(impl="C 前瞻实际回填", file="scripts/sp5/p13b_forward_track.py:89-99",
                    spread="0.001（半边 0.05%）", min_comm="有 max(5,…)",
                    roundtrip_on_build="否（逐笔按方向）",
                    buy_pct=round(b["pct"], 4), sell_pct=round(s["pct"], 4),
                    roundtrip_pct=round(b["pct"] + s["pct"], 4),
                    eff_comm_pct=round(b["comm"] / per_pos * 100, 4),
                    comparable="可与 B 对照（B 高估约 1 倍）",
                    note="与 A 的差异仅价差 0.10%"))

    # ---- D V-B 配置驱动（魔数）----
    rows.append(row(impl="D V-B 配置驱动", file="scripts/audit/sp4_forward.py:158",
                    spread="含在魔数内（不可分解）", min_comm="**未建模**",
                    roundtrip_on_build="按换手只数×单只费率",
                    buy_pct=round(0.000204 * 100 / 2, 4), sell_pct=round(0.000204 * 100 / 2, 4),
                    roundtrip_pct=0.0204, eff_comm_pct=None,
                    comparable="**不可比**（未处理最低佣金，10 万资金下低估 3~4 倍）",
                    note="0.000204 无推导过程，无法复算"))

    # ---- E 横截面因子 cost_of ----
    rows.append(row(impl="E 横截面因子", file="scripts/run_phase_c.py:628 cost_of()",
                    spread="无", min_comm="**未建模**",
                    roundtrip_on_build="不适用（月度 2×单边换手×费率）",
                    buy_pct=None, sell_pct=None, roundtrip_pct=None, eff_comm_pct=None,
                    comparable="**不可直接用于组合账本**",
                    note="用于因子层净收益，费率由调用方传入"))

    with open(os.path.join(OUT, "SP5_COST_MODEL_ALIGNMENT.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"[saved] SP5_COST_MODEL_ALIGNMENT.csv（{len(rows)} 套实现）")

    # ---- 资金规模敏感性：最低佣金何时不再绑定 ----
    scale = []
    for cap in (100_000, 200_000, 400_000, 1_000_000, 3_000_000, 10_000_000, 100_000_000):
        pp = cap * WEIGHT / K
        b2 = eff(pp, 0.0, "buy"); s2 = eff(pp, 0.0, "sell")
        scale.append({"capital": cap, "per_position": round(pp, 2),
                      "comm_before_min": round(pp * COMM_RATE, 4),
                      "comm_charged": round(b2["comm"], 2),
                      "eff_comm_pct": round(b2["comm"] / pp * 100, 4),
                      "roundtrip_pct_ex_spread": round(b2["pct"] + s2["pct"], 4),
                      "min_comm_binding": bool(pp * COMM_RATE < COMM_MIN)})
    with open(os.path.join(OUT, "SP5_COST_MODEL_SCALE.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(scale[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(scale)
    print("[saved] SP5_COST_MODEL_SCALE.csv")
    for r in scale:
        print(f"  {r['capital']:>12,.0f} 元/档 {r['per_position']:>10,.0f}｜"
              f"佣金 {r['comm_charged']:>7.2f}｜实际费率 {r['eff_comm_pct']:.4f}%｜"
              f"往返 {r['roundtrip_pct_ex_spread']:.4f}%｜最低佣金绑定={r['min_comm_binding']}")
    json.dump({"per_position_at_100k": per_pos,
               "capital_to_escape_min_comm": COMM_MIN / COMM_RATE / (WEIGHT / K)},
              open(os.path.join(OUT, "..", "sp5_out", "p15_cost_scale.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
