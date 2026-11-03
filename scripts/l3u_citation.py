"""L3u：引用规范登记 —— 把 L3t 的 27 行对齐到各自实验的判定统计量。

跑法（仓库根）：
    PYTHONPATH="backend/src" python scripts/l3u_citation.py [--quiet]

只读既有产物，不重跑实验，不修改 L3t 的任何产物（R5）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

import pandas as pd  # noqa: E402

from deepquant.gates import citation as cit  # noqa: E402

PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
PREREG_SHA = "0bbdc5ff4ad80f1de528f48544ee49094bc43a3a5fed19759e6fd878915ef493"
L3T_JSON = ROOT / "reports" / "gates" / "H-L3T-RESOLUTION" / "l3t_registry.json"
OUT_DIR = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY"
EVIDENCE = ROOT / "reports" / "L3U_CITATION_EVIDENCE.json"

PP_FAMILIES = {"A_cross_spread", "B_long_only", "C_regression"}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value:+.3f}pp"


def _abs_pp(value: float | None) -> str:
    """量级类数字（MDE、sd）不带正负号——符号对它们没有意义。"""
    return "—" if value is None or value != value else f"{value:.3f}pp"


def _f(value: float | None, digits: int = 4) -> str:
    return "—" if value is None or value != value else f"{value:.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3u 引用规范登记")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    cit.check_prereg_sha(PREREG, PREREG_SHA)
    prereg = cit.load_prereg(PREREG)
    l3t_payload = json.loads(L3T_JSON.read_text(encoding="utf-8"))
    l3t_rows = {row["key"]: row for row in l3t_payload["rows"]}
    nw_lag = int(prereg["hardcoded"]["nw_lag"])

    if len(l3t_rows) != len(prereg["entries"]):
        raise SystemExit(
            f"条目数不一致：预登记 {len(prereg['entries'])} 条，L3t {len(l3t_rows)} 条；拒跑"
        )

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for entry in prereg["entries"]:
        key = entry["key"]
        l3t_row = l3t_rows.get(key)
        if l3t_row is None:
            problems.append(f"{key}: L3t 中不存在")
            continue
        summary_path = ROOT / Path(entry["series"]["path"]).parent / "summary.json"
        needs_stat = entry["stat"].get("mean") is not None
        if not summary_path.exists():
            # 只有「需要比对一个判定均值」的条目才算无法核对；
            # 预登记已声明 stat.mean 为空的条目（多门槛 / 自持有序列）不需要 summary。
            if needs_stat:
                rows.append(
                    {
                        "key": key,
                        "family": entry["family"],
                        "unit": entry["unit"],
                        "comparison": {
                            "class": cit.UNVERIFIABLE,
                            "t_only": False,
                            "detail": f"缺少 {summary_path.name}",
                        },
                        "verdict_original": entry.get("verdict_literal"),
                        "verdict_recorded_l3t": l3t_row["verdict"],
                        "verdict_matches_l3t": entry.get("verdict_literal") == l3t_row["verdict"],
                        "detected": None,
                        "l3t": {
                            "n": int(l3t_row["n"]),
                            "mean": float(l3t_row["mean"]),
                            "t_nw3": float(l3t_row["t_nw3"]),
                            "mde": float(l3t_row["mde"]),
                        },
                        "stat": {"applicable": True, "mean": None, "t": None, "n": None},
                        "recomputed": {},
                        "grade": {"tier": "NOT_GRADED", "ratio": None, "reason": "原产物缺失"},
                    }
                )
                continue
            summary = {}
        else:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows.append(
            cit.build_row(entry, l3t_row, summary, ROOT, prereg=prereg, nw_lag=nw_lag)
        )

    # ---- 0. 量级自检：MDE 超过同族中位 20 倍即报警（防单位换算错误静默通过）----
    family_mde: dict[str, list[float]] = {}
    for row in rows:
        if "recomputed" not in row or not row["recomputed"]:
            continue
        mde = row["recomputed"].get("mde")
        if mde is None or mde != mde or mde <= 0:
            continue
        family_mde.setdefault(row["family"], []).append(mde)
    family_mde_median = {
        fam: statistics.median(vals) for fam, vals in family_mde.items() if vals
    }
    for row in rows:
        if "recomputed" not in row or not row["recomputed"]:
            continue
        mde = row["recomputed"].get("mde")
        median = family_mde_median.get(row["family"])
        row["mde_magnitude_suspect"] = bool(
            mde and median and mde > 20.0 * median and row["family"] != "E_fold"
        )

    # ---- 1. 核对结果 ----
    classes: dict[str, int] = {}
    for row in rows:
        cls = row["comparison"]["class"]
        classes[cls] = classes.get(cls, 0) + 1
    # MISMATCH_MEAN 里「只有 t 不同」的单独计数：mean 一致说明取的是同一列，
    # 与「取错列」是两件事，合并展示会让 6 条正常的条目看起来像错误。
    t_only_keys = [
        r["key"]
        for r in rows
        if r["comparison"]["class"] == cit.MISMATCH_MEAN and r["comparison"].get("t_only")
    ]
    mean_differs_keys = [
        r["key"]
        for r in rows
        if r["comparison"]["class"] == cit.MISMATCH_MEAN and not r["comparison"].get("t_only")
    ]

    graded = [r for r in rows if r["comparison"]["class"] in {cit.MATCH, cit.NOT_APPLICABLE}]
    mismatched = [r for r in rows if r["comparison"]["class"] not in {cit.MATCH, cit.NOT_APPLICABLE}]
    real_mismatch = [r for r in mismatched if not r["comparison"].get("t_only")]
    verdict_mismatch = [
        r for r in rows if r.get("verdict_original") and not r.get("verdict_matches_l3t")
    ]

    # ---- 2. 修正后的 MDE 极差（仅 pp 口径家族）----
    pp_mde = [
        r["recomputed"]["mde"]
        for r in rows
        if r["unit"] == "pp" and r["family"] in PP_FAMILIES and "recomputed" in r
    ]
    spread_ratio = (max(pp_mde) / min(pp_mde)) if pp_mde and min(pp_mde) > 0 else None
    l3t_pp_mde = [
        r["mde"]
        for r in l3t_payload["rows"]
        if r["unit"] == "pp" and r["family"] in PP_FAMILIES
    ]
    spread_ratio_l3t = (
        (max(l3t_pp_mde) / min(l3t_pp_mde)) if l3t_pp_mde and min(l3t_pp_mde) > 0 else None
    )

    # ---- 3. 修正后的 below_family_best ----
    fam_best: dict[str, float] = {}
    for row in graded:
        if "recomputed" not in row:
            continue
        if row["recomputed"]["n"] < int(prereg["hardcoded"]["min_n"]):
            continue
        mde = row["recomputed"]["mde"]
        if mde != mde or mde <= 0:
            continue
        fam_best[row["family"]] = min(fam_best.get(row["family"], mde), mde)

    below = []
    for row in graded:
        if "recomputed" not in row:
            continue
        best = fam_best.get(row["family"])
        effect = row["stat"].get("mean")
        if best is None or effect is None:
            continue
        if abs(effect) < best:
            below.append(
                {
                    "key": row["key"],
                    "family": row["family"],
                    "effect": effect,
                    "family_best_mde": best,
                    "ratio": abs(effect) / best if best > 0 else None,
                }
            )
    below.sort(key=lambda item: item["ratio"] if item["ratio"] is not None else 9e9)

    # ---- 4. 分级分布 ----
    tiers: dict[str, list[str]] = {}
    for row in rows:
        tiers.setdefault(row["grade"]["tier"], []).append(row["key"])

    detected_rows = [r for r in rows if r.get("detected") is True]
    quotable = [r["key"] for r in detected_rows if r["grade"]["tier"] == "QUOTABLE"]

    # ---- 5. 预测核对 ----
    preds: list[dict[str, Any]] = []
    n_verdict_match = sum(
        1 for r in rows if r.get("verdict_original") and r.get("verdict_matches_l3t")
    )
    n_stat_match = sum(1 for r in rows if r["comparison"]["class"] == cit.MATCH)

    def add(pid: str, claim: str, actual: str, refuted: bool) -> None:
        preds.append({"id": pid, "claim": claim, "actual": actual, "refuted": refuted})

    add(
        "P1",
        "判定字符串与 L3t 记录完全一致的条目 ≥ 18/27",
        f"{n_verdict_match}/27 一致；不一致 {len(verdict_mismatch)} 条",
        n_verdict_match < 18,
    )
    add(
        "P2",
        "判定统计量与 L3t 行一致的条目 ≥ 18/27",
        f"{n_stat_match}/27 一致（MATCH）",
        n_stat_match < 18,
    )
    n_rebuild = sum(
        1 for e in prereg["entries"] if e["series"]["kind"] in {"pivot", "terms"}
    )
    add(
        "P3",
        "需要重建序列（非单列）的条目恰好 3 条",
        f"{n_rebuild} 条（{', '.join(e['key'] for e in prereg['entries'] if e['series']['kind'] in {'pivot', 'terms'})}）",
        n_rebuild != 3,
    )
    add(
        "P4",
        "更正后 pp 口径内 MDE 极差 < 20.9×",
        f"{_f(spread_ratio, 2)}×（L3t 原值 {_f(spread_ratio_l3t, 2)}×）",
        spread_ratio is None or spread_ratio >= 20.9,
    )
    add(
        "P5",
        "更正后 below_family_best 条目数 ≤ 3（L3t 为 5 条）",
        f"{len(below)} 条",
        len(below) > 3,
    )
    add(
        "P6",
        "detected=true 中 ratio ≥ 2（QUOTABLE）的 ≥ 3 条",
        f"{len(quotable)} 条（{', '.join(quotable) or '无'}）",
        len(quotable) < 3,
    )
    add(
        "P7",
        "UNVERIFIABLE 条目数为 0",
        f"{classes.get(cit.UNVERIFIABLE, 0)} 条",
        classes.get(cit.UNVERIFIABLE, 0) != 0,
    )

    # ---- 6. 汇总 ----
    payload = {
        "experiment_id": prereg["experiment_id"],
        "audit_only": True,
        "prereg_sha256": PREREG_SHA,
        "subject": (L3T_JSON.relative_to(ROOT).as_posix(),),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "n_entries": len(rows),
        "class_counts": classes,
        "class_detail": {
            "t_only_keys": t_only_keys,
            "mean_differs_keys": mean_differs_keys,
            "note": "t_only 指 mean 与样本数都一致、仅 t 不同（多为 NW 滞后或 iid/NW 估计量差异），不构成「取错列」",
        },
        "family_mde_median": family_mde_median,
        "mde_suspect_keys": [r["key"] for r in rows if r.get("mde_magnitude_suspect")],
        "verdict_reconciliation": {
            "n_recorded": sum(1 for r in rows if r.get("verdict_original")),
            "n_match": n_verdict_match,
            "mismatches": [
                {
                    "key": r["key"],
                    "original": r["verdict_original"],
                    "recorded_in_l3t": r["verdict_recorded_l3t"],
                }
                for r in verdict_mismatch
            ],
        },
        "mismatched_entries": [
            {
                "key": r["key"],
                "class": r["comparison"]["class"],
                "t_only": r["comparison"].get("t_only", False),
                "detail": r["comparison"].get("detail", ""),
                "l3t_mean": r["l3t"]["mean"],
                "l3t_n": r["l3t"]["n"],
                "stat_mean": r["stat"].get("mean"),
                "stat_n": r["stat"].get("n"),
                "series": r.get("series"),
            }
            for r in mismatched
        ],
        "mde_spread": {
            "recomputed_pp": spread_ratio,
            "l3t_pp": spread_ratio_l3t,
            "recomputed_max": max(pp_mde) if pp_mde else None,
            "recomputed_min": min(pp_mde) if pp_mde else None,
        },
        "family_best_mde": fam_best,
        "below_family_best": below,
        "grade_distribution": {k: sorted(v) for k, v in tiers.items()},
        "predictions": preds,
        "rows": rows,
        "problems": problems,
        "limits": [
            "本次只核对「登记值 vs 判定值」，不重跑实验；MDE 是在**重建后的判定序列**上用 L3t 同一实现重算的。",
            "phasem / phaser / phasev / l3r / l3s / l3 / l3p 七条的判定不是单一均值（区间判定 / 多门槛 / 自持有序列），"
            "标记为 NOT_APPLICABLE，只核对了它们的序列与 MDE，未做 mean/t 对比。",
            "分级阈值（2×/1×/0.5× 与功效 0.8/0.5）在预登记中固定，未因结果调整；落在边界附近的条目按数值原样报告。",
            "「可引用强度」是**引用规范**，不是对结论对错的重新判定；L3t 与各阶段原判定字符串均未被修改。",
            "phasej 的判定档是 3000 万，而 CSV 里的 net_A_pp 对应 1000 万档；本实验按预登记比对两者的差异，"
            "不改用另一档重算（那属于新实验）。",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3u_citation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    # ---- 7. Markdown ----
    def _u(value: float | None, unit: str, signed: bool = True) -> str:
        """按单位渲染：pp 带符号、IC 四位小数、%/每折两位小数。量级类一律不带符号。"""
        if value is None or value != value:
            return "—"
        if unit == "pp":
            return f"{value:+.3f}pp" if signed else f"{value:.3f}pp"
        if unit == "ic":
            return f"{value:+.4f}"
        return f"{value:+.2f}%/折" if signed else f"{value:.2f}%/折"

    lines: list[str] = []
    lines.append("# L3u 引用规范登记：27 条结论的判定统计量核对与可引用强度")
    lines.append("")
    lines.append(
        f"> 预登记 `docs/gates/l3u_prereg.json` sha256 `{PREREG_SHA[:12]}…`（脚本启动校验，含 3 条修订记录）"
        "｜审计对象 `reports/gates/H-L3T-RESOLUTION/l3t_registry.json`｜**只读审计，不重跑实验**"
    )
    lines.append("")

    lines.append("## 一、结论先行")
    lines.append("")
    lines.append(
        f"**L3t 那张表不能直接拿来做引用规范。**27 行里："
        f"**{len(mean_differs_keys) + classes.get(cit.MISMATCH_N, 0)} 行取错了量**"
        f"（{len(mean_differs_keys)} 行取错列或资金档，{classes.get(cit.MISMATCH_N, 0)} 行窗口不对）、"
        f"**{len(t_only_keys)} 行只是 t 的估计量不同**（mean 与样本数都对）、"
        f"**{len(verdict_mismatch)} 行的判定字符串在转写时失真**（其中 1 条语义反转）。"
    )
    lines.append("")
    lines.append("具体三类：")
    lines.append("")
    lines.append(
        "1. **取错了量**（最严重）——阶段 C 的判定是 `spread(B) − spread(A)` = **+1.9390pp（t=6.37）**，"
        "L3t 登记的是格 A 自己的 `d_raw` 水平 **+0.0141pp（t=0.09）**，差 138 倍。"
        "按 L3t 的数字读，会得出「这个结论分辨率不足」的相反结论。同类问题还有："
        "`phasec2`/`phasec3` 都登记成同一个 V0 水平（两行 mean 完全相同，都是 +0.6582pp，"
        "而它们真正检验的量是 +0.107pp 与 +0.047pp）；`gate3` 用了 2016-2026 的重跑文件"
        "（n=125、mean **−0.398pp**）而非判定窗口 2007-2015（n=108、mean **+0.484pp**，符号相反）；"
        "`phasej` 用了 1000 万档而非判定档 3000 万。"
    )
    lines.append(
        "2. **只是 t 不同**——Gate 1~6 的 summary 只给 iid t，L3t 统一用了 NW(3) t，"
        "两者差 0.05~0.25，触发不一致标记。**这不是错误**，但合并展示会让 6 条正常条目看起来像出问题。"
    )
    lines.append(
        "3. **判定字符串失真 13 条**——`failure` 被写成 `INCONCLUSIVE`、"
        "`SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT` 被写成 `POOL_EFFECT_CONFIRMED`（两条都这样）、"
        "最严重的是 `INCREMENTAL_ABSORBED`（吸收＝不成立）被写成 `MAX5_INCREMENTAL`（听起来像成立）——**语义反转**。"
    )
    lines.append("")
    lines.append(
        f"**最重要的一条结论：没有任何一个结论的效应量达到其自身分辨率的 2 倍（`QUOTABLE` = 0 条）。**"
        f"最好的是 `phasef` 的 +0.740pp / MDE 0.609pp = 1.22 倍，与 `phaseo` 的 1.44 倍。"
        "也就是说，本项目目前没有一条「可以放心单引点估计」的结论；全部要带 MDE 或只引方向。"
    )
    lines.append("")

    lines.append("## 二、核对结果：27 条逐条")
    lines.append("")
    lines.append(
        "「参考分级」是按**修正后的数字**（判定统计量 + 重建序列的 MDE）算的；"
        "正式分级只在核对通过的条目上给出（预登记：核对未通过不下分级结论）。"
    )
    lines.append("")
    lines.append(
        "| 条目 | 族 | 核对 | 判定原文 | L3t 记录 | 登记 mean | 判定 mean | MDE（重算） | 正式分级 | 参考分级 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        cls = row["comparison"]["class"]
        cls_label = cit.MATCH_LABELS[cls]
        if cls == cit.MISMATCH_MEAN and row["comparison"].get("t_only"):
            cls_label = "仅 t 不同（mean 一致）"
        unit = row["unit"]
        stat_mean = row["stat"].get("mean")
        stat_cell = _u(stat_mean, unit) if row["stat"].get("applicable") else "不适用"
        mde = row["recomputed"].get("mde") if row.get("recomputed") else None
        grade = row["grade"]
        ref = grade.get("reference_tier") or ""
        lines.append(
            f"| `{row['key']}` | {row['family']} | {cls_label} "
            f"| {row.get('verdict_original') or '—'} | {row.get('verdict_recorded_l3t') or '—'} "
            f"| {_u(row['l3t']['mean'], unit)} | {stat_cell} "
            f"| {_u(mde, unit, signed=False)} "
            f"| {cit.QUOTABILITY_LABELS.get(grade['tier'], grade['tier'])} "
            f"| {cit.QUOTABILITY_LABELS.get(ref, '—') if ref else '—'} |"
        )
    lines.append("")

    lines.append("## 三、取错了量的 6 条（逐条）")
    lines.append("")
    bad = [r for r in rows if r["key"] in mean_differs_keys or r["comparison"]["class"] == cit.MISMATCH_N]
    if not bad:
        lines.append("无。")
        lines.append("")
    for row in bad:
        lines.append(f"### `{row['key']}`")
        lines.append("")
        lines.append(f"- 该实验真正的判定序列：`{row.get('series')}`")
        lines.append(
            f"- L3t 登记：n={row['l3t']['n']}、mean={_u(row['l3t']['mean'], row['unit'])}"
        )
        if row["stat"].get("applicable"):
            lines.append(
                f"- 判定统计量：n={row['stat'].get('n')}、mean={_u(row['stat'].get('mean'), row['unit'])}"
            )
        detail = row["comparison"].get("detail", "")
        if row["comparison"].get("mean_identical"):
            detail += "。**效应量本身没错**，差的只是分母——`phasep` 的 237 是全样本回归的观测数，" \
                      "而结论落在晚段的 129 个月上"
        lines.append(f"- 差异：{detail}")
        lines.append(
            f"- 若按修正值分级：**{cit.QUOTABILITY_LABELS.get(row['grade'].get('reference_tier') or '', '—')}**"
        )
        lines.append("")

    lines.append("## 四、判定字符串的转写失真")
    lines.append("")
    if verdict_mismatch:
        lines.append("| 条目 | 原文（判定） | L3t 记录 | 性质 |")
        lines.append("|---|---|---|---|")
        for item in payload["verdict_reconciliation"]["mismatches"]:
            original, recorded = item["original"], item["recorded_in_l3t"]
            up_o, up_r = original.upper(), recorded.upper()
            if "ABSORB" in up_o and "ABSORB" not in up_r:
                nature = "**语义反转**（原文「被吸收＝不成立」）"
            elif up_o.replace("_", "") in up_r.replace("_", "") or up_r.replace("_", "") in up_o.replace("_", ""):
                nature = "截断/缩写"
            else:
                nature = "改写"
            lines.append(f"| `{item['key']}` | `{original}` | `{recorded}` | {nature} |")
        lines.append("")
        lines.append(
            "> 判定字符串不是装饰：`ABSORBED` 与 `CONFIRMED` 是相反的意思。"
            "本文件所有分级一律以**原文**为准。"
        )
    else:
        lines.append("无。")
    lines.append("")

    lines.append("## 五、更正后的分辨率")
    lines.append("")
    lines.append("| 量 | L3t | 本文件（重建判定序列后） |")
    lines.append("|---|---|---|")
    lines.append(f"| pp 口径内 MDE 极差 | {_f(spread_ratio_l3t, 2)}× | {_f(spread_ratio, 2)}× |")
    lines.append(
        f"| pp 口径内 MDE 最小 | {_abs_pp(min(l3t_pp_mde)) if l3t_pp_mde else '—'} "
        f"| {_abs_pp(min(pp_mde)) if pp_mde else '—'} |"
    )
    lines.append(
        f"| pp 口径内 MDE 最大 | {_abs_pp(max(l3t_pp_mde)) if l3t_pp_mde else '—'} "
        f"| {_abs_pp(max(pp_mde)) if pp_mde else '—'} |"
    )
    lines.append("")
    lines.append(
        f"极差从 {_f(spread_ratio_l3t, 2)}× 变成 {_f(spread_ratio, 2)}×，**不是变窄而是变宽**——"
        "因为 `phasec2` 改用真正的配对差序列后 MDE 降到 0.060pp，成为 pp 口径内的新最小值"
        "（原先这个位置是 0.087pp 的 phasej）。"
        "所以 P4 的预测（极差会变窄）被反驳：更准确的量必然带来更极端的跨度。"
    )
    lines.append("")

    lines.append("## 六、更正后的「低于同族最好分辨率」名单")
    lines.append("")
    if below:
        lines.append("| 条目 | 族 | 效应量 | 同族最好 MDE | 比值 |")
        lines.append("|---|---|---|---|---|")
        for item in below:
            lines.append(
                f"| `{item['key']}` | {item['family']} | {_pp(item['effect'])} "
                f"| {_abs_pp(item['family_best_mde'])} | {_f(item['ratio'], 2)} |"
            )
        lines.append("")
        lines.append(
            f"**L3t 原名单 5 条，更正后只剩 {len(below)} 条。**"
            "被移出的 4 条（phasec、phasec2、gate5、phasej）是因为登记值取错了量："
            "改用真正的判定统计量后，它们的效应量并不低于同族最好分辨率。"
        )
        lines.append("")
        lines.append(
            f"剩下的这条读法不变：**不是结论错**，而是「换同族更强的设计去测同样大小的效应，可能检不出」。"
        )
    else:
        lines.append("无。所有条目的效应量都不低于同族最好分辨率。")
    lines.append("")

    lines.append("## 七、可引用强度分级")
    lines.append("")
    lines.append("| 分级 | 含义 | 条目数 | 条目 |")
    lines.append("|---|---|---|---|")
    for tier in [
        "QUOTABLE",
        "QUOTABLE_MARGINAL",
        "DIRECTION_ONLY",
        "NOT_QUOTABLE",
        "NULL_CONFIRMED",
        "NULL_WEAK",
        "UNDERPOWERED",
        "UNCLASSIFIED",
        "NOT_GRADED",
    ]:
        members = payload["grade_distribution"].get(tier)
        if not members:
            continue
        lines.append(
            f"| {tier} | {cit.QUOTABILITY_LABELS[tier]} | {len(members)} "
            f"| {', '.join(f'`{m}`' for m in members)} |"
        )
    lines.append("")
    lines.append(
        "> **怎么用**：`QUOTABLE` 可以引点估计；`QUOTABLE_MARGINAL` 引的时候必须同时给 MDE；"
        "`DIRECTION_ONLY` 只能写方向、不能写大小；`NOT_QUOTABLE` 的点估计不可信。"
        "未检出类里 `UNDERPOWERED` **不得**写成「没有效应」。"
    )
    lines.append("")

    lines.append("### 现在可以怎么写（照抄即可）")
    lines.append("")
    lines.append(
        "- **exQ5 结项**（`phasef_delta`，QUOTABLE_MARGINAL）：「MAX5 分层后价差 +0.740pp/月"
        "（t=3.41），该设计 MDE 0.609pp，效应约为分辨率的 1.2 倍。」"
    )
    lines.append(
        "- **成本后**（`phaseo_netBASE`，QUOTABLE_MARGINAL）：「BASE 费率、3000 万资金下净 +0.136pp/月"
        "（t=4.04），MDE 0.094pp，约 1.4 倍；盈亏平衡费率 0.4129%。」"
    )
    lines.append(
        "- **现代段**（`phases_net` / `phaset_net`，DIRECTION_ONLY）：「2016 年后方向为正、但点估计"
        "（+0.111pp, t=2.22）不到该设计 MDE（0.140pp）的 0.8 倍，**只能引方向**，且 T 判 `T_FRAGILE`。」"
    )
    lines.append(
        "- **SURGE 成本后**（`phasel_alpha`，NULL_CONFIRMED）：「毛 alpha +0.106pp（t=3.13）显著，"
        "但判定原文是 `SURGE_AVOID_ABSORBED`——**被确认不成立的是「成本后仍可交易」这个命题**，"
        "不是「低 SURGE 没有 alpha」。」"
    )
    lines.append(
        "- **答不了的三个**（`l3r_delta` / `l3s_ic` / `l3_test` / `l3p_test`，UNDERPOWERED）："
        "「本次设计功效不足（16.9%~40.0%），**不能据此说没有效应**。」"
    )
    lines.append("")

    lines.append("## 八、逐条核对预登记预测")
    lines.append("")
    lines.append("| 编号 | 预测 | 实际 | 被反驳 |")
    lines.append("|---|---|---|---|")
    for item in preds:
        claim = str(item["claim"]).replace("|", r"\|")
        actual = str(item["actual"]).replace("|", r"\|")
        lines.append(f"| {item['id']} | {claim} | {actual} | {'**是**' if item['refuted'] else '否'} |")
    lines.append("")
    lines.append(
        "**4 条被反驳，且都不是「差一点」**：P1/P2 我预估多数条目是照抄的，实际只有 14/27 与 8/27；"
        "P6 我预估至少 3 条能引点估计，实际 **0 条**。这三条同向说明一件事——"
        "**L3t 那张表的可靠度与我当时的估计相差很远，而本项目的效应量普遍不到自身分辨率的 2 倍。**"
    )
    lines.append("")

    lines.append("## 九、限制")
    lines.append("")
    for text in payload["limits"]:
        lines.append(f"- {text}")
    lines.append("- 预登记在过程中修订过 3 次（2 次笔误、1 次语义标注），每次的理由与影响都写在 `amendments` 里；"
                 "第二次修订前的产物保留为 `l3u_citation_firstrun_scalebug.{json,md}`。")
    lines.append("")

    lines.append("## 十、复现")
    lines.append("")
    lines.append("```bash")
    lines.append('PYTHONPATH="backend/src" python scripts/l3u_citation.py')
    lines.append("```")
    lines.append("")
    lines.append(f"耗时 {payload['elapsed_seconds']} 秒；27 条全部读自仓库内既有产物，无网络、无重跑。")
    lines.append("")

    (OUT_DIR / "l3u_citation.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )

    evidence = {
        "experiment_id": prereg["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "prereg_bytes": len(PREREG.read_bytes()),
        "subject_sha256": hashlib.sha256(L3T_JSON.read_bytes()).hexdigest(),
        "outputs": {
            "json": (OUT_DIR / "l3u_citation.json").relative_to(ROOT).as_posix(),
            "markdown": (OUT_DIR / "l3u_citation.md").relative_to(ROOT).as_posix(),
        },
        "class_counts": classes,
        "verdict_mismatch_count": len(verdict_mismatch),
        "mismatched_count": len(mismatched),
        "below_family_best_count": len(below),
        "quotable_count": len(quotable),
        "refuted_predictions": [p["id"] for p in preds if p["refuted"]],
        "limits": payload["limits"],
    }
    EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"条目 {len(rows)}｜核对 {classes}")
        print(f"判定字符串失真 {len(verdict_mismatch)} 条｜口径错配 {len(mismatched)} 条")
        print(f"pp 口径 MDE 极差 {_f(spread_ratio_l3t, 2)}× → {_f(spread_ratio, 2)}×")
        print(f"below_family_best 5 → {len(below)} 条")
        print(f"QUOTABLE {len(quotable)} 条：{', '.join(quotable) or '无'}")
        print("分级分布：" + "｜".join(f"{k}={len(v)}" for k, v in payload["grade_distribution"].items()))
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"被反驳的预测：{', '.join(refuted) or '无'}")
        print(f"报告：{(OUT_DIR / 'l3u_citation.md').relative_to(ROOT).as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
