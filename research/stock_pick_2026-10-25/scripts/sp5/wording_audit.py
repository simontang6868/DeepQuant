"""SP-5 / P0-1 验收脚本：全库措辞审计

用途
----
P0-1 的完成标准要求「全库不存在**无标注**的旧结论句」。本脚本把这件事变成
可机械复查的动作，而不是靠人眼。

判定方法
--------
1. 扫描 `research/stock_pick_2026-10-25/**/*.md`；
2. 命中受控措辞（见 PATTERNS）；
3. 以命中行为中心取 ±WINDOW 行的上下文，若含任一「更正标记」，判 `annotated=True`；
4. 输出 CSV + 汇总。`annotated=False` 的行必须为 0，否则 P0-1 判 `DOC_INCONSISTENT`。

⚠️ 本脚本只读，不修改任何文档。发现未标注项 → 人工补标注，不改数值（R5）。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
OUT = BASE / "sp5_out"
OUT.mkdir(parents=True, exist_ok=True)

WINDOW = 8

# 规则化：只有「数值 + 结论词」同时出现才算在传播旧结论。
# 只提数字做对账（如审计报告里的 −17.82% vs −17.91% 对比）不算命中，避免假阳性。
RULES = [
    (
        "结论句:两条目标达成",
        lambda ln: any(k in ln for k in ("同时达成", "同时满足两条目标", "两条目标同时")),
        "旧结论（回撤已破界）",
    ),
    (
        "旧月频回撤当结论",
        lambda ln: ("17.82%" in ln)
        and any(k in ln for k in ("以内", "达成", "满足", "不破")),
        "旧月频回撤被当作满足上限的依据",
    ),
    (
        "情景假设缺标签",
        # 出现「研究情景假设 / 待验证 / 验证 / 核查」的句子是在**提出问题**，不是在宣布结论，
        # 例如任务名「20% 仓位四道验证」。只对「像结论一样陈述」的句子要求标签。
        lambda ln: ("7~11%" in ln or "20% 仓位" in ln)
        and not any(
            k in ln for k in ("研究情景假设", "待验证", "验证", "核查")
        ),
        "须标注「研究情景假设」",
    ),
]

MARKERS = (
    "撤回",
    "已被",
    "更正",
    "研究情景假设",
    "§10",
    "阅读须知",
    "不成立",
)

# 本文件自身与预登记/任务书属「新写文件」，允许直接出现受控措辞（它们在定义规则）
EXEMPT = {"05_SP5_任务书.md", "SP5_预登记.md"}


def audit(root: Path, exempt: set[str] | None = None) -> tuple[list[dict], int]:
    """扫描 root 下所有 .md，返回 (命中行, 扫描文件数)。抽成函数以便测试。"""
    exempt = EXEMPT if exempt is None else exempt
    rows: list[dict] = []
    n_files = 0
    for md in sorted(root.rglob("*.md")):
        if md.name in exempt:
            continue
        n_files += 1
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, ln in enumerate(lines, start=1):
            for name, test, why in RULES:
                if not test(ln):
                    continue
                lo, hi = max(0, i - 1 - WINDOW), min(len(lines), i - 1 + WINDOW + 1)
                ctx = "\n".join(lines[lo:hi])
                annotated = any(m in ctx for m in MARKERS)
                rows.append(
                    {
                        "file": str(md.relative_to(root)).replace("\\", "/"),
                        "line": i,
                        "pattern": name,
                        "reason": why,
                        "annotated": annotated,
                        "text": ln.strip()[:160],
                    }
                )
    return rows, n_files


def main() -> int:
    rows, n_files = audit(BASE)

    csv_path = OUT / "wording_audit.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)

    bad = [r for r in rows if not r["annotated"]]
    by_pat: dict[str, int] = {}
    for r in bad:
        by_pat[r["pattern"]] = by_pat.get(r["pattern"], 0) + 1

    res = {
        "files_scanned": n_files,
        "files_with_hits": len({r["file"] for r in rows}),
        "hits_total": len(rows),
        "hits_annotated": len(rows) - len(bad),
        "hits_unannotated": len(bad),
        "unannotated_by_pattern": by_pat,
        "verdict": "DOC_CONSISTENT" if not bad else "DOC_INCONSISTENT",
        "unannotated_examples": [
            f"{r['file']}:{r['line']}  [{r['pattern']}]  {r['text'][:80]}" for r in bad[:20]
        ],
    }
    (OUT / "wording_audit.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for k, v in res.items():
        if k == "unannotated_examples":
            continue
        print(f"{k:26s} = {v}")
    if bad:
        print("\n未标注命中：")
        for x in res["unannotated_examples"]:
            print("  " + x)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
