"""L3t 分辨率登记：把各口径实验的 MDE 用同一套实现重算并登记。

用法：``PYTHONPATH=backend/src python scripts/l3t_registry.py [--quiet]``

预登记 ``docs/gates/l3t_prereg.json``（sha256 前缀 ``e0a4167372b23bd2``）在启动时校验。

产物：
- ``reports/gates/H-L3T-RESOLUTION/l3t_registry.json``
- ``reports/gates/H-L3T-RESOLUTION/l3t_registry.md``
- ``reports/L3T_RESOLUTION_EVIDENCE.json``
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "src"))

PREREG = ROOT / "docs" / "gates" / "l3t_prereg.json"
PREREG_SHA_PREFIX = "e0a4167372b23bd2"
OUT_DIR = ROOT / "reports" / "gates" / "H-L3T-RESOLUTION"
EVIDENCE = ROOT / "reports" / "L3T_RESOLUTION_EVIDENCE.json"

#: 每条序列的登记配置。unit：decimal=原始小数（×100 转 pp）、pp=已是百分点、ic=无量纲。
#: k_column 只取中位数用于说明「截面多宽」，不参与 MDE 计算。
SERIES = [
    # ---- 族 A：月度截面多空价差 ----
    dict(key="gate1_d", family="A_cross_spread", label="Gate 1 动量（策略−基准）",
         path="gate1_out/032a11f591553e25/monthly_paired.csv", column="d", unit="decimal",
         verdict="INCONCLUSIVE", note="策略 vs 基准的单边差，不是多空"),
    dict(key="gate2_d", family="A_cross_spread", label="Gate 2 反转 532 池",
         path="gate2_out/c440c77d284a/monthly_paired.csv", column="d", unit="decimal",
         k_column="k_used", verdict="FAIL"),
    dict(key="gate3_d", family="A_cross_spread", label="Gate 3 反转（长样本）",
         path="gate3_out/9b7235d77689/gate2_rerun_2016_2026.csv", column="d", unit="decimal",
         k_column="k_used", verdict="PASS"),
    dict(key="gate4_d", family="A_cross_spread", label="Gate 4 低波动",
         path="gate4_out/94988f7c57d1/monthly_paired.csv", column="d", unit="decimal",
         k_column="k_used", verdict="FAIL"),
    dict(key="gate5_d", family="A_cross_spread", label="Gate 5 活跃度",
         path="gate5_out/7c0c89916b0c/monthly_paired.csv", column="d", unit="decimal",
         k_column="k_used", verdict="FAIL"),
    dict(key="gate6_d", family="A_cross_spread", label="Gate 6 反转 as-of 全A",
         path="gate6_out/b8c4f1ecba44/monthly_paired.csv", column="d", unit="decimal",
         k_column="k_used", verdict="PASS"),
    dict(key="phasec_d", family="A_cross_spread", label="阶段 C 池效应（格 A 含约束）",
         path="phasec_out/3a01759a37f0/five_cells.csv", column="d_raw", unit="decimal",
         filters={"cell": "A"}, k_column="k_used", verdict="POOL_EFFECT_CONFIRMED"),
    dict(key="phasec2_d", family="A_cross_spread", label="阶段 C-2 成交约束",
         path="phasec2_out/953c32fdc556/cells.csv", column="d", unit="decimal",
         verdict="POOL_EFFECT_CONFIRMED"),
    dict(key="phasec3_d", family="A_cross_spread", label="阶段 C-3 剔涨停",
         path="phasec3_out/6a76a10687b4/cells.csv", column="d_v0", unit="decimal",
         verdict="POOL_EFFECT_CONFIRMED"),
    dict(key="phasee_alpha", family="A_cross_spread", label="阶段 E 单边 alpha",
         path="phasee_out/2a3893f3f5b1/monthly.csv", column="alpha", unit="decimal",
         k_column="k", verdict="SINGLE_SIDE_NOT_DETECTED"),
    dict(key="phasef_delta", family="A_cross_spread", label="阶段 F MAX5 分层 delta",
         path="phasef_out/aec46d88bbda/monthly.csv", column="delta", unit="decimal",
         verdict="MAX5_EFFECT_CONFIRMED"),
    dict(key="phaseg_alpha", family="A_cross_spread", label="阶段 G MAX5 单边 alpha",
         path="phaseg_out/e7da0f9ff3f1/monthly.csv", column="alpha_v1", unit="decimal",
         k_column="n_v1", verdict="NOT_EXECUTABLE"),
    dict(key="phasel_alpha", family="A_cross_spread", label="阶段 L SURGE alpha（V1）",
         path="phasel_out/9ffc3710cf6f/monthly.csv", column="alpha_v1_pp", unit="pp",
         k_column="n_keep", verdict="SURGE_AVOID_ABSORBED"),
    dict(key="phasem_spread", family="A_cross_spread", label="阶段 M 多空价差",
         path="phasem_out/2f5f61dd0b5f/monthly.csv", column="spread_pp", unit="pp",
         verdict="SHORT_SIDE_REJECTED"),
    # ---- 族 B：月度 long-only 相对基准 ----
    dict(key="phasej_netA", family="B_long_only", label="阶段 J 净收益（冲击 A）",
         path="phasej_out/f76de8a277f6/monthly.csv", column="net_A_pp", unit="pp",
         k_column="n_keep", verdict="NET_ABSORBED"),
    dict(key="phasek_m1net", family="B_long_only", label="阶段 K 持仓 1 月净收益",
         path="phasek_out/3bc31a35903d/monthly.csv", column="m1_net", unit="pp",
         verdict="LOW_TURNOVER_INSUFFICIENT"),
    dict(key="phaseo_netBASE", family="B_long_only", label="阶段 O 净收益 BASE@3000万",
         path="phaseo_out/ac7aa2830ab0/monthly.csv", column="net_BASE_3000w", unit="pp",
         k_column="n_keep", verdict="EXECUTABLE_UNDER_BASE_FEES"),
    dict(key="phasep_late", family="B_long_only", label="阶段 P 后段净收益",
         path="phasep_out/3d19f7508e10/monthly.csv", column="net_BASE_3000w_pp", unit="pp",
         filters={"segment": "late"}, verdict="SEGMENT_DIFF_NOT_DETECTED"),
    dict(key="phaser_alpha", family="B_long_only", label="阶段 R SURGE alpha（V1）",
         path="phaser_out/7b5e7643e8ca/monthly.csv", column="alpha_v1_pp", unit="pp",
         k_column="n_keep_v1", verdict="SURGE_SCALE_DEPENDENT"),
    dict(key="phases_net", family="B_long_only", label="阶段 S 后段净收益",
         path="phases_out/f098a59fb1b5/monthly.csv", column="net_S_3000w", unit="pp",
         filters={"segment": "2016-2026"}, k_column="n_keep_v1",
         verdict="MODERN_ERA_ALPHA_DETECTED"),
    dict(key="phaset_net", family="B_long_only", label="阶段 T 现代段净收益",
         path="phaset_out/ef29a743997b/monthly.csv", column="net_S_3000w", unit="pp",
         filters={"segment": "modern"}, verdict="T_FRAGILE"),
    dict(key="phasev_net", family="B_long_only", label="阶段 V 净收益 O BASE@3000万",
         path="phasev_out/3614e12a0546/monthly.csv", column="net_O_BASE_3000w", unit="pp",
         verdict="COST_CONVENTION_SENSITIVE"),
    dict(key="l3r_delta", family="B_long_only", label="L3r 选择效应（截面 Δ）",
         path="reports/gates/H-L3R-SELECTION/periods.json", column="delta", unit="decimal",
         source="json", k_column="n_strategy_priced", verdict="INCONCLUSIVE_POWER"),
    # ---- 族 C：截面回归系数 ----
    dict(key="phaseq_max5", family="C_regression", label="阶段 Q MAX5 偏效应系数",
         path="phaseq_out/ae7fc45acab0/monthly.csv", column="coef_MAX5_pp", unit="pp",
         verdict="MAX5_INCREMENTAL"),
    # ---- 族 D：IC ----
    dict(key="l3s_ic", family="D_ic", label="L3s 全池截面 IC",
         path="reports/gates/H-L3S-IC-000001/l3s_result.json", column="ic", unit="ic",
         source="json", item_path="periods", verdict="IC_INCONCLUSIVE_POWER"),
    # ---- 族 E：Walk-Forward 折级 ----
    dict(key="l3_test", family="E_fold", label="L3 折级测试收益",
         path="reports/gates/H-L3-MACROSS-000001/l3_result.json", column="test_return",
         unit="decimal", source="json", item_path="result.folds", verdict="L3_BLOCKED_PREREQUISITE"),
    dict(key="l3p_test", family="E_fold", label="L3p 折级测试收益",
         path="reports/gates/H-L3P-MACROSS-000001/l3p_result.json", column="test_return",
         unit="decimal", source="json", item_path="result.folds", verdict="L3P_BLOCKED_PREREQUISITE"),
]


def _f(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or value != value else f"{value:.{digits}f}"


def _pp(value: float | None) -> str:
    return "—" if value is None or value != value else f"{value:+.3f}pp"


def main() -> int:
    parser = argparse.ArgumentParser(description="L3t 分辨率登记")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    from deepquant.gates import resolution as rs

    started = time.perf_counter()
    sha = rs.check_prereg_sha(PREREG, PREREG_SHA_PREFIX)

    rows: list[dict[str, Any]] = []
    for spec_dict in SERIES:
        spec = rs.SeriesSpec(**{k: v for k, v in spec_dict.items() if k in rs.SeriesSpec.__dataclass_fields__})
        values, meta = rs.load_series(spec, ROOT)
        summary = rs.summarise(values, unit=spec.unit)
        row = {
            "key": spec.key,
            "family": spec.family,
            "label": spec.label,
            "verdict": spec_dict.get("verdict", ""),
            "note": spec_dict.get("note", ""),
            **summary,
            **meta,
        }
        flags = rs.flag_row(row)
        row["flags"] = flags
        row["unit_suspect"] = any("疑似单位错" in f for f in flags)
        rows.append(row)

    rollup = rs.family_rollup(rows)
    below = rs.references_below_best_resolution(rows, rollup)

    # ---- 预测核对 ----
    a = rollup.get("A_cross_spread", {})
    b = rollup.get("B_long_only", {})
    # 极差只能在**同一单位**内算：把 IC（无量纲）和 %/折 混进来会得到一个
    # 看似惊人、实则无意义的 200× —— 这正是本表要防的那类错误。
    pp_mde = [
        r["mde"]
        for r in rows
        if r["unit"] == "pp" and r["mde"] == r["mde"] and r["family"] in {"A_cross_spread", "B_long_only", "C_regression"}
    ]
    spread_ratio = (max(pp_mde) / min(pp_mde)) if pp_mde and min(pp_mde) > 0 else None
    l3r = next((r for r in rows if r["key"] == "l3r_delta"), None)
    b_mdes = sorted(
        [r["mde"] for r in rows if r["family"] == "B_long_only" and r["mde"] == r["mde"]]
    )
    l3r_worst_rank = (
        (b_mdes.index(l3r["mde"]) + 1) / len(b_mdes) if l3r and b_mdes else None
    )

    predictions = [
        {
            "id": "P1",
            "claim": "全部序列换算 pp 后 sd ∈ [0.2, 30] 且 |mean| ≤ 5",
            "actual": f"单位越界条目 {sum(1 for r in rows if r['unit_suspect'])} 条",
            "refuted": any(r["unit_suspect"] for r in rows),
        },
        {
            "id": "P2",
            "claim": "族 A 的 MDE 中位 ∈ [0.3, 1.0]pp",
            "actual": f"{_pp(a.get('mde_median'))}",
            "refuted": not (a.get("mde_median") and 0.3 <= a["mde_median"] <= 1.0),
        },
        {
            "id": "P3",
            "claim": "族 B 的 MDE 中位 < 族 A 的 MDE 中位",
            "actual": f"A {_pp(a.get('mde_median'))} vs B {_pp(b.get('mde_median'))}",
            "refuted": not (
                a.get("mde_median")
                and b.get("mde_median")
                and b["mde_median"] < a["mde_median"]
            ),
        },
        {
            "id": "P4",
            "claim": "全库最大 MDE / 最小 MDE ≥ 5",
            "actual": f"{_f(spread_ratio, 2)}×（仅 pp 口径家族；最大 {_pp(max(pp_mde))}，最小 {_pp(min(pp_mde))}）",
            "refuted": not (spread_ratio and spread_ratio >= 5),
        },
        {
            "id": "P5",
            "claim": "L3r 的 0.926pp 位于族 B 最差端（MDE 排名后 20%）",
            "actual": f"L3r MDE {_pp(l3r['mde']) if l3r else '—'}，族 B 内由小到大排名 {_f(l3r_worst_rank, 2)}（1=最好）",
            "refuted": not (l3r_worst_rank and l3r_worst_rank >= 0.8),
        },
        {
            "id": "P6",
            "claim": "已确认结论中至少 3 条效应量 < 同族最好 MDE",
            "actual": f"{len(below)} 条",
            "refuted": len(below) < 3,
        },
    ]

    elapsed = time.perf_counter() - started
    verdict = "RESOLUTION_REGISTRY_BUILT" if not predictions[0]["refuted"] else "UNIT_CHECK_FAILED"

    payload = {
        "experiment_id": "H-L3T-RESOLUTION-000001",
        "audit_only": True,
        "prereg_sha256": sha,
        "prereg": PREREG.relative_to(ROOT).as_posix(),
        "elapsed_seconds": elapsed,
        "verdict": verdict,
        "families": rollup,
        "rows": rows,
        "below_family_best": below,
        "spread_ratio": spread_ratio,
        "predictions": predictions,
        "limits": [
            "只登记**有月度/折级序列落盘**的实验；阶段 I（容量表）、U（profile 表）等无逐期序列者未入表。",
            "MDE 对应 NW(3)+2.8016 这一套口径；换成 NW(6) 或单侧 1.645 后数值会变。",
            "跨族不得并列：se 同时取决于 n、截面标的数 k 与序列波动，三者都不同。",
            "「效应量 < 同族最好 MDE」不等于该结论错：它只说明换更强的设计可能检不出同样的效应。",
            "族 E 的 n 是折数不是月数，MDE 单位是「%/折」，与族 A/B 不可直接比。",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3t_registry.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )
    (OUT_DIR / "l3t_registry.md").write_text(_render(payload), encoding="utf-8", newline="\n")
    EVIDENCE.write_text(
        json.dumps(
            {
                "experiment_id": payload["experiment_id"],
                "audit_only": True,
                "prereg_sha256": sha,
                "verdict": verdict,
                "families": rollup,
                "spread_ratio": spread_ratio,
                "predictions": predictions,
                "refuted": [p["id"] for p in predictions if p["refuted"]],
                "artifacts": {
                    "registry": (OUT_DIR / "l3t_registry.json").relative_to(ROOT).as_posix(),
                    "report": (OUT_DIR / "l3t_registry.md").relative_to(ROOT).as_posix(),
                },
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        for family, item in rollup.items():
            print(
                f"{family:16s} n={item['n_series']:2d} 可用 {item['n_usable']:2d} "
                f"MDE 中位 {_pp(item['mde_median'])} 最好 {_pp(item['mde_min'])}"
            )
        print(f"pp 口径内 MDE 极差 {_f(spread_ratio, 1)}×")
    print(f"判定：{verdict}")
    print(f"效应量小于同族最好分辨率：{len(below)} 条")
    print(f"被反驳的预测：{[p['id'] for p in predictions if p['refuted']] or '无'}")
    print(f"耗时 {elapsed:.1f}s")
    return 0


def _render(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# L3t：分辨率登记（各口径的最小可检测效应）")
    lines.append("")
    lines.append(f"- 预登记：`{payload['prereg']}`，sha256 `{payload['prereg_sha256'][:16]}…`")
    lines.append(f"- 判定：**{payload['verdict']}**（audit_only：不产生新证据，不改任何既有结论）")
    lines.append(f"- 登记条目：{len(payload['rows'])} 条，覆盖 {len(payload['families'])} 个口径族")
    lines.append("")
    lines.append("## 一、结论")
    lines.append("")
    lines.append(
        "本项目的分辨率（仅 pp 口径家族）相差 "
        f"**{_f(payload['spread_ratio'], 1)} 倍**。所以「某效应 0.5pp/月算不算大」这个问题，"
        "在不同口径下答案相反——引用必须带口径族。"
    )
    lines.append("")
    lines.append("| 口径族 | 条目 | 可用 | MDE 中位 | 最好（最小） | 最差 |")
    lines.append("|---|---|---|---|---|---|")
    for family, item in payload["families"].items():
        unit = "IC" if item["unit"] == "IC" else "pp"
        fmt = (lambda v: _f(v, 4)) if unit == "IC" else _pp
        lines.append(
            f"| {family} | {item['n_series']} | {item['n_usable']} | {fmt(item['mde_median'])} "
            f"| {fmt(item['mde_min'])} | {fmt(item['mde_max'])} |"
        )
    lines.append("")
    lines.append(
        "> 族名见预登记：A=月度多空价差、B=月度 long-only 超额、C=截面回归系数、D=IC、E=Walk-Forward 折级。"
        "**跨族不得并列**：se 同时取决于 n、截面标的数 k 与序列波动。"
    )
    lines.append("")
    lines.append("## 二、全部条目（按 MDE 由好到差）")
    lines.append("")
    lines.append("| 实验 | 族 | 判定 | n | 平均 | MDE | t_nw3 | 功效 | 备注 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in sorted(payload["rows"], key=lambda r: (r["mde"] if r["mde"] == r["mde"] else 9e9)):
        if row["unit"] == "IC":
            effect, mde = _f(row["mean"], 4), _f(row["mde"], 4)
        elif row["family"] == "E_fold":
            effect, mde = f"{row['mean']:+.2f}%/折", f"{row['mde']:.2f}%/折"
        else:
            effect, mde = _pp(row["mean"]), _pp(row["mde"])
        note = "；".join(row["flags"]) if row["flags"] else row.get("note", "")
        lines.append(
            f"| {row['label']} | {row['family'].split('_')[0]} | `{row['verdict']}` | {row['n']} "
            f"| {effect} | {mde} | {_f(row['t_nw3'], 2)} | {_f(row['power'], 2)} | {note} |"
        )
    lines.append("")
    lines.append("## 三、效应量小于同族最好分辨率的条目")
    lines.append("")
    if payload["below_family_best"]:
        lines.append("| 实验 | 族 | 效应量 | 同族最好 MDE | 比值 |")
        lines.append("|---|---|---|---|---|")
        for item in payload["below_family_best"]:
            lines.append(
                f"| {item['key']} | {item['family']} | {_pp(item['effect_pp'])} "
                f"| {_pp(item['family_best_mde_pp'])} | {_f(item['ratio'], 2)} |"
            )
        lines.append("")
        lines.append(
            "读法：这些条目**自报的效应量**小于同族里分辨率最好的设计能分辨的下限。"
            "它不代表这些结论错（它们的 t 由各自 se 决定），而是说：**换一个更强的设计去测同样大小的效应，"
            "可能检不出**。引用时要写清是哪个设计给出的。"
        )
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 四、逐条核对预登记预测")
    lines.append("")
    lines.append("| 编号 | 预测 | 实测 | 被反驳 |")
    lines.append("|---|---|---|---|")
    for p in payload["predictions"]:
        claim = str(p["claim"]).replace("|", r"\|")
        actual = str(p["actual"]).replace("|", r"\|")
        lines.append(f"| {p['id']} | {claim} | {actual} | {'**是**' if p['refuted'] else '否'} |")
    lines.append("")
    lines.append("## 五、限制")
    lines.append("")
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
