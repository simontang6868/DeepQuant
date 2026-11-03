> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# Phase 2 进度报告（1/3）：适配层与订单规划

> 生成时间：2026-11-02 22:10（GMT+8）
> 依据：任务书 §5 Phase 2 的 2.1、2.2
> 验收标准：`docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md`（动手前冻结）
> 状态：**2.1 ✅ / 2.2 ✅**，2.3–2.10 未开始

---

## 0. 一句话

`BrokerAdapter` 抽象 + 可注入故障的 `MockBroker` + 订单规划已完成，**50 条测试**（28 + 22）全部通过，后端全量 **3243 passed / 1 skipped**、无回归。这一阶段**没有接任何真实券商**，全部是 `PAPER` 路径。

## 1. 2.1 BrokerAdapter + MockBroker

### 1.1 分层决定：Mock 扮演券商，不做撮合

仓库里已有 `eventloop/matching.py`（bar 内撮合：价格可及性、参与率、队列位置、部分成交）。**没有复用它，也没有重写**——两者不是一回事：

| | `eventloop.matching` | `BrokerAdapter` |
| --- | --- | --- |
| 视角 | 市场怎么撮合 | 券商怎么应答 |
| 输入 | bar（OHLCV） | 委托请求 |
| 输出 | 成交价/量 | 接受/拒单/超时/回报 |
| 用途 | 回测 | 实盘 |

如果 MockBroker 自己按 bar 判能不能成交，它就变成第二个撮合模型，与回测那套必然在细节上分叉，而分叉点**没人会发现**。所以 Mock 只回答"券商收到这笔单之后会怎样"。

这条约束被写成测试 `test_adapter_layer_does_not_reimplement_matching`：`mock.py` 的代码里出现「参与率/队列位置/SlippageModel」即失败。

### 1.2 八项能力与厂商中立

`BrokerAdapter` 声明 8 个抽象方法：`get_positions` / `get_cash` / `get_orders` / `get_fills` / `get_quote` / `get_calendar` / `place_order` / `cancel_order`。

厂商中立性不是靠"看起来没有"，而是 `test_no_vendor_vocabulary_in_adapter_layer` 用 **AST** 扫标识符、import 与非 docstring 字符串。

> ⚠️ 第一版写成 `token in 全文文本`，结果在**描述性 docstring**（"本层不出现 `xtquant`"）上误报两条。这正是本项目反复踩的「检查器分不清提到与使用」。改成 AST 后另加了 `test_code_token_scanner_ignores_prose_but_catches_real_use` 钉住检查器本身。

### 1.3 `UNKNOWN` 是独立状态

`BrokerOrderStatus.UNKNOWN` 不是装饰。真实接口一定会有"我不确定这笔单现在怎样"的时刻（回报丢失、超时、崩溃）。把它收敛成 `ACCEPTED` 会诱导下游再发一笔 = 重复下单。

四类结果被显式区分：

| 情景 | 返回/抛出 | 持仓是否变 |
| --- | --- | --- |
| 正常成交 | `FILLED` / `PARTIALLY_FILLED` | 是 |
| 明确拒绝 | 抛 `OrderRejected`（带原因码）+ 留一条 `REJECTED` 废单 | 否 |
| 请求超时 | 抛 `BrokerTimeout` + 留一条 `UNKNOWN` | 否 |
| **回报丢失** | 返回 `UNKNOWN` | **是** |

最后一行是关键：回报丢了但成交真的发生了，持仓变了、回报看不见——这正是对账该发现的差异来源。若只把状态改成 UNKNOWN 而持仓不动，模拟的是"什么都没发生"，对账永远发现不了问题。

### 1.4 六类故障与确定性

`FaultScript` 覆盖 6 类：`reject` / `fill_ratio` / `drop_fills` / `duplicate_fills` / `timeout` / `stale_quotes`。

确定性的做法：不用 `random`、不用 `time.time()`、`fill_id` 由 `client_order_id` + 成交序号派生。`test_replay_is_bitwise_identical` 跑两遍同一场景，断言逐位相同。

## 2. 2.2 订单规划

### 2.1 三条结构性性质

| 性质 | 为什么做成结构而非检查 |
| --- | --- |
| **先卖后买** | 换仓时买入的钱来自卖出；顺序错了同一笔钱被用两次 |
| **整手是取整规则** | 目标股数生成时就是 `lot_size` 整数倍，不靠"发出去被拒"兜底 |
| **幂等键确定性派生** | `20261030-600990-B-200`，不含时间戳/随机数/进程号 |

### 2.2 ⭐ 买入预算不假定卖出回款

`settled_cash_from_sales` 默认 **0**。于是换仓是两步：先卖 → 等成交 → 重新调用 `plan_orders` 生成买单。

这是**有意的保守**，不是缺陷：宁可多跑一轮，也不要发出可能因资金不足被拒的单。代价是 `planner` 必须幂等可重入——已由 `test_planning_twice_yields_identical_orders` 钉住。

### 2.3 ⚠️ 限价不是真实"对手价"

