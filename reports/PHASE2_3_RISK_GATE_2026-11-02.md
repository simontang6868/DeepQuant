> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# Phase 2.3 报告：RiskGate 11 条硬限

> 生成时间：2026-11-02 22:45（GMT+8）
> 依据：任务书 §5 Phase 2.3
> 验收标准：`docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.3-a ~ 2.3-d
> 状态：**2.3 ✅**（67 条行为测试 + 10 条变异测试）

---

## 0. 一句话

11 条硬限全部落地，**每一条都有「刚好通过」与「刚好拒绝」两个边界用例**，9 个变异体全部被抓到。过程中发现两件比实现本身更重要的事：**两条闸在「缺行情」上互为备份**（好），以及**「恰好 1%」这个用例在浮点里根本不存在**（我原来的测试通过的理由是错的）。

## 1. 11 条硬限

| # | 检查名 | 判据 | 拒绝原因码 |
| --- | --- | --- | --- |
| 1 | `halt_switch` | 状态停机 **或** 停机文件存在 | `HALT_SWITCH` |
| 2 | `circuit_breaker` | 已熔断 | `CIRCUIT_BREAKER` |
| 3 | `authorization` | 无授权 / 过期 / 板块未授权 / 金额超授权上限 | `NO_AUTHORIZATION` / `NOT_AUTHORIZED` |
| 4 | `reconciliation` | 上轮对账**不是** `True`（含从未对账） | `RECONCILIATION_MISMATCH` |
| 5 | `unknown_orders` | 存在 `UNKNOWN` 委托 | `UNKNOWN_ORDER_PENDING` |
| 6 | `trading_hours` | 不在交易时段 | `NOT_TRADING_HOURS` |
| 7 | `blacklist` | 命中黑名单 | `BLACKLIST` |
| 8 | `quote` | 无行情 / 标记陈旧 / 价格 ≤0 / 超龄 | `STALE_QUOTE` |
| 9 | `price_deviation` | 偏离 > 1% | `PRICE_DEVIATION` |
| 10 | `position_limit` | 单票市值 > 权益 × 20% | `POSITION_LIMIT` |
| 11 | `order_notional` | 单笔金额 > 上限 | `ORDER_NOTIONAL_LIMIT` |
| 12 | `daily_count` | 当日已发 ≥ 上限 | `DAILY_ORDER_LIMIT` |

第 5 条是从任务书 2.4 提前并进来的：**存在 UNKNOWN 时禁止新单**，它本质上也是一条"该不该发单"的门禁，放在闸门里比散落在状态机里更难绕过。

## 2. 为什么顺序固定

顺序本身就是规则：**先问「这个系统还该不该发单」，再问「这笔单合不合规」**。

一个已经熔断的系统不该继续讨论单笔金额——那会让人以为"改小金额就能过"。测试 `test_stops_at_first_failure` 同时触发停机与熔断，断言返回的是 `HALT_SWITCH` 且 `checked == ()`。

停在第一个失败还有一层原因：继续跑会产出「缺 A」和「缺 B」两条原因，调用方按第一条修完再试又收到第二条，像打地鼠。

## 3. ⭐ 缺失即拒（预登记 P4 正身）

`OrderContext` 里有 **7 个可空字段**，每一个为 `None` 都必须导致拒单：

`quote` / `equity` / `position_value` / `board` / `is_trading_hours` / `trading_day` / `halt_file_present`

`test_every_missing_input_is_rejected` 用参数化逐个跑。这条测试的价值不在当下——而在将来有人给 `OrderContext` 加字段却忘了处理时，**只要加进那个列表就会立刻红**。

⚠️ `MISSING_INPUT` 原因码是刻意存在的：它把「拿不到数据」变成一次**显式拒绝**，而不是一次静默放行。本项目在闸门上反复犯这一个错。

## 4. ⭐ 峰值持久化（预登记 P5 正身）

熔断判的是「相对峰值的回撤」。峰值一旦在重启后丢失，回撤从 0 重算，熔断**永远不会触发**。

所以 `GateState.save/load` 不是便利方法，是功能的一部分：

- `test_peak_survives_restart`：落盘后重启，峰值仍是 25,000
- `test_breaker_still_trips_after_restart`：重启后从 25,000 跌到 24,000 仍触发
- `test_tripped_breaker_persists`：熔断状态本身也跨重启保留（否则「重启即复活」）
- `M9` 变异：把 `"peak_equity": self.peak_equity` 改成 `None`，测试抓到

## 5. 变异测试：9 个变异体 + 1 条锚点自检

| ID | 变异 | 抓法 |
| --- | --- | --- |
| M1 | `_c_breaker` 改成永远放行 | 熔断场景从拒变通过 |
| M2 | 从检查链摘掉 `blacklist` | 黑名单场景从拒变通过 |
| M3 | 单票上限 `>` 写成 `>=` | 「恰好等于上限」从通过变拒绝 |
| M4 | 偏离阈值 1% 悄悄放宽到 10% | 2% 偏离从拒变通过 |
| M5 | 缺行情时跳过 quote 检查 | `quote` 出现在 `checked` 里（见 §6.1） |
| M6 | 对账 `is not True` 写成 `is False` | 「从未对账」从拒变通过 |
| M7 | 单日笔数 `>=` 写成 `>` | 恰好达上限从拒变通过 |
| M8 | 熔断 `>` 写成 `>=` | 恰好触及阈值从不触发变触发 |
| M9 | 峰值不落盘 | 重启后峰值为 `None` |

另加 `test_mutation_anchors_all_exist`：所有变异锚点必须在源码里**恰好出现一次**。没有这条，源码一改（比如自己把 bug 修了），变异会从「抓到」静默退化成「没抓到」，而测试仍然显示 passed——这就是「假变异」，本项目出现过两次。

每个变异体都先断言 `mutated != src`，从机制上排除假变异。

## 6. 两个真实发现

### 6.1 两条闸在「缺行情」上互为备份

M5 变异后，`quote` 检查确实被跳过了，但**整体仍然拒单**——因为 `price_deviation` 单独实现了「没有最新价就拒」（`MISSING_INPUT`）。

这不是重复代码，是**防御纵深**：一处失效不至于全盘放行。

所以 M5 的判据不是「整体变通过」，而是「`quote` 出现在 `checked` 里」且「`price_deviation` 接过拦截责任」。测试里写明了这一点。

### 6.2 ⚠️ 「恰好 1%」这个用例在浮点里不存在

原来那条测试是这样的：

```python
def test_price_deviation_at_limit_passes(gate):
    """边界：恰好 1% 应放行。"""
    d = gate.evaluate(_ctx(order=_order(price=10.1)))    # 恰好 1%
    assert d.allowed is True
