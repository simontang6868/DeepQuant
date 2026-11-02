# SP-5 后续任务结题：冻结规则审计与前瞻启动门禁

| 项目 | 内容 |
|---|---|
| 课题 | DeepQuant SP-5 后续任务（冻结规则审计 + 前瞻观察启动门禁） |
| 报告日期 | 2026-10-26（GMT+8） |
| 代码基线 | git `5cfed94`（审计期间未产生新提交；`scripts/sp5/` 仍未纳入 git） |
| 审计范围 | P0-1 规则一致性、P0-2 独立账本、P0-3 启动门禁、P1-1 成本口径、P1-2 数据偏置、P1-3 判定协议、§九 11 项行为测试 |
| 判定性质 | 只读审计。**未修改任何规则、参数、冻结件与历史产物**；发现缺陷只登记（任务书 §二.8） |
| 总判定 | **`BLOCKED_RULE_VERSION_UNRESOLVED`**（次：`BLOCKED_LEDGER_INTEGRITY`、`OBSERVATION_NOT_STARTED`） |

---

## 0. 最终状态

> ### `BLOCKED_RULE_VERSION_UNRESOLVED`
> **前瞻正式观察期不得启动。** 观察期当前 **0 个月**（`forward/observations.csv` 唯一一行为 `SELFTEST`）。
> SP-4 总判定维持 **`EVIDENCE_INSUFFICIENT_FOR_CAPITAL`**。

三句话概括：

1. **规则层面**：冻结文档写 1-50 元、引擎跑 1-200 元，两者产生的 top5 目标在 4 个截面上 **0/5 重合**——不是"标注不一致"，是**两个不同策略**。原 P0-1 报告"影响≈0"的结论已被本次更正的实测推翻。
2. **账本层面**：账本数学完全可独立复现（NAV 差 ≤51.84 元 / 0.03%，50 路径中位数逐位一致），但账本隐含三个理想化假设（买入不受涨停/停牌限制、停牌缺口无限期 carry、停更代码当正常持仓），且台账可被静默覆盖（已发生一次自指实证）。
3. **前瞻层面**：唯一方法上合法的前瞻流（`p13_forward`）恰是没有冻结配置的那一套；另一套（`sp4_forward`）使用 `fwd20.notna()`，**含未来信息**，不可用于前瞻。判定协议本身也跑不通（36 个月对 8% 门槛的分辨率只有 ±5pp）。

---

## 1. 执行快照与变更声明

### 1.1 冻结件哈希核对（审计开始 → 结束，全部未变）

| 文件 | sha256（前 16 位，raw bytes） | 变化 |
|---|---|---|
| `SP5_预登记.md` | `32ecc9a6794948f0` | 无 |
| `05_SP5_任务书.md` | `006fdee753376971` | 无 |
| `forward/sp4_config_v1.json` | `d4dacb7113cc72ee` | 无 |
| `scripts/sp5/p04_position_dd.py` | `de47e75cfbde3cf8` | 无 |
| `scripts/sp5/p13_forward.py` | `c298552241d38079` | 无 |
| `scripts/sp5/p13b_forward_track.py` | `4fc4e4a1a226f37d` | 无 |
| `scripts/sp5/fill_engine.py` | `2e7bdbd30426c8ea` | 无 |
| `forward/ledger/20260930.json` | `41952de4403242af` | 无（mtime 16:34:01，早于本次审计） |

### 1.2 本次新增（全部为审计侧新文件，未触碰被测代码）

| 类别 | 文件 |
|---|---|
| 脚本 | `p04b_ledger_independent.py`、`p04c_exception_detail.py`、`p04d_bias_diagnostics.py`、`p04e_attrib_fix.py`、`p13c_rule_diff_check.py`、`p13d_pool_overlap.py`、`p15_cost_alignment.py`、`p16_decision_power.py` |
| 测试 | `scripts/sp5/tests/test_p0_audit_behaviors.py`（11 项） |
| 报告/表格 | 11 项（见 §3） |
| 数据产物 | `sp5_out/p04b_*`、`p04c_*`、`p04d_*`、`p13c_*`、`p13d_*`；`reports/SP5_LEDGER_DAILY_RECONCILIATION.csv`、`SP5_DECISION_POWER.csv`、`SP5_COST_MODEL_*csv` |

