#!/usr/bin/env python
"""A6 统计口径与推断稳健性审计（任务 §六、§八）。

1. 全网格独立复算：16 条被引用序列 × L∈{0..6} + iid，从**源 CSV** 重算，
   与 phaseu_out/b97d66d91989/profiles.csv（阶段 U 记录）逐格对照；
2. 估计量混用审计：Gate 1~6 的 iid 口径 vs 阶段 C 起的 NW(3)（源码坐实）；
3. 单位审计：pp vs 小数（T3 标签缺陷、newey_west 契约、t 的尺度不变性演示）；
4. MDE / 功效公式复算；
5. 单侧 vs 双侧临界值审计（Gate 2/3 = 1.645 单侧；C 起 = 1.96 双侧）；
6. 多重检验与研究选择效应说明（不做校正，只披露）。

输出：out/a6_stat/（grid_full.csv / units_audit.json / summary.json / run.log）
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_common as A  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a6_stat")
REPO = A.REPO

# 阶段 U 的 16 条序列定义（与 run_phase_u_nw_lag_audit.py 的 SERIES 一致；此处独立重写）
SERIES = [
    dict(id="U1", name="Gate 6 反转全A 毛 alpha", csv="gate6_out/b8c4f1ecba44/monthly_paired.csv",
         col="d", unit="dec", est="iid", cls="A", filt=("full",)),
    dict(id="U2", name="阶段 I exQ5 V0", csv="phasei_out/8f5c35121fdb/monthly.csv",
         col="alpha_v0_pp", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U3", name="阶段 I exQ5 V1", csv="phasei_out/8f5c35121fdb/monthly.csv",
         col="alpha_v1_pp", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U4", name="阶段 F MAX5 分层价差", csv="phasef_out/aec46d88bbda/monthly.csv",
         col="delta", unit="dec", est="nw3", cls="A", filt=("full",)),
    dict(id="U5", name="阶段 L SURGE V1", csv="phasel_out/9ffc3710cf6f/monthly.csv",
         col="alpha_v1_pp", unit="pp", est="nw3", cls="C", filt=("full",)),
    dict(id="U6", name="阶段 O BASE@3000万", csv="phaseo_out/ac7aa2830ab0/monthly.csv",
         col="net_BASE_3000w", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U7", name="阶段 O CONS@3000万", csv="phaseo_out/ac7aa2830ab0/monthly.csv",
         col="net_CONS_3000w", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U8", name="阶段 O STRESS@3000万", csv="phaseo_out/ac7aa2830ab0/monthly.csv",
         col="net_STRESS_3000w", unit="pp", est="nw3", cls="C", filt=("full",)),
    dict(id="U9", name="阶段 O BASE@1000万", csv="phaseo_out/ac7aa2830ab0/monthly.csv",
         col="net_BASE_1000w", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U10", name="阶段 O CONS@1000万", csv="phaseo_out/ac7aa2830ab0/monthly.csv",
         col="net_CONS_1000w", unit="pp", est="nw3", cls="A", filt=("full",)),
    dict(id="U11", name="阶段 S 现代段 net_S@3000万", csv="phases_out/f098a59fb1b5/monthly.csv",
         col="net_S_3000w", unit="pp", est="nw3", cls="B", filt=("segment", "2016-2026")),
    dict(id="U12", name="阶段 T T1a（剔6极端月）", csv="phases_out/f098a59fb1b5/monthly.csv",
         col="net_S_3000w", unit="pp", est="nw3", cls="B",
         filt=("segment_drop", "2016-2026", A.DROP6)),
    dict(id="U13", name="阶段 S 毛 alpha 现代段", csv="phases_out/f098a59fb1b5/monthly.csv",
         col="alpha_pp", unit="pp", est="nw3", cls="B", filt=("segment", "2016-2026")),
    dict(id="U14", name="阶段 S BASE 现代段", csv="phases_out/f098a59fb1b5/monthly.csv",
         col="net_BASE_3000w", unit="pp", est="nw3", cls="B", filt=("segment", "2016-2026")),
    dict(id="U15", name="阶段 E 反转单边多头", csv="phasee_out/2a3893f3f5b1/monthly.csv",
         col="alpha", unit="dec", est="nw3", cls="C", filt=("full",)),
    dict(id="U16", name="阶段 G MAX5 单边 V1", csv="phaseg_out/e7da0f9ff3f1/monthly.csv",
         col="alpha_v1", unit="dec", est="nw3", cls="C", filt=("full",)),
]


def extract(spec):
    rows = A.read_csv_rows(os.path.join(REPO, spec["csv"]))
    dates = [r["signal_date"] for r in rows]
    vals = A.col(rows, spec["col"])
    f = spec["filt"]
    if f[0] == "full":
        sel = list(range(len(rows)))
    elif f[0] == "segment":
        seg_col = [r.get("segment", "") for r in rows]
        sel = [i for i, s in enumerate(seg_col) if s == f[1]]
    elif f[0] == "segment_drop":
        seg_col = [r.get("segment", "") for r in rows]
        drop = set(f[2])
        sel = [i for i, (s, d) in enumerate(zip(seg_col, dates))
               if s == f[1] and d[:7] not in drop]
    x = [vals[i] for i in sel]
    if spec["unit"] == "dec":
        x = [v * 100.0 for v in x]        # 统一转 pp（单位铁律）
    return [d for d in (dates[i] for i in sel)], x


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []

    def say(s):
        print(s)
        log.append(s)

    say("A6 统计口径与推断稳健性审计")

    # ---------- 1. 16×7 全网格独立复算 ----------
    say("\n[1] 16 序列 × L{0..6} + iid 全网格独立复算（源 CSV → 独立 NW → 对照阶段 U 记录）")
    up = A.read_csv_rows(A.FROZEN["phaseu_profiles"])
    rec = {(r["series_id"], int(r["L"])): r for r in up}
    grid_rows, checks = [], []
    for spec in SERIES:
        dates, x = extract(spec)
        g = A.nw_profile(x)
        row = {"id": spec["id"], "name": spec["name"], "class": spec["cls"],
               "est_recorded": spec["est"], "n": g[3]["n"]}
        for L in A.LAG_GRID:
            row[f"t_L{L}"] = g[L]["t_nw"]
        row["t_iid"] = g[0]["t_iid"]
        row["mean_pp"] = g[3]["mean"]
        # 对照记录（U1 记录头条 t 是 iid 口径 4.3861083442；profiles 网格里 L=3 格是 NW(3)=4.6443）
        if spec["est"] == "iid":
            got, exp = g[0]["t_iid"], 4.3861083442
            ok_t = abs(got - exp) < 5e-4
        else:
            got, exp = g[3]["t_nw"], float(rec[(spec["id"], 3)]["t_nw"])
            ok_t = abs(got - exp) < 5e-4
        ok_mean = abs(g[3]["mean"] - float(rec[(spec["id"], 3)]["mean_pp"])) < 5e-4
        # 全网格对照
        grid_ok = True
        for L in A.LAG_GRID:
            r_exp = float(rec[(spec["id"], L)]["t_nw"])
            if abs(g[L]["t_nw"] - r_exp) >= 5e-4:
                grid_ok = False
        flip = any((g[L]["t_nw"] >= A.T_CRIT) != (g[3]["t_nw"] >= A.T_CRIT) for L in A.LAG_GRID)
        rec_flip = [L for L in A.LAG_GRID
                    if (float(rec[(spec["id"], L)]["t_nw"]) >= A.T_CRIT)
                    != (float(rec[(spec["id"], 3)]["t_nw"]) >= A.T_CRIT)]
        mine_flip = [L for L in A.LAG_GRID
                     if (g[L]["t_nw"] >= A.T_CRIT) != (g[3]["t_nw"] >= A.T_CRIT)]
        row["flip_mine"] = mine_flip
        row["flip_recorded"] = rec_flip
        row["flip_match"] = (mine_flip == rec_flip)
        grid_rows.append(row)
        checks.append({"id": spec["id"], "t3_ok": ok_t, "mean_ok": ok_mean,
                       "grid_ok": grid_ok, "flip_match": row["flip_match"],
                       "est": spec["est"], "n": g[3]["n"]})
        say(f"  {spec['id']:<4} n={g[3]['n']:>3} t(L3)={g[3]['t_nw']:+.4f}（记录 {exp:+.4f}）"
            f" 全网格={'✓' if grid_ok else '✗'} 翻转={mine_flip or '无'}"
            f"（记录 {rec_flip or '无'}）{'✓' if row['flip_match'] else '✗'}")
    n_grid_bad = [c for c in checks if not c["grid_ok"]]
    n_flip_bad = [c for c in checks if not c["flip_match"]]
    n_t_bad = [c for c in checks if not c["t3_ok"]]
    say(f"  → 112 格中：t 值不一致 {len(n_grid_bad)} 条序列，翻转集不一致 {len(n_flip_bad)} 条，"
        f"t(L=3) 不一致 {len(n_t_bad)} 条")

    # A 类全网格稳健性（独立重算）
    a_ids = [c["id"] for c in checks if rec[(c["id"], 0)]["class"] == "A"]
    a_min = min(min(r[f"t_L{L}"] for L in A.LAG_GRID) for r in grid_rows if r["id"] in a_ids)
    a_min_row = min((r for r in grid_rows if r["id"] in a_ids),
                    key=lambda r: min(r[f"t_L{L}"] for L in A.LAG_GRID))
    say(f"  A 类（{len(a_ids)} 条）全网格最小 t = {a_min:.4f}（{a_min_row['id']}）→ "
        f"{'全部 L 稳健 ✓' if a_min >= A.T_CRIT else '存在不稳健 ✗'}")

    # ---------- 2. 估计量混用审计 ----------
    say("\n[2] 估计量口径审计（源码坐实，非二手）")
    g6 = open(os.path.join(REPO, "scripts/run_gate6_allasof.py"), encoding="utf-8").read()
    has_iid = "statistics.stdev" in g6 and "se = sd / math.sqrt(n)" in g6
    say(f"  Gate 6 `stats_of`：纯 iid（stdev/√n）→ {has_iid}")
    g2 = open(os.path.join(REPO, "scripts/run_gate2_reversal.py"), encoding="utf-8").read()
    say(f"  Gate 2/3：单侧临界 SUCCESS_T=1.645 → {'1.645' in g2}")
    c_src = open(os.path.join(REPO, "scripts/run_phase_c.py"), encoding="utf-8").read()
    say(f"  阶段 C 起：T_CRIT=1.96 双侧，newey_west Bartlett → {'T_CRIT = 1.96' in c_src}")
    say("  → 项目史：Gate 1~6 头条 t 为 iid（U 的发现 #1，已有更正说明）；"
        "Gate 2/3 用单侧 1.645；阶段 C 起 NW(3) 双侧 1.96。三种口径并存，引用必须标注。")

    # ---------- 3. 单位审计 ----------
    say("\n[3] 单位审计")
    # 3a. T3 标签缺陷（A1 已坐实，此处记录）
    pt = json.load(open(A.FROZEN["phaset_summary"], encoding="utf-8"))
    t3 = pt["T3"]["contrib"]
    tot = sum(t3[g]["mean_pp"] for g in t3)
    v0_dec = 0.230703 / 100
    say(f"  T3：五组 mean_pp 合计 {tot:.8f} = V0 alpha {v0_dec:.8f}（小数）→ "
        f"字段名 mean_pp 实为小数（比率/t/恒等式不受影响；引用其数值须 ×100）")
    # 3b. t 的尺度不变性演示（为什么单位 bug 能骗过只看 t 的检查）
    x = A.col(A.read_csv_rows(A.FROZEN["phaseo_monthly"]), "net_BASE_3000w")
    t_pp = A.nw_t(x, 3)["t_nw"]
    t_dec = A.nw_t([v / 100.0 for v in x], 3)["t_nw"]
    say(f"  t 尺度不变性演示：同一序列 pp 入参 t={t_pp:.6f} vs 小数入参 t={t_dec:.6f}"
        f"（差 {abs(t_pp-t_dec):.2e}）→ 单位错误不改变 t，必须同校量级")
    # 3c. newey_west 契约
    say("  newey_west 契约：入参小数 → mean_pp/se_nw_pp = ×100；入参 pp 时只能用 se_nw。"
        "阶段 O/J 曾因误用 se_nw_pp/mean_pp 把 CI/净值放大 100 倍（各有单测钉住）")

    # ---------- 4. MDE / 功效 ----------
    say("\n[4] MDE 与功效公式复算")
    rec_s = json.load(open(A.FROZEN["phases_summary"], encoding="utf-8"))["primary"]
    se = rec_s["se_nw_pp"]
    mde = A.mde_of(se)
    pw = A.power_two_sided(rec_s["t_nw"])
    say(f"  阶段 S 现代段：MDE = 2.8016×{se:.6f} = {mde:.6f}（记录 {rec_s['mde_pp']:.6f}）")
    say(f"  功效 = Φ(|t|−1.96)+Φ(−|t|−1.96) = {pw:.4f}（记录 {rec_s['power_observed']:.4f}）")
    t1a = json.load(open(A.FROZEN["phaset_summary"], encoding="utf-8"))["T1a"]["result"]
    say(f"  阶段 T T1a：MDE = {A.mde_of(t1a['se_nw_pp']):.6f}（记录 {t1a['mde_pp']:.6f}）"
        f" 功效 = {A.power_two_sided(t1a['t_nw']):.4f}（记录 {t1a['power_observed']:.4f}）")

    # ---------- 5. 多重检验与研究选择 ----------
    say("\n[5] 多重检验与研究选择效应（披露，不做校正）")
    say("  - 项目共 24 项预登记实验 + 只读审计，全部在同一批 2007-2026 数据上；")
    say("    exQ5 是多个构造（Gate 6→C→E→F→G→H→I→O→S→T→U）中的**存活者**，")
    say("    其 t 值受研究选择效应影响， Holm 等日内校正无法修正跨构造选择。")
    say("  - exQ5 家族内部：现代段被 P（分段）、S（结项）、T（稳健）、U（NW）四次检视，")
    say("    均非独立样本；『现代段』的任何显著性都只能是样本内读数。")
    say("  - 引用规则：凡翻转结论必须附 t(L) 剖面（U 的披露要求）；Gate 6 须标 iid。")

    # ---------- 产物 ----------
    A.write_csv(os.path.join(OUT, "grid_full.csv"),
                ["id", "name", "class", "est_recorded", "n", "mean_pp"]
                + [f"t_L{L}" for L in A.LAG_GRID] + ["t_iid", "flip_mine", "flip_recorded", "flip_match"],
                [[r["id"], r["name"], r["class"], r["est_recorded"], r["n"],
                  round(r["mean_pp"], 6)]
                 + [round(r[f"t_L{L}"], 6) for L in A.LAG_GRID]
                 + [round(r["t_iid"], 6), "|".join(map(str, r["flip_mine"])),
                    "|".join(map(str, r["flip_recorded"])), r["flip_match"]]
                 for r in grid_rows])
    A.write_json(os.path.join(OUT, "summary.json"), {
        "grid": grid_rows,
        "checks": checks,
        "n_grid_mismatch": len(n_grid_bad), "n_flip_mismatch": len(n_flip_bad),
        "n_t3_mismatch": len(n_t_bad),
        "a_class_min_t_over_grid": a_min,
        "estimator_audit": {"gate6_iid": has_iid, "gate23_one_sided_1645": "1.645" in g2,
                            "phase_c_onward_nw3_two_sided": "T_CRIT = 1.96" in c_src},
        "units_audit": {"t3_mean_pp_is_decimal": {"sum_groups": tot, "v0_alpha_dec": v0_dec,
                                                  "match": abs(tot - v0_dec) < 1e-6},
                        "t_scale_invariance_demo": {"t_pp": t_pp, "t_dec": t_dec,
                                                    "abs_diff": abs(t_pp - t_dec)}},
        "mde_power": {"S_modern": {"mde": mde, "power": pw,
                                   "recorded_mde": rec_s["mde_pp"],
                                   "recorded_power": rec_s["power_observed"]},
                      "T1a": {"mde": A.mde_of(t1a["se_nw_pp"]), "power": A.power_two_sided(t1a["t_nw"]),
                              "recorded_mde": t1a["mde_pp"], "recorded_power": t1a["power_observed"]}},
    })
    with open(os.path.join(OUT, "run.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[产物] {OUT}/")
    return 0 if not (n_grid_bad or n_flip_bad or n_t_bad) else 1


if __name__ == "__main__":
    raise SystemExit(main())
