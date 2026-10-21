#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""券商实际费率测算（东方财富 / 中原证券 / 量化口径）

⚠️ 性质：**情景分析**，不是新实验。策略、样本、组合构造全部沿用已冻结产物。
   费率来自公开检索（2026-10-21），**费率会变、且因账户/渠道/资金量而异**，
   故每条都标注来源与可信度，不得当作确定的报价。

数据源（只读）：
  - `phaseo_out/ac7aa2830ab0/monthly.csv`（阶段 O 冻结产物，含 to / alpha_pp / finv / n_keep）
  - `phasej_out/f76de8a277f6/summary.json`（各资金档的 Amihud 冲击）
  - `phasel_out/9ffc3710cf6f/`（SURGE 的 alpha 与换手）
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402

NW_LAG = 3
T_CRIT = 1.96
Z_POWER80 = 2.8016

# ── 国家规费（不由券商决定，多家来源一致） ─────────────────────────
STAMP_RATE = 0.0005      # 印花税 万5，**仅卖出**（2023-08-28 起由千1降至万5）
TRANSFER_RATE = 0.00001  # 过户费 万0.1，沪深**双边**（2022-04-29 起沪深均收）
REG_FEE = 0.0000541      # 交易所规费（经手费+证管费）约万0.541 —— 全佣已含，净佣需另加

# ── 费率情景（全部标注来源与可信度） ──────────────────────────────
# comm_rate 一律为**单边全佣口径**（含规费）；min_comm=0 表示无最低（合规存疑）
SCENARIOS = [
    {"id": "EM_DEFAULT", "name": "东方财富 官方默认", "comm_rate": 0.00025,
     "min_comm": 5.0,
     "source": "东方财富证券《佣金收费协议书(基础品种篇)》官方PDF（doc.18.cn，2023-02-24）："
               "股票佣金 0.25‰，含代收监管费与交易所经手费；A股每笔不足5元按5元收",
     "confidence": "高（券商官方文件）",
     "caveat": "协议第五条：券商有权调整费率，通知后5个交易日未异议视为接受"},
    {"id": "ZY_DEFAULT", "name": "中原证券 默认", "comm_rate": 0.0003,
     "min_comm": 5.0,
     "source": "多家理财问答一致称默认万3、最低5元（未找到官方原文）",
     "confidence": "中（第三方口径，非官方文件）",
     "caveat": "未注明是全佣还是净佣；若为净佣需另加规费万0.541"},
    {"id": "QMT_WAN1", "name": "量化协商 万1（含5元下限）", "comm_rate": 0.0001,
     "min_comm": 5.0,
     "source": "客户经理/渠道口径：量化客户常见万1~万1.5，需资金量与频率协商",
     "confidence": "低（营销/导流渠道口径）",
     "caveat": "通常需 QMT/Ptrade 权限（门槛 10~50 万）"},
    {"id": "QMT_WAN1_NO5", "name": "量化协商 万1 + 免五", "comm_rate": 0.0001,
     "min_comm": 0.0,
     "source": "部分渠道宣称可对量化客户豁免5元下限",
     "confidence": "低 且 ⚠️ 合规存疑",
     "caveat": "⚠️ 证监会规定单笔最低5元，多个来源称『免五』属违规宣传 —— "
               "**本情景仅作上界参考，不得假定可获得**"},
    {"id": "QMT_WAN08_NO5", "name": "极优 万0.8 + 免五", "comm_rate": 0.00008,
     "min_comm": 0.0,
     "source": "渠道宣称最低可至万0.8~万0.741，多需 50~500 万门槛",
     "confidence": "低 且 ⚠️ 合规存疑",
     "caveat": "⚠️ 同上；且高门槛对应更高资金要求"},
    {"id": "NETRATE_TRAP", "name": "净佣陷阱：报万0.85 但另收规费",
     "comm_rate": 0.00085 + REG_FEE, "min_comm": 5.0,
     "source": "多个来源警示：报『净佣』不含规费，需另加万0.541",
     "confidence": "中（用于对照，非报价）",
     "caveat": "表面万0.85，实际约万0.904 —— 询价必须问清『全佣还是净佣』"},
    {"id": "STRESS_03", "name": "阶段 O 压力口径 0.3%（对照）", "comm_rate": 0.003,
     "min_comm": 0.0,
     "source": "本项目阶段 O 预登记写死的压力参数",
     "confidence": "—（对照用）",
     "caveat": "对称单边口径，不区分印花税方向；仅作对照"},
]

# 资金档（元）
CAPITALS = (1.0e7, 3.0e7, 5.0e7, 1.0e8)
# 各档的 Amihud 冲击（来自阶段 J；未覆盖的档用 3000 万值并标注）
IMPACT_BY_CAP = {1.0e7: 0.00027359, 3.0e7: 0.00078376}