### 1.3 本次修改（仅限本任务自身产物，均已按规则"保留原文 + 追加更正"）

| 文件 | 改动 | 理由 |
|---|---|---|
| `SP5_AUDIT_SNAPSHOT.md` | 追加"快照自审更正" | 原记录的 ledger 哈希 `01e19ea3…` 不可复现（S1），改为权威值 `41952de4…` 并写明算法 |
| `SP5_RULE_VERSION_AUDIT.md` | 追加 §7 更正说明 | 原 §3"top5 5/5 一致"方法错误，实测 0/5 |
| `SP5_RULE_DIFF_MATRIX.csv` | `price_filter` 行 impact/disposition 更新 | 同上 |
| `SP5_LEDGER_INDEPENDENT_AUDIT.md` | 补 §5、更新 §0/§6/核验项 9 | 50 路径复现与异常案例完成 |

> 未修改：`SP5_预登记.md`、`sp4_config_v1.json`、`p04_position_dd.py`、`p13_forward.py`、`p13b_forward_track.py`、`fill_engine.py`、`forward/` 下任何文件。

---

## 2. 七个问题的回答

> 注：以下七问对应本次六项交付（P0-1→P1-3）＋处置，顺序与任务书分节一致；若专家问题清单另有措辞，可按 §3 的交付物对应关系逐条映射。

### Q1 冻结规则与实际执行是否一致？

**否，`RULE_VERSION_UNRESOLVED`（性质：两个不同策略）。**

- 冻结文档（预登记 §2、`sp4_config_v1.json`）写 `price_filter=[1.0, 50.0]`；产生**全部**机制阶段数字的引擎（`build_panel.py:190`、`sp3_entry_rule.py:25`）实际执行 `between(1.0,200.0)`；`p13_forward.py:65` 硬编码 200 为主口径。
- **更正后的实测**（4 个截面，权威实现）：top5 目标 **0/5 重合**；最终池差 72~125 只；1-50 池是 1-200 池的**子集**（Jaccard 0.905）。
- 机制：1-200 的 top5 全部卡在 `liqpct` = 0.1987~0.1996（阈值 0.20 边缘），宇宙收紧 10% 后分位重算全部越界。原 P0-1 报告因沿用 1-200 宇宙预计算分位而漏检（更正见 `SP5_RULE_VERSION_AUDIT.md` §7）。
- 其余 11 项规则（n_hold、f3 阈值、流动性带、amt_min、仓位、现金收益、换手、ST 缺失、fwd20 使用）均一致或已登记差异。

### Q2 账本能否独立重建？重建结果是否与原件一致？

**能，且一致。但账本隐含三个理想化假设。**

- 独立实现（不调用 `p04.simulate/metrics`）逐日对账：NAV 最大绝对差 **51.84 元（0.03%）**，MDD/年化差 **≤1e-7**，逐日 DD 差 ≤0.0004（26,727 行）。
- 50 路径全分布：7 个起点的中位数与破界占比与 `sp5_position_dd.json` **逐位相同**（2005 起点 ann +8.84%、MDD −15.92%、破界 4.0%），判定同为 `DD_ROBUST_20PCT`。
- 三项假设（详见 `SP5_LEDGER_EXCEPTION_CASES.md`）：
  - **A 买入端无成交约束**：path-0 99 笔 / 74 个月（无 bar 89、停牌 7、涨停 3）；施加约束后年化 −1.14~+0.03pp、MDD 最多恶化 4.12pp。
  - **B 停牌缺口 carry**：≥5 日缺口 12,391 段 / 2,594 只，最长 1,648 交易日；填充日 path-0 69,933、50 路径 644,713。
  - **C 幽灵仓位**：724/64,250 = **1.127%** 槽位、18 只（最长 600647 连续 81 个月）。

### Q3 前瞻系统能否启动？

**不能，`BLOCKED`。**

- G1~G11 十一项门禁：**PASS 3 / FAIL 7 / UNVERIFIED 1**。
- 三个致命项：① 规则版本未裁决且两口径目标 0/5 重合；② 1-200 口径**没有冻结配置**（无法作为"预登记系统"启动）；③ 台账可静默覆盖 + 配置哈希不校验。
- 两套系统对比：V-A（`p13_forward`）方法合法但无冻结配置；V-B（`sp4_forward`）有哈希断言但使用 `fwd20.notna()`（**未来信息**）且依赖尾部被截断的面板，**不可用于前瞻**。

