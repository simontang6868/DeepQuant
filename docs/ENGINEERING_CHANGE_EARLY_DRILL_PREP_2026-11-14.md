# 12-01 演练「提前准备」工程变更单（DRAFT，待 U-16）

生成时间：2026-11-14 23:0x｜状态：`DRAFT_NOT_AUTHORIZED`
触发：账户持有人回「做这个」，引用上一轮 §四「11-16 能提前做的准备（不越界）」。
配套：`docs/SIM_REBALANCE_DRILL_2026-12-01.md`（方案，DESIGN_ONLY）、`reports/STAGE_CONCLUSION_2026-11-14.md`、`docs/PAPER_AUTH_READBACK_2026-11-14.md` §8。

## 0. 结论先行

**两项只读预检已实跑完成**；本单把「提前实现演练驱动 + 成交回报对账器」的范围、边界、验收项逐条对齐现有代码位置，作为 U-16 到达后的实施依据。

⚠️ **本轮不写实现代码、不接日更链、不执行演练、不报单**。原因不是拖延：方案 §6 明确「授权后第一步才实现，且实现前先写预登记并冻结 sha256」，而 U-16 是**书面授权原文**（用户引用的那段也带条件「如果你现在就给 U-16」），目前尚未给出。把「做这个」解释为演练放行，会重演 R7 泛授权被限定范围的那类错误。

## 1. 本轮已完成的准备（实跑输出）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `preflight_decision_day.py --cloud`（本地发起；云端无该私钥） | **PASS 12｜FAIL 0｜PENDING 1**（目标日 `20261130` 模拟） |
| 2 | `deploy_manifest.py --verify --remote` | **云端 /opt/quant｜commit `40cfb2de`｜47/47 文件逐位比对｜PASS** |

预检明细：C1 日历自校验 8797 条（19901219~20261231）、C2 覆盖 202611、C3 决策日推导 `20261130` 且为 11 月最后一个交易日、C4 数据末日覆盖、C5 目标台账未落盘、C6 定时器会触发、C7 补跑窗口、C8a `p13_forward` 含 `target_holds_d4` 赋值、C9 ADJ 重建+校验链（`--div-source merge`）、C10 快照镜像 10 文件 `all_identical=True`、C11a 本地 47 文件差异 0、C11b 云端 47/47。

⭐ **C8b 是 PENDING，不得提前当 PASS**：`20261130.json` 当日快照由 p17 生成，**11-30 17:10 之后必须重跑本预检**；快照失败不中断链路，台账会沿用最近可用快照 ⇒ 时点证据不新鲜。
⚠️ C4 的 `data_last=20261130` 是**模拟值**，11-30 当天须用真实全池末日重跑。

## 2. 范围与硬边界

| 项 | 内容 |
| --- | --- |
| 允许 | 实现 `sim_drill` 演练驱动、成交回报对账器、gm-paper 外部真值只读源；写单元测试与真变异测试；产物落 `forward/sim_orders/` / `forward/sim_reconcile/`；云端 scp 后**只跑自检** |
| 禁止 | 接入 `daily_chain.sh`、写正式台账、向任何账户发委托、改策略参数 / `p13_forward.py` / sp4 配置 v1·v2、上线整手追加规则、改 `LIVE_ENABLED`、把演练接入 G1/G2/S4 计数 |
| 时点 | 代码可 11-16 起提前完成；**执行日仍是 12-01**（11-30 才有 `mode=FORWARD` + `target_holds_d4` 台账） |

## 3. 组件盘点：复用 vs 新增（逐条对齐方案 §3 五项演练与 §5 通过标准）