任务书要求"对手价最多让 0.3%"。我们没有盘口数据，只能用**最新价 × (1 ± 0.3%)** 近似，再被涨跌停夹住。

这不是精确的对手价，**必须在台账里注明**，Phase 3 用真实成交价校准。已在 `_limit_price` 的 docstring 写明。

## 3. 预登记缺陷的裁决

| ID | 预登记内容 | 裁决 |
| --- | --- | --- |
| **P1** | 「回报重复」比「回报丢失」更容易写错——多数实现会无意生成新 `fill_id` | **命中风险点，未踩坑**：预登记直接把实现逼成"复用同一条 `Fill`"，`test_fault_duplicate_fill_reuses_same_fill_id` 断言两条 `fill_id` 相等且持仓只增一次 |
| **P2** | `client_order_id` 若含时间戳，幂等彻底失效且单次运行看不出来 | **未踩坑**：`make_client_order_id` 是纯函数，测试隔 10ms 调用两次断言相等，并禁止十六进制长串 |
| P3 | 对账易漏「券商有、本地无」 | 未开始（属 2.5） |
| P4 | 「委托价偏离最新价」在无行情时被静默跳过 | 未开始（属 2.3） |
| P5 | 「峰值回撤」的峰值重启后丢失 | 未开始（属 2.3） |

**P1/P2 是被预登记挡住的两处**：如果没先写下来，实现时很可能会用 `uuid4()` 当 `fill_id`、用 `datetime.now()` 拼 `client_order_id`——两者都能跑通，且在单次运行里完全正常。

## 4. ⚠️ 实现与测试各自的错误（不掩盖）

### 4.1 实现缺陷（被测试抓到，已修）

`plan_orders` 第一版把「可卖为 0」的持仓**静默跳过**，没有记入 `skipped`：

```python
if p.available <= 0:      # ← 在判断「是否需要减仓」之前就 continue
    continue
```

后果是「该减没减」这件事从此无法归因——次日持仓与目标不符，但台账里没有任何记录。修法是把顺序倒过来：先判 `quantity <= want`（不需要减），再判能不能卖，卖不了就记 `SkippedItem`。

### 4.2 测试预期错误（4 处，我算错了）

`target_weight` 是**每只权重**，5 只 × 20% = 满仓策略仓。我在 4 个用例里把它当成了"总仓位"，于是把「权益 20 万 ⇒ 每只 4 万 ⇒ 4000 股」算成了 20000 股。

按纪律：**测试预期错就改测试，实现缺陷就改实现**，两者不混。这里改的是测试，并在注释里写清算法。

## 5. 测试

| 套件 | 结果 |
| --- | --- |
| `tests/broker/` | **28 passed** |
| `tests/execution/` | **22 passed** |
| 后端全量 | **3243 passed / 1 skipped**（基线 3193 + 50），无回归 |

⚠️ 跑后端测试必须带 `PYTHONPATH=<repo>/backend/src`，否则 19 个模块 `ModuleNotFoundError: No module named 'deepquant'`——那是环境问题，不是回归。

## 6. 未完成

| 项 | 状态 | 阻塞 |
| --- | --- | --- |
| 2.3 RiskGate 硬限（11 条）+ 变异测试 | 未开始 | — |
| 2.4 状态机与恢复 | 未开始 | — |
| 2.5 日终对账 | 未开始 | — |
| 2.6 台账 | 未开始 | — |
| 2.7 监控告警 | 未开始 | — |
| 2.8 故障注入（11 项） | 部分：Mock 侧 6 类已就绪，端到端未做 | 依赖 2.3–2.6 |
| 2.9 SHADOW | 未开始 | **D8** |
| 2.10 接口材料草稿 | 未开始 | D3 待核实 |

## 7. 产物

| 文件 | 内容 |
| --- | --- |
| `backend/src/deepquant/broker/models.py` | 8 类 DTO（`UNKNOWN` 独立状态、T+1 结构、`fill_id` 必填） |
| `backend/src/deepquant/broker/errors.py` | `RejectCode` 22 个机读原因码（含 `MISSING_INPUT`） |
| `backend/src/deepquant/broker/base.py` | `BrokerAdapter` 8 项能力抽象 |
| `backend/src/deepquant/broker/mock.py` | `MockBroker` + `FaultScript`（6 类故障，确定性） |
| `backend/src/deepquant/execution/planner.py` | 差额订单、先卖后买、幂等键、预算保守 |
| `backend/tests/broker/test_broker_adapter.py` | 28 条 |
| `backend/tests/execution/test_planner.py` | 22 条 |

## 8. 下一步

按 2.3 → 2.4 → 2.5 → 2.6 → 2.7 顺序推进。**2.3 是硬骨头**：11 条硬限逐条两个边界用例 + 至少 6 个变异体，其中「删掉某条检查」「`and` 写 `or`」「`>` 写 `>=`」必须被抓到。

预登记 P4（缺失输入被静默跳过）与 P5（峰值回撤重启后丢失）都落在 2.3，届时**优先验证这两条**。
