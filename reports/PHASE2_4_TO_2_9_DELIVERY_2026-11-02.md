> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# Phase 2.4–2.9 交付报告：状态机、对账、台账、告警、故障注入、SHADOW

**完成时间**：2026-11-02
**对应验收标准**：`docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md`（21:40 冻结，本文未修改它）
**判定**：2.4 / 2.5 / 2.6 / 2.7 / 2.8 **`PASS`**；2.9 **`PARTIAL`**（机制完成，20 个交易日需 D8）
**新增测试**：179 条（2.4 的 41 + 2.5 的 30 + 2.6 的 27 + 2.7 的 30 + 2.8 的 29 + 2.9 的 22）

---

## 1. 逐项对照

| 验收项 | 结果 | 关键证据 |
| --- | --- | --- |
| 2.4-a `UNKNOWN` 阻断新单 | ✅ | `is_blocking` 全枚举参数化；**集成测试**从重建一路跑到闸门，断言 `UNKNOWN_ORDER_PENDING` |
| 2.4-b 非法转移抛异常 | ✅ | 终态回退/反向/数量减少/身份字段改动/时钟倒退各一条；`TRANSITIONS` 表覆盖性自检 |
| 2.4-c 以券商为准重建 | ✅ | 本地 1000 股、券商 800 ⇒ 重建 800；`rebuild_from_broker` **签名里没有持仓入参** |
| 2.4-d 重建只读 | ✅ | `ReadOnlyBroker` 白名单 + 显式拦截双道；用真 `MockBroker` 验"重建前后委托数不变" |
| 2.5-a 四类对象任一差异⇒停机 | ✅ | 持仓/资金三字段/成交/委托逐字段；差异写 `last_reconciliation_ok=False` |
| 2.5-b 双向 | ✅ | 每类都有 `MISSING_LOCAL` 与 `MISSING_BROKER` 两条用例 |
| 2.5-c 落到字段与差值 | ✅ | `Difference.field` 为空直接抛；断言 `delta` **符号** |
| 2.5-d 容差方向相反 | ✅ | 股数差 1 股必报；金额用**整数分**，1 分忽略、2 分报出 |
| 2.6-a 决策快照带哈希 | ✅ | `data_sha256`/`config_sha256` 必填 + 64 位 hex 校验；四类成本分项独立 |
| 2.6-b 成本只来自 `compute_fees` | ✅ | 运行时（三项之和 == `fees.total`）+ 静态（AST 扫导入/调用与费率量级字面量） |
| 2.6-c 只追加 | ✅ | **字节级**断言：追加后文件前缀完全不变；哈希链 + 改行/重算自哈希/删行三种篡改都能抓 |
| 2.7-a 五类告警 | ✅ | 每类主线 + 边界；`HeartbeatState(None)` 判 CRITICAL 而非静默 |
| 2.7-b 不发邮件 | ✅ | AST 扫 import 与调用，另加检查器自检 |
| 2.8-a 11 项各一个测试 | ✅ | 见 §3 映射表；后果不同的拒因拆开测 |
| 2.8-b 断言具体后果 | ✅ | 原因码 / 状态 / 持仓资金数值 / 告警等级 / 台账字段 |
| 2.8-c 配置被篡改验运行时校验 | ✅ | `FrozenConfig.get` 内部**每次**重算文件哈希；冻结与使用两个时刻分开构造 |
| 2.9-a 不发送 + 差异可解释 | ⚠️ `PARTIAL` | 机制齐；"连续 20 日无未解释差异"需 D8 |
| 2.9-b 代码完成、跑满依赖 D8 | ⚠️ | 同 G2 |

---

## 2. 本轮查出并修掉的三个真实缺陷

三个都不是"风格问题"，每一个都会让系统在**看起来正常**的情况下错下去。

### 2.1 ⭐⭐ `roll_day` 日切把 `has_unknown_orders` 一起清掉，且不防日期倒退

原实现：

```python
def roll_day(self, day: int) -> None:
    if day != self.trading_day:
        self.trading_day = day
        self.daily_order_count = 0
        self.has_unknown_orders = False      # ← 致命
```

两个后果：

1. **`day != trading_day` 对倒退也成立**。时钟漂移（10-30 被读成 10-29）会被当成"跨日"，当日委托笔数清零 ⇒ **11 条硬限里第 3 条（单日笔数上限）当场失效**。
2. **跨日清 `has_unknown_orders`**。UNKNOWN 是**券商侧的事实**，不因日历翻页消失。昨天的回报丢了，今天系统"自动痊愈"继续下单——而账实从那一刻起就错了。