| 验收项 | 现有代码位置 | 判断 |
| --- | --- | --- |
| 计划单 / 整手向下取整 / `client_order_id` 纯函数派生 | `execution/planner.py:167 plan_orders` | **复用** |
| 09:35 时点、10:30·14:30 重定价、`MAX_REPRICE=2`、`CARRY` ≤10 日 | `execution/session.py:59/62/82`、`execution/order_loop.py:145/208/305` | **复用**（Phase 2.2-c/d 已补齐） |
| 限速器（瞬时上界 4 笔/秒） | `execution/rate_limit.py` | **复用** |
| 熔断两条件 / 降档 / 人工确认点 | `execution/risk_gate.py:437/566` | **复用判据**；但方案要求「本地注入模拟触发、**不下真单**、不真改 `stock_weight`、不建 v3」⇒ **新增驱动侧注入** |
| 状态机 / `UNKNOWN` / 重复回报同一 `fill_id` | `execution/state_machine.py`、`broker/mock.py` | **复用** |
| 本地台账哈希链 | `execution/ledger.py` | **复用语义**；演练须写**独立** `forward/sim_orders/20261201.jsonl`，不写正式台账 |
| 双向差集对账 | `execution/reconciler.py:302/320` | **复用语义**；但方案要求**外部真值优先**（`get_execution_reports` / `get_unfinished_orders` / `get_positions` / `get_cash`）⇒ **新增 gm-paper 外部真值源** |
| 外部真值源（只读） | `scripts/paper_account_adapter.py` 目前**只有** `get_cash` / `get_positions`，源码明确「绝不发单」 | ⭐ **核心缺口**：需扩展回报/未完结委托查询，且保持零 `order_*` |
| 演练驱动（编排五项演练、停机、证据归档） | **无**（方案 §6 已诚实登记） | ⭐ **新增** |
| 五项演练各 ≥1 次真实记录 | `backend/tests/execution/test_fault_injection.py` 只覆盖单元场景 | **新增驱动产物 + 验收记录** |

⚠️ 按 R2 纪律「判 PASS 前须逐条对齐验收项原文与代码位置」：上表是**实现前的静态盘点**，不声称任何组件已通过演练验收；实现后须逐条重判。

## 4. U-16 到达后的固定实施顺序

1. 逐字存证授权原文（`.md` + `.sha256` 冻结，CRLF=0，UTF-8/LF）。
2. **写预登记**：范围、判据、预期方向 + 机制（落在可算量上）、符号与幅度约定、样本/时点、停机条件；冻结 sha256。
3. 实现（边界见 §2），每次改动留 `.bak.<date>`；不改既有报告原文。
4. 回归四连（`backend tests` → `property_checks` → `scripts/tests` → `verify_exec_all`）＋**真变异测试**：变异必须真改行为、闸门不得调用被测函数、期望值来自产物或独立算术、覆盖 11-30 后场景。
5. scp 云端 + 云端实跑**自检**（不触发报单），manifest 重新核对。
6. **12-01** 才按台账执行；成交回报对账任一差异即停机，不重跑凑通过。

## 5. U-16 可直接复制的书面原文

> 前置条件已全部满足（U-15 授权文件存在且 `--remote` 通道隔离 PASS、11-30 已产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账、11-27 月末 DRYRUN 完成、日历缺口已记录），我书面确认：**开始执行 12-01 仿真月度调仓演练**，范围以 `docs/SIM_REBALANCE_DRILL_2026-12-01.md` 为准；成交回报对账任一差异即停机；五项演练（幂等重复委托／撤单重定价／部分成交／涨跌停／熔断模拟）各至少一次。

若你希望**现在就把代码写了**（仍不接链路、不执行），回一句「按『做这个』授权提前实现」即可：我会把这句原文存证为**工程准备授权**（不是 U-16 演练放行），按 §4 的第 1–2 步冻结后开工。

## 6. 为什么执行日不能提前（简表）

| 阻塞 | 依据 |
| --- | --- |
| 无可执行目标 | 交易日历 2026-11 最后一交易日 = **11-30**；`forward/ledger/` 只有 `20260930` / `20261030`，均带 `LEGACY_NO_D4`；11-16 为 `NOT_DUE` |
| 口径不一致 | 当前影子对账 5 只是 10 月主口径 top5，与 11-30 起 d4 严格口径 top5 **重合 0/5** |
| 授权 | U-15 声明 + 方案写明「11-30 前不向任何账户报单」；U-16 前置含 FORWARD 台账 |
| 代码 | 演练驱动 / 对账器按方案 §6 未实现 |
| 证据价值 | 方案 §5：演练只验执行链路，**不构成样本外证据、不改变 G1/G2/S4** ⇒ 提前演练不加速「能不能赚钱」的结论 |

## 7. 边界声明

本轮**只读**：两项预检 + 代码静态盘点；未改任何运行代码 / 配置 / 策略参数 / 授权文件，未写正式台账，未发委托，未回显凭证值。`LIVE_ENABLED=false` 未变；11-30 前不向任何账户报单的边界未变。既有方案与报告原文均未改写。

---

## 8. 实施记录（EARLY-IMPL-1，2026-11-14 23:03 授权后追加；§0–§7 原文未改写）

