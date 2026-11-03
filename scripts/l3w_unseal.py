"""L3w：封印条目解封复核 —— 把「L3t 抄错」与「判定量不同源」分开。

跑法（仓库根）：
    PYTHONPATH="backend/src" python scripts/l3w_unseal.py [--quiet]

只读 L3u 产物与各阶段判定序列，重算 NW 滞后网格；不重跑任何策略（R5）。
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

PREREG = ROOT / "docs" / "gates" / "l3w_prereg.json"
PREREG_SHA = "709d76ddf1766e107baa3109c0eb5d7da46539ccd46ee632e163cb0448d52477"

L3U_JSON = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
L3U_SHA = "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b"
L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"

OUT_DIR = ROOT / "reports" / "gates" / "H-L3W-UNSEAL"
EVIDENCE = ROOT / "reports" / "L3W_UNSEAL_EVIDENCE.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _esc(text: object) -> str:
    return str(text).replace("|", r"\|")


def _f(value: object, digits: int = 4) -> str:
    if not isinstance(value, (int, float)) or value != value:
        return "—"
    return f"{value:.{digits}f}"


def _check_predictions(payload: dict, decisions: dict, merge: dict) -> list[dict]:
    hard = payload["hardcoded"]
    unsealed = [d for d in decisions if d["status"] == "UNSEALED"]
    sealed = [d for d in decisions if d["status"] != "UNSEALED"]
    by_key = {d["key"]: d for d in decisions}

    def ratio(key):
        return by_key.get(key, {}).get("ratio_primary")

    p_phasec = by_key.get("phasec_d", {})
    p_c2 = by_key.get("phasec2_d", {})
    p_g3 = by_key.get("gate3_d", {})

    observed = {
        "P1": {"n_unsealed": len(unsealed), "keys": sorted(d["key"] for d in unsealed)},
        "P2": {"ratio": ratio("phasec_d"), "tier": p_phasec.get("tier")},
        "P3": {
            "stable": (p_phasec.get("stability") or {}).get("stable"),
            "tiers": (p_phasec.get("stability") or {}).get("tiers"),
        },
        "P4": {"stable": (p_c2.get("stability") or {}).get("stable")},
        "P5": {"tier": p_g3.get("tier"), "ratio": ratio("gate3_d")},
        "P6": {
            "quotable_after": merge["quotable_after"],
            "n_changed": merge["n_changed"],
        },
    }
    holds = {
        "P1": len(unsealed) == hard["n_expected_unsealed"]
        and sorted(d["key"] for d in unsealed)
        == ["gate3_d", "phasec2_d", "phasec3_d", "phasec_d"],
        "P2": bool(observed["P2"]["ratio"] is not None and observed["P2"]["ratio"] >= 2.0)
        and observed["P2"]["tier"] == "QUOTABLE",
        "P3": bool(observed["P3"]["stable"])
        and set((observed["P3"]["tiers"] or {}).values()) == {"QUOTABLE"},
        "P4": observed["P4"]["stable"] is False,
        "P5": observed["P5"]["tier"] == "DIRECTION_ONLY"
        and observed["P5"]["ratio"] is not None
        and 0.7 <= observed["P5"]["ratio"] <= 1.0,
        "P6": observed["P6"]["quotable_after"] == hard["expected_quotable_after"]
        and observed["P6"]["n_changed"] == hard["expected_n_changed"],
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
    _ = sealed
    return rows


def _render_md(
    payload: dict,
    decisions: list[dict],
    merge: dict,
    preds: list[dict],
    grid_table: dict,
    lags: list[int],
) -> str:
    by_status = {"UNSEALED": [], "STAY_SEALED": []}
    for d in decisions:
        by_status[d["status"]].append(d)
    refuted = [p["id"] for p in preds if p["refuted"]]
    unsealed = by_status["UNSEALED"]

    lines: list[str] = []
    lines.append("# L3w 封印条目解封复核：6 条里哪些其实从未不可核对")
    lines.append("")
    lines.append(
        f"> 预登记 `docs/gates/l3w_prereg.json` sha256 `{PREREG_SHA[:12]}…`（脚本启动校验）"
        f"｜对照 L3u `{L3U_SHA[:12]}…`（启动校验）｜**只读审计，不重跑策略**"
    )
    lines.append("")
    lines.append("## 一、结论先行")
    lines.append("")
    lines.append(
        f"**6 条「核对未通过」里，{len(unsealed)} 条从未不可核对，只是 L3t 登记时抄错了列或窗口。**"
        f"另外 {len(by_status['STAY_SEALED'])} 条不是抄错，而是**判定量与序列本来不是同一个东西**，"
        "拼在一起算 ratio 就是拼口径——这两种情况被 L3u 的同一条规则压成了同一格。"
    )
    lines.append("")
    if unsealed:
        top = max(unsealed, key=lambda d: d["ratio_primary"] or 0)
        lines.append(
            f"解封后全项目 `QUOTABLE` 从 {merge['quotable_before']} 条变为 "
            f"**{merge['quotable_after']} 条**；最高的是 `{top['key']}`，"
            f"|效应|/MDE = {_f(top['ratio_primary'], 4)}，分级 `{top['tier']}`。"
        )
        lines.append("")
        if merge["quotable_after"] > merge["quotable_before"]:
            lines.append(
                "也就是说：**我们不是没有可引用的点估计，而是把它锁在「核对未通过」这一格里没放出来。**"
                "反过来说，这条放出来之前已经在网格上验过 6 个滞后不至于跨桶（见 §四）。"
            )
            lines.append("")

    lines.append("## 二、6 条逐条")
    lines.append("")
    lines.append("| 条目 | 核对分类 | 均值同源 | t 同源 | 分母同源 | ratio(NW3) | 解封分级 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in decisions:
        s = d["same_source"]
        lines.append(
            f"| `{d['key']}` | {d['class']} | {'✓' if s['mean_match'] else '✗'} | "
            f"{s['t_match'] or '✗'} | {'✓' if s['n_match'] else '✗'} | "
            f"{_f(d['ratio_primary'], 3)} | {d['tier']} | {d['status']} |"
        )
    lines.append("")

    lines.append("## 三、为什么 B 类不能放")
    lines.append("")
    sealed = by_status["STAY_SEALED"]
    if sealed:
        for d in sealed:
            lines.append(f"### `{d['key']}`（{d['class']}）")
            lines.append("")
            for r in d["reasons"]:
                lines.append(f"- {r}")
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")
    lines.append(
        "这两条的 ratio 看起来也能算，但分子与分母来自不同的东西：一个把 3000 万档的判定均值"
        "配上 1000 万档序列的 MDE，另一个把回归系数的 t 配上序列均值的 MDE。"
        "**能算不等于该算**——放宽到这一步，分级就不再是「效应量相对自身分辨率」了。"
    )
    lines.append("")

    lines.append("## 四、分级稳定性：NW 滞后网格")
    lines.append("")
    lines.append(
        "同一序列、同一实现，只改滞后。**跨桶就不解封**——那说明结论对标准误口径敏感。"
    )
    lines.append("")
    header = "| 条目 | " + " | ".join(f"L={lag}" for lag in lags) + " | 稳定 |"
    lines.append(header)
    lines.append("|---" * (len(lags) + 2) + "|")
    for d in decisions:
        tiers = (d["stability"] or {}).get("tiers") or {}
        cells = " | ".join(_esc(tiers.get(lag, "—")) for lag in lags)
        lines.append(f"| `{d['key']}` | {cells} | {'是' if d['stability']['stable'] else '否'} |")
    lines.append("")
    lines.append("各档 MDE（pp，= 2.8016 × se_NW）：")
    lines.append("")
    lines.append("| 条目 | " + " | ".join(f"L={lag}" for lag in lags) + " |")
    lines.append("|---" * (len(lags) + 1) + "|")
    for d in decisions:
        row = grid_table.get(d["key"], {})
        cells = " | ".join(_f(row.get(lag, {}).get("mde"), 3) for lag in lags)
        lines.append(f"| `{d['key']}` | {cells} |")
    lines.append("")

    lines.append("## 五、解封后全项目分级")
    lines.append("")
    lines.append("| 分级 | 解封前 | 解封后 |")
    lines.append("|---|---|---|")
    keys = sorted(set(merge["counts_before"]) | set(merge["counts_after"]))
    for tier in keys:
        lines.append(
            f"| {tier} | {merge['counts_before'].get(tier, 0)} | {merge['counts_after'].get(tier, 0)} |"
        )
    lines.append("")
    lines.append("发生变化的条目：")
    lines.append("")
    lines.append("| 条目 | 解封前 | 解封后 |")
    lines.append("|---|---|---|")
    for c in merge["changed"]:
        lines.append(f"| `{c['key']}` | {c['before']} | {c['after']} |")
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
    lines.append(
        "L3v 的卡片与产物**不修改**（历史记录）。解封结果单独输出为 "
        "`l3v_claims_patch.json`；把它合并进 `docs/gates/l3v_claims.json` 并重跑 L3v 闸门，"
        "是另一个动作，需要单独的改动记录。"
    )
    lines.append("")
    if unsealed:
        lines.append("| 条目 | 卡片现值 | 建议值 |")
        lines.append("|---|---|---|")
        for d in unsealed:
            lines.append(f"| `{d['key']}` | NOT_GRADED | {d['tier']} |")
        lines.append("")

    lines.append("## 八、限制")
    lines.append("")
    for item in payload["disclosures"]:
        lines.append(f"- {item}")
    lines.append(
        "- B 类要保持可查而不是被忘掉：`phasej_netA` 与 `phasep_late` 仍列为封印，"
        "要分级必须先把对应资金档的序列单独导出（新实验）。"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3w 封印条目解封复核")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    cit.check_prereg_sha(PREREG, PREREG_SHA)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))

    actual = _sha(L3U_JSON)
    if actual != L3U_SHA:
        raise uns.UnsealError(f"L3u 产物已被改动：期望 {L3U_SHA}，实际 {actual}；对照基准变了，拒跑")
    l3u = json.loads(L3U_JSON.read_text(encoding="utf-8"))
    l3u_prereg = cit.load_prereg(L3U_PREREG)
    entries = {e["key"]: e for e in l3u_prereg["entries"]}
    rules = l3u_prereg.get("quotability_rules") or {}
    lags = [int(x) for x in payload["hardcoded"]["lags"]]

    sealed_rows = uns.select_sealed(l3u["rows"])
    if len(sealed_rows) != payload["hardcoded"]["n_sealed"]:
        raise uns.UnsealError(
            f"封印条目数不符：期望 {payload['hardcoded']['n_sealed']}，实际 {len(sealed_rows)}"
        )

    decisions: list[dict] = []
    grid_table: dict[str, dict[int, dict]] = {}
    for row in sealed_rows:
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
        decision["l3u_reference"] = (row.get("grade") or {}).get("reference_tier")
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

    merge = uns.merge_project_tiers(l3u["rows"], decisions)
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
        "elapsed_seconds": round(elapsed, 3),
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
        "status": (
            "UNSEAL_REVIEW_DONE"
            if len(decisions) == payload["hardcoded"]["n_sealed"]
            else "UNSEAL_REVIEW_INCOMPLETE"
        ),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3w_unseal.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT_DIR / "l3v_claims_patch.json").write_text(
        json.dumps(
            {
                "generated_from": payload["experiment_id"],
                "l3v_claims_sha256": payload["subject"]["l3v_claims_sha256"],
                "note": "合并进 docs/gates/l3v_claims.json 后必须重跑 L3v 闸门；本文件不修改任何历史产物",
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
    (OUT_DIR / "l3w_unseal.md").write_text(md, encoding="utf-8", newline="\n")

    evidence = {
        "experiment_id": payload["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "prereg_bytes": len(PREREG.read_bytes()),
        "subject_l3u_sha256": L3U_SHA,
        "outputs": {
            "json": (OUT_DIR / "l3w_unseal.json").relative_to(ROOT).as_posix(),
            "markdown": (OUT_DIR / "l3w_unseal.md").relative_to(ROOT).as_posix(),
            "patch": (OUT_DIR / "l3v_claims_patch.json").relative_to(ROOT).as_posix(),
        },
        "n_unsealed": result["n_unsealed"],
        "n_stay_sealed": result["n_stay_sealed"],
        "quotable_before": merge["quotable_before"],
        "quotable_after": merge["quotable_after"],
        "n_changed": merge["n_changed"],
        "refuted_predictions": [p["id"] for p in preds if p["refuted"]],
    }
    EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    if not args.quiet:
        print(f"封印条目 {len(decisions)}｜解封 {result['n_unsealed']}｜维持封印 {result['n_stay_sealed']}")
        for d in decisions:
            print(
                f"  {d['key']:<15} {d['status']:<12} {d['tier']:<18} "
                f"ratio={_f(d['ratio_primary'], 3):>6}  stable={d['stability']['stable']}"
            )
        print(
            f"全项目 QUOTABLE {merge['quotable_before']} → {merge['quotable_after']}｜"
            f"变化 {merge['n_changed']} 条"
        )
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"被反驳的预测：{', '.join(refuted) or '无'}")
        print(f"报告：{(OUT_DIR / 'l3w_unseal.md').relative_to(ROOT).as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
