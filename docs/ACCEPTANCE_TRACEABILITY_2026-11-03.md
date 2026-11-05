# 验收可追溯表（Phase 1 / Phase 2）

**生成时间**：2026-11-03
**口径**：每条验收条款 → 对应测试 ID → 三态判定（`PASS` / `PASS_WITH_GAPS` / `NOT_IMPLEMENTED`）。
**最后更新**：2026-11-05（2.2-c / 2.2-d 由 `NOT_IMPLEMENTED` 改判 `PASS`，依据见该两行）。
**重判原则**：**只看条款原文是否被代码实现，不看测试是否全绿。** 2.2 的教训是测试全绿但调度部分根本没实现。

> 全量测试清单：`var/test_inventory_backend.txt`（344 条）｜`var/test_inventory_sp5.txt`（206 条）。
> 下表只列**能作为该条款证据**的测试，不是全部测试。

---

## 1. Phase 1（调度可靠性）

| 条款 | 要求摘要 | 判定 | 证据（测试 ID，省略 `tests/`） |
| --- | --- | --- | --- |
| 1.3-a | `--dry-run` 不写任何文件 | **PASS** | `sp5/tests/test_phase1_forward_hardening.py::test_dry_run_writes_nothing`、`::test_dry_run_on_stale_data_also_writes_nothing` |
| 1.3-b | 台账不可覆盖；`--force` 先备份 + 更正日志 | **PASS** | `::test_ledger_immutable_refuses_second_run`、`::test_force_backs_up_and_appends_correction` |
| 1.3-c | 补跑显式标记，不得用决策日之后价格算信号 | **PASS** | `::test_late_run_is_marked_and_counted`、`::test_late_run_flag_rejected_when_not_late` |
| 1.3-d | 决策日判定与文档一致 | **PASS（今日重写）** | `test_month_end_calendar.py` 全 28 条；⚠️ 契约已改为「决策日来自独立日历」，见 §3 |
| 1.3-e | 次日未知时 `PENDING_NO_CALENDAR` | **PASS** | `::test_next_td_unknown_is_pending_not_judged_on_decision_day` |
| 1.3-f | 台账记录代码来源三字段 | **PASS** | `::test_record_carries_provenance_and_data_last_date` |
| 1.3-g | 每次运行写心跳 | **PASS** | `::test_heartbeat_written_on_success`、`::test_heartbeat_written_on_stale_abort` |
| 1.3-h | `--verify` 只读自检 | **PASS** | `::test_verify_detects_tampered_ledger`（及 `p13_forward.py --verify` 实跑） |
| 1.3-i | `p13b` 的 selftest 从台账推导 | **PASS** | `::test_p13b_derives_selftest_from_ledger`、`test_p13b_aborts_on_selftest_mismatch` |
| **G1** | 连续 10 个交易日零漏跑 | **NOT PASSED** | 无云主机，判据无法验证。见 `STATUS.md` §3 |

---

## 2. Phase 2（执行层）

### 2.1 BrokerAdapter + MockBroker

| 条款 | 判定 | 证据（`backend/tests/broker/test_broker_adapter.py`） |
| --- | --- | --- |
| 2.1-a 8 项能力齐全 | **PASS** | `test_adapter_declares_all_eight_capabilities`、`test_mock_implements_every_capability` |
| 2.1-b 无券商专有词 | **PASS** | `test_no_vendor_vocabulary_in_adapter_layer`、`test_code_token_scanner_ignores_prose_but_catches_real_use` |
| 2.1-c 六类故障可注入 | **PASS** | `test_fault_reject_carries_reason_code`、`test_fault_partial_fill_stays_partially_filled`、`test_fault_dropped_fill_leaves_order_unknown_but_position_moved`、`test_fault_duplicate_fill_reuses_same_fill_id`、`test_fault_timeout_raises_and_leaves_unknown_order`、`test_fault_stale_quote_is_flagged_not_zeroed` |
| 2.1-d 初始状态显式给定 | **PASS** | `test_initial_state_is_explicit_not_defaulted` |
| 2.1-e 可重放逐位一致 | **PASS** | `test_replay_is_bitwise_identical` |

### 2.2 订单规划