**授权**：账户持有人原话「按『做这个』授权提前实现」逐字存证于
`research/stock_pick_2026-10-25/forward/authorization_statements/EARLY_IMPL_statement_2026-11-14.md`
（sha256 `5a99c555fbb37f69dfa96211479ff81882dfc70ad56db30267e3ad9df5b22618`）。
性质为**工程准备授权，不是 U-16**；§0 的 `DRAFT_NOT_AUTHORIZED` 是当时状态，原文保留不改。
**预登记**：`docs/SIM_DRILL_PREP_PREREG_2026-11-14.md`
（sha256 `a6f5a9beb29efa52b2d55a1c67ed21be3c637c56e3c92cb7e11277d4b3db292c`），
在**任何运行代码改动之前**冻结；`sha256sum -c` 复核 OK。

### 8.1 实现清单（严格按 §4 顺序）

| # | 文件 | 内容 |
| --- | --- | --- |
| 1 | `scripts/paper_account_adapter.py`（留 `.bak.20261114`） | 新增 `TRUTH_QUERY_NAMES` / `REDACTED_FIELDS` / `_field_names` / `_as_plain` / `query_names_of` / `read_external_truth` / `build_truth_record`；CLI 加 `--truth-probe` / `--truth-out`。**只读**、零下单调用 |
| 2 | `scripts/sim_reconcile.py`（新） | 纯函数对账器：外部真值优先、双向差集、逐字段、成交累计、重复 `fill_id`、费用、持仓、资金；`NO_INPUTS` 与「无差异」两条独立路径；金额 `Fraction` 独立算术 |
| 3 | `scripts/sim_drill_driver.py`（新） | 五项演练离线编排（`OfflineFixtureBroker`）、`--self-check` 唯一可运行模式、`--execute` 恒 `NOT_AUTHORIZED`、`selftest_` 路径守卫 |
| 4 | `scripts/tests/test_sim_drill_prep.py`（新） | P1–P5 + 硬边界 + SDK 字段核对 + 脱敏 + protobuf 回归，39 条 |
| 5 | `scripts/sim_drill_mutation_test.py`（新） | 11 个真变异，全部「停机/拒绝 → PASS/放行」 |
| 6 | `scripts/deploy_manifest.py`、`scripts/tests/test_paper_account_adapter.py` | 清单纳入 3 个新脚本（47→50）；修既有用例的环境隔离（见 8.3），原文 `.bak.20261114` 保留 |

产物：`forward/sim_orders/selftest_20261114.jsonl`（969 B）、`forward/sim_reconcile/selftest_20261114_driver.json`（2418 B），本地与云端各一份，均 `mode=SELF_CHECK_ONLY`、UTF-8/LF/无 BOM、deepquant 属主。

### 8.2 验收结果（全部实跑）

| 项 | 结果 |
| --- | --- |
| P1 注入差异检出率 | **10/10 = 1.00**（10 类差异各独立 `HALT:*`） |
| P2 空/坏输入停机率 | **4/4 = 1.00**（另 3 类坏输入也全部 `NO_INPUTS`），**无一次判 PASS** |
| P3 真变异 | **11/11** 检出，全部真改行为；无「只换拒绝理由」的假变异 |
| P4 `--execute` 拒绝率 | **100%**（含 `--execute --self-check` 组合），rc=4 |
| P5 五项演练离线记录 | **5/5**，`SELF_CHECK_ONLY`；⚠️ **不等于**方案 §5「五项演练全部完成」 |
| 回归四连 | backend `3651 passed/1 skipped`｜property `9/9 PASS`｜scripts/tests `678 passed/1 skipped`（修测试隔离后复跑同数）｜`verify_exec_all PASS`；数据层两个验收产物 sha256 跑前跑后逐位一致 |
| 云端自检 | `sim_reconcile --self-check` rc=0、`sim_drill_driver --self-check` rc=0、`--execute` rc=4、变异 11/11、新增单测 **39/39**、适配器既有用例 **19/19** |
| 云端外部真值探测 | `--truth-probe` **rc=0**（只读，不落盘）：`cash=1 / positions=0 / execution_reports=0 / unfinished_orders=0`；只打印字段名与条数 |
| 通道隔离 `--remote` | **FAIL 0 项｜PASS**；只读适配器 `order_*(` 远端计数 0 |
| 部署清单 `--verify --remote` | **50/50 文件逐位一致｜PASS** |

