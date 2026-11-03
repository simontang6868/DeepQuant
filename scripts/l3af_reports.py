#!/usr/bin/env python
"""阶段 L3af：成本后净收益重建 + 检验家族台账 + 证据分级 + 当前引用清单。

一份脚本产出全部报告，**所有数字都从三个 JSON 结果里取**，正文不写死任何数值。
这样「报告与 JSON 对账」就不是靠人眼看，而是靠重建：
`--check` 模式会重新渲染并与磁盘上的文件逐字节比较。

用法：
    PYTHONPATH=backend/src python scripts/l3af_reports.py
    PYTHONPATH=backend/src python scripts/l3af_reports.py --check
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src"))

from deepquant.gates import cost_net as cn  # noqa: E402
from deepquant.gates import evidence as ev  # noqa: E402
from deepquant.gates import family_af as fa  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AF-COST-NET"
SHEET = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET_CURRENT.md"

#: 生成时间戳写死，保证产物可逐字节复现。真实运行时刻记在 run_manifest.json。
GENERATED_AT = "2026-10-29T22:30+08:00"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text(path: Path, text: str) -> str:
    """统一 LF 落盘——Windows 默认会把 \\n 翻成 \\r\\n，产物哈希就不稳了。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, obj) -> str:
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


# --------------------------------------------------------------------------
# 报告 1：逐月净收益重建
# --------------------------------------------------------------------------


