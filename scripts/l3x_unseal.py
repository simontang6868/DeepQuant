"""L3x：仅 t 口径不同的 6 条封印条目解封。

跑法（仓库根）：
    PYTHONPATH="backend/src" python scripts/l3x_unseal.py [--quiet]

与 L3w 同一套闸门（同源三关 + NW 滞后网格），只把对象换成 ``t_only`` 的条目。
L3u 与 L3w 产物都在启动时校验 sha。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import citation as cit  # noqa: E402
from deepquant.gates import unseal as uns  # noqa: E402

PREREG = ROOT / "docs" / "gates" / "l3x_prereg.json"
PREREG_SHA = "ffdff3b75f9c3f16029e34b205c40300518a82bb6758af102968cbefe0438dfd"

L3U_JSON = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
L3U_SHA = "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b"
L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
L3W_JSON = ROOT / "reports" / "gates" / "H-L3W-UNSEAL" / "l3w_unseal.json"
L3W_SHA = "ce10baa7dabb1e1219177448e7e4ba6d1d81769cbcbde40c8dae85860445b6c0"

OUT_DIR = ROOT / "reports" / "gates" / "H-L3X-TONLY"
EVIDENCE = ROOT / "reports" / "L3X_TONLY_EVIDENCE.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _esc(text: object) -> str:
    return str(text).replace("|", r"\|")


def _f(value: object, digits: int = 4) -> str:
    if not isinstance(value, (int, float)) or value != value:
        return "—"
    return f"{value:.{digits}f}"


def _check_predictions(payload: dict, decisions: list[dict], merge: dict) -> list[dict]:
    hard = payload["hardcoded"]
    unsealed = [d for d in decisions if d["status"] == "UNSEALED"]
    by_key = {d["key"]: d for d in decisions}
    g6 = by_key.get("gate6_d", {})
    g2 = by_key.get("gate2_d", {})
    g6_ratio = g6.get("ratio_primary")
    observed = {
        "P1": {
            "n_same_source": sum(1 for d in decisions if d["same_source"]["ok"]),
            "n_total": len(decisions),
        },
        "P2": {"n_unsealed": len(unsealed)},
        "P3": {"tier": g6.get("tier"), "ratio": g6_ratio},
        "P4": {
            "tier": g2.get("tier"),
            "tiers": (g2.get("stability") or {}).get("tiers"),
        },
        "P5": {"NOT_GRADED": merge["counts_after"].get("NOT_GRADED")},
        "P6": {
            "QUOTABLE": merge["counts_after"].get("QUOTABLE"),
            "QUOTABLE_MARGINAL": merge["counts_after"].get("QUOTABLE_MARGINAL"),
        },
    }
    holds = {
        "P1": observed["P1"]["n_same_source"] == hard["n_sealed"],
        "P2": len(unsealed) == hard["n_expected_unsealed"],
        "P3": g6.get("tier") == "QUOTABLE_MARGINAL"
        and g6_ratio is not None
        and 1.5 <= g6_ratio <= 1.8,
        "P4": g2.get("tier") == "UNDERPOWERED"
        and set(((g2.get("stability") or {}).get("tiers") or {}).values()) == {"UNDERPOWERED"},
        "P5": merge["counts_after"].get("NOT_GRADED") == hard["expected_not_graded_after"],
        "P6": merge["counts_after"].get("QUOTABLE") == hard["expected_quotable_after"]
        and merge["counts_after"].get("QUOTABLE_MARGINAL")
        == hard["expected_quotable_marginal_after"],
    }
    rows = []
    for pred in payload["predictions"]:
        pid = pred["id"]
        rows.append(
            {
                "id": pid,
                "statement": pred["statement"],
                "quantity": pred["quantity"],
                "observed": observed[pid],
                "holds": holds[pid],
                "refuted": not holds[pid],
            }
        )
    return rows


def _render_md(
    payload: dict,
    decisions: list[dict],
    merge: dict,
    preds: list[dict],
    grid_table: dict,
    lags: list[int],
) -> str:
    refuted = [p["id"] for p in preds if p["refuted"]]
    unsealed = [d for d in decisions if d["status"] == "UNSEALED"]
    sealed = [d for d in decisions if d["status"] != "UNSEALED"]

    lines: list[str] = []
    lines.append("# L3x 仅 t 口径不同的 6 条：解封复核")
    lines.append("")
    lines.append(
        f"> 预登记 `docs/gates/l3x_prereg.json` sha256 `{PREREG_SHA[:12]}…`（脚本启动校验）"
        f"｜对照 L3u `{L3U_SHA[:12]}…` 与 L3w `{L3W_SHA[:12]}…`｜**只读审计，不重跑策略**"
    )
    lines.append("")
    lines.append("## 一、结论先行")
    lines.append("")
    lines.append(
        f"这 6 条的「核对未通过」与 L3w 那 6 条性质不同：**它们的均值与分母本来就逐位一致**，"
        f"唯一差异是判定用 iid t、登记表用 NW(3) t。{len(unsealed)} 条解封、{len(sealed)} 条维持封印。"
    )
    lines.append("")
    g6 = next((d for d in decisions if d["key"] == "gate6_d"), None)
    if g6:
        lines.append(
            f"最高的 `gate6_d` 解封为 `{g6['tier']}`（|效应|/MDE = {_f(g6['ratio_primary'], 3)}），"
            "全项目 `QUOTABLE_MARGINAL` 因此变为 4 条——**但这 6 条没有一条产生新的强结论**，"
            "它们的效应量本来就在 1~2 倍或以下。"
        )
        lines.append("")
    lines.append(
        f"合并 L3w + L3x 后：`NOT_GRADED` {merge['counts_before'].get('NOT_GRADED', 0)} → "
        f"**{merge['counts_after'].get('NOT_GRADED', 0)}**，"
        f"`QUOTABLE` {merge['quotable_before']} → {merge['quotable_after']}，"
        f"共 {merge['n_changed']} 条分级发生变化。"
    )
    lines.append("")

    lines.append("## 二、6 条逐条")
    lines.append("")
    lines.append("| 条目 | 均值同源 | t 同源 | 分母同源 | ratio(NW3) | 解封分级 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in decisions:
        s = d["same_source"]
        lines.append(
            f"| `{d['key']}` | {'✓' if s['mean_match'] else '✗'} | {s['t_match'] or '✗'} | "
            f"{'✓' if s['n_match'] else '✗'} | {_f(d['ratio_primary'], 3)} | {d['tier']} | {d['status']} |"
        )
    lines.append("")

    lines.append("## 三、t 同源意味着什么")
    lines.append("")
    lines.append(
        "这 6 条的 `stat.t` 大部分等于重建序列的 **t_iid**，而 L3t 登记表记的是 **t_nw3**。"
        "两者差 0.05~0.25，`mean` 完全一致——所以 L3u 判的是「t 的估计量不同」，不是「取错了量」。"
        "把这种情况永久封印，等于要求所有结论必须用 NW(3) 报告，而 Gate 1~6 的原始 summary 只给 iid。"
    )
    lines.append("")
    lines.append(
        "**但 `t_only` 这个标记不等于「t 只是换了个口径」。** L3u 的 t_only 只比较「登记表的 t_nw3」"
        "与「summary 的 t」，没有比较「summary 的 t」与「重算的 t_iid / t_nw3」。"
        "所以被标成 t_only 的条目里，仍可能有 t 与两种标准误都对不上、甚至 summary 没给 n 的情况——"
        "本阶段就有 1 条是这样，被 U2/U3 拦下。这是闸门比标记更严格的地方，也是「标记不能替代核对」的直接证据。"
    )
    lines.append("")
    if sealed:
        lines.append("仍未解封：")
        lines.append("")
        for d in sealed:
            lines.append(f"- `{d['key']}`：" + "；".join(d["reasons"]))
        lines.append("")

    lines.append("## 四、分级稳定性：NW 滞后网格")
    lines.append("")
    lines.append("| 条目 | " + " | ".join(f"L={lag}" for lag in lags) + " | 稳定 |")
    lines.append("|---" * (len(lags) + 2) + "|")
    for d in decisions:
        tiers = (d["stability"] or {}).get("tiers") or {}
        cells = " | ".join(_esc(tiers.get(lag, "—")) for lag in lags)
        lines.append(f"| `{d['key']}` | {cells} | {'是' if d['stability']['stable'] else '否'} |")
    lines.append("")
    lines.append("各档 ratio（= |效应| / MDE，detected=True 才看这一列）：")
    lines.append("")
    lines.append("| 条目 | " + " | ".join(f"L={lag}" for lag in lags) + " |")
    lines.append("|---" * (len(lags) + 1) + "|")
    for d in decisions:
        row = grid_table.get(d["key"], {})
        effect = abs(d["effect"])
        cells = " | ".join(
            _f(effect / row[lag]["mde"], 3) if row.get(lag, {}).get("mde") else "—" for lag in lags
        )
        lines.append(f"| `{d['key']}` | {cells} |")
    lines.append("")

    lines.append("## 五、全项目合并（L3w + L3x）")
    lines.append("")
    lines.append("| 分级 | L3u 原始 | L3w+L3x 后 |")
    lines.append("|---|---|---|")
    keys = sorted(set(merge["counts_before"]) | set(merge["counts_after"]))
    for tier in keys:
        lines.append(
            f"| {tier} | {merge['counts_before'].get(tier, 0)} | {merge['counts_after'].get(tier, 0)} |"
        )
    lines.append("")
    lines.append("| 条目 | 原始 | 现在 |")
    lines.append("|---|---|---|")
    for c in merge["changed"]:
        lines.append(f"| `{c['key']}` | {c['before']} | {c['after']} |")
    lines.append("")
    lines.append(
        "剩下的 `NOT_GRADED` 才是真正不该分级的：**2 条不同源**（`phasej_netA` 资金档不同、"
        "`phasep_late` 回归系数）＋ **3 条判定非单一均值**（`phasem_spread`/`phaser_alpha`/`phasev_net`）。"
    )
    lines.append("")

    lines.append("## 六、预测对照（先于测量写定）")
    lines.append("")
    lines.append("| # | 预测 | 实测 | 结论 |")
    lines.append("|---|---|---|---|")
    for p in preds:
        lines.append(
            f"| {p['id']} | {_esc(p['statement'])} | {_esc(json.dumps(p['observed'], ensure_ascii=False))} | "
            f"{'成立' if p['holds'] else '**被反驳**'} |"
        )
    lines.append("")
    lines.append(f"被反驳：{('、'.join(refuted)) if refuted else '无'}。被反驳的预测原样保留，不改判据（R5）。")
    lines.append("")

    lines.append("## 七、L3v 卡片补丁")
    lines.append("")
    if unsealed:
        lines.append("| 条目 | 卡片现值 | 建议值 |")
        lines.append("|---|---|---|")
        for d in unsealed:
            lines.append(f"| `{d['key']}` | NOT_GRADED | {d['tier']} |")
        lines.append("")
    lines.append(
        "与 L3w 的补丁一起合并进 `docs/gates/l3v_claims.json` 后重跑 L3v 闸门；"
        "合并时 L3v 的 `unseal_source` 需要同时指向 L3w 与 L3x 两个产物。"
    )
    lines.append("")

    lines.append("## 八、限制")
    lines.append("")
    for item in payload["disclosures"]:
        lines.append(f"- {item}")
    lines.append(
        "- 解封只代表「这条结论的效应量可以被分级」，不代表它可交易；窗口、成本口径、"
        "横截面与账户收益的区别依旧适用。"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3x t_only 解封复核")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    cit.check_prereg_sha(PREREG, PREREG_SHA)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))

    for path, expected, label in (
        (L3U_JSON, L3U_SHA, "L3u"),
        (L3W_JSON, L3W_SHA, "L3w"),
    ):
        actual = _sha(path)
        if actual != expected:
            raise uns.UnsealError(f"{label} 产物已被改动：期望 {expected}，实际 {actual}；拒跑")

    l3u = json.loads(L3U_JSON.read_text(encoding="utf-8"))
    l3w = json.loads(L3W_JSON.read_text(encoding="utf-8"))
    l3u_prereg = cit.load_prereg(L3U_PREREG)
    entries = {e["key"]: e for e in l3u_prereg["entries"]}
    rules = l3u_prereg.get("quotability_rules") or {}
    lags = [int(x) for x in payload["hardcoded"]["lags"]]

    rows = uns.select_sealed(l3u["rows"], mode="t_only")
    if len(rows) != payload["hardcoded"]["n_sealed"]:
        raise uns.UnsealError(
            f"t_only 条目数不符：期望 {payload['hardcoded']['n_sealed']}，实际 {len(rows)}"
        )

    decisions: list[dict] = []
    grid_table: dict[str, dict[int, dict]] = {}
    for row in rows:
        entry = entries.get(row["key"])
        if entry is None:
            raise uns.UnsealError(f"[{row['key']}] L3u 预登记里没有该条目")
        rebuilt = cit.rebuild_series(entry, ROOT)
        grid = uns.mde_grid(rebuilt.values, lags=lags)
        decision = uns.decide(row, grid, rules=rules)
        decision["series_n"] = rebuilt.n
        decision["series"] = rebuilt.description
        decision["detected"] = row.get("detected")
        decision["effect"] = float(row["recomputed"]["mean"])
        decision["l3u_grade"] = (row.get("grade") or {}).get("tier")
        decisions.append(decision)
        grid_table[row["key"]] = {
            lag: {
                "mde": stats.get("mde"),
                "se_nw": stats.get("se_nw"),
                "t_nw": stats.get("t_nw3"),
                "power": stats.get("power"),
            }
            for lag, stats in grid.items()
        }

    # 合并必须同时含 L3w 的 4 条，否则会把它们当成仍是 NOT_GRADED
    combined = list(l3w["decisions"]) + [
        {"key": d["key"], "status": d["status"], "tier": d["tier"]} for d in decisions
    ]
    merge = uns.merge_project_tiers(l3u["rows"], combined)
    preds = _check_predictions(payload, decisions, merge)

    patches = [
        {
            "key": d["key"],
            "from": "NOT_GRADED",
            "to": d["tier"],
            "quotability_note": d["reasons"][0],
            "ratio_primary": d["ratio_primary"],
            "source": payload["experiment_id"],
        }
        for d in decisions
        if d["status"] == "UNSEALED"
    ]

    elapsed = time.perf_counter() - started
    result = {
        "experiment_id": payload["experiment_id"],
        "audit_only": True,
        "prereg_sha256": PREREG_SHA,
        "subject_l3u_sha256": L3U_SHA,
        "subject_l3w_sha256": L3W_SHA,
        "elapsed_seconds": round(elapsed, 3),
        "mode": "t_only",
        "lags": lags,
        "primary_lag": uns.PRIMARY_LAG,
        "n_sealed": len(decisions),
        "n_unsealed": sum(1 for d in decisions if d["status"] == "UNSEALED"),
        "n_stay_sealed": sum(1 for d in decisions if d["status"] != "UNSEALED"),
        "decisions": decisions,
        "grid": grid_table,
        "merge": merge,
        "patches": patches,
        "predictions": preds,
        "status": "UNSEAL_REVIEW_DONE",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3x_unseal.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT_DIR / "l3v_claims_patch.json").write_text(
        json.dumps(
            {
                "generated_from": payload["experiment_id"],
                "note": "与 L3w 的补丁一起合并；合并后必须重跑 L3v 闸门",
                "patches": patches,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md = _render_md(payload, decisions, merge, preds, grid_table, lags)
    (OUT_DIR / "l3x_unseal.md").write_text(md, encoding="utf-8", newline="\n")

    evidence = {
        "experiment_id": payload["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "subject_l3u_sha256": L3U_SHA,
        "subject_l3w_sha256": L3W_SHA,
        "outputs": {
            "json": (OUT_DIR / "l3x_unseal.json").relative_to(ROOT).as_posix(),
            "markdown": (OUT_DIR / "l3x_unseal.md").relative_to(ROOT).as_posix(),
            "patch": (OUT_DIR / "l3v_claims_patch.json").relative_to(ROOT).as_posix(),
        },
        "n_unsealed": result["n_unsealed"],
        "n_stay_sealed": result["n_stay_sealed"],
        "counts_after": merge["counts_after"],
        "n_changed_total": merge["n_changed"],
        "refuted_predictions": [p["id"] for p in preds if p["refuted"]],
    }
    EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    if not args.quiet:
        print(f"t_only 条目 {len(decisions)}｜解封 {result['n_unsealed']}｜维持封印 {result['n_stay_sealed']}")
        for d in decisions:
            print(
                f"  {d['key']:<15} {d['status']:<12} {d['tier']:<18} "
                f"ratio={_f(d['ratio_primary'], 3):>6}  t={d['same_source']['t_match']}  "
                f"stable={d['stability']['stable']}"
            )
        print(f"合并后：{merge['counts_after']}")
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"被反驳的预测：{', '.join(refuted) or '无'}")
        print(f"报告：{(OUT_DIR / 'l3x_unseal.md').relative_to(ROOT).as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
