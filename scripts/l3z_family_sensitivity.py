#!/usr/bin/env python
"""L3z 附：检验家族的定义复核与敏感性分析。

回答三件事：

1. 27 条登记结论里，哪些是**同一次检验**（数值逐位相同）、哪些是**多门槛判定**（没有单一 p）、
   哪些真正进入了多重检验家族。
2. m 取 24（数值指纹去重）、27（登记条目）、分族 三种口径下，三种校正的结论分别是什么变化。
3. 明确「序列去重」**不等于**「统计独立」——24 是不同数值序列的数目，不是独立检验数。

用法：
    PYTHONPATH=backend/src python scripts/l3z_family_sensitivity.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from deepquant.gates import multiplicity as mp

ROOT = Path(__file__).resolve().parents[1]
L3U = ROOT / "reports/gates/H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
L3V = ROOT / "docs" / "gates" / "l3v_claims.json"  # 结论卡片（含 quotability），非 L3v 产物
L3W = ROOT / "reports/gates/H-L3W-UNSEAL" / "l3w_unseal.json"
L3X = ROOT / "reports/gates/H-L3X-TONLY" / "l3x_unseal.json"
OUT_MD = ROOT / "reports" / "L3Z_FAMILY_SENSITIVITY.md"
OUT_JSON = ROOT / "reports" / "L3Z_FAMILY_SENSITIVITY.json"

ESTIMATOR = "t_nw3"


def fingerprint(row) -> tuple:
    rc = row.get("recomputed") or {}
    m, t, n = rc.get("mean"), rc.get("t_nw3"), rc.get("n")
    if m is None or t is None or n is None:
        return ("key", str(row.get("key")))
    return (round(float(m), 10), round(float(t), 10), int(n))


def build_inventory(l3u, cards, l3w, l3x) -> tuple[list[dict], dict]:
    quotability = {c["key"]: c["quotability"] for c in cards["claims"]}
    decisions = {}
    for payload in (l3w, l3x):
        for d in payload.get("decisions") or []:
            decisions.setdefault(d["key"], d)

    groups: dict[tuple, list[str]] = {}
    for row in l3u["rows"]:
        groups.setdefault(fingerprint(row), []).append(row["key"])

    inv = []
    for row in l3u["rows"]:
        key = row["key"]
        rc = row.get("recomputed") or {}
        stat = row.get("stat") or {}
        cmp_cls = (row.get("comparison") or {}).get("class")
        fp = fingerprint(row)
        dup_group = groups[fp]
        multi_gate = (not stat.get("applicable", True)) or cmp_cls == "NOT_APPLICABLE"
        scored = mp.eligible([row])[0]
        counts_only = bool(scored.get("_counts_only"))
        t = rc.get(ESTIMATOR)
        n = rc.get("n")
        p = mp.t_two_sided_p(t, n - 1) if (t is not None and n) else None
        dec = decisions.get(key)
        inv.append(
            {
                "key": key,
                # 原始假设：预登记未登记逐条假设，无法从产物恢复，一律标 UNKNOWN。
                # 不能用 verdict_original 反推——L3u 已发现 13 条判定字符串失真。
                "original_hypothesis": "UNKNOWN",
                "hypothesis_note": (
                    "预登记未逐条登记假设；判定字符串已被证明有 13 条失真，不可反推"
                ),
                "statistic": {
                    "mean": rc.get("mean"),
                    "t_nw3": rc.get("t_nw3"),
                    "t_iid": rc.get("t_iid"),
                    "mde": rc.get("mde"),
                },
                "series": row.get("series"),
                "n": n,
                "p_nw3": p,
                "se_method": "NW(3) 主口径 / iid 对照",
                "family": row.get("family"),
                "shares_sequence_with": [k for k in dup_group if k != key],
                "is_duplicate_registration": bool(
                    len(dup_group) > 1 and dup_group.index(key) > 0
                ),
                "is_multi_gate": multi_gate,
                "comparison_class": cmp_cls,
                "status_l3u_tier": (row.get("grade") or {}).get("tier"),
                "status_unseal": (
                    None if dec is None else {"status": dec.get("status"), "class": dec.get("class")}
                ),
                "status_l3y": quotability.get(key),
                "in_family": not counts_only,
                "family_role": "counts_only" if counts_only else "scored",
                "inclusion_reason": (
                    "判定由多门槛给出，无单一 p 值；计入 m 但不参与评分"
                    if counts_only
                    else "具备可核实的单一均值与 t，参与校正"
                ),
            }
        )
    return inv, groups


def run_scheme(rows, m, label, note):
    scored = mp.eligible(rows)
    run = mp.apply_corrections(scored, m=m, estimator=ESTIMATOR)
    return {
        "label": label,
        "m": run["m_used"],
        "n_scored": run["n_scored"],
        "n_counts_only": run["n_counts_only"],
        "note": note,
        "bonferroni": sorted(i["key"] for i in run["items"] if i["bonferroni"]),
        "holm": sorted(i["key"] for i in run["items"] if i["holm"]),
        "bh": sorted(i["key"] for i in run["items"] if i["bh"]),
        "verdict_counts": run["verdict_counts"],
    }


def main() -> int:
    started = time.perf_counter()
    l3u = json.loads(L3U.read_text(encoding="utf-8"))
    cards = json.loads(L3V.read_text(encoding="utf-8"))
    l3w = json.loads(L3W.read_text(encoding="utf-8"))
    l3x = json.loads(L3X.read_text(encoding="utf-8"))

    inv, groups = build_inventory(l3u, cards, l3w, l3x)

    # 五种计数口径的区分
    n_registered = len(l3u["rows"])
    n_distinct_series_paths = len({r.get("series") for r in l3u["rows"]})
    n_distinct_numeric = len(groups)
    n_multi_gate = sum(1 for i in inv if i["is_multi_gate"])
    n_scored = sum(1 for i in inv if i["in_family"])
    n_reported = len(cards["claims"])
    n_hypotheses = "UNKNOWN"

    schemes = [
        run_scheme(
            l3u["rows"],
            n_distinct_numeric,
            "m = 24（数值指纹去重，主口径）",
            "把逐位相同的重复登记合并为一次检验；被封印的检验仍计入 m",
        ),
        run_scheme(
            l3u["rows"],
            n_registered,
            "m = 27（登记条目数，替代口径）",
            "不做去重，每条登记都算一次检验；这是最保守的上界",
        ),
    ]
    # 分族：按 L3t 定义的口径族，各族内用自己的 m
    by_family_json = mp.assemble(l3u["rows"], estimator=ESTIMATOR)["by_family"]
    family_schemes = []
    for fam, fr in sorted(by_family_json.items()):
        family_schemes.append(
            {
                "label": f"分族 · {fam}",
                "m": fr["m_used"],
                "n_scored": fr["n_scored"],
                "n_counts_only": fr.get("n_counts_only", 0),
                "note": "族内校正：同一口径族内的假设更相关，族内 m 小于全族",
                "bonferroni": sorted(i["key"] for i in fr["items"] if i["bonferroni"]),
                "holm": sorted(i["key"] for i in fr["items"] if i["holm"]),
                "bh": sorted(i["key"] for i in fr["items"] if i["bh"]),
                "verdict_counts": fr["verdict_counts"],
            }
        )

    result = {
        "experiment_id": "H-L3Z-FAMILY-SENSITIVITY",
        "counts": {
            "登记条目数": n_registered,
            "不同序列路径数": n_distinct_series_paths,
            "不同数值序列数": n_distinct_numeric,
            "不同原假设数": n_hypotheses,
            "多门槛结论数": n_multi_gate,
            "实际参与校正的检验数": n_scored,
            "最终报告的结论数": n_reported,
            "⚠️ 说明": (
                "「不同数值序列数」不等于「统计独立的检验数」。"
                "去重只合并了逐位相同的重复登记；余下检验之间仍可能高度相关"
                "（同期、同池、同族）。因此 24 是不同数值序列的数目，"
                "不是独立检验数的估计，也不应被当作相关结构的证据。"
            ),
        },
        "duplicate_groups": [
            {"keys": keys, "numeric_fingerprint": [list(map(str, g))]}
            for g, keys in groups.items()
            if len(keys) > 1
        ],
        "inventory": inv,
        "schemes": schemes,
        "family_schemes": family_schemes,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    OUT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    lines = []
    lines.append("# L3z 附：检验家族定义复核与敏感性")
    lines.append("")
    lines.append("## 一、五种计数必须分开")
    lines.append("")
    lines.append("| 计数口径 | 数量 | 含义 |")
    lines.append("| --- | --- | --- |")
    for k, v in result["counts"].items():
        if k.startswith("⚠️"):
            continue
        lines.append(f"| {k} | {v} | |")
    lines.append("")
    lines.append(f"⚠️ **{result['counts']['⚠️ 说明']}**")
    lines.append("")
    lines.append(
        "「不同原假设数」标记为 **UNKNOWN**：预登记未逐条登记假设，"
        "而 L3u 已证明 13 条判定字符串失真，因此不能用判定字符串反推假设。此项不补全。"
    )
    lines.append("")
    lines.append("## 二、重复登记（数值逐位相同）")
    lines.append("")
    lines.append("| 组 | 成员 |")
    lines.append("| --- | --- |")
    for g in result["duplicate_groups"]:
        lines.append(f"| — | {'、'.join(g['keys'])} |")
    lines.append("")
    lines.append(
        "这 3 组是**同一次检验被登记了两次**，判定不同但数字逐位相同。"
        "把它们算成 6 次检验会凭空抬高其余结论的门槛。"
    )
    lines.append("")
    lines.append("## 三、逐条清单")
    lines.append("")
    lines.append(
        "| 结论 | 族 | n | p (NW3) | 共用序列 | 重复登记 | 多门槛 | 进入家族 | 排除/纳入理由 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i in inv:
        p = f"{i['p_nw3']:.6f}" if i["p_nw3"] is not None else "—"
        lines.append(
            f"| `{i['key']}` | {i['family']} | {i['n']} | {p} | "
            f"{'、'.join(i['shares_sequence_with']) or '—'} | "
            f"{'是' if i['is_duplicate_registration'] else '否'} | "
            f"{'是' if i['is_multi_gate'] else '否'} | "
            f"{'计分' if i['in_family'] else '只计名额'} | {i['inclusion_reason']} |"
        )
    lines.append("")
    lines.append("## 四、三种 m 口径下的校正结果")
    lines.append("")
    lines.append("| 口径 | m | 参与评分 | 只计名额 | Bonferroni | Holm | BH |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for s in schemes:
        lines.append(
            f"| {s['label']} | {s['m']} | {s['n_scored']} | {s['n_counts_only']} | "
            f"{len(s['bonferroni'])} | {len(s['holm'])} | {len(s['bh'])} |"
        )
    lines.append("")
    for s in schemes:
        lines.append(f"**{s['label']}**（{s['note']}）")
        lines.append("")
        lines.append(f"- Bonferroni 拒绝：{'、'.join(s['bonferroni']) or '无'}")
        lines.append(f"- Holm 拒绝：{'、'.join(s['holm']) or '无'}")
        lines.append(f"- BH 拒绝：{'、'.join(s['bh']) or '无'}")
        lines.append("")
    b24 = set(schemes[0]["bh"])
    b27 = set(schemes[1]["bh"])
    diff = b24 ^ b27
    lines.append(
        f"两口径的 BH 拒绝集合差异：{'、'.join(sorted(diff)) if diff else '无'}"
        "。Bonferroni 差异见上（phasel_alpha 在 m=24 下通过、m=27 下不通过）。"
    )
    lines.append("")
    lines.append("## 五、分族方案")
    lines.append("")
    lines.append("| 族 | m | 参与评分 | BH 拒绝 |")
    lines.append("| --- | --- | --- | --- |")
    for s in family_schemes:
        lines.append(
            f"| {s['label'].split('· ')[-1]} | {s['m']} | {s['n_scored']} | "
            f"{'、'.join(s['bh']) or '无'} |"
        )
    lines.append("")
    lines.append(
        "分族依据是 L3t 定义的**口径族**（横截面价差 / 单边 / 回归 / IC / 折），"
        "不是为了让更多条目通过而事后拆分。族内 m 更小，门槛更松，"
        "但族内假设的相关性也更高——BH 对正相关相对稳健，Bonferroni 则会因此偏保守。"
    )
    lines.append("")
    lines.append("## 六、BH 的适用性")
    lines.append("")
    lines.append(
        "BH 要求 p 值满足特定的依赖结构（独立或 PRDS）。"
        "本项目的检验之间**存在已知的正相关**（同池、同期、同一份序列被登记多次），"
        "但**没有做依赖结构的实证检验**。因此："
    )
    lines.append("")
    lines.append(
        "- 不能把「一般意义上正相关时 BH 相对稳健」当作已经证明满足所需条件；"
    )
    lines.append(
        "- 本轮把 BH 作为**参考口径**，把 Bonferroni / Holm 作为 FWER 口径并列报告；"
    )
    lines.append(
        "- 结论以三者一致的部分为准；只在 BH 下通过、FWER 下不通过的条目，"
        "引用时必须说明用的是 FDR 口径。"
    )
    lines.append("")
    lines.append("## 七、复现")
    lines.append("")
    lines.append("```bash")
    lines.append("PYTHONPATH=backend/src python scripts/l3z_family_sensitivity.py")
    lines.append("```")
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(f"登记 {n_registered}｜数值序列 {n_distinct_numeric}｜多门槛 {n_multi_gate}｜参与校正 {n_scored}")
    print(f"m=24 BH 拒绝 {len(schemes[0]['bh'])} 条｜m=27 BH 拒绝 {len(schemes[1]['bh'])} 条")
    print(f"产物：{OUT_MD.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
