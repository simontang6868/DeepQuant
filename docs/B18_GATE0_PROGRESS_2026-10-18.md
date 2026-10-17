# B18 / Gate 0 执行进度 —— 装置修复、基准体系、组合层第一阶段

日期：2026-10-18 02:40（GMT+8）
依据：`docs/B18_RESEARCH_VERDICT_2026-10-18.md`（裁决）+ 用户 2026-10-18 02:05「全部按你的推荐来做」
性质：**G0-1 ~ G0-4 完成；G0-5 部分完成（A1/A2）；G0-5 主体 A3 未开始**。
本轮**未跑任何策略回测、未产生任何新收益结论**。

---

## 0. 一句话状态

> **装置修复与基准体系已就位（D2 已修、D1 已勘误、B0/B1/B2 与 Sharpe/换手率已实现并被测试锁定）；
> 组合层只做到「配置 + 调仓日历 + 截面选股」，记账循环（A3）尚未实现，
> 因此 Gate 0 未完成，按 B18 §14 结论 3，Gate 1 仍不得开始。**

---

## 1. 用户决策记录（Q1~Q8 全部按推荐）

| # | 决策项 | 结论 |
|---|---|---|
| Q1 | 时点成分数据 | **(a) 先用当日快照跑第一轮**，结论明确标注为「上界估计」；若上界下不成立 → 直接停止，省下历史成分采购成本 |
| Q2 | 训练期设计 | **(a) 无训练期**，参数先验固定，全样本即样本外 |
| Q3 | 本金与口径 | **(a) 权重记账为主 + (c) 100 万整手为附**，两者并行报告 |
| Q4 | 真实费率 | **(a) 保持三档假设 + 显式标注**；不编造任何实际券商费率 |
| Q5 | A8 涨跌停 | **批准**（已实施，见 §2.1） |
| Q6 | A1~A7 | **批准**（A1/A2/A6/A7 已实施；A3/A4/A5 未实施，见 §3） |
| Q7 | 指数口径 | **待核实数据源**：不猜测价格指数/全收益指数，`index_kind` 为必填参数 |
| Q8 | Git 授权 | **授权**（本 PR 按此推送，但仍不 merge） |

---

## 2. 已完成项

### 2.1 G0-1 —— D2 修复：涨跌停启用