处置：`roll_day` 现在日期倒退抛 `ClockWentBackwards`；不再清 `has_unknown_orders`；新增三条测试（`test_10_clock_drift` / `test_10b` / `test_10c`）。既有 227 条 execution 测试无一受影响。

### 2.2 ⭐⭐ SHADOW 用 `side.value == "buy"` 比较，导致所有买单"消失"

`Side.BUY.value` 是 `"BUY"`（全大写），我写成 `== "buy"`。后果不是报错，而是更糟的东西：

- `buy_codes` 恒为空 ⇒ 台账每只目标都被判成"没买"
- 这些差异全部落到 `EXPLAINED_NO_QUOTE`（默认上下文没有最新价）这个**已解释**原因上
- 于是 `ShadowReport.ok == True`，摘要显示"0 条未解释差异"

**一个完全失效的 SHADOW 会自我报告为通过。** 这就是为什么 2.9 的解释枚举必须有 `UNEXPLAINED` 兜底，以及为什么我要专门写一条测试断言 `Side.BUY.value == "BUY"` 这个前提。

### 2.3 `FrozenConfig` 的"运行时校验"边界

设计时刻意写明威胁模型：能防会话中途改文件，**不能防**内存被改或代码被改。同时定了一条保守规则——**内容不变但重新格式化也算篡改**（否则等于开一个"重排一遍再顺手改个数值"的口子）。`test_11d` 把"冻结"与"使用"两个时刻分开构造，只在加载时校验的实现在这条上必红。

---

## 3. 11 项故障注入的落法

| # | 故障 | 断言的具体后果 |
| --- | --- | --- |
| 1 | 断网 | `NotConnected` 抛在**全部**四个查询与下单上；反例测试确认它**不返回**空持仓/零资金（否则对账会产出"本地多记"的虚构差异） |
| 2 | 拒单 | 三种原因码原样透出（`PRICE_CAGE`/`INSUFFICIENT_CASH`/`INVALID_LOT`）；被拒委托仍登记 `REJECTED`；资金与持仓一分不动；出 `WARN` 告警 |
| 3 | 部分成交 | `PARTIALLY_FILLED` + 已成 400（1000×0.4）+ 持仓增量 400 + 对账无差异 |
| 4a | 回报丢失 | 状态 `UNKNOWN` + **持仓已增 200** + `get_fills()` 为空（三者同时断言）；重建后闸门拒绝新单 |
| 4b | 回报重复 | 同一 `fill_id` 出现两次 + 持仓只增 200（不是 400）+ 去重后对账平 + 告警 `WARN` |
| 5 | 行情陈旧 | `STALE_QUOTE`，且 `failure.check == "quote"`（不是"跳过检查") |
| 6 | 停牌 | `SUSPENDED`，持仓仍在 |
| 7 | 涨停买不进 | `LIMIT_UP` + 无持仓变化 |
| 8 | 跌停卖不出 | `LIMIT_DOWN` + 持仓不减 |
| 9 | 日内重启 | 持仓来自券商 400 / 峰值保持 ×1.2 / 当日笔数保持 3 / 台账 seq 与链续上（四件事同时断言） |
| 10 | 时钟漂移 | `ClockWentBackwards` + 笔数不变 + 交易日不变 + UNKNOWN 未被清 |
| 11 | 配置被篡改 | `ConfigTampered`（含删文件、仅重格式、改 1 个字符） |

另有一条全链路冒烟：三项故障同时打开跑 planner→gate→broker→ledger→reconcile→alerts，最后 `verify_chain()`。

---

## 4. 两处"检查器自己也检一遍"

本项目反复吃「检查器分不清提到与使用」的亏（2.1-b 的厂商词、L3ac 的闸门）。本轮凡是静态检查都配了自检：

| 检查器 | 自检 |
| --- | --- |
| 台账不得调用 `compute_fees` | 第一版写成 `"compute_fees" in src`，被 `FillCost.from_fill` 里那句**错误消息**误报。改 AST 扫导入与调用后，另写 `test_scanner_ignores_the_same_words_in_prose` |
| 台账不得出现费率量级字面量 | `test_the_static_scanners_actually_catch_a_real_violation` 塞 `X = 0.00025` 确认抓得到 |
| 告警不得发邮件 | `test_mail_scanner_can_actually_catch_smtplib` 塞 `smtplib` + `sendmail` |
| 变异锚点必须唯一 | `test_mutation_anchors_all_exist`（9 个锚点逐个断言出现次数为 1） |

---

## 5. 变异测试（9 个）

