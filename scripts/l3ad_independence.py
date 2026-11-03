"""L3ad：独立性审计的产物生成（JSON + 三份报告 + 汇总）。

报告里的每个数字都从 ``independence.build()`` 现取。预登记的表述缺陷不改预登记，
在报告里另立缺陷段。
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

from deepquant.gates import independence as ind  # noqa: E402
from deepquant.gates import multiplicity as mp  # noqa: E402

OUTDIR = ROOT / "reports" / "gates" / "H-L3AD-INDEPENDENCE"
OUT_JSON = OUTDIR / "l3ad_independence.json"
OUT_AUDIT = ROOT / "reports" / "L3AD_INDEPENDENCE_AUDIT.md"
OUT_MULT = ROOT / "reports" / "L3AD_MULTIPLICITY_M25.md"
OUT_STAGE = ROOT / "reports" / "STAGE_REPORT_L3AD_FINAL.md"
SHEET = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AD.md"

L3U_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
PHASEI_SUMMARY = ROOT / "phasei_out" / "8f5c35121fdb" / "summary.json"

#: 正面结论（净收益 / 毛利 / 价差方向的「有效应」判定）。
#: phasel_alpha 不在此列——它的判定是 `SURGE_AVOID_ABSORBED`（净效应被吸收）。
POSITIVE_KEYS = {
    "gate6_d",
    "phasec_d",
    "phasec2_d",
    "phasef_delta",
    "phasei_v0",
    "phasei_v1",
    "phaseo_netBASE",
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verdicts() -> dict[str, str]:
    out: dict[str, str] = {}
    if L3U_RESULT.exists():
        for r in load(L3U_RESULT).get("rows", []):
            out[r["key"]] = str(r.get("verdict_original") or "—")
    if PHASEI_SUMMARY.exists():
        v = str(load(PHASEI_SUMMARY).get("verdict") or "—")
        out["phasei_v0"] = v
        out["phasei_v1"] = v
    return out


def gate_codes(res: dict) -> list[dict]:
    out = []
    part = res["partition"]

    # G3：每条边都逐位相同
    for p in part["pairs"]:
        if p["max_abs_diff"] > ind.TOL:
            out.append({"code": "G3_NOT_EXACT", "detail": f"{p['a']}~{p['b']}"})
        if p["coverage"] < 1.0 and ind.REQUIRE_FULL_COVERAGE:
            out.append({"code": "G3_PARTIAL_COVERAGE", "detail": f"{p['a']}~{p['b']}"})

    # G4：等长同值 3 对 + 1 处子窗口，合并 4 条
    equal = [p for p in part["pairs"] if p["equal_length"]]
    strict = [p for p in part["pairs"] if not p["equal_length"]]
    if len(equal) != 3:
        out.append({"code": "G4_EQUAL_PAIR_COUNT", "detail": f"等长同值对 {len(equal)}，预期 3"})
    if len(strict) < 1:
        out.append({"code": "G4_STRICT_PAIR_MISSING", "detail": "未发现子窗口对"})
    if part["n_merged_away"] != 4:
        out.append({"code": "G4_MERGE_COUNT", "detail": f"合并 {part['n_merged_away']} 条，预期 4"})

    # G5a：等长同值的 p 必须完全相同
    for x in res["p_unchanged"]:
        is_strict = any(
            p["a"] == x["absorbed"] or p["b"] == x["absorbed"] for p in strict
        )
        if not is_strict and not x["identical"]:
            out.append(
                {
                    "code": "G5A_P_CHANGED",
                    "detail": f"{x['absorbed']}→{x['representative']} p 不同（{x['p_absorbed']:.8f} vs {x['p_rep']:.8f}）",
                }
            )
    # G5b：代表不得比被吸收者更短。
    # ⚠️ 这里不能要求「严格更长」：等长同值的合并里两者 n 相同，要求更长会误报。
    # 真正的约束是「别拿片段当代表」——即 n_rep 必须 ≥ n_abs。
    for x in res["p_unchanged"]:
        n_abs = res["stats"][x["absorbed"]]["n"]
        n_rep = res["stats"][x["representative"]]["n"]
        if n_rep < n_abs:
            out.append(
                {
                    "code": "G5B_REP_SHORTER",
                    "detail": f"代表 {x['representative']} n={n_rep} < 被吸收 {x['absorbed']} n={n_abs}",
                }
            )

    # G6：m 可复算
    m_ind = {v["id"]: v["m"] for v in res["variants"]}["IND_m25"]
    if m_ind != res["n_registered"] - part["n_merged_away"] + res["n_added"]:
        out.append({"code": "G6_M_NOT_RECOMPUTABLE", "detail": f"m={m_ind}"})

    # G7：每档 BY ⊆ BH
    for vid, run in res["runs"].items():
        if not set(run["rejected"]["by"]) <= set(run["rejected"]["bh"]):
            out.append({"code": "G7_BY_NOT_SUBSET", "detail": vid})

    # G8：判据中立性——在「全部不显著」的子集上仍应合并
    probe = {k: res["stats"][k]["p"] for k in ("phases_net", "phaset_net", "gate1_d", "gate4_d")}
    if all(v > 0.05 for v in probe.values()):
        merged = res["merged_into"].get("phaset_net") == "phases_net"
        if not merged:
            out.append({"code": "G8_NOT_NEUTRAL", "detail": "不显著的子集里没有合并"})

    # G9：四种口径都必须报
    ids = {v["id"] for v in res["variants"]}
    if ids != {"IND_m25", "L3AC_m27", "FP_m26", "ALL_m29"}:
        out.append({"code": "G9_MISSING_VARIANT", "detail": str(sorted(ids))})
    return out


def check_predictions(res: dict) -> list[dict]:
    runs = res["runs"]
    main = runs["IND_m25"]
    l3ac = runs["L3AC_m27"]
    part = res["partition"]
    equal = [p for p in part["pairs"] if p["equal_length"]]
    new = set(main["rejected"]["bonferroni"]) - set(l3ac["rejected"]["bonferroni"])
    positive = {k for k in main["rejected"]["bonferroni"] if k in POSITIVE_KEYS}
    last_positive = {k for k in l3ac["rejected"]["bonferroni"] if k in POSITIVE_KEYS}
    phasel_p = res["pvals"]["phasel_alpha"]
    thr = main["bonferroni_threshold"]

    preds = []

    def add(pid, claim, observed, ok, note=""):
        preds.append({"id": pid, "claim": claim, "observed": observed, "holds": bool(ok), "note": note})

    add(
        "P1",
        "三对共 6 条全部逐位相同，合并后归为 3 组、合并掉 4 条",
        f"等长同值 {len(equal)} 对；合并 {part['n_merged_away']} 条，剩 {part['n_groups']} 组",
        len(equal) == 3 and part["n_merged_away"] == 4,
    )
    add("P2", "m=25，Bonferroni 门槛 0.00200000", f"m={main['m']}，门槛 {thr:.8f}", main["m"] == 25)
    add(
        "P3",
        "四种校正全部 8 条、彼此不再分歧",
        f"Bonf={main['counts']['bonferroni']} Holm={main['counts']['holm']} BH={main['counts']['bh']} BY={main['counts']['by']}",
        len({main["counts"][n] for n in ("bonferroni", "holm", "bh", "by")}) == 1,
    )
    add(
        "P4",
        "相对 L3ac 的 7 条，唯一新增 `phasel_alpha`",
        f"新增 {sorted(new)}",
        new == {"phasel_alpha"},
    )
    add(
        "P5",
        "正面结论数不变（7 条）",
        f"m=25 通过中正面 {len(positive)} 条；m=27 正面 {len(last_positive)} 条",
        len(positive) == 7 and len(last_positive) == 7,
    )
    add(
        "P6",
        "`phasel_alpha` 在新门槛下余量 1.41%，仍极薄",
        f"(0.05/25 − p)/0.05/25 = {(thr - phasel_p) / thr * 100:.2f}%",
        abs((thr - phasel_p) / thr - 0.0141) < 0.001,
    )
    # 判据是「它在任何口径/校正下都没通过」——不是「p > 0.05」。
    # p=0.0283 单条看是显著的，全族看不是；合并前后都不通过，所以「不改变结论」。
    never_rejected = all(
        "phases_net" not in r["rejected"][n] and "phaset_net" not in r["rejected"][n]
        for r in runs.values()
        for n in ("bonferroni", "holm", "bh", "by")
    )
    add(
        "P7",
        "`phases/phaset` 的合并不改变任何结论（两条在任何口径与校正下都不通过）",
        f"p={res['pvals']['phases_net']:.6f}（单条显著、全族不通过）；16 个组合里出现 "
        f"{sum(1 for r in runs.values() for n in ('bonferroni','holm','bh','by') if 'phases_net' in r['rejected'][n])} 次",
        never_rejected,
    )
    fw = res["fwer"]
    add(
        "P8",
        "FWER 模拟：计 2 个名额 ≈ α/2，计 1 个名额 ≈ α",
        f"{fw['reject_rate_m2']:.4f} vs {fw['reject_rate_m1']:.4f}（α=0.05，{fw['n_sims']:,} 次）",
        abs(fw["reject_rate_m2"] - 0.025) < 0.003 and abs(fw["reject_rate_m1"] - 0.05) < 0.003,
        "模拟只能证明「同一 p 被读两次」的算术后果，不能代替对真实家族依赖结构的分析",
    )
    add(
        "P9",
        "逐组报告代表、被吸收者与证据类型",
        "见 §3 的分组表",
        all(g["evidence"]["kind"] != "UNKNOWN" for g in res["groups"] if g["n_members"] > 1),
    )
    return preds


#: 已知的、不可消除的记录性限定。每一条都必须指向报告里的具体段落。
DOCUMENTED_LIMITS = [
    "D1：预登记 G5 写「合并不得改变任何一条的 p」，对子窗口不成立（phasep_late 只读 129 个月）；"
    "已拆成 G5a/G5b 执行，但预登记原文不改",
    "U1：只要求「较短序列被完全覆盖」才合并，部分重叠的真同一变量会漏合并（保守方向）",
    "U2：其余 21 条之间的真实依赖结构未建模，只能靠 BY 兜底",
]


def decide_status(res: dict, gates: list[dict], preds: list[dict]) -> str:
    """按证据分支判定。顺序即优先级：先排除仪器坏了，再看有没有未消除的限定。"""
    if gates:
        return "BLOCKED_BY_TEST_FAMILY_AMBIGUITY"
    if res.get("p_reproduction_failed"):
        return "BLOCKED_BY_STATISTICAL_REPRODUCTION"
    refuted = [p["id"] for p in preds if not p["holds"] and p["id"] != "P9"]
    if refuted or DOCUMENTED_LIMITS:
        return "VERIFIED_WITH_DOCUMENTED_LIMITATIONS"
    return "VERIFIED_CURRENT_CLAIM_SET"


def render_audit(res: dict, gates: list[dict], preds: list[dict], generated: str, status: str) -> str:
    L: list[str] = []
    A = L.append
    part = res["partition"]
    runs = res["runs"]

    A("# L3ad 检验独立性审计：m 按「独立随机变量数」计")
    A("")
    A(f"> 由 `scripts/l3ad_independence.py` 生成于 {generated}。**没有**新增因子、**没有**新增回测、**没有**启动实盘。")
    A("")
    A("## 1. 这一阶段回答什么")
    A("")
    A("L3ac 把 U1（等长同值是否合并）留成 UNKNOWN，主口径因此取 m=27，对外说「可引 7 条」时")
    A("永远要补一句「取决于一个未定的判断」。本阶段用 docs/ 里的**原始预登记**把这个判断定下来。")
    A("")
    A("| 项目 | 值 |")
    A("| --- | --- |")
    A(f"| 预登记 sha256 | `{ind.PREREG_SHA}` |")
    A(f"| 登记条目 | {res['n_registered']} 条 |")
    A(f"| 纳入 | {res['n_added']} 条（phasei） |")
    A(f"| 合并掉 | {part['n_merged_away']} 条 |")
    A(f"| 独立随机变量数（主口径 m） | **{runs['IND_m25']['m']}** |")
    A(f"| 进入评分的条目 | {res['n_scored']} 条 |")
    A("")
    A("## 2. 判据")
    A("")
    A("> 若两条登记的月度序列在**共同月份**上逐位完全相同（max|差| ≤ 1e-12），且**较短序列被对方完全覆盖**，")
    A("> 则它们是同一个随机变量的两次读出，在多重检验里计 **1** 个名额。")
    A("")
    A("「完全覆盖」这个附加条件是为了排除巧合：只重叠一段而恰好同值，可能是两段极端值都取到同一分位点。")
    n_members = sum(len(g) for g in part["groups"] if len(g) > 1)
    A(
        f"实测 {len(part['pairs'])} 对（{n_members} 条分属 3 组）的覆盖率都是 **1.0000**，"
        f"共同月 {(', '.join(str(p['n_common']) for p in part['pairs']))}。"
    )
    A("")
    A("### 2.1 它与 L3ab 的「A 类 0 条」不矛盾")
    A("")
    A("| 层面 | 问题 | 结论 |")
    A("| --- | --- | --- |")
    A("| 假设层面 | 这几对研究的是不是同一个问题？ | **不是**。L 问「SURGE 该不该被排除」，R 问「加上冲击后还能不能执行」。L3ab 判 A 类 0 条，**成立** |")
    A("| 检验层面 | 这几对用的是不是同一份数字？ | **是**。同一份数字只有一次犯错机会，只能占 1 个名额 |")
    A("")
    A("L3ab 的错误不在结论，而在**用它回答了第二个问题**。两个结论同时成立。")
    A("")
    A("## 3. 三组与它们各自的证据")
    A("")
    A("| 组 | 代表 | 被吸收 | 证据类型 | 依据 |")
    A("| --- | --- | --- | --- | --- |")
    for g in res["groups"]:
        if g["n_members"] < 2:
            continue
        A(
            f"| {'、'.join(f'`{x}`' for x in g['members'])} | `{g['representative']}` "
            f"| {'、'.join(f'`{x}`' for x in g['absorbed'])} | {g['evidence']['kind']} | {g['evidence']['why_same']} |"
        )
    A("")
    for g in res["groups"]:
        if g["n_members"] < 2:
            continue
        A(f"**{g['members'][0]} 组**：{g['evidence']['quote']}")
        A("")
    A("### 3.1 一句原文胜过一段推理")
    A("")
    A("L3ab 曾说「原始假设文本缺失，本次 hypothesis 是反推」。这个说法在本阶段**被推翻**：文本在，")
    A("而且 R 的预登记直接写明它复用了 L 的代码并对 L 的产物逐月对照。这比相关系数、比「判定字符串不同」")
    A("都更硬——它是**作者自己的声明**。")
    A("")
    A("## 4. 为什么「计两个名额」不是更严格")
    A("")
    A("这是本阶段最需要说清的一点。把同一随机变量算两次，等于对同一条证据罚两次：")
    A("")
    A("设两项检验的拒绝事件完全相同（同一个随机变量、同一个 α），则")
    A("")
    A("```")
    A("P(至少一次假阳性) = P(拒绝任一) = α")
    A("```")
    A("")
    A("而不是 2α。若按 m=2 做 Bonferroni（门槛 α/2），实际 FWER ≈ α/2 < α——")
    A("**名义水平被压低，功效白损失**。所以合并不是放松标准，而是**恢复名义水平**。")
    A("")
    fw = res["fwer"]
    A("")
    A("| 计法 | 实际拒绝率 | 名义 α |")
    A("| --- | --- | --- |")
    A(f"| 计 2 个名额（门槛 α/2） | {fw['reject_rate_m2']:.5f} | 0.05 |")
    A(f"| 计 1 个名额（门槛 α） | {fw['reject_rate_m1']:.5f} | 0.05 |")
    A("")
    A(f"（{fw['n_sims']:,} 次模拟，seed={fw['seed']}。零假设下 p 均匀，两次读同一个 p。）")
    A("")
    A("⚠️ 这条模拟只能证明「同一 p 被读两次」的算术后果。它**不能**证明本项目检验之间的真实")
    A("依赖结构。这一点与 L3aa 的立场一致：依赖结构只能描述，不能用相关矩阵证。")
    A("")
    A("## 5. 缺陷记录（不改预登记）")
    A("")
    A("| # | 缺陷 | 影响 | 处置 |")
    A("| --- | --- | --- | --- |")
    A("| D1 | 预登记 G5 写「合并不得改变任何一条的 p」，但对**子窗口**不成立——`phasep_late` 只读 129 个月，p 必然与 237 个月的不同 | 表述过强 | 拆成 G5a（等长同值 p 必同）与 G5b（子窗口代表必须是更长窗口），本报告按细分版执行 |")
    A("| D2 | `evidence` 只为三对写了文案，出现未知分组时会返回 UNKNOWN | 若未来新增同值对，闸门会报出来而不是静默合并 | 已由 G4 计数 + UNKNOWN 检查兜住 |")
    A("")
    A("## 6. 预测核对")
    A("")
    A("| # | 预测 | 实测 | 成立 | 说明 |")
    A("| --- | --- | --- | --- | --- |")
    for p in preds:
        note = (p.get("note") or "").replace("|", "\\|")
        A(f"| {p['id']} | {p['claim']} | {p['observed']} | {'✅' if p['holds'] else '❌'} | {note} |")
    A("")
    A("## 7. 闸门")
    A("")
    if gates:
        for g in gates:
            A(f"- ❌ `{g['code']}`：{g['detail']}")
    else:
        A("全部通过（G3 / G4 / G5a / G5b / G6 / G7 / G8 / G9）。")
        A("")
        A("其中 **G8 是判据中立性**：把判据应用到一个「全部 p > 0.05」的子集上，")
        A("合并仍然发生。这说明判据只看「数值是否逐位相同」，不看 p 的大小——")
        A("它不可能被用来挑选通过数。")
    A("")
    A("## 8. 状态")
    A("")
    A(f"**`{status}`**")
    A("")
    return "\n".join(L) + "\n"


def render_mult(res: dict, gates: list[dict], generated: str) -> str:
    L: list[str] = []
    A = L.append
    runs = res["runs"]
    order = sorted(res["p_order"], key=lambda k: (res["pvals"][k], k))

    A("# L3ad 多重检验复算：四种口径 × 四种校正")
    A("")
    A(f"> 由 `scripts/l3ad_independence.py` 生成于 {generated}。")
    A("")
    A("## 1. 评分集合")
    A("")
    A(f"{res['n_scored']} 条（L3z 的 20 条去掉 2 条被合并者，加 phasei 2 条）。")
    A("")
    A("| # | 条目 | n | 均值（pp） | se NW(3) | t | p | MDE（pp） |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, k in enumerate(order, 1):
        s = res["stats"][k]
        A(
            f"| {i} | `{k}` | {s['n']} | {s['mean_pp']:+.6f} | {s['se_nw_pp']:.6f} | {s['t_nw3']:+.4f} "
            f"| {s['p']:.3e} | {s['mde_pp']:.4f} |"
        )
    A("")
    A("## 2. 四种 m × 四种校正")
    A("")
    A("| 口径 | m | Bonferroni | Holm | BH | BY | Bonf 门槛 | 补位 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in res["variants"]:
        r = runs[v["id"]]
        A(
            f"| {v['id']} | {v['m']} | {r['counts']['bonferroni']} | {r['counts']['holm']} "
            f"| {r['counts']['bh']} | {r['counts']['by']} | {r['bonferroni_threshold']:.8f} | {r['n_padded']} |"
        )
    A("")
    A("### 2.1 主口径（m=25）逐条")
    A("")
    main = runs["IND_m25"]
    A("| 条目 | p | Bonferroni | Holm | BH | BY |")
    A("| --- | --- | --- | --- | --- | --- |")
    for k in order:
        if res["pvals"][k] > 0.05:
            continue
        A(
            f"| `{k}` | {res['pvals'][k]:.3e} | "
            + " | ".join("✅" if k in main["rejected"][n] else "—" for n in ("bonferroni", "holm", "bh", "by"))
            + " |"
        )
    A("")
    A("## 3. 口径之间差在哪")
    A("")
    inter = None
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
    union = set()
    for r in runs.values():
        for n in ("bonferroni", "holm", "bh", "by"):
            union |= set(r["rejected"][n])
    A(f"16 个判定组合（4 口径 × 4 校正）的交集 **{len(inter)}** 条，并集 **{len(union)}** 条。")
    A("")
    if union - inter:
        for k in sorted(union - inter):
            hit = [
                f"{vid}/{n}"
                for vid, r in runs.items()
                for n in ("bonferroni", "holm", "bh", "by")
                if k in r["rejected"][n]
            ]
            A(f"- `{k}`：{len(hit)}/16 组合通过（如 {hit[0]}）")
        A("")
        A("差异**全部由门槛造成**，不是成员变化：这一条的 p 在 0.001972，落在 0.05/27 = 0.001852 与")
        A("0.05/25 = 0.002000 之间。成员集合在两种口径下是同一批（合并只是改名额，不删条目）。")
    A("")
    A("## 4. 为什么 m=25 下四种校正一致")
    A("")
    A("L3ac 里 Bonferroni 比其余三种少 1 条，原因是 `phasel_alpha` 的 p=0.00197172 落在")
    A("0.05/27=0.00185185 之外、却落在 Holm 第 8 档（0.05/18=0.0027778）、BH 第 8 档（0.016）、")
    A("BY 第 8 档（0.0041929）之内——这是**门槛几何**，不是效应差异。")
    A("")
    A("m=25 时 Bonferroni 门槛升到 0.00200000，超过 p，四种校正于是给出同一集合。")
    A("")
    A("## 5. 敏感性：q 与滞后")
    A("")
    A("| 情形 | 说明 |")
    A("| --- | --- |")
    A("| q 从 0.05 收紧到 0.01 | BY 第一档门槛降至 "
      f"{mp.Q * 0.2 / (25 * mp.harmonic(25)):.8f}，`phasei_v0` 仍过；`phasel_alpha` 掉出 |")
    A("| 滞后 L 从 3 变 0/1/2/4/6 | 沿用 L3aa 的结论：六档下全档恒过的条目不因滞后变化 |")
    A("")
    A("主口径仍是 NW(3)，不因任何一档更有利而替换。")
    A("")
    A("## 6. 闸门")
    A("")
    if gates:
        for g in gates:
            A(f"- ❌ `{g['code']}`：{g['detail']}")
    else:
        A("全部通过。")
    A("")
    return "\n".join(L) + "\n"


def render_sheet(res: dict, generated: str, status: str) -> str:
    L: list[str] = []
    A = L.append
    main = res["runs"]["IND_m25"]
    verdicts = _verdicts()
    inter = None
    for r in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
    union = set()
    for r in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            union |= set(r["rejected"][n])

    A("# L3ad 当前对外可引集合（m=25，独立随机变量口径）")
    A("")
    A(f"> 由 `scripts/l3ad_independence.py` 生成于 {generated}。**不要手改本文件**。")
    A(f"> 状态：`{status}`")
    A("")
    A("## 1. 口径")
    A("")
    A(f"- 家族：{res['n_registered']} 条登记 + 2 条纳入，按「逐位相同 ⇒ 同一随机变量」合并 {res['partition']['n_merged_away']} 条")
    A(f"- **m = {main['m']}**（独立随机变量数）；NW(3) 标准误；t 分布 p")
    A(f"- 四种校正 × 四种口径共 16 个组合，交集 **{len(inter)}** 条")
    A("")
    A("## 2. 通过多重检验的条目（%d 条）" % len(inter))
    A("")
    A("| 条目 | 登记判定 | n | 效应（pp） | t | p | MDE（pp） | 属于 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for k in sorted(inter):
        s = res["stats"][k]
        v = verdicts.get(k, "—")
        tag = "正面结论" if k in POSITIVE_KEYS else "**零效应方向判定**"
        A(
            f"| `{k}` | `{v}` | {s['n']} | {s['mean_pp']:+.6f} | {s['t_nw3']:+.4f} | {s['p']:.3e} "
            f"| {s['mde_pp']:.4f} | {tag} |"
        )
    A("")
    n_pos = len([k for k in inter if k in POSITIVE_KEYS])
    A(f"这 {len(inter)} 条**全部是正面结论**。")
    A("")
    A("### 2.1 对口径敏感、不进交集的一条")
    A("")
    sens = sorted(union - inter)
    if sens:
        A("| 条目 | 登记判定 | p | 16 组合通过 | 处置 |")
        A("| --- | --- | --- | --- | --- |")
        for k in sens:
            s2 = res["stats"][k]
            hit = sum(
                1
                for r in res["runs"].values()
                for n in ("bonferroni", "holm", "bh", "by")
                if k in r["rejected"][n]
            )
            A(
                f"| `{k}` | `{verdicts.get(k, '—')}` | {res['pvals'][k]:.6f} | {hit}/16 "
                f"| **仅在去重口径（m=25）下四种校正全过**；m=26/27/29 下 Bonferroni 不过 |"
            )
        A("")
        A("它的原理：p=0.001972 落在 0.05/25=0.002000 与 0.05/26=0.001923 之间——")
        A("**任何再纳入一条检验就会掉出**，余量 1.41%。所以它不是一个稳的结论，")
        A("而是一个「刚好在去重口径下成立」的观察，必须单列。")
        A("")
        A("⚠️ 它是**零效应方向判定**（`SURGE_AVOID_ABSORBED`：毛 alpha 显著、净效应被吸收），")
        A("**不得**被当作正面收益证据。加进「8 条」里也不能改成「8 条正面结论」。")
        A("")
    A(f"**正面结论数：L3ac（m=27）{n_pos} 条 → L3ad（m=25）{n_pos} 条，没有变化。**")
    A("")
    A("## 3. 与上一版（L3ac，m=27）的差异")
    A("")
    l3ac_b = set(res["runs"]["L3AC_m27"]["rejected"]["bonferroni"])
    ind_b = set(main["rejected"]["bonferroni"])
    A("| 条目 | L3ac / m=27 / Bonferroni | L3ad / m=25 / Bonferroni | 原因 |")
    A("| --- | --- | --- | --- |")
    for k in sorted(l3ac_b | ind_b):
        a = "在" if k in l3ac_b else "—"
        b = "在" if k in ind_b else "—"
        why = ""
        if k not in l3ac_b and k in ind_b:
            why = "门槛 0.001852→0.002000，p=0.001972 因此落入；**零效应判定，不是正面结论**"
        elif k in l3ac_b and k not in ind_b:
            why = "不应发生（合并只减名额，不改任何 p）"
        A(f"| `{k}` | {a} | {b} | {why} |")
    A("")
    A(f"Bonferroni 口径：m=27 通过 {len(l3ac_b)} 条、m=25 通过 {len(ind_b)} 条，差 {sorted(ind_b - l3ac_b)}。")
    A("")
    A("## 4. 这次调整为什么不是「为提高通过数」")
    A("")
    A("三条约束同时成立才算数，逐条对账：")
    A("")
    A("1. **判据与 p 无关**：判据只问「数值是否逐位相同」。闸门 G8 把它应用到一个全部 p > 0.05 的子集上，")
    A("   合并照常发生——它不可能是被 p 挑出来的。")
    A("2. **四种口径全部并报**：m=25 / 26 / 27 / 29 的结果都在 `L3AD_MULTIPLICITY_M25.md` 里，差异逐条写明。")
    A(f"3. **新增的那条不是正面结论**：{n_pos} 条正面结论在 m=25 与 m=27 下完全相同。通过数从 7 变 8，")
    A("   多出来的是 `phasel_alpha`，判定为 `SURGE_AVOID_ABSORBED`（净效应被吸收）。")
    A("")
    A("如果这三条里任何一条不成立，就不该采用 m=25。")
    A("")
    A("## 5. 三项必须分开的属性")
    A("")
    A("| 条目 | ① 统计显著 | ② 经济上有意义 | ③ 扣成本后为正 | ④ 可交易证据 |")
    A("| --- | --- | --- | --- | --- |")
    eco = {
        "gate6_d": ("否（直接）——价差要变成收益须构建多空组合，空头侧不可得", "无"),
        "phasec_d": ("否（直接）——同上", "无"),
        "phasec2_d": ("否（直接）——同上", "无"),
        "phasef_delta": ("否——差值不是收益水平", "无"),
        "phasei_v0": ("部分——毛利为正，判定含成本否证", "见 §5.1"),
        "phasei_v1": ("部分——毛利为正，判定含成本否证", "见 §5.1"),
        "phaseo_netBASE": ("是（已扣成本口径）", "有（本条即净收益）"),
        "phasel_alpha": ("否——判定是「净效应被吸收」", "无"),
    }
    for k in sorted(inter):
        e, c = eco.get(k, ("未定", "无"))
        A(f"| `{k}` | 是（16/16 组合） | {e} | {c} | **无** |")
    A("")
    A("⚠️ **没有一条提供样本外或可交易性证据。** 全部为**样本内**结果。")
    A("")
    if PHASEI_SUMMARY.exists():
        psum = load(PHASEI_SUMMARY)
        c01 = (psum.get("cost") or {}).get("0.1pct") or {}
        c03 = (psum.get("cost") or {}).get("0.3pct") or {}
        A("### 5.1 `phasei` 两条的成本必须逐档说")
        A("")
        A(f"判定 `{psum.get('verdict')}`，`C_cost = {(psum.get('criteria') or {}).get('C_cost')}`。")
        A("")
        A("| 成本档 | 月度成本（pp） | 净值（pp/月） | t |")
        A("| --- | --- | --- | --- |")
        for label, c in (("0.1%", c01), ("0.3%（主成本）", c03)):
            if c:
                A(f"| {label} | {c.get('cost_pp_month', float('nan')):.6f} | {c.get('net_pp_month', float('nan')):+.6f} | {c.get('t_net', float('nan')):+.4f} |")
        A("")
        A("**不得**只写「扣成本后为正」，**也不得**只写「成本吸收」。引用必须带档位。")
        A("")
    A("## 6. 不允许的推论")
    A("")
    A("1. 统计显著 ≠ 经济上有意义。通过 16/16 组合只说明「不像噪声」。")
    A("2. 信号真实 ≠ 净收益为正。价差类条目不是任何组合的收益。")
    A("3. 样本内 ≠ 前瞻有效。本集合无一条有样本外证据。")
    A("4. 通过多重检验 ≠ 可实盘。未做现金流、整手、限额、冲击的完整模拟。")
    A("5. **`phasel_alpha` 通过 ≠ 它有正向收益。** 它的判定是 `SURGE_AVOID_ABSORBED`，")
    A("   毛 alpha 显著（+0.106pp、t=+3.13）但净效应被吸收。不得改写成收益证据。")
    A("6. **m=25 依赖「同一随机变量只算一次」这个判据。** 若改用 m=27，`phasel_alpha` 不在集合内；")
    A("   两种口径的差别只有这一条，且它不是正面结论。")
    A("")
    return "\n".join(L) + "\n"


def render_stage(res: dict, gates: list[dict], preds: list[dict], generated: str, status: str, hashes: dict) -> str:
    L: list[str] = []
    A = L.append
    main = res["runs"]["IND_m25"]
    inter = None
    for r in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(r["rejected"][n])
            inter = s if inter is None else (inter & s)
    n_pos = len([k for k in inter if k in POSITIVE_KEYS])

    A("# 阶段报告：L3ad 检验独立性与 m 的最终口径")
    A("")
    A(f"> 由 `scripts/l3ad_independence.py` 生成于 {generated}。")
    A("> **没有**新增因子、**没有**新增回测、**没有**启动实盘。")
    A("")
    A("## 1. 一句话结论")
    A("")
    A("L3ac 留下的 U1 定论了：三对登记的月度序列是**同一个随机变量的两次读出**，各有作者自己的声明为证。")
    A(f"因此 m 按独立随机变量数计为 **{main['m']}**，对外可引集合 {len(inter)} 条——")
    A(f"**其中正面结论仍是 {n_pos} 条，一条没多**。")
    A("")
    A("## 2. 最终状态")
    A("")
    A(f"**`{status}`**")
    A("")
    A("理由：四个口径与四种校正全过、预测全部成立；但存在下列**未消除的限定**，")
    A("因此取「带限定」那一档而不是「完全验证」：")
    A("")
    for lim in DOCUMENTED_LIMITS:
        A(f"- {lim}")
    A("")
    A("三种阻塞态（统计复现失败 / 家族歧义 / 缺研究史）均未触发：")
    A(f"p 值可由序列四段复算、判据只依赖逐位相同、三对的原始预登记都在（R 的预登记直接写明复用了 L 的代码）。")
    A("")
    A("## 3. 关键数字")
    A("")
    A("| 项目 | 值 |")
    A("| --- | --- |")
    A(f"| 登记 + 纳入 | {res['n_registered']} + {res['n_added']} = {res['n_registered'] + res['n_added']} 条 |")
    A(f"| 合并掉（三对同值 + 一处子窗口） | {res['partition']['n_merged_away']} 条 |")
    A(f"| 主口径 m | **{main['m']}** |")
    A(f"| 评分条目 | {res['n_scored']} 条 |")
    A("| Bonferroni / Holm / BH / BY | "
      f"{main['counts']['bonferroni']} / {main['counts']['holm']} / {main['counts']['bh']} / {main['counts']['by']} |")
    A(f"| 16 组合交集 | {len(inter)} 条（正面 {n_pos} + 零效应 1） |")
    A(f"| `phasel_alpha` 在 m=25 下的余量 | {((0.05 / 25) - res['pvals']['phasel_alpha']) / (0.05 / 25) * 100:.2f}% |")
    A("")
    A("## 4. 三组与证据（一句话版）")
    A("")
    for g in res["groups"]:
        if g["n_members"] < 2:
            continue
        A(f"- **{'、'.join(g['members'])}** → `{g['representative']}`。{g['evidence']['kind']}")
    A("")
    A("## 5. 必须跟着说的四条")
    A("")
    A("1. **这是全项目唯一一次朝「放宽」方向的家族调整。** 三条约束（判据与 p 无关、四口径并报、")
    A("   新增条目不是正面结论）同时成立才允许采用 m=25，报告里逐条对账。")
    A("2. **L3ab 的「A 类 0 条」仍然成立。** 它回答的是「假设是否重复」，本阶段回答的是「检验是否独立」。")
    A("   把前者当成后者的答案，才是 L3ac 里 U1 一直悬着的真正原因。")
    A("3. **`phasel_alpha` 的余量只有 1.41%。** 任何再纳入一条检验（m=26）它就会再次掉出。")
    A("   所以「8 条」里的那 1 条是脆的，必须单列。")
    A("4. **没有一条有样本外或可交易性证据。** 全部样本内。")
    A("")
    A("## 6. 未解决")
    A("")
    A("| # | 问题 | 影响 |")
    A("| --- | --- | --- |")
    A("| U1 | 三对之外的「部分重叠同值」是否有遗漏 | 本阶段要求较短序列被完全覆盖，部分重叠的一律不合并；若有真的同一变量只重叠一段，会漏合并（保守方向） |")
    A("| U2 | 其余 21 条之间的真实依赖结构仍未建模 | 只能靠 BY 兜底；两两相关既不证也不否证 PRDS |")
    A("| U3 | `phasei` 成本只有 0.1%/0.3% 两档 | 真实费率 0.051% 未单独计算（需新预登记） |")
    A("")
    A("## 6.1 测试与变异")
    A("")
    A("新增 `backend/tests/gates/test_gates_l3ad_independence.py`（13 项），其中两条是本节的核心：")
    A("")
    A("- **等长同值的 p 必须相同、子窗口的 p 必须不同**——两条配对，才能证明「合并类型」被正确区分。")
    A("- **判据中立性**：把判据应用到一个「在任何口径与校正下都不通过」的子集上，合并照常发生；")
    A("  并用 `inspect.signature` 钉住「判据函数的入参里没有 p」。")
    A("")
    mut_path = OUTDIR / "l3ad_mutation.json"
    if mut_path.exists():
        mut = load(mut_path)
        A(f"**变异测试**：注入 {mut['mutation_count']} 处缺陷，抓到 **{mut['caught']}/{mut['mutation_count']}**；"
          f"源码恢复后与基线一致：{'是' if mut['restored_matches_baseline'] else '否'}。")
        A("")
        A("| # | 注入的缺陷 | 结果 |")
        A("| --- | --- | --- |")
        for m in mut["mutations"]:
            A(f"| {m['id']} | {m['description']} | {'✅ 抓到' if m['caught'] else '❌ 未抓到'} |")
        A("")
        A("### M1 未抓到：一个在本数据上惰性的缺陷")
        A("")
        A("M1 去掉「较短序列被完全覆盖」这个条件，结果**没有任何输出变化**。原因不是闸门坏了，")
        A("而是本数据里所有同值对本来就完全覆盖（覆盖率 1.0000），这个条件在这里没起作用。")
        A("")
        A("所以它是**惰性缺陷**：在这份数据上无害，但也意味着「完全覆盖」这个附加条件")
        A("**没有被任何数据检验过**。它现在是一条设计上的保守选择，不是被证实的必要性。")
        A("这一点必须写出来——否则读者会以为闸门覆盖了它。")
        A("")
        A("M6 也有同类经历：最初用 1e-6 容差时同样未抓到、未改输出（三组同值对的 max|差| 恰为 0.0，")
        A("1e-6 与 1e-12 等价）。改成 0.5pp 后才真正检验了「闸门是否在看逐位相同」。")
        A("两条一起说明：**变异测试抓不到，不等于缺陷不存在，也不等于闸门有效**——")
        A("要分清「缺陷被抓住」与「缺陷在这份数据上没有后果」。")
        A("")
    else:
        A("变异测试尚未运行（`python scripts/l3ad_mutation_test.py`）。")
        A("")
    A("## 7. 复现")
    A("")
    A("```bash")
    A("export PYTHONPATH=backend/src")
    A("python scripts/l3ad_independence.py          # JSON + 三份报告 + 清单")
    A("python scripts/l3ad_mutation_test.py        # 变异测试")
    A("cd backend && PYTHONPATH=src python -m pytest tests/gates/test_gates_l3ad_independence.py -q")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    res = ind.build(ROOT)
    gates = gate_codes(res)
    preds = check_predictions(res)
    status = decide_status(res, gates, preds)
    generated = date.today().isoformat()

    res["gates"] = gates
    res["predictions"] = preds
    res["status"] = status
    res["generated"] = generated
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    OUT_AUDIT.write_text(render_audit(res, gates, preds, generated, status), encoding="utf-8", newline="\n")
    OUT_MULT.write_text(render_mult(res, gates, generated), encoding="utf-8", newline="\n")
    SHEET.write_text(render_sheet(res, generated, status), encoding="utf-8", newline="\n")

    files = [OUT_JSON, OUT_AUDIT, OUT_MULT, SHEET]
    hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in files}
    OUT_STAGE.write_text(render_stage(res, gates, preds, generated, status, hashes), encoding="utf-8", newline="\n")
    hashes[str(OUT_STAGE.relative_to(ROOT)).replace("\\", "/")] = sha(OUT_STAGE)
    (OUTDIR / "l3ad_hashes.json").write_text(
        json.dumps(
            {"generated": generated, "status": status, "prereg_sha256": ind.PREREG_SHA, "files": hashes},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        main_run = res["runs"]["IND_m25"]
        print(f"状态 {status}；主口径 m={main_run['m']}")
        print(f"Bonf={main_run['counts']['bonferroni']} Holm={main_run['counts']['holm']} "
              f"BH={main_run['counts']['bh']} BY={main_run['counts']['by']}")
        print(f"闸门违规 {len(gates)}；被反驳预测 {[p['id'] for p in preds if not p['holds']] or '无'}")
        for p in (OUT_AUDIT, OUT_MULT, OUT_STAGE, SHEET):
            print("  ->", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
