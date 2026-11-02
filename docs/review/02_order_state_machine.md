# 审查包 2 · 委托状态机与崩溃重建

## 1. 源码与接口

| 项 | 内容 |
| --- | --- |
| 源码 | `backend/src/deepquant/execution/state_machine.py` |
| 测试 | `backend/tests/execution/test_state_machine.py`（**30**）、`test_state_machine_mutation.py`（**11**） |
| 入口 | `OrderStateMachine.apply(BrokerOrder) -> BrokerOrder`、`rebuild_from_broker(adapter) -> RebuiltState` |
| 关键常量 | `TRANSITIONS`（状态转移表）、`TERMINAL_STATUSES` |
| 只读包装 | `ReadOnlyBroker`（白名单 + 显式拦截） |

## 2. 三层保护（这是本模块最值得看的地方）

终态不可回退由**三层**同时守着：

1. **显式检查** `if old.status in TERMINAL_STATUSES: raise`
2. **转移表** `TRANSITIONS[FILLED] = frozenset()`（无出边）
3. **数量单调** `filled_shares` 不得减少

写变异体时先做「FILLED(200) → PARTIALLY_FILLED(100)」，结果被第 3 层拦住 —— 这反而**证明了第 3 层在生效**。
因此另加 `test_M1b`：**只**删第 1 层，行为不变（由第 2 层接管）。这条测试记录的是"这里是三层"这个事实本身，
避免以后有人"清理冗余代码"时把三层一起删掉。

## 3. 测试清单（关键）

| 断言 | 测试 |
| --- | --- |
| 非法转移抛异常（不是返回 False） | `test_illegal_transition_raises_instead_of_returning_false` |
| 终态不可回退 | `test_terminal_state_cannot_go_back_to_partial_fill`、`test_cancelled_cannot_become_filled` |
| 已成数量不可减少 | `test_filled_shares_cannot_decrease` |
| 身份字段不可改 | `test_identity_fields_cannot_be_mutated` |
| `UNKNOWN` 阻断新单 | `test_unknown_is_blocking_and_others_are_not`、`test_registry_reports_blocking_when_any_unknown` |
| 转移表覆盖全部状态 | `test_transition_table_covers_every_status`、`test_terminal_states_have_no_outgoing_transitions` |
| 重复终态回报幂等 | `test_duplicate_terminal_report_is_idempotent_not_error` |
| 重建以券商为准 | `test_rebuild_takes_broker_positions_not_local_cache`、`test_rebuild_adopts_order_that_local_never_saw` |
| 重建不删本地多记 | `test_rebuild_reports_local_only_order_without_deleting_it` |
| 重建可覆盖本地乐观状态 | `test_rebuild_can_override_local_over_optimism` |
| 重建只读 | `test_readonly_broker_blocks_place_and_cancel`、`test_readonly_broker_rejects_methods_outside_whitelist`、`test_readonly_broker_records_only_reads` |

## 4. 已知缺陷 / 未覆盖

| # | 内容 | 影响 |
| --- | --- | --- |
| D-1 | `UNKNOWN` **只能由券商数据解封**，但没有实现"主动查证"（例如定时向券商补查该委托） | 中。丢回报后可能长期无法自动恢复，需要人工介入 |
| D-2 | 重建**允许状态倒退**（本地虚报成交比漏报危险），但只有 `Override` 记录，**没有人工确认流程** | 中。倒退事件应触发告警而非仅落盘 |
| D-3 | 未测"券商返回同一 `client_order_id` 但内容不同"的对抗场景（`broker/mock.py` 有 `test_same_id_different_content_is_an_incident`，状态机侧未覆盖） | 低-中 |

## 5. 变异测试结果（11 个）

| 变异 | 内容 | 结果 |
| --- | --- | --- |
| M1 | 删终态检查 + 给终态加出边 | 抓到 |
| M1b | **只**删显式检查 | 行为不变（记录三层保护） |
| M2 | 本地可解封 `UNKNOWN` | 抓到 |
| M3 | 已成股数可减少 | 抓到 |
| M4 | 重建时重置峰值权益 | 抓到 |
| M5 | 只读守卫放行下单 | 抓到 |
| M6 | 身份字段可改 | 抓到 |
| M7 | 逻辑时钟可倒退 | 抓到 |
| M8 | `is_blocking` 恒 False | 抓到 |
| M9 | 丢"券商有本地无" | 抓到 |
| — | 锚点自检 | 每个变异锚点须在源码恰好出现一次 |

## 6. 建议重点质疑

1. 「三层保护」是否有一层永远不可能被单独触发（即死代码）？如果是，保留的理由是否只是"可读性"？
2. `UNKNOWN` 无法自动解封，会不会变成"一旦丢一次回报就永久停摆"？需要什么兜底？
3. 重建允许倒退——**允许的边界在哪**？如果券商侧数据本身是错的重建会把错放大。