### 8.3 两处真实发现（按「不删失败」保留，均为修 bug 而非新实验）

1. ⭐ **`--truth-probe` 首轮 rc=2**：gmtrade 的 `ExecRpt`/`Order` 是 protobuf 对象，`dir()` 里的 `Extensions` 属性访问抛 `AttributeError`。`_as_plain` 原实现遍历 `dir()` ⇒ 失败关闭生效但读不到字段。修为按 `DESCRIPTOR.fields` 取业务字段（`_field_names`），并加 protobuf 形态回归测试。
   **这正好命中预登记结构假设 4**「离线 fixture 与 SDK 真实协议不保证一致；自检 PASS 不证明 SDK 契约」—— 所以自检全绿不等于端到端可用，必须云端实跑。
   另一实证：`FIELD_MAP` 与真实 `ExecRpt` 字段对得上（`cl_ord_id/symbol/side/volume/price/commission/exec_id/exec_type`）；但 `status` 在成交回报里实际取 **`exec_type`（执行类型，非订单终态）**，其与本地状态编码的语义对齐须 12-01 用真实回报确认。
2. ⭐ **脱敏**：`ExecRpt`/`Order` 带 `account_id` / `account_name` / `strategy_id`，原样落盘会把账户标识写进证据文件。加 `REDACTED_FIELDS`（值替 `<redacted>`，`fields_seen` 只留字段名）与对应测试。
3. **云端既有用例首轮 2 FAIL**：`test_paper_account_adapter.py` 两条断言 `not paa.OUT.exists()`，而云端保留 R7 迁移时冻结的 `account_snapshot.json`（2026-11-09 17:15、142 B、sha `fcaeec0d…`），本地仓库没有 ⇒ 恒假。改动前后 `OUT` 定义与 `write_snapshot` 语义**逐字一致**，证明非本轮回归。修为注入 `tmp_path` 的 `OUT`（判据等价「失败关闭时不写旧单文件」，环境无关，**不是弱化测试**），原文 `.bak.20261114` 保留。复跑云端 19/19。

### 8.4 硬边界复核（逐条）

`daily_chain.sh` 相对 HEAD **零改动**｜`LIVE_ENABLED` 云端未显式配置（隔离检查判「须为 false/未设置」PASS）｜正式台账 `forward/ledger/` 仍只有 `20260930.json` / `20261030.json` 两份 `LEGACY_NO_D4`｜自检产物只在 `sim_orders/` / `sim_reconcile/` 且带 `selftest_` 前缀｜三个脚本 `order_*(` 命中 **0**｜未改策略参数 / `p13_forward.py` / `sp4_config_v1·v2` / 授权文件｜未发任何委托、未回显凭证值、未接 G1/G2/S4。

### 8.5 未完成（12-01 前必须闭环，本单不解锁执行）

| # | 事项 |
| --- | --- |
| 1 | 11-30 17:10 产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账；11-27 月末 DRYRUN；11-30 后重跑 `preflight_decision_day.py --cloud`（C8b 当日快照 PENDING，不得提前当 PASS） |
| 2 | **U-16 书面放行**（§5 原文），且须**另立执行预登记** |
| 3 | ⭐ gm-paper **真实报单适配器仍未实现**（本单只有只读真值源 + 离线 fixture broker）；实现前须新预登记 |
| 4 | 真实 `ExecRpt` 的 `exec_type` ↔ 本地 `status` 编码对齐；空回报在 12-01 执行场景必然 `NO_INPUTS` 停机（设计内，不是失败） |
| 5 | 费用下限「不足 5 元按 5 元」仍需方案 §四.1 的一笔约 4,000 元实测 |
| 6 | D3（两机 G1 合并口径）、logrotate 备份文件修复：均需另行授权 |

### 8.6 引用规范

引用本轮结果必须同时给出：预登记 sha256 `a6f5a9be…` + 授权存证 sha256 `5a99c555…` + 产物目录
`research/stock_pick_2026-10-25/forward/sim_orders/` 与 `sim_reconcile/` +
**`StatisticalVerdict = ENGINEERING_SELF_CHECK_ONLY`**（不是 `QUOTABLE`）+
**`MaturityLevel = DESIGN_VALIDATED_OFFLINE`**。
本轮**不构成样本外证据、不改变 G1/G2/S4、不得用于经济性判断**（方案 §五、§四.3）。
