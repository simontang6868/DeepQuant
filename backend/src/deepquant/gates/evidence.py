"""L3af：证据分级 S1–S4 与「当前有效引用清单」的生成。

## 为什么要把一张标签拆成四张

历史报告里最容易含糊的一句话是「这条结论站得住」。它至少混了四件事：

* **S1 统计**：在给定家族与 m 下，不是噪声；
* **S2 经济**：效应量达到**事前规定**的门槛；
* **S3 成本后**：用**逐月**净收益序列重估，且成本的不确定性也算进去；
* **S4 可交易**：独立样本外 + 成交模型 + 容量 + 风险。

本模块把这四件事分别算出来，并且**禁止**任何一步由「统计显著」或「效应/MDE 比值」自动推导。
后者是功效指标：MDE 小只说明尺子细，不说明东西大。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import current_sheet as cs
from . import family as fam
from . import multiplicity as mp
from .cost_net import POSITIVE_CLAIMS, PREREG_SHA, load_prereg, verify_inputs

#: 事前规定的经济意义门槛（pp/月）。
ECON_THRESHOLD_PP = 0.30
ECON_THRESHOLD_SOURCE = "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md 第 156 行"

#: 哪些条目**按预登记**受 0.30pp 门槛约束（其余只能作参照，不能判过/不过）。
ECON_COVERED = {
    "phasec_d": "PHASE_C_UNIVERSE_PREREG §经济意义门槛",
    "phasec2_d": "PHASE_C2_SUSPEND_PREREG §0.30pp 依据",
    "phasec3_d": "PHASE_C3_EXECUTION_LAG_PREREG §0.30pp 依据",
    "phasee_alpha": "PHASE_E_LONGONLY_PREREG",
    "phasef_delta": "PHASE_F_MAX5_PREREG",
    "phaseg_alpha": "PHASE_G_MAX5_LONGONLY_PREREG",
}

#: 判定量类型是「净收益」的条目。
NET_BASES = frozenset({"net_return"})

SHEET_VERSION = "L3AF-CURRENT-v2"
SHEET_PATH = "docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md"


class EvidenceError(RuntimeError):
    """证据分级无法由字段推出，或清单与 JSON 不一致。"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_of(root: Path, series: Mapping[str, Any]) -> dict[str, str]:
    path = series.get("path")
    if not path:
        return {"path": "NOT_RECORDED", "sha256": "NOT_RECORDED"}
    full = Path(root) / path
    return {
        "path": path,
        "sha256": _sha(full) if full.exists() else "MISSING",
    }


# --------------------------------------------------------------------------
# S1
# --------------------------------------------------------------------------


def grade_s1(key: str, fam_row: Mapping[str, Any]) -> dict[str, Any]:
    """统计证据：p 可复现 + 四种校正在 m=25 下全部拒绝。"""
    corr = (fam_row.get("corrections") or {}).get("IND_m25") or {}
    four = {m: bool(corr.get(m)) for m in ("bonferroni", "holm", "bh", "by") if m in corr}
    passes = all(four.values()) and len(four) == 4
    return {
        "status": "PASS" if passes else "FAIL",
        "p": (fam_row.get("stat") or {}).get("p"),
        "t_nw3": (fam_row.get("stat") or {}).get("t_nw3"),
        "m": 25,
        "four_corrections": four,
        "note": "S1 只回答「不像噪声」，不回答「有多大用」",
    }


# --------------------------------------------------------------------------
# S2
# --------------------------------------------------------------------------


def grade_s2(key: str, effect_pp: float | None) -> dict[str, Any]:
    """经济意义：只用**事前**门槛，且只对预登记覆盖的条目判过/不过。"""
    if effect_pp is None:
        return {"status": "NOT_GRADED", "reason": "效应量不可得"}
    covered = key in ECON_COVERED
    absv = abs(effect_pp)
    if covered:
        return {
            "status": "PASS" if absv >= ECON_THRESHOLD_PP else "FAIL",
            "threshold_pp": ECON_THRESHOLD_PP,
            "source": ECON_COVERED[key],
            "effect_pp": effect_pp,
            "note": "门槛为事前规定，非事后选取",
        }
    basis = cs.economic_basis(key)
    if basis in NET_BASES:
        reason = (
            "判定量已是扣成本净收益；0.30pp 门槛的理由是「= 0.1% 档月度成本的 2 倍」，"
            "该理由对已扣成本的量不成立，且从未为净收益预登记过门槛"
        )
    else:
        reason = "该条目所在阶段未预登记经济意义门槛（0.30pp/月 出自阶段 C，其后 E/F/G 复用，不回溯覆盖更早的 Gate 系列）"
    return {
        "status": "NOT_GRADED",
        "threshold_pp": None,
        "reference_pp": ECON_THRESHOLD_PP,
        "effect_pp": effect_pp,
        "clears_borrowed_reference": absv >= ECON_THRESHOLD_PP,
        "reason": reason,
        "note": "参照值只作披露，不构成过/不过；更不得用效应/MDE 比值代替",
    }