def cost_month(to: float, w: float, k: int, comm_rate: float,
               min_comm: float) -> float:
    """月度成本（pp）。`to` 为**单边**换手（占组合市值比例）。

    成本 = 佣金(双边) + 印花税(仅卖出) + 过户费(双边)
    佣金每笔 = max(单笔金额 × 费率, 最低收费)
    """
    if k <= 0 or to <= 0:
        return 0.0
    per_trade = w / k
    n_trades = 2.0 * to * k
    per_comm = max(per_trade * comm_rate, min_comm) if min_comm else per_trade * comm_rate
    c_comm = n_trades * per_comm / w * 100.0
    c_stamp = to * STAMP_RATE * 100.0
    c_transfer = 2.0 * to * TRANSFER_RATE * 100.0
    return c_comm + c_stamp + c_transfer


def net_stats(net: list[float]) -> dict:
    n = len(net)
    m = statistics.fmean(net)
    se_iid = statistics.stdev(net) / math.sqrt(n)
    nw = C.newey_west(net, NW_LAG)          # ⚠️ net 已是 pp，用 se_nw 不是 se_nw_pp
    return {"n": n, "net_pp": round(m, 6),
            "t_iid": round(m / se_iid, 4), "t_nw": round(nw["t_nw"], 4),
            "p_nw": round(nw["p"], 6),
            "ci95_pp": [round(m - T_CRIT * nw["se_nw"], 6),
                        round(m + T_CRIT * nw["se_nw"], 6)]}