### Q4 成本口径是否与回测可比？

**不可直接对比，`COST_MODEL_INCONSISTENT_ACROSS_SYSTEMS`。**

- 五套实现、三种单边费率：回测价差 0；前瞻价差 0.10%；V-B 魔数 `0.000204`（不可复算、未建模最低佣金）。
- 建仓计费方式不一：`p13` 建仓即计完整往返（高估约 1 倍），`p13b` 按方向单边计。
- **最低佣金在 SP-5 结构下影响巨大**：5 只 × 25% → 每档 = 资金 × 5%，需 **W ≥ 40 万元**才不绑定（10 万元时实际佣金率 0.10%，往返 0.252% vs 大资金 0.102%，2.47 倍）。
- 实际发生成本锚点：path-0 2005 起点累计 10,653.83 元 / 22.29 年 → 年化 **0.133%**（占平均 NAV）、0.478%（占初始资金）。

### Q5 历史数据有哪些偏差会影响结论？

**已登记 14 项，其中 4 项 UNVERIFIED、3 项方向不明**（`SP5_HISTORICAL_DATA_BIAS_REGISTER.md`）。

- 影响方向的：D1 幽灵仓位（若实为退市→高估）、D2 缺口 carry（→低估波动）、D4 `fwd20` 过滤（→高估）。
- 方向不明的：D3 买入无约束、D5 沪市 ST 无真值、D6 分红口径未验证。
- 引用时必须带的限定语已列成清单（§2 of 登记册），共 8 类场景。

### Q6 观察期判定协议能否执行？

**不能，`PROTOCOL_NOT_EXECUTABLE_AS_WRITTEN`。**

- 10 条条款：PASS 1、FAIL 5、AMBIGUOUS 3、且 1 条与 `no_peeking` 潜在冲突。
- **36 个月 + 8% 门槛无法判定**：se(年化) 2.57pp、**MDE 7.19pp**；观测值 8% 的 95% CI = **[3.0%, 13.0%]**。要用 8% 作门槛需约 **120 个月**（MDE 3.94pp）。
- 成功线 −20% 与失效线 −25% 之间有 5pp **无状态区间**；两条失效条件（基准超额、流动性溢价）**未实现**；状态机缺失。
- 草案 v1.0 已写入（未生效，需授权）。

### Q7 现在应当做什么？

**三件事，前两件需授权，第三件可立即执行。**

| # | 事项 | 是否需要授权 | 说明 |
|---|---|---|---|
| 1 | **裁决规则版本**：出具预登记 v1.3/v2.0，明确唯一口径或双策略并行（各出冻结配置） | **需要**（规则变更） | 这是唯一能解 BLOCKED 的动作；10-30 前未完成则该月不产生 FORWARD 记录（正确代价） |
| 2 | **修台账完整性**：写入前拒覆盖 + 启动 assert 配置哈希 + 记录代码/数据哈希 + 回合单边计费 | **需要**（改代码） | 属缺陷修复，按 §二.8 必须先存证据再改；本次已存 |
| 3 | **补基准实现**（等权可买股 + H00922 + 同池随机配对） | 不需要（新增只读计算） | 可在裁决期间完成，不改变任何策略行为 |

---

## 3. 交付物清单

