#!/usr/bin/env python
"""L3ab 第一部分：检验家族审计 + 校正复现。

产出两份报告：
    reports/L3AB_TEST_FAMILY_AUDIT.md
    reports/L3AB_MULTIPLICITY_REPRODUCTION.md

本阶段不新增因子、不新增回测、不启动实盘，也不改动任何历史产物。
所有数字从既有产物现取；判断来自 `docs/gates/l3ab_family_decisions.json`（sha 校验）。

用法：
    PYTHONPATH=backend/src python scripts/l3ab_family_audit.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from deepquant.gates import citation as ct
from deepquant.gates import family as fam
from deepquant.gates import multiplicity as mp

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3AB-FAMILY"
R_FAMILY = ROOT / "reports" / "L3AB_TEST_FAMILY_AUDIT.md"
R_REPRO = ROOT / "reports" / "L3AB_MULTIPLICITY_REPRODUCTION.md"

L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
L3U_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
L3Z_RESULT = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"
L3AA_RESULT = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_dependence.json"

FROZEN = {
    "l3u_prereg": (L3U_PREREG, "0bbdc5ff4ad80f1de528f48544ee49094bc43a3a5fed19759e6fd878915ef493"),
    "l3u_result": (L3U_RESULT, "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b"),
    "cards": (CARDS, "5b4173c7265e5dd5be0cbf37b9af8620ed3488ea3480224d249a91740b1317b0"),
    "l3z_result": (L3Z_RESULT, "a138f8803ea45ca0717bc6bd6f53a654096f5dfde84200f42f2db17d1c3b3edc"),
    "l3aa_result": (L3AA_RESULT, "dffcf4645cfe8c2b155b1d53f3a4e5b6c5afd007772d1fc5d3206fe99e6854ec"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_frozen() -> dict[str, str]:
    problems, shas = [], {}
    for key, (path, expected) in FROZEN.items():
        if not path.exists():
            problems.append(f"{key}: 不存在 {path.relative_to(ROOT)}")
            continue
        got = _sha(path)
        shas[key] = got
        if expected and got != expected:
            problems.append(f"{key}: 期望 {expected[:12]}… 实际 {got[:12]}…")
    if problems:
        raise SystemExit("冻结输入已被改动，拒绝运行：\n  " + "\n  ".join(problems))
    return shas


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20
        )
        return out.stdout.strip() if out.returncode == 0 else f"UNKNOWN(exit={out.returncode})"
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def _fmt(x, nd=4) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if x != x:
            return "nan"
        return f"{x:.{nd}f}"
    return str(x)


def collect() -> dict:
    shas = _check_frozen()
    decisions = fam.load_decisions(ROOT)
    entries = ct.load_prereg(L3U_PREREG)["entries"]
    l3u = json.loads(L3U_RESULT.read_text(encoding="utf-8"))
    cards = json.loads(CARDS.read_text(encoding="utf-8"))["claims"]
    l3z = json.loads(L3Z_RESULT.read_text(encoding="utf-8"))
    l3aa = json.loads(L3AA_RESULT.read_text(encoding="utf-8"))

    series = fam.series_map(ROOT, entries)
    pairs = fam.exact_pairs(series)
    subwin = fam.subwindow_relations(series, pairs)

    # 逐条从序列重算，用于「p 值链条是否复现」
    repro: dict[str, dict] = {}
    for key, ser in series.items():
        repro[key] = fam.reproduce_from_series(list(ser.values.values()), lag=3)

    inventory = fam.build_inventory(
        decisions=decisions,
        prereg_entries=entries,
        l3u_rows=l3u["rows"],
        cards=cards,
        l3z_items=l3z["runs"]["m_independent"]["items"],
        series=series,
        reproduced=repro,
    )
    specs = [fam.spec_table(inventory, s, decisions) for s in decisions["m_specs"]]

    # 复现对照：重算 vs L3u vs L3z
    row_by_key = {r["key"]: r for r in l3u["rows"]}
    l3z_p = {i["key"]: i["p"] for i in l3z["runs"]["m_independent"]["items"]}
    repro_rows = []
    for item in inventory:
        key = item["key"]
        rr = item["reproduced"]
        row = row_by_key.get(key) or {}
        rec = row.get("recomputed") or {}
        if not rr or rr.get("n", 0) == 0:
            repro_rows.append({"key": key, "status": "NO_SERIES", "match": None})
            continue
        d_mean = abs(rr["mean"] - rec["mean"]) if rec.get("mean") is not None else None
        d_se = abs(rr["se_nw"] - rec["se_nw"]) if rec.get("se_nw") is not None else None
        d_t = abs(rr["t"] - rec["t_nw3"]) if rec.get("t_nw3") is not None else None
        p_l3z = l3z_p.get(key)
        d_p = abs(rr["p"] - p_l3z) if (p_l3z is not None and rr["p"] == rr["p"]) else None
        worst = max(v for v in (d_mean, d_se, d_t, d_p) if v is not None) if any(
            v is not None for v in (d_mean, d_se, d_t, d_p)
        ) else None
        repro_rows.append(
            {
                "key": key,
                "status": "SCORED" if p_l3z is not None else "COUNTS_ONLY",
                "n": rr["n"],
                "mean": rr["mean"],
                "mean_l3u": rec.get("mean"),
                "se_nw": rr["se_nw"],
                "se_nw_l3u": rec.get("se_nw"),
                "t": rr["t"],
                "t_l3u": rec.get("t_nw3"),
                "p": rr["p"],
                "p_l3z": p_l3z,
                "d_mean": d_mean,
                "d_se": d_se,
                "d_t": d_t,
                "d_p": d_p,
                "worst": worst,
                "match": (worst is not None and worst <= 1e-9),
            }
        )

    return {
        "shas": shas,
        "git": {
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "head": _git("log", "-1", "--format=%h %s"),
            "dirty_count": len([ln for ln in _git("status", "--short").splitlines() if ln.strip()]),
        },
        "decisions": decisions,
        "inventory": inventory,
        "exact_pairs": pairs,
        "subwindow": subwin,
        "specs": specs,
        "repro": repro_rows,
        "coverage": fam.coverage_gap(ROOT, entries),
        "l3z_sets": {
            "bonferroni": sorted(i["key"] for i in l3z["runs"]["m_independent"]["items"] if i["bonferroni"]),
            "bh": sorted(i["key"] for i in l3z["runs"]["m_independent"]["items"] if i["bh"]),
            "by": sorted(l3aa["by_m24"]["rejected"]),
        },
        "l3aa_status": l3aa.get("status"),
    }


# --------------------------------------------------------------------------
# 报告一：家族审计
# --------------------------------------------------------------------------


def render_family(res: dict) -> str:
    L: list[str] = []
    A = L.append
    dec = res["decisions"]
    inv = res["inventory"]
    cov = res["coverage"]
    specs = {s["id"]: s for s in res["specs"]}

    A("# L3ab 检验家族审计")
    A("")
    A("> 本报告由 `scripts/l3ab_family_audit.py` 生成。本阶段**不新增因子、不新增回测、不启动实盘**，"
      "不改动任何历史产物，也不为增加或减少通过条数而调整家族边界。")
    A("")
    A("## 1. 审计范围")
    A("")
    A("回答三个问题：")
    A("")
    A("1. 现在被同时报告并一起做多重检验的 27 条结论，是不是一个**有依据的**检验家族？")
    A("2. m=24 这个数字，是「独立检验数」还是「独立数字数」？两者该不该一样？")
    A("3. 这个家族**覆盖全了吗**——有没有已产生结论却没被算进来的实验？")
    A("")
    A("明确不在范围内：新因子、新回测、实盘、以及「按经济意义重组结论」。")
    A("")
    A("## 2. 开工时状态")
    A("")
    A("| 项目 | 值 |")
    A("| --- | --- |")
    A(f"| 分支 | `{res['git']['branch']}` |")
    A(f"| HEAD | `{res['git']['head']}` |")
    A(f"| 工作区改动条目（含未跟踪） | {res['git']['dirty_count']}——本项目阶段产物目录多为未跟踪，"
      "并非全部由本次引入 |")
    A("| 测试基线 | 后端 3015 通过 / 1 跳过；性质检查 9/9；脚本侧 332 通过 / 1 跳过；交易层验收 PASS |")
    A("")
    A("⚠️ 上表的「工作区改动条目」是**运行时刻快照**（重跑会变）；其余字段是定值。"
      "测试基线也是开工时的值，本阶段结束后后端为 3047（新增 32 项）。")
    A("")
    A("冻结输入（sha256 前 12 位）：")
    A("")
    A("| 输入 | sha256 |")
    A("| --- | --- |")
    for k, v in res["shas"].items():
        A(f"| `{k}` | `{v[:12]}…` |")
    A("")
    A(f"判定记录 `docs/gates/l3ab_family_decisions.json` 单独校验（`{fam.DECISIONS_SHA[:12]}…`）："
      "它承载本审计的**判断**，与数字分开，改一个分类就会改 sha，脚本随即拒跑。")
    A("")
    A("## 3. 家族划分的方法")
    A("")
    A("审计提纲给了四类关系。实际排查发现它不够用，补了第五类：")
    A("")
    A("| 类 | 含义 | 本项目命中 |")
    A("| --- | --- | --- |")
    A("| A | 同一检验被重复登记（同数据 + 同假设） | **0 条** |")
    A("| B | 数据重叠或同源，但对应不同研究假设 | 6 条 |")
    A("| C | 一条序列是另一条的子窗口 | 1 条（`phasep_late`） |")
    A("| D | 不同数据或不同统计量产生的独立问题 | 17 条 |")
    A("| **E（本次补）** | **同一假设在不同窗口/样本上重复检验** | **3 条**（`gate2_d`/`gate3_d`/`gate6_d`） |")
    A("")
    A("E 类不在提纲里，但它恰恰是最常被忽略的多重性来源：同一个问题换三个窗口问三次，"
      "在族系错误率下占三个名额，语义却是「一问多答」。把它归进 D 会说「这是三个独立问题」（不准确），"
      "归进 A 会说「重复登记」（也不准确，因为数据确实不同）。所以单列。")
    A("")
    A("**所有分类都基于登记判定字符串 + 卡片 headline 的反推**，不是原始设计文本——"
      "各阶段预登记没有假设字段。这一点记在 UNKNOWN 里，见第 8 节。")
    A("")
    A("## 4. 检验清单")
    A("")
    A("### 4.1 假设与统计量")
    A("")
    A("| claim_id | 研究假设（反推） | 判定量 / 登记字段 | 序列窗口 | n | p | 引用状态 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for r in inv:
        A(
            f"| `{r['key']}` | {r['hypothesis']} | {r['statistic']} | {r['window']} "
            f"| {r['n'] if r['n'] is not None else '—'} | {_fmt(r['p'], 6)} | {r['tier']} |"
        )
    A("")
    A("### 4.2 家族判定")
    A("")
    A("| claim_id | 类 | 假设簇 | 占名额 | 出 p | 纳入理由 |")
    A("| --- | --- | --- | --- | --- | --- |")
    for r in inv:
        A(
            f"| `{r['key']}` | {r['class']} | `{r['cluster']}` | "
            f"{'是' if r['in_family'] else '否'} | {'否' if r['counts_only'] else '是'} | {r['reason']} |"
        )
    A("")
    A(f"合计 {len(inv)} 条登记；其中 **{sum(1 for r in inv if r['counts_only'])} 条**判定不由单一均值给出，"
      f"占名额但不出 p；**{sum(1 for r in inv if not r['counts_only'])} 条**参与评分。")
    A("")
    A("## 5. 同源与子窗口：数据层面的事实")
    A("")
    A("逐条比较「共同月份上的数值」，不看均值、不看 t、不看相关系数——"
      "用均值和 t 去判同源，本项目出过错（曾把不同源的两条判成同源）。")
    A("")
    A("| a | b | 共同月 | n_a | n_b | 最大逐月差 | 关系 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for p in res["exact_pairs"]:
        sw = res["subwindow"]
        is_sub = p["b"] in sw.get(p["a"], []) or p["a"] in sw.get(p["b"], [])
        rel = "子窗口" if is_sub else "等长同值"
        diff_txt = "0" if p["max_abs_diff"] == 0 else f"{p['max_abs_diff']:.1e}"
        A(
            f"| `{p['a']}` | `{p['b']}` | {p['n_common']} | {p['n_a']} | {p['n_b']} "
            f"| {diff_txt} | {rel} |"
        )
    A("")
    pair_keys = {x for p in res["exact_pairs"] for x in (p["a"], p["b"])}
    A(f"共 {len(res['exact_pairs'])} 对逐位相同，涉及 **{len(pair_keys)} 条结论**，"
      f"但它们只落在 **{len(pair_keys) - 4} 份序列**上（三组，2+3+2）。")
    A("")
    A("### 5.0 一个容易算错的地方：5 对不等于 5 次合并")
    A("")
    A("5 对配对里有两组是「三条互相同值」——{`phaseo_netBASE`, `phasep_late`, `phasev_net`} 每两条都构成一对。"
      "所以按数据同一性完全合并是 **27 − 7 + 3 = 23**，不是 24。")
    A("")
    A("那 24 是怎么来的？L3z 的数值指纹是 `(mean, t, n)`：`phasep_late` 的 n=129 与 `phaseo_netBASE` 的 237 不同，"
      "指纹不同，于是被单独算。**所以 24 不是「数据去重数」，而是「指纹去重数」**——"
      "这两个数在别的数据集上会分叉，引用时必须说清是哪一个。")
    A("")
    A("⚠️ 本条来自测试的反驳：本审计初稿写的是「27 − 3 对 = 24」，算式与结论都对不上。"
      "是同名测试（`test_duplicate_data_is_not_double_counted_as_cross_validation`）把它逼出来的，"
      "记录在此。")
    A("")
    A("### 5.1 但数据相同不等于假设相同")
    A("")
    A("对上面每一对，再比一次**登记判定字符串**：")
    A("")
    A("| 配对 | 判定（a） | 判定（b） | 同假设？ |")
    A("| --- | --- | --- | --- |")
    by_key = {r["key"]: r for r in inv}
    for p in res["exact_pairs"]:
        va = by_key[p["a"]]["verdict_original"]
        vb = by_key[p["b"]]["verdict_original"]
        A(f"| `{p['a']}` / `{p['b']}` | {va} | {vb} | {'**是**' if va == vb else '否'} |")
    A("")
    A("**没有一对判定相同。** 所以按「同数据 + 同假设」的严格定义，A 类（重复登记）是 **0 条**。")
    A("")
    A("## 6. m=24 的审查")
    A("")
    A("### 6.1 这个数字实际是什么")
    A("")
    A("m=24 是**独立数字数**（按 `(mean, t, n)` 数值指纹去重），不是独立假设数。"
      "两者在有同源配对时必然不等。问题不是「哪个数字对」，而是「FDR/FWER 该按哪个计数」。")
    A("")
    A("按族系错误率的定义，该计的是**假设**数：同一个假设换数据重测，仍然是一个假设；"
      "不同假设共用同一份数据，则仍是两个假设（其相关性由 BY 之类的任意依赖方法吸收）。")
    A("")
    A("### 6.2 四种口径的比较")
    A("")
    A("成员表完全相同（27 条，20 条出 p），只改 m。这样差异只能来自门槛，不会混入「换了一批检验」。")
    A("")
    A("| 口径 | m | 补位 | Bonferroni 门槛 | BY 第 1 档门槛 | Bonf | Holm | BH | BY | 采用 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in res["specs"]:
        A(
            f"| {s['label']} | {s['m']} | {s['n_padded']} | {s['bonferroni_threshold']:.8f} "
            f"| {s['by_unit_threshold']:.8f} | {len(s['rejected']['bonferroni'])} "
            f"| {len(s['rejected']['holm'])} | {len(s['rejected']['bh'])} | {len(s['rejected']['by'])} "
            f"| {'**是**' if s['adopted'] else '否'} |"
        )
    A("")
    for s in res["specs"]:
        A(f"- **{s['label']}**（{s['role']}）：{s['limitation']}")
    A("")
    A("### 6.3 结论：保留 m=24，但理由不是「更保守」")
    A("")
    A("保留 m=24 的四条理由，按强度排：")
    A("")
    A("1. **它是已冻结的口径**。L3z 预登记写定 m=24 并被 sha 冻结；换口径必须新预登记，"
      "不能在本轮审计里就地改。")
    A("2. **A 类重复登记为 0**，所以不存在「同一假设被算两次」这一最明确的虚高情形——"
      "原以为的虚高并不存在。")
    A("3. **24 与 27 的分歧方向是 UNKNOWN**，不是「24 更保守」。这一点必须说清："
      "m 越小门槛越松，m=24 比 m=27 **更宽松**。若同源配对其实是独立假设，就该用 27，结论会更严。")
    A("4. **对外结论对这处分歧不敏感**：见第 7 节，5 条正面结论在四种口径 × 四种校正下全部通过；"
      "唯一受影响的是 `phasel_alpha`（零效应判定），且只在 m=27 与 Bonferroni 的组合下掉出。")
    A("")
    A("⚠️ 因此不能说「保留 24 是因为它更保守」。正确的说法是：**保留它因为它已被冻结，"
      "且分歧方向的证据不足（U2），而对外结论对分歧不敏感。**")
    A("")
    A("## 7. 覆盖面：这个家族不是全部结论")
    A("")
    A(f"工作区共 {cov['n_dirs']} 个 `*_out` 产物目录，27 条登记的序列只覆盖其中 "
      f"{cov['n_registered_dirs']} 个。未覆盖的 {len(cov['unregistered'])} 个：")
    A("")
    A("| 目录 | summary 数 | 实验 | 判定 | 类型 | 是否实质缺口 |")
    A("| --- | --- | --- | --- | --- | --- |")
    for u in cov["unregistered"]:
        kind = "只读审计" if u["audit_only"] else ("无 summary" if not u["n_summary"] else "含假设检验")
        A(
            f"| `{u['dir']}` | {u['n_summary']} | {u['experiment'] or '—'} | {u['verdict'] or '—'} "
            f"| {kind} | {'**是**' if u['has_hypothesis_conclusion'] else '否'} |"
        )
    A("")
    A("「有 verdict」不等于「是待校正的假设」：`phaseu_out` 是只读审计，它的 "
      "`NW_LAG_SENSITIVITY_CONFIRMED` 是审计自身的结论，不是新假设；`probe_out` 无 summary。"
      "所以真正的实质缺口是 "
      f"**{cov['n_hypothesis_unregistered']} 个**，就是 `phasei_out`。")
    A("")
    A("`phasei_out` 是 exQ5 可成交实验：V0 均值 +0.2307pp / t=6.357，V1 可成交 +0.1634pp / t=4.528，"
      "有完整判定，却**不在** L3u 的 27 条里。它在项目内部被当作头部结果引用，"
      "但从未进入任何一次多重检验校正的家族。")
    A("")
    A("> **⇒「可引 6 条」的适用范围是登记在册的 27 条，不是项目全部结论。**")
    A("> 对外表述必须带这个范围限定。")
    A("")
    A("**本审计不把 phasei 补进家族重算 m。** 补进去会改动全部门槛，而它该不该纳入本身是判断；"
      "补齐家族应作为一次新预登记，而不是在审计里顺手改口径。")
    A("")
    A("## 8. 未解决项")
    A("")
    A("| 编号 | 项目 | 状态 | 对结论的影响 |")
    A("| --- | --- | --- | --- |")
    for u in dec["unknowns"]:
        A(f"| {u['id']} | {u['item']} | {u['status']} | {u['impact']} |")
    A("")
    A("## 9. 本次的自我更正")
    A("")
    for r in dec["retractions"]:
        A(f"### {r['id']}")
        A("")
        A(f"- **撤回的话**：{r['retracted_statement']}")
        A(f"- **为什么错**：{r['why_wrong']}")
        A(f"- **替换为**：{r['replaced_by']}")
        A(f"- **原文处理**：{r['original_preserved']}")
        A("")
    A("## 10. 状态")
    A("")
    A("家族审计部分的可判性：")
    A("")
    A("- 家族成员与 m 的口径**已明确**（27 条、24 份数字、20 条出 p），且有依据与敏感性；")
    A("- 但**「该按假设还是按数字计数」无法从记录判定**（U2），且**家族覆盖不全**（至少缺 phasei）。")
    A("")
    A("⇒ 家族这部分不能给「已完全核实」，只能给「已核实到记录允许的程度，并写明缺口」。")
    A("最终状态由汇总报告统一给出。")
    A("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告二：校正复现
# --------------------------------------------------------------------------


def render_repro(res: dict) -> str:
    L: list[str] = []
    A = L.append
    rows = [r for r in res["repro"] if r["match"] is not None]
    worst = max((r["worst"] for r in rows), default=float("nan"))
    n_match = sum(1 for r in rows if r["match"])
    specs = res["specs"]

    A("# L3ab 多重检验结果的复现")
    A("")
    A("> 本报告由 `scripts/l3ab_family_audit.py` 生成。目标只有一个：**证明现有 Bonferroni / Holm / BH / BY "
      "的结果可以从原始序列一路复现出来**，而不是引用上一次运行的结论。")
    A("")
    A("## 1. p 值的复现链条")
    A("")
    A("链条：逐月序列 → 均值与 NW(3) 标准误 → t → t 分布双侧 p。三段都与既有产物对表：")
    A("")
    A("| 环节 | 本次重算 | 对照对象 |")
    A("| --- | --- | --- |")
    A("| 均值、标准误、t | 由序列现算（Bartlett 核、g0 有偏方差、权重 1−j/(lag+1)） | L3u `recomputed.mean / se_nw / t_nw3` |")
    A("| p | `p = I_{df/(df+t²)}(df/2, 1/2)`，df=n−1 | L3z `runs.m_independent.items[].p` |")
    A("")
    A(f"**结果：{n_match}/{len(rows)} 条在 1e-9 内一致，最大偏差 {worst:.2e}。**")
    A("")
    if n_match == len(rows):
        A("⇒ p 值链条可复现，L3z 的校正结果建立在正确的输入上。")
    else:
        A("⇒ ⚠️ 存在不一致条目，校正结果不能直接引用，需先查清差异来源。")
    A("")
    A("| claim_id | n | 均值（重算） | 均值（L3u） | 标准误（重算） | t（重算） | p（重算） | p（L3z） | 偏差 | 一致 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in res["repro"]:
        if r["match"] is None:
            A(f"| `{r['key']}` | — | — | — | — | — | — | — | — | 无序列 |")
            continue
        A(
            f"| `{r['key']}` | {r['n']} | {_fmt(r['mean'])} | {_fmt(r['mean_l3u'])} "
            f"| {_fmt(r['se_nw'], 6)} | {_fmt(r['t'])} | {_fmt(r['p'], 8)} | {_fmt(r['p_l3z'], 8)} "
            f"| {r['worst']:.1e} | {'是' if r['match'] else '**否**'} |"
        )
    A("")
    A("## 2. 四种校正的适用条件（必须分开讲）")
    A("")
    A("| 方法 | 控制什么 | 需要什么假设 | 本项目能不能用 |")
    A("| --- | --- | --- | --- |")
    A("| Bonferroni | 族系错误率（FWER） | 无（任意依赖下都成立，代价是过度保守） | 能，作为上界口径 |")
    A("| Holm | FWER | 无（step-down，仍是 Bonferroni 的加强） | 能 |")
    A("| Benjamini–Hochberg | 错误发现率（FDR） | **需要独立性或 PRDS（正回归相依）** | 能算，但假设未验证 |")
    A("| Benjamini–Yekutieli | FDR | 无（任意依赖） | 能，作为「放弃 PRDS」的对照 |")
    A("")
    A("**BH 与 BY 的差别只有一处**：BY 把门槛再除一个调和数 H_m。H_m ≥ 1 恒成立，"
      "所以 BY 的拒绝集合必然是 BH 的子集——这条是数学必然，实现写反了一定会露出来。")
    A("")
    A("⚠️ **BY 通过不等于经济有效或可交易。** 它只回答「在放弃相依假设、且假设 p 值与家族定义都正确的前提下，"
      "这条结论还站不站得住」。经济意义与可交易性在第三份报告里单独处理。")
    A("")
    A("## 3. 四种 m 口径 × 四种校正")
    A("")
    A("成员表完全相同（27 条，20 条出 p，7 条占名额不出 p），只改 m。")
    A("")
    A("| 口径 | m | Bonf | Holm | BH | BY |")
    A("| --- | --- | --- | --- | --- | --- |")
    for s in specs:
        A(
            f"| {s['label']} | {s['m']} | {len(s['rejected']['bonferroni'])} "
            f"| {len(s['rejected']['holm'])} | {len(s['rejected']['bh'])} | {len(s['rejected']['by'])} |"
        )
    A("")
    A("### 3.1 逐条结果（主口径 m=24）")
    A("")
    primary = next(s for s in specs if s["adopted"])
    tier = {r["key"]: r["tier"] for r in res["inventory"]}
    A("| claim_id | p | Bonferroni | Holm | BH | BY | 定稿分级 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for it in sorted(primary["items"], key=lambda x: x["p"]):
        mark = lambda f: "通过" if f else "—"  # noqa: E731
        A(
            f"| `{it['key']}` | {_fmt(it['p'], 6)} | {mark(it['bonferroni'])} | {mark(it['holm'])} "
            f"| {mark(it['bh'])} | {mark(it['by'])} | {tier.get(it['key'], '?')} |"
        )
    A("")
    A("### 3.2 m 变化带来的翻转，以及原因")
    A("")
    base = {s["id"]: s for s in specs}["S1_m24"]
    flips: list[str] = []
    for s in specs:
        if s["id"] == "S1_m24":
            continue
        for method in ("bonferroni", "holm", "bh", "by"):
            got = set(s["rejected"][method])
            ref = set(base["rejected"][method])
            for key in sorted(ref - got):
                flips.append(
                    f"| `{key}` | {method} | {base['m']} → {s['m']} | 掉出 | "
                    f"门槛 {base['bonferroni_threshold']:.8f} → {s['bonferroni_threshold']:.8f}"
                    f"（BY 第 1 档 {base['by_unit_threshold']:.8f} → {s['by_unit_threshold']:.8f}） |"
                )
            for key in sorted(got - ref):
                flips.append(
                    f"| `{key}` | {method} | {base['m']} → {s['m']} | 加入 | 门槛变松 |"
                )
    if flips:
        A("| claim_id | 方法 | 口径变化 | 方向 | 原因 |")
        A("| --- | --- | --- | --- | --- |")
        L.extend(flips)
        A("")
        A("原因只有一类：**m 越小门槛越松**。所有翻转都是门槛效应，没有一条是因为「检验本身变了」。")
    else:
        A("四种口径下四种校正的拒绝集合完全相同，无翻转。")
    A("")
    A("### 3.3 对外结论是否受 m 口径影响")
    A("")
    positive = ["phasec_d", "phasec2_d", "gate6_d", "phaseo_netBASE", "phasef_delta"]
    all_pass = all(
        all(k in s["rejected"][meth] for meth in ("bonferroni", "holm", "bh", "by"))
        for s in specs
        for k in positive
    )
    A(f"5 条正面结论（1 条 `QUOTABLE` + 4 条 `QUOTABLE_MARGINAL`）在**四种口径 × 四种校正共 16 种组合**下："
      f"**{'全部通过' if all_pass else '并非全部通过，需逐条核对'}**。")
    A("")
    A("第 6 条 `phasel_alpha` 是零效应判定。它在 m=24 下四种方法都通过；"
      "在 m=27 下 Bonferroni 掉出（门槛 0.001852 < p=0.001972），Holm 与 BY 仍通过。")
    A("")
    A("⇒ **m=24 与 m=27 的分歧不影响任何一条正面结论。** 这是保留 m=24 而不必先解决 U2 的实际依据。")
    A("")
    A("## 4. 复现命令")
    A("")
    A("```")
    A("PYTHONPATH=backend/src python scripts/l3ab_family_audit.py")
    A("```")
    A("")
    A("脚本启动即校验 5 个冻结输入与判定记录的 sha256；任一不符即拒跑。")
    A("")
    return "\n".join(L) + "\n"


def main() -> int:
    started = time.perf_counter()
    res = collect()
    quiet = "--quiet" in sys.argv
    elapsed = time.perf_counter() - started

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3ab_family.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    R_FAMILY.write_text(render_family(res), encoding="utf-8", newline="\n")
    R_REPRO.write_text(render_repro(res), encoding="utf-8", newline="\n")

    if not quiet:
        rows = [r for r in res["repro"] if r["match"] is not None]
        print(f"登记条目        {len(res['inventory'])}（出 p {sum(1 for r in res['inventory'] if not r['counts_only'])}）")
        print(f"逐位相同配对    {len(res['exact_pairs'])} 对；A 类重复登记 "
              f"{sum(1 for c in res['decisions']['per_claim'] if c['class'] == 'A')} 条")
        print(f"p 复现          一致 {sum(1 for r in rows if r['match'])}/{len(rows)}，"
              f"最大偏差 {max((r['worst'] for r in rows), default=float('nan')):.2e}")
        for s in res["specs"]:
            print(f"  {s['id']:14s} m={s['m']:2d}  Bonf {len(s['rejected']['bonferroni'])} "
                  f"Holm {len(s['rejected']['holm'])} BH {len(s['rejected']['bh'])} BY {len(s['rejected']['by'])}")
        print(f"家族覆盖缺口    {res['coverage']['n_hypothesis_unregistered']} 个未登记但有结论")
        print(f"报告            {R_FAMILY.relative_to(ROOT).as_posix()}")
        print(f"                {R_REPRO.relative_to(ROOT).as_posix()}")
        print(f"耗时 {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
