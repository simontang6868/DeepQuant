#!/usr/bin/env python
"""L3z 附：标准误（NW 滞后）敏感性 —— **补充分析**。

主口径仍为 NW(3)，这是 L3u 预登记规定的，不因任何滞后更有利而替换。

本脚本是**新增**的补充分析，标记为 SUPPLEMENTARY：
- L3w / L3x 预登记已规定「解封需在 NW 滞后网格上不跨档」，且对那 9 条执行过；
- 但另有若干可引用结论（phasef / phasel / phaseo / phases / phaset）从未在该网格上验证过。
- 本轮补齐，并明确它**不是**原预登记的一部分。

用法：
    PYTHONPATH=backend/src python scripts/l3z_lag_sensitivity.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from deepquant.gates import citation as cit
from deepquant.gates import multiplicity as mp
from deepquant.gates import unseal as uns

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
L3U = ROOT / "reports/gates/H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
OUT_MD = ROOT / "reports" / "L3Z_LAG_SENSITIVITY.md"
OUT_JSON = ROOT / "reports" / "L3Z_LAG_SENSITIVITY.json"

LAGS = (0, 1, 2, 3, 4, 6)
MAIN_LAG = 3


def main() -> int:
    started = time.perf_counter()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    l3u = json.loads(L3U.read_text(encoding="utf-8"))
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    entries = {e["key"]: e for e in prereg["entries"]}
    quotability = {c["key"]: c["quotability"] for c in cards["claims"]}
    scored = {r["key"] for r in mp.eligible(l3u["rows"]) if not r.get("_counts_only")}

    rows = []
    failures = []
    for key in sorted(scored):
        entry = entries.get(key)
        if entry is None:
            failures.append({"key": key, "reason": "预登记中没有该条目的序列声明"})
            continue
        try:
            rebuilt = cit.rebuild_series(entry, ROOT)
        except Exception as exc:  # noqa: BLE001 - 记录原因比中断更有用
            failures.append({"key": key, "reason": f"重建失败：{type(exc).__name__}: {exc}"})
            continue
        vals = list(rebuilt.values)
        if len(vals) < 8:
            failures.append({"key": key, "reason": f"序列长度 {len(vals)} 过短"})
            continue
        grid = uns.mde_grid(vals, lags=LAGS)
        n = len(vals)
        mean = sum(vals) / n
        per_lag = {}
        for lag, g in sorted(grid.items()):
            mde = g["mde"]
            per_lag[lag] = {
                "se_nw": g["se_nw"],
                "mde": mde,
                "ratio": abs(mean) / mde if mde else None,
                "tier": mp_tier(abs(mean) / mde if mde else 0.0),
            }
        tiers = {v["tier"] for v in per_lag.values()}
        rows.append(
            {
                "key": key,
                "quotability": quotability.get(key),
                "n": n,
                "mean": mean,
                "per_lag": per_lag,
                "distinct_tiers": sorted(tiers),
                "crosses_tier": len(tiers) > 1,
                "main_lag_tier": per_lag.get(MAIN_LAG, {}).get("tier"),
            }
        )

    crossing = [r for r in rows if r["crosses_tier"]]
    result = {
        "experiment_id": "H-L3Z-LAG-SENSITIVITY",
        "status": "SUPPLEMENTARY",
        "note": (
            "本分析为 L3z 阶段新增，不属于 L3u/L3w/L3x 的原预登记。"
            "主口径仍为 NW(3)；不因任何一个滞后更有利而替换主口径。"
        ),
        "lags": list(LAGS),
        "main_lag": MAIN_LAG,
        "rows": rows,
        "n_crossing": len(crossing),
        "crossing_keys": sorted(r["key"] for r in crossing),
        "failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    OUT_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    def esc(x) -> str:
        return str(x).replace("|", r"\|")

    lines = []
    lines.append("# L3z 附：标准误（NW 滞后）敏感性 —— 补充分析")
    lines.append("")
    lines.append(
        "> **本分析是 L3z 阶段新增的补充分析，不是原预登记的一部分。** "
        "主口径仍为 NW(3)（L3u 预登记规定），不因任何滞后更有利而替换。"
    )
    lines.append("")
    lines.append("## 一、为什么要补")
    lines.append("")
    lines.append(
        "L3w / L3x 预登记规定「解封需在 NW 滞后网格上不跨档」，并已对复核的 9 条执行。"
        "但另有若干可引用结论从未在该网格上验证过，本轮补齐。"
    )
    lines.append("")
    lines.append(f"网格：{LAGS}（lag=0 的 NW 不等于 iid，两者差 √((n−1)/n)）。")
    lines.append("")
    lines.append("## 二、结果")
    lines.append("")
    lines.append("| 结论 | 定稿分级 | n | " + " | ".join(f"L={l}" for l in LAGS) + " | 跨档 |")
    lines.append("| --- | --- | --- | " + " | ".join("---" for _ in LAGS) + " | --- |")
    for r in rows:
        cells = []
        for l in LAGS:
            v = r["per_lag"].get(l)
            cells.append(f"{v['ratio']:.2f}" if v and v["ratio"] is not None else "—")
        lines.append(
            f"| `{r['key']}` | {esc(r['quotability'])} | {r['n']} | "
            + " | ".join(cells)
            + f" | {'**是**' if r['crosses_tier'] else '否'} |"
        )
    lines.append("")
    lines.append("表中数值为 |效应| / MDE 的比值；跨档指该比值在网格上落进了不同的分级桶。")
    lines.append("")
    lines.append(f"**跨档条目：{len(crossing)} 条**")
    if crossing:
        lines.append("")
        for r in crossing:
            lines.append(
                f"- `{r['key']}`：{r['distinct_tiers']}（主口径 L={MAIN_LAG} 为 `{r['main_lag_tier']}`）"
            )
    lines.append("")
    if failures:
        lines.append("## 三、未能计算的条目")
        lines.append("")
        for f in failures:
            lines.append(f"- `{f['key']}`：{esc(f['reason'])}")
        lines.append("")
    lines.append("## 四、结论")
    lines.append("")
    lines.append(
        f"- 主口径 NW({MAIN_LAG}) 保持不变。"
        f"网格上跨档的有 {len(crossing)} 条，引用时须说明其分级依赖滞后选择。"
    )
    lines.append("- 本分析不改变任何一条的定稿分级；跨档信息作为限定补充说明。")
    lines.append("")
    lines.append("## 五、复现")
    lines.append("")
    lines.append("```bash")
    lines.append("PYTHONPATH=backend/src python scripts/l3z_lag_sensitivity.py")
    lines.append("```")
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"计算 {len(rows)} 条｜跨档 {len(crossing)} 条｜失败 {len(failures)} 条")
    if crossing:
        print("  跨档:", ", ".join(sorted(r["key"] for r in crossing)))
    for f in failures:
        print(f"  失败 {f['key']}: {f['reason'][:70]}")
    print(f"产物：{OUT_MD.relative_to(ROOT).as_posix()}")
    return 0


def mp_tier(ratio: float) -> str:
    """与 citation.grade_quotability 同一套阈值：2× / 1× / 0.5×。"""
    if ratio >= 2.0:
        return "QUOTABLE"
    if ratio >= 1.0:
        return "QUOTABLE_MARGINAL"
    if ratio >= 0.5:
        return "DIRECTION_ONLY"
    return "UNDERPOWERED"


if __name__ == "__main__":
    raise SystemExit(main())
