# 供外部审查｜DeepQuant「仿真执行门」预登记草案（DRAFT，待用户确认）

| 字段 | 内容 |
|---|---|
| 课题 | 仿真执行链路的工程就绪判定（成交回报对账 + 五项演练 + 通过标准） |
| 阶段 | 预登记草案（六阶段技能链的**前置治理**，非建模/预测/评估阶段） |
| 编号 / 状态 | `DRAFT-SIM-EXEC-GATE-1`｜**`DRAFT_PENDING_USER_CONFIRMATION`** |
| 生成时间 | 2026-11-15（起草），本转发版 2026-11-15 15:29 CST |
| 上游输入 | `docs/ENGINEERING_CHANGE_R8_2026-11-15.md` §6；`docs/SIM_REBALANCE_DRILL_2026-12-01.md` §2/§5；`scripts/sim_reconcile.py`、`scripts/sim_drill_driver.py` |
| 版本 | 源文件 `docs/SIM_EXECUTION_GATE_PREREG_DRAFT_2026-11-15.md`，**正文逐字保留、本转发版只加封面与审查指引** |
| 冻结情况 | ⚠️ **未冻结、无 `.sha256` 侧车、未生效**；不得被引用为判定依据 |
| `StatisticalVerdict` | `NOT_APPLICABLE`（草案阶段无统计判定、无样本外数据） |
| `MaturityLevel` | `DRAFT_UNFROZEN_PENDING_USER_CONFIRMATION` |
| 样本区间 | 无（不含任何回测样本、行情区间或收益数字） |
| 方法 | 外部真值优先的双向差集对账 + 五项离线演练 + 逐条通过标准；金额用 `Fraction` 精确比较 |
| 检查结果 | 本轮未运行任何新实验；既有组件的离线自检结论引自 R8 交付记录，**不构成对本草案的验证** |
| 未解决问题 | 见 §4 前置链（6 项中 5 项未完成）、§7 审查者重点判断 |
| 下一步 | 用户确认 → 按 `docs/PREREG_TEMPLATE_2026-11-10.md` v2 补 §0/§4-A → 冻结 sha256 → 另立执行预登记 → 才进入实现 |

> ⭐ **一句话给审查者**：这是一道**工程执行就绪门**，不是策略有效性检验。
> 它**不改 G2 定义**，通过也不折算成 G2 计数、不进 G1/G2/S4、不写入可引集合、不作经济性判断。
> **执行方自审、不构成独立审查**；本文件由执行 Agent 起草，请按 §6 命令自行复算。

---

## 1. 这道门要解决什么（与 G2 的分工）

| | G2（既有，不动） | 仿真执行门（本草案，新增） |
| --- | --- | --- |
| 问的问题 | 规划器与台账是否一致 | **真实委托／成交／回报／费用**链路是否可用且无未解释差异 |
| 真值来源 | 影子对账（`shadow_reconcile/<day>.json`） | gm-paper **外部真值**：`get_execution_reports` / `get_unfinished_orders` / `get_cash` / `get_positions` |
| 计数 | 连续 20 个交易日 | **按次判定**，不设连续天数、不设判定日 |
| 含成交回报 | ❌ 不含 | ✅ 必须含 |
| 结果归属 | 证据分级 | **工程执行就绪判定**；不构成样本外证据，不进 G1/G2/S4、不作经济性判断 |

> 之所以另设：`G2 只比规划与台账、不含成交回报` ⇒ 即使 G2 通过，也回答不了「报单真的能成交、
> 回报能被对账器消费、佣金口径对不对」。这两件事不能互相替代，也不能把本门的结果折算进 G2 计数。

## 2. 组成（三个部件，全部满足才算通过）

**(a) 成交回报对账** —— 复用 `scripts/sim_reconcile.py`（已实现、离线自检通过）

- 外部真值优先：以 gm-paper 回报为真值，本地订单记录为被核对方；做**双向差集**（外部有本地无／本地有外部无都算差异）。
- 金额用 `Fraction` 精确比较；`exec_type` ↔ 本地 `status` 映射表须完整，未识别值 ⇒ `HALT:UNKNOWN_STATUS`。
- 空输入 ⇒ `NO_INPUTS` **停机**，⚠️ **不算 PASS**（「没检出」≠「没问题」）。
- 任一差异 ⇒ `HALT:*`；差异必须逐条解释，解释不了就停机，**不重跑凑通过**。