def breakeven_rate(tos, alphas, finvs, ks, w, impact, use_min: bool) -> float | None:
    """数值求解盈亏平衡佣金费率（令 mean(net)=0）。返回 None 表示无解。"""
    def f(rate):
        mc = 5.0 if use_min else 0.0
        net = [fi * a - cost_month(t, w, k, rate, mc) - impact
               for t, a, fi, k in zip(tos, alphas, finvs, ks)]
        return statistics.fmean(net)
    lo, hi = 0.0, 0.02
    if f(lo) <= 0:          # 即使零费率也为负 → 无解
        return None
    if f(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main() -> int:
    t0 = time.time()
    os.makedirs("reports", exist_ok=True)

    # ── 读取 exQ5（阶段 O 冻结产物）
    p_o = "phaseo_out/ac7aa2830ab0/monthly.csv"
    rows = list(csv.DictReader(open(p_o, encoding="utf-8")))
    o_to = [float(r["to"]) for r in rows]
    o_alpha = [float(r["alpha_pp"]) for r in rows]
    o_nkeep = [int(r["n_keep"]) for r in rows]
    o_finv = {1.0e7: [float(r["finv_1000w"]) for r in rows],
              3.0e7: [float(r["finv_3000w"]) for r in rows]}
    # 5000万/1亿 无冻结 finv → 用 3000 万的（保守，因为现金残留随 W 单调改善）
    o_finv[5.0e7] = o_finv[3.0e7]
    o_finv[1.0e8] = o_finv[3.0e7]

    # ── 读取 SURGE（阶段 L + 阶段 J 冲击）
    p_l = "phasel_out/9ffc3710cf6f/monthly.csv"
    rows_l = list(csv.DictReader(open(p_l, encoding="utf-8")))
    l_alpha = [float(r["alpha_v1_pp"]) for r in rows_l]
    l_nkeep = [int(r["n_keep"]) for r in rows_l]
    l_to_mean = 0.240941                       # 阶段 L 报告的单边换手均值
    l_finv = 1.0 - 0.048099                    # 阶段 L capacity_desc @3000万 未投资中位

    print("=" * 78)
    print("券商实际费率测算（情景分析，非新实验）")
    print("=" * 78)
    print(f"  规费：印花税 {STAMP_RATE*1e4:.1f}‱（仅卖出）＋ 过户费 "
          f"{TRANSFER_RATE*1e4:.2f}‱（双边）")
    print(f"  样本：237 月；exQ5 月均单边换手 {statistics.fmean(o_to)*100:.2f}%，"
          f"SURGE {l_to_mean*100:.2f}%")
    print(f"  持仓：exQ5 中位 {int(statistics.median(o_nkeep))} 只，"
          f"SURGE 中位 {int(statistics.median(l_nkeep))} 只")

    # ── 高频交易认定检查（2025-07-07 实施）
    print()
    print("① 高频交易差异化收费是否适用（沪深北《程序化交易管理实施细则》2025-07-07 施行）")
    print("-" * 78)
    k_med = statistics.median(o_nkeep)
    to_mean = statistics.fmean(o_to)
    orders_month = 2.0 * to_mean * k_med            # 买卖合计
    orders_day = orders_month / 21.0
    print(f"   认定阈值：单账户 每秒申报/撤单 ≥300 笔  或  单日 ≥20,000 笔")
    print(f"   本策略  ：月度委托 {orders_month:,.0f} 笔 → 日均 {orders_day:,.0f} 笔"
          f"（按 21 交易日）")
    print(f"   结论    ：日均 {orders_day:,.0f} 笔 << 20,000 笔阈值 "
          f"（差 {20000/max(orders_day,1):.0f} 倍）→ **不构成高频交易，不适用差异化收费**")
    print("   ⚠️ 注：差异化收费具体方案监管尚未最终落定（征求意见稿为流量费 0.1→1 元/笔、")
    print("      撤单费 5 元/笔），但无论最终标准如何，本策略的委托频率都远在阈值之下。")

    # ── 5 元门槛的资金脱离点
    print()
    print("② 单笔 5 元下限：多大的资金才不再受它约束")
    print("-" * 78)
    print(f"   单笔金额 = W / {int(k_med)} 只；摆脱下限需 W/持仓 × 费率 ≥ 5 元")
    floor_rows = []
    for sc in SCENARIOS:
        if sc["min_comm"] <= 0 or sc["comm_rate"] <= 0:
            continue
        w_star = sc["min_comm"] / sc["comm_rate"] * k_med
        floor_rows.append([sc["id"], f"{sc['comm_rate']*1e4:.3f}‱",
                           f"{w_star/1e4:,.0f} 万"])
        print(f"   {sc['id']:<14} 费率 {sc['comm_rate']*1e4:>6.3f}‱ → "
              f"需 W ≥ {w_star/1e4:>8,.0f} 万 才摆脱 5 元下限")
    print("   ⚠️ 关键：费率越低，摆脱下限所需资金反而**越大**"
          "（万1 需 ~9,700 万，万2.5 只需 ~3,900 万）")

    # ── exQ5 各情景测算
    print()
    print("③ exQ5：各费率情景下的成本后净值")
    print("-" * 78)
    out = {"scenarios": SCENARIOS, "exq5": {}, "surge": {},
           "high_freq_check": {"orders_per_month": round(orders_month, 1),
                               "orders_per_day": round(orders_day, 1),
                               "threshold_per_day": 20000,
                               "is_high_freq": False},
           "min_commission_escape_capital": {r[0]: r[1:] for r in floor_rows}}

    for w in CAPITALS:
        impact = IMPACT_BY_CAP.get(w, IMPACT_BY_CAP[3.0e7])
        imp_note = "" if w in IMPACT_BY_CAP else "（用 3000 万档代值）"
        print(f"\n   ── W = {w/1e4:,.0f} 万   冲击(Amihud) {impact:.6f} pp{imp_note}")
        print(f"      {'情景':<16} {'成本pp':>8} {'净pp':>9} {'t(iid)':>8} "
              f"{'t(NW)':>8} {'95%CI下界':>10} {'显著':>5}")
        cap_res = {}
        for sc in SCENARIOS:
            costs, nets = [], []
            for t, a, fi, k in zip(o_to, o_alpha, o_finv[w], o_nkeep):
                c = cost_month(t, w, k, sc["comm_rate"], sc["min_comm"])
                costs.append(c)
                nets.append(fi * a - c - impact)
            st = net_stats(nets)
            st["cost_pp"] = round(statistics.fmean(costs), 6)
            cap_res[sc["id"]] = st
            sig = "✓" if abs(st["t_nw"]) >= T_CRIT else "✗"
            print(f"      {sc['id']:<16} {st['cost_pp']:>8.4f} {st['net_pp']:>+9.4f} "
                  f"{st['t_iid']:>+8.3f} {st['t_nw']:>+8.3f} {st['ci95_pp'][0]:>+10.4f} "
                  f"{sig:>5}")
        out["exq5"][f"{w/1e4:.0f}万"] = cap_res
        # 盈亏平衡费率
        be_min = breakeven_rate(o_to, o_alpha, o_finv[w], o_nkeep, w, impact, True)
        be_free = breakeven_rate(o_to, o_alpha, o_finv[w], o_nkeep, w, impact, False)
        out["exq5"][f"{w/1e4:.0f}万"]["_breakeven"] = {
            "with_min_commission": be_min, "without_min_commission": be_free}
        print(f"      盈亏平衡佣金费率：含5元下限 "
              f"{'%.4f‱' % (be_min*1e4) if be_min else '无解（零费率仍为负）'}；"
              f"无下限 {'%.4f‱' % (be_free*1e4) if be_free else '无解'}")

    # ── SURGE
    print()
    print("④ SURGE（阶段 L：0.3% 口径下净 −0.0385pp）在各情景下")
    print("-" * 78)
    w_s = 3.0e7
    impact_s = 0.0        # ⚠️ SURGE 的冲击成本从未建模
    print(f"   ⚠️ 冲击成本 = 0（**未建模**，阶段 L 没算过）→ 以下为**上界**")
    print(f"      {'情景':<16} {'成本pp':>8} {'净pp':>9} {'t(iid)':>8} "
          f"{'t(NW)':>8} {'95%CI下界':>10} {'显著':>5}")
    surge_res = {}
    for sc in SCENARIOS:
        nets = []
        for a, k in zip(l_alpha, l_nkeep):
            c = cost_month(l_to_mean, w_s, k, sc["comm_rate"], sc["min_comm"])
            nets.append(l_finv * a - c - impact_s)
        st = net_stats(nets)
        st["cost_pp"] = round(statistics.fmean(
            [cost_month(l_to_mean, w_s, k, sc["comm_rate"], sc["min_comm"])
             for k in l_nkeep]), 6)
        surge_res[sc["id"]] = st
        sig = "✓" if abs(st["t_nw"]) >= T_CRIT else "✗"
        print(f"      {sc['id']:<16} {st['cost_pp']:>8.4f} {st['net_pp']:>+9.4f} "
              f"{st['t_iid']:>+8.3f} {st['t_nw']:>+8.3f} {st['ci95_pp'][0]:>+10.4f} "
              f"{sig:>5}")
    out["surge"] = {"capital": w_s, "f_inv": round(l_finv, 6),
                    "turnover_one_side": l_to_mean,
                    "impact_pp": 0.0,
                    "impact_note": "未建模（阶段 L 未计算），故结果为上界",
                    "scenarios": surge_res}

    # ── 费率谈判的边际价值
    print()
    print("⑤ 费率谈判的边际价值（中原默认万3 → 量化万1，成本能省多少）")
    print("-" * 78)
    print(f"      {'资金':>9} {'万3成本':>9} {'万1成本':>9} {'节省pp':>9} {'净值提升pp':>11}")
    nego = {}
    for w in CAPITALS:
        a = out["exq5"][f"{w/1e4:.0f}万"]["ZY_DEFAULT"]
        b = out["exq5"][f"{w/1e4:.0f}万"]["QMT_WAN1"]
        save = a["cost_pp"] - b["cost_pp"]
        nego[f"{w/1e4:.0f}万"] = {"cost_wan3_pp": a["cost_pp"],
                                  "cost_wan1_pp": b["cost_pp"],
                                  "saving_pp": round(save, 6),
                                  "net_gain_pp": round(b["net_pp"] - a["net_pp"], 6)}
        print(f"      {w/1e4:>8,.0f}万 {a['cost_pp']:>9.4f} {b['cost_pp']:>9.4f} "
              f"{save:>9.4f} {b['net_pp'] - a['net_pp']:>+11.4f}")
    print("   ⚠️ 关键：**资金越小，谈费率越没用。** 1,000 万时节省 **0.0000pp**")
    print("     —— 因为单笔仅约 5,163 元，任何费率下佣金都不足 5 元，全部按下限收。")
    print("     真正的杠杆是**资金规模**（脱离 5 元下限）或**单笔委托金额**，不是费率。")
    out["negotiation_value"] = nego

    # ── 结论
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    em3 = out["exq5"]["3000万"]["EM_DEFAULT"]
    zy3 = out["exq5"]["3000万"]["ZY_DEFAULT"]
    q3 = out["exq5"]["3000万"]["QMT_WAN1"]
    st3 = out["exq5"]["3000万"]["STRESS_03"]
    print(f"  东方财富默认（万2.5全佣+5元）@3000万：净 {em3['net_pp']:+.4f}pp  "
          f"t(NW)={em3['t_nw']:+.3f}  CI下界 {em3['ci95_pp'][0]:+.4f}")
    print(f"  中原证券默认（万3+5元）      @3000万：净 {zy3['net_pp']:+.4f}pp  "
          f"t(NW)={zy3['t_nw']:+.3f}")
    print(f"  量化协商（万1+5元）          @3000万：净 {q3['net_pp']:+.4f}pp  "
          f"t(NW)={q3['t_nw']:+.3f}")
    print(f"  阶段 O 压力口径 0.3%         @3000万：净 {st3['net_pp']:+.4f}pp  "
          f"t(NW)={st3['t_nw']:+.3f}  ← 原判据")
    print()
    print("  → 两家券商的**默认**费率下，exQ5 净值均已显著为正；")
    print("    与 0.3% 压力口径的差别主要来自佣金（0.019~0.039 vs 0.113 pp）。")
    print("  ⚠️ 但**不构成可用确认**：全部样本内、无前瞻验证；")
    print("    且 SURGE 的冲击成本未建模、现金残留仍在。")

    out["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    out["runtime_sec"] = round(time.time() - t0, 1)
    with open("reports/BROKER_FEE_CALCULATION.json", "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print(f"产物：reports/BROKER_FEE_CALCULATION.json  （{out['runtime_sec']}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
