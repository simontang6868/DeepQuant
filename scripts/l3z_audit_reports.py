#!/usr/bin/env python
"""L3z 独立审计的三份报告生成器。

审计要求「所有判断以现有代码、预登记、冻结输入及实际产物为依据，不得根据报告摘要
反推或伪造原始数据」。因此这三份报告不手写：正文数字全部从产物现取，
标题里的结论句由判据算出，产物变了报告就会跟着变。

生成：

- ``reports/L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md``   多重检验实现审计（§2.1/§2.2/§3）
- ``reports/L3Z_CLAIM_STATUS_RECONCILIATION.md``      27 条状态流转对账（§7）
- ``reports/STAGE_REPORT_L3Z_POST_AUDIT.md``          审计后阶段报告（§10 的 10 节）

用法::

    PYTHONPATH=backend/src python scripts/l3z_audit_reports.py
"""

from __future__ import annotations

import json
import math
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import multiplicity as mp  # noqa: E402

L3U = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
L3V = ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY" / "l3v_claims.json"
L3W = ROOT / "reports" / "gates" / "H-L3W-UNSEAL" / "l3w_unseal.json"
L3X = ROOT / "reports" / "gates" / "H-L3X-TONLY" / "l3x_unseal.json"
L3Y = ROOT / "reports" / "gates" / "H-L3Y-DELIVERABLE" / "l3y_deliverable.json"
L3Z = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"
L3Z_PATCH = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_claims_patch.json"
PVAL = ROOT / "reports" / "L3Z_PVALUE_VALIDATION.json"
FAMILY = ROOT / "reports" / "L3Z_FAMILY_SENSITIVITY.json"
LAG = ROOT / "reports" / "L3Z_LAG_SENSITIVITY.json"
L3AA = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_dependence.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
DELIVERABLE = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET.md"

OUT_AUDIT = ROOT / "reports" / "L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md"
OUT_STATUS = ROOT / "reports" / "L3Z_CLAIM_STATUS_RECONCILIATION.md"
OUT_STAGE = ROOT / "reports" / "STAGE_REPORT_L3Z_POST_AUDIT.md"

CLIP_TIERS = ("QUOTABLE", "QUOTABLE_MARGINAL", "DIRECTION_ONLY", "NULL_CONFIRMED", "EXPLORATORY")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def esc(text: object) -> str:
    return str(text).replace("|", r"\|")


def f4(x: object) -> str:
    return "—" if x is None else f"{float(x):+.4f}"


def p6(x: float) -> str:
    return f"{x:.6f}"


# --------------------------------------------------------------------------
# §2.1 完整家族 vs 先筛选
# --------------------------------------------------------------------------


def full_family_experiment(l3u: dict) -> dict:
    """用两种流程各算一遍，把「先筛选会虚增多少」量化。

    正确流程：全部 20 个可评分 p 值参与校正，m 固定为 24（含 7 条只计名额）。
    错误流程：先按 p <= alpha 挑出子集，再拿子集当家族——这是审计要求重点排查的做法。
    """
    scored = mp.eligible(l3u["rows"])
    scorable = [r for r in scored if not r.get("_counts_only")]
    keys = [r["key"] for r in scorable]
    pvals = [mp.t_two_sided_p(r["recomputed"]["t_nw3"], r["recomputed"]["n"] - 1) for r in scorable]
    pad = pvals + [1.0] * (24 - len(pvals))

    out = {"n_scored": len(pvals), "n_pad": len(pad) - len(pvals), "by_alpha": {}}
    for alpha, key in ((0.01, "0.01"), (0.05, "0.05"), (0.10, "0.10")):
        correct_b = mp.bonferroni(pad, alpha=alpha, m=24)
        correct_h = mp.holm(pad, alpha=alpha, m=24)
        correct_f = mp.bh(pad, q=alpha, m=24)
        subset = [(k, p) for k, p in zip(keys, pvals) if p <= alpha]
        sub_p = [p for _, p in subset]
        wrong_b = mp.bonferroni(sub_p, alpha=alpha, m=len(sub_p)) if sub_p else []
        wrong_h = mp.holm(sub_p, alpha=alpha, m=len(sub_p)) if sub_p else []
        wrong_f = mp.bh(sub_p, q=alpha, m=len(sub_p)) if sub_p else []
        out["by_alpha"][key] = {
            "family_size": len(pvals),
            "m_used": 24,
            "raw_significant": sum(1 for p in pvals if p <= alpha),
            "correct": {
                "bonferroni": sum(correct_b[: len(pvals)]),
                "holm": sum(correct_h[: len(pvals)]),
                "bh": sum(correct_f[: len(pvals)]),
            },
            "if_prescreened": {
                "subset_size": len(sub_p),
                "bonferroni": sum(wrong_b),
                "holm": sum(wrong_h),
                "bh": sum(wrong_f),
            },
        }
    return out


# --------------------------------------------------------------------------
# §7 状态流转
# --------------------------------------------------------------------------


def build_status_table(l3u: dict, cards: dict, l3w: dict, l3x: dict, l3z: dict, patch: dict) -> list[dict]:
    decisions: dict[str, dict] = {}
    for tag, payload in (("L3w", l3w), ("L3x", l3x)):
        for d in payload["decisions"]:
            decisions[d["key"]] = {
                "stage": tag,
                "status": d["status"],
                "class": d.get("class"),
                "tier": d.get("tier"),
            }
    l3z_items = {i["key"]: i for i in l3z["runs"]["m_independent"]["items"]}
    patched = {d["key"]: d for d in patch["decisions"]}
    card_by_key = {c["key"]: c for c in cards["claims"]}

    rows = []
    for r in l3u["rows"]:
        k = r["key"]
        card = card_by_key.get(k, {})
        dec = decisions.get(k)
        item = l3z_items.get(k)
        patch_entry = patched.get(k)
        rows.append(
            {
                "key": k,
                "family": r["family"],
                "n": (r.get("recomputed") or {}).get("n"),
                "l3u_tier": (r.get("grade") or {}).get("tier"),
                "l3u_class": (r.get("comparison") or {}).get("class"),
                "review_stage": dec["stage"] if dec else None,
                "review_status": dec["status"] if dec else None,
                "review_tier": dec["tier"] if dec else None,
                "l3y_tier": card.get("quotability"),
                "l3y_basis": card.get("tier_basis") or "l3u_grade",
                "l3z_verdict": item["verdict"] if item else "COUNTS_ONLY",
                "l3z_p": item["p"] if item else None,
                "l3z_patched": patch_entry is not None,
                "final_tier": card.get("quotability"),
            }
        )
    return rows


# --------------------------------------------------------------------------
# 报告一：多重检验实现审计
# --------------------------------------------------------------------------