| 条款 | 判定 | 证据 |
| --- | --- | --- |
| 2.2-a 先卖后买 + 整手 | **PASS** | `test_planner.py::test_sell_before_buy_on_rebalance`、`::test_rounds_target_shares_down_to_whole_lot` |
| 2.2-b `client_order_id` 幂等无时钟 | **PASS** | `::test_client_order_id_is_deterministic`、`::test_client_order_id_contains_no_clock_or_randomness` |
| **2.2-c 执行时点/重定价/顺延** | **PASS**（2026-11-05 补实现） | ✅ `execution/session.py` 时段闸（`phase_of` / `is_trading_time`，含午休与 14:57 收盘竞价）+ `execution/order_loop.py` 发单循环；09:35 前/午休/收盘竞价一概不发；10:30、14:30 两个时点；收盘未成交 → `CARRY`，顺延 **10 个交易日**（`MAX_CARRY_DAYS`，只认交易日序列，不按自然日）。测试：`test_session.py` 44 条 + `test_order_loop.py` 31 条；变异测试 **11/11 被检出**（`scripts/session_mutation_test.py`）。端到端纸面演练：`scripts/dryrun_decision_day.py` |
| **2.2-d 重定价次数上限 2** | **PASS**（2026-11-05 补实现） | ✅ `OrderTracker.reprice_count` 跨日累计**不重置**；第 3 次 `raise RepriceNotAllowed`（**硬失败，不静默跳过**——跳过会让账本显示"没超"）。口径歧义已在 `session.py` docstring 记录：2.2-c「各最多一次」与 2.2-d「上限 2」按**更严格的累计口径**实现。变异 M4（改成静默 `return`）与 M1（上限放宽到 3）均被检出 |
| 2.2-e 不假定卖出回款 | **PASS** | `::test_buy_budget_excludes_pending_sale_proceeds` |

⇒ **本节整体：`PASS_WITH_GAPS`**（3/5 实现，2 条未实现）

### 2.3 RiskGate 11 条硬限

| 条款 | 判定 | 证据（`test_risk_gate.py`） |
| --- | --- | --- |
| 2.3-a 逐条 + 两个边界 | **PASS** | 11 条各有 `*_rejects` 与 `*_passes`/边界用例；`test_baseline_passes_every_check` |
| 2.3-b 顺序固定 + 原因码 | **PASS** | `test_check_order_is_fixed_and_documented`、`test_order_rejected_requires_typed_reason_code` |
| 2.3-c 缺输入即拒（P4） | **PASS** | `test_missing_authorization_rejects`、`test_missing_quote_rejects`、`test_missing_equity_rejects`、`test_missing_position_value_rejects`、`test_missing_trading_day_rejects`（7 字段参数化） |
| 2.3-d 变异测试 ≥6 | **PASS** | `test_risk_gate_mutation.py` 9 个变异体 + `test_mutation_anchors_all_exist` |

### 2.4 状态机与恢复

| 条款 | 判定 | 证据（`test_state_machine.py`） |
| --- | --- | --- |
| 2.4-a `UNKNOWN` 阻断新单 | **PASS** | `test_unknown_is_blocking_and_others_are_not`、`test_registry_reports_blocking_when_any_unknown` |
| 2.4-b 非法转移抛异常 | **PASS** | `test_illegal_transition_raises_instead_of_returning_false`、`test_terminal_state_cannot_go_back_to_partial_fill` |
| 2.4-c 重启以券商为准 | **PASS** | `test_rebuild_takes_broker_positions_not_local_cache` |
| 2.4-d 重建只读 | **PASS** | `test_readonly_broker_blocks_place_and_cancel`、`test_readonly_broker_records_only_reads` |

### 2.5 日终对账 ｜ 2.6 台账 ｜ 2.7 告警 ｜ 2.8 故障注入

| 条款 | 判定 | 证据 |
| --- | --- | --- |
| 2.5-a 四对象任一差异即停 | **PASS** | `test_reconciler.py::test_identical_state_reconciles_clean` 等 30 条 |
| 2.5-b 双向（含券商有本地无） | **PASS** | `::test_broker_only_position_is_reported`、`::test_local_only_position_is_reported` |
| 2.5-c 落到字段与差值 | **PASS** | `::test_position_quantity_mismatch_reports_exact_delta` |
| 2.5-d 股数无容差 / 金额 1 分 | **PASS** | `::test_share_counts_have_no_tolerance`、`::test_money_tolerance_uses_integer_cents_not_float_compare` |
| 2.6-a 决策快照含数据哈希 | **PASS** | `test_ledger.py::test_decision_snapshot_requires_data_hash` |
| 2.6-b 成本只能来自 `compute_fees` | **PASS** | `::test_ledger_does_not_compute_fees_itself`、`::test_ledger_source_contains_no_fee_rate_constants` |
| 2.6-c 只追加 | **PASS** | `::test_appending_does_not_change_any_existing_byte` |
| 2.7-a 五类告警各有测试 | **PASS** | `test_alerts.py` 30 条（心跳/数据/订单/对账/回撤） |
| 2.7-b 不发邮件 | **PASS** | `::test_alerts_module_has_no_mail_capability`、`::test_mail_scanner_can_actually_catch_smtplib` |
| 2.8-a 11 项各一个测试 | **PASS** | `test_fault_injection.py::test_01`…`test_11` |
| 2.8-b 断言具体后果 | **PASS** | 每项断言原因码/状态/数值/告警级 |
| 2.8-c 配置篡改是运行时校验 | **PASS** | `test_11_config_tampered`、`test_11b_config_tamper_is_detected_even_without_change_in_size`、`test_11c_deleted_config_is_a_tamper_not_a_crash`（含删文件、重新格式化、改 1 字符） |

