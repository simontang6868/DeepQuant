# 审查包 5 · 日终对账

## 1. 源码与接口

| 项 | 内容 |
| --- | --- |
| 源码 | `backend/src/deepquant/execution/reconciler.py` |
| 测试 | `backend/tests/execution/test_reconciler.py`（**30**） |
| 入口 | `reconcile_with_broker(adapter, local_*) -> ReconcileResult` |
| 结构 | `Difference(kind, code, field, local, broker, delta)`；`ReconcileResult.diffs` / `.ok` |

## 2. 四条要求与实现

| 要求 | 实现 |
| --- | --- |
| 四对象：持仓、资金、成交、委托 | 各一段，逐字段比 |
| **双向**：「本地有券商无」与「券商有本地无」 | `MISSING_LOCAL` / `MISSING_BROKER` 两类分别产出 |
| 差异落到字段与差值 | `Difference.field` 不得为空（空则抛异常） |
| 容差：股数无、金额 1 分 | 股数**完全相等**；金额用**整数分**比较 |

### 2.1 容差方向为什么是"整数分"而不是 `abs(a-b) <= 0.01`

`100.01 - 100.00 = 0.010000000000000009`，用浮点比较会把**恰好 1 分**判成超差。
改成 `round(a*100) == round(b*100)` 后边界才稳定（`test_money_tolerance_uses_integer_cents_not_float_compare`）。

### 2.2 资金必须查三个字段

只比 `total` 会漏掉"冻结没释放"：总额对、可用不对，实盘就是买不了。
`test_cash_swap_between_available_and_frozen_is_caught`。

### 2.3 重复回报先**去重**再比

不去重会造出假差异（同一笔成交在两边出现两次，数量差一倍）。但重复本身另报 WARN，不静默吞掉。

## 3. 测试清单（关键）

| 断言 | 测试 |
| --- | --- |
| 一致时无差异 | `test_identical_state_reconciles_clean` |
| 双向缺失 | `test_broker_only_position_is_reported`、`test_local_only_position_is_reported` |
| 数量差给精确 delta | `test_position_quantity_mismatch_reports_exact_delta` |
| available 与 quantity 分开报 | `test_position_available_mismatch_is_separate_from_quantity` |
| 股数无容差 | `test_share_counts_have_no_tolerance` |
| 金额 1 分内视为舍入 | `test_money_rounding_within_one_cent_is_ignored`、`test_money_rounding_over_one_cent_is_reported` |
| 委托状态不符 | `test_order_status_mismatch_is_reported` |

## 4. 已知缺陷 / 未覆盖

| # | 内容 | 影响 |
| --- | --- | --- |
| D-1 | **对账通过不自动开门**（`apply_reconcile_to_gate_state` 默认 `clear=False`）：有差异就关，无差异则**不变** | 有意设计，但意味着"差异消失"需要人工确认，运维上容易被忽略 |
| D-2 | 成交价容差用"金额 1 分"，**未考虑券商可能给 6 位小数并四舍五入到分** | 低。可能产生 1 分的持续假差异 |
| D-3 | 未做**三方对账**（券商 / 本地 / 交易所回报），仅两方 | 中。若券商与本地同时错，对账会通过 |
| D-4 | 对账本身用 `ReadOnlyBroker` 包装（对账不改变被对账对象），但**未测"包装被绕过"** | 低。异常路径已有白名单测试 |

## 5. 建议重点质疑

1. D-3 是本模块最大的结构性问题：**两方对账无法发现"两边都错"**。是否值得引入第三方（对账单文件/邮件）？
2. 金额整数分比较在**汇率/港股/美股**上不成立；当前仅 A 股，未来扩展需重做。
3. 「差异消失不自动清除」在长期运维中会不会变成"每天都有一条历史差异挂着，最终没人看"？
