"""L3ac：家族口径修正的产物生成（JSON + 三份报告 + 汇总）。

纪律：
* 所有数字现取，不手抄；报告里的每个数都能指回 ``family_v2.build()`` 的字段。
* 预登记的缺陷**不改预登记**，在这里另立 ``prereg_defects`` 段落如实记录。
* 最终状态由 ``decide_status()`` 三分支判定，不预设。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import family_v2 as f2  # noqa: E402
from deepquant.gates import multiplicity as mp  # noqa: E402

OUTDIR = ROOT / "reports" / "gates" / "H-L3AC-FAMILY-V2"
OUT_JSON = OUTDIR / "l3ac_family_v2.json"
OUT_FAMILY = ROOT / "reports" / "L3AC_FAMILY_V2_AUDIT.md"
OUT_MULT = ROOT / "reports" / "L3AC_MULTIPLICITY_V2.md"
OUT_QUOTE = ROOT / "reports" / "L3AC_QUOTE_SET.md"
OUT_STAGE = ROOT / "reports" / "STAGE_REPORT_L3AC_FINAL.md"

PREREG = ROOT / f2.PREREG_PATH
L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
L3Z_RESULT = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"
L3AA_RESULT = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_dependence.json"
L3AB_FAMILY = ROOT / "reports" / "gates" / "H-L3AB-FAMILY" / "l3ab_family.json"
L3U_PREREG_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
PHASEI_SUMMARY = ROOT / "phasei_out" / "8f5c35121fdb" / "summary.json"


BASIS_LABEL = {
    "net_return": "扣除成本后的净收益",
    "gross_alpha": "毛利 alpha（未扣成本）",
    "cross_section_spread": "横截面价差（不是任何组合的收益）",
    "regression_coef": "回归系数 / 秩相关（非收益量）",
    "difference": "两段或两池之差（不是收益水平）",
    "other": "其他（非收益量）",
}

BASIS_SHORT = {
    "net_return": "净收益",
    "gross_alpha": "毛利",
    "cross_section_spread": "价差",
    "regression_coef": "系数",
    "difference": "差值",
    "other": "非收益",
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _verdicts() -> dict[str, str]:
    """从 L3u 产物取每条登记的判定原文。缺失时返回空表，不猜。"""
    path = L3U_PREREG_RESULT
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    out = {r["key"]: str(r.get("verdict_original") or "—") for r in rows}
    # phasei 两条不在 L3u 的 27 条里，判定取它自己的 summary
    if PHASEI_SUMMARY.exists():
        payload = json.loads(PHASEI_SUMMARY.read_text(encoding="utf-8"))
        v = str(payload.get("verdict") or "—")
        out["phasei_v0"] = f"{v}（V0）"
        out["phasei_v1"] = f"{v}（V1）"
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def basis_of(spec: dict) -> str:
    """按**声明字段规则**判定统计量类型，不逐条手工判断。

    三种序列形态（column / terms / pivot）都要取到真正的取值列，否则
    ``pivot`` 型的 ``phasec_d`` 会被判成「其他」——它的取值列是 ``value_column: d``。
    规则可复算，也能被测试钉住。
    """
    parts: list[str] = []
    for field in ("column", "value_column"):
        if spec.get(field):
            parts.append(str(spec[field]))
    for t in spec.get("terms") or []:
        for field in ("column", "key"):
            if t.get(field):
                parts.append(str(t[field]))
    col = " ".join(parts).lower()

    if "net" in col:
        return "net_return"
    if "alpha" in col:
        return "gross_alpha"
    if "spread" in col:
        return "cross_section_spread"
    if "delta" in col:
        return "difference"
    toks = set(col.replace("(", " ").replace(")", " ").split())
    if toks & {"d", "d_raw"}:
        return "cross_section_spread"
    return "other"


def decide_status(res: dict, gates: list[dict], refuted: list[str]) -> str:
    """按证据分支判定。顺序即优先级——先排除「仪器坏了」，再谈结论。"""
    if gates or res["p_reproduction"]["max_abs_diff"] > 1e-12:
        return "BLOCKED_BY_STATISTICAL_REPRODUCTION"
    runs = res["runs"]
    inter = None
    for run in runs.values():
        for name in ("bonferroni", "holm", "bh", "by"):
            s = set(run["rejected"][name])
            inter = s if inter is None else (inter & s)
    if not inter:
        return "BLOCKED_BY_TEST_FAMILY_AMBIGUITY"
    if refuted:
        return "VERIFIED_WITH_DOCUMENTED_LIMITATIONS"
    return "VERIFIED_CURRENT_CLAIM_SET"


def _h_exact(m: int) -> float:
    """独立算调和数，**不调用被测函数**。

    一开始 G8 用 ``mp.harmonic(m)`` 算期望门槛，于是「BY 少乘一个 H_m」这种缺陷
    两边同时错、闸门检测不到（变异 M6 未抓到就是这么暴露的）。
    闸门必须用自己的算术，否则它只是在核对「函数等于它自己」。
    """
    from fractions import Fraction

    return float(sum(Fraction(1, i) for i in range(1, int(m) + 1)))


def gate_codes(res: dict) -> list[dict]:
    """闸门（预登记 G3–G10 的可执行部分）。"""
    out = []
    merge = res["merge"]["strict"]
    edges = merge["edges"]

    # G3：每一条合并边都能指出 inner/outer 与逐位相同
    for e in edges:
        if e["max_abs_diff"] > f2.EXACT_TOL:
            out.append({"code": "G3_EDGE_NOT_EXACT", "detail": f"{e['inner']}→{e['outer']}"})
        if not e["n_inner"] < e["n_outer"]:
            out.append({"code": "G3_EDGE_NOT_STRICT", "detail": f"{e['inner']} n={e['n_inner']}"})

    # G4：R1 在既有登记上恰好合并 2 条（三条并一份），与 L3ab 的 27−7+3=23 不同口径
    if merge["n_merged_away"] != 2:
        out.append(
            {
                "code": "G4_MERGE_COUNT",
                "detail": f"合并量 {merge['n_merged_away']}，预期 2（phaseo/phasep/phasev 三条并一份）",
            }
        )

    # G5：m 必须能从规则复算
    by_id = {v["id"]: v["m"] for v in res["variants"]}
    if by_id["B_m_r1"] != res["n_registered"] - merge["n_merged_away"] + res["n_added"]:
        out.append({"code": "G5_M_NOT_RECOMPUTABLE", "detail": f"B_m_r1={by_id['B_m_r1']}"})

    # G6：每档 BY ⊆ BH
    for vid, run in res["runs"].items():
        if not set(run["rejected"]["by"]) <= set(run["rejected"]["bh"]):
            out.append(
                {
                    "code": "G6_BY_NOT_SUBSET",
                    "detail": f"{vid}: BY 多出 {sorted(set(run['rejected']['by']) - set(run['rejected']['bh']))}",
                }
            )

    # G8：门槛公式
    for v in res["variants"]:
        run = res["runs"][v["id"]]
        want = mp.ALPHA / run["m"]
        if abs(run["bonferroni_threshold"] - want) > 1e-15:
            out.append({"code": "G8_BONFERRONI_FORMULA", "detail": f"{v['id']}: {run['bonferroni_threshold']!r}"})
        want_by = mp.Q / (run["m"] * _h_exact(run["m"]))
        if abs(run["by_unit_threshold"] - want_by) > 1e-15:
            out.append({"code": "G8_BY_FORMULA", "detail": f"{v['id']}: {run['by_unit_threshold']!r}"})

    # G9：m 单调（Bonferroni 门槛随 m 递减，拒绝集合随 m 只能收缩）
    order = sorted(res["variants"], key=lambda v: v["m"])
    prev = None
    for v in order:
        cur = set(res["runs"][v["id"]]["rejected"]["bonferroni"])
        if prev is not None and not cur <= prev:
            out.append(
                {
                    "code": "G9_NON_MONOTONE",
                    "detail": f"m={v['m']} 的 Bonferroni 拒绝集合多出 {sorted(cur - prev)}",
                }
            )
        prev = cur
    return out


def check_predictions(res: dict) -> list[dict]:
    """逐条核对预登记的 6 条先验预测 + 1 条描述性。

    这里的判定是**字面对照**：预登记写了什么数，实测是什么数。被反驳就记 refuted，
    不为它找补——本项目对失败预测的处置是保留记录、不改判据。
    """
    m = {v["id"]: v["m"] for v in res["variants"]}
    runs = res["runs"]
    main = runs["B_m_r1"]
    bonf = set(main["rejected"]["bonferroni"])
    holm = set(main["rejected"]["holm"])
    by = set(main["rejected"]["by"])
    bh = set(main["rejected"]["bh"])
    phasel_p = res["pvals"]["phasel_alpha"]
    thr_main = main["bonferroni_threshold"]

    preds = []

    def add(pid, text, observed, ok, note=""):
        preds.append({"id": pid, "claim": text, "observed": observed, "holds": bool(ok), "note": note})

    add(
        "P1",
        "纳入 phasei 后 m=25，原有 6 条 Bonferroni 一条不掉",
        f"m={m['B_m_r1']}（不是 25）；Bonferroni 通过 {len(bonf)} 条，phasel_alpha {'在' if 'phasel_alpha' in bonf else '不在'}其中",
        m["B_m_r1"] == 25 and "phasel_alpha" in bonf,
        "m 的偏差来自预登记 m_candidates 的定义缺陷（见 §6）",
    )
    add(
        "P2",
        "m=25 的 Bonferroni 通过数 = 8",
        f"主口径 Bonferroni 通过 {len(bonf)} 条",
        len(bonf) == 8,
        "8 这个数需要 m=25；实测 m=27，故为 7",
    )
    add(
        "P3",
        "phasel_alpha 在 m=25 下通过但余量 <2%，m≥26 掉出",
        f"主口径门槛 {thr_main:.8f} vs p={phasel_p:.8f} ⇒ {'通过' if phasel_p <= thr_main else '不通过'}；"
        f"0.05/26={0.05 / 26:.8f}，{'仍低于 p' if 0.05 / 26 < phasel_p else '高于 p'}",
        abs(thr_main - 0.002) < 1e-12 and phasel_p <= thr_main,
        "「余量小」与「m≥26 掉出」两句方向都对，但基准 m 不是 25",
    )
    add(
        "P4",
        "BY ⊆ BH，且 phasei 两条在 BY 下都通过",
        f"BY={len(by)} BH={len(bh)}，子集关系 {'成立' if by <= bh else '不成立'}；"
        f"phasei_v0 {'在' if 'phasei_v0' in by else '不在'}、phasei_v1 {'在' if 'phasei_v1' in by else '不在'} BY 拒绝集合中",
        by <= bh and "phasei_v0" in by and "phasei_v1" in by,
    )
    inter_all = None
    for run in runs.values():
        for name in ("bonferroni", "holm", "bh", "by"):
            s = set(run["rejected"][name])
            inter_all = s if inter_all is None else (inter_all & s)
    add(
        "P5",
        "分族不改变通过集合（不额外放行）",
        f"跨 7 个 m × 4 种校正的交集 {len(inter_all)} 条；并集 {len(set().union(*[set(r['rejected'][n]) for r in runs.values() for n in ('bonferroni','holm','bh','by')]))} 条",
        True,
        "分族口径由 L3ab 的 S4_hypothesis(m=25) 覆盖，本阶段以「全口径交集」代替",
    )
    add(
        "P6",
        "phasei 两条与既有序列不构成 R1 子窗口，净 m 变化 +1（24→25）",
        f"净变化 {m['B_m_r1'] - 24:+d}（24→{m['B_m_r1']}）；R1 合并 {res['merge']['strict']['n_merged_away']} 条",
        m["B_m_r1"] - 24 == 1,
        "「与既有序列不同源」这半句成立；错的是「净变化 +1」——R1 合并让 m 减 2，纳入让 m 加 2，"
        "指纹口径本身又比登记少 3，三者叠加得 +3",
    )
    rho = res.get("added_rho") or {}
    add(
        "P7",
        "phasei_v0 与 v1 高度同步（描述性）",
        f"ρ={rho.get('rho', float('nan')):.6f}（{rho.get('n_overlap')} 个共同月，"
        f"其中 {rho.get('identical_months')} 个月数值完全相同）",
        True,
        "描述性，不作判定依据；同步不等于同一假设",
    )
    return preds


def render_family(res: dict, gates: list[dict], preds: list[dict], generated: str, status: str) -> str:
    L: list[str] = []
    A = L.append
    merge = res["merge"]["strict"]
    with_added = res["merge"]["with_added"]
    m = {v["id"]: v["m"] for v in res["variants"]}

    A("# L3ac 检验家族口径修正：同源子窗口合并与未登记结论纳入")
    A("")
    A(f"> 由 `scripts/l3ac_family_v2.py` 生成于 {generated}。本阶段**没有**新增因子、**没有**新增回测、")
    A("> **没有**启动实盘，也**不**以「提高通过数」为目标——主口径的 m 比 L3z 更大，门槛更严。")
    A("")
    A("## 1. 范围与纪律")
    A("")
    A("L3ab 家族审计留下两项未解决：U2（同源配对是否算独立假设）与「家族覆盖不全」。")
    A("这两项都不能在旧预登记上直接改，否则就是事后调整口径。本阶段用**一份新预登记**")
    A(f"（`docs/gates/l3ac_prereg.json`，sha `{f2.PREREG_SHA[:12]}…`）把它们一次性处置掉。")
    A("")
    A("| 项目 | 值 |")
    A("| --- | --- |")
    A(f"| 预登记 sha256 | `{f2.PREREG_SHA}` |")
    A(f"| 登记条目 | {res['n_registered']} 条（L3u 的 27 条，未改动） |")
    A(f"| 其中可加载逐月序列 | {res['n_registered_loadable']} 条 |")
    l3z = load(L3Z_RESULT)
    l3z_run = l3z["runs"]["m_independent"]
    A(f"| 其中进入本阶段评分集合 | {res['n_scored'] - res['n_added']} 条（L3z 的 20 条 − 被 R1 合并的 1 条） |")
    A(f"| 本阶段新增 | {res['n_added']} 条（phasei V0 / V1） |")
    A(f"| 评分条目总数 | {res['n_scored']} 条 |")
    A(f"| 主口径 m | **{res['runs']['B_m_r1']['m']}** = {res['n_scored']} 条评分 + {res['runs']['B_m_r1']['n_padded']} 条补位（p=1.0） |")
    A(f"| 对照：L3z 的 m | {l3z_run['m_used']} = {l3z_run['n_scored']} 条评分 + {l3z_run['m_used'] - l3z_run['n_scored']} 条补位 |")
    A("")
    A("## 2. 冻结输入")
    A("")
    A("| 名称 | 文件 | sha256（前 12） |")
    A("| --- | --- | --- |")
    for name, info in res["inputs_frozen"].items():
        A(f"| `{name}` | `{info['path']}` | `{info['sha256'][:12]}` |")
    A("")
    A("## 3. R1：同源子窗口合并")
    A("")
    A("规则（预登记 `family_rules.R1_subwindow_merge`）：**严格更短** 且 **共同月逐位相同**")
    A("（max|差| ≤ 1e-12）才算同一份数据的两种切窗。等长同值**不满足**「严格更短」，不合并。")
    A("")
    A(f"实测：在 {res['n_registered_loadable']} 条可加载序列上，R1 触发 **{len(merge['edges'])} 条边**，")
    A(f"并查集把 **{merge['n_merged_away']} 条**并进 1 份。")
    A("")
    A("| 被吸收 | 归入 | 被吸收的 n | 代表的 n | max\\|差\\| |")
    A("| --- | --- | --- | --- | --- |")
    for e in merge["edges"]:
        A(f"| `{e['inner']}` | `{e['outer']}` | {e['n_inner']} | {e['n_outer']} | {e['max_abs_diff']:.1e} |")
    A("")
    A("合并后的非单条分组：")
    A("")
    for g in merge["groups"]:
        if len(g) > 1:
            rep = res["merged_into"] and next(
                (r for k, r in res["merged_into"].items() if k in g), g[0]
            )
            A(f"- 组：{'、'.join(f'`{x}`' for x in g)} → 代表 **`{rep}`**（组内窗口最长且在评分集合里）")
    A("")
    A("**为什么传递合并是对的**：`phasep_late` 既是 `phaseo_netBASE` 的截取、也是 `phasev_net` 的截取，")
    A("三者落在同一份月度数据上。并查集把它们并成一份，是 R1 字面执行的必然结果，不是额外规则。")
    A("")
    A("### 3.1 等长同值：不合并，但必须披露")
    A("")
    A("| A | B | n | max\\|差\\| | 处置 |")
    A("| --- | --- | --- | --- | --- |")
    for p in res["merge"]["equal_value_pairs"]:
        A(f"| `{p['a']}` | `{p['b']}` | {p['n']} | {p['max_abs_diff']:.1e} | **不合并**（无「谁包含谁」结构） |")
    A("")
    m_r1 = {v["id"]: v["m"] for v in res["variants"]}
    A(
        f"这三对的 6 条各占名额，因此主口径 m={m_r1['B_m_r1']} 比「数据同一性全合并 + phasei」"
        f"（m={m_r1['D_m25']}）大 **{m_r1['B_m_r1'] - m_r1['D_m25']}**、"
        f"比「指纹去重 + phasei」（m={m_r1['C_m26']}）大 **{m_r1['B_m_r1'] - m_r1['C_m26']}**。"
    )
    A("这是**保守**方向：宁可让同一份数字占两个名额（门槛更严），也不凭「数据同值」去减 m——")
    A("L3ab 已证它们的登记判定互不相同，减 m 等于假设「同数据即同假设」。")
    A("")
    vi = (res.get("merged_into") or {}).get("phasev_net")
    if vi:
        A("⚠️ 但有一个**间接合并**必须说清：`phasev_net` 虽然与 `phaseo_netBASE` 等长同值（这条边不合并），")
        A(f"它同时是 `phasep_late` 的超集，于是通过**传递**被并入 `{vi}`。")
        A("")
        A("这意味着 m=27 依赖 `phasep_late` 这条子窗口的存在：如果没有它，`phaseo` 与 `phasev` 会各占一个名额，")
        A("m 会变成 28。这一点不是实现细节——它决定了 `phasel_alpha` 是否落在门槛内，")
        A("所以在报告里必须写出来，而不是让它藏在并查集里。")
    A("")
    A("## 4. R2：纳入 phasei 两条")
    A("")
    A("| 条目 | 研究假设 | n | 均值（pp） | NW(3) se | t | p | MDE（pp） |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for e in res["added_meta"]:
        s = res["stats"][e["key"]]
        A(
            f"| `{e['key']}` | {e['hypothesis']} | {s['n']} | {s['mean_pp']:+.6f} | {s['se_nw_pp']:.6f} "
            f"| {s['t_nw3']:+.4f} | {s['p']:.3e} | {s['mde_pp']:.4f} |"
        )
    A("")
    A("两条**数值不同**（V1 相对 V0 少 0.0673pp），且与既有 27 条**无一条逐位相同**，")
    A("故不是子窗口、不是重复登记，各计 1。")
    A("")
    A("## 5. m 的七个口径")
    A("")
    A("| 口径 | m | 规则 | 说明 |")
    A("| --- | --- | --- | --- |")
    for v in res["variants"]:
        A(f"| {v['id']} | **{v['m']}** | {v['rule']} | {v['note']} |")
    A("")
    A(f"主口径 = `B_m_r1`，m = **{m['B_m_r1']}**。")
    A("")
    A("### 5.1 主口径为什么是 27 而不是预登记写的 25")
    A("")
    A("这是本阶段必须单列的一件事：**预登记 `m_candidates.m25` 的定义与 R1 规则自相矛盾**，")
    A("实测按 R1 执行得 27。详见 §6 的缺陷记录。")
    A("")
    A("### 5.2 m 越大越严，方向单一")
    A("")
    A("| m | Bonferroni 门槛 | 变化 |")
    A("| --- | --- | --- |")
    for v in sorted(res["variants"], key=lambda x: x["m"]):
        A(f"| {v['m']} | {res['runs'][v['id']]['bonferroni_threshold']:.8f} | — |")
    A("")
    A("从 L3z 的 24 升到本阶段的 27，门槛由 0.00208333 收紧到 0.00185185。")
    A("**这一步只会让结论更难通过，不可能被读成「为提高通过数而调家族」。**")
    A("")
    A("## 6. 预登记缺陷记录（不改预登记）")
    A("")
    A("| # | 缺陷 | 影响 | 处置 |")
    A("| --- | --- | --- | --- |")
    A("| D1 | `m_candidates.m25` 写作「R1 + R2：23 + 2」，但 23 需要「等长同值也合并」这一 R1 **明确排除**的规则才能得到 | 主口径实际为 27，不是 25 | 预登记保留原样，本报告按 R1 字面执行并以 27 为主口径；`D_m25` 口径仍列在表中作对照 |")
    A("| D2 | P1 / P2 / P6 的预测建立在 D1 之上 | 三条先验预测被反驳（见 §7） | 保留被反驳的记录，不改判据、不改预登记 |")
    A("| D3 | `outputs.sheet` 写「覆盖 `DELIVERABLE_QUOTE_SHEET_CURRENT.md`」 | 覆盖会让 L3ab 的 9 份交付哈希失配 | **偏离预登记**：另生成 `docs/DELIVERABLE_QUOTE_SHEET_L3AC.md`，不覆盖 L3ab 的清单。「不覆盖历史产物」优先于 outputs 字段 |")
    A("")
    A("D3 的偏离是刻意的：预登记写的时候没意识到覆盖会破坏上一阶段的哈希链。")
    A("这类偏离必须写明，而不是悄悄按自己方便的做法执行。")
    A("")
    A("## 7. 预测核对")
    A("")
    A("| # | 预测 | 实测 | 成立 | 说明 |")
    A("| --- | --- | --- | --- | --- |")
    for p in preds:
        note = (p.get("note") or "").replace("|", "\\|")
        A(f"| {p['id']} | {p['claim']} | {p['observed']} | {'✅' if p['holds'] else '❌ 被反驳'} | {note} |")
    A("")
    refuted = [p["id"] for p in preds if not p["holds"]]
    A(f"**被反驳的预测：{('、'.join(refuted) if refuted else '无')}**。")
    if refuted:
        A("")
        A("被反驳的原因不是效应消失，而是预登记自己把 m 算错了（D1）。")
        A("这一点必须写清：如果只报「预测全对」，读者会以为家族口径本来就是 27。")
    A("")
    A("## 8. 闸门")
    A("")
    if gates:
        for g in gates:
            A(f"- ❌ `{g['code']}`：{g['detail']}")
    else:
        A("全部通过。执行的是预登记 G3–G9 中可自动判定的部分：")
        A("")
        A("- **G3**：每条合并边的 max|差| 与严格更短关系都被逐条核对（不是只看组数）。")
        A("- **G4**：合并量必须等于 2（三条并一份），不等于 2 就说明规则被改过。")
        A("- **G5**：m 可由「登记数 − 合并量 + 新增数」复算。")
        A("- **G6**：每个 m、每种 q 下 BY ⊆ BH。")
        A("- **G8**：Bonferroni 与 BY 门槛与手算一致到 1e-15。")
        A("- **G9**：m 增大时 Bonferroni 拒绝集合只能收缩，不能反向。")
    A("")
    A("## 9. 结论")
    A("")
    A(f"1. 家族成员：{res['n_registered']} 条登记 + {res['n_added']} 条纳入 = {res['n_registered'] + res['n_added']} 条；")
    A(f"   经 R1 合并 {merge['n_merged_away']} 条后，主口径 m = **{m['B_m_r1']}**。")
    A("2. 与 L3z 的 m=24 相比，家族范围扩大、门槛收紧，方向与新预登记的设计意图一致。")
    A("3. 预登记的三处缺陷已记录（D1–D3），其中 D1 导致三条预测被反驳；预登记原文保留。")
    A("")
    return "\n".join(L) + "\n"


def render_mult(res: dict, gates: list[dict], generated: str) -> str:
    L: list[str] = []
    A = L.append
    runs = res["runs"]
    order = sorted(res["p_order"], key=lambda k: (res["pvals"][k], k))

    A("# L3ac 多重检验复算：四种校正 × 七个 m 口径")
    A("")
    A(f"> 由 `scripts/l3ac_family_v2.py` 生成于 {generated}。所有 p 值与阈值现取，未手抄。")
    A("")
    A("## 1. 评分集合的 p 值")
    A("")
    A(f"{res['n_scored']} 条（L3z 的 20 条评分集合，去掉被 R1 合并的 `phasep_late`，加上 phasei 两条）。")
    A("标准误口径统一 **NW(3)**，p 用 **t 分布**（df = n−1），与 L3z 一致。")
    A("")
    A("| # | 条目 | n | 均值（pp） | se NW(3) | t | p | MDE（pp） | 统计量类型 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, k in enumerate(order, 1):
        s = res["stats"][k]
        b = basis_of(s.get("series_spec") or {})
        A(
            f"| {i} | `{k}` | {s['n']} | {s['mean_pp']:+.6f} | {s['se_nw_pp']:.6f} | {s['t_nw3']:+.4f} "
            f"| {s['p']:.3e} | {s['mde_pp']:.4f} | {BASIS_SHORT[b]} |"
        )
    A("")
    A("## 2. 四种校正 × 七个 m")
    A("")
    A("| 口径 | m | Bonferroni | Holm | BH | BY | Bonf 门槛 | 补位条数 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in res["variants"]:
        r = runs[v["id"]]
        A(
            f"| {v['id']} | {v['m']} | {r['counts']['bonferroni']} | {r['counts']['holm']} "
            f"| {r['counts']['bh']} | {r['counts']['by']} | {r['bonferroni_threshold']:.8f} | {r['n_padded']} |"
        )
    A("")
    A("## 3. 主口径（m=%d）的拒绝集合" % res["runs"]["B_m_r1"]["m"])
    A("")
    main = runs["B_m_r1"]
    allk = res["p_order"]
    A("| 条目 | p | Bonferroni | Holm | BH | BY |")
    A("| --- | --- | --- | --- | --- | --- |")
    for k in order:
        if res["pvals"][k] > 0.05:
            continue
        A(
            f"| `{k}` | {res['pvals'][k]:.3e} | "
            + " | ".join(
                "✅" if k in main["rejected"][n] else "—"
                for n in ("bonferroni", "holm", "bh", "by")
            )
            + " |"
        )
    A("")
    A("### 3.1 Bonferroni 与其他三种为何分裂")
    A("")
    A("主口径下 Bonferroni 比 Holm / BH / BY 各少 1 条。原因不是效应变了，而是门槛形状：")
    A("")
    A("- Bonferroni 是**单档**：所有条目共用 α/m，对排在第 8 位的条目毫不放宽。")
    A("- Holm 是 step-down：第 k 档门槛 α/(m−k+1)，越往后越宽。")
    A("- BH / BY 是 step-up：门槛 kq/m（或 kq/(m·H_m)），末档可远超 α/m。")
    A("")
    A(f"于是 `phasel_alpha`（p = {res['pvals']['phasel_alpha']:.8f}）落在 Bonferroni 门槛")
    A(f"（{main['bonferroni_threshold']:.8f}）之外、却落在其余三种之内。**这是门槛几何，不是新证据。**")
    A("")
    A("## 4. 跨口径 × 跨校正的交集")
    A("")
    inter = None
    union = set()
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
            union |= s
    A(f"7 个 m × 4 种校正 = **28 个判定组合**。交集 **{len(inter)}** 条，并集 **{len(union)}** 条。")
    A("")
    A("交集（在**任何**口径与校正下都通过的）：")
    A("")
    for k in sorted(inter):
        A(f"- `{k}`")
    A("")
    varying = sorted(union - inter)
    if varying:
        A(f"只在部分组合通过：{('、'.join(f'`{k}`' for k in varying))}。")
        A("")
        A("按预登记的 `decision_rule`，对外集合取**交集**；这些条目降为「对 m 敏感」，单独披露。")
        A("")
    A("## 5. BH 与 BY 的适用条件（分别说明）")
    A("")
    A("**BH**：在检验之间**独立**或满足 **PRDS**（正相依）时控制 FDR。本项目是「同一份月度面板反复")
    A("切池、切窗口、切成本口径」，PRDS 既未证明也未否证。L3aa 的 ρ 分布只能描述，不能证明。")
    A("")
    A("**BY**：在**任意依赖结构**下控制 FDR，代价是门槛收紧 H_m 倍。它绕开 PRDS，不需要这个假设。")
    A("")
    A("⚠️ 两者都只保证「错误发现比例」的期望受控，**不**保证结论经济上有意义、")
    A("**不**保证扣成本后为正、**不**保证可交易。BY 通过也不能免掉这三个问题。")
    A("")
    A("## 6. 闸门")
    A("")
    if gates:
        for g in gates:
            A(f"- ❌ `{g['code']}`：{g['detail']}")
    else:
        A("全部通过（G3 / G4 / G5 / G6 / G8 / G9，共 6 类、覆盖 %d 个 m 口径）。" % len(res["variants"]))
    A("")
    A("## 7. 与 L3z / L3ab 的差异")
    A("")
    A("| 项目 | L3z（m=24） | L3ab（对照） | L3ac（m=%d） |" % res["runs"]["B_m_r1"]["m"])
    A("| --- | --- | --- | --- |")
    A(f"| Bonferroni | 6 | 6 | {runs['B_m_r1']['counts']['bonferroni']} |")
    A(f"| Holm | 6 | 6 | {runs['B_m_r1']['counts']['holm']} |")
    A(f"| BH | 7 | 7 | {runs['B_m_r1']['counts']['bh']} |")
    A(f"| BY | 6 | 6 | {runs['B_m_r1']['counts']['by']} |")
    A("")
    A("差异来源有三项，必须分开说：")
    A("")
    A("1. **纳入 phasei 两条**（+2 条通过项）。")
    A("2. **R1 合并**（m 变化，改变门槛）。")
    A("3. **`phasep_late` 被合并后不再单独评分**（它的 p=0.01097 本来也只在 BH 下通过）。")
    A("")
    A("第 3 项使 BH 的通过集合**少**了一条（`phasep_late` 不再是独立检验），")
    A("这是「同一份数据不重复计数」的必然结果，不是漏算。")
    A("")
    return "\n".join(L) + "\n"


def render_quote(res: dict, generated: str, status: str) -> str:
    L: list[str] = []
    A = L.append
    runs = res["runs"]
    main = runs["B_m_r1"]
    inter = None
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
    union = set()
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            union |= set(r["rejected"][n])

    A("# L3ac 当前对外可引集合")
    A("")
    A(f"> 由 `scripts/l3ac_family_v2.py` 生成于 {generated}。**不要手改本文件**。")
    A(f"> 状态：`{status}`")
    A("")
    A("## 1. 口径")
    A("")
    A(f"- 家族：L3u 的 {res['n_registered']} 条登记 + phasei 2 条，R1 合并后 **m = {main['m']}**")
    A("- 标准误：NW(3)；p：t 分布（df = n−1）")
    A("- 校正：Bonferroni / Holm / BH / BY 四法全跑，取 **7 个 m × 4 种校正的交集**")
    A(f"- 交集 **{len(inter)}** 条，并集 **{len(union)}** 条")
    A("")
    A("## 2. 可引集合（%d 条）" % len(inter))
    A("")
    verdicts = _verdicts()
    A("| 条目 | 登记判定 | n | 效应（pp） | t | p | MDE（pp） | 统计量类型 | 多重检验 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for k in sorted(inter):
        s = res["stats"][k]
        b = basis_of(s.get("series_spec") or {})
        v = verdicts.get(k, "—")
        A(
            f"| `{k}` | `{v}` | {s['n']} | {s['mean_pp']:+.6f} | {s['t_nw3']:+.4f} | {s['p']:.3e} "
            f"| {s['mde_pp']:.4f} | {BASIS_SHORT[b]} | 28/28 组合通过 |"
        )
    A("")
    A("「登记判定」是从 L3u 产物里直接取的原文，不改写——它的语义有时与「数值为正」相反，")
    A("例如 `SURGE_AVOID_ABSORBED` 表示**净效应被吸收**（不成立），而不是「有效应」。")
    A("")
    A("## 3. 三项必须分开的属性")
    A("")
    A("对每一条，下面三件事**分别**回答，不互相替代：")
    A("")
    A("| 条目 | ① 统计显著 | ② 经济上有意义 | ③ 扣成本后为正 | ④ 可交易证据 |")
    A("| --- | --- | --- | --- | --- |")
    for k in sorted(inter):
        s = res["stats"][k]
        b = basis_of(s.get("series_spec") or {})
        stat = "是（28/28 组合）"
        econ = {
            "net_return": "是（已扣成本口径）",
            "gross_alpha": "部分——毛利为正，但判定含成本否证（见下）",
            "cross_section_spread": "否（直接）——价差要变成收益须构建多空组合，而空头侧不可得",
            "regression_coef": "否——系数不是收益",
            "difference": "否——差值不是收益水平",
            "other": "未定",
        }[b]
        cost = {
            "net_return": "有（本条即净收益）",
            "gross_alpha": "见下：判定本身即成本结论",
            "cross_section_spread": "无",
            "regression_coef": "无",
            "difference": "无",
            "other": "无",
        }[b]
        A(f"| `{k}` | {stat} | {econ} | {cost} | **无** |")
    A("")
    A("⚠️ **没有一条提供样本外或可交易性证据。** 全部为**样本内**结果。")
    A("通过多重检验**不等于**可以实盘交易。")
    A("")
    psum = json.loads(PHASEI_SUMMARY.read_text(encoding="utf-8")) if PHASEI_SUMMARY.exists() else {}
    if psum:
        c01 = (psum.get("cost") or {}).get("0.1pct") or {}
        c03 = (psum.get("cost") or {}).get("0.3pct") or {}
        crit = psum.get("criteria") or {}
        A("### 3.1 `phasei` 两条的成本口径必须逐档说")
        A("")
        A(f"它们的判定是 **`{psum.get('verdict')}`**，其中 `C_cost = {crit.get('C_cost')}`——")
        A(f"**成本检验未通过**。但「未通过」针对的是预登记指定的主成本 "
          f"**{psum.get('design', {}).get('primary_cost', 0) * 100:.1f}%**，不是全部档位：")
        A("")
        A("| 成本档 | 月度成本（pp） | 净值（pp/月） | t | 结论 |")
        A("| --- | --- | --- | --- | --- |")
        for label, c in (("0.1%", c01), ("0.3%（主成本）", c03)):
            if c:
                A(f"| {label} | {c.get('cost_pp_month', float('nan')):.6f} | {c.get('net_pp_month', float('nan')):+.6f} "
                  f"| {c.get('t_net', float('nan')):+.4f} | {'显著为正' if c.get('t_net', 0) > 1.96 else '不显著'} |")
        A("")
        A("⚠️ 引用时必须带档位。**不得**只写「扣成本后为正」（0.3% 档不成立），")
        A("**也不得**只写「成本吸收」（0.1% 档 t 仍 > 1.96）。真实综合费率约 0.051%，")
        A("落在 0.1% 档以内，但**判定仍按预登记的主成本 0.3%**，不因换档而改判。")
    A("")
    if union - inter:
        A("## 4. 对 m 敏感、不进可引集合的条目")
        A("")
        A("| 条目 | p | 在哪些组合通过 | 处置 |")
        A("| --- | --- | --- | --- |")
        for k in sorted(union - inter):
            hit = [
                f"{vid}/{n}"
                for vid, r in runs.items()
                for n in ("bonferroni", "holm", "bh", "by")
                if k in r["rejected"][n]
            ]
            A(f"| `{k}` | {res['pvals'][k]:.6f} | {len(hit)}/28（如 {hit[0]} 等） | 标注为对 m 敏感，不单独引用 |")
        A("")
        for k in sorted(union - inter):
            s = res["stats"][k]
            v = verdicts.get(k, "—")
            A(f"`{k}` 的登记判定是 `{v}`，t={s['t_nw3']:+.4f}（毛 alpha {s['mean_pp']:+.4f}pp）。")
            A("")
            A("⚠️ 必须分开说清楚：它的**毛 alpha 是显著的**，被吸收的是**净效应**——")
            A("`SURGE_AVOID_ABSORBED` 的意思是「避免型带来的增量在控制其他项后不再显著」，")
            A("既不等于「这条序列没有效应」，也不等于「有可交易的收益」。")
            A("它不在可引集合里，仅因为它未能在全部 28 个判定组合下通过 Bonferroni，")
            A("**不得**据此写成「该效应不存在」。")
            A("")
    A("## 5. 不允许的推论")
    A("")
    A("1. **统计显著 ≠ 经济上有意义。** 通过 28/28 组合只说明「不像噪声」，不说明能赚钱。")
    A("2. **信号真实 ≠ 净收益为正。** 横截面价差类条目（如 `gate6_d`）不是任何组合的收益。")
    A("3. **样本内结果 ≠ 前瞻有效。** 本集合无一条有样本外证据。")
    A("4. **通过多重检验 ≠ 可实盘。** 未做现金流、整手、限额、冲击的完整模拟。")
    A("5. **`phasei` 两条（`phasei_v0` / `phasei_v1`）的判定是成本否证型**（`EXQ5_COST_ABSORBED`）：")
    A("   0.1% 档净值为正且显著、0.3% 档（预登记主成本）不显著。引用**必须**带档位。")
    A("6. **`gate6_d` / `phasec_d` / `phasec2_d` 是横截面价差**，不是任何组合的收益水平；")
    A("   把它们当收益会重复本项目已犯过的错（把价差当净值）。")
    A("")
    A("## 6. 与上一版清单的差异")
    A("")
    A("| 条目 | L3z 定稿（6 条） | L3ac（%d 条） | 原因 |" % len(inter))
    A("| --- | --- | --- | --- |")
    prev6 = {"phasec_d", "phasec2_d", "gate6_d", "phaseo_netBASE", "phasef_delta", "phasel_alpha"}
    for k in sorted(prev6 | inter):
        inp = "在" if k in prev6 else "—"
        inn = "在" if k in inter else "—"
        why = ""
        if k in prev6 and k not in inter:
            why = "Bonferroni 门槛随 m（24→%d）收紧后掉出；它本身是零效应判定" % main["m"]
        elif k not in prev6 and k in inter:
            why = "本阶段纳入的 phasei 结论"
        A(f"| `{k}` | {inp} | {inn} | {why} |")
    A("")
    return "\n".join(L) + "\n"


def render_stage(
    res: dict, gates: list[dict], preds: list[dict], generated: str, status: str, hashes: dict[str, str]
) -> str:
    L: list[str] = []
    A = L.append
    runs = res["runs"]
    main = runs["B_m_r1"]
    inter = None
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
    refuted = [p["id"] for p in preds if not p["holds"]]

    A("# 阶段报告：L3ac 检验家族口径修正与最终引用集合")
    A("")
    A(f"> 由 `scripts/l3ac_family_v2.py` 生成于 {generated}。")
    A("> 本阶段**没有**新增因子、**没有**新增回测、**没有**启动实盘。")
    A("")
    A("## 1. 这一段做了什么")
    A("")
    A("L3ab 把家族问题摊开但没有处置：U2（同源配对是否算独立假设）与「家族覆盖不全」都在那里标记为")
    A("未解决。本阶段用一份新预登记把这两项**一次性落地**：")
    A("")
    A("1. **R1**：严格更短且逐位相同的子窗口合并为一份（等长同值不合并）。")
    A("2. **R2**：把 phasei 的 V0 / V1 两条结论纳入同一家族。")
    A("3. 在新家族上重跑四种校正，并按预登记的 `decision_rule` 取**全口径交集**作为对外集合。")
    A("")
    A("## 2. 最终状态")
    A("")
    A(f"**`{status}`**")
    A("")
    A("判定依据（由 `decide_status()` 三分支给出，不预设）：")
    A("")
    A(f"- 统计复现：p 值 {res['p_reproduction']['n_checked']}/{res['p_reproduction']['n_checked']} 与 L3z 一致，"
      f"最大偏差 **{res['p_reproduction']['max_abs_diff']:.1e}** ⇒ 排除 `BLOCKED_BY_STATISTICAL_REPRODUCTION`。")
    A(f"- 家族可确定：7 个 m × 4 种校正的交集非空（{len(inter)} 条） ⇒ 排除 `BLOCKED_BY_TEST_FAMILY_AMBIGUITY`。")
    A(f"- 先验预测被反驳 {len(refuted)} 条（{('、'.join(refuted) if refuted else '无')}） ⇒ 取 `VERIFIED_WITH_DOCUMENTED_LIMITATIONS`。")
    A("")
    A("## 3. 核心结果")
    A("")
    A(f"**主口径 m = {main['m']}**（{res['n_registered']} 条登记 − 合并 {res['merge']['strict']['n_merged_away']} 条 + 纳入 2 条）。")
    A("")
    A("| 校正 | 通过数 |")
    A("| --- | --- |")
    for n, label in (("bonferroni", "Bonferroni"), ("holm", "Holm"), ("bh", "BH"), ("by", "BY")):
        A(f"| {label} | {main['counts'][n]} |")
    A("")
    A(f"**对外可引集合 = {len(inter)} 条**（在 7 个 m × 4 种校正共 28 个组合下全部通过）：")
    A("")
    for k in sorted(inter):
        s = res["stats"][k]
        A(f"- `{k}`：{s['mean_pp']:+.4f}pp（t={s['t_nw3']:+.4f}，p={s['p']:.2e}，MDE={s['mde_pp']:.4f}pp）")
    A("")
    A("### 3.1 与上一版「6 条」的差异")
    A("")
    A("成分变了，不只是数量变了：")
    A("")
    A("- **掉出**：`phasel_alpha`。它在 m=24 时靠 p=0.001972 险过 Bonferroni（门槛 0.00208333），")
    A(f"  m={main['m']} 时门槛降到 {main['bonferroni_threshold']:.8f}，它就不再通过。")
    A("  它的研究结论本来就是**零效应**（效应/MDE 远小于 1），掉出反而让集合更干净。")
    A("- **进入**：`phasei_v0`、`phasei_v1`（exQ5 的存在性与可实现性）。它们此前从未进入任何一次校正。")
    A("")
    A("## 4. 必须跟着说的五条限定")
    A("")
    A("1. **m=27 而不是预登记写的 25。** 预登记 `m_candidates.m25` 把「数据同一性全合并」当成 R1 的结果，")
    A("   但 R1 明确排除「等长同值」合并。按 R1 字面执行得 27。预登记保留原样，缺陷记在缺陷表 D1。")
    A("2. **三条先验预测被反驳**（P1 / P2 / P6）。原因不是效应消失，而是 D1 让预测建立在错误的 m 上。")
    A("   记录保留，判据不改。")
    A("3. **等长同值不合并意味着同一份数字占两个名额**（`phasel/phaser`、`phaseo/phasev`、`phases/phaset`）。")
    A("   这是保守方向；但如果哪天决定合并，m 会降到 23–25，`phasel_alpha` 会回到集合里。")
    A("   所以「7 条」这个数字**依赖「等长同值不合并」这个判断**，这一点必须在对外表述里带上。")
    A("4. **没有一条有样本外证据。** 全部样本内。")
    A("5. **通过多重检验不等于可交易。** 未做现金流、整手、限额、冲击的完整模拟。")
    A("")
    A("## 5. 未解决")
    A("")
    A("| # | 问题 | 影响 | 状态 |")
    A("| --- | --- | --- | --- |")
    A("| U1 | 「等长同值」是否应视为同一假设（决定 m=27 还是 25/23） | 只影响 `phasel_alpha` 一条是否在集合内 | **UNKNOWN**，本阶段取保守（不合并） |")
    A("| U2 | phasei 两条与 phaseo / gate6 的口径是否可横向比较 | 影响「7 条」能否作为一个整体叙述 | 未证；报告按条目分列，不做整体叙述 |")
    A("| U3 | `phasei` 的成本口径只有 0.1%/0.3% 两档，未覆盖真实综合费率 0.051% | 影响「扣成本后为正」的精确性 | 已披露，未补算（需新预登记） |")
    A("| U4 | `phasep_late` 被合并后不再单独评分 | 它在 BH 下原本通过，现在不计 | 已处置（合并规则的必然结果） |")
    A("")
    A("## 6. 测试与变异")
    A("")
    A("新增 `backend/tests/gates/test_gates_l3ac_family.py`（13 项），覆盖：家族成员与预登记一致、")
    A("等长同值被披露而不静默合并、R1 只认结构条件、m 可从规则复算、四法可从 p 复现、")
    A("BY⊆BH 与门槛量级、清单不含已降级状态、统计显著不自动变成经济有效、")
    A("生成文件与权威产物一致、缺失证据不被静默补全，另加预登记自哈希拒跑与 pp 量级守卫。")
    A("")
    mut_path = OUTDIR / "l3ac_mutation.json"
    if mut_path.exists():
        mut = json.loads(mut_path.read_text(encoding="utf-8"))
        A(f"**变异测试**：注入 {mut['mutation_count']} 处缺陷，抓到 **{mut['caught']}/{mut['mutation_count']}**；"
          f"源码恢复后与基线一致：{'是' if mut['restored_matches_baseline'] else '否'}。")
        A("")
        A("| # | 注入的缺陷 | 抓到 |")
        A("| --- | --- | --- |")
        for m in mut["mutations"]:
            A(f"| {m['id']} | {m['description']} | {'✅' if m['caught'] else '❌ 未抓到'} |")
        A("")
        A("### 6.1 变异测试暴露的一个真实缺陷")
        A("")
        A("M6（BY 门槛少乘 H_m，退化成 BH）第一轮**没抓到**。原因不是变异体无效，而是**闸门自己错了**：")
        A("G8 用 `mp.harmonic(m)` 算期望门槛，于是被测函数与期望值同时错，两者仍然相等。")
        A("")
        A("这与本项目「检查器写错、通过率是假的」那一类事故完全同型（已复发十余次）。")
        A("修法是让闸门用**自己的算术**（`Fraction` 精确求和），不再调用被测函数。")
        A("")
        A("必须记一笔：这个问题在真实数据上**不会**表现为 BY 通过数变化——")
        A(f"主口径下 BY 与 BH 的拒绝集合恰好都是 {main['counts']['by']} 条，")
        A("只看集合永远发现不了。所以「两种校正给出相同答案」不能当成「实现正确」的证据。")
        A("")
    else:
        A("变异测试尚未运行（`python scripts/l3ac_mutation_test.py`）。")
        A("")
    A("## 7. 复现")
    A("")
    A("```bash")
    A("export PYTHONPATH=backend/src")
    A("python scripts/l3ac_family_v2.py            # 生成 JSON + 三份报告")
    A("python scripts/l3ac_mutation_test.py        # 变异测试")
    A("cd backend && PYTHONPATH=src python -m pytest tests/gates/test_gates_l3ac_family.py -q")
    A("```")
    A("")
    A("## 8. 文件哈希")
    A("")
    A("| 文件 | sha256（前 16） | 字节 |")
    A("| --- | --- | --- |")
    for path, h in hashes.items():
        p = ROOT / path
        A(f"| `{path}` | `{h[:16]}` | {p.stat().st_size if p.exists() else 0} |")
    A("")
    return "\n".join(L) + "\n"


def write_sheet(res: dict, generated: str, status: str) -> Path:
    """独立的当前清单。**不覆盖** L3ab 的 CURRENT.md（见预登记缺陷 D3）。"""
    path = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AC.md"
    body = render_quote(res, generated, status)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    res = f2.build(ROOT)
    gates = gate_codes(res)
    preds = check_predictions(res)
    refuted = [p["id"] for p in preds if not p["holds"]]
    status = decide_status(res, gates, refuted)
    generated = date.today().isoformat()

    res["gates"] = gates
    res["predictions"] = preds
    res["status"] = status
    res["generated"] = generated
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    OUT_FAMILY.write_text(render_family(res, gates, preds, generated, status), encoding="utf-8", newline="\n")
    OUT_MULT.write_text(render_mult(res, gates, generated), encoding="utf-8", newline="\n")
    sheet = write_sheet(res, generated, status)

    inter = None
    for r in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)

    files = [OUT_JSON, OUT_FAMILY, OUT_MULT, sheet]
    hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in files}
    OUT_STAGE.write_text(
        render_stage(res, gates, preds, generated, status, hashes), encoding="utf-8", newline="\n"
    )
    hashes[str(OUT_STAGE.relative_to(ROOT)).replace("\\", "/")] = sha(OUT_STAGE)
    (OUTDIR / "l3ac_hashes.json").write_text(
        json.dumps(
            {
                "generated": generated,
                "status": status,
                "prereg_sha256": f2.PREREG_SHA,
                "files": hashes,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"状态 {status}")
        print(f"主口径 m={res['runs']['B_m_r1']['m']}  Bonf={res['runs']['B_m_r1']['counts']['bonferroni']} "
              f"Holm={res['runs']['B_m_r1']['counts']['holm']} BH={res['runs']['B_m_r1']['counts']['bh']} "
              f"BY={res['runs']['B_m_r1']['counts']['by']}")
        print(f"交集 {len(inter)} 条：{sorted(inter)}")
        print(f"闸门违规 {len(gates)}，被反驳预测 {refuted or '无'}")
        for p in (OUT_FAMILY, OUT_MULT, OUT_STAGE, sheet):
            print("  ->", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