**(b) 五项演练各 ≥1 次** —— 复用 `scripts/sim_drill_driver.py` 的 `SCENARIOS`

| # | 场景 | 验收点（可算） |
| --- | --- | --- |
| 1 | `IDEMPOTENT_REPLAY` | 同一 `client_order_id` 重放 ⇒ 实际委托笔数不增 |
| 2 | `CANCEL_REPRICE` | 撤单重定价次数 ≤ `MAX_REPRICE=2`；超次 ⇒ 停机 |
| 3 | `PARTIAL_FILL` | 部分成交的数量／金额在双向差集中**零未解释差异** |
| 4 | `LIMIT_UNTRADABLE` | 不可成交标的 ⇒ 不产生委托，或委托后按 `CARRY_MAX_DAYS=10` 正确顺延／终止 |
| 5 | `CIRCUIT_SIM` | 熔断触发 ⇒ 该时点后续委托数 = 0 |

**(c) 通过标准** —— 沿用 `docs/SIM_REBALANCE_DRILL_2026-12-01.md` §5，逐字对齐

1. **零**未授权／重复／错误订单（计数 = 0）；
2. 差异**全部解释**，且 `NO_INPUTS` 不算 PASS；
3. 五项演练全部完成；
4. 授权校验（`paper_authorization_guard` `ALLOW`）、通道隔离（`check_channel_isolation.py --remote` FAIL 0）、证据归档齐备；
5. 不通过 ⇒ **停机报告，不重跑凑通过、不改判据**。

## 3. 明确不做（防止被误用）

- ⭐ **不改 G2 定义、不给 G2 加天数、不改 G2 判定日**；本门通过也不折算成 G2 计数。
- 演练／对账结果**不构成样本外证据**，不改变 G1／G2／S4，不写入 `RESULTS_INDEX.md` 可引集合。
- 不作任何经济性判断（不看净值、收益率、夏普、MDE）；`MDE_NOT_ESTIMABLE`。
- 不启动实盘、不改策略参数、不新增因子、不合并两机、不创建/修改授权文件。
- 探针（`PROBE`，见 `GM_PAPER_PROBE_PREREG_2026-11-15.md`）与本门**分开计数、分开授权**；探针授权 ≠ 本门授权 ≠ U-16。

## 4. 前置链（本草案不解锁任何一步）

| # | 前置 | 状态 |
| --- | --- | --- |
| 1 | U-15 书面声明 + `paper_authorization.json` `ALLOW 6/6` | ✅ 已就位（agent 不触碰） |
| 2 | 11-30 `mode=FORWARD` 且含 `target_holds_d4` 的台账 | ⏳ 等时间 |
| 3 | 11-27 月末 DRYRUN、11-30 17:10 后 `preflight_decision_day.py --cloud` | ⏳ 保持不变 |
| 4 | **U-16** 书面放行（12-01 演练） | ⏳ 独立待授权 |
| 5 | 用户确认**本草案**并冻结正式预登记 sha256 | ⏳ **本步即 R8 §6 要求交用户确认的动作** |
| 6 | 真实 gm-paper 报单适配器（非离线类）实现 + 变异 | ⏳ 尚未实现；R8 §5 只授权「独立适配器类 + 离线测试」 |

## 5. 请用户确认的三件事

1. **是否同意「另设一道独立的仿真执行门」**（G2 之外，不折算、不改 G2 定义）？
2. **是否同意通过标准按方案 §5 逐字采用**（特别是 `NO_INPUTS` 不算 PASS、不重跑凑通过）？
3. **确认后何时冻结**：建议与 U-16 一并给出，冻结 sha256 后再实现真实报单适配器与执行预登记。

> 用户回复确认后，执行方将：补全模板 v2 的 §0 元信息 / §4-A 符号与幅度约定 → 冻结 `.sha256`
> → 另立执行预登记 → 再进入实现。**在用户确认前，本文件保持 DRAFT，不执行任何动作。**

---

