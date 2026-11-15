# 12-01 演练「提前实现」交付结果（EARLY-IMPL-1）

**2026-11-14**｜状态：`EARLY_IMPL_COMPLETED`｜`EXECUTION_NOT_AUTHORIZED`｜`LIVE_ENABLED=false`
**授权存证**：`research/stock_pick_2026-10-25/forward/authorization_statements/EARLY_IMPL_statement_2026-11-14.md`（sha256 `5a99c555fbb3…`）
**预登记（代码前冻结）**：`docs/SIM_DRILL_PREP_PREREG_2026-11-14.md`（sha256 `a6f5a9beb29e…`）
**实施明细**：`docs/ENGINEERING_CHANGE_EARLY_DRILL_PREP_2026-11-14.md` §8

---

## 一句话结论

**代码提前写完了、回归和真变异全过、云端也实跑通了；但仍然不能报单。** 你给的是「提前实现」授权，不是 12-01 演练放行。现在系统多了三样能力：能只读拿仿真账户的成交回报、能按外部真值做逐笔对账、能用离线夹具把五项演练的判据跑通。**执行日仍是 12-01**。

## 做了什么（严格按变更单 §4 的顺序）

1. 把「按『做这个』授权提前实现」**逐字存证**并冻结 sha256；
2. **先写预登记并冻结 sha256**，之后才动第一行代码（范围、P1–P5 可算量判据、结构假设、停机条件、明确不做的事）；
3. 实现 3 个模块 + 1 组单测 + 1 组真变异：
   - `scripts/paper_account_adapter.py`：新增 `--truth-probe` / `--truth-out`，只读拉资金 / 持仓 / 成交回报 / 未完结委托；
   - `scripts/sim_reconcile.py`：外部真值优先的成交回报对账器，空输入 ⇒ `NO_INPUTS` 停机，任一差异 ⇒ `HALT:*`；
   - `scripts/sim_drill_driver.py`：五项演练离线编排，`--self-check` 是唯一可运行模式，`--execute` 恒返回 `NOT_AUTHORIZED`；
4. 回归四连 + 真变异；
5. scp 云端，**只跑自检**，并重新核对部署清单。

## 验收结果

| 项 | 结果 |
| --- | --- |
| P1 注入差异检出 | **10/10 = 1.00**（10 类差异各独立停机） |
| P2 空/坏输入停机 | **4/4 = 1.00**，无一次误判 PASS |
| P3 真变异 | **11/11** 检出，全部真改行为（停机/拒绝 → PASS/放行） |
| P4 `--execute` 拒绝 | **100%**，rc=4（含与 `--self-check` 同传） |
| P5 五项演练 | **5/5 离线记录**，⚠️ 不等于方案 §5「五项演练全部完成」 |
| 回归四连 | `3651/1`｜`9/9`｜`678/1`｜`verify_exec_all PASS`；数据层验收产物哈希跑前跑后逐位一致 |
| 云端自检 | 对账器 rc=0、驱动 rc=0、`--execute` rc=4、变异 11/11、单测 **39/39**、适配器既有用例 **19/19** |
| 云端真值探测 | `--truth-probe` **rc=0**（只读不落盘）：cash 字段齐全、持仓/回报/未完结委托均为 0 |
| 通道隔离 `--remote` | **FAIL 0 项｜PASS**，`order_*(` 远端计数 0 |
| 部署清单 `--verify --remote` | **50/50 文件逐位一致｜PASS**（47→50，纳入 3 个新脚本） |

产物：`forward/sim_orders/selftest_20261114.jsonl`、`forward/sim_reconcile/selftest_20261114_driver.json`，本地与云端各一份，均 `mode=SELF_CHECK_ONLY`、LF、无 BOM。

## 两件值得单独说的事（都是真发现，已修，原文与失败记录保留）

- ⭐ **离线自检全绿 ≠ 端到端能用**：云端首跑 `--truth-probe` 是 **rc=2**。gmtrade 的 `ExecRpt`/`Order` 是 protobuf 对象，`dir()` 里的 `Extensions` 属性一读就抛 `AttributeError`。已改成按 `DESCRIPTOR.fields` 读业务字段并补了回归测试，重跑 rc=0。这恰好预登记里写的「结构假设 4」被验证：**自检 PASS 只证明判据与编排，不证明 SDK 契约**。
- ⭐ **SDK 对象带身份字段**：`account_id` / `account_name` / `strategy_id` 会出现在回报里，原样落盘会写进证据文件。已加脱敏（值替 `<redacted>`，只留字段名供诊断）。
- 另外云端既有适配器用例首轮有 2 条 FAIL：云端保留 R7 迁移冻结的 `account_snapshot.json`（11-09 17:15、142 B），本地没有，旧断言 `not OUT.exists()` 因而恒假。改动前后 `OUT` 定义逐字一致 ⇒ 非本轮回归；改成注入临时 `OUT`（判据等价、不弱化），原文备份保留，复跑 19/19。

## 硬边界（逐条复核过）

`daily_chain.sh` 相对 HEAD **零改动**｜`LIVE_ENABLED` 未设置（隔离检查 PASS）｜正式台账仍只有 `20260930` / `20261030` 两份 `LEGACY_NO_D4`｜自检产物只在 `sim_orders/` / `sim_reconcile/` 且带 `selftest_` 前缀｜三个脚本零 `order_*(` 调用｜未改策略参数 / `p13_forward.py` / sp4 配置 / 授权文件｜**未发任何委托**、未回显凭证、未接 G1/G2/S4。

## 12-01 之前还缺什么

1. 11-30 17:10 产出 `mode=FORWARD` + `target_holds_d4` 台账；11-27 月末 DRYRUN；11-30 后重跑决策日预检（C8b 当日快照是 PENDING，不能提前当 PASS）。
2. **U-16 书面放行**（变更单 §5 有可复制原文），且须另立执行预登记。
3. ⭐ **gm-paper 真实报单适配器仍未实现** —— 本单只有只读真值源和离线 fixture broker，这是刻意留的边界。
4. 真实回报的 `exec_type` 与本地 `status` 编码对齐；「不足 5 元按 5 元」的费用下限仍需方案 §四.1 那笔约 4,000 元实测。
5. 空成交回报在 12-01 执行场景会判 `NO_INPUTS` 停机 —— 这是设计内的失败关闭，不是缺陷。

## 引用规范

引用本轮结果须同时给出：预登记 sha256 `a6f5a9be…` + 授权存证 sha256 `5a99c555…` + 产物目录 +
**`StatisticalVerdict = ENGINEERING_SELF_CHECK_ONLY`**（不是 `QUOTABLE`）+ **`MaturityLevel = DESIGN_VALIDATED_OFFLINE`**。
本轮**不构成样本外证据、不改变 G1/G2/S4、不得用于「能不能赚钱」的任何判断**。