**问题**：`data/step75/research.py` 的 `BacktestConfig(...)` 未传 `market_rules`，
走默认 `a_share_rules()`（`price_limit_rate=None`）→ `limit_prices()` 返 None
→ `open_fill_block_reason()` 跳过判定 → **全部历史研究（含 48 只 288 组）从未模拟涨跌停**。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/src/deepquant/market/rules.py` | 新增 `a_share_price_limit_rate(code, mode)` 纯函数；`PRICE_LIMIT_20PCT_PREFIXES` / `PRICE_LIMIT_MODES` 常量 |
| `data/step75/research.py` | `BacktestConfig(...)` 显式传 `market_rules=market_rules_for(code)`；新增 `DEEPQUANT_PRICE_LIMIT` 开关（默认 `board`）；输出 JSON 新增 `market_rules` 段 |
| `data/step75/research.py` | `DESIGN_VERSION` **`oos75-v2` → `oos75-v3`** |

**比例解析规则**（`board` 模式，默认）：

- 创业板 300/301、科创板 688/689 → **20%**
- 其余（主板/中小板） → **10%**
- `flat` 模式一律 10%（消融对照）；`off` 模式返回 `None`（不判定，**不得作主结果**）

**已声明的限制**（写进输出 JSON 的 `market_rules.limitations`）：

1. 创业板 2020-08-24 前实际为 10%，本模型统一按 20% → **早期创业板判定偏松**；
2. `MarketRules` 为静态配置，引擎不支持按日期切换比例（改它属核心交易规则，未获批）；
3. 仅以开盘价是否触及边界判定，不模拟排队/部分成交/盘中打开；
4. 不区分 ST 5% 档（池已剔除 ST）。

**口径影响**：v3 与 v1/v2 **不可直接比较**。拦截成交会同时改变笔数与收益，
v2→v3 的差值里既有「拦截效应」也有「样本变化」，不得解释为策略改进。

### 2.2 G0-2 —— D1 勘误：`simulation` 不是「最低成本档」

事实：`fee_presets.py` 中 simulation 佣金 **万 3** > broker_reference **万 2.5**，
simulation 仅过户费为 0。实测 `ma_cross` 中位 simulation −2.15% vs **broker_reference −2.13%**。

已修正 + 追加勘误（不覆写原文，符合 R5）：

| 文档 | 处理 |
|---|---|
| `docs/RETURN_SHAPE_2026-10-18.md` | 2 处措辞改为「佣金最低档（broker_reference）」+ 勘误块 |
| `docs/EVIDENCE_SYNTHESIS_2026-10-18.md` | 2 处措辞 + 文末勘误 |
| `docs/B16_DIRECTION_VERDICT_2026-10-18.md` | 1 处措辞 + 文末勘误 |
| `docs/B17_CLOSURE_REPORT_2026-10-18.md` | 3 处措辞 + **附录 E 勘误记录** |

**数值与结论均不变**：56.5% 在两档相同，三档全负方向不变，B16 推荐 ② 不受影响。

### 2.3 G0-3 —— A7 基准模块

`backend/src/deepquant/performance/benchmarks.py`

| 基准 | 函数 | 成本 | 股息口径 |
|---|---|---|---|
| B0 现金 | `cash_benchmark` | 无 | 无 |
| B1 同标的买入持有 | `buy_and_hold` | **含买卖费用** | `approximate_qfq`（声明未验证） |
| B2 市场指数 | `index_benchmark` | **不含**（指数不可交易） | `price`→`none` / `total_return`→`included` |

B1 的关键语义（均有测试）：

- 期初**首个可执行日**按开盘价买入，价格含买入滑点；
- 数量受 100 股整手 + 「成交额+费用 <= 现金」约束逐手回退；
- **买不起 1 手 → 返回不可执行，`cumulative_return=None`，绝不记 0%**；
- 期初涨停 → 顺延，并记录 `blocked_reason="limit_up"`；一直买不进 → 不可执行；
- 复用 `limit_prices` / `open_fill_block_reason`，不另写一份边界计算；
- `liquidate_at_end` 默认 `False`，选择写进 `assumptions`。

### 2.4 G0-4 —— A6 组合绩效指标 + 公共曲线统计

| 文件 | 内容 |
|---|---|
| `performance/curve.py` | `curve_stats`：累计/年化/波动/最大回撤的**唯一定义处**（基准与组合共用） |
| `performance/portfolio_metrics.py` | `compute_sharpe` / `compute_turnover` / `compute_cost_ratio` / `assess_evidence_level` / `compute_portfolio_performance` |

口径（写死，测试锁定）：

- **Sharpe** = `mean(r − rf/N) / stdev(r − rf/N, ddof=1) × sqrt(N)`；`rf` 默认 **0**；
  标准差为 0 → `None`（不是 0）；样本 < 2 → `None` + 原因。
- **换手率** = `(买+卖成交额)/2 / 平均权益 / 年数`（除 2 折单边；分母用平均权益而非初始权益）。
- **成本占毛收益比** = `总费用 / 毛收益`，毛收益 = 净收益 + 总费用；**毛收益 <= 0 → None**
  （不给「成本占比 150%」这种伪精确值）。
- **证据等级**：调仓期数 < 36 或可交易标的 < 100 → `low`，理由逐条列出（B18 §9.2 预先登记门槛）。

### 2.5 G0-5（部分）—— A1/A2 组合层配置与调度

`backend/src/deepquant/backtest/portfolio.py`

- **A1** `PortfolioBacktestConfig`：标的池、初始资金、**费率模板复用 `BacktestConfig`**
  （费用口径与单标的回测共用一处）、`allocation ∈ {weights, lots}`、单标的上限。
- **A2** `month_end_signal_dates`：每月最后**交易日**（不是最后自然日）为信号日。
- **A2** `equal_weight_targets`：截面排序取前 `top_fraction`、等权。
  - 缺失/NaN **整只剔除**，不填 0（填 0 会让缺数据标的凭空进入组合）；
  - 并列按**代码升序**（否则不可复现）；
  - 不足 `min_assets` → **空仓**（合法状态，不强行凑数）；
  - 触及单标的上限 → **降低总仓位**，不把权重挪给第二名（否则上限变成选股参数）。
- **A2** `TargetPortfolio`：负权重（做空）直接报错；权重和 > 1 报错。
- `validate_schedule`：池外标的、非递增信号日 → 报错。

---

## 3. 未完成项（Gate 0 剩余）

| # | 任务 | 状态 | 为什么现在不做 |
|---|---|---|---|
| G0-5 主体 | **A3 `run_portfolio_backtest`**（组合记账循环） | **未开始** | 与四项口径强耦合，必须先定稿：①卖出先于买入的现金释放顺序；②权重记账下费用粒度（1 股 vs 整手）与最低佣金是否生效；③停牌/缺价标的的估值冻结；④涨跌停导致的「该卖卖不出/该买买不进」顺延。口径未定就写循环，产出的是一条**看起来像组合净值但说不清口径**的曲线 |
| G0-6 | A4/A5 双口径并行报告 | 未开始 | 依赖 A3 |
| G0-7 | 合约测试 + 回归 | 部分 | 新增模块测试已就位；组合层端到端合约待 A3 |
| G0-8 | 文档：基准口径说明 + 组合层限制声明 | 部分 | 本文件是初版；A3 完成后补组合层限制全表 |

> **A6 的 `compute_portfolio_performance` 目前没有输入来源**：它要求**连续账户净值**，
> 而连续净值只有 A3 能产出。现在调用它只能得到「窗口级收益拼接」——那正是 R12 禁止的东西。
> 因此该函数在当前状态下**只被单测覆盖，不得用于任何研究结论**。

---

## 4. 测试与回归

| 项 | 结果 |
|---|---|
| 新增测试文件 | `tests/market/test_price_limit_rate.py`（10）、`tests/performance/test_benchmarks.py`（17）、`tests/performance/test_portfolio_metrics.py`（15）、`tests/backtest/test_portfolio_schedule.py`（26） |
| 契约测试新增 | `tests/test_research_script_contract.py` 增 5 条（market_rules 必传、默认 board、复用共享解析、落盘比例、off 警告）；版本断言同步为 `oos75-v3` |
| 本轮相关全量 | **1138 passed / 1 skipped / 6 xfailed / 0 failed**（`--ignore=tests/research`） |
| `tests/research/` 的 5 个失败 | **与本轮无关**：该目录属 A 线并行在途工作（`equity_bond_spread`，文件 mtime 02:15–02:18，晚于本轮开始），本轮未触碰该目录任何文件 |
| R7 只增不减 | 未删除、未弱化任何既有测试；唯一改动是把版本号断言从 v2 同步到 v3（变更留痕机制，B18 §7 A9 的同类处理） |

---

## 5. 下一步（按依赖顺序）

1. **A3 口径定稿会（0.5 天，需用户确认 2 个问题）**
   - 权重记账口径下，费用是否仍触发 `min_commission=5 元`？（建议：**是**，否则组合层会系统性低估成本）
   - 停牌标的估值：用最后可用收盘价冻结（并在结果里计 `stale_valuation_count`），还是整条净值置 `None`？（建议：**冻结 + 计数**，否则单只停牌会让全组合失效）
2. **A3 实现 + A4/A5 双口径**（6~8 人日）
3. **G0-7 端到端合约测试**（2~3 人日）：用 3 只构造数据验证「调仓 → 费用 → 净值」闭环
4. **G0-8 文档**（1~2 人日）
5. Gate 0 全部完成 → 才进入 **G1 第一轮横截面动量实验**（8~12 人日）

---

## 6. 合规自查

| 红线 | 状态 |
|---|---|
| R2 / R11 不用 OOS 选参、不按收益择优 | ✅ 未跑任何回测；组合层参数为先验设计参数，无搜索 |
| R3 不把合成行情标为真实 | ✅ 未产生新数据；`index_kind` 强制显式，不猜测 |
| R4 占位费率 ≠ 真实费率 | ✅ 三档 `verified_fields` 未改；未编造任何券商费率 |
| R5 不删失败记录 | ✅ 勘误以追加方式保留原文；D1/D2 全部留档 |
| R6 无 reset / clean / restore / checkout | ✅ |
| R7 不删不弱化既有测试 | ✅ 只增；版本断言为同步更新并加注释说明 |
| R8 不 push main、不 force push | ✅ 仅推 `workbuddy/b18-*` 分支，PR 不 merge |
| R12 窗口级统计 ≠ 连续账户收益 | ✅ 已在 `compute_portfolio_performance` docstring 与 §3 明确禁止拼接 |
| 不改 `backtest/engine.py`、不改风险阈值 | ✅ 零改动 |
| 不实现/启动实盘交易 | ✅ |
| 不把多股票伪装成单股票 | ✅ 组合逻辑独立成模块，未塞进 `Strategy` 接口 |
