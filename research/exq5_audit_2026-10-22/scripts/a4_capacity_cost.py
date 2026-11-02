#!/usr/bin/env python
"""A4 资金规模、最低佣金与现金残留审计（任务 §六）。

输入：A3 独立重建产物（out/a3_panel/）+ 阶段 O/J 冻结 CSV + 券商费率 JSON。
新增计算（全部标注为审计诊断）：
  E1 扩展资金档（5000万/1亿）净值：两种冲击口径（broker 惯例=3000万常数；√/线性缩放）
  E2 各档订单数与最低佣金影响（CONS−BASE 成本差）
  E3 成本因素清单（是否入模/方法/来源/实证or假设/重复风险）
输出：out/a4_capacity/（capital_table.csv / cost_factors.csv / extended_tiers.csv / summary.json / run.log）
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_common as A  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a4_capacity")
REPO = A.REPO
IMPACT_3000W = 0.000784      # 阶段 O 收取的 Amihud 冲击常数（W=3000万）


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []

    def say(s):
        print(s)
        log.append(s)

    say("A4 资金规模、最低佣金与现金残留审计")

    a3 = json.load(open(os.path.join(A.OUT_DIR, "a3_panel", "summary.json"), encoding="utf-8"))
    po = A.read_csv_rows(A.FROZEN["phaseo_monthly"])
    pj = A.read_csv_rows(A.FROZEN["phasej_monthly"])
    bj = json.load(open(os.path.join(REPO, "reports/BROKER_FEE_CALCULATION.json"), encoding="utf-8"))

    tos = A.col(po, "to")
    alpha_v1 = A.col(po, "alpha_pp")
    finv = {"1000万": A.col(po, "finv_1000w"), "3000万": A.col(po, "finv_3000w")}
    n_keep = A.col(po, "n_keep")

    # ---------- E1 主表：1000万/3000万（冻结口径，A 级已复现） ----------
    say("\n[E1] 资金档对照表（1000万/3000万 = 冻结口径，A1/A3 已逐位复现）")
    rec_o = json.load(open(A.FROZEN["phaseo_summary"], encoding="utf-8"))
    table = []
    for w_key in ("1000万", "3000万"):
        w = 1.0e7 if w_key == "1000万" else 3.0e7
        k_med = statistics.median([A.capacity_indep(
            [l for l in [1]], w)["k"] for l in [1]])  # 占位，下面用 A3 的 k_median
        cap = [c for c in a3["capacity_indep"] if abs(c["w_wan"] - w / 1e4) < 1e-9][0]
        orders = statistics.mean([2.0 * t * cap["k_median"] for t in tos])
        for fee in ("BASE", "STRESS", "CONSERVATIVE"):
            r = rec_o["results"][f"W_{w_key}|{fee}"]
            cost_min_comm = r["cost_pp"] - rec_o["results"][f"W_{w_key}|BASE"]["cost_pp"] \
                if fee == "CONSERVATIVE" else 0.0
            row = {"w_wan": w_key, "fee": fee,
                   "gross_alpha_v1_pp": 0.163411,
                   "net_pp": r["net_pp"], "t_nw": r["t_nw"], "t_iid": r["t_iid"],
                   "cost_pp": r["cost_pp"], "uninvested_median_pct": cap["uninvested_median"] * 100,
                   "uninvested_mean_pct": cap["uninvested_mean"] * 100,
                   "to_mean": statistics.mean(tos), "orders_per_month": orders,
                   "min_comm_effect_pp": cost_min_comm,
                   "n": r["n"]}
            table.append(row)
            say(f"  {w_key} {fee:<13} 净 {r['net_pp']:+.4f}pp t={r['t_nw']:+.2f} "
                f"成本 {r['cost_pp']:.4f}pp 未投资中位 {row['uninvested_median_pct']:.2f}% "
                f"订单 {orders:.0f}/月"
                + (f" 最低佣金影响 {cost_min_comm:+.4f}pp" if fee == "CONSERVATIVE" else ""))

    # ---------- E2 扩展档（诊断性：broker 惯例 + 缩放冲击两种口径） ----------
    say("\n[E2] 扩展资金档（5000万/1亿）——诊断性，非预登记口径")
    say("  口径 a（broker doc 惯例）：现金残留与冲击沿用 3000万 值")
    say("  口径 b（审计缩放）：现金残留用 A3 独立容量；冲击按模型缩放"
        "（Amihud 线性 ∝W；√ 模型 ∝√W）")
    ext_rows = []
    for w_key, w in (("5000万", 5.0e7), ("10000万", 1.0e8)):
        cap = [c for c in a3["capacity_indep"] if abs(c["w_wan"] - w / 1e4) < 1e-9][0]
        finv_own = 1.0 - cap["uninvested_median"]
        for tag, finv_v, imp_v in [
            ("a_broker惯例", finv["3000万"], IMPACT_3000W),
            ("b_独立容量+线性冲击", [finv_own] * len(tos), IMPACT_3000W * w / 3.0e7),
            ("b_独立容量+√冲击", [finv_own] * len(tos), IMPACT_3000W * (w / 3.0e7) ** 0.5),
        ]:
            for fee in ("BASE", "CONSERVATIVE"):
                vals = []
                for t, a, f in zip(tos, alpha_v1, finv_v):
                    if fee == "BASE":
                        cst = A.cost_base_indep(t)
                    else:
                        cst = A.cost_cons_indep(t, w, int(cap["k_median"]))
                    vals.append(f * a - cst - imp_v)
                pr = A.nw_t(vals, 3)
                ext_rows.append({"w_wan": w_key, "convention": tag, "fee": fee,
                                 "net_pp": pr["mean"], "t_nw": pr["t_nw"],
                                 "impact_pp": imp_v, "finv_used": statistics.mean(finv_v)})
                say(f"  {w_key} {tag:<22} {fee:<12} 净 {pr['mean']:+.4f}pp t={pr['t_nw']:+.2f} "
                    f"（冲击 {imp_v:.6f}pp，finv {statistics.mean(finv_v):.4f}）")

    # ---------- E3 成本因素清单 ----------
    say("\n[E3] 成本因素清单")
    factors = [
        {"factor": "佣金费率（万2.5 双边）", "in_model": "是（BASE/CONS）", "method": "2×to×费率×100",
         "source": "东方财富官方协议（broker doc §0）", "type": "实证（官方文件）",
         "overlap_risk": "与最低佣金互斥（取 max）"},
        {"factor": "最低佣金 5 元/笔", "in_model": "是（仅 CONSERVATIVE）",
         "method": "单笔 max(金额×费率, 5元)；n_trades=2×to×k",
         "source": "券商官方协议", "type": "实证（官方文件）",
         "overlap_risk": "与从价佣金取 max，无重复"},
        {"factor": "印花税 0.05% 仅卖出", "in_model": "是（BASE/CONS）", "method": "to×0.0005×100",
         "source": "2023-08-28 税制（broker doc §0）", "type": "实证（税制）", "overlap_risk": "无"},
        {"factor": "过户费 0.001% 双边", "in_model": "是（BASE/CONS）", "method": "2×to×0.00001×100",
         "source": "2022-04-29 起沪深均收", "type": "实证（规费）", "overlap_risk": "无"},
        {"factor": "买卖价差（spread）", "in_model": "否", "method": "N/A",
         "source": "无盘中/盘口数据", "type": "未建模",
         "overlap_risk": "与冲击成本部分重叠（若未来建模需避免双算）"},
        {"factor": "市场冲击（Amihud 线性）", "in_model": "是（阶段 O 常数上界）",
         "method": "Σ w_i×ILLIQ_i×P；O 用样本均值常数 0.000784pp",
         "source": "Amihud 2002（无自由参数）", "type": "模型估计",
         "overlap_risk": "与 √ 模型二选一（S 阶段改收 √ 后作为敏感性）"},
        {"factor": "市场冲击（√ 模型 η=1）", "in_model": "是（阶段 S 起）",
         "method": "η×σ×√(P/ADV) 加权；S 用 3000万全样本常数 0.018996pp",
         "source": "文献最悲观端 η=1", "type": "模型假设",
         "overlap_risk": "常数法会高估后段冲击（S 已披露，方向保守）"},
        {"factor": "建仓日涨跌停/停牌", "in_model": "是（V1 剔除）",
         "method": "flags_fast 判定，BLOCK_LONG 四类",
         "source": "exec 真实 OHLC", "type": "实证",
         "overlap_risk": "ST 股 5% 限制无历史标记 → 漏判（已知限制）"},
        {"factor": "平仓日不可卖出", "in_model": "否（V2 计数器 1.93%）", "method": "只计数不重算",
         "source": "exec 真实 OHLC", "type": "已披露缺口", "overlap_risk": "无"},
        {"factor": "T+1 执行缺口（收益期自信号日收盘起算）", "in_model": "否",
         "method": "A3 诊断：月均 −0.0108pp（冻结口径高估）",
         "source": "A3 面板重建", "type": "审计诊断（新）", "overlap_risk": "与冲击/价差部分同向"},
        {"factor": "持有期内退市", "in_model": "否", "method": "A3 诊断（见 exec_diagnostics）",
         "source": "span.last vs 月末", "type": "审计诊断（新）", "overlap_risk": "无"},
        {"factor": "整手/现金约束", "in_model": "是（capacity_of）",
         "method": "可负担子集 + floor 整手；finv 缩放 alpha",
         "source": "阶段 I 预登记 §5", "type": "规则（写死）", "overlap_risk": "无"},
    ]
    for f in factors:
        say(f"  {f['factor']:<34} 入模:{f['in_model']:<18} 类型:{f['type']}")

    # ---------- 产物 ----------
    A.write_csv(os.path.join(OUT, "capital_table.csv"),
                ["w_wan", "fee", "gross_alpha_v1_pp", "net_pp", "t_nw", "t_iid", "cost_pp",
                 "uninvested_median_pct", "uninvested_mean_pct", "to_mean", "orders_per_month",
                 "min_comm_effect_pp", "n"],
                [[r["w_wan"], r["fee"], r["gross_alpha_v1_pp"], round(r["net_pp"], 6),
                  round(r["t_nw"], 4), round(r["t_iid"], 4), round(r["cost_pp"], 6),
                  round(r["uninvested_median_pct"], 4), round(r["uninvested_mean_pct"], 4),
                  round(r["to_mean"], 6), round(r["orders_per_month"], 1),
                  round(r["min_comm_effect_pp"], 6), r["n"]] for r in table])
    A.write_csv(os.path.join(OUT, "extended_tiers.csv"),
                ["w_wan", "convention", "fee", "net_pp", "t_nw", "impact_pp", "finv_used"],
                [[r["w_wan"], r["convention"], r["fee"], round(r["net_pp"], 6),
                  round(r["t_nw"], 4), round(r["impact_pp"], 6), round(r["finv_used"], 6)]
                 for r in ext_rows])
    A.write_csv(os.path.join(OUT, "cost_factors.csv"),
                ["factor", "in_model", "method", "source", "type", "overlap_risk"],
                [[f["factor"], f["in_model"], f["method"], f["source"], f["type"], f["overlap_risk"]]
                 for f in factors])
    A.write_json(os.path.join(OUT, "summary.json"), {
        "capital_table": table, "extended_tiers": ext_rows, "cost_factors": factors,
        "cash_decomposition_from_a3": a3["cash_decomposition"],
        "broker_em_default_3000w": bj["exq5"]["3000万"]["EM_DEFAULT"],
        "note": "扩展档为审计诊断（C 类），非预登记口径；两种冲击惯例并列报告",
    })
    with open(os.path.join(OUT, "run.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[产物] {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