### 2.9 SHADOW ｜ 2.10 申报材料

| 条款 | 判定 | 证据 / 原因 |
| --- | --- | --- |
| 2.9-a 生成不发送 + 差异可解释 | **PASS** | `test_shadow.py::test_shadow_never_receives_a_broker`、`::test_shadow_module_does_not_call_place_order`、6 类 `*_is_explained` |
| 2.9-b 跑满 20 个交易日 | **NOT_IMPLEMENTED** | 缺运行机器。机制已交付，交易日数为 0 |
| 2.10-a 只准备草稿不提交 | **PASS** | `docs/reporting/` 三份材料 + `00_README.md`；无任何提交动作 |
| 2.10-b 数字与实际实现一致 | **PASS** | `test_execution_config.py` 16 条（配置↔代码一致性 + 描述实测） |

⇒ **2.9 整体：`PASS_WITH_GAPS`**（机制 PASS，20 日 NOT_IMPLEMENTED；该条缺时间是必然的，G2 判定期在 12-01 前后）
⇒ **2.2 整体：`PASS_WITH_GAPS`**

---

## 3. 今日新增：决策日契约变更（影响 1.3-d）

`latest_closed_month()` 的语义从「数据末日」改为「最近一个已完结月的最后一个交易日（来自独立日历）」。
原因：日更链下数据末日等于今天，旧语义会返回一个**非月末**的决策日。

| 新契约 | 测试 |
| --- | --- |
| 跨月 | `test_cross_month_october` |
| 跨年 | `test_cross_year_december_to_january` |
| 停牌月末 | `test_month_end_suspension_does_not_pin_decision_day` |
| 缺数据月末 | `test_missing_last_day_data_is_rejected` |
| 日历缺失/被截断/被篡改/过旧 | `test_calendar_missing_fails_closed`、`test_calendar_truncated_mid_month_is_not_covered`、`test_calendar_tamper_is_detected`、`test_calendar_staleness_is_detected` |
| 全池扫描（不含抽样钉死） | `test_tail_scan_sees_dates_beyond_first_40_files` |
| 日更链入口 `--if-due` | `test_if_due_*`（4 条） |

---

## 4. 未实现项的实施清单（按依赖排序）

| # | 项 | 依赖 | 完成判据 |
| --- | --- | --- | --- |
| 1 | 09:35 发单时点 | 发单循环（Phase 3） | 有调度器且能断言「09:35 前不发」 |
| 2 | 10:30 / 14:30 重定价 | 同上 + 盘口或对手价来源 | 每次重定价产生 `-r1`/`-r2` 新 id，次数可数 |
| 3 | 收盘未成交顺延 ≤10 交易日 | 同上 + 日切状态机 | 跨日保留未成交并计数，超 10 日置 `FAILED` |
| 4 | 重定价次数上限 2 | 同上 | 第 3 次重定价被拒且原因码可检索 |
| 5 | 限速器接入发单循环 | 同上 | 申报速率从「设计上限」变「运行时保证」 |

> 这 5 项完成前，`docs/reporting/` 的材料**不得提交**（当前判 `DRAFTED_NOT_SUBMITTABLE`）。

---

## 5. 总判定

| 阶段 | 判定 | 说明 |
| --- | --- | --- |
| Phase 1 | `PASS_WITH_GAPS` | 1.3 的 **9 条全部 PASS**；**G1 未通过**（缺机器） |
| Phase 2 | `PASS_WITH_GAPS` | **33/34 条款 PASS**（2026-11-05：2.2-c / 2.2-d 补实现并过变异测试）；仅 2.9-b（跑满 20 个交易日）未实现——它缺的是**时间**不是代码 |
| G1 / G2 | `NOT_PASSED` | 两者同卡「没有一台每天醒着的机器」 |