## 6. 审查者只读复算命令（全部不触发报单、不读凭证值）

| # | 命令（在仓库根目录） | 期望值 |
|---|---|---|
| 1 | `sha256sum docs/SIM_EXECUTION_GATE_PREREG_DRAFT_2026-11-15.md` | 与本转发版 §1–§5 正文逐字一致（本转发版**只**加封面与 §6/§7） |
| 2 | `ls docs/SIM_EXECUTION_GATE_PREREG_DRAFT_2026-11-15.md.sha256` | **不存在** ⇒ 证明 DRAFT 未冻结 |
| 3 | `python scripts/sim_reconcile.py --self-check` | `PASS`，含 `NO_INPUTS` 停机行为 |
| 4 | `python scripts/sim_drill_driver.py --self-check` | 五项演练离线记录 5/5，不写盘 |
| 5 | `python scripts/sim_drill_driver.py --execute` | `rc=4`（`NOT_AUTHORIZED`） |
| 6 | `python scripts/gm_paper_order_adapter.py --execute` | `rc=4`（`NOT_AUTHORIZED`） |
| 7 | `python scripts/sim_drill_mutation_test.py` | 真变异 **11/11**（全为真改行为） |
| 8 | `python scripts/gm_paper_probe_mutation_test.py` | **10/10**（六项 DENY→ALLOW 全检出） |
| 9 | `python scripts/deploy_manifest.py --verify --remote` | 清单文件逐位一致 `PASS` |
| 10 | `python scripts/check_channel_isolation.py --remote` | `FAIL 0`（只读适配器无 `order_*(` 调用） |
| 11 | `grep -n "G2" docs/SIM_EXECUTION_GATE_PREREG_DRAFT_2026-11-15.md` | 只出现「不改 G2 定义 / 不折算」，无新增计数或判定日 |
| 12 | `python scripts/corrections_hashchain.py --verify` | `PASS`，`n_lines=51`，链头 `738817f4…` |

> 云端只读（需部署密钥）：`ls /opt/quant/research/stock_pick_2026-10-25/forward/ledger/` ⇒ 只应有
> `20260930.json`、`20261030.json`；**11-30 台账尚不存在**，故 §4 第 2 项不可能已满足。

## 7. 建议审查者重点判断的五个问题

1. **另设一道门是否必要**，还是应把「含成交回报」并入 G2？并入会改变 G2 定义与已登记判定日，代价如何？
2. **`NO_INPUTS` 停机**是否足够？「外部真值拿不到」与「确实无成交」在真实 gm-paper 上如何区分而不产生假 PASS？
3. **`exec_type` ↔ `status` 映射表**由谁冻结？若由实现方在看到真实回报后补写，是否构成「用数据定判据」？
4. **五项演练的验收点是否可算、是否覆盖验收项原文**（项目曾出现「测试全绿 ≠ 验收项覆盖」）；特别是 `CIRCUIT_SIM` 的「该时点后续委托数 = 0」如何防止被时间窗口 Choose 出来？
5. **本门通过后的措辞边界**：如何防止「工程就绪」被外部读成「策略有效」？建议强制携带 `StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY` / `MaturityLevel=DESIGN_VALIDATED_OFFLINE` 一类的标注。

## 8. 引用规范与不可引清单

- 引用本草案**必须**带：编号 `DRAFT-SIM-EXEC-GATE-1` + 状态 `DRAFT_PENDING_USER_CONFIRMATION` + `StatisticalVerdict=NOT_APPLICABLE` + `MaturityLevel=DRAFT_UNFROZEN_PENDING_USER_CONFIRMATION`。
- **不得**把本草案写成「已通过」「已冻结」「已验证」；**不得**用其中的通过标准去判定任何历史结果。
- **不得**把演练／对账结果折算进 G2、写进 `RESULTS_INDEX.md` 可引集合、或用于「能不能赚钱」的判断。
- 本文件由**执行 Agent 自审**，不构成独立审查；发现错误按项目纪律**保留原始结果 + 出更正，原文不改写**。

---

*转发版生成：2026-11-15 15:29 CST｜源文件未改动｜全程只读核查，未报单、未创建或修改任何授权文件、未改策略参数、未回显凭证值。*
