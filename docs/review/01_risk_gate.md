# 审查包 1 · RiskGate 硬限闸门

## 1. 源码与接口

| 项 | 内容 |
| --- | --- |
| 源码 | `backend/src/deepquant/execution/risk_gate.py` |
| 测试 | `backend/tests/execution/test_risk_gate.py`（**67**）、`test_risk_gate_mutation.py`（**10**，含 9 个变异体 + 1 个锚点自检） |
| 入口 | `RiskGate.evaluate(ctx: OrderContext) -> Decision` |
| 状态 | `GateState`（峰值权益、熔断标志、当日笔数、`has_unknown_orders`、交易日），可 `save/load` 落盘 |
| 授权 | `Authorization`（金额上限、板块白名单、有效期），**只有 `load`，没有任何写方法** |

## 2. 12 条检查（顺序即规则）

`halt_switch → breaker → authorization → reconciliation → unknown_orders → trading_hours → blacklist → quote → price_deviation → position_limit → order_notional → daily_count`

停在**第一个**失败，`Decision.checked` 记录已通过的检查名。

> 顺序为什么重要：已熔断的系统不该继续讨论"单笔金额是否超限"——那会让人以为改小金额就能通过。
> 测试 `test_check_order_is_fixed_and_documented` 同时触发停机与黑名单，断言返回 `HALT_SWITCH` 且 `checked == ()`。

## 3. 测试清单（按条款）

| 条款 | 关键测试 |
| --- | --- |
| 逐条两个边界 | `test_order_notional_at_limit_passes` / `test_order_notional_rejects`、`test_notional_exactly_at_authorization_passes` / `test_notional_over_authorization_rejects` |
| 顺序固定 | `test_check_order_is_fixed_and_documented` |
| 缺输入即拒 | `test_missing_authorization_rejects`、`test_missing_quote_rejects`、`test_missing_equity_rejects`、`test_missing_position_value_rejects`、`test_missing_trading_day_rejects` |
| 熔断跨重启 | `test_breaker_still_trips_after_restart`、`test_tripped_breaker_persists`、`test_resume_does_not_reset_breaker` |
| 授权边界 | `test_authorization_last_valid_day_passes`、`test_expired_authorization_rejects` |

## 4. 已知缺陷 / 未覆盖

| # | 内容 | 影响 |
| --- | --- | --- |
| D-1 | **第 1 条"授权文件金额上限"在 LIVE 未启用时无法端到端验证**：没有真实授权文件，只测了 `load` 的解析与边界 | 中。首次实盘前必须用真实文件跑一次 |
| D-2 | 「未对账即拒」只验证"上一轮对账未通过"，**不验证"对账发生在何时"** | 中。若进程整天不跑对账，检查会一直用旧结论 |
| D-3 | 峰值权益按"策略仓"而非"总资产"计；若资金划入划出，回撤口径会漂移 | 低-中。当前资金固定，暂不触发 |
| D-4 | 12 条之外的**速率限制不在本模块**（在 `rate_limit.py`，尚未接入发单循环） | 高。见 `docs/ACCEPTANCE_TRACEABILITY_2026-11-03.md` §4 |

## 5. 变异测试结果

9 个变异体全部被抓到；另有 `test_mutation_anchors_all_exist` 断言**每个变异锚点在源码中恰好出现一次**。

| 变异 | 内容 | 结果 |
| --- | --- | --- |
| M1 | 删掉熔断检查 | 抓到 |
| M2 | 黑名单永远返回通过 | 抓到 |
| M3 | 单票上限 `>` 改 `>=` | 抓到 |
| M4 | 价格偏离阈值放宽 10 倍 | 抓到 |
| M5 | 缺行情时跳过检查 | **抓到（但整体仍拒）** |
| M6 | 对账判断 `is not True` 改 `is False` | 抓到 |
| M7 | 日笔数 `>=` 改 `>` | 抓到 |
| M8 | 熔断 `>` 改 `>=` | 抓到 |
| M9 | 峰值不落盘 | 抓到 |

### ⭐ M5 揭示的一件事（请重点看）

跳过 `quote` 检查后，**整体仍然是拒单**——因为 `price_deviation` 单独实现了"没有最新价就拒"。
这是**防御纵深**，不是重复代码。因此 M5 的判据不是"整体放行"，而是"`quote` 不出现在 `checked` 里、
拦截责任由 `price_deviation` 接过"。

> 审查建议：请确认这种"双闸"是否会在别处产生**语义分歧**（比如一处允许 0.3% 让价、另一处按 1% 判）。

## 6. 建议重点质疑

1. `MISSING_INPUT` 一律拒单，会不会在**行情源短暂抖动**时把当天全废掉？这是有意的，但请判断代价是否可接受。
2. 熔断线 −2.5%（策略仓口径）相对**月度**调仓是否过紧——一次正常的日内波动可能触发。
3. `Authorization` 只读，但文件本身可被替换；是否应加"授权文件哈希进台账"。