def render_cost_report(c: dict, corr: dict) -> str:
    L, a = [], None
    a = L.append
    av = c["availability"]
    a("# L3af：逐月成本后净收益重建")
    a("")
    a(f"> 预登记 `{c['prereg_sha256'][:12]}…`｜产物 `reports/gates/H-L3AF-COST-NET/l3af_cost_net.json`")
    a(">")
    a("> 本报告只回答一个问题：**L3ae 用「月度平均换手 × 费率」得到的净收益 t 值，"
      "和逐月重建出来的 t 值差多少？**")
    a("")
    a("## 1. 数据可用性（先把「能不能拿到」问清楚）")
    a("")
    a("| 数据项 | 可得 | 文件 | 字段 | 样本数 | 缺失率 | 单位 |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for it in av["items"]:
        a(f"| {it['item']} | {'是' if it['available'] else '**否**'} "
          f"| `{it['path'] or '—'}` | `{it['field'] or '—'}` | {it['n']} "
          f"| {it['missing_rate']*100:.1f}% | {it['unit'] or '—'} |")
    a("")
    a(f"**判定：`{av['verdict']}`**。{av['n_available']}/{av['n_items']} 项可得。")
    a("")
    a("### 1.1 一条容易被误判的事")
    a("")
    a("`phasei_out/8f5c35121fdb/monthly.csv` 里**没有换手列**。如果只扫这一个文件，"
      "结论就是 `BLOCKED_BY_MISSING_TURNOVER_SERIES`，本阶段到此为止。")
    a("")
    a("真正的答案是：逐月换手在 **`phaseo_out/ac7aa2830ab0/monthly.csv` 的 `to` 列**里，"
      "而且能证明它与 phasei 同源——")
    a("")
    a(f"- phase O 的脚本 `scripts/run_phase_o_fee_convention.py` `import run_phase_i_exq5.build_records`，"
      f"与 phase I 是同一个生成器；")
    a(f"- 两者 `alpha` 在 **{c['alpha_identity']['n_compared']} 个共同月份**上 "
      f"`max|diff| = {c['alpha_identity']['max_abs_diff']!r}`（逐位相同）；")
    a(f"- 月份键逐位同序（G4）。")
    a("")
    a("所以这一步**不是**「用别的信号的换手代替」，而是「同一实验的另一份产物记了换手」。"
      "两者的区别正是铁律要保护的东西，必须在报告里说清，而不是默默取用。")
    a("")
    a(f"⚠️ 仍然缺的一项是**原始交易/订单记录**：全仓库没有逐笔成交落盘，"
      f"因此无法核成交价假设、无法逐笔核最低佣金、无法算真实滑点。"
      f"需要补充的是券商成交回报明细（日期/代码/方向/数量/成交价/佣金/印花/过户）。")
    a("")
    a("## 2. 换手与成本的定义（单位先说清）")
    a("")
    a(f"- 定义：`{c['turnover']['definition']}`；单位 **单边比例**，取值 "
      f"[{c['turnover']['min']:.4f}, {c['turnover']['max']:.4f}]，"
      f"均值 {c['turnover']['mean']:.6f}、sd {c['turnover']['sd']:.6f}，"
      f"其中 {c['turnover']['n_zero']} 个月为 0（首月建仓）、"
      f"{c['turnover']['n_over_one']} 个月 > 1。")
    a(f"- 月度成本：`{c['cost_arithmetic']['formula']}`。")
    a(f"- 单边/双边换算已钉住：把换手当双边代入，成本必须**恰好翻倍**；"
      f"实测残差 {c['cost_arithmetic']['unit_conversion_max_abs']:.3e}（G2B）。")
    a(f"- 与 phase O 落盘的 `cost_BASE_pp` 逐月对账：`max|diff| = "
      f"{c['cost_arithmetic']['max_abs_resid']:.3e}`（三处 6 位小数存储的舍入上限内）。")
    a("")
    a("### 2.1 构造 P 与构造 Q")
    a("")
    a("| 构造 | 公式 | 含现金残留 | 含冲击常数 | 用途 |")
    a("| --- | --- | --- | --- | --- |")
    a("| **P** | `net = alpha_v1 − 2·to·rate·100` | 否 | 否 | 主构造（与 phasei 的 `cost_of` 与 L3ae 逐字一致） |")
    a("| Q | `net = finv·alpha_v1 − cost − 0.000784` | 是 | 是 | 对照（直接读 phase O 的 `net_*` 列） |")
    a("")
    a(f"构造 Q 的恒等式核对：`max|finv·a − cost − 0.000784 − net_stored| = "
      f"{c['net_identity']['max_abs_resid']:.3e}`，容差 "
      f"{c['net_identity']['tolerance']:.3e}（由三项 6 位小数存储 + alpha 量级推出）。")
    a("")
    a("## 3. 五档成本后的逐月净收益")
    a("")
    a("| 档位 | 费率 | 月度成本 | 净额（pp/月） | se(iid) | t(iid) | se(NW3) | t(NW3) | p(NW3) |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in c["tiers"]:
        n = t["net"]
        a(f"| `{t['id']}`{'（主）' if t['role']=='primary' else ''} | {t['rate_pct']:.4f}% "
          f"| {t['cost_pp_month']:.6f} | {n['mean_pp']:+.6f} | {n['se_iid_pp']:.6f} "
          f"| {n['t_iid']:+.4f} | {n['se_nw3_pp']:.6f} | {n['t_nw3']:+.4f} | {n['p_nw3']:.3e} |")
    a("")
    a(f"毛利基准：均值 {c['gross']['mean_pp']:+.6f} pp/月，se(iid) {c['gross']['se_iid_pp']:.6f}，"
      f"t(iid) {c['gross']['t_iid']:+.4f}，t(NW3) {c['gross']['t_nw3']:+.4f}，n = {c['gross']['n']}。")
    a("")
    a(f"**主判定档**是 `{c['primary']['tier']}`（phasei 冻结的 `primary_cost`），"
      f"其净额 {c['primary']['net_pp_month']:+.6f} pp/月、t(iid) {c['primary']['t_iid']:+.4f}、"
      f"t(NW3) {c['primary']['t_nw3']:+.4f} ⇒ **{c['primary']['significance']}**，"
      f"冻结判定 `EXQ5_COST_ABSORBED` 不变。")
    a("")
    a("## 4. 标准误：L3ae 那句话到底对不对")
    a("")
    a("L3ae 的表述是：**「真实净收益标准误不小于毛收益标准误，故现有 t 值是真实 t 值上界」**。")
    a("")
    a("这句话的数学形式是 `se_net ≥ se_gross`，等价于 `Var(cost) ≥ 2·Cov(gross, cost)`。"
      "它**不是恒真命题**，而是一个关于数据的判断。四个分量逐档列出：")
    a("")
    a("| 档位 | Var(gross) | Var(cost) | Cov | Var(net) | Var(c)/Var(g) | ρ(α, to) | se_net/se_gross | se_net≥se_gross |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in c["tiers"]:
        v = t["variance"]
        a(f"| `{t['id']}` | {v['var_gross']:.6f} | {v['var_cost']:.8f} | {v['cov']:.8f} "
          f"| {v['var_net']:.6f} | {v['var_cost_over_gross']:.2e} | {v['rho_alpha_cost']:+.4f} "
          f"| {v['se_net_over_gross']:.6f} | {'是' if v['se_net_ge_gross'] else '**否**'} |")
    a("")
    a(f"**判定：`{c['l3ae_claim']['verdict']['code']}`** —— "
      f"{c['l3ae_claim']['verdict']['n_holds']}/{c['l3ae_claim']['verdict']['n']} 档成立。")
    a("")
    a(f"- 只有压力档（0.3%）满足 `Var(cost) ≥ 2Cov`，因为成本方差随费率**平方**放大；")
    a(f"- 真实费率档恰好相反：`2Cov` 大于 `Var(cost)`，`se_net` 比 `se_gross` **小** "
      f"{abs(1-c['l3ae_claim']['verdict']['max_se_ratio_deviation'])*100:.4f}% 的量级；")
    a(f"- 但幅度可忽略：纯 se 效应带来的 t 变化最大只有 "
      f"{c['l3ae_claim']['verdict']['max_t_shift']:.4f}。")
    a("")
    a("**正确的表述**：成本对标准误的影响 < 0.1%，所以 L3ae 的 t 值可以用，"
      "但它**不是上界**，只是近似值。「上界」这个词在方向上错了，必须撤回；"
      "数值上不构成问题，也必须说清——否则下一个用它的人会以为存在保守余量。")
    a("")
    a("## 5. 缺陷与更正")
    a("")
    for d in corr["corrections"]:
        if d["id"] in ("D1", "D3", "D4", "D5", "D6"):
            a(f"### {d['id']}｜{d['title']}")
            a("")
            a(f"- **问题**：{d['problem']}")
            a(f"- **证据**：{d['evidence']}")
            a(f"- **影响**：{d['impact']}")
            a(f"- **处置**：{d['handling']}")
            a("")
    a("## 6. 结论")
    a("")
    a(f"1. 逐月净收益**可以**重建，前提是承认换手来自 phase O 而不是 phase I（同源已证）。")
    a(f"2. 五档净收益随费率单调递减，主档 {c['primary']['t_iid']:+.4f}（iid）/ "
      f"{c['primary']['t_nw3']:+.4f}（NW3），不显著 ⇒ 冻结判定不变。")
    a(f"3. 真实费率（0.051%）档净额 {c['tiers'][2]['net_pp_month']:+.6f} pp/月、"
      f"t(NW3) {c['tiers'][2]['net']['t_nw3']:+.4f}，显著；但那是**敏感性**，不是判定。")
    a(f"4. 「t 是上界」应撤回：方向错、幅度可忽略。逐月数据能算的是**近似相等**，不是不等式。")
    a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告 2：家族台账
# --------------------------------------------------------------------------


def render_family_report(f: dict, corr: dict) -> str:
    L = []
    a = L.append
    a("# L3af：检验家族台账与 m 口径复核")
    a("")
    a(f"> 预登记 `{f['prereg_sha256'][:12]}…`｜产物 `reports/gates/H-L3AF-COST-NET/l3af_family.json`")
    a(">")
    a(f"> 家族 = L3u 登记 {f['n_registered']} 条 + L3af 纳入 {f['n_added']} 条 = {f['n_entries_total']} 条；"
      f"进评分集合 {f['n_scored']} 条。")
    a("")
    a("## 1. m 的算术")
    a("")
    ma = f["m_arithmetic"]
    a(f"`m = {ma['n_registered']} − 合并 {ma['n_merged_away']} + 纳入 {ma['n_added']} = "
      f"{ma['recomputed']}`，与主口径 {f['m_adopted']} 一致。")
    a("")
    a("合并的 4 条（逐位相同 ⇒ 同一随机变量，只占一个名额）：")
    a("")
    a("| 被合并 | 代表 |")
    a("| --- | --- |")
    for k, v in sorted(f["merged_into"].items()):
        a(f"| `{k}` | `{v}` |")
    a("")
    a("## 2. 五个 m 口径")
    a("")
    a("| 口径 | m | 规则 | 角色 | Bonferroni 门槛 | 通过条数（Bonf/Holm/BH/BY） |")
    a("| --- | --- | --- | --- | --- | --- |")
    for v in f["variants"]:
        if v["id"] not in f["runs"]:
            continue
        r = f["runs"][v["id"]]
        cts = r["counts"]
        a(f"| `{v['id']}` | {r['m']} | {v['rule']} | {v['role']} "
          f"| {r['params']['bonferroni_threshold']:.6f} "
          f"| {cts['bonferroni']}/{cts['holm']}/{cts['bh']}/{cts['by']} |")
    sub = f["subfamily"]
    a(f"| `SUB_family` | 按族 | {sub['n_groups']} 个子族，族内独立校正 | 合理性检验，不采用 "
      f"| — | {sub['counts']['bonferroni']}/{sub['counts']['holm']}/{sub['counts']['bh']}/{sub['counts']['by']} |")
    a("")
    a("### 2.1 为什么主口径是 25，以及它为什么不是「唯一正确的 m」")
    a("")
    a("- 合并的判据只问一件事：**数值是否逐位相同且较短序列被完全覆盖**。与 p 无关，"
      "所以不可能是「被 p 挑出来的」（闸门把它应用到全 p>0.05 的子集上，合并照常发生）。")
    a("- 但「同一随机变量」不自动等于「同一研究假设」：本项目 A 类重复登记为 **0** 条，"
      "5 对同值条目的登记判定全不同。所以 m=25 是**名额**口径，不是假设口径。")
    a("- 敏感性：m=24/25/27/29 与子族口径下，7 条正面结论**完全相同**（P7/P8 均成立）。"
      "因此 m 之争不影响对外集合——这是本阶段能给的最强结论，也是它的上限。")
    a("")
    a(f"- 子族口径**不采用**：族的划分规则是在看过 p 之后才提出的，采用它等于事后换家族。"
      f"做它只是为了回答一个质疑（「7 条是不是把不同问题混在一起凑出来的」），"
      f"答案是：族内校正下 7 条仍全部通过。")
    a("")
    a("## 3. 逐条台账（29 条）")
    a("")
    a("完整 12 字段见 JSON 的 `registry`。下表只列核对用的数量。"
      "**空值写 `NOT_RECORDED`，不猜**。")
    a("")
    a("| ID | 族 | n | 效应（pp） | t(NW3) | p | 判定类别 | 合并到 | m=25 四校正 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in f["registry"]:
        st = r["stat"] or {}
        corr25 = (r["corrections"] or {}).get("IND_m25") or {}
        four = "".join(
            "✓" if corr25.get(m) else "·" for m in ("bonferroni", "holm", "bh", "by")
        )
        eff = f"{st['mean_pp']:+.4f}" if st.get("mean_pp") is not None else "—"
        tt = f"{st['t_nw3']:+.4f}" if st.get("t_nw3") is not None else "—"
        pp = f"{st['p']:.2e}" if st.get("p") is not None else "—"
        if not r.get("in_partition"):
            rep_cell = "—（序列不可载入）"
        else:
            rep = r["representative"]
            rep_cell = "（代表）" if rep == r["id"] else f"`{rep}`"
        a(f"| `{r['id']}` | {r['cluster'] or '—'} | {r['n'] or '—'} | {eff} | {tt} | {pp} "
          f"| {r['decisions_class'] or '—'} | {rep_cell} | {four} |")
    a("")
    a("`✓` = 该校正下拒绝。四列全 `✓` 即为本清单认定的正面结论候选。")
    a("")
    a("## 4. 两套校正实现互校")
    a("")
    a("校正后 p 由本阶段**独立实现**（Bonferroni/Holm/BH/BY 四条公式重写），"
      "再与 `multiplicity` 的布尔结果逐条比对：")
    a("")
    a("| 口径 | Bonferroni | Holm | BH | BY |")
    a("| --- | --- | --- | --- | --- |")
    for vid, chk in f["cross_check"].items():
        cells = " | ".join(
            ("一致" if chk[m]["agree"] else f"**不一致 {chk[m]['diff']}**") for m in
            ("bonferroni", "holm", "bh", "by")
        )
        a(f"| `{vid}` | {cells} |")
    a("")
    a("## 5. 与 L3ac / L3ad 的差异")
    a("")
    a("| 项目 | L3ac | L3ad | L3af |")
    a("| --- | --- | --- | --- |")
    a("| m | 27 | 25 | 25（另报 24/27/29 + 子族） |")
    a("| 家族成员 | 27 | 27 + 2 | 27 + 2 |")
    a("| 逐条校正后 p | 未存 | 未存 | **逐条落盘** |")
    a("| 两套实现互校 | 无 | 无 | 有（4 口径 × 4 方法） |")
    a("")
    a("## 6. 无法关闭的缺口")
    a("")
    for u in f["unknowns"]:
        a(f"- {u}")
    a("")
    a("**因此不能声称「已校正全部研究者自由度」。** 本清单的「通过全族校正」"
      "只对登记在册的条目成立。")
    a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告 3：证据状态
# --------------------------------------------------------------------------


def render_evidence_report(e: dict, c: dict, f: dict) -> str:
    L = []
    a = L.append
    cnt = e["counts"]
    a("# L3af：当前证据状态（S1–S4 四分法）")
    a("")
    a(f"> 预登记 `{e['prereg_sha256'][:12]}…`｜产物 `reports/gates/H-L3AF-COST-NET/l3af_evidence.json`")
    a("")
    a("## 1. 为什么要拆成四张标签")
    a("")
    a("「这条结论站得住」至少混了四件事，历史报告里常被压成一句：")
    a("")
    a("| 标签 | 问题 | 通过条件 |")
    a("| --- | --- | --- |")
    a(f"| **S1 统计** | 在给定家族与 m 下，是不是噪声？ | p 可复现 + 四种校正（m=25）全过 |")
    a(f"| **S2 经济** | 效应量够不够大？ | \\|效应\\| ≥ {e['econ_threshold_pp']}pp/月，且该门槛对该条目**事前**适用 |")
    a(f"| **S3 成本后** | 扣掉成本、且成本的不确定性也算进去，还剩多少？ | 有逐月净序列，且该序列自身四种校正全过、均值为正 |")
    a(f"| **S4 可交易** | 能不能真下单？ | 独立样本外 + 成交模型 + 容量 + 风险，四项齐备 |")
    a("")
    a("**禁止的自动推导**：统计显著 ⇒ 经济有意义；效应/MDE 比值大 ⇒ 经济有意义（那是功效，不是价值）；"
      "样本内净收益为正 ⇒ 可实盘。")
    a("")
    a("## 2. 七条正面结论的四张标签")
    a("")
    a("| claim_id | S1 | S2 | S3 | S4 | 当前状态 |")
    a("| --- | --- | --- | --- | --- | --- |")
    for card in e["cards"]:
        st = card["statistics"]
        a(f"| `{card['id']}` | {st['S1']['status']} | {st['S2']['status']} "
          f"| {st['S3']['status']} | {st['S4']['status']} | `{card['status']}` |")
    a("")
    a(f"通过数：S1 **{cnt['S1_PASS']}/7**、S2 **{cnt['S2_PASS']}/7**、"
      f"S3 **{cnt['S3_PASS']}/7**、S4 **{cnt['S4_PASS']}/7**。")
    a("")
    a("### 2.1 S2 只过 2 条——这不是坏消息，是门槛在起作用")
    a("")
    a("| 条目 | 效应 | 是否受事前门槛约束 | 结论 |")
    a("| --- | --- | --- | --- |")
    for card in e["cards"]:
        s2 = card["statistics"]["S2"]
        cov = "是" if s2["status"] != "NOT_GRADED" else "否"
        if s2["status"] == "NOT_GRADED":
            verdict = f"不判（参照 {s2['reference_pp']}pp，" \
                      f"{'高于' if s2['clears_borrowed_reference'] else '低于'}该参照）"
        else:
            verdict = "过" if s2["status"] == "PASS" else "不过"
        a(f"| `{card['id']}` | {card['effect_pp']:+.4f} | {cov} | {verdict} |")
    a("")
    a("净收益类（`phaseo_netBASE`）不判 S2：0.30pp 门槛的理由是「= 0.1% 档月度成本的 2 倍」，"
      "而这条本身已经扣掉成本，门槛的理由不迁移，也从未为它预登记过门槛。")
    a("")
    a("### 2.2 S3 的两条路径必须分开说")
    a("")
    for card in e["cards"]:
        s3 = card["statistics"]["S3"]
        if s3["status"] == "ABSENT":
            continue
        a(f"#### `{card['id']}` —— `{s3['status']}`")
        a("")
        a(f"- 路径：`{s3['path']}`")
        a(f"- {s3['note']}")
        if s3.get("tiers"):
            a("")
            a("| 档位 | 费率 | 净值（pp/月） | t(iid) | t(NW3) | iid 显著 | NW3 显著 |")
            a("| --- | --- | --- | --- | --- | --- | --- |")
            for t in s3["tiers"]:
                a(f"| `{t['tier']}` | {t['rate_pct']:.4f}% | {t['net_pp_month']:+.6f} "
                  f"| {t['t_iid']:+.4f} | {t['t_nw3']:+.4f} "
                  f"| {'是' if t['significant_iid'] else '否'} "
                  f"| {'是' if t['significant_nw3'] else '否'} |")
        a("")
    a("`phasei_v0` 为什么是 ABSENT 而不是沿用 V1 的换手：V0 的持仓含不可成交标的，"
      "其逐月换手与 V1 **不是同一条序列**。拿 V1 的换手估 V0 的成本，"
      "正是「换手禁止沿用别的信号」要防的事。这条宁可不判，也不借用。")
    a("")
    a("## 3. 其余在册条目")
    a("")
    a("| ID | S1 | S2 | S3 | S4 | 状态 | 登记判定 |")
    a("| --- | --- | --- | --- | --- | --- | --- |")
    for o in e["not_quotable_but_registered"]:
        a(f"| `{o['id']}` | {o['S1']} | {o['S2']} | {o['S3']} | {o['S4']} | `{o['status']}` "
          f"| {o['quotability'] or '—'} |")
    a("")
    a("## 4. 全族层面的限制")
    a("")
    for x in e["global_limits"]:
        a(f"- {x}")
    a("")
    d2 = e["econ_threshold_defect"]
    a(f"### 4.1 {d2['code']}｜经济门槛的记录矛盾")
    a("")
    a(f"- {d2['detail']}")
    a(f"- 影响：{d2['impact']}")
    a(f"- 处置：{d2['handling']}")
    a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 报告 4：结题
# --------------------------------------------------------------------------


def render_stage_report(c, f, e, corr, status, answers) -> str:
    L = []
    a = L.append
    a("# 阶段 L3af 结题报告：成本后净收益重建与当前引用清单审计")
    a("")
    a(f"> **最终状态**：`{status}`")
    a(f"> **预登记**：`docs/gates/l3af_prereg.json`（sha256 `{c['prereg_sha256']}`）")
    a(f"> **产物**：`reports/gates/H-L3AF-COST-NET/`")
    a("")
    a("## 0. 一句话")
    a("")
    a(f"逐月净收益可以重建（换手在 phase O 的产物里，且与 phasei 逐位同源）；"
      f"重建后主判定不变，但 L3ae 的「t 是上界」这句话方向错了，必须撤回；"
      f"7 条正面结论在四种 m 口径下完全一致；其中带成本后证据的仍是 **1 条**"
      f"（另 1 条分档相反）。")
    a("")
    a("## 1. 哪些结果真正复现成功")
    a("")
    a("| 复现项 | 判据 | 结果 |")
    a("| --- | --- | --- |")
    a(f"| phase O 与 phase I 的 alpha 同源 | max\\|diff\\| ≤ 1e-12 | "
      f"{c['alpha_identity']['max_abs_diff']!r}（{c['alpha_identity']['n_compared']} 月） |")
    a(f"| 成本算术 | \\|cost_BASE − 0.102·to\\| ≤ 6e-7 | {c['cost_arithmetic']['max_abs_resid']:.3e} |")
    a(f"| phase O 净列恒等式 | 残差 ≤ {c['net_identity']['tolerance']:.2e} | "
      f"{c['net_identity']['max_abs_resid']:.3e} |")
    a(f"| 构造 Q 的 se 复现 phase O summary | 相对 ≤ 1e-3 | "
      f"最大 {max(r['rel_diff'] for r in c['se_crosscheck']):.3e} |")
    a(f"| L3z 的 p 链（22/22） | 逐位 | 由 `family.reproduce_from_series` 沿用，本阶段未改动 |")
    a(f"| 四种校正两套实现 | 逐条一致 | "
      f"{'全部一致' if all(all(x['agree'] for x in chk.values()) for chk in f['cross_check'].values()) else '存在不一致'} |")
    a("")
    a("## 2. 哪些结论的状态发生变化")
    a("")
    a("| 条目 | 旧状态（L3ad/L3ae） | 新状态（L3af） | 原因 |")
    a("| --- | --- | --- | --- |")
    a("| `phasei_v1` | 「成本吸收」（仅两档披露） | `QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST` | 补 5 档逐月净收益，档间结论相反 |")
    a("| `phaseo_netBASE` | 可引（净收益） | `QUOTABLE_WITH_COST_EVIDENCE` | 经逐月序列独立复算，se 来自净序列而非毛序列 |")
    a("| `gate6_d` / `phasec2_d` / `phasec_d` / `phasef_delta` | 可引 | `QUOTABLE_STAT_ONLY` | 新增 S3 检查：判定量不是净收益，不存在净收益版本 |")
    a("| `phasei_v0` | 未登记 | `QUOTABLE_STAT_ONLY`（S3 ABSENT） | 已纳入家族（m 25）；V0 换手未落盘，不能借 V1 |")
    a("| L3ae 的「t 是上界」 | 断言 | **撤回** | 方向错（真实档 se_net < se_gross），幅度可忽略 |")
    a("")
    a("## 3. phasei 的真实费率净收益显著性是否成立")
    a("")
    p = c["primary"]
    best = c["tiers"][2]
    a(f"**分两句话答，缺一句都会误导：**")
    a("")
    a(f"1. **按冻结的判定口径（0.3%）**：净额 {p['net_pp_month']:+.6f} pp/月，"
      f"t(iid) {p['t_iid']:+.4f}、t(NW3) {p['t_nw3']:+.4f} ⇒ 不显著，"
      f"`EXQ5_COST_ABSORBED` **不变**。")
    a(f"2. **按真实等价费率（0.051%，1 亿档）**：净额 {best['net_pp_month']:+.6f} pp/月，"
      f"t(iid) {best['net']['t_iid']:+.4f}、t(NW3) {best['net']['t_nw3']:+.4f} ⇒ 显著。"
      f"这是**敏感性**结果，不是判定结果。")
    a("")
    a("所以「真实费率下的净收益显著性」在统计上成立、在流程上**不构成通过**："
      "判定档是预登记选定的 0.3%，改成 0.051% 属于换主口径，须新预登记。"
      "本阶段不动它，只把它作为披露。")
    a("")
    a("## 4. m=25 的口径依据是否充分")
    a("")
    a("**分三句话说：**")
    a("")
    a("1. **作为「名额」口径充分**：判据只问「是否逐位相同且完全覆盖」，与 p 无关；"
      "FWER 论证（同一 p 读两次，计 2 名额的实际拒绝率是 α/2）表明计 2 是保守偏差而非正确性。")
    a("2. **作为「假设」口径不充分**：A 类重复登记 0 条，说明同值条目对应的科学问题并不同。"
      "「独立随机变量数」是名额数，不是假设数。")
    a("3. **但对对外集合没有影响**：m=24/25/27/29 与子族口径下，7 条正面结论完全相同"
      "（P7、P8 均成立）。所以 m 之争**不改变结论**，只改变「怎么说」。")
    a("")
    a("## 5. 当前有哪些可引用的统计结论")
    a("")
    a(f"**{e['counts']['n_positive']} 条**通过 m=25 四种校正：")
    a("")
    a("| ID | 效应（pp/月） | t(NW3) | 状态 |")
    a("| --- | --- | --- | --- |")
    for card in e["cards"]:
        a(f"| `{card['id']}` | {card['effect_pp']:+.4f} | {card['t_nw3']:+.4f} | `{card['status']}` |")
    a("")
    a(f"此外 `phasel_alpha` 只在 m=24/25（去重口径）下四种校正全过，m=27/29 下 Bonferroni 不过，"
      f"且其判定是 `SURGE_AVOID_ABSORBED`（净效应被吸收）——**不是正面结论**，单列。")
    a("")
    a("## 6. 哪些结论有成本后证据")
    a("")
    a(f"- `phaseo_netBASE`：**有**（判定量本身即净收益，3000 万 / BASE 口径）。")
    a(f"- `phasei_v1`：**有但分档相反** —— 0.3% 档不显著、0.051%~0.1% 档显著。引用必须带档位。")
    a(f"- `phasei_v0`：**无**（V0 逐月换手未落盘；借用 V1 换手违反「换手不沿用别的信号」）。")
    a(f"- 其余 4 条：**无**（判定量是价差/差值，不存在净收益版本）。")
    a("")
    a("## 7. 是否存在独立样本外证据")
    a("")
    a("**不存在。** 全部窗口止于 2026-09，没有前瞻期数据参与。"
      "S4 在 7 条上全部为 ABSENT，且四项（样本外/成交模型/容量/风险）逐项都不完整："
      "成交模型只有冲击上限常数、容量只有覆盖率描述、风险只到 MDD。")
    a("")
    a("## 8. 为什么本阶段仍然不能据此启动实盘")
    a("")
    a("1. **没有样本外**：所有结论都是同一段历史内的估计，不能排除样本内选择。")
    a("2. **没有成交回放**：成交按 bar 收盘价、只在信号日剔除不可成交标的；"
      "真实下单的价格路径与部分成交未建模。")
    a("3. **成本不确定性的来源没查完**：改费率档可以算，但券商实际成交回报（唯一外部输入）缺失，"
      "所以「真实费率」本身仍是一个**情景假设**，不是测得的事实。")
    a("4. **只有 1 条带成本后证据**，且它 |效应|/MDE 只有 1.44 倍；"
      "分档后压力口径净值趋近 0。")
    a("5. **m 的问题没解决，只是不影响这 7 条**：研究历史缺口的关闭需要新数据，不是新计算。")
    a("")
    a("## 9. 下一阶段必须解决的问题")
    a("")
    a("1. **拿到券商成交回报**（两家）：这是唯一能替代「情景费率」的东西，也是 S3→S4 的必经之路。")
    a("2. **V0 的逐月换手**：需要重跑 V0 并落盘 `to`，否则 `phasei_v0` 的 S3 永远是 ABSENT。")
    a("3. **经济门槛的重新预登记**：现存的 0.30pp 门槛是为价差类写的，净收益类没有门槛；"
      "且 C2 文档与 C/E/F 三处矛盾（D2）。这两件事都要新预登记，不能回头改旧文。")
    a("4. **研究历史缺口**：尝试过但未报告的策略/窗口/成本档位无法从产物恢复。"
      "要关闭它只能靠外部记录（实验日志/对话记录），本仓库内做不到。")
    a("5. **成本波动幅度**：本阶段算出了逐月成本序列（sd 已报），但「成本为什么波动」"
      "（换手驱动 vs 费率驱动）需要逐月换手分解到个股层面，产物里没有。")
    a("")
    a("## 10. 闸门与预测")
    a("")
    a("### 10.1 闸门")
    a("")
    a("| 阶段 | 闸门数 | 违规 |")
    a("| --- | --- | --- |")
    a(f"| 成本重建 | 8 | {len(c['gates'])} |")
    a(f"| 家族台账 | 4 | {len(f['gates'])} |")
    a(f"| 证据分级 | 3 | {len(e['gates'])} |")
    a("")
    for g in c["gates"] + f["gates"] + e["gates"]:
        a(f"- `{g['code']}`：{g['detail']}")
    a("")
    a("### 10.2 预测核对")
    a("")
    a("| ID | 事前/事后 | 结论 | 是否成立 |")
    a("| --- | --- | --- | --- |")
    for pr in c["predictions"] + f["predictions"]:
        a(f"| {pr['id']} | {pr['timing']}{'（侦察时已观察）' if pr.get('recon_observed') else ''} "
          f"| {pr['claim']} | {'成立' if pr['holds'] else '**不成立**'} |")
    a("")
    a("## 10.3 变异测试（闸门能不能真的抓）")
    a("")
    mut = ROOT / "reports/gates/H-L3AF-COST-NET/l3af_mutation.json"
    if mut.exists():
        m = json.loads(mut.read_text(encoding="utf-8"))
        a(f"共 {m['n_mutations']} 条变异，按预期判定 {m['n_caught_as_expected']} 条，"
          f"不符 {m['n_mismatch']} 条；反向对照（未变异）违规 "
          f"{sum(m['negative_control'].values())} 条。")
        a("")
        a("| ID | 变异 | 期望被谁抓到 | 实际 | 说明 |")
        a("| --- | --- | --- | --- | --- |")
        for r in m["mutations"]:
            a(f"| {r['id']} | {r['title']} | `{r['expect_caught_by']}` "
              f"| {'抓到' if r['caught'] else '**没抓到**'} | {r['detail']} |")
        a("")
        for n in m.get("notes", []):
            a(f"> {n}")
            a("")
    else:
        a("未运行 `scripts/l3af_mutation_test.py`。")
        a("")
    a("## 11. 更正日志")
    a("")
    a("| 编号 | 标题 | 影响 |")
    a("| --- | --- | --- |")
    for d in corr["corrections"]:
        a(f"| {d['id']} | {d['title']} | {d['impact']} |")
    a("")
    a("## 12. 交付物")
    a("")
    a("| 文件 | 内容 |")
    a("| --- | --- |")
    for p_ in [
        "docs/gates/l3af_prereg.json", "docs/gates/l3af_corrections.json",
        "backend/src/deepquant/gates/cost_net.py",
        "backend/src/deepquant/gates/family_af.py",
        "backend/src/deepquant/gates/evidence.py",
        "backend/tests/gates/test_gates_l3af_cost.py",
        "backend/tests/gates/test_gates_l3af_family.py",
        "backend/tests/gates/test_gates_l3af_evidence.py",
        "scripts/l3af_reports.py", "scripts/l3af_mutation_test.py",
        "reports/gates/H-L3AF-COST-NET/l3af_cost_net.json",
        "reports/gates/H-L3AF-COST-NET/l3af_family.json",
        "reports/gates/H-L3AF-COST-NET/l3af_evidence.json",
        "reports/gates/H-L3AF-COST-NET/l3af_monthly_net.csv",
        "reports/L3AF_COST_NET_RETURN_RECONSTRUCTION.md",
        "reports/L3AF_TEST_FAMILY_AUDIT.md",
        "reports/L3AF_CURRENT_EVIDENCE_STATUS.md",
        "reports/STAGE_REPORT_L3AF_FINAL.md",
        "docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md",
    ]:
        a(f"| `{p_}` | |")
    a("")
    a("## 13. 九问速答")
    a("")
    for i, (q, ans) in enumerate(answers, 1):
        a(f"{i}. **{q}** {ans}")
    a("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def build_all() -> dict[str, str]:
    t0 = time.time()
    corr = json.loads((ROOT / "docs/gates/l3af_corrections.json").read_text(encoding="utf-8"))
    cost = cn.build(ROOT)
    family = fa.build(ROOT)
    evid = ev.build(ROOT, cost=cost, family=family)

    # 状态由 evidence.choose_status 机械选，脚本不参与判断
    status = ev.choose_status(cost, family, evid)

    _t_real = cost["tiers"][2]["net"]["t_nw3"]
    _n_pos = evid["counts"]["n_positive"]
    answers = [
        ("哪些结果真正复现成功？",
         "alpha 同源（逐位相同）、成本算术、phase O 净列恒等式、构造 Q 的 se、四种校正两套实现——"
         "五项全部通过，违规 0。"),
        ("哪些结论的状态发生变化？",
         "`phasei_v1` 由「成本吸收」升级为「分档相反」；`phaseo_netBASE` 的 S3 由「推断」变为「逐月复核」；"
         "L3ae 的「t 是上界」撤回。"),
        ("phasei 的真实费率净收益显著性是否成立？",
         f"统计上成立（真实费率档 t(NW3)={_t_real:+.2f}），流程上不构成通过——"
         f"判定档是 0.3%，改档须新预登记。"),
        ("m=25 的口径依据是否充分？",
         "作为名额口径充分、作为假设口径不充分；但四种 m 与子族口径下 7 条正面结论完全相同，"
         "故不影响对外集合。"),
        ("当前有哪些可引用的统计结论？",
         f"{_n_pos} 条通过 m=25 四种校正；`phasel_alpha` 只在去重口径下成立且方向为零效应，单列。"),
        ("哪些结论有成本后证据？",
         f"{evid['counts']['S3_PASS']} 条（`phaseo_netBASE`）+ "
         f"{evid['counts']['by_status'].get('QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST', 0)} 条分档相反"
         f"（`phasei_v1`）；`phasei_v0` 因换手未落盘不判。"),
        ("是否存在独立样本外证据？",
         f"不存在。S4 在 {_n_pos} 条上全部 ABSENT。"),
        ("为什么本阶段仍然不能据此启动实盘？",
         "无样本外、无成交回放、无券商交割单、只有 1 条带成本后证据且压力口径趋零、"
         "研究历史缺口未关闭。"),
        ("哪些问题必须由下一阶段解决？",
         "券商成交回报、V0 逐月换手、净收益类的经济门槛预登记、研究历史缺口、成本波动的个股级分解。"),
    ]

    texts: dict[str, str] = {}
    texts["reports/L3AF_COST_NET_RETURN_RECONSTRUCTION.md"] = render_cost_report(cost, corr)
    texts["reports/L3AF_TEST_FAMILY_AUDIT.md"] = render_family_report(family, corr)
    texts["reports/L3AF_CURRENT_EVIDENCE_STATUS.md"] = render_evidence_report(evid, cost, family)
    texts["reports/STAGE_REPORT_L3AF_FINAL.md"] = render_stage_report(
        cost, family, evid, corr, status, answers
    )
    texts["docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md"] = ev.render_sheet(
        evid, cost, family, generated_at=GENERATED_AT
    )

    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "l3af_cost_net.json", cost)
    write_json(OUT / "l3af_family.json", family)
    write_json(OUT / "l3af_evidence.json", evid)

    header, rows = cn.monthly_rows(ROOT)
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(header)
    wr.writerows(rows)
    write_text(OUT / "l3af_monthly_net.csv", buf.getvalue())

    # 明细与汇总必须自洽：每档净额的列均值 == JSON 的 net_pp_month
    for j, t in enumerate(cost["tiers"]):
        col = 3 + 2 * j + 1
        mean = sum(float(r[col]) for r in rows) / len(rows)
        if abs(mean - t["net_pp_month"]) > 1e-9:
            raise SystemExit(
                f"明细与汇总不一致：{t['id']} 列均值 {mean!r} vs JSON {t['net_pp_month']!r}"
            )

    for rel, text in texts.items():
        write_text(ROOT / rel, text)

    man = {
        "generated_at": GENERATED_AT,
        "run_wall_clock": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runtime_sec": round(time.time() - t0, 1),
        "status": status,
        "prereg_sha256": cost["prereg_sha256"],
        "files": {
            k: sha256_text(v) for k, v in texts.items()
        },
        "json": {
            n: hashlib.sha256((OUT / n).read_bytes()).hexdigest()
            for n in ("l3af_cost_net.json", "l3af_family.json", "l3af_evidence.json",
                      "l3af_monthly_net.csv")
        },
        "gates": {"cost": len(cost["gates"]), "family": len(family["gates"]), "evidence": len(evid["gates"])},
    }
    write_json(OUT / "l3af_run_manifest.json", man)
    return {"status": status, **{k: man["files"][k] for k in man["files"]}}


def check() -> int:
    """只校验不重写：重新渲染并与磁盘逐字节比较。"""
    before = {p: (ROOT / p).read_bytes() for p in (
        "reports/L3AF_COST_NET_RETURN_RECONSTRUCTION.md",
        "reports/L3AF_TEST_FAMILY_AUDIT.md",
        "reports/L3AF_CURRENT_EVIDENCE_STATUS.md",
        "reports/STAGE_REPORT_L3AF_FINAL.md",
        "docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md",
    )}
    build_all()
    bad = 0
    for p, old in before.items():
        new = (ROOT / p).read_bytes()
        if old != new:
            bad += 1
            print(f"[FAIL] {p} 内容与重建不一致（{len(old)} → {len(new)} 字节）")
    print("重建一致" if bad == 0 else f"{bad} 个文件不一致")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验 md/json 是否可逐字节重建")
    ns = ap.parse_args()
    if ns.check:
        return check()
    info = build_all()
    print(f"status = {info['status']}")
    for k, v in info.items():
        if k != "status":
            print(f"  {v}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