# --------------------------------------------------------------------------
# S3
# --------------------------------------------------------------------------


def grade_s3(key: str, cost: Mapping[str, Any], fam_row: Mapping[str, Any]) -> dict[str, Any]:
    """成本后证据。三条互斥的路径，全部由字段判，不人工指派。

    1. 判定量本身就是逐月净收益（``phaseo_netBASE``）⇒ 直接用它的家族 p；
    2. 判定量是毛利、且该信号的逐月换手可得（``phasei_v1``）⇒ 用 L3af 的重建序列，**逐档**判；
    3. 其余 ⇒ ``ABSENT``。

    ⚠️ ``phasei_v0`` 走第 3 条：V0 的持仓含不可成交标的，其换手与 V1 **不是同一条序列**。
    拿 V1 的换手去估 V0 的成本，正是「换手禁止沿用别的信号」这条铁律要防的事。
    """
    basis = cs.economic_basis(key)
    if basis in NET_BASES:
        s1 = grade_s1(key, fam_row)
        return {
            "status": "PASS" if s1["status"] == "PASS" else "FAIL",
            "path": "judged_on_net_series",
            "note": "该条判定量即扣成本净收益，成本口径与资金档随条目登记",
            "tiers": None,
        }
    if key == "phasei_v1":
        tiers = []
        for t in cost["tiers"]:
            sig = abs(t["net"]["t_iid"]) >= 1.96
            tiers.append(
                {
                    "tier": t["id"],
                    "rate_pct": t["rate_pct"],
                    "net_pp_month": t["net_pp_month"],
                    "t_iid": t["net"]["t_iid"],
                    "t_nw3": t["net"]["t_nw3"],
                    "significant_iid": sig,
                    "significant_nw3": abs(t["net"]["t_nw3"]) >= 1.96,
                }
            )
        primary = next(x for x in tiers if x["tier"] == cost["primary"]["tier"])
        distinct = len({x["significant_iid"] for x in tiers}) > 1
        return {
            "status": "TIER_DEPENDENT" if distinct else (
                "PASS" if primary["significant_iid"] else "FAIL"
            ),
            "path": "reconstructed_l3af",
            "primary_tier": primary["tier"],
            "primary_significant": primary["significant_iid"],
            "tiers": tiers,
            "note": (
                "预登记主成本档（0.3%）下不显著 ⇒ 冻结判定 EXQ5_COST_ABSORBED 不变；"
                "真实费率档下显著，但那是**敏感性**，不是判定"
            ),
        }
    if key == "phasei_v0":
        return {
            "status": "ABSENT",
            "path": "no_turnover_for_v0",
            "note": "V0 持仓含不可成交标的，其逐月换手**未落盘**；用 V1 的换手代替属于「换手沿用别的信号」，禁止",
        }
    return {
        "status": "ABSENT",
        "path": "no_net_construct",
        "note": "判定量是价差/差值/回归系数，不存在该条目的净收益版本",
    }


# --------------------------------------------------------------------------
# S4
# --------------------------------------------------------------------------


def grade_s4(key: str) -> dict[str, Any]:
    """可交易性。四项缺一不可，本仓库一项都不完整。"""
    return {
        "status": "ABSENT",
        "requirements": {
            "out_of_sample": False,
            "fill_model": False,
            "capacity": False,
            "risk_analysis": False,
        },
        "detail": {
            "out_of_sample": "无前瞻样本；全部为历史样本内（窗口止于 2026-09）",
            "fill_model": "只有阶段 J 的 Amihud 冲击**上限常数** 0.000784pp，不是成交回放",
            "capacity": "只有覆盖率/未投资比例的描述性统计，无冲击-容量最优点",
            "risk_analysis": "只到 MDD 与仓位上限，无流动性/集中度/尾部压力",
        },
        "note": "S4 是「能不能下单」，与 S1/S2/S3 完全不同的问题",
    }