```

它一直是绿的，我也一直以为它验证了「边界是 `>` 而非 `>=`」。做 M4 变异时才发现：

```
abs(10.1 - 10.0) / 10.0 = 0.009999999999999964
```

**比 1% 小**。任何 `x * 1.01` 都得不到精确的 1%。这个测试之所以通过，是因为浮点恰好落在阈值下方，**不是因为逻辑正确**——如果实现写成 `>=`，它照样会绿。

⇒ 处置：
1. 改名为 `test_price_deviation_just_below_limit_passes`，docstring 写明「这不是恰好 1%」；
2. 加 `test_price_deviation_just_above_limit_rejects`（10.11 = 1.1%）补齐另一侧；
3. M4 改为测「阈值被放宽 10 倍」这一更现实、也更危险的失效；
4. 顺手给 `observe_equity` 的回撤比较加上 `1e-12` 容差——`1 - 19500/20000 = 0.025000000000000022`，直接跟 0.025 比会把「恰好触及」判成「超出」。

这条与 MEMORY 里「容差按产物存储精度定」是同一族问题，但方向相反：那次是**容差太小导致永远不可满足**，这次是**边界用例选了一个浮点里不存在的点**。

## 7. 授权文件只读

`Authorization` 只有 `load`，**没有任何写方法**。没有提供，比"有方法但约定不调用"更难被绕过——测试 `test_authorization_has_no_write_method` 断言 `save/write/create/extend/update` 都不在公开接口里。

缺字段直接报错，不补默认值：

```python
for key in ("max_notional", "valid_until", "boards"):
    if key not in d:
        raise NoAuthorization(...)
```

「补一个看起来合理的默认值」正是本项目在成本口径上错过四次的同一个动作。

## 8. 测试与回归

| 套件 | 结果 |
| --- | --- |
| `tests/execution/test_risk_gate.py` | **67 passed** |
| `tests/execution/test_risk_gate_mutation.py` | **10 passed** |
| `tests/execution/`（含 2.2 planner） | **99 passed** |
| 后端全量 | **3320 passed / 1 skipped**（3193 基线 + 28 + 22 + 77），无回归 |

## 9. 未完成

2.4 状态机与恢复、2.5 日终对账、2.6 台账、2.7 告警、2.8 端到端故障注入、2.9 SHADOW（缺 D8）、2.10 接口材料。

## 10. 产物

| 文件 | 内容 |
| --- | --- |
| `backend/src/deepquant/execution/risk_gate.py` | `RiskLimits` / `GateState` / `Authorization` / `OrderContext` / `RiskGate` + 12 个检查器 |
| `backend/tests/execution/test_risk_gate.py` | 67 条行为测试 |
| `backend/tests/execution/test_risk_gate_mutation.py` | 9 个变异体 + 锚点自检 |