| 变异 | 抓它的测试 |
| --- | --- |
| M1 删终态检查 + 给表加后继 | `test_M1_terminal_state_can_be_left_if_both_guards_removed` |
| M1b **只**删终态检查（行为不变） | `test_M1b_removing_only_the_explicit_check_keeps_behaviour` |
| M2 去掉 `source != "broker"` | 本地可解封 UNKNOWN |
| M3 去掉 `filled_shares` 单调性 | 已成股数可减 |
| M4 重建重置峰值 | P5 变异体 |
| M5 只读守卫放行 `place_order` | 2.4-d 失效 |
| M6 去掉身份字段校验 | `shares` 可缩小 |
| M7 去掉时钟倒退校验 | ts 可倒退 |
| M8 `is_blocking` 恒 False | 2.4-a 失效 |
| M9 忽略「券商有本地无」 | P3 变异体 |

⭐ **M1 揭示的一个事实**：终态回退其实是**三层**保护——显式终态检查（给人读的原因）、`TRANSITIONS` 表（机器判）、`filled_shares` 单调性（数据一致性）。只删第一层行为完全不变（M1b 就是用来记录这件事的）。M1 因此必须**同时**改表才构成真变异；而首次写 M1 时我用了 `FILLED(200) → PARTIALLY_FILLED(100)`，结果被第三层拦住——那次失败反而证明了第三层在生效。

---

## 6. 三处刻意的"反直觉"设计

**① 台账拿不到成本计算能力。** `ledger.py` 不 import `compute_fees`，只接收已算好的 `Fill.fees`。第二条静态扫描就是为了阻止"哪天顺手 import 一下"。台账一旦自己算钱，它就从"记录"变成"账本"，而一个只能追加、不能更正的账本当输入源，错了永远改不回来。

**② 对账通过不自动开门。** `apply_reconcile_to_gate_state` 默认 `clear=False`：有差异 ⇒ 关；无差异 ⇒ **不变**。一条差异被"下次对账恰好没复现"而自动清除，等于系统自己宣布自己没问题。必须人工核对后显式开门。

**③ SHADOW 的签名里没有券商对象。** 不发送如果靠 `if mode != SHADOW` 保证，一次参数传错就真发出去了。`build_shadow_plan` / `shadow_report` 的入参只有台账、账户快照、行情——**结构上不可能下单**，另有 AST 扫描兜住"将来加个 adapter 参数但暂时没调"。

---

## 7. 新增文件（含两处验收清单外的必要文件）

验收标准 §5 列的允许范围是 `execution/{planner,risk_gate,reconciler,ledger,alerts}.py`。实际新增中 `state_machine.py`、`config_guard.py`、`shadow.py` 在清单外，理由：

| 文件 | 为什么必须单独成文件 |
| --- | --- |
| `execution/state_machine.py` | 2.4 的状态机与重建；塞进 `risk_gate.py` 会让"闸门规则"与"委托生命周期"混在一处，而这两者的失效后果完全不同 |
| `execution/config_guard.py` | 2.8-c 要求**运行时**哈希校验，需要一个能被 `get` 反复调用的对象 |
| `execution/shadow.py` | 2.9；它的核心性质就是"签名里没有券商"，放在任何已有文件里都会破坏这一点 |

**未改动**：`risk/gateway.py`（API 检查点网关，与本阶段 `RiskGate` 分层不同）、`eventloop/`（回测撮合）、`backtest/costs.py`（费率唯一源）、`research/` 下策略代码与冻结配置。

唯一改动的既有文件是 `execution/risk_gate.py` 的 `roll_day`（见 §2.1）与新增 `ClockWentBackwards` 导出。

---

## 8. 回归

| 套件 | 结果 |
| --- | --- |
| `backend/ tests` | **3499 passed / 1 skipped**（3193 基线 + 28 broker + 278 execution） |
| `backend/tests/execution` | **278 passed**（22+77+41+30+27+30+29+22） |
| 运行方式 | 必须带 `PYTHONPATH=<repo>/backend/src`，否则 19 个模块 `ModuleNotFoundError`（环境问题，不是回归） |

---

## 9. 尚未解决

1. **D8 运行机器**——G2 的「SHADOW 连续 20 个交易日无未解释差异」无法开始。2.9 只交付机制。
2. **D4 创业板权限**——影响 1,393 只（可买池 27%），是安全过滤里最大的单项（见 A2 报告）。
3. **D5 价格上限**——当前三本账 15 只股票价格 2.10~33.00 元，全部低于 40 元，暂不影响决策。
4. **2.10 程序化交易报告**——本轮未做。按 2.10-a，agent 只准备材料草稿，不得代办、不得提交。
5. `research/` 整个目录仍未进 git，前瞻台账与新快照无版本控制保护。