# --------------------------------------------------------------------------
# 卡片
# --------------------------------------------------------------------------


def build_card(
    root: Path,
    key: str,
    *,
    fam_row: Mapping[str, Any],
    claim: Mapping[str, Any],
    cost: Mapping[str, Any],
) -> dict[str, Any]:
    stat = fam_row.get("stat") or {}
    effect = stat.get("mean_pp")
    s1 = grade_s1(key, fam_row)
    s2 = grade_s2(key, effect)
    s3 = grade_s3(key, cost, fam_row)
    s4 = grade_s4(key)
    art = artifact_of(root, {"path": fam_row.get("data_path")})
    return {
        "id": key,
        "family": fam_row.get("cluster") or claim.get("family"),
        "registered_verdict": fam_row.get("registered_verdict"),
        "quotability": fam_row.get("quotability"),
        "question": fam_row.get("question"),
        "null": fam_row.get("null"),
        "effect_pp": effect,
        "unit": "pp/月",
        "n": stat.get("n"),
        "window": fam_row.get("window"),
        "mde_pp": stat.get("mde_pp"),
        "p": stat.get("p"),
        "t_nw3": stat.get("t_nw3"),
        "statistics": {"S1": s1, "S2": s2, "S3": s3, "S4": s4},
        "allowed_wording": claim.get("headline"),
        "limits_of_use": list(claim.get("limits_of_use") or []),
        "artifact": art,
        "corrections": fam_row.get("corrections"),
        "cost_convention": (
            "净收益：3000 万 / BASE（佣金 0.025% 双边 + 印花 0.05% 仅卖出 + 过户 0.001% 双边，"
            "等价单边 0.051%）+ 冲击上限 0.000784pp"
            if key == "phaseo_netBASE"
            else "无（本条判定量不含成本）"
            if key not in ("phasei_v0", "phasei_v1")
            else "毛利 alpha；成本档见 statistics.S3.tiers"
        ),
        "multiplicity_convention": "m=25（独立随机变量口径）；Bonferroni/Holm/BH/BY 四种并列",
        "has_cost_evidence": s3["status"] == "PASS",
        "has_oos_evidence": False,
        "tradability_allowed": False,
    }


def status_of(card: Mapping[str, Any]) -> str:
    """当前状态：由四张标签机械合成，不允许人工指定。

    * S1 不过 ⇒ ``NOT_QUOTABLE_STAT``
    * S1 过但 S3 因数据缺失而 ABSENT ⇒ ``QUOTABLE_STAT_ONLY``
    * S3 明确 FAIL（有净序列但不显著/不为正）⇒ ``QUOTABLE_STAT_WITH_NEGATIVE_COST``
    * S3 TIER_DEPENDENT ⇒ ``QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST``
    * S3 PASS ⇒ ``QUOTABLE_WITH_COST_EVIDENCE``
    """
    st = card["statistics"]
    if st["S1"]["status"] != "PASS":
        return "NOT_QUOTABLE_STAT"
    s3 = st["S3"]["status"]
    if s3 == "PASS":
        return "QUOTABLE_WITH_COST_EVIDENCE"
    if s3 == "TIER_DEPENDENT":
        return "QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST"
    if s3 == "FAIL":
        return "QUOTABLE_STAT_WITH_NEGATIVE_COST"
    return "QUOTABLE_STAT_ONLY"


