# 审查包 4 · SHADOW 比对

## 1. 源码与接口

| 项 | 内容 |
| --- | --- |
| 源码 | `backend/src/deepquant/execution/shadow.py` |
| 测试 | `backend/tests/execution/test_shadow.py`（**22**） |
| 入口 | `build_shadow_plan(...)` / `compare_with_ledger(...)` / `ShadowReport` |
| 关键性质 | **签名里没有任何券商对象** ⇒ 结构上不可能下单 |

## 2. 设计要点

1. **不发送靠签名，不靠开关**。若用 `if mode != SHADOW`，一次参数传错就真发出去了；靠签名则结构上不可能。
   另有 AST 扫描兜住"以后有人加了个 adapter 参数但暂时没调用"：`test_shadow_module_does_not_call_place_order`、`test_shadow_never_receives_a_broker`。
2. **差异原因事前枚举**：价格上限 / 板块 / ST / 无行情 / 资金不足 / 整手 六类口径差 + `UNEXPLAINED`。
   归不进任何一条一律 `UNEXPLAINED` 并挡 G2。规则是**事前**写死的，不是事后逐条圆。
3. **`UNEXPLAINED` 才是否决项**。差异条数为 0 不代表通过——只有"0 条未解释"才算。

## 3. ⭐ 本轮在此处查出一个"自我伪装"缺陷

首版用 `o.side.value == "buy"` 判断买卖方向，而 `Side.BUY.value` 是 **`"BUY"`**（大写）。
后果不是报错，而是更糟：

- `buy_codes` 恒为空 ⇒ 台账里每只目标都被判成"没买"
- 这些差异全部落到 `EXPLAINED_NO_QUOTE`（默认上下文没有行情）这个**已解释**原因上
- 于是 `ShadowReport.ok == True`，摘要显示"0 条未解释差异"

**一个完全失效的 SHADOW 会自我报告为通过。**

现改为枚举比较，并加前提断言 `test_buy_codes_uses_enum_not_value_string`（内含 `assert Side.BUY.value == "BUY"`）。

> 这条对"可解释差异"这类机制的普遍启示：**解释器越完善，掩盖失效的能力越强。**

## 4. 测试清单（关键）

| 断言 | 测试 |
| --- | --- |
| 结构上不下单 | `test_shadow_never_receives_a_broker`、`test_shadow_module_does_not_call_place_order` |
| 方向判断用枚举 | `test_buy_codes_uses_enum_not_value_string` |
| 计划无"已发送"状态 | `test_shadow_plan_has_no_sent_state` |
| 从台账目标构造订单 | `test_shadow_builds_orders_from_ledger_targets` |
| 六类可解释原因 | `test_st_is_explained`、`test_board_restriction_is_explained`、`test_price_cap_is_explained`、`test_no_quote_is_explained`、`test_cash_shortage_is_explained` |
| 缺输入报错而非静默 | `test_missing_target_holds_is_an_error_not_a_quiet_noop`、`test_missing_decision_date_is_an_error` |

## 5. 已知缺陷 / 未覆盖

| # | 内容 | 影响 |
| --- | --- | --- |
| D-1 | **20 个交易日实跑为 0 天**（缺运行机器），机制正确性未被真实数据检验 | 高。这是 G2 未通过的唯一原因 |
| D-2 | 「整手」类差异的解释只按 100 股判定，**未考虑科创板/北交所的最小单位差异** | 中。当前授权板块不含科创板，暂不触发 |
| D-3 | 六类解释项**互斥性未验证**：一只股票可能同时命中两条（如 ST + 无行情），当前按顺序取第一条 | 低-中。会造成归因偏差 |
| D-4 | 比对的是"本应下的单"与"台账记录"，**不是与真实成交**比对 | 中。SHADOW 阶段尚无真实成交，属预期内 |

## 6. 建议重点质疑

1. 六类解释规则是否足够覆盖真实差异？有没有"解释得过于宽松"从而把真实 bug 说成口径差？
2. `UNEXPLAINED` 之外，是否也该对"解释数量异常增多"报警（例如某天 5 只全部落到"无行情"）？
3. 20 个交易日样本量本身是否足以支撑"无未解释差异"的结论？