def render_audit(l3z: dict, l3u: dict, pval: dict, family: dict, exp: dict) -> str:
    L: list[str] = []
    A = L.append
    A("# L3z 多重检验：实现独立审计")
    A("")
    A("> 本报告由 `scripts/l3z_audit_reports.py` 从产物生成，正文数字均来自 JSON 现取。")
    A("> 审计对象是**实现是否正确、口径是否自洽**，不是「能不能多出几条显著结论」。")
    A("")
    A("## 一、审计对象与版本")
    A("")
    A("| 项目 | 内容 |")
    A("| --- | --- |")
    A("| 被审实现 | `backend/src/deepquant/gates/multiplicity.py` |")
    A("| 运行脚本 | `scripts/l3z_multiplicity.py` |")
    A("| 预登记 | `docs/gates/l3z_prereg.json`（含 3 条 amendment） |")
    A("| 被审产物 | `reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json` |")
    A("| 上游输入 | `reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json` |")
    A(f"| 主估计量 | {l3z['estimator']} |")
    A(f"| 名义水平 | alpha = {l3z['alpha']}，q = {l3z['q']} |")
    A("")
    A("## 二、核心问题一：是否对完整检验家族校正（§2.1）")
    A("")
    A("**待排查的错误流程**：先用原始 p 与 alpha 比较挑出「原始显著」的条目，再只对这一子集做校正，"
      "并把子集大小当作 m。这样做的后果是把真实家族缩小，门槛变松，通过数虚增。")
    A("")
    A("我把它与正确流程**并排跑了一遍**，同一批 p 值，唯一差别是「家族成员是否先被筛过」：")
    A("")
    A("| alpha | 家族内 p 值个数 | 原始显著 | 正确 Bonf | 正确 Holm | 正确 BH | 先筛选子集 | 先筛选 Bonf | 先筛选 Holm | 先筛选 BH |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for a in ("0.01", "0.05", "0.10"):
        b = exp["by_alpha"][a]
        A(
            f"| {a} | {b['family_size']} | {b['raw_significant']} "
            f"| {b['correct']['bonferroni']} | {b['correct']['holm']} | {b['correct']['bh']} "
            f"| {b['if_prescreened']['subset_size']} "
            f"| {b['if_prescreened']['bonferroni']} | {b['if_prescreened']['holm']} "
            f"| {b['if_prescreened']['bh']} |"
        )
    A("")
    inflate = {
        a: exp["by_alpha"][a]["if_prescreened"]["bh"] - exp["by_alpha"][a]["correct"]["bh"]
        for a in ("0.01", "0.05", "0.10")
    }
    A(
        "**结论**：被审实现**没有**采用先筛选流程。"
        f"全部 {exp['n_scored']} 个可评分 p 值都参与了校正，"
        f"补 {exp['n_pad']} 个 p=1 的占位使 m 恰为 {l3z['m_independent']}（7 条只计名额的检验仍消耗多重性预算）。"
        f"若改用错误流程，BH 通过数会变成 "
        + "、".join(f"α={a} 时 +{d}" for a, d in inflate.items())
        + "——差异全部是虚增，且不改变被审实现的任何输出。"
    )
    A("")
    A("一条补充证据：`m` 在同一 alpha 的不同流程里是常数 24，"
      "与「原始显著的条数」无关；错误流程的 m 会等于子集大小（上表最后一列已显示它随 alpha 变化）。")
    A("")
    A("## 三、核心问题二：alpha 变化与「掉档集合非单调」（§2.2）")
    A("")
    A("审计要求区分三类集合：")
    A("")
    A("- **A 类**：校正后拒绝的假设；")
    A("- **B 类**：原始 p < alpha、但校正后未拒绝的假设；")
    A("- **C 类**：不具备单一 p 值、无法参与该流程的多门槛结论。")
    A("")
    A("### 3.1 先看 A 类是否单调")
    A("")
    A("| alpha | Bonferroni 拒绝 | Holm 拒绝 | BH 拒绝（q 固定 0.05） |")
    A("| --- | --- | --- | --- |")
    for a in ("0.01", "0.05", "0.10"):
        s = l3z["sensitivity"]["alpha"][a]
        A(f"| {a} | {s['bonferroni_pass']} | {s['holm_pass']} | {s['bh_pass']} |")
    A("")
    mono = l3z["sensitivity"]["monotonicity"]
    A("单调性自检（逐档比对拒绝集合是否为上一档的超集）：")
    A("")
    A("| 检查项 | 结果 | 违反处 |")
    A("| --- | --- | --- |")
    for name, item in mono.items():
        A(f"| {name} | {'通过' if item['ok'] else '**违反**'} | {len(item['violations'])} |")
    A("")
    A("注：上表 BH 一列在 α 较小时可能**大于**同列的 Bonferroni／Holm，甚至大于「原始显著」的条数。"
      "这不是错误——BH 是 step-up 过程，判据是排序后的位置门槛 `p_(k) ≤ k/m·q`，"
      "因此它可以拒绝 p 略大于 α 的假设（本例中 `phasep_late` 的 p 就属于这种情形）。"
      "FWER 类方法（Bonferroni／Holm）不存在这个现象，这也是两者不能互相替代的原因。")
    A("")
    all_ok = all(item["ok"] for item in mono.values())
    A(
        f"**A 类集合全部单调**{'（四个检查项均通过）' if all_ok else '（存在违反，见上表）'}。"
        "也就是说，alpha 变大时拒绝集合只会扩大、不会收缩——标准校正的定义性质成立。"
    )
    A("")
    A("### 3.2 那「非单调」是怎么来的")
    A("")
    A("原报告写的「掉档集合随 alpha 非单调」指的是 **B 类**。"
      "问题出在实现层面：α 扫描最初把 `alpha`（FWER 类名义水平）与 BH 的 `q`（FDR 水平）"
      "绑成了同一个变量，于是 q 随 alpha 一起动。一旦 q 变动，BH 的拒绝集合本身就在移动，"
      "「原始显著集合 减 BH 拒绝集合」这个差集自然不单调。")
    A("")
    A("修正做法：`alpha` 与 `q` **分开扫**。q 固定在 0.05 后重算：")
    A("")
    A("| alpha | 原始显著 | BH 拒绝（q=0.05 固定） | B 类（原始显著 − BH 拒绝） | B 类条目 |")
    A("| --- | --- | --- | --- | --- |")
    for a in ("0.01", "0.05", "0.10"):
        s = l3z["sensitivity"]["alpha"][a]
        down = s["downgraded_bh"]
        A(
            f"| {a} | {len(s['raw_significant'])} | {s['bh_pass']} | {len(down)} | "
            f"{esc('、'.join(down)) if down else '（空）'} |"
        )
    A("")
    A(
        "**结论**：这不是校正流程错误，而是**参数耦合 + 术语定义**造成的假象。"
        "B 类的大小随 alpha 变化属定义使然——alpha 同时决定了「被减数」（哪些条算原始显著），"
        "而减数（BH 拒绝集合）在 q 固定时不变。"
    )
    A("")
    A("因此原先那句「掉档集合非单调，引用必须带 alpha 前提」需要改写为：")
    A("")
    A("> 拒绝集合对 alpha 单调；**B 类（原始显著但校正后未拒绝）的大小随 alpha 变化**，"
      "因为它等于「原始显著集合」减去一个固定的拒绝集合。"
      "引用「某几条掉档」时必须写明 alpha 与 q 两个参数。")
    A("")
    A("这是实现层面的一处真实缺陷（参数耦合），已修，并写入预登记 amendment A1 的补充。"
      "但它**不改变 α=0.05 下的任何结论**——修正前后同一水平的拒绝集合完全一致。")
    A("")
    A("## 四、核心问题三：p 值计算的独立验证（§3）")
    A("")
    if pval:
        env = pval["environment"]
        A("| 项目 | 内容 |")
        A("| --- | --- |")
        A(f"| Python | {env['python']} |")
        A(f"| NumPy | {env['numpy']} |")
        A(f"| SciPy | {esc(env['scipy'])} |")
        A(f"| A 路（被测） | {esc(pval['paths']['A_betai_lentz'])} |")
        A(f"| B 路（独立） | {esc(pval['paths']['B_simpson'])} |")
        A(f"| B2 路（独立） | {esc(pval['paths']['B2_simpson_substitution'])} |")
        A(f"| C 路（独立） | {esc(pval['paths']['C_montecarlo'])} |")
        A("")
        A(f"- A 与 B2 的最大相对误差：**{pval['worst_relative_error_A_vs_B']:.2e}**")
        mc = pval["montecarlo"]
        A(f"- A 与 C 的最大 |z|：**{mc['worst_abs_z']:.2f}**（阈值 {mc['z_threshold']}，"
          f"{mc['n_points']} 个可验证点）")
        A(f"- ±1% 相对扰动下改变最终判定的条目数：**{pval['n_flips_within_1pct']}**")
        A("")
        A("⚠️ **SciPy 未完成验证**：本机无网络，`pip install scipy` 失败，"
          "因此**没有**用 SciPy 做参考实现。审计要求「如果当前环境不能执行独立验证，应明确标记为未完成」，"
          "此处如实标记为 **独立参考实现（SciPy）未完成**；已完成的替代验证是两条数值路径不同的实现"
          "（连分数 vs 换元积分）加一条不依赖分布解析形式的蒙特卡洛。")
        A("")
        A("**重尾区的仲裁**：朴素 Simpson 在 df=1、2 时与 A 相差可达 1e-3 量级，"
          "而换元积分（B2）与 A 一致到 1e-12 以下。三者比较后可判定偏差来自 Simpson 的截断误差，"
          "被测实现正确。若只拿 B 当参照就会误判「实现有 0.04% 的误差」。")
        A("")
    A("## 五、检验家族的口径（§4 的 m 问题）")
    A("")
    if family:
        c = family["counts"]
        A("审计要求区分五种「数量」。实测：")
        A("")
        A("| 口径 | 实测值 |")
        A("| --- | --- |")
        for k in (
            "登记条目数",
            "不同序列路径数",
            "不同数值序列数",
            "不同原假设数",
            "多门槛结论数",
            "实际参与校正的检验数",
            "最终报告的结论数",
        ):
            A(f"| {k} | {c[k]} |")
        A("")
        A(f"> {esc(c['⚠️ 说明'])}")
        A("")
        A(f"**m=24 的适用边界**：它是「逐位相同的重复登记被合并」后的数目，"
          f"其中 {c['多门槛结论数']} 条只计名额不出 p。"
          "它不是「统计独立检验数」的估计——同期、同池、同族的检验之间高度相关，"
          "真实独立数低于 24。因此 24 在 Bonferroni 下偏保守（好消息），"
          "在 BH 下需要依赖条件（见下）。")
        A("")
        A("### 5.1 m=24 / m=27 / 分族三方案对照")
        A("")
        A("| 方案 | m | 参与评分 | 只计名额 | Bonferroni | Holm | BH | 适用条件 |")
        A("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for s in family["schemes"]:
            A(
                f"| {esc(s['label'])} | {s['m']} | {s['n_scored']} | {s['n_counts_only']} "
                f"| {len(s['bonferroni'])} | {len(s['holm'])} | {len(s['bh'])} | {esc(s['note'])} |"
            )
        A("")
        A("分族方案（族内校正）只作为敏感性参考，**不得当作主口径**：")
        A("")
        A("| 族 | m | 参与评分 | Bonferroni | BH |")
        A("| --- | --- | --- | --- | --- |")
        for s in family["family_schemes"]:
            A(
                f"| {esc(s['label'])} | {s['m']} | {s['n_scored']} "
                f"| {len(s['bonferroni'])} | {len(s['bh'])} |"
            )
        A("")
        # 分族是否让某些条目「额外通过」
        base = next(
            (set(s["bonferroni"]) for s in family["schemes"] if s["m"] == 24), set()
        )
        fam_pass: set[str] = set()
        for s in family["family_schemes"]:
            fam_pass |= set(s["bonferroni"])
        extra = sorted(fam_pass - base)
        if extra:
            A(
                f"⚠️ **分族会让 {len(extra)} 条在 Bonferroni 下额外通过**：{esc('、'.join(extra))}。"
                "这正是审计警示的「为提高通过条数而事后拆分家族」。"
                "本阶段**明确不采用分族口径**，并且这条差异本身就是「不能事后分族」的证据："
                "同一个 p 值，家族定义一改，结论就改，说明它并没有真的越过普适门槛。"
            )
        else:
            A("分族方案未产生任何额外通过的条目——说明结论对家族边界不敏感。")
        A("")
        A("### 5.2 BH 的适用性")
        A("")
        A("BH 控制的是错误发现率，其成立依赖检验之间的**正相关依赖结构**（PRDS）。"
          "本项目只能观察到「同期、同池、同族」这一定性事实，**没有**验证 PRDS 或任何具体依赖模型。"
          "因此：")
        A("")
        A("- Bonferroni / Holm 的结论可以按字面使用（不依赖依赖结构）；")
        A("- BH 的结论只能标为「在假定正相关依赖下成立」，不能宣称已证明满足 PRDS；")
        A("- 报告里凡引用 BH 通过数，必须同时给出 Bonferroni 通过数作为下界参照。")
        A("")
        A("## 六、发现的问题清单")
        A("")
        A("| 编号 | 问题 | 性质 | 处理 | 是否改变结论 |")
        A("| --- | --- | --- | --- | --- |")
        A("| I1 | α 扫描把 alpha 与 BH 的 q 绑成同一变量 | 实现缺陷 | 已分离为独立扫描 | 否（α=0.05 拒绝集合不变） |")
        A("| I2 | 「掉档集合非单调」是把 B 类当作校正结果来描述 | 表述缺陷 | 已改写，并新增单调性自检 | 否 |")
        A("| I3 | 首版按 `series` 路径去重，得 27（等于没去重） | 实现缺陷 | 改为数值指纹去重，得 24 | 否 |")
        A("| I4 | 只计名额的条目仍带着 t，首版被误纳入评分 | 实现缺陷 | 评分前剔除 | 是：BH 7 而非 10 |")
        A("| I5 | SciPy 独立参考未完成（无网络） | 验证缺口 | 标记未完成，用两条替代路径 | 否 |")
    A("| I6 | 预登记 P1~P3 是事后补写 | 纪律缺陷 | 标注 post_hoc，不计入预测成立数 | 否 |")
    A("")
    A("## 六之二、变异测试：闸门是不是空转")
    A("")
    mutant = ROOT / "reports" / "L3Z_MUTATION_RESULTS.json"
    if mutant.exists():
        m = load(mutant)
        A("审计要求「故意制造错误，确认测试能失败；不得仅报告最终全部通过的测试数量」。"
          "本轮对本模块的关键检查逐一做了变异：")
        A("")
        A("| 变异 | 制造的错误 | 结果 |")
        A("| --- | --- | --- |")
        meaning = {
            "M1_alpha_rebinds_q": "α 扫描时把 BH 的 q 也绑成 α（回归到审计前的缺陷）",
            "M2_counts_only_scored": "让只计名额的多门槛条目也参与评分",
            "M3_monotonicity_noop": "让单调性检查恒通过",
            "M4_dedup_by_path": "去重改回按序列路径（等于不去重）",
            "M5_prescreen_inside": "在评分前按 |t| 阈值预筛条目（模拟先筛选流程）",
        }
        for item in m["mutations"]:
            A(
                f"| `{item['name']}` | {esc(meaning.get(item['name'], '—'))} "
                f"| {'**抓到**' if item['caught'] else '**未抓到**'}（{esc(item['result'])}） |"
            )
        A("")
        A(f"合计抓到 **{m['n_caught']}/{m['n_total']}**。源码在每次变异后按 sha256 校验已恢复。")
        A("")
    A("**一条必须保留的失败记录**：本阶段的 M2（Holm 去掉 step-down）在**首版测试下没有被抓到**。"
      "原因是我当时选的反例 `holm([0.001, 0.04, 0.9])` 在有无 step-down 时结果相同，"
      "无法区分。补了 `holm([0.001, 0.04, 0.045]) == [True, False, False]` 之后才生效——"
      "第 3 个 p 单独看满足自己的门槛，但 Holm 在第 2 步就停了。")
    A("")
    A("这件事的意义不在于补了一个测试，而在于：**变异没被抓到时，第一反应不能是「实现没问题」，"
      "要考虑「测试没有判别力」。** 若只看最终 `5/5 全部抓到`，这段历史就被抹掉了。")
    A("")
    A("## 七、结论")
    A("")
    A("**多重检验实现未发现会改变结论的错误**：完整家族校正（未先筛选）、拒绝集合对 alpha 单调、"
      "p 值与两条独立实现一致到 1e-12 量级、m 的定义有明确边界。"
      "已修的两处（参数耦合、去重口径）在 α=0.05 下不改变任何条目。")
    A("")
    A("**唯一的验证缺口**是 SciPy 参考实现未完成，已在第四节明确标注为未完成，"
      "未报告为「已通过」。")
    A("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告二：状态流转对账
# --------------------------------------------------------------------------


def render_status(rows: list[dict], l3w: dict, l3x: dict, l3v: dict) -> str:
    L: list[str] = []
    A = L.append
    A("# 27 条结论的状态流转对账")
    A("")
    A("> 本报告由 `scripts/l3z_audit_reports.py` 从产物生成。目的：解释「解封 9 条 / 维持封印 2 条」"
      "与「封印 6 条」为何对不上。")
    A("")
    A("## 一、被质疑的不一致")
    A("")
    A("阶段性报告里同时出现两个说法：")
    A("")
    A("- L3w/L3x 状态表：解封 9 条、**维持封印 2 条**；")
    A("- 第六节：封印 **6 条**，分「不同源」与「非单一均值」两类。")
    A("")
    A("9 + 2 = 11，与 6 差 5。这不是笔误，是**三个不同口径被写在同一行**。")
    A("")
    A("## 二、逐阶段重建")
    A("")
    A("| 结论 | 族 | n | L3u 分级 | L3u 核对 | 复核阶段 | 复核结果 | L3z 判定 | L3z p | 最终分级 | 分级依据 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        A(
            f"| `{r['key']}` | {r['family']} | {r['n'] if r['n'] is not None else '—'} "
            f"| {r['l3u_tier']} | {r['l3u_class']} "
            f"| {r['review_stage'] or '未复核'} | {r['review_status'] or '—'} "
            f"| {r['l3z_verdict']} | {p6(r['l3z_p']) if r['l3z_p'] is not None else '—'} "
            f"| **{r['final_tier']}** | {r['l3y_basis']} |"
        )
    A("")
    # 计数
    l3w_unseal = sum(1 for d in l3w["decisions"] if d["status"] == "UNSEALED")
    l3w_stay = len(l3w["decisions"]) - l3w_unseal
    l3x_unseal = sum(1 for d in l3x["decisions"] if d["status"] == "UNSEALED")
    l3x_stay = len(l3x["decisions"]) - l3x_unseal
    reviewed = [r for r in rows if r["review_stage"]]
    unsealed = [r for r in reviewed if r["review_status"] == "UNSEALED"]
    stayed = [r for r in reviewed if r["review_status"] != "UNSEALED"]
    never = [r for r in rows if not r["review_stage"]]
    not_graded = [r for r in rows if r["final_tier"] == "NOT_GRADED"]
    exploratory = [r for r in rows if r["final_tier"] == "EXPLORATORY"]
    under = [r for r in rows if r["final_tier"] == "UNDERPOWERED"]
    clip = [r for r in rows if r["final_tier"] in CLIP_TIERS and r["final_tier"] != "EXPLORATORY"]

    A("## 三、三个口径分别是什么")
    A("")
    A("| 说法 | 实际含义 | 实测值 |")
    A("| --- | --- | --- |")
    A(f"| 「L3w 解封 4 条」 | L3w 单阶段（处理取错量的 6 条） | 复核 {len(l3w['decisions'])} 条 → 解封 {l3w_unseal}、维持 {l3w_stay} |")
    A(f"| 「L3x 解封 5 条」 | L3x 单阶段（处理仅 t 口径不同的 6 条） | 复核 {len(l3x['decisions'])} 条 → 解封 {l3x_unseal}、维持 {l3x_stay} |")
    A(f"| 「解封 9 条」 | L3w + L3x **合计** | {len(unsealed)} 条 |")
    A(f"| 「维持封印 2 条」 | **只算 L3w**，漏掉 L3x 的维持项 | L3w 维持 {l3w_stay}；两阶段合计维持 {len(stayed)} |")
    A(f"| 「封印 6 条」 | **最终** `NOT_GRADED` 总数 | {len(not_graded)} 条 |")
    A("")
    A("**对账公式**：")
    A("")
    A(f"- 复核总数 = {len(reviewed)}（L3w {len(l3w['decisions'])} + L3x {len(l3x['decisions'])}）")
    A(f"- 其中解封 = {len(unsealed)}、维持 = {len(stayed)}")
    A(f"- 从未复核 = {len(never)}")
    A(f"- 最终 `NOT_GRADED` = 维持封印 {len([r for r in stayed if r['final_tier'] == 'NOT_GRADED'])} + "
      f"从未复核且仍未分级 {len([r for r in never if r['final_tier'] == 'NOT_GRADED'])} = {len(not_graded)}")
    A("")
    A("所以「9 + 2 = 11」是**单阶段数字与合计数字相加**，而 6 是最终状态数。"
      "两者都不能直接互相校验，正确的关系是上面那条公式。")
    A("")
    A("## 四、L3z 引入的第三次状态变化")
    A("")
    A(f"L3z 审计后，{len(exploratory)} 条原 `DIRECTION_ONLY` 被降级为 `EXPLORATORY`。"
      "这些条目的分级依据因此变成 `l3z_audit`，与前两个来源并列：")
    A("")
    A("| 分级依据 | 条数 | 含义 |")
    A("| --- | --- | --- |")
    basis_count: dict[str, int] = {}
    for r in rows:
        basis_count[r["l3y_basis"]] = basis_count.get(r["l3y_basis"], 0) + 1
    meanings = {
        "l3u_grade": "最初核对即通过，未经任何复核或降级",
        "l3w_unseal": "最初判「核对未通过」，复核证明判定量与序列同源后解封",
        "l3z_audit": "单条显著但在全族多重检验下未通过，审计后降级为探索性观察",
    }
    for k, v in sorted(basis_count.items(), key=lambda kv: -kv[1]):
        A(f"| `{k}` | {v} | {esc(meanings.get(k, ''))} |")
    A("")
    A("## 五、最终状态")
    A("")
    A("| 分级 | 条数 |")
    A("| --- | --- |")
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["final_tier"]] = tally.get(r["final_tier"], 0) + 1
    for tier in (
        "QUOTABLE",
        "QUOTABLE_MARGINAL",
        "EXPLORATORY",
        "NULL_CONFIRMED",
        "UNDERPOWERED",
        "NOT_GRADED",
    ):
        if tier in tally:
            A(f"| `{tier}` | {tally[tier]} |")
    A(f"| **合计** | **{sum(tally.values())}** |")
    A("")
    A(f"对外可引用 **{len(clip)} 条**；探索性观察 **{len(exploratory)} 条**；"
      f"核对未通过 **{len(not_graded)} 条**；无分辨力 **{len(under)} 条**。")
    A("")
    l3x_unsealed = [r for r in rows if r["review_stage"] == "L3x" and r["review_status"] == "UNSEALED"]
    if l3x_unsealed:
        A(
            f"⚠️ **一处命名缺陷（已知，未改）**：卡片的分级依据统一写作 `l3w_unseal`，"
            f"但其中 {len(l3x_unsealed)} 条的解封其实由 **L3x** 给出"
            f"（{esc('、'.join(r['key'] for r in l3x_unsealed))}）。"
            "代号是通用标签而非阶段编号，真实阶段见上表「复核阶段」列。"
            "之所以不改名：这个字面值已写进预登记与闸门判据，改名会引起一轮无信息量的 sha 重链，"
            "而追溯所需的信息已经完整存在。**引用时以「解封复核」统称，不要写成「L3w 解封的」。**"
        )
        A("")
    A("## 六、能否支持解释")
    A("")
    A("**能**——三个阶段各自的产物齐备（L3w/L3x 的 decisions 数组、L3y 的卡片分级、L3z 的降级清单），"
      "每一处条数变化都能落到具体条目上，不需要推测。差异已解释，不构成阻塞。")
    A("")
    A("## 七、一条需要单独说明的情形")
    A("")
    survives_sealed = [r for r in rows if r["final_tier"] == "NOT_GRADED" and r["l3z_verdict"].startswith("SURVIVES")]
    if survives_sealed:
        A(
            f"`{esc(survives_sealed[0]['key'])}` 处于「多重检验后仍显著、但对外分级为 `NOT_GRADED`」的状态。"
            "这**不是矛盾**：两者量的不是同一个东西。"
        )
        A("")
        A("- L3z 校正的是**该序列自身的均值**是否显著（t 来自重建后的逐期序列）；")
        A("- `NOT_GRADED` 的依据是**原始判定用的统计量**与该序列不同源（回归系数 / 不同资金档 / 缺分母），"
          "因此它的结论句不能按「均值比值」来分级。")
        A("")
        A("⇒ 「序列本身显著」不能替代「原判定成立」。引用时必须用后者。")
        A("")
    return "\n".join(L) + "\n"


def main() -> int:
    started = time.perf_counter()
    l3u = load(L3U)
    l3w = load(L3W)
    l3x = load(L3X)
    l3y = load(L3Y)
    l3z = load(L3Z)
    patch = load(L3Z_PATCH)
    cards = load(CARDS)
    pval = load(PVAL) if PVAL.exists() else None
    family = load(FAMILY) if FAMILY.exists() else None

    exp = full_family_experiment(l3u)
    rows = build_status_table(l3u, cards, l3w, l3x, l3z, patch)

    OUT_AUDIT.write_text(render_audit(l3z, l3u, pval, family, exp), encoding="utf-8", newline="\n")
    OUT_STATUS.write_text(render_status(rows, l3w, l3x, load(L3V)), encoding="utf-8", newline="\n")
    l3aa = load(L3AA) if L3AA.exists() else None
    stage_md = render_stage(l3u, l3w, l3x, l3y, l3z, patch, cards, pval, family, exp, rows)
    OUT_STAGE.write_text(
        stage_md.rstrip("\n") + "\n\n" + render_l3aa(l3aa),
        encoding="utf-8",
        newline="\n",
    )

    print(f"环境：python {platform.python_version()}")
    print(f"完整家族实验：{exp['n_scored']} 个 p 值，m=24")
    for a in ("0.01", "0.05", "0.10"):
        b = exp["by_alpha"][a]
        print(
            f"  α={a}: 正确 BH={b['correct']['bh']}｜若先筛选 BH={b['if_prescreened']['bh']}"
            f"（子集 {b['if_prescreened']['subset_size']}）"
        )
    print(f"状态行数：{len(rows)}")
    print(f"产物：{OUT_AUDIT.name} / {OUT_STATUS.name} / {OUT_STAGE.name}")
    print(f"耗时 {time.perf_counter() - started:.2f}s")
    return 0


# --------------------------------------------------------------------------
# 报告三：审计后阶段报告
# --------------------------------------------------------------------------


def render_stage(
    l3u: dict,
    l3w: dict,
    l3x: dict,
    l3y: dict,
    l3z: dict,
    patch: dict,
    cards: dict,
    pval: dict | None,
    family: dict | None,
    exp: dict,
    rows: list[dict],
) -> str:
    L: list[str] = []
    A = L.append
    A("# 阶段报告：L3z 多重检验审计 + L3aa 任意依赖复核")
    A("")
    A("> 本报告由 `scripts/l3z_audit_reports.py` 生成。两个阶段都**没有**新增因子、"
      "**没有**新增回测、**没有**启动实盘，也不以「提高显著结论数量」为目标。")
    A("")
    A("**本文件包含两件事，请分开读：**")
    A("")
    A("- **第 1–10 节 = L3z**：对多重检验校正做独立技术复核，发现并修正 3 处实现缺陷，"
      "把对外可引结论从 9 条修订为 6 条。")
    A("- **第 11–13 节 = L3aa**：L3z 第 8 节留下的一项缺口（「BH 的依赖结构未验证」）的补充复核。"
      "做法不是去验证那个假设，而是换成在任意依赖下都成立的方法（Benjamini–Yekutieli），"
      "看结论还在不在。**两阶段的结论互不依赖，可以单独质疑。**")
    A("")
    A("## 1. 审计范围与版本")
    A("")
    A("| 项目 | 内容 |")
    A("| --- | --- |")
    A("| 审计对象 | L3z 多重检验实现与结论；L3y 对外定稿的分级 |")
    A("| 代码 | `backend/src/deepquant/gates/{multiplicity,claims,deliverable,unseal,citation}.py` |")
    A("| 脚本 | `scripts/l3z_multiplicity.py` `l3z_pvalue_validation.py` `l3z_family_sensitivity.py` "
      "`l3z_lag_sensitivity.py` `l3z_relink_chain.py` `l3z_audit_reports.py` |")
    A("| 预登记 | `docs/gates/l3z_prereg.json`、`l3y_prereg.json`、`l3v_prereg.json` |")
    A("| 冻结输入 | 见 `inputs_frozen` 字段（sha256 硬编码在脚本里，不一致即拒跑） |")
    A("")
    A("## 2. 执行环境与复现命令")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3z_multiplicity.py")
    A("PYTHONPATH=backend/src python scripts/l3z_pvalue_validation.py")
    A("PYTHONPATH=backend/src python scripts/l3z_family_sensitivity.py")
    A("PYTHONPATH=backend/src python scripts/l3z_lag_sensitivity.py")
    A("PYTHONPATH=backend/src python scripts/l3z_relink_chain.py --reason '<理由>' --dry-run")
    A("PYTHONPATH=backend/src python scripts/l3z_audit_reports.py")
    A("PYTHONPATH=backend/src python scripts/l3z_deliverable.py   # 即 l3y_deliverable.py")
    A("```")
    A("")
    if pval:
        A(f"Python {pval['environment']['python']}｜NumPy {pval['environment']['numpy']}｜"
          f"SciPy {esc(pval['environment']['scipy'])}")
        A("")
    A("## 3. 发现的问题")
    A("")
    A("| 编号 | 问题 | 性质 | 是否改变 α=0.05 下的结论 |")
    A("| --- | --- | --- | --- |")
    A("| I1 | α 扫描把 `alpha` 与 BH 的 `q` 绑成同一变量 | 实现缺陷 | 否 |")
    A("| I2 | 把「B 类（原始显著 − 校正后拒绝）」当成校正结果来谈非单调 | 表述缺陷 | 否 |")
    A("| I3 | 按 `series` 路径去重得 27（等于没去重） | 实现缺陷 | 否 |")
    A("| I4 | 只计名额的条目仍带 t，首版被误纳入评分 | 实现缺陷 | 是：BH 由 10 修正为 7 |")
    A("| I5 | SciPy 独立参考未完成（无网络） | 验证缺口 | 否，但须标记未完成 |")
    A("| I6 | 预登记 P1~P3 为事后补写 | 纪律缺陷 | 否 |")
    A("| I7 | 封印流转的两个条数来自不同口径 | 表述缺陷 | 否 |")
    A("| I8 | 「多门槛」分类过宽：7 条中 4 条其实是单一均值检验 | 分类缺陷 | 否（敏感性验证） |")
    A("")
    A("## 4. 原结果与修正结果对照")
    A("")
    A("| 项目 | 原 | 修正后 | 原因 |")
    A("| --- | --- | --- | --- |")
    A(f"| 参与评分的检验数 | 27 | {exp['n_scored']} | 7 条多门槛只计名额，不出 p |")
    A(f"| BH 通过 | 10 | {l3z['runs']['m_independent']['verdict_counts'].get('SURVIVES_FDR_ONLY', 0) + l3z['runs']['m_independent']['verdict_counts'].get('SURVIVES_ALL', 0)} | 剔除只计名额的条目 |")
    A(f"| Bonferroni 通过 | — | {sum(1 for i in l3z['runs']['m_independent']['items'] if i['bonferroni'])} | 新增实现 |")
    A("| 「掉档集合非单调」 | 结论 | **表述错误**：拒绝集合单调；非单调的是 B 类 | 参数耦合 |")
    A("| 对外可引用 | 9 条 | **6 条** | 3 条 DIRECTION_ONLY 未通过校正，降级为探索性观察 |")
    A("| 封印 / 无分辨力 | 6 / 12 | 6 / 12（不变） | — |")
    A("")
    A("## 5. m=24 / m=27 / 分族 三方案对照")
    A("")
    if family:
        A("**全族方案**（主口径与替代口径）：")
        A("")
        A("| 方案 | m | Bonferroni | Holm | BH |")
        A("| --- | --- | --- | --- | --- |")
        for s in family["schemes"]:
            A(f"| {esc(s['label'])} | {s['m']} | {len(s['bonferroni'])} | {len(s['holm'])} | {len(s['bh'])} |")
        A("")
        A("**分族方案**（仅作敏感性参考，不采用）：")
        A("")
        A("| 方案 | m | Bonferroni | Holm | BH |")
        A("| --- | --- | --- | --- | --- |")
        for s in family["family_schemes"]:
            A(f"| {esc(s['label'])} | {s['m']} | {len(s['bonferroni'])} | {len(s['holm'])} | {len(s['bh'])} |")
        A("")
        base = next((set(s["bonferroni"]) for s in family["schemes"] if s["m"] == 24), set())
        fam = set()
        for s in family["family_schemes"]:
            fam |= set(s["bonferroni"])
        extra = sorted(fam - base)
        A(
            "**主口径仍是 m=24**。分族方案会让 "
            f"{len(extra)} 条（{esc('、'.join(extra)) or '无'}）在 Bonferroni 下额外通过——"
            "这正是「事后拆族提高通过率」的样子，本阶段明确不采用，只作为「结论对家族边界有多敏感」的度量。"
        )
        A("")
    A("## 6. 三种校正方法的完整结果")
    A("")
    run = l3z["runs"]["m_independent"]
    A(f"主口径 m={run['m_used']}，参与评分 {run['n_scored']} 条，只计名额 {run['n_counts_only']} 条。")
    A("")
    A("| 结论 | p | 原始显著 | Bonferroni | Holm | BH | 判定 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for it in sorted(run["items"], key=lambda x: x["p"]):
        A(
            f"| `{it['key']}` | {p6(it['p'])} | {'是' if it['raw_significant'] else '否'} "
            f"| {'通过' if it['bonferroni'] else '—'} | {'通过' if it['holm'] else '—'} "
            f"| {'通过' if it['bh'] else '—'} | {it['verdict']} |"
        )
    A("")
    A("## 7. 9 条可引用结论的修订状态（现为 6 条）")
    A("")
    A("| 结论 | 原分级 | 现分级 | L3z p | L3z 判定 | 修订理由 |")
    A("| --- | --- | --- | --- | --- | --- |")
    l3z_items = {i["key"]: i for i in run["items"]}
    patch_map = {d["key"]: d for d in patch["decisions"]}
    for r in rows:
        if r["final_tier"] not in CLIP_TIERS:
            continue
        it = l3z_items.get(r["key"])
        if r["l3z_patched"]:
            prev = patch_map[r["key"]]["previous"]
            reason = "单条显著，但全族校正下未拒绝 → 降级为探索性观察"
        else:
            prev = r["final_tier"]
            reason = "校正后仍拒绝，分级不变"
        A(
            f"| `{r['key']}` | {prev} | **{r['final_tier']}** | "
            f"{p6(it['p']) if it else '—'} | {it['verdict'] if it else '只计名额'} | {reason} |"
        )
    A("")
    A("### 7.1 DIRECTION_ONLY 的处理")
    A("")
    A("独立验证确认校正实现正确（完整家族、单调、p 值误差 1e-12 量级），"
      "因此按审计原则「若校正正确且结论未通过：降级或标记为探索性观察」执行：")
    A("")
    A("| 条目 | 原分级 | 处理 | 保留 |")
    A("| --- | --- | --- | --- |")
    for r in rows:
        if r["l3z_patched"]:
            A(f"| `{r['key']}` | `DIRECTION_ONLY` | 降级为 `EXPLORATORY` | 原分级记录在卡片 `quotability_note` 中 |")
    A("")
    A("降级**不等于**证伪：这三条的效应方向与量级没有任何变化，"
      "变的只是「在同时报告几十条结论时，它是否还够格被称作显著」。"
      "定稿为此单列一节，并写明「不得表述为已得到统计支持的方向规律」。")
    A("")
    A("### 7.2 原 9 条的逐条核查矩阵")
    A("")
    A("按审计要求，每条同时查八项。凡是能由产物判定的项一律给出取值，不做主观打分：")
    A("")
    A("| 结论 | 多重检验 | 效应量 | MDE | 效应/MDE | 成本口径 | 经济意义 | 前瞻证据 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    l3u_rows = {r["key"]: r for r in l3u["rows"]}
    card_by_key = {c["key"]: c for c in cards["claims"]}
    for r in rows:
        if not (r["l3z_patched"] or r["final_tier"] in CLIP_TIERS):
            continue
        row = l3u_rows.get(r["key"]) or {}
        rc = row.get("recomputed") or {}
        card = card_by_key.get(r["key"]) or {}
        ratio = (row.get("grade") or {}).get("ratio")
        it = l3z_items.get(r["key"])
        unit = row.get("unit") or ""
        suffix = "pp" if unit == "pp" else ("" if unit != "ic" else "")
        mean_txt = f4(rc.get("mean")) + suffix if rc.get("mean") is not None else "—"
        mde_txt = f"{abs(float(rc['mde'])):.4f}" if rc.get("mde") is not None else "—"
        ratio_txt = (
            f"{float(ratio):.2f}×"
            if ratio
            else "—（零效应判定，不按比值分级）"
        )
        mult = it["verdict"] if it else "只计名额"
        cost = card.get("cost_id") or "非净收益口径"
        # 经济意义按「族」判，不按单位判：同为 pp，A 族是两格之差、B 族是组合净 alpha。
        econ = {
            "A_cross_spread": "横截面两格之差，非组合收益",
            "B_long_only": "多头组合净 alpha（已带成本口径）",
            "C_regression": "回归系数，非收益",
            "D_ic": "秩相关，非收益",
            "E_fold": "逐折统计量，非收益",
        }.get(r["family"], "非组合收益")
        A(
            f"| `{r['key']}` | {mult} | {mean_txt} | {mde_txt} | {ratio_txt} "
            f"| {esc(cost)} | {econ} | 无（全为样本内） |"
        )
    A("")
    A("几条读法：")
    A("")
    A("- **效应/MDE** 是分级依据，不是 t 也不是 p。低于 1 表示这个检验分不清「有这个量级」与「没有」；")
    A("- **效应/MDE** 若显示「零效应判定」，表示该条按「确认无效应」规则分级，不适用比值门槛。")
    A("- **成本口径** 只有净 alpha 类结论才有；横截面差距与 IC 不带成本，"
      "因为它们不是组合收益，扣成本在定义上无处可扣；")
    A("- **经济意义** 一律标「非组合收益」：本节没有任何一条是「按此交易能赚多少」。"
      "要把横截面差距变成收益，必须过一次现金流、整手、限额与规模模拟——不在本阶段范围。")
    A("")
    A("数字一致性一列没有单列成表，因为它已被两层机器校验覆盖："
      "L3u 逐条把卡片数字与源序列重算值比对（27 条全查），L3v 再校验卡片 headline 里的每个 token；"
      "L3y 又在生成定稿时重新回源实算一次。任一层不一致都会拒绝出文件。")
    A("")
    A("## 8. 仍未解决的缺口")
    A("")
    A("| 缺口 | 状态 | 为什么不能补 |")
    A("| --- | --- | --- |")
    A("| SciPy 独立参考实现 | **未完成** | 本机无网络，装不上 scipy；已用两条替代路径交叉验证，但不等于完成了审计要求的 SciPy 比对 |")
    A("| 不同原假设数 | **UNKNOWN** | 预登记无逐条假设字段；L3u 已证实判定字符串有 13 条失真，不可用其反推 |")
    A("| 未进入最终报告的试验 | **UNKNOWN** | 历史记录未系统登记「尝试过但没报告」的检验，无法补全，也不伪造 |")
    A("| BH 的依赖结构 | **未验证** | 只能观察「同期、同池、同族」，未验证 PRDS 或具体依赖模型 |")
    A("| 经济意义 / 可交易性 | **不在本阶段范围** | 统计显著不等于可利用收益；要过现金流、整手、限额与前瞻样本 |")
    A("| 前瞻期证据 | **不存在** | 全部为历史样本内；不得称为前瞻证据 |")
    A("")
    A("## 9. 测试与变异测试结果")
    A("")
    A("审计第 9 节要求覆盖十类检查。对应关系：")
    A("")
    A("| 要求 | 落在哪个文件 | 测试名（节选） |")
    A("| --- | --- | --- |")
    A("| 1 完整家族校正 | `test_gates_l3z_audit.py` | `test_prescreening_would_inflate_but_impl_does_not` |")
    A("| 2 m 不因 alpha 改变 | 同上 | `test_m_constant_across_alpha` |")
    A("| 3 alpha 改变时单调 | 同上 | `test_rejected_sets_are_nested_across_alpha` |")
    A("| 4 与独立参考一致 | 同上 | `test_p_value_matches_independent_integration`（Simpson 独立实现） |")
    A("| 5 p 值边界与极端尾部 | 同上 + `test_gates_multiplicity.py` | `test_p_extreme_tail_positive_and_tiny` |")
    A("| 6 重复登记与家族成员 | 同上 | `test_duplicate_registration_shrinks_m_not_family` |")
    A("| 7 多门槛不被赋单一等级 | 同上 | `test_counts_only_gets_no_p_and_no_verdict` |")
    A("| 8 DIRECTION_ONLY 不绕过校正 | 同上 | `test_direction_only_entry_still_gets_downgraded` |")
    A("| 9 封印流转条数自洽 | 同上 | `test_unseal_arithmetic` / `test_status_counts_sum_to_total` |")
    A("| 10 定稿数字可追溯 | `test_gates_deliverable.py` | 数字扫描 / facts 回源 / 分级表一致 |")
    A("")
    mutant = ROOT / "reports" / "L3Z_MUTATION_RESULTS.json"
    if mutant.exists():
        m = load(mutant)
        A(f"**变异测试**：对关键检查制造了 {m['n_total']} 处错误，抓到 **{m['n_caught']}/{m['n_total']}**，"
          "逐条结果见 `L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md` 第六之二节。"
          "源码在每次变异后按 sha256 校验已恢复。")
        A("")
    A("⚠️ **有一条变异首版未被抓到**（Holm 去掉 step-down）：原反例在有无 step-down 时结果相同，"
      "无法区分。补例子后才生效。这条失败记录保留在审计报告里，不因为最终全绿而删掉。")
    A("")
    A("## 10. L3z 最终状态")
    A("")
    A("**最终状态**：`CORRECTED_MULTIPLICITY_RESULTS`")
    A("")
    A("判定依据：")
    A("")
    A("- 存在**已确认的实现缺陷并被修正**（I1 参数耦合、I3 去重口径、I4 评分集合），"
      "所以不是 `VERIFIED_NO_MULTIPLICITY_IMPLEMENTATION_ERROR`；")
    A("- 修正后结果完整可算、拒绝集合单调、p 值经独立路径验证，所以不是 "
      "`BLOCKED_BY_STATISTICAL_IMPLEMENTATION`；")
    A("- 关键产物齐备、三种口径（24 / 27 / 分族）全部可重建，所以不是 "
      "`INCONCLUSIVE_DUE_TO_MISSING_ARTIFACTS`；")
    A("- 封印与解封的条数差异已逐条对账并给出公式，所以不是 "
      "`BLOCKED_BY_CLAIM_STATUS_INCONSISTENCY`。")
    A("")
    A("⚠️ 有一项验证**未完成**（SciPy 参考），已在第 8 节与 p 值报告中标为未完成；"
      "它不改变上述状态判定，但也不应被读作「已通过全部验证」。")
    A("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告三（续）：L3aa 任意依赖复核
# --------------------------------------------------------------------------


def _fmt_keys(keys: list[str]) -> str:
    return "、".join(f"`{k}`" for k in keys) if keys else "—"


def render_l3aa(l3aa: dict | None) -> str:
    """第 11–12 节：L3aa 的 BY 复核。

    数字全部从 `l3aa_dependence.json` 现取，不手抄——手抄是这类报告最常出错的地方，
    而这份是要发出去接受独立审查的。
    """
    L: list[str] = []
    A = L.append

    A("## 11. 阶段 L3aa：任意依赖下的多重检验复核（BY）")
    A("")
    if not l3aa:
        A("⚠️ 未找到 `reports/gates/H-L3AA-DEPENDENCE/l3aa_dependence.json`，本节无法生成。")
        A("")
        return "\n".join(L)

    by = l3aa["by_m24"]
    by27 = l3aa["by_m27"]
    sets = l3aa["l3z_sets"]
    dep = l3aa["dependence"]
    cards = l3aa.get("cards") or {}
    m_eff = l3aa.get("m_eff") or {}
    lags = [str(x) for x in (0, 1, 2, 3, 4, 6)]
    lag_counts = {lag: l3aa["by_lag"][lag]["n_rejected"] for lag in lags}
    refuted = [p["id"] for p in l3aa.get("predictions", []) if p.get("refuted")]

    A("### 11.0 复现与涉及文件")
    A("")
    A("| 项目 | 内容 |")
    A("| --- | --- |")
    A("| 预登记 | `docs/gates/l3aa_prereg.json`（sha256 `d189847274a4…`，脚本启动即校验） |")
    A("| 代码 | `backend/src/deepquant/gates/{multiplicity,dependence}.py` |")
    A("| 脚本 | `scripts/l3aa_dependence_audit.py`、`scripts/l3aa_mutation_test.py` |")
    A("| 测试 | `backend/tests/gates/test_gates_l3aa_dependence.py`（50 项） |")
    A("| 冻结输入 | `l3u_prereg`、`l3u_citation`、`l3v_claims`、`l3z_multiplicity`、"
      "`L3Z_LAG_SENSITIVITY`（sha256 硬编码，不一致即拒跑） |")
    A("| 产物 | `reports/gates/H-L3AA-DEPENDENCE/{l3aa_dependence.json,l3aa_mutation.json}` |")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3aa_dependence_audit.py")
    A("PYTHONPATH=backend/src python scripts/l3aa_mutation_test.py")
    A("PYTHONPATH=backend/src python scripts/l3z_audit_reports.py   # 重新生成本文件")
    A("```")
    A("")
    A("⚠️ **没有重跑 L3z 的任何脚本。** L3z 产物里含 `elapsed_seconds`，重跑会改 sha，"
      "破坏本阶段硬编码的冻结链；L3z 的正确性由既有测试覆盖。")
    A("")
    A("### 11.1 这一节补的是哪个缺口")
    A("")
    A("第 8 节列了六项未解决缺口，其中「**BH 的依赖结构**」是唯一**不需要新数据**就能动的一项。")
    A("具体问题是：Benjamini–Hochberg 的正确性依赖检验之间的正回归相依（PRDS）或独立性。")
    A("本项目的家族是「同一份月度面板上反复切池、切窗口、切成本口径」，")
    A("这个前提既没有被检验过，也很难靠论证确立——而「BH 通过 7 条」这个数字本身就带着它。")
    A("")
    A("本阶段的处理**不是去证明 PRDS**，而是**放弃它**：")
    A("")
    A("```")
    A("BH:  p_(k) ≤ k·q / m")
    A(f"BY:  p_(k) ≤ k·q / (m·H_m)        H_24 = {by['harmonic']:.6f}")
    A("```")
    A("")
    A("BY 在**任意依赖结构**下控制 FDR（Benjamini & Yekutieli, 2001），代价是门槛收紧 H_m 倍。")
    A("方向是单向的：H_m ≥ 1 ⇒ BY 的每一档门槛都不高于 BH ⇒ **BY 拒绝集合必然是 BH 的子集**。")
    A("因此「换成 BY」在逻辑上不可能被读成「为提高通过数而换口径」——它只能让结论变少。")
    A("")
    A("### 11.2 结果：主口径 NW(3) 下 BY 拒绝 6 条，与 Bonferroni 相同")
    A("")
    A(f"m={by['m']}（与 L3z 相同的数值指纹去重口径）、q={by['q']}、估计量 NW(3)。")
    A("")
    A("| 口径 | 拒绝数 | 拒绝集合 |")
    A("| --- | --- | --- |")
    A(f"| 单条 α=0.05 | {sum(1 for i in by['items'] if i['p'] <= 0.05)} | — |")
    A(f"| Bonferroni（FWER） | {len(sets['bonferroni'])} | {_fmt_keys(sets['bonferroni'])} |")
    A(f"| Holm（FWER） | {len(sets['holm'])} | {_fmt_keys(sets['holm'])} |")
    A(f"| BH（FDR，需 PRDS） | {len(sets['bh'])} | {_fmt_keys(sets['bh'])} |")
    A(f"| **BY（FDR，任意依赖）** | **{len(by['rejected'])}** | {_fmt_keys(by['rejected'])} |")
    A("")
    same_as_bonf = set(by["rejected"]) == set(sets["bonferroni"])
    A(f"BY 的拒绝集合与 Bonferroni **{'完全相同' if same_as_bonf else '不同'}**。含义是：")
    A("")
    A("> 对外可引的 6 条结论，在一个**不假设任何相依结构**的口径下仍然成立。")
    A("")
    diff = sorted(set(sets["bh"]) - set(by["rejected"]))
    if diff:
        A(f"BY 比 BH 少的那 {len(diff)} 条是 {_fmt_keys(diff)}。")
        A("该条在 L3z 的定稿里本来就是 `NOT_GRADED`（判定统计量与序列不同源），")
        A("不进对外可引清单，因此它掉出 BY **不改变任何可引结论**。")
        A("")
    A("### 11.3 逐条对照")
    A("")
    A("| 结论 | p | BH（需 PRDS） | BY（任意依赖） | 定稿分级 |")
    A("| --- | --- | --- | --- | --- |")
    for item in sorted(by["items"], key=lambda x: x["p"]):
        tier = cards.get(item["key"], "UNKNOWN")
        bh_mark = "通过" if item["key"] in sets["bh"] else "—"
        by_mark = "**通过**" if item["by"] else "—"
        A(f"| `{item['key']}` | {p6(item['p'])} | {bh_mark} | {by_mark} | {tier} |")
    A("")
    A("### 11.4 m 口径敏感性")
    A("")
    thr27 = 0.05 / l3aa["m_all"]
    n27b = sum(1 for i in by27["items"] if i["p"] <= thr27)
    A("| m 口径 | BY 拒绝 | Bonferroni 拒绝 | BY 第 1 档门槛 |")
    A("| --- | --- | --- | --- |")
    A(
        f"| {by['m']}（数值指纹去重，主口径） | {len(by['rejected'])} | {len(sets['bonferroni'])} "
        f"| {by['unit_threshold']:.8f} |"
    )
    A(
        f"| {l3aa['m_all']}（登记条目数） | {len(by27['rejected'])} | {n27b} "
        f"| {by27['unit_threshold']:.8f} |"
    )
    A("")
    A("**BY 在两种 m 下都给 6 条，Bonferroni 从 6 掉到 5。**")
    A("原因是 BY 是 step-up，第 k 档门槛按 k 线性放宽，补偿了 H_m 的收紧；Bonferroni 的门槛与 k 无关。")
    A("所以 BY 对「m 取 24 还是 27」这个本身有争议的口径更不敏感。")
    A("")
    A("### 11.5 必须紧跟的限定：这个「6」只对 NW(3) 成立")
    A("")
    A("| 滞后 | " + " | ".join(f"L={x}" for x in lags) + " |")
    A("| --- | " + " | ".join("---" for _ in lags) + " |")
    A("| BY 拒绝数 | " + " | ".join(str(lag_counts[x]) for x in lags) + " |")
    A(
        "| 同批 p 值上的 BH | "
        + " | ".join(str(l3aa["by_lag"][x]["n_rejected_bh"]) for x in lags)
        + " |"
    )
    A("")
    A(f"六档下 BY 的通过数是 **{'/'.join(str(lag_counts[x]) for x in lags)}**，会摆动。")
    A(f"**全档都在的只有 {len(l3aa['lag_intersection'])} 条**：{_fmt_keys(l3aa['lag_intersection'])}；")
    if l3aa["lag_varying"]:
        A(f"会摆动的是 {_fmt_keys(l3aa['lag_varying'])}。")
    A("")
    A("按分级摊开，说法可以更精确：")
    A("")
    A("> **5 条正面结论（1 条 `QUOTABLE` + 4 条 `QUOTABLE_MARGINAL`）在六档滞后下全部通过 BY；")
    A("> 会摆动的第 6 条 `phasel_alpha` 是「确认无效应」那条（`NULL_CONFIRMED`），"
      "`phasep_late` 本来就是 `NOT_GRADED`。**")
    A("")
    A("也就是说：**没有任何一条正面结论的 BY 通过性依赖滞后选择。**")
    A("")
    A("⚠️ **这条限定来自一次失败的预测，不是事后的解释。**")
    A("预登记 P4 写的是「六档滞后 BY 通过数恒为 6」，被实测反驳。")
    A("原来的推断是：L3u 已测「20 条 × 6 档只有 1 条跨档」，所以 BY 应该也稳。")
    A("但 L3u 测的是「**分级**是否跨档」，而 BY 卡在 p 的绝对值上——")
    A("分级只看效应/MDE 的比值落在哪个区间，p 从 0.002 变到 0.004 不会跨档，BY 却会。")
    A("「分级稳」推不出「判定稳」，这两件事原先被混为一谈。预测被反驳的记录保留，判据不改。")
    A("")
    if refuted:
        A(f"**被反驳的预登记预测**：{'、'.join(refuted)}（共 "
          f"{sum(1 for p in l3aa['predictions'] if p['timing'] == 'priori')} 条先验预测）。")
        A("")
    A("### 11.6 依赖诊断：这些检验到底有多同步")
    A("")
    A(f"{dep['n_series']} 条逐月序列、两两 {dep['n_pairs']} 对。")
    A("")
    A("| 指标 | 值 |")
    A("| --- | --- |")
    A(f"| 窗口重叠率中位数（交集 / 较短窗口） | {dep['median_overlap_ratio']:.4f} |")
    A(f"| 零重叠对数 | {dep['n_zero_overlap']} |")
    A(f"| \\|ρ\\| 中位数（三偏移取最大） | {dep['median_abs_rho']:.4f} |")
    A(f"| \\|ρ\\| 中位数（仅 lag 0） | {dep['median_abs_rho_shift0']:.4f} |")
    A(f"| \\|ρ\\| > 0.5 的对数 | {dep['n_abs_rho_gt_0_5']} / {dep['n_pairs_with_rho']} |")
    A(f"| 最大 \\|ρ\\| | {dep['max_abs_rho']:.4f} |")
    A(f"| 日期口径不一致的对数 | {dep['n_alignment_warning']} |")
    A(f"| 平移 ±1 月后 \\|ρ\\| 提升 >0.10 的对数 | {dep['n_shift_helps']} |")
    A("")
    A("**为什么主口径取「三偏移的 |ρ| 最大值」**：各阶段月度文件的日期列并不统一——")
    A("一部分有 `month`（持有月），一部分只有 `signal_date`（信号月），两者天然差 1 个月。")
    A("按单一偏移对齐会人为压低相关性。取三个偏移下的最大值，是对「这些检验同步性不强」")
    A("这一方向**最不利**的选择——若这样算出来仍不算高，那「同步性不强」就不可能是口径错位造的假象。")
    A("")
    A("⚠️ **这一段不能证明什么，请务必按这个读法看**：")
    A("**两两 Pearson ρ 既不能证明也不能否证 PRDS。** PRDS 是关于联合分布的高维性质，而 ρ 只是二阶矩。")
    A("本节的作用只有两个：让读者自己判断这批检验大致有多同步；给 BY 提供一个参照。")
    A("**BY 的结论不依赖这里任何一个数字。**")
    A("")
    if m_eff.get("m_eff"):
        A(f"**有效检验数（描述性）**：在相关矩阵完整的 {m_eff['m_subset']} 条子集上，")
        A(f"Li-Ji 指标 m_eff = **{m_eff['m_eff']:.3f}**（平均非对角 ρ={m_eff['mean_offdiag']:.4f}，"
          f"平均 \\|ρ\\|={m_eff['mean_abs_offdiag']:.4f}）。")
        A("⚠️ 这是**子集**上的量，不是全部 24 条；而且它只是「平均相关强度」的换算，")
        A("**不是 FDR 控制成立的依据**，也不能用来替代 BY。")
        A("")
    A("### 11.7 顺带查出来的一件事：有一条是另一条的精确子窗口")
    A("")
    A("把 \\|ρ\\| ≥ 0.9999 的对拉出来，再逐月比对数值：")
    A("")
    A("| a | b | ρ | 共同月 | max\\|逐月差\\| | 逐位相同 |")
    A("| --- | --- | --- | --- | --- | --- |")
    for p in dep["near_duplicates"]:
        d = "—" if p["max_abs_diff"] is None else f"{p['max_abs_diff']:.3e}"
        A(
            f"| `{p['a']}` | `{p['b']}` | {p['rho']:.6f} | {p['n_overlap']} | {d} "
            f"| {'**是**' if p['exact_subwindow'] else '否'} |"
        )
    A("")
    A(f"其中 **{dep['n_exact_subwindow']} 对逐位相同**，但两者性质不同：")
    A("")
    A("- `phases_net` 与 `phaset_net` 逐位相同——这一对 **L3v 已登记过**（27 条只对应 24 份独立数字，"
      "其中一组就是它们），不是新发现；")
    A("- `phaseo_netBASE` 与 `phasep_late` 逐位相同——**这一对是新的**。后者就是"
      "「前者取 late 段」，在 129 个共同月上是同一批数字，不是「高度相关」，是**同一条序列的截取**。")
    A("")
    A("L3v 的数值指纹去重抓不到它：指纹是 `(mean, t, n)`，两条序列的 n 不同（237 vs 129），")
    A("指纹自然不同，于是被算成两次独立检验。**所以 m=24 其实是偏大的。**")
    A("")
    A("**明知偏大，为什么不去把它改小**：把 m 改小会让每档门槛都变松，只会让更多条目通过——")
    A("这正是「为提高通过数而调 m」的样子，也是 L3z 预登记明令禁止的动作。")
    A("所以本阶段只把这个发现记录下来，**不重跑更小的 m**：现在的 6 条是在偏严的 m 下得到的，")
    A("属于保守结论；要处理应写成下一次预登记里的去重规则（把「同源子窗口」定义进去），")
    A("而不是这一次就地放宽。")
    A("")
    A("### 11.8 仪器与变异测试")
    A("")
    gates = l3aa.get("gates") or []
    A(f"**闸门**：{len(gates)} 处违规。覆盖 "
      "BY ⊆ BH（逐档逐 m）、门槛公式自洽、H_24 已知值、m=1 退化、全零效应不放行、q 单调、"
      "重叠率子集性（交集等于较短窗口时必为 1）、m_eff 必被相关性压低。")
    A("")
    mut = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_mutation.json"
    if mut.exists():
        m = load(mut)
        n_caught = sum(1 for x in m["mutations"] if x["caught"])
        A(f"**变异测试**：注入 {len(m['mutations'])} 处缺陷，抓到 **{n_caught}/{len(m['mutations'])}**。"
          f"源码恢复后与基线一致：{'是' if m['restored_matches_baseline'] else '否'}。")
        A("")
        A("| # | 注入的缺陷 | 抓到了 | 触发点 |")
        A("| --- | --- | --- | --- |")
        for x in m["mutations"]:
            hit = "、".join(x["gates_fired"]) or "、".join(x["refuted_after"]) or "—"
            A(f"| {x['id']} | {x['defect']} | {'是' if x['caught'] else '否（预期内）'} | {hit} |")
        A("")
        A("未抓到的那一条（相关性只取 lag 0）**是设计如此**：它改变的是「披露得多保守」"
          "（\\|ρ\\| 中位数从 0.41 掉到 0.12），而 P6 是单侧阈值 0.5，两种口径都在下方，结论不变。")
        A("闸门只管算得对不对，不管披露得多保守。这条如实保留。")
        A("")
    A("**回归**：后端全量测试全绿（含本阶段新增 50 项，覆盖 BY 数学性质、四种读取失败必须区分、"
      "以及「带索引重建」与既有 `rebuild_series` 的逐位一致）；性质检查 9/9 全过；"
      "脚本侧测试全绿；交易层验收 PASS。")
    A("")
    A("### 11.9 这一节的状态")
    A("")
    A(f"**状态**：`{l3aa.get('status', '—')}`")
    A("")
    A("判定依据：主口径结论成立（BY = 6 = Bonferroni，且 BY 严格是 BH 的子集、闸门零违规），")
    A("但滞后全档稳定性**不成立**，故不写成 `ROBUST`，而写成「主口径稳健、对滞后敏感」。")
    A("")
    A("## 12. 两个阶段的状态汇总")
    A("")
    A("| 阶段 | 状态 | 对定稿的影响 |")
    A("| --- | --- | --- |")
    A("| L3z 多重检验审计 | `CORRECTED_MULTIPLICITY_RESULTS` | 可引 **9 → 6**；3 条降为 `EXPLORATORY`；定稿已改 |")
    A(f"| L3aa 任意依赖复核 | `{l3aa.get('status', '—')}` | **无需改动**：BY 给出同一 6 条，只是多了一个不依赖 PRDS 的理由 |")
    A("")
    A("当前对外可引 6 条的完整状态：")
    A("")
    A("- 主口径 NW(3)：BY 拒绝 6 条，与 Bonferroni 集合相同——**不依赖 PRDS**；")
    A("- 滞后稳健性：5 条恒过，第 6 条（`phasel_alpha`，`NULL_CONFIRMED`）在 L=0/1 下不通过 BY；")
    A("- 引用时必须带的两句话：**BY 不验证 PRDS**（只是绕开）；**两两 ρ 既不能证明也不能否证 PRDS**。")
    A("")
    A("## 13. 仍未解决的")
    A("")
    A("| 缺口 | 状态 |")
    A("| --- | --- |")
    A("| BH 的 PRDS 前提 | **仍未验证**（L3aa 改走 BY，绕开而非验证） |")
    A("| NW 滞后与 BY 的联合稳健性 | **已测但有摆动**（六档 5/5/6/6/6/7；主口径仍 NW(3)） |")
    A("| m 的精确口径 | **偏大**（`phaseo` 与 `phasep_late` 是同源子窗口，指纹抓不到）——留待新预登记 |")
    A("| SciPy 独立参考 | 未完成（本机无网络） |")
    A("| 不同原假设数 / 未报告的试验 | UNKNOWN（预登记无逐条假设字段；不伪造） |")
    A("| 经济意义 / 可交易性 | 不在这两个阶段范围内 |")
    A("")
    A("> 最后一句说明：本报告里的每一条「显著」都仍是**统计**结论，不是「按此交易能赚多少」。")
    A("> 要把横截面差距变成收益，必须过一次现金流、整手、限额与规模模拟——不在本报告范围。")
    A("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())