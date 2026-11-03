#!/usr/bin/env python
"""L3z：多重检验校正 —— 同时报告 27 条结论时，哪些还站得住。

此前各层管的都是单条结论的数字对不对；这一层管的是「这 N 条是一起报出来的」。

用法：
    PYTHONPATH=backend/src python scripts/l3z_multiplicity.py
    PYTHONPATH=backend/src python scripts/l3z_multiplicity.py --quiet
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from deepquant.gates import multiplicity as mp

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY"

PREREG = ROOT / "docs" / "gates" / "l3z_prereg.json"
PREREG_SHA = "202758c3f916170ef7c928968823fc95ac77db76d223661fd31f913fdc03838f"
L3U_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
#: 审计前的卡片备份（用于追溯「从哪一档降下来」）
PREVIOUS_CARDS = ROOT / "docs" / "gates" / "l3v_claims_v1_pre2026-10-29.json"
L3V_RESULT = ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY" / "l3v_claims.json"

FROZEN = {
    "l3u_result": (
        L3U_RESULT,
        "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b",
    ),
    "claims_cards": (
        CARDS,
        "5b4173c7265e5dd5be0cbf37b9af8620ed3488ea3480224d249a91740b1317b0",
    ),
}

#: 敏感性扫描用的名义水平。alpha（FWER 类）与 q（BH）分开扫，不绑成同一个值。
ALPHA_GRID = (0.01, 0.05, 0.10)
Q_GRID = (0.01, 0.05, 0.10)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_frozen() -> None:
    problems = []
    if not PREREG.exists():
        raise SystemExit(f"预登记不存在：{PREREG}")
    got_prereg = _sha(PREREG)
    if got_prereg != PREREG_SHA:
        raise SystemExit(
            f"预登记已被改动：期望 {PREREG_SHA[:12]}…，实际 {got_prereg[:12]}…；"
            "拒绝运行。若确需修订，先写入 amendments 并更新脚本里的 sha。"
        )
    for key, (path, expected) in FROZEN.items():
        if not path.exists():
            problems.append(f"{key}: 不存在 {path.relative_to(ROOT)}")
            continue
        got = _sha(path)
        if got != expected:
            problems.append(f"{key}: 期望 {expected[:12]}…，实际 {got[:12]}…")
    if problems:
        raise SystemExit("冻结输入已被改动，拒绝运行：\n  " + "\n  ".join(problems))


def _fingerprint(row) -> tuple:
    rc = row.get("recomputed") or {}
    m, t, n = rc.get("mean"), rc.get("t_nw3"), rc.get("n")
    if m is None or t is None or n is None:
        return ("key", str(row.get("key")))
    return (round(float(m), 10), round(float(t), 10), int(n))


def _drop_duplicate_rows(rows: list[dict]) -> list[dict]:
    """每组重复只保留一条（保留先出现的），用于「实删」敏感性检验。"""
    seen: set = set()
    out = []
    for row in rows:
        fp = _fingerprint(row)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(row)
    return out


def _gates(result: dict, l3v: dict, scored_rows: list[dict]) -> list[dict]:
    """闸门：仪器自检。与「某条掉档」是两件事。"""
    violations: list[dict] = []

    # G1 p 值精度
    for t, df, ref in ((1.96, 236, 0.05117), (2.0, 236, 0.04665), (3.0, 236, 0.002989)):
        got = mp.t_two_sided_p(t, df)
        if abs(got - ref) > 1e-3:
            violations.append(
                {
                    "code": "P_VALUE_PRECISION",
                    "detail": f"t={t}, df={df} 得 p={got:.6f}，对照 {ref}，差 {abs(got - ref):.2e}",
                }
            )
    # G1b p_to_t 是 t_two_sided_p 的逆
    for p in (0.05, 0.01, 0.001):
        back = mp.t_two_sided_p(mp.p_to_t(p, 236), 236)
        if abs(back - p) > 1e-6:
            violations.append(
                {"code": "P_INVERSION", "detail": f"p={p} 反解再回代得 {back:.8f}"}
            )

    # G2 m_independent 必须等于 L3v 的独立数字数
    l3v_indep = l3v.get("n_independent_numbers")
    if l3v_indep is not None and result["m_independent"] != l3v_indep:
        violations.append(
            {
                "code": "M_INCONSISTENT",
                "detail": f"本模块算得独立检验 {result['m_independent']}，L3v 记 {l3v_indep}",
            }
        )

    # G3 只计名额的条目不得出现在评分结果里
    counts_only_keys = {r["key"] for r in scored_rows if r.get("_counts_only")}
    for tag, run in result["runs"].items():
        leaked = [i["key"] for i in run["items"] if i["key"] in counts_only_keys]
        if leaked:
            violations.append(
                {"code": "COUNTS_ONLY_SCORED", "detail": f"{tag} 把只计名额的条目评了分：{leaked}"}
            )
        if run["n_counts_only"] != len(counts_only_keys):
            violations.append(
                {"code": "COUNTS_ONLY_MISC", "detail": f"{tag} 计名额数 {run['n_counts_only']} 不等于 {len(counts_only_keys)}"}
            )

    # G4 全零效应时任何校正都不得放行
    zeros = [1.0] * 27
    if any(mp.bonferroni(zeros)) or any(mp.holm(zeros)) or any(mp.bh(zeros)):
        violations.append({"code": "ZERO_EFFECT_LEAK", "detail": "全部 p=1 时仍有条目被放行"})
    tiny = [1e-9] * 27
    if not all(mp.bonferroni(tiny)):
        violations.append({"code": "ZERO_EFFECT_LEAK", "detail": "全部 p=1e-9 时 Bonferroni 未全部放行"})

    # G5 校正不制造「无效应」
    for tag, run in result["runs"].items():
        for item in run["items"]:
            if item["verdict"] == "NULL_CONFIRMED" and (item["power"] or 0.0) < mp.POWER_FLOOR:
                violations.append(
                    {
                        "code": "POWERLESS_NULL",
                        "detail": (
                            f"{tag}/{item['key']} 判 NULL_CONFIRMED 但功效 {item['power']:.2f} "
                            f"< {mp.POWER_FLOOR}：功效不足不等于无效应"
                        ),
                    }
                )
    return violations


def _check_predictions(
    result: dict, sens: dict, quotability: dict, previous_quotability: dict | None = None
) -> list[dict]:
    main = result["runs"]["m_independent"]
    all_run = result["runs"]["m_all"]
    n_raw = sum(i["raw_significant"] for i in main["items"])
    n_bonf = sum(i["bonferroni"] for i in main["items"])
    n_bh = sum(i["bh"] for i in main["items"])
    down = {i["key"] for i in main["items"] if i["verdict"] == "DOWNGRADED"}
    # P2 观测的是「降级前」的定稿分级——审计对象就是那一份；
    # 卡片已被改写，用当前 quotability 会让判据失效而非结论变化。
    baseline = previous_quotability or quotability
    down_dir = {k for k in down if baseline.get(k) == "DIRECTION_ONLY"}
    phasel_all = next(
        (i["verdict"] for i in all_run["items"] if i["key"] == "phasel_alpha"), None
    )
    phasel_indep = next(
        (i["verdict"] for i in main["items"] if i["key"] == "phasel_alpha"), None
    )

    observed = {
        "P1": {"raw_significant": n_raw, "bonferroni": n_bonf, "bh": n_bh},
        "P2": {"downgraded": sorted(down), "downgraded_direction_only": sorted(down_dir)},
        "P3": {"phasel_m_all": phasel_all, "phasel_m_independent": phasel_indep},
        "P4": {f"{a:.2f}": sens["alpha"][f"{a:.2f}"]["downgraded_bh"] for a in ALPHA_GRID},
        "P5": {
            "baseline_bh": n_bh,
            "drop_duplicates_bh": sens["drop_duplicates"]["bh_pass"],
        },
        "P6": {
            "baseline_bh": n_bh,
            "include_counts_only_bh": sens["include_counts_only"]["bh_pass"],
        },
    }
    holds = {
        "P1": n_raw == 10 and n_bonf == 6 and n_bh == 7,
        "P2": down_dir == {"gate3_d", "phases_net", "phaset_net"},
        "P3": phasel_all == "SURVIVES_FDR_ONLY" and phasel_indep == "SURVIVES_ALL",
        "P4": (
            len(sens["alpha"]["0.01"]["downgraded_bh"]) >= 4
            and down_dir <= set(sens["alpha"]["0.01"]["downgraded_bh"])
            and set(sens["alpha"]["0.10"]["downgraded_bh"]) <= down
        ),
        "P5": sens["drop_duplicates"]["bh_pass"] == n_bh,
        "P6": abs(sens["include_counts_only"]["bh_pass"] - n_bh) >= 1,
    }
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for pred in prereg["predictions"]:
        pid = pred["id"]
        rows.append(
            {
                "id": pid,
                "timing": pred.get("timing", "priori"),
                "statement": pred["statement"],
                "observed": observed[pid],
                "holds": holds[pid],
                "refuted": not holds[pid],
            }
        )
    return rows


def _render(result, sens, preds, quotability, violations, l3u_idx, cross) -> str:
    def esc(x) -> str:
        return str(x).replace("|", r"\|")

    lines = []
    lines.append("# L3z：多重检验校正")
    lines.append("")
    main = result["runs"]["m_independent"]
    lines.append(
        f"登记条目 **{result['m_all']}** 条，去重后独立检验 **{result['m_independent']}** 份；"
        f"在 α={result['alpha']} 下，若全部零效应，期望假阳性 **{result['expected_false_positives']:.2f} 条**。"
    )
    lines.append("")
    lines.append("## 一、为什么要有这一层")
    lines.append("")
    lines.append(
        "此前各层（L3t 分辨率、L3u 引用核对、L3v 卡片、L3w/L3x 解封、L3y 定稿）管的都是"
        "**单条**结论的数字对不对。这一层管的是另一件事：这些结论是**一起报出来的**。"
    )
    lines.append("")
    lines.append(
        f"单条 α={result['alpha']} 显著的有 "
        f"{sum(i['raw_significant'] for i in main['items'])} 条——但这恰恰是问题所在："
        "在全部零效应的世界里，报告这么多条检验，期望也会冒出 "
        f"{result['expected_false_positives']:.2f} 条「显著」。"
    )
    lines.append("")
    lines.append("## 二、三种 m 口径与两种估计量")
    lines.append("")
    lines.append("| 口径 | m | 参与评分 | 只计名额 | Bonferroni 门槛 | 通过 | BH 门槛 | 通过 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for tag in ("m_all", "m_independent"):
        for est in ("t_nw3", "t_iid"):
            r = result["runs_by_estimator"][est][tag]
            bh = r["bh_p_threshold"]
            lines.append(
                f"| {esc(tag)} / {est} | {r['m_used']} | {r['n_scored']} | {r['n_counts_only']} | "
                f"{r['bonferroni_p_threshold']:.6f} | {sum(i['bonferroni'] for i in r['items'])} | "
                f"{('% .6f' % bh) if bh else '—'} | {sum(i['bh'] for i in r['items'])} |"
            )
    lines.append("")
    lines.append(
        "⚠️ **去重不能用序列路径**：共享同一份数字的条目路径不同"
        "（`phasel_out/…` 与 `phaser_out/…` 数值逐位相同），只能按数值指纹去重。"
    )
    lines.append("")
    lines.append("## 三、逐条结果（主口径：NW(3)，m = 独立检验数）")
    lines.append("")
    lines.append("| 结论 | p | 功效 | 单条显著 | Bonf | Holm | BH | 校正后 | 定稿分级 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for item in sorted(main["items"], key=lambda i: i["p"]):
        row = l3u_idx.get(item["key"], {})
        rc = row.get("recomputed") or {}
        lines.append(
            f"| `{item['key']}` | {item['p']:.6f} | {(item['power'] or 0):.2f} | "
            f"{'✓' if item['raw_significant'] else '·'} | "
            f"{'✓' if item['bonferroni'] else '·'} | {'✓' if item['holm'] else '·'} | "
            f"{'✓' if item['bh'] else '·'} | `{item['verdict']}` | "
            f"{esc(quotability.get(item['key'], '—'))} |"
        )
    lines.append("")
    lines.append("判定含义：")
    lines.append("")
    lines.append("- `SURVIVES_ALL`：三种校正全部通过，可以宣称显著。")
    lines.append("- `SURVIVES_FDR_ONLY`：只在 FDR 口径下通过，FWER 口径下不成立——引用需说明用的是 FDR。")
    lines.append("- `DOWNGRADED`：单条显著，但在同时报告 N 条的背景下不成立。")
    lines.append(
        f"- `NEITHER`：单条就不显著，且功效 < {mp.POWER_FLOOR}——**只能说无法判断，不能说无效应**。"
    )
    lines.append("- `NOT_APPLICABLE`：结论不由单一均值的显著性给出，多重性校正对它不适用。")
    lines.append("")
    lines.append("## 四、与对外定稿的交叉")
    lines.append("")
    lines.append("定稿标注为「可引用」的条目，在多重检验校正下分别落在哪一档：")
    lines.append("")
    lines.append("| 定稿分级 | 条数 | 存活 | 掉档 | 不适用 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for tier, counts in cross.items():
        lines.append(
            f"| `{tier}` | {counts['total']} | {counts['survives']} | "
            f"{counts['downgraded']} | {counts['not_applicable']} |"
        )
    lines.append("")
    lines.append(
        "**掉档**的含义是「在同时报告 N 条的背景下不足以宣称显著」，"
        "**不是**「效应不存在」——这一点与「功效不足 ≠ 无效应」是同一条纪律。"
    )
    lines.append("")
    lines.append("## 五、族内校正")
    lines.append("")
    lines.append("| 族 | m | BH 门槛 | 存活 | 掉档 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for fam, fr in sorted(result["by_family"].items()):
        surv = [i["key"] for i in fr["items"] if i["verdict"].startswith("SURVIVES")]
        down = [i["key"] for i in fr["items"] if i["verdict"] == "DOWNGRADED"]
        bh = fr["bh_p_threshold"]
        lines.append(
            f"| `{fam}` | {fr['m_used']} | {('%.6f' % bh) if bh else '—'} | "
            f"{esc('、'.join(surv) or '—')} | {esc('、'.join(down) or '—')} |"
        )
    lines.append("")
    lines.append("## 六、敏感性")
    lines.append("")
    lines.append(
        "**α 与 q 分开扫。** α 是 FWER 类（Bonferroni / Holm）的水平，q 是 BH 的目标错误发现率；"
        "把它们绑成同一个值会让下面的 B 类集合看起来「非单调」，而那只是两个参数被同时改动了。"
    )
    lines.append("")
    lines.append("### α 扫描（q 固定 = 0.05）")
    lines.append("")
    lines.append("| α | A 校正后拒绝（BH） | B 原始显著但未拒绝 | 原始显著数 |")
    lines.append("| --- | --- | --- | --- |")
    for a in ALPHA_GRID:
        s = sens["alpha"][f"{a:.2f}"]
        lines.append(
            f"| {a} | {len(s['rejected_bh'])} | {len(s['downgraded_bh'])} "
            f"（{esc('、'.join(s['downgraded_bh']) or '无')}） | {len(s['raw_significant'])} |"
        )
    lines.append("")
    lines.append(
        f"C 类（不具备单一 p 值、无法参与该校正流程）恒为 "
        f"{len(sens['alpha'][str(ALPHA_GRID[0])]['counts_only'])} 条，不随 α 变化。"
    )
    lines.append("")
    lines.append("### q 扫描（α 固定 = 0.05）")
    lines.append("")
    lines.append("| q | BH 拒绝 | BH 未拒绝 |")
    lines.append("| --- | --- | --- |")
    for q in Q_GRID:
        s = sens["q"][f"{q:.2f}"]
        lines.append(f"| {q} | {len(s['rejected_bh'])} | {len(s['not_rejected_bh'])} |")
    lines.append("")
    lines.append("### 单调性检查")
    lines.append("")
    lines.append("| 检查 | 结果 |")
    lines.append("| --- | --- |")
    for name, chk in sens["monotonicity"].items():
        lines.append(
            f"| {esc(name)} | {'✓ 满足' if chk['ok'] else '**违反**：' + esc(chk['violations'][0]['detail'])} |"
        )
    lines.append("")
    lines.append(
        "拒绝集合随水平放宽必须单调增大，这是标准校正方法的基本性质。上表是**对实现的检查**，"
        "不是对结论的判断。"
    )
    lines.append("")
    lines.append("| 其余检验 | 结果 |")
    lines.append("| --- | --- |")
    lines.append(
        f"| 实删重复条目（27→{sens['drop_duplicates']['n_rows']} 条） | "
        f"BH 通过 {sens['drop_duplicates']['bh_pass']} 条（基线 {sens['drop_duplicates']['baseline']}） |"
    )
    lines.append(
        f"| 错误地把只计名额的 7 条纳入评分 | "
        f"BH 通过 {sens['include_counts_only']['bh_pass']} 条（基线 {sens['include_counts_only']['baseline']}） |"
    )
    lines.append("")
    lines.append("## 七、预测对照")
    lines.append("")
    lines.append("| 预测 | 时机 | 内容 | 观测 | 结果 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for p in preds:
        lines.append(
            f"| {p['id']} | {p['timing']} | {esc(p['statement'])} | "
            f"{esc(json.dumps(p['observed'], ensure_ascii=False))} | "
            f"{'成立' if p['holds'] else '**被反驳**'} |"
        )
    lines.append("")
    post = [p["id"] for p in preds if p["timing"] == "post_hoc"]
    refuted = [p["id"] for p in preds if p["refuted"]]
    lines.append(
        f"⚠️ 其中 {len(post)} 条标 `post_hoc`：它们是**观测结果的复述，不是预测**——"
        "本阶段先做了探索性计算才回头写预登记，违反 R2。偏差不修饰。"
        f"其余真预测中，成立 {len(preds) - len(post) - len([p for p in preds if p['refuted'] and p['timing'] != 'post_hoc'])}"
        f"/{len(preds) - len(post)}。"
    )
    if refuted:
        lines.append("")
        lines.append(f"被反驳：{'、'.join(refuted)}。原样保留，不改判据。")
        if "P4" in refuted:
            lines.append("")
            lines.append(
                "**P4 为什么错**：我把它当成「α 只收紧校正门槛」，但 α 同时定义了"
                "「哪些条算单条显著」。收紧 α 会让进入待校正集合的条目变少，掉档反而变少——"
                "α=0.01 时掉档集合是空的。所以「掉档」不是条目本身的性质，"
                "而是「在给定 α 约定下，单条显著却没通过校正」的相对状态；"
                "引用时必须带 α 前提（见预登记 A1）。"
            )
    lines.append("")
    lines.append("## 八、闸门自检")
    lines.append("")
    if not violations:
        lines.append("G1~G5 全部通过。")
    else:
        for v in violations:
            lines.append(f"- `{v['code']}`｜{esc(v['detail'])}")
    lines.append("")
    lines.append("## 九、限制")
    lines.append("")
    lines.append(
        "- 这些检验不是预先设计的固定族，而是陆续追加的实验，"
        "**m 的界定本身就有选择空间**（27 / 24 / 分族给出不同答案）。"
    )
    lines.append(
        "- BH 对正相关稳健是理论性质，本阶段未做 Monte-Carlo 验证实际 FDR。"
    )
    lines.append(
        "- 多重检验只回答「这个非零信号是不是真的」，不回答「它有没有经济意义」。"
        "`phasel_alpha` 的 p 很小但结论是「净效应被成本吸收」，两者不矛盾。"
    )
    lines.append("- 本阶段不改对外定稿；是否据此修订定稿，是一次需要单独授权的改动。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    started = time.perf_counter()
    _check_frozen()
    quiet = "--quiet" in sys.argv

    l3u = json.loads(L3U_RESULT.read_text(encoding="utf-8"))
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    l3v = json.loads(L3V_RESULT.read_text(encoding="utf-8"))
    quotability = {c["key"]: c["quotability"] for c in cards["claims"]}
    l3u_idx = {r["key"]: r for r in l3u["rows"]}

    scored = mp.eligible(l3u["rows"])
    result = mp.assemble(l3u["rows"])

    # 两种估计量 × 两种 m
    by_est = {}
    for est in ("t_nw3", "t_iid"):
        r = mp.assemble(l3u["rows"], estimator=est)
        by_est[est] = r["runs"]
    result["runs_by_estimator"] = by_est

    # 敏感性
    # α 扫描：q 固定；q 扫描：α 固定。两者分离，避免把两个参数的耦合误读成「非单调」。
    alpha_steps = mp.alpha_sweep(
        scored, m=result["m_independent"], alphas=ALPHA_GRID, q=mp.Q, estimator="t_nw3"
    )
    q_steps = mp.q_sweep(
        scored, m=result["m_independent"], qs=Q_GRID, alpha=mp.ALPHA, estimator="t_nw3"
    )
    monotonicity = {
        "bonferroni_over_alpha": mp.monotonicity_check(alpha_steps, "rejected_bonferroni"),
        "holm_over_alpha": mp.monotonicity_check(alpha_steps, "rejected_holm"),
        "bh_over_alpha": mp.monotonicity_check(alpha_steps, "rejected_bh"),
        "bh_over_q": mp.monotonicity_check(q_steps, "rejected_bh"),
    }
    sens: dict = {
        # 键名固定两位小数：str(0.1) 得到 '0.1'、str(0.01) 得到 '0.01'，
        # 直接 str() 会让下游无法预测键名（P4 判据曾因此靠 and 短路才没炸）。
        "alpha": {f"{s['alpha']:.2f}": s for s in alpha_steps},
        "q": {f"{s['q']:.2f}": s for s in q_steps},
        "monotonicity": monotonicity,
        "drop_duplicates": {},
        "include_counts_only": {},
    }
    for s in alpha_steps:
        s["bonferroni_pass"] = len(s["rejected_bonferroni"])
        s["holm_pass"] = len(s["rejected_holm"])
        s["bh_pass"] = len(s["rejected_bh"])
    dropped = _drop_duplicate_rows(scored)
    run_drop = mp.apply_corrections(
        dropped, m=len(dropped), alpha=mp.ALPHA, q=mp.Q, estimator="t_nw3"
    )
    sens["drop_duplicates"] = {
        "n_rows": len(dropped),
        "bh_pass": sum(i["bh"] for i in run_drop["items"]),
        "baseline": sum(i["bh"] for i in result["runs"]["m_independent"]["items"]),
    }
    naive = [{**r, "_counts_only": False} for r in scored]
    run_naive = mp.apply_corrections(
        naive, m=result["m_independent"], alpha=mp.ALPHA, q=mp.Q, estimator="t_nw3"
    )
    sens["include_counts_only"] = {
        "bh_pass": sum(i["bh"] for i in run_naive["items"]),
        "baseline": sum(i["bh"] for i in result["runs"]["m_independent"]["items"]),
        "n_scored": run_naive["n_scored"],
    }
    result["sensitivity"] = sens

    # 与定稿分级交叉
    cross: dict[str, dict] = {}
    for item in result["runs"]["m_independent"]["items"]:
        tier = quotability.get(item["key"], "UNKNOWN")
        bucket = cross.setdefault(
            tier, {"total": 0, "survives": 0, "downgraded": 0, "not_applicable": 0}
        )
        bucket["total"] += 1
        if item["verdict"].startswith("SURVIVES"):
            bucket["survives"] += 1
        elif item["verdict"] == "DOWNGRADED":
            bucket["downgraded"] += 1
        elif item["verdict"] == "NOT_APPLICABLE":
            bucket["not_applicable"] += 1
    # 只计名额的条目也要计入交叉表
    for row in scored:
        if not row.get("_counts_only"):
            continue
        tier = quotability.get(row["key"], "UNKNOWN")
        bucket = cross.setdefault(
            tier, {"total": 0, "survives": 0, "downgraded": 0, "not_applicable": 0}
        )
        bucket["total"] += 1
        bucket["not_applicable"] += 1
    result["cross_with_deliverable"] = cross

    # 「原分级」从审计前的卡片备份读：卡片本身已被改写，读不回原值，
    # 而追溯要求必须留下「从哪一档降下来」。预测判据同样以这一份为基准，
    # 否则卡片一改，判据就失效了（看起来像结论变了，其实没有）。
    previous_by_key: dict[str, str | None] = {}
    if PREVIOUS_CARDS.exists():
        try:
            previous_by_key = {
                c["key"]: c.get("quotability")
                for c in json.loads(PREVIOUS_CARDS.read_text(encoding="utf-8"))["claims"]
            }
        except (json.JSONDecodeError, KeyError):  # pragma: no cover
            previous_by_key = {}

    violations = _gates(result, l3v, scored)
    preds = _check_predictions(result, sens, quotability, previous_by_key)
    result["predictions"] = preds
    result["gates"] = violations
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    result["inputs_frozen"] = {
        k: {"path": p.relative_to(ROOT).as_posix(), "sha256": s}
        for k, (p, s) in FROZEN.items()
    }

    # 降级清单：供 L3v 作为 tier_basis="l3z_audit" 的依据。
    # 只收录「单条显著但校正后未通过」的条目——这才是审计能给出的结论；
    # 其余条目的分级仍归 L3u / L3w / L3x，本轮不碰。
    patch = {
        "experiment_id": "H-L3Z-MULTIPLICITY",
        "basis": "l3z_audit",
        "prereg_sha256": PREREG_SHA,
        "main_estimator": "t_nw3",
        "m": result["m_independent"],
        "decisions": [
            {
                "key": i["key"],
                "status": "DOWNGRADED",
                "tier": "EXPLORATORY",
                "previous": previous_by_key.get(i["key"]),
                "p": i["p"],
                "raw_significant": i["raw_significant"],
                "reason": (
                    "单条 α=0.05 显著，但在把全部结论作为一组同时报告时，"
                    "Bonferroni / Holm / BH 均未拒绝；只能记为待验证的方向性观察"
                ),
            }
            for i in sorted(
                (x for x in result["runs"]["m_independent"]["items"]
                 if x["verdict"] == "DOWNGRADED"),
                key=lambda x: x["p"],
            )
        ],
    }
    (OUT_DIR / "l3z_claims_patch.json").write_text(
        json.dumps(patch, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3z_multiplicity.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md = _render(result, sens, preds, quotability, violations, l3u_idx, cross)
    (OUT_DIR / "l3z_multiplicity.md").write_text(md, encoding="utf-8", newline="\n")

    if not quiet:
        main_run = result["runs"]["m_independent"]
        print(f"独立检验        {result['m_independent']} / 登记 {result['m_all']} 条")
        print(f"期望假阳性      {result['expected_false_positives']:.2f} 条（全部零效应时）")
        print(
            f"主口径 NW(3)    单条显著 {sum(i['raw_significant'] for i in main_run['items'])}"
            f" → Bonferroni {sum(i['bonferroni'] for i in main_run['items'])}"
            f" → BH {sum(i['bh'] for i in main_run['items'])}"
        )
        down = [i["key"] for i in main_run["items"] if i["verdict"] == "DOWNGRADED"]
        print(f"掉档            {down}")
        print(f"闸门自检        {len(violations)} 处")
        for v in violations:
            print(f"  - {v['code']}: {v['detail']}")
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"预测            成立 {len(preds) - len(refuted)}/{len(preds)}，被反驳 {refuted or '无'}")
        print(f"报告            {OUT_DIR.relative_to(ROOT).as_posix()}/l3z_multiplicity.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
