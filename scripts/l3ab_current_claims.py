#!/usr/bin/env python
"""L3ab 第二部分：6 条引用状态 + 当前有效引用清单。

产出：
    reports/L3AB_CURRENT_CLAIM_STATUS.md
    docs/history/DELIVERABLE_QUOTE_SHEET_CURRENT_L3AB-v1.md

⚠️ 2026-10-29 L3af 起，`docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md` 由
   `scripts/l3af_reports.py` 生成（v2、m=25、S1–S4）。本脚本的输出改到 history/，
   以免两个生成器抢同一个文件、把当前清单覆盖成旧口径。

两者都由脚本生成、都可重建。`docs/DELIVERABLE_QUOTE_SHEET.md`（L3y 的对外定稿）**不改动**：
本阶段只新增一份「当前状态」清单，不动已被引用的定稿。

用法：
    PYTHONPATH=backend/src python scripts/l3ab_current_claims.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deepquant.gates import current_sheet as cs

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3AB-FAMILY"
FAMILY_JSON = OUT_DIR / "l3ab_family.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
L3AA = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_dependence.json"

R_STATUS = ROOT / "reports" / "L3AB_CURRENT_CLAIM_STATUS.md"
R_SHEET = ROOT / "docs" / "history" / "DELIVERABLE_QUOTE_SHEET_CURRENT_L3AB-v1.md"

TZ = timezone(timedelta(hours=8))


def _pct(flag: bool) -> str:
    return "通过" if flag else "—"


def _cell(text: object) -> str:
    """表格单元格转义。

    卡片结论句里有 `|效应|/MDE` 这种写法，原样塞进 Markdown 表格会把一列切成两列——
    转换到 docx 后表现为整行错位。这里把竖线转义，转换器会还原成字面竖线。
    """
    return str(text if text is not None else "—").replace("|", "\\|")


def _load() -> dict:
    if not FAMILY_JSON.exists():
        raise SystemExit(f"缺少 {FAMILY_JSON.relative_to(ROOT)}；先跑 l3ab_family_audit.py")
    family = json.loads(FAMILY_JSON.read_text(encoding="utf-8"))
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    l3aa = json.loads(L3AA.read_text(encoding="utf-8")) if L3AA.exists() else {}
    return {"family": family, "cards": cards, "l3aa": l3aa}


def render_status(res: dict, hashes: dict[str, str], generated: str) -> str:
    L: list[str] = []
    A = L.append

    A("# L3ab 当前引用状态：6 条结论的逐条核对")
    A("")
    A("> 由 `scripts/l3ab_current_claims.py` 生成，**禁止手工编辑**。")
    A(f"> 版本 `{res['sheet_version']}`｜生成时间 {generated}")
    A("")
    A("本报告回答一个问题：**当前被列为可引用的 6 条结论，各自凭什么、能说到哪一步。**")
    A("")
    A("每条的九项核查都给出取值，不给「总体可靠」这类结论。缺证据的写「无」，不写「不适用」。")
    A("")
    A("## 0. 一览")
    A("")
    A("| claim_id | 分级 | 经济属性 | 多重检验（m=24） | 扣成本正收益证据 | 前瞻证据 |")
    A("| --- | --- | --- | --- | --- | --- |")
    for c in res["claims"]:
        mp_ok = c["passes_all_four"]
        A(
            f"| `{c['key']}` | {c['tier']} | {c['economic_short']} "
            f"| {'四种全过' if mp_ok else '部分通过'} | "
            f"{'有' if c['cost_adjusted_positive_evidence'] else '无'} | 无 |"
        )
    A("")
    A("⚠️ 最要紧的一行：**6 条里只有 1 条（`phaseo_netBASE`）带扣成本后的正收益证据，"
      "而且它是样本内的。** 其余 5 条是价差或毛利 alpha，不能读成「能赚多少」。")
    A("")
    for c in res["claims"]:
        A(f"## `{c['key']}`｜{c['tier']}")
        A("")
        A(f"**结论句（对外原话，直接抄）**：{c['allowed_wording']}")
        A("")
        A("| 核查项 | 结果 |")
        A("| --- | --- |")
        A(f"| 1 数字可追溯 | L3u 核对类别 `{c['traceability']['l3u_class']}`；{c['traceability']['detail'] or '—'} |")
        A(f"| 2 统计方法独立核对 | {c['verification']['conclusion']}（L3u `{c['verification']['l3u_class']}`；"
          f"后续层 `{c['verification']['later_layer']}`） |")
        A(f"| 3 多重检验（m={res['m_primary']}） | Bonferroni {_pct(c['multiplicity']['bonferroni'])}"
          f"｜Holm {_pct(c['multiplicity']['holm'])}｜BH {_pct(c['multiplicity']['bh'])}"
          f"｜BY {_pct(c['multiplicity']['by'])} |")
        if c["multiplicity"].get("at_m27"):
            at = c["multiplicity"]["at_m27"]
            A(f"| 3b 替代口径 m={res['m_alt']} | Bonferroni {_pct(at['bonferroni'])}"
              f"｜Holm {_pct(at['holm'])}｜BH {_pct(at['bh'])}｜BY {_pct(at['by'])} |")
        lag = c["sensitivity"]
        lag_txt = (
            "、".join(lag["lag_tiers"]) if lag["lag_tiers"] else "无记录"
        )
        A(f"| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `{lag_txt}`"
          f"（{'跨档' if lag['lag_crosses_tier'] else '不跨档'}）"
          f"；m 口径影响：{'有' if lag['m_changes_verdict'] else '无'} |")
        A(f"| 5 经济解释 | {c['economic_label']} |")
        A(f"| 6 扣成本后正向收益证据 | {'**有**（净收益口径）' if c['cost_adjusted_positive_evidence'] else '**无**（该判定量不是组合收益，扣成本在定义上无处可扣）'} |")
        A(f"| 7 样本外/前瞻证据 | **无**。{c['out_of_sample_note']} |")
        A(f"| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |")
        banned = c["forbidden"]
        A(f"| 9 不允许的推论 | {'；'.join(banned) if banned else '见下方限定'} |")
        A("")
        if c["limits_of_use"]:
            A("**引用时必须同时给出的限定**：")
            A("")
            for lim in c["limits_of_use"]:
                A(f"- {lim}")
            A("")
        if c["null_claim"]:
            A("⚠️ **本条是零效应判定**：它的「通过」含义是「这个非零信号本身是真的」，"
              "**不是**「存在可利用的净收益」。任何把它读成正向收益的表述都越界。")
            A("")
    A("## 全族层面的五条限制")
    A("")
    for i, lim in enumerate(res["global_limits"], 1):
        A(f"{i}. {lim}")
    A("")
    A("## 依凭的产物与哈希")
    A("")
    A("| 文件 | sha256 |")
    A("| --- | --- |")
    for k, v in hashes.items():
        A(f"| `{k}` | `{v[:16]}…` |")
    A("")
    A("## 复现")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3ab_family_audit.py")
    A("PYTHONPATH=backend/src python scripts/l3ab_current_claims.py")
    A("```")
    A("")
    return "\n".join(L) + "\n"


def render_sheet(res: dict, hashes: dict[str, str], generated: str, status: str) -> str:
    L: list[str] = []
    A = L.append

    A("# 当前有效引用清单（L3ab）")
    A("")
    A(f"> **版本** `{res['sheet_version']}`｜**生成时间** {generated}｜**状态** `{status}`")
    A(">")
    A("> 本文件由 `scripts/l3ab_current_claims.py` 生成，**禁止手工编辑**；改数字会导致重建失败。")
    A("> 它取代的不是历史报告，而是「需要读者自己拼接当前结论」这件事：")
    A("> 历史报告与更正记录一律保留（见 §6），但**当前有效的引用状态只以本文件为准**。")
    A("")
    A("## 1. 一句话结论")
    A("")
    A(f"登记在册的 {res['n_claims_registered']} 条结论中，**{res['n_quotable']} 条**当前可对外引用：")
    A("1 条 `QUOTABLE`、4 条 `QUOTABLE_MARGINAL`、1 条 `NULL_CONFIRMED`。")
    A("")
    A("**但这 6 条里只有 1 条带扣成本后的正收益证据，且全部是样本内结果。**")
    A("「统计上站得住」与「能赚多少」「能不能实盘」是三件事，本文件分三栏写。")
    A("")
    A("## 2. 校正口径（引用时必须一并说明）")
    A("")
    A("| 项目 | 取值 |")
    A("| --- | --- |")
    A(f"| 检验家族 | L3u 登记在册的 {res['n_claims_registered']} 条（**不含**家族外实验，见 §5） |")
    A(f"| 主口径 m | **{res['m_primary']}**（按 `(mean, t, n)` 数值指纹去重；A 类重复登记 0 条） |")
    A(f"| 替代口径 m | {res['m_alt']}（全部登记条目）。仅影响 `phasel_alpha`，不影响任何正面结论 |")
    A("| 校正方法 | Bonferroni / Holm / BH / Benjamini–Yekutieli，四种结果并列 |")
    A("| 标准误 | Newey-West(3)，Bartlett 核；p 用 t 分布双侧，df = n − 1 |")
    A("| 显著性水平 | α = 0.05（FWER 类）；q = 0.05（FDR 类） |")
    A("| 依赖结构 | BY 不要求独立性或 PRDS；BH 要求 PRDS，该假设**未验证**，故 BH 的结果只作对照 |")
    A("")
    A("## 3. 可引用清单")
    A("")
    A("| claim_id | 分级 | 结论句 | 统计（m=24） | 经济属性 | 扣成本正收益 | 前瞻 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for c in res["claims"]:
        A(
            f"| `{c['key']}` | {c['tier']} | {_cell(c['headline'])} "
            f"| 四种全过={'是' if c['passes_all_four'] else '否'} "
            f"| {c['economic_short']} "
            f"| {'有' if c['cost_adjusted_positive_evidence'] else '无'} | 无 |"
        )
    A("")
    A("### 3.1 三件事分开说")
    A("")
    A("| 层次 | 这些结论能支持什么 | 不能支持什么 |")
    A("| --- | --- | --- |")
    A("| **统计** | 在给定家族与 m 下，这些差异不是随机噪声 | 不能说明差异有多大用；不能说明其他家族外的结论也成立 |")
    A("| **经济** | `phaseo_netBASE` 在 BASE 费率、3000 万资金、样本内的净 alpha 为正 | 其余 5 条没有扣成本收益；`phasel_alpha` 是零效应判定，方向相反 |")
    A("| **交易可用性** | **没有任何一条提供可交易性证据** | 无前瞻样本、无券商交割单核对、无实盘 |")
    A("")
    A("## 4. 逐条限定（引用时不可省略）")
    A("")
    for c in res["claims"]:
        A(f"### `{c['key']}`｜{c['tier']}")
        A("")
        A(f"- **可引用句**：{c['allowed_wording']}")
        for lim in c["limits_of_use"]:
            A(f"- **限定**：{lim}")
        if c["forbidden"]:
            for b in c["forbidden"]:
                A(f"- **禁止表述**：{b}")
        A(f"- **经济属性**：{c['economic_label']}")
        A(f"- **前瞻**：无（{c['out_of_sample_note']}）")
        A("")
    A("## 5. 适用范围（容易越界的一条）")
    A("")
    A("本清单的「全族校正」只覆盖 **L3u 登记在册的 27 条**。审计已确认至少有一个"
      "已产生完整结论的实验**不在**该家族内（`phasei_out`，exQ5 可成交，"
      "V0 +0.2307pp / t=6.357、V1 +0.1634pp / t=4.528）。")
    A("")
    A("> 因此不能把「通过了全族校正」推广成「项目全部结论都通过了校正」。")
    A("")
    A("## 6. 历史与更正（保留，不覆盖）")
    A("")
    A("| 文件 | 角色 |")
    A("| --- | --- |")
    A("| `docs/DELIVERABLE_QUOTE_SHEET.md` | L3y 的对外定稿（9→6 修订后版本）。本清单不改它 |")
    A("| `reports/L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md` | L3z 审计：3 处实现缺陷的更正 |")
    A("| `reports/L3AA_ARBITRARY_DEPENDENCE_AUDIT.md` | L3aa：BY 复核。其中「m=24 偏大」一句已被本阶段撤回 |")
    A("| `reports/L3AB_TEST_FAMILY_AUDIT.md` | 本阶段：家族审计与撤回记录 |")
    A("")
    A("## 7. 依凭产物")
    A("")
    A("| 文件 | sha256 |")
    A("| --- | --- |")
    for k, v in hashes.items():
        A(f"| `{k}` | `{v[:16]}…` |")
    A("")
    A("## 8. 复现")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3ab_family_audit.py")
    A("PYTHONPATH=backend/src python scripts/l3ab_current_claims.py")
    A("```")
    A("")
    return "\n".join(L) + "\n"


def main() -> int:
    started = time.perf_counter()
    quiet = "--quiet" in sys.argv
    data = _load()
    fam = data["family"]
    generated = datetime.now(TZ).strftime("%Y-%m-%d %H:%M %z")

    hashes = cs.input_hashes(ROOT)
    res = cs.build_status(
        root=ROOT,
        inventory=fam["inventory"],
        family_result={"specs": fam["specs"]},
        cards=data["cards"]["claims"],
        l3u_rows=json.loads(
            (ROOT / "reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json").read_text(
                encoding="utf-8"
            )
        )["rows"],
        retractions=data["cards"].get("retractions", []),
        l3aa=data["l3aa"],
    )
    # 清单状态：审计已完成、但有已记录的限制（家族覆盖不全 + m 口径分歧未定方向）⇒ 带限定标记。
    status = "CURRENT_WITH_DOCUMENTED_LIMITATIONS"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3ab_current_claims.json").write_text(
        json.dumps(
            {"generated_at": generated, "sheet_status": status, "hashes": hashes, **res},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    R_STATUS.write_text(render_status(res, hashes, generated), encoding="utf-8", newline="\n")
    R_SHEET.write_text(render_sheet(res, hashes, generated, status), encoding="utf-8", newline="\n")

    if not quiet:
        print(f"可引用条目      {res['n_quotable']} / {res['n_claims_registered']}")
        for c in res["claims"]:
            print(
                f"  {c['key']:16s} {c['tier']:18s} 四种全过={c['passes_all_four']!s:5s} "
                f"扣成本证据={c['cost_adjusted_positive_evidence']!s:5s} {c['economic_basis']}"
            )
        print(f"清单状态        {status}")
        print(f"产物            {R_STATUS.relative_to(ROOT).as_posix()}")
        print(f"                {R_SHEET.relative_to(ROOT).as_posix()}")
        print(f"耗时 {time.perf_counter() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
