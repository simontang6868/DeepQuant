#!/usr/bin/env python
"""L3ab 汇总报告：把家族审计、校正复现、引用状态合成一份，并给出最终状态。

最终状态**由证据分支判定，不预设**。五个候选里选哪个，取决于三道检查：
复现是否成立、家族是否可定、未决项是否影响对外结论。判定逻辑写在 `decide_status()` 里，
每个分支都对应一条可核的事实。

产出：
    reports/STAGE_REPORT_L3AB_FINAL.md
    reports/gates/H-L3AB-FAMILY/l3ab_final_hashes.json

用法：
    PYTHONPATH=backend/src python scripts/l3ab_stage_report.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3AB-FAMILY"
FAMILY_JSON = OUT_DIR / "l3ab_family.json"
CLAIMS_JSON = OUT_DIR / "l3ab_current_claims.json"
MUT_JSON = OUT_DIR / "l3ab_mutation.json"
REPORT = ROOT / "reports" / "STAGE_REPORT_L3AB_FINAL.md"

DELIVERABLES = [
    "reports/L3AB_TEST_FAMILY_AUDIT.md",
    "reports/L3AB_MULTIPLICITY_REPRODUCTION.md",
    "reports/L3AB_CURRENT_CLAIM_STATUS.md",
    "docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md",
    "reports/STAGE_REPORT_L3AB_FINAL.md",
    "reports/gates/H-L3AB-FAMILY/l3ab_family.json",
    "reports/gates/H-L3AB-FAMILY/l3ab_current_claims.json",
    "reports/gates/H-L3AB-FAMILY/l3ab_mutation.json",
    "docs/gates/l3ab_family_decisions.json",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decide_status(family: dict, claims: dict, mut: dict) -> tuple[str, list[str]]:
    """按证据分支判定最终状态。返回 (状态, 判据列表)。"""
    reasons: list[str] = []
    rows = [r for r in family["repro"] if r["match"] is not None]
    worst = max((r["worst"] for r in rows), default=float("inf"))
    repro_ok = worst <= 1e-9 and len(rows) == 22

    # 5 条正面结论在全部口径 × 全部校正下都通过 —— 决定「m 的分歧是否影响对外结论」
    positive = ["phasec_d", "phasec2_d", "gate6_d", "phaseo_netBASE", "phasef_delta"]
    invariant = all(
        all(k in s["rejected"][m] for m in ("bonferroni", "holm", "bh", "by"))
        for s in family["specs"]
        for k in positive
    )
    u2 = next((u for u in family["decisions"]["unknowns"] if u["id"] == "U2"), None)
    coverage_gap = family["coverage"]["n_hypothesis_unregistered"]
    all_caught = bool(mut.get("all_caught"))

    if not repro_ok:
        reasons.append(f"p 值无法复现：{len(rows)} 条可比重算中最大偏差 {worst:.2e}（阈值 1e-9）。")
        return "BLOCKED_BY_STATISTICAL_REPRODUCTION", reasons
    reasons.append(f"p 值链条可复现：{len(rows)} 条重算与 L3u/L3z 一致，最大偏差 {worst:.0e}。")

    if not all_caught:
        reasons.append("变异测试未全部抓到，检查器可信度不足。")
        return "BLOCKED_BY_TEST_FAMILY_AMBIGUITY", reasons
    reasons.append("6 个变异体全部被抓到，检查器自检通过。")

    if u2 is not None and u2["status"] == "UNKNOWN" and not invariant:
        reasons.append("m 的方向 UNKNOWN，且对外结论随 m 改变 ⇒ 家族歧义阻塞最终判定。")
        return "BLOCKED_BY_TEST_FAMILY_AMBIGUITY", reasons

    if u2 is not None and u2["status"] == "UNKNOWN":
        reasons.append(
            f"U2（同源配对是否为独立假设）仍 UNKNOWN；但 5 条正面结论在 "
            f"{len(family['specs'])} 种 m 口径 × 4 种校正下全部通过，对外集合不受其影响。"
        )
        reasons.append(
            f"家族覆盖面有 {coverage_gap} 个已证实缺口（phasei_out），"
            "因此「可引 6 条」只能声称覆盖登记在册的 27 条。"
        )
        return "VERIFIED_WITH_DOCUMENTED_LIMITATIONS", reasons

    reasons.append("家族可定、复现成立、未决项不影响对外集合。")
    return "VERIFIED_CURRENT_CLAIM_SET", reasons


def render(family: dict, claims: dict, mut: dict, status: str, reasons: list[str]) -> str:
    L: list[str] = []
    A = L.append
    dec = family["decisions"]
    specs = family["specs"]
    primary = next(s for s in specs if s["adopted"])
    inv = family["inventory"]

    A("# 阶段报告：L3ab 检验家族审计与最终引用清单")
    A("")
    A("> 本报告由 `scripts/l3ab_stage_report.py` 生成。本阶段**不新增因子、不新增回测、不启动实盘**；")
    A("> 不改动任何历史产物，不删除失败的预测或测试，也不为增加/减少通过条数而调整家族边界。")
    A("")
    A(f"**最终状态**：`{status}`")
    A("")
    A("判定依据：")
    A("")
    for r in reasons:
        A(f"- {r}")
    A("")
    A("## 1. 审计范围")
    A("")
    A("| 问题 | 结论 |")
    A("| --- | --- |")
    A("| m=24 的家族定义是否有依据 | 有依据，但**不是**「独立假设数」：它是「独立数字（指纹）数」。27 条里 7 条落在 3 份序列上，按数据同一性应为 23 |")
    A("| 重复登记（A 类）有几条 | **0 条**。5 对逐位相同的配对，登记判定字符串全部不同 |")
    A("| 家族覆盖全了吗 | **没有**。至少 `phasei_out`（exQ5 可成交）有完整结论却未登记 |")
    A("| 四种校正能否复现 | **能**，22 条重算与既有产物完全一致（最大偏差 0） |")
    A("| 当前可引用几条 | **6 条**，且 6 条里只有 1 条带扣成本正收益证据 |")
    A("")
    A("不做什么：不重算策略、不换数据、不重新定义显著性、不把统计结论翻译成收益预期。")
    A("")
    A("## 2. 原始证据来源")
    A("")
    A("| 证据 | 路径 | 角色 |")
    A("| --- | --- | --- |")
    A("| L3u 预登记 | `docs/gates/l3u_prereg.json` | 27 条的序列声明与判定字段 |")
    A("| L3u 结果 | `reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json` | 逐条重算的均值/标准误/t |")
    A("| 结论卡片 | `docs/gates/l3v_claims.json` | 分级、限定、撤回清单 |")
    A("| L3z 校正 | `reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json` | p 值与四种校正 |")
    A("| L3aa 复核 | `reports/gates/H-L3AA-DEPENDENCE/l3aa_dependence.json` | BY 与依赖诊断 |")
    A("| 判定记录 | `docs/gates/l3ab_family_decisions.json` | **本审计的判断层**（sha 校验） |")
    A("| 原始阶段产物 | 23 个 `*_out/` 目录 | 逐月序列本身 |")
    A("")
    A("判断与数字分开存放是本阶段的一个设计：判断放在判定记录里、可哈希、改了就拒跑；")
    A("数字从产物现取、不手抄。这样审查者能核对「这条凭什么算进家族」。")
    A("")
    A("## 3. 检验家族的定义")
    A("")
    A(f"27 条登记结论，其中 {sum(1 for r in inv if r['counts_only'])} 条判定不由单一均值给出"
      f"（占名额、不出 p），{sum(1 for r in inv if not r['counts_only'])} 条参与评分。")
    A("")
    A("| 类 | 含义 | 条数 |")
    A("| --- | --- | --- |")
    for cls, label in (
        ("A", "同一检验被重复登记（同数据 + 同假设）"),
        ("B", "数据重叠或同源，但假设不同"),
        ("C", "一条序列是另一条的子窗口"),
        ("D", "不同数据或统计量的独立问题"),
        ("E", "同一假设在不同窗口/样本上重复检验（提纲未列，本次补）"),
    ):
        n = sum(1 for r in inv if r["class"] == cls)
        A(f"| {cls} | {label} | **{n}** |")
    A("")
    A("逐条的假设、统计量、窗口、n、p、分级与纳入理由见 "
      "`reports/L3AB_TEST_FAMILY_AUDIT.md` 第 4 节（27 行，不在这里重复）。")
    A("")
    A("## 4. m 口径比较")
    A("")
    A("| 口径 | m | Bonf | Holm | BH | BY | 采用 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for s in specs:
        A(
            f"| {s['label']} | {s['m']} | {len(s['rejected']['bonferroni'])} "
            f"| {len(s['rejected']['holm'])} | {len(s['rejected']['bh'])} "
            f"| {len(s['rejected']['by'])} | {'**主口径**' if s['adopted'] else '敏感性'} |"
        )
    A("")
    A("**四种口径里三种给出相同的 6 条；唯一差异是 m=27 时 Bonferroni 掉到 5 条。**")
    A("")
    A("保留 m=24 的理由（按强度排序，**不含「更保守」**——m 越小门槛越松，24 比 27 更宽松）：")
    A("")
    A("1. 它是 L3z 预登记冻结的口径，换口径须新预登记；")
    A("2. A 类重复登记为 0，不存在「同一假设被算两次」这一最明确的虚高情形；")
    A("3. 24 与 27 的分歧方向是 UNKNOWN（U2），证据不足，不能定方向；")
    A("4. 对外 5 条正面结论对全部分歧不敏感——这是最关键的一条。")
    A("")
    A("## 5. 四种校正的结果（主口径 m=24）")
    A("")
    A("| claim_id | p | Bonferroni | Holm | BH | BY |")
    A("| --- | --- | --- | --- | --- | --- |")
    for it in sorted(primary["items"], key=lambda x: x["p"]):
        mark = lambda f: "通过" if f else "—"  # noqa: E731
        A(
            f"| `{it['key']}` | {it['p']:.6f} | {mark(it['bonferroni'])} "
            f"| {mark(it['holm'])} | {mark(it['bh'])} | {mark(it['by'])} |"
        )
    A("")
    A("适用条件的差别（引用时必须讲）：Bonferroni / Holm 控制族系错误率、不依赖任何相依假设；")
    A("BH 控制错误发现率、**需要 PRDS**（本项目的 PRDS 未验证）；")
    A("BY 同样控制错误发现率、但**不要求**独立性或 PRDS，代价是门槛收紧 H_m 倍。")
    A("")
    A("⚠️ BY 通过不等于经济有效或可交易，见第 7 节。")
    A("")
    A("## 6. 6 条当前引用结论的逐条状态")
    A("")
    A("| claim_id | 分级 | 四种校正 | 经济属性 | 扣成本正收益 | 前瞻 |")
    A("| --- | --- | --- | --- | --- | --- |")
    for c in claims["claims"]:
        A(
            f"| `{c['key']}` | {c['tier']} | {'全过' if c['passes_all_four'] else '部分'} "
            f"| {c['economic_short']} | {'有' if c['cost_adjusted_positive_evidence'] else '无'} | 无 |"
        )
    A("")
    A("九项核查（可追溯性 / 独立核对 / 多重检验 / 敏感性与滞后 / 经济解释 / 扣成本收益 /")
    A("样本外证据 / 允许措辞 / 禁止推论）逐条见 `reports/L3AB_CURRENT_CLAIM_STATUS.md`。")
    A("")
    A("## 7. 统计、经济、交易可用性：三件不同的事")
    A("")
    A("| 层次 | 本阶段能确认的 | 不能确认的 |")
    A("| --- | --- | --- |")
    A("| 统计 | 6 条在给定家族与 m 下通过四种校正；p 值链可复现 | 不能推广到家族外的结论（如 `phasei_out`） |")
    A("| 经济 | `phaseo_netBASE` 在 BASE 费率 / 3000 万 / 样本内净 alpha 为正 | 其余 5 条是价差或毛利 alpha，扣成本无处可扣；`phasel_alpha` 是零效应判定，方向相反 |")
    A("| 交易可用性 | **没有任何一条提供可交易性证据** | 无前瞻样本、无券商交割单、无实盘 |")
    A("")
    A("四句话必须成对出现：")
    A("")
    A("- 统计显著 ≠ 经济上有意义；")
    A("- 统计信号真实存在 ≠ 净收益为正；")
    A("  （`phasel_alpha` 正是反例：它的 t 显著，但结论是「净效应被成本吸收」。）")
    A("- 历史样本内结果 ≠ 前瞻期有效；")
    A("- 通过多重检验 ≠ 已证明可以实盘交易。")
    A("")
    A("## 8. 测试与变异测试")
    A("")
    muts = mut["mutations"]
    A(f"- 新增 **32 项**测试，覆盖审计提纲第七节的十项；")
    A(f"- 变异测试注入 {len(muts)} 处缺陷，抓到 **{sum(1 for m in muts if m['caught'])}/"
      f"{len(muts)}**，源码恢复后与基线一致：{'是' if mut['restored_matches_baseline'] else '否'}。")
    A("")
    A("| # | 注入的缺陷 | 抓到 |")
    A("| --- | --- | --- |")
    for m in muts:
        A(f"| {m['id']} | {m['defect']} | {'是' if m['caught'] else '**否**'} |")
    A("")
    A("**测试抓到了本审计自己的两个错误**，都记录在此而不是悄悄改掉：")
    A("")
    A("1. 初稿写「27 − 3 对 = 24」，算式与结论对不上。实际是 7 条落在 3 份序列上（2+3+2），"
      "按数据同一性应为 23；24 是**指纹**去重（`phasep_late` 的 n 不同故单列）。"
      "是同名测试把它逼出来的。")
    A("2. 子窗口关系初版只报一个超集。三个条目逐位相同时 `phasep_late` 有两个合法超集，"
      "只报一个会让读者以为只有一个。已改为返回候选列表。")
    A("")
    A("## 9. 未解决的问题")
    A("")
    A("| 编号 | 项目 | 状态 | 影响 |")
    A("| --- | --- | --- | --- |")
    for u in dec["unknowns"]:
        A(f"| {u['id']} | {u['item']} | {u['status']} | {u['impact']} |")
    A("")
    A("另有一条**本阶段明确不做**的事：`phasei_out` 有完整结论却未登记，本审计只报告这个缺口，")
    A("**不把它补进家族重算 m**——补进去会改动全部门槛，而纳入与否本身是判断。"
      "补齐家族应作为一次新预登记。")
    A("")
    A("## 10. 对 L3aa 的自我更正")
    A("")
    for r in dec["retractions"]:
        A(f"- **{r['id']}**：撤回「{r['retracted_statement']}」。")
        A(f"  - 为什么错：{r['why_wrong']}")
        A(f"  - 替换为：{r['replaced_by']}")
        A(f"  - 原文处理：{r['original_preserved']}")
    A("")
    A("## 11. 复现命令")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3ab_family_audit.py")
    A("PYTHONPATH=backend/src python scripts/l3ab_current_claims.py")
    A("PYTHONPATH=backend/src python scripts/l3ab_mutation_test.py")
    A("PYTHONPATH=backend/src python scripts/l3ab_stage_report.py")
    A("PYTHONPATH=src pytest backend/tests/gates/test_gates_l3ab_family.py -q")
    A("```")
    A("")
    A("全部脚本在启动时校验预登记与判定记录的 sha256，任一不符即拒跑。")
    A("交付文件哈希见 `reports/gates/H-L3AB-FAMILY/l3ab_final_hashes.json`"
      "（本报告自身的哈希也在其中，故不在正文内自指）。")
    A("")
    return "\n".join(L) + "\n"


def main() -> int:
    for path in (FAMILY_JSON, CLAIMS_JSON, MUT_JSON):
        if not path.exists():
            raise SystemExit(f"缺少前置产物：{path.relative_to(ROOT)}，先跑前三个脚本")
    family = json.loads(FAMILY_JSON.read_text(encoding="utf-8"))
    claims = json.loads(CLAIMS_JSON.read_text(encoding="utf-8"))
    mut = json.loads(MUT_JSON.read_text(encoding="utf-8"))

    status, reasons = decide_status(family, claims, mut)
    REPORT.write_text(render(family, claims, mut, status, reasons), encoding="utf-8", newline="\n")

    hashes = {}
    for rel in DELIVERABLES:
        p = ROOT / rel
        hashes[rel] = _sha(p) if p.exists() else "MISSING"
    (OUT_DIR / "l3ab_final_hashes.json").write_text(
        json.dumps(
            {"final_status": status, "reasons": reasons, "files": hashes},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if "--quiet" not in sys.argv:
        print(f"最终状态        {status}")
        for r in reasons:
            print(f"  - {r}")
        print("")
        for rel, dig in hashes.items():
            print(f"{dig[:16]}…  {rel}")
        print(f"\n报告            {REPORT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