| # | 文件 | 内容 | 判定 |
|---|---|---|---|
| 1 | `reports/SP5_RULE_VERSION_AUDIT.md` | 规则/配置/执行一致性 + **§7 更正说明** | `RULE_VERSION_UNRESOLVED`（性质升级） |
| 2 | `reports/SP5_RULE_DIFF_MATRIX.csv` | 14 行规则差异矩阵 | 同上 |
| 3 | `reports/SP5_LEDGER_INDEPENDENT_AUDIT.md` | 独立账本重建 + 十项核验 | `LEDGER_MATH_PASS` / 审计项 10 `FAIL` |
| 4 | `reports/SP5_LEDGER_DAILY_RECONCILIATION.csv` | 26,727 行逐日对账 | 最大差 0.03% |
| 5 | `reports/SP5_LEDGER_EXCEPTION_CASES.md` | 四类异常逐笔案例 | A/C 为新发现 |
| 6 | `reports/SP5_FORWARD_START_GATE.md` | 11 项启动门禁 | **`BLOCKED_RULE_VERSION_UNRESOLVED`** |
| 7 | `reports/SP5_COST_MODEL_ALIGNMENT.md` + `.csv` / `SP5_COST_MODEL_SCALE.csv` | 5 套成本实现对账 + 资金敏感性 | `COST_MODEL_INCONSISTENT_ACROSS_SYSTEMS` |
| 8 | `reports/SP5_HISTORICAL_DATA_BIAS_REGISTER.md` | 14 项数据偏置登记 | `HISTORICAL_BIAS_REGISTERED` |
| 9 | `reports/SP5_FORWARD_DECISION_PROTOCOL_AUDIT.md` | 判定协议审计 + 草案 v1.0 | `PROTOCOL_NOT_EXECUTABLE_AS_WRITTEN` |
| 10 | `reports/SP5_DECISION_POWER.csv` | 36/60/120 月功效表 | 支撑 Q6 |
| 11 | `scripts/sp5/tests/test_p0_audit_behaviors.py` | §九 11 项行为测试 | 6 PASS / 5 FAIL（FAIL 即缺陷证据） |

---

## 4. 缺陷清单

| 编号 | 缺陷 | 严重度 | 阻塞启动 | 证据 | 处置 |
|---|---|---|---|---|---|
| **B1** | 价格过滤两口径目标完全不同（0/5）且 1-200 无冻结配置 | 高 | **是** | P0-1 §7 | 需正式裁决 |
| **B2** | 台账可静默覆盖；配置哈希只记录不校验 | 高 | **是** | P0-3 G1/G4 | 需授权改代码 |
| C1 | `prev = sorted(glob)` 自指 → `n_swaps=0` | 中 | 否 | ledger `prev==target` | 排除当月文件 |
| C2 | 无基准实现 | 中 | 否 | P0-3 G6 | 可立即补 |
| C3 | 成本口径三套并存 | 中 | 否 | P1-1 | 统一到 `fee_parts` |
| C4 | 数据版本无哈希 | 低 | 否 | P0-3 G10 | 写指纹 |
| C5 | `p13.judge_fills` 传 `is_st=False` | 低 | 否 | P0-3 G8 附注 | 与 p13b 同源 |
| C6 | 前复权重算 → 历史 NAV 不可精确重现 | 低 | 否 | P0-3 §4.2 | 冻结快照 |
| S1 | 审计快照的 ledger 哈希不可复现（记录方法未写明） | 低（自审） | 否 | 快照更正节 | 已更正 + 登记教训 |
| M1 | P0-1 原 §3 方法错误导致漏检 | 高（方法学） | 是（连带 B1） | P0-1 §7 | 已出更正说明，原文保留 |
| T1 | 11 项测试中 5 项在现有代码上失败 | —— | 是（连带 B2） | 测试输出 | 作为规格而非回归；修复后应转 PASS |

---

## 5. 对既有结论的影响

| 既有结论 | 状态 | 说明 |
|---|---|---|
| SP-4 总判定 `EVIDENCE_INSUFFICIENT_FOR_CAPITAL` | **不变** | 本次不产生任何样本外证据 |
| `DD_ROBUST_20PCT` | **不变** | 独立复现逐位一致；最差路径 −20.65%（2008-03-11→2008-11-04，最大亏损窗口 −7.52%，5/5 持仓全跌） |
| `FILL_ABSORBED` | **须加限定** | 未含涨停/停牌买入限制（D3） |
| `ENTRY_RULE_CANNOT_VERIFY` | **不变** | 与 MDE 2.22pp 一致；且配置 `known_limitations` 写"审计已证买入规则对期望无贡献"，与判定**冲突**，属措辞错误（须更正） |
| `ST_PARTIAL_CANNOT_VERIFY` | **不变** | D5 |
| 机制阶段参数标注"价 1~50 元" | **须更正** | 实际执行为 1-200（P0-1）；数字有效，标注错误 |
| "价格过滤冲突对持仓影响≈0" | **作废** | 实测 0/5 重合（P0-1 §7） |
| "两口径观察期初期结论将重合" | **作废** | 同上 |
| exQ5 / S / T / U 全样本与"现代段边缘"表述 | **不变** | 这些数字在 1-200 下产生，未受影响 |
| `PIPELINE_READY`（`SP5_FORWARD_READY.md`） | **有效，须限定** | 指"管道端到端能跑"；本次判的是"能否开始正式观察"，两者不冲突。该文件第 3 条已正确指出两口径"会让 5 只目标全部换人" |