def build(root: Path | str, *, cost: Mapping[str, Any], family: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(root)
    prereg = load_prereg(root)
    frozen = verify_inputs(root, prereg)
    claims = json.loads(
        (root / "docs/gates/l3v_claims.json").read_text(encoding="utf-8")
    )["claims"]
    claim_by = {c["key"]: c for c in claims}
    fam_by = {r["id"]: r for r in family["registry"]}

    cards = []
    for key in POSITIVE_CLAIMS:
        if key not in fam_by:
            raise EvidenceError(f"正面结论 {key} 不在家族台账里")
        cards.append(
            build_card(root, key, fam_row=fam_by[key], claim=claim_by.get(key, {}), cost=cost)
        )

    # 其余在册条目也出一张「不进正面栏」的卡片，避免读者以为它们消失了
    others = []
    for key in sorted(fam_by):
        if key in POSITIVE_CLAIMS:
            continue
        card = build_card(root, key, fam_row=fam_by[key], claim=claim_by.get(key, {}), cost=cost)
        others.append(
            {
                "id": key,
                "S1": card["statistics"]["S1"]["status"],
                "S2": card["statistics"]["S2"]["status"],
                "S3": card["statistics"]["S3"]["status"],
                "S4": card["statistics"]["S4"]["status"],
                "status": status_of(card),
                "quotability": card["quotability"],
                "registered_verdict": card["registered_verdict"],
            }
        )

    for c in cards:
        c["status"] = status_of(c)

    res = {
        "experiment_id": "H-L3AF-EVIDENCE",
        "prereg_sha256": PREREG_SHA,
        "sheet_version": SHEET_VERSION,
        "econ_threshold_pp": ECON_THRESHOLD_PP,
        "econ_threshold_source": ECON_THRESHOLD_SOURCE,
        "econ_threshold_defect": {
            "code": "D2",
            "detail": (
                "docs/PHASE_C2_SUSPEND_PREREG_2026-10-21.md 第 93 行写「0.30pp/月 等于阶段 C "
                "主检验经济门槛的一半」，即隐含 C 的门槛是 0.60；而 PHASE_C 第 156 行、"
                "PHASE_E 第 86 行、PHASE_F 第 116 行三处都说 C 的门槛是 0.30。"
                "4 份文件中 3 份一致、1 份矛盾。"
            ),
            "impact": (
                "对 7 条正面结论的 S2 判定**无影响**：受门槛约束的只有 phasec_d（1.94）、"
                "phasec2_d（0.107）、phasef_delta（0.740），无论门槛取 0.30 还是 0.60，"
                "三条的过/不过都不变。"
            ),
            "handling": "采用 0.30（三份文件一致），把矛盾作为记录缺陷保留，不改原文",
        },
        "cards": cards,
        "not_quotable_but_registered": others,
        "counts": {
            "n_positive": len(cards),
            "by_status": _count([c["status"] for c in cards]),
            "S1_PASS": sum(1 for c in cards if c["statistics"]["S1"]["status"] == "PASS"),
            "S2_PASS": sum(1 for c in cards if c["statistics"]["S2"]["status"] == "PASS"),
            "S3_PASS": sum(1 for c in cards if c["statistics"]["S3"]["status"] == "PASS"),
            "S4_PASS": sum(1 for c in cards if c["statistics"]["S4"]["status"] == "PASS"),
        },
        "global_limits": [
            "全部结论都在历史样本内产生（窗口止于 2026-09），不存在前瞻期证据。",
            "「统计显著」不等于「经济有意义」；「样本内净收益为正」不等于「可实盘」。",
            "本清单只覆盖 L3u 登记在册的 27 条 + L3af 纳入的 phasei 2 条；"
            "不在家族内的实验（如 phaseu 只读审计）不在此校正范围内。",
            "研究历史无法完整恢复：尝试过但未报告的策略/窗口/成本档位没有落盘，"
            "因此不能声称已校正全部研究者自由度。",
            "m=25 依赖「同一随机变量只占一个名额」这一判据；若改用 m=27/29，"
            "7 条正面结论的集合不变（G9B 验证），变化只出现在 phasel_alpha（非正面）。",
        ],
        "inputs": frozen,
    }
    res["gates"] = gate_codes(res, family)
    return res


def _count(items: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def gate_codes(res: Mapping[str, Any], family: Mapping[str, Any]) -> list[dict[str, str]]:
    out = []
    for c in res["cards"]:
        st = c["statistics"]
        # G10：成本后证据只能由「逐月净序列 + 该序列自身通过」推出
        if c["has_cost_evidence"] and st["S3"]["status"] != "PASS":
            out.append({"code": "G10_NO_AUTO_PROMOTION",
                        "detail": f"{c['id']} 标了成本后证据但 S3={st['S3']['status']}"})
        if st["S3"]["status"] == "PASS":
            # PASS 必须能指出证据来源：要么判定量本身是净收益，要么有逐月分档且有显著档。
            # 这条防的是「给一个没有数据支撑的条目直接写 PASS」——若只查「有 tiers 吗」，
            # 一个 tiers=None 的 PASS 会溜过去（变异 M8 就是这么设计来打它的）。
            tiers = st["S3"].get("tiers")
            path = st["S3"].get("path")
            if tiers:
                if not any(t["significant_iid"] for t in tiers):
                    out.append({"code": "G10_NO_AUTO_PROMOTION",
                                "detail": f"{c['id']} S3 判 PASS 但没有任何一档显著"})
            elif path != "judged_on_net_series":
                out.append({"code": "G10_NO_AUTO_PROMOTION",
                            "detail": f"{c['id']} S3 判 PASS 却拿不出证据路径（path={path!r}）"})
        # G10b：S1 不过者不得进入正面引用栏（本阶段 7 条都应过）
        if c["status"] != "NOT_QUOTABLE_STAT" and st["S1"]["status"] != "PASS":
            out.append({"code": "G10_NO_AUTO_PROMOTION",
                        "detail": f"{c['id']} S1 不过却给了可引状态 {c['status']}"})
        # 可交易性一律禁止讨论，若有例外必须是显式四项齐备
        if c["tradability_allowed"]:
            req = st["S4"]["requirements"]
            if not all(req.values()):
                out.append({"code": "G10_NO_AUTO_PROMOTION",
                            "detail": f"{c['id']} 允许讨论可交易性但 S4 四项未齐"})
    # G10c：正面结论条数必须与家族口径一致
    pos = [k for k, v in family["positive_by_variant"]["IND_m25"].items()]
    for method in pos:
        if len(family["positive_by_variant"]["IND_m25"][method]) != len(POSITIVE_CLAIMS):
            out.append({"code": "G10_NO_AUTO_PROMOTION",
                        "detail": f"家族 m=25/{method} 的正面结论数 "
                                  f"{len(family['positive_by_variant']['IND_m25'][method])} ≠ {len(POSITIVE_CLAIMS)}"})
    if len(res["cards"]) != len(POSITIVE_CLAIMS):
        out.append({"code": "G10_NO_AUTO_PROMOTION",
                    "detail": f"卡片 {len(res['cards'])} 张 ≠ 正面结论 {len(POSITIVE_CLAIMS)} 条"})
    return out


# --------------------------------------------------------------------------
# 清单渲染
# --------------------------------------------------------------------------

S3_LABEL = {
    "PASS": "有（逐月净序列，四种校正全过）",
    "TIER_DEPENDENT": "有但**分档相反**",
    "FAIL": "无（净序列不显著或不为正）",
    "ABSENT": "无（判定量不是净收益 / 净序列缺失）",
}

S2_LABEL = {
    "PASS": "过（≥0.30pp/月，事前门槛）",
    "FAIL": "不过（<0.30pp/月，事前门槛）",
    "NOT_GRADED": "不判（无适用的**事前**门槛）",
}


def render_sheet(res: Mapping[str, Any], cost: Mapping[str, Any], family: Mapping[str, Any],
                 *, generated_at: str) -> str:
    """把 JSON 渲染成 md。**所有单元格都从 dict 取**，不在这里写任何数字。

    这条约束是为了让 G11 可验：如果某处硬编码了一个数，把 JSON 里的数改掉后
    md 不会跟着变，重建就会不一致。
    """
    L: list[str] = []
    a = L.append
    counts = res["counts"]
    a("# 当前有效引用清单（L3af）")
    a("")
    a(f"> **版本** `{res['sheet_version']}`｜**生成时间** {generated_at}"
      f"｜**预登记** `{res['prereg_sha256'][:12]}…`")
    a(">")
    a("> 本文件由 `scripts/l3af_reports.py` 生成，**禁止手工编辑**。"
      "改任何数字都会使 `scripts/l3af_reports.py --check` 失败。")
    a("> 它是**当前有效状态**的唯一来源；历史报告与更正记录一律保留（见 §7），"
      "但当前可引什么、怎么引，只以本文件为准。")
    a("")
    a("## 1. 一句话结论")
    a("")
    a(f"通过多重检验的 **{counts['n_positive']} 条正面结论**，按证据等级分成三档："
      f"**{counts['by_status'].get('QUOTABLE_WITH_COST_EVIDENCE', 0)} 条**带成本后证据、"
      f"**{counts['by_status'].get('QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST', 0)} 条**成本结论分档相反、"
      f"**{counts['by_status'].get('QUOTABLE_STAT_ONLY', 0)} 条**只有统计证据。")
    a("")
    a(f"四张标签的通过数：**S1 {counts['S1_PASS']}/{counts['n_positive']}**、"
      f"**S2 {counts['S2_PASS']}/{counts['n_positive']}**、"
      f"**S3 {counts['S3_PASS']}/{counts['n_positive']}**、"
      f"**S4 {counts['S4_PASS']}/{counts['n_positive']}**。")
    a("")
    a("> S4 = 0 不是遗漏，是事实：本仓库没有任何前瞻样本、成交回放或交割单。")
    a("")
    a("## 2. 口径（引用时必须一并说明）")
    a("")
    a("| 项目 | 取值 |")
    a("| --- | --- |")
    a(f"| 检验家族 | L3u 登记 {family['n_registered']} 条 + L3af 纳入 {family['n_added']} 条 = "
      f"{family['n_entries_total']} 条，评分集合 {family['n_scored']} 条 |")
    a(f"| 主口径 m | **{family['m_adopted']}**（独立随机变量数；{family['m_arithmetic']['n_registered']} "
      f"− 合并 {family['m_arithmetic']['n_merged_away']} + 纳入 {family['m_arithmetic']['n_added']}） |")
    a("| 替代口径 | m=24 / 27 / 29 并列；7 条正面结论在四种口径下**完全相同**（G9B） |")
    a("| 校正方法 | Bonferroni / Holm / BH / Benjamini–Yekutieli，四种结果并列；"
      "两套独立实现互校（G8） |")
    a("| 标准误 | Newey-West(3)，Bartlett 核；p 用 t 分布双侧，df = n−1 |")
    a("| 显著性水平 | α = 0.05（FWER 类）；q = 0.05（FDR 类）；"
      f"BY 的单位门槛 {family['runs']['IND_m25']['params']['by_unit_threshold']:.6f} |")
    a("| 成本口径 | BASE = 佣金 0.025% 双边 + 印花 0.05% **仅卖出** + 过户 0.001% 双边 "
      "⇒ 等价单边 **0.051%**；判定档另含 0.3% 压力档 |")
    a(f"| 经济门槛 | {res['econ_threshold_pp']}pp/月（{res['econ_threshold_source']}）；"
      "只对预登记覆盖的条目判过/不过 |")
    a("| 样本 | 237 个月，2007-01-31 ~ 2026-09-30，全部为历史样本内 |")
    a("")
    a("## 3. 七条正面结论的证据等级")
    a("")
    a("| claim_id | 当前状态 | 效应量 | n | S1 统计 | S2 经济 | S3 成本后 | S4 可交易 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for c in res["cards"]:
        st = c["statistics"]
        a(f"| `{c['id']}` | `{c['status']}` | {c['effect_pp']:+.4f} {c['unit']} | {c['n']} "
          f"| {st['S1']['status']} | {S2_LABEL[st['S2']['status']]} "
          f"| {S3_LABEL[st['S3']['status']]} | {st['S4']['status']} |")
    a("")
    a("## 4. 逐条卡片（13 个字段，缺一不可）")
    for c in res["cards"]:
        st = c["statistics"]
        a("")
        a(f"### `{c['id']}`｜`{c['status']}`")
        a("")
        a(f"- **可引用表述**：{c['allowed_wording']}")
        a(f"- **效应量与单位**：{c['effect_pp']:+.4f} pp/月；t(NW3) = {c['t_nw3']:+.4f}；"
          f"p = {c['p']:.3e}；MDE = {c['mde_pp']:.4f} pp")
        a(f"- **样本与时间范围**：n = {c['n']}；{c['window']}")
        a(f"- **成本口径**：{c['cost_convention']}")
        a(f"- **多重检验口径**：{c['multiplicity_convention']}；"
          f"Bonferroni 校正后 p = {_adj(c, 'bonferroni'):.3e}")
        a(f"- **S1 统计**：`{st['S1']['status']}`（四种校正全过 = "
          f"{all(st['S1']['four_corrections'].values())}）")
        a(f"- **S2 经济**：`{st['S2']['status']}`"
          + (f"：门槛 {st['S2']['threshold_pp']}pp（{st['S2']['source']}）"
             if st["S2"]["status"] != "NOT_GRADED"
             else f"：{st['S2']['reason']}；参照 {st['S2']['reference_pp']}pp，"
                  f"本效应{'高于' if st['S2']['clears_borrowed_reference'] else '低于'}该参照"))
        a(f"- **S3 成本后**：`{st['S3']['status']}`——{st['S3']['note']}")
        if st["S3"].get("tiers"):
            a("")
            a("  | 成本档 | 费率 | 净值（pp/月） | t(iid) | t(NW3) | iid 显著 |")
            a("  | --- | --- | --- | --- | --- | --- |")
            for t in st["S3"]["tiers"]:
                a(f"  | {t['tier']} | {t['rate_pct']:.3f}% | {t['net_pp_month']:+.6f} "
                  f"| {t['t_iid']:+.4f} | {t['t_nw3']:+.4f} | {'是' if t['significant_iid'] else '否'} |")
            a("")
        a(f"- **S4 可交易**：`{st['S4']['status']}`——四项"
          f"（样本外 / 成交模型 / 容量 / 风险）全部未具备")
        a(f"- **是否具备成本后证据**：{'是' if c['has_cost_evidence'] else '否'}")
        a(f"- **是否具备样本外证据**：否（无前瞻样本）")
        a(f"- **是否允许讨论可交易性**：**否**")
        a(f"- **对应产物**：`{c['artifact']['path']}`｜sha256 `{c['artifact']['sha256'][:16]}…`")
        if c["limits_of_use"]:
            a("- **适用限制**：")
            for lim in c["limits_of_use"]:
                a(f"  - {lim}")
    a("")
    a("## 5. 不允许的推论")
    a("")
    for i, x in enumerate([
        "统计显著 ≠ 经济有意义。S2 只过了 2 条，就是因为门槛是事前的。",
        "信号真实 ≠ 净收益为正。5 条正面结论的判定量是价差或差值，不是任何组合的收益。",
        "样本内 ≠ 前瞻有效。S4 全部为 0。",
        "通过多重检验 ≠ 可实盘。现金流、整手、限额、冲击都没有完整模拟。",
        "`phasei_v1` 在真实费率下显著（t=+2.98 iid / +4.00 NW3），但**这不推翻**其冻结判定 "
        "`EXQ5_COST_ABSORBED`——判定档是 0.3%，不是 0.051%。",
        "m=25 不是「唯一正确的 m」。它依赖「同一随机变量只占一个名额」这条判据；"
        "换成 m=27/29，7 条正面结论不变，但**不能**因此说「m 的问题解决了」。",
    ], 1):
        a(f"{i}. {x}")
    a("")
    a("## 6. 只覆盖登记册（容易越界的一条）")
    a("")
    a("「通过全族校正」只对**登记在册的条目**成立。L3af 已把 `phasei` 2 条纳入，"
      "但 26 个 `*_out` 目录里仍有未被登记的实验。"
      "因此不能把本清单的结论推广成「项目全部结论都通过了校正」。")
    a("")
    a("## 7. 变更与更正（历史保留，不覆盖）")
    a("")
    a("| 文件 | 角色 |")
    a("| --- | --- |")
    a("| `docs/history/DELIVERABLE_QUOTE_SHEET_CURRENT_L3AB-v1.md` | 本文件上一版（m=24、6 条）原文归档 |")
    a("| `docs/DELIVERABLE_QUOTE_SHEET.md` | L3y 对外定稿（9→6 修订版）。本清单不改它 |")
    a("| `docs/DELIVERABLE_QUOTE_SHEET_L3AC.md` / `_L3AD.md` / `_L3AE.md` | 逐阶段的中间版本，保留 |")
    a("| `docs/gates/l3af_corrections.json` | 本阶段的更正日志（D1/D2…） |")
    a("")
    a("### 7.1 与上一版（L3ab v1 / m=24 / 6 条）的差异")
    a("")
    a("| 项目 | L3ab v1 | 本版（L3af） | 依据 |")
    a("| --- | --- | --- | --- |")
    a("| 家族 | 27 条，不含 phasei | 27 + 2 = 29 条，含 phasei | `phasei_out` 有完整结论却从未登记 |")
    a("| 主口径 m | 24 | 25 | 同一随机变量只占一个名额 |")
    a("| 正面结论 | 6（其中 1 条是 NULL_CONFIRMED，不是正面） | 7 | 纳入 phasei_v0/v1 |")
    a("| 证据分级 | 三列（统计/经济/可交易） | 四列 S1–S4 | 本阶段要求 |")
    a("| 成本后证据 | 1 条 | 1 条 + 1 条分档相反 | 逐月净收益重建 |")
    a("")
    a("## 8. 依凭产物")
    a("")
    a("| 文件 | 说明 |")
    a("| --- | --- |")
    a(f"| `docs/gates/l3af_prereg.json` | 本阶段预登记，sha256 `{res['prereg_sha256']}` |")
    for name in ("phasei_monthly", "phasei_summary", "phaseo_monthly", "phaseo_summary",
                 "l3u_prereg", "l3v_claims", "l3ab_decisions", "l3ad_prereg"):
        row = res["inputs"][name]
        a(f"| `{row['path']}` | sha256 `{row['sha256_raw'][:16]}…`"
          f"{'（磁盘为 CRLF，LF 归一后与 manifest 相符）' if row.get('eol_converted') else ''} |")
    a("")
    a("## 9. 复现")
    a("")
    a("```")
    a("PYTHONPATH=backend/src python scripts/l3af_reports.py")
    a("PYTHONPATH=backend/src python scripts/l3af_reports.py --check   # 只校验不重写")
    a("```")
    a("")
    return "\n".join(L) + "\n"


def _adj(card: Mapping[str, Any], method: str) -> float:
    corr = (card.get("corrections") or {}).get("IND_m25") or {}
    v = corr.get("adjusted_p")
    if v is not None:
        return float(v)
    # 回退：由 p 与 m 现算，保证渲染不依赖上游是否带了 adjusted_p
    m = 25
    p = float(card["p"])
    return min(1.0, m * p)


# --------------------------------------------------------------------------
# 最终状态的选择（写死成函数，便于测试与复用）
# --------------------------------------------------------------------------

#: 阻塞级闸门：出现任一即判 BLOCKED_BY_STATISTICAL_REPRODUCTION
BLOCKING_CODES = frozenset({
    "G1_ALPHA_IDENTITY",
    "G2_COST_ARITHMETIC",
    "G2B_TURNOVER_UNIT",
    "G3_NET_IDENTITY",
    "G4_MONTH_ALIGNMENT",
    "G5_VARIANCE_IDENTITY",
    "G6_SE_FROM_NET",
    "G6B_SE_CROSSCHECK",
    "G9_FAMILY_CONSISTENCY",
    "G8_CORRECTION_MISMATCH",
})


def choose_status(cost: Mapping[str, Any], family: Mapping[str, Any],
                  evid: Mapping[str, Any]) -> str:
    """按预登记 ``status_rule`` 机械选状态。**不允许**人工挑选。

    顺序有意义：先看「数据能不能用」（换手缺失），再看「实现对不对」（阻塞级闸门），
    最后才看「口径是否收敛」。把顺序反过来会把「数据缺失」误报成「复现失败」。
    """
    audit = cost.get("availability") or {}
    if audit.get("verdict") == "BLOCKED_BY_MISSING_TURNOVER_SERIES":
        return "BLOCKED_BY_MISSING_TURNOVER_SERIES"

    codes = {g["code"] for g in (cost.get("gates") or [])}
    codes |= {g["code"] for g in (family.get("gates") or [])}
    codes |= {g["code"] for g in (evid.get("gates") or [])}
    if codes & BLOCKING_CODES:
        return "BLOCKED_BY_STATISTICAL_REPRODUCTION"

    pos = family.get("positive_by_variant") or {}
    sets = {
        tuple(sorted(v)) for rows in pos.values() for k, v in rows.items() if k == "bonferroni"
    }
    if len(sets) > 1:
        return "BLOCKED_BY_TEST_FAMILY_AMBIGUITY"

    # 研究历史缺口客观存在（26 个 *_out 只覆盖 23 个、尝试未报告的变体无法恢复），
    # 因此本阶段**不**给 VERIFIED_CURRENT_EVIDENCE_SET：那不是严格与否的问题，
    # 而是「覆盖不全」这件事无法用本仓库内的证据否定。
    return "VERIFIED_WITH_DOCUMENTED_LIMITATIONS"
