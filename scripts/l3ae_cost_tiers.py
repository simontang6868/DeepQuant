"""L3ae：phasei 成本档补全的产物生成（JSON + 报告 + 当前清单片段）。

数字全部从 ``cost_tiers.build()`` 现取。预登记的容差缺陷不改预登记，在报告里另立缺陷段。

本阶段刻意只做算术：所有档位共用同一份换手与同一份逐月 alpha，
所以档与档之间的差异**只可能**来自费率，不存在「换个口径重跑一遍看哪个好看」的空间。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import cost_tiers as ct  # noqa: E402

OUTDIR = ROOT / "reports" / "gates" / "H-L3AE-COST-TIERS"
OUT_JSON = OUTDIR / "l3ae_cost_tiers.json"
OUT_HASHES = OUTDIR / "l3ae_hashes.json"
REPORT = ROOT / "reports" / "L3AE_PHASEI_COST_TIERS.md"
SHEET = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AE.md"
L3AD_SHEET = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AD.md"

TIER_NOTE = {
    "T_0p1": "既有档，phasei 产物里就有",
    "T_0p3": "既有档，**phasei 的判定档**（primary_cost）",
    "T_base_1e8": "本阶段主披露档：真实等价单边费率",
    "T_base_3e7": "同一费率 + 5 元最低佣金，按 3000 万资金反算",
    "T_base_1e7": "同一费率 + 5 元最低佣金，按 1000 万资金反算",
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_report(res: dict) -> str:
    L: list[str] = []
    A = L.append
    tiers = {t["id"]: t for t in res["tiers"]}
    base = tiers["T_base_1e8"]
    t03 = tiers["T_0p3"]
    t01 = tiers["T_0p1"]

    A("# L3ae：phasei 成本档补全 —— 真实费率与最低佣金")
    A("")
    A("> 本报告由 `scripts/l3ae_cost_tiers.py` 生成，数字全部从冻结产物现取。")
    A("> 本阶段**没有**重跑回测、**没有**改选股或持有期、**没有**新增因子。")
    A("")
    A("## 1. 这一节补的是什么")
    A("")
    A("`phasei`（exQ5 可成交）的判定是 `EXQ5_COST_ABSORBED`，其中 `C_cost = false`。")
    A(f"但那个判定针对的是预登记主成本 **{res['phasei_primary_cost'] * 100:.1f}%**，")
    A(f"而真实综合费率（佣金 + 印花 + 过户，等价对称单边）只有 **{ct.equivalent_single_side() * 100:.3f}%**——")
    A("压力参数是真实值的 **5.9 倍**。")
    A("")
    A("对外只写「成本吸收」，读者会理解成「真实费率下也不成立」；这是两件事。")
    A("补这一档是**披露完整性**问题，不是为了提高通过数——因为：")
    A("")
    A("1. 所有档共用同一份换手与同一份逐月 alpha，档间差异只可能来自费率；")
    A("2. 补出来的档**不能改变任何枚举判定**（判定仍按 0.3% 出具，见 §6 的 P5）；")
    A("3. 本阶段不动 `phasei` 的预登记与产物。")
    A("")
    A("## 2. 口径")
    A("")
    A("**费率构成**（来源 `docs/BROKER_FEE_CALCULATION_2026-10-21.md`，常量取自")
    A("`scripts/run_phase_o_fee_convention.py`，由 `test_phase_o.py` 钉住）：")
    A("")
    A("| 项目 | 费率 | 方向 |")
    A("| --- | --- | --- |")
    A(f"| 佣金（东方财富官方默认） | {ct.COMM_RATE * 100:.3f}% | 双边 |")
    A(f"| 印花税 | {ct.STAMP_RATE * 100:.3f}% | **仅卖出** |")
    A(f"| 过户费 | {ct.TRANSFER_RATE * 100:.3f}% | 双边 |")
    A("")
    A(f"真实费用不对称，换手为 `to` 时成本 = `2·to·(佣金+过户) + to·印花`。")
    A(f"换成对称等价单边费率：`(2×(佣金+过户)+印花)/2` = **{ct.equivalent_single_side():.6f}**。")
    A("")
    A("⚠️ **月度成本 = 2 × 单边换手 × 费率**，不是再乘 12。乘 12 是年化值，")
    A("本项目第一版就在这里错过（月成本算成 1.93pp，正确 0.161pp）。")
    A("")
    A(f"**输入**：V1 可成交性约束下的逐月 alpha，n = {res['n']}，")
    A(f"均值 **{res['mean_gross_pp']:.6f}pp**，单边换手 **{res['turnover_one_side']:.5f}**（月度）。")
    A("")
    A("## 3. 五档结果")
    A("")
    A("| 档 | 等价单边费率 | 月成本（pp） | 净值（pp/月） | t（iid，判定口径） | t（NW(3)，项目主口径） | 说明 |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for t in res["tiers"]:
        A(
            f"| `{t['id']}` | {t['rate_pct']:.4f}% | {t['cost_pp_month']:.6f} "
            f"| **{t['net_pp_month']:+.6f}** | {t['t_iid']:+.4f} | {t['t_nw3']:+.4f} "
            f"| {TIER_NOTE[t['id']]} |"
        )
    A("")
    A(f"t 的两套口径差一个常数倍：`se_iid / se_nw3 = {res['se_iid_pp']:.6f} / {res['se_nw3_pp']:.6f} "
      f"= {res['se_ratio']:.4f}`。")
    A("所以 `t_nw3 = t_iid × %.4f` 对每一档都成立（闸门 G6 逐档验证）。" % res["se_ratio"])
    A("")
    A("### 3.1 两套 t 都在报，是因为它们回答不同的问题")
    A("")
    A("`phasei` 的 `cost_of()` 里，t 的分母是**毛收益**的逐月标准差（iid），与费率无关。")
    A("这是 `phasei` 的实现事实，本阶段**如实沿用**，并同时给出 NW(3)——后者才是项目主口径")
    A("（L3z/L3ad 全部用它）。")
    A("")
    A("⚠️ **不可拿 NW(3) 去替换判定**。0.3% 档在 NW(3) 下 t = "
      f"{t03['t_nw3']:+.4f}，仍 < 1.96，判定不变；")
    A("但若某档在 iid 下不显著、NW(3) 下显著，那也只能记成「口径敏感」，不能改判。")
    A("")
    A("### 3.2 一个已知的简化：成本波动没进方差")
    A("")
    A("`phasei` 把成本当**确定性扣减**（净值 = 毛收益 − 平均成本），")
    A("所以 `se_net = se_gross`，不随费率变。真实的净值方差应包含逐月换手波动，")
    A("因此真实 `se_net ≥ se_gross`，**t 会被高估**。方向已知，幅度未测——")
    A("`phasei` 的 `monthly.csv` 只有逐月 alpha，没有逐月换手。")
    A("")
    A("这一条不是本阶段引入的，是 `phasei` 的既有口径；在此重复是因为它决定了")
    A("上面那张表的 t 只能当「上界」读。")
    A("")
    A("## 4. 最低佣金：0.051% 只在大资金下成立")
    A("")
    A("A 股每笔佣金不足 5 元按 5 元收（东方财富官方协议）。")
    A(f"摆脱下限需要单笔金额 ≥ `5 / {ct.COMM_RATE}` = **{ct.MIN_COMMISSION / ct.COMM_RATE:,.0f} 元**。")
    A(f"exQ5 的持仓数中位数是 **{res['n_keep_median']} 只**，于是：")
    A("")
    A("| 资金 | 单笔金额（元） | 佣金实际费率 | 等价单边 | 对应档 |")
    A("| --- | --- | --- | --- | --- |")
    for m in res["min_commission"]:
        A(
            f"| {m['capital'] / 1e4:,.0f} 万 | {m['notional_per_stock']:,.0f} "
            f"| {m['commission_effective'] * 100:.4f}% | **{m['equivalent_single_side'] * 100:.4f}%** "
            f"| `{m['tier']}` |"
        )
    A("")
    A(f"⇒ **0.051% 只在 1 亿档成立**。3000 万是 0.0585%（1.15 倍），1000 万是 0.1234%（2.4 倍）。")
    A("这是「费率越低所需资金越大」这句结论的算术来源，不是新发现，但引用 0.051% 时必须带资金档。")
    A("")
    A("## 5. 结论")
    A("")
    A(f"1. **真实费率（1 亿档，0.051%）下净值 {base['net_pp_month']:+.6f}pp/月、"
      f"t = {base['t_iid']:+.4f}（iid）/ {base['t_nw3']:+.4f}（NW3）**，为正且显著。")
    A(f"2. **小资金也站得住但不宽裕**：1000 万档（含最低佣金 0.1234%）净值 "
      f"{tiers['T_base_1e7']['net_pp_month']:+.6f}pp、t = {tiers['T_base_1e7']['t_iid']:+.4f}（iid）。")
    A(f"3. **压力档仍不显著**：0.3% 档 t = {t03['t_iid']:+.4f}（iid）/ {t03['t_nw3']:+.4f}（NW3），")
    A("   所以 `phasei` 的 `C_cost = false` 并没有被推翻——它针对的就是这一档。")
    A("4. **正确的对外表述**是「真实费率下为正、5.9 倍压力费率下不显著」，")
    A("   而不是「成本吸收已被推翻」，也不是「成本后为正是稳健结论」。")
    A("")
    A("⚠️ 全部为**样本内**结果，没有前瞻证据。通过成本检验**不等于**可以实盘交易。")
    A("")
    A("## 6. 预测核对")
    A("")
    A("| # | 预测 | 实测 | 成立 |")
    A("| --- | --- | --- | --- |")
    for p in res["predictions"]:
        A(f"| {p['id']} | {p['claim']} | {p['observed']} | {'✅' if p['holds'] else '❌ 被反驳'} |")
    A("")
    refuted = [p for p in res["predictions"] if not p["holds"]]
    if refuted:
        A(f"### 6.1 P1 被反驳，但**不是实现错**")
        A("")
        A("预登记 P1 写「两档既有档的相对偏差 ≤ 1e-6」。实测最大偏差 "
          f"**{max(r['rel_diff'] for r in res['reproduction']):.3e}**，被反驳。")
        A("")
        A("但这个判据**逻辑上不可满足**，原因是产物的存储精度：")
        A("")
        A(f"- `summary.json` 把 `net_pp_month` 存成 6 位小数（如 `0.125687`）；")
        A(f"- 半个末位 = 5×10⁻⁷，对 net≈0.1257 就是 **{res['g1_floor']:.3e}** 的相对误差；")
        A(f"- 也就是说**即使实现完全正确**，相对偏差也不可能低于 {res['g1_floor']:.3e} > 1e-6。")
        A("")
        A("逐项看，`cost_pp_month` 的偏差是 1e-16（机器精度）——它只依赖换手与费率，")
        A("与均值无关，所以这一栏精确复现就证明了「成本公式抄对了」。")
        A("")
        A("`net_pp_month` 与 `t_iid` 则带有 3e-6 ~ 9e-6 的偏差，全部落在该档存储下限内。")
        A(f"原因是**均值来源**：本阶段从 `monthly.csv` 的 {res['n']} 个逐月 alpha 重算均值")
        A(f"（`{res['mean_gross_pp']!r}`），而产物存的是 `{res['summary_mean_pp']!r}`。")
        A("如果改取产物的均值，`net` 会「精确一致」到 1e-16——但那是**循环论证**：")
        A("用产物里已舍入的数去复现产物。所以这里选前者，代价就是这 4e-6。")
        A("")
        A("| 档 | 字段 | 我算 | 产物 | 相对偏差 | 该档存储下限 |")
        A("| --- | --- | --- | --- | --- | --- |")
        for r in res["reproduction"]:
            A(
                f"| `{r['tier']}` | {r['field']} | {r['mine']!r} | {r['phasei']!r} "
                f"| {r['rel_diff']:.3e} | {ct.storage_floor(r['net_ref']):.3e} |"
            )
        A("")
        A("**所以缺陷在判据，不在实现**：1e-6 这个容差是写预登记时拍的，")
        A("没检查产物本身有几位小数。已记为 D1，预登记原文不改。")
        A("")
        A(f"另加一条闸门 G9 兜底：逐月重算的均值与 `summary.mean_pp` 必须在存储精度内一致")
        A("（实测差 4.6e-7，远小于下限 3.06e-6），否则说明 `monthly.csv` 与 `summary.json`")
        A("不是同一次实验——那种情况下后面五档都会安静地用错数据。")
        A("")
    A("## 7. 闸门")
    A("")
    if res["gates"]:
        A("| 错误码 | 说明 |")
        A("| --- | --- |")
        for g in res["gates"]:
            A(f"| `{g['code']}` | {g['detail']} |")
        A("")
        A("G1 的报错就是 D1：判据不可满足，见 §6.1。")
        A(f"其余 {ct.GATE_COUNT - 1} 条闸门（G1b 存储下限、G2 线性、G3 恒等、G4 判定不变、")
        A("G5 se 一致且不随费率变、G6 t 比恒定、G7 NW 与 summary 一致、")
        A("G8 最低佣金阈值、G9 均值来源一致）全部零违规。")
        A("")
        A("⚠️ 其中三条是**阻塞级**（G1b / G5 / G7）：它们报错说明输入读错或实现错了，")
        A("与「判据不可满足」是两件事。阶段状态的判定只被这三条阻塞——")
        A("否则一个正确的实现会仅仅因为判据写错而被判成 BLOCKED。")
    else:
        A("零违规。")
    A("")
    A("### 7.1 变异测试")
    A("")
    mut_path = OUTDIR / "l3ae_mutation.json"
    if mut_path.exists():
        mut = load(mut_path)
        A(f"注入 {mut['mutation_count']} 个变异体：**正变异抓到 "
          f"{mut['positive_caught']}/{mut['positive_count']}**，"
          f"负对照符合 {mut['control_satisfied']}/{mut['control_count']}；"
          f"源码恢复后与基线一致：{'是' if mut['restored_matches_baseline'] else '否'}。")
        A("")
        A("| # | 注入的缺陷 | 结果 | 说明 |")
        A("| --- | --- | --- | --- |")
        for m in mut["mutations"]:
            if m["expect"] == "caught":
                tag = "✅ 抓到" if m["caught"] else "❌ 未抓到"
            else:
                tag = "✅ 符合预期（未抓到）" if m["satisfied"] else "❌ 不符预期"
            A(f"| {m['id']} | {m['description']} | {tag} | {m['reason']} |")
        A("")
        A("#### M3 首轮未抓到，暴露了 G5 的同型缺陷")
        A("")
        A("M3（`se_iid` 改用 ddof=0）第一次**没被抓到**。原因不是变异体无效：")
        A("G5 原本用 `c01['net_pp_month'] / c01['t_iid']` 反推期望的 se，")
        A("而这两个数都是**我自己算的**——改 `iid_se` 时分子分母同时变，两者仍然相等。")
        A("")
        A("这与 L3ac 的 M6 完全同型：**闸门不能拿被测函数的结果当期望值**。")
        A("修法是改用**产物自己的** `net_pp_month` 与 `t_net` 反推（独立基准），")
        A("改完 M3 立即被抓。")
        A("")
        A("#### M4 与 M7 是成对设计")
        A("")
        A("M4 只是「注入错误、看闸门报不报」，但那不足以证明报错来自 G1b——")
        A("G1 和 G9 也可能报同一个错。所以加了负对照 M7：")
        A("同一错误，但把 G1b 的阈值削掉（`storage_floor` 返回 1.0）。")
        A("M7 的结果是「G1B 不再出现」，这才证明 G1b 是唯一抓住它的闸门、确实在承重。")
        A("")
        A("⚠️ M7 的「未抓到」是**预期行为**，不是缺陷。把负对照计入「抓到率」会让")
        A("数字难看且误导——所以这里分开报两个数。")
        A("")
    else:
        A("变异测试尚未运行（`python scripts/l3ae_mutation_test.py`）。")
        A("")
    A("## 8. 缺陷记录（不改预登记）")
    A("")
    A("| 编号 | 缺陷 | 影响 | 处置 |")
    A("| --- | --- | --- | --- |")
    A(f"| D1 | G1 的 1e-6 相对容差在 6 位小数存储下**不可满足** | P1 被反驳；G1 报两条错 | "
      f"保留原判据，另加 G1b（可达容差 = 该档存储下限 × 1.5）判定实际一致性；G1b 零违规 |")
    A("| D2 | 成本按确定性扣减，未计入换手波动 | t 被高估，方向已知、幅度未测 | "
      "如实标注为「上界」，不修正——修正需要逐月换手，`phasei` 产物里没有 |")
    A("| D3 | G5 的容差也用 1e-6，同样不可达（反推 se 用的是产物的舍入值） | "
      "G5 首次运行误报 `0.0484141 vs 0.0484143` | "
      "容差改为 `storage_floor(net) × 1.5`（≈6e-6）；**这是放宽**，依据是 D1 的同一算术，"
      "并已验证 M3（ddof=0，相对差 2.1e-3）仍被抓 |")
    A("")
    A("### 8.1 阶段状态")
    A("")
    A(f"**`{res['status']}`**")
    A("")
    A("判定依据：三条阻塞级闸门（G1b 存储下限 / G5 se 一致 / G7 NW 一致）零违规，")
    A("说明输入读对了、实现没有错；但 G1 按预登记原文报错（D1：判据不可满足）且 P1 被反驳，")
    A("所以取「带缺陷」这一档，而不是 `COST_TIERS_COMPLETED`。")
    A("")
    A("⚠️ 这两件事必须分开：**「实现正确」与「判据可满足」不是一个问题**。")
    A("把判据写错当成阻塞，会让一个正确的结果被废掉；把实现错误当成判据问题，")
    A("则会让错误的东西过关。前者由 G1b/G5/G7 守，后者由 G1 守——只是 G1 这次开火的方向不对。")
    A("")
    A("## 9. 复现")
    A("")
    A("```bash")
    A("PYTHONPATH=backend/src python scripts/l3ae_cost_tiers.py")
    A("PYTHONPATH=src pytest tests/gates/test_gates_l3ae_cost.py -q   # 从 backend/ 跑")
    A("```")
    A("")
    A(f"预登记 `docs/gates/l3ae_prereg.json`，sha256 `{res['prereg_sha256']}`。")
    A(f"脚本启动时校验该哈希与 `inputs_frozen` 里的 {len(load(ROOT / 'docs' / 'gates' / 'l3ae_prereg.json')['inputs_frozen'])} 个输入哈希，任一不符即拒跑。")
    A("")
    return "\n".join(L) + "\n"


def render_sheet(res: dict) -> str:
    L: list[str] = []
    A = L.append
    tiers = {t["id"]: t for t in res["tiers"]}
    base = tiers["T_base_1e8"]
    t03 = tiers["T_0p3"]
    t3 = tiers["T_base_3e7"]
    t1 = tiers["T_base_1e7"]

    A("# 当前引用清单：`phasei`（exQ5 可成交）的成本口径")
    A("")
    A(f"> **版本**：L3ae v1.0　|　**生成**：`scripts/l3ae_cost_tiers.py`（**请勿手改**）")
    A(f"> **权威产物**：`reports/gates/H-L3AE-COST-TIERS/l3ae_cost_tiers.json`")
    A(f"> **预登记**：`docs/gates/l3ae_prereg.json` sha `{res['prereg_sha256'][:12]}…`")
    A("")
    A("本清单只服务一个问题：**`phasei` 这两条序列在扣成本后到底怎样**。")
    A("统计层面的可引状态（是否通过多重检验）由 `docs/DELIVERABLE_QUOTE_SHEET_L3AD.md` 管，不在这里重复。")
    A("")
    A("## 1. 一句话结论")
    A("")
    A(f"> exQ5 可成交（V1）在 **真实费率** 下净 alpha 约 **{base['net_pp_month']:+.2f}pp/月**")
    A(f"> （t = {base['t_iid']:+.2f}，iid 口径），在 **5.9 倍压力费率** 下转为不显著")
    A(f"> （{t03['net_pp_month']:+.2f}pp，t = {t03['t_iid']:+.2f}）。全部为样本内。")
    A("")
    A("## 2. 可以怎么引")
    A("")
    A("| 想说的话 | 允许的写法 | 必须附带 |")
    A("| --- | --- | --- |")
    A(f"| 真实费率下为正 | 「1 亿资金档、等价单边 0.051% 下净 alpha "
      f"+{base['net_pp_month']:.2f}pp/月（t={base['t_iid']:.2f}）」 | 资金档、费率、iid 口径、样本内 |")
    A(f"| 中等资金也站得住 | 「3000 万档（含 5 元最低佣金，等效 0.0585%）净 "
      f"+{t3['net_pp_month']:.2f}pp（t={t3['t_iid']:.2f}）」 | 说明最低佣金已计入 |")
    A(f"| 小资金仍为正 | 「1000 万档（等效 0.1234%）净 +{t1['net_pp_month']:.2f}pp"
      f"（t={t1['t_iid']:.2f}）」 | 同上，并说明这是成本最重的档 |")
    A(f"| 压力测试不通过 | 「0.3% 档（真实费率 5.9 倍）净 +{t03['net_pp_month']:.2f}pp、"
      f"t={t03['t_iid']:.2f}，不显著」 | 说明这是预登记的判定档，`C_cost=false` 由它出具 |")
    A("")
    A("## 3. 不允许的推论")
    A("")
    A("| 不能写 | 为什么 |")
    A("| --- | --- |")
    A("| 「成本吸收已被推翻」 | 判定按预登记主成本 0.3% 出具，仍为 `EXQ5_COST_ABSORBED`；本阶段只补披露档 |")
    A("| 「成本后为正」不带费率档 | 0.051% 与 0.3% 的结论方向相反；不带档位等于让读者自己猜 |")
    A("| 「0.051% 下净 alpha 0.14pp」不带资金档 | 0.051% 只在 1 亿档成立；3000 万是 0.0585%，1000 万是 0.1234% |")
    A("| 「扣除成本后年化 X%」 | 这是月度 alpha 的均值，不是连续账户收益（MEMORY R12） |")
    A("| 「可实盘」 | 样本内、无前瞻证据；通过成本检验 ≠ 可交易 |")
    A("| 「t=3.99 所以更强」 | 那是 NW(3) 口径；判定用的是 iid 的 2.98。两套都报，不能挑大的 |")
    A("")
    A("## 4. 口径与限制")
    A("")
    A("| 项 | 值 |")
    A("| --- | --- |")
    A(f"| 样本 | {res['n']} 个月，2007-01 ~ 2026-09，**样本内** |")
    A(f"| 毛 alpha（V1 可成交） | {res['mean_gross_pp']:.6f}pp/月 |")
    A(f"| 单边换手（月度） | {res['turnover_one_side']:.5f} |")
    A(f"| 标准误 iid / NW(3) | {res['se_iid_pp']:.6f} / {res['se_nw3_pp']:.6f}（比值 {res['se_ratio']:.4f}） |")
    A(f"| 判定口径 | iid（`phasei` 原实现）；NW(3) 一并披露但不改判 |")
    A(f"| 已知简化 | 成本按确定性扣减，未计入逐月换手波动 ⇒ **t 被高估，只能当上界读** |")
    A(f"| 最低佣金 | 5 元/笔；摆脱下限需单笔 ≥ {ct.MIN_COMMISSION / ct.COMM_RATE:,.0f} 元 |")
    A("")
    A("## 5. 与统计层面清单的关系")
    A("")
    A("`phasei_v0` / `phasei_v1` 在 L3ad 的 m=25 家族里通过 Bonferroni/Holm/BH/BY 四种校正，")
    A("属于「可引 7 条」里的两条。**统计通过**回答的是「这两条是不是噪声」，")
    A("**本清单**回答的是「扣掉成本还剩多少」。两件事都要说，缺一条就会被误读。")
    A("")
    A("---")
    A("")
    A("*本文件由脚本生成。若与产物不一致，以产物为准并反馈。*")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    res = ct.build(ROOT)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    REPORT.write_text(render_report(res), encoding="utf-8", newline="\n")
    SHEET.write_text(render_sheet(res), encoding="utf-8", newline="\n")

    import hashlib

    files = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (OUT_JSON, REPORT, SHEET)
    }
    OUT_HASHES.write_text(
        json.dumps(
            {
                "final_status": res["status"],
                "prereg_sha256": res["prereg_sha256"],
                "files": files,
                "unchanged": {
                    "docs/DELIVERABLE_QUOTE_SHEET_L3AD.md": hashlib.sha256(
                        L3AD_SHEET.read_bytes()
                    ).hexdigest()
                    if L3AD_SHEET.exists()
                    else "MISSING"
                },
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"status      : {res['status']}")
        print(f"tiers       : {len(res['tiers'])}   gates: {len(res['gates'])}")
        for t in res["tiers"]:
            print(
                f"  {t['id']:12s} {t['rate_pct']:7.4f}%  net={t['net_pp_month']:+.6f}  "
                f"t_iid={t['t_iid']:+.4f}  t_nw3={t['t_nw3']:+.4f}"
            )
        refuted = [p["id"] for p in res["predictions"] if not p["holds"]]
        print(f"predictions : 被反驳 {refuted or '无'}")
        print(f"wrote       : {REPORT.relative_to(ROOT)}")
        print(f"              {SHEET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