---

## 6. §九 十一项行为测试结果

| # | 行为 | 结果 | 说明 |
|---|---|---|---|
| T1 | 冻结配置哈希被校验（非仅记录） | **FAIL** | 仅写入台账，无 assert |
| T2 | 台账不可变 | **FAIL** | 无条件覆盖，无存在性检查 |
| T3 | 种子可复现 | PASS | 两次 `build_decisions` 逐位一致 |
| T4 | 资产恒等式 nav = 股票 + 现金 | PASS | 逐日成立 |
| T5 | 费用只扣一次（买无印花/卖有/最低佣金） | PASS | `fee_parts` 三项校验 |
| T6 | 不得静默填充缺失行情 | **FAIL** | p04 仍 `pd.Series(arr).ffill()` |
| T7 | SELFTEST 被强制排除 | **FAIL** | 无任何代码对该字段做比较/过滤 |
| T8 | 数据截止拒收 | PASS | 建仓日/终点校验 + 纯函数抛错 |
| T9 | 失败必须留痕 | PASS | 无行情代码返回 `NO_BAR` |
| T10 | 记录含配置与代码哈希 | **FAIL** | 无代码提交哈希 |
| T11 | UNVERIFIED 不得冒充 PASS | PASS | 报告层保留标记 |

**合计：6 PASS / 5 FAIL**；`scripts/sp5/tests` 全部 **70 项**合跑为 **65 passed / 5 failed**——5 个失败**全部**来自本文件新增的规格测试，既有 **59 项无回归**。

**测试自身的两次修正（记录在案，防止"假通过"）**：
- T7 初版用正则匹配 `[.*counts_toward_observation.*]`，误把 `rec['counts_toward_observation']`（字典取值）判为"过滤" → **假通过**；改为要求出现比较运算符后才正确 FAIL。
- T11 初版把汇总行"PASS 3 … UNVERIFIED 1"误判为"同一行既 UNVERIFIED 又 PASS" → **假报**；改为按单元格判定。

---

## 7. 复现命令

```bash
cd research/stock_pick_2026-10-25
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# P0-2 独立账本 + 50 路径
SP5_PATHS=50 $PY -u scripts/sp5/p04b_ledger_independent.py
$PY -u scripts/sp5/p04c_exception_detail.py
$PY -u scripts/sp5/p04d_bias_diagnostics.py
$PY -u scripts/sp5/p04e_attrib_fix.py

# P0-1 复核（两口径 0/5 重合）
$PY -u scripts/sp5/p13c_rule_diff_check.py
$PY -u scripts/sp5/p13d_pool_overlap.py

# P1-1 / P1-3 量化
$PY -u scripts/sp5/p15_cost_alignment.py
$PY -u scripts/sp5/p16_decision_power.py

# §九 11 项行为测试（5 失败为预期：即缺陷证据）
$PY -m pytest scripts/sp5/tests -q
```

---

## 8. 引用规范（本次新增约束）

1. 引用任何 SP-5 持仓/收益结论，须同时写明**价格口径版本**（1-200 或 1-50，两者 0/5 重合）。
2. 引用"成本后"须带**费率档 + 资金规模 + 最低佣金是否绑定**；10 万元本金的往返成本为 **0.252%**（不含价差）。
3. 引用 MDD/波动须注明**含缺口 carry（D2）+ 买入无约束（D3）**。
4. 引用"前瞻"一词时，仅 `p13_forward` 路径合法；`sp4_forward` 含未来信息，**不得**称为前瞻。
5. 引用哈希须写明算法（本审计统一 `sha256(原始字节)`）。
6. `EVIDENCE_INSUFFICIENT_FOR_CAPITAL` 继续有效；本报告**不产生**任何可用于资金决策的证据。

---

*本报告为只读审计结论。所有建议性修改（B2/C1~C6）均未实施，须授权后另开任务执行。*
