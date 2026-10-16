# DeepQuant 项目交接指导书（致腾讯 WorkBuddy 专家团队）

> **文档目的**：本文件是 DeepQuant 项目的完整交接指导，供腾讯 WorkBuddy 专家团队接手并持续推进直至项目完成。
> 本文件整合了项目全部历史任务指令（Step 1 → Step 7.5）、核心设计原则、代码架构、当前状态、用户新增需求（信号概念、基准对比等）以及下一步方向。
> **生成日期**：2026-10-16
> **当前 Git HEAD**：`e636048`（已推送到 `origin/main`）
> **仓库地址**：https://github.com/simontang6868/DeepQuant
> **阅读建议**：专家团队接手后，建议按 §1（项目定位）→ §11（检查清单）→ §3（已完成工作）→ §9（未完成事项）→ §10（路线图）的顺序阅读。

---

## 目录

- 第一部分：项目概述与最终目标
- 第二部分：用户明确的红线（12 条）
- 第三部分：已完成工作总览（含每阶段验收标准）
- 第四部分：用户新增需求（信号 / 基准对比 / 多策略 / 持久化）
- 第五部分：核心设计原则
- 第六部分：代码架构（目录 / 数据结构 / 风险规则）
- 第七部分：已完成的真实 A 股研究（Step 7.5）
- 第八部分：环境与工具信息
- 第九部分：未完成事项与优先级（P0-P4，16 项）
- 第十部分：建议的下一步路线图（4 条）
- 第十一部分：接手检查清单
- 第十二部分：关键文件速查表
- 第十三部分：交付报告索引
- 第十四部分：用户偏好与沟通风格
- 第十五部分：重要提醒
- 第十六部分：项目"完成"的定义

---

## 第一部分：项目概述与最终目标

### 1.1 项目定位

DeepQuant 是一个 **A 股量化研究与回测平台**。它不是一个简单的回测引擎，而是一套完整的、可审计的量化研究流程，覆盖：

```
真实数据接入 → 数据质量校验 → 策略信号生成 → 回测撮合 → 费用计算
→ Walk-Forward 滚动验证 → 训练期参数选择 → 样本外（OOS）评估
→ 参数敏感性分析 → 过拟合风险审计 → 研究报告
```

### 1.2 项目的最终目标（用户核心意图）

用户的目标**不是**开发一个自动赚钱的交易系统，而是建立一套**可信、可审计、能诚实暴露策略缺陷**的量化研究框架。具体而言：

1. **可信的数据基础**：真实 A 股行情，有明确来源、SHA-256 摘要、复权口径、质量校验
2. **严格的时间隔离**：参数选择只能用训练期数据，OOS 数据绝不提前泄漏
3. **透明的费用模型**：每笔费用有来源标记（占位 / 已核实 / 假设），不把模拟费率伪装成真实券商费率
4. **诚实的风险暴露**：过拟合风险、参数敏感性、样本量不足都要明确报告，不包装漂亮结果
5. **可复现的研究**：所有实验配置、数据摘要、费用假设、窗口切分都能完整复现

### 1.3 当前诚实结论（截至 Step 7.5）

截至目前，**没有证据支持 MaCrossStrategy 或 MeanReversionStrategy 在 A 股 2018-2024 上具有稳定 alpha**：

- 6 只股票 × 2 策略 × 3 费用 = 36 组实验，共 180 个 OOS 窗口
- **所有 OOS 窗口收益中位数为 0**
- 跨标的分化严重（贵州茅台 MA 正收益 7% vs. 中国平安 MA 47%）
- 这是客观事实，不是 bug，也不是失败——研究框架正确地暴露了策略的不足

---

## 第二部分：用户明确的红线（12 条，绝对不能违反）

| 编号 | 红线 | 原因 | 违反后果 |
|---|---|---|---|
| R1 | 不做实盘交易 / 自动下单 / 券商 API | 项目定位是研究，不是交易执行 | 项目方向偏离 |
| R2 | 不用 OOS 数据选参 | 防止未来函数和数据泄漏 | 研究结果无效 |
| R3 | 不把合成行情标记为真实行情 | 研究诚信 | 虚假结论 |
| R4 | 不把占位费率标记为真实券商费率 | 费用透明 | 费用假设失真 |
| R5 | 不删除 / 弱化失败窗口和失败策略记录 | 诚实暴露不足 | 研究偏差 |
| R6 | 不 git reset / clean / restore / checkout | 保护历史成果 | 数据丢失 |
| R7 | 不删除或弱化既有测试 | 保持质量基线 | 回归风险 |
| R8 | 不擅自 commit / push | Git 安全（需用户明确授权） | 版本混乱 |
| R9 | 不把历史样本外表现说成未来收益保证 | 研究诚信 | 误导用户 |
| R10 | 不为了好看而修改风险规则阈值 | 审计规则的独立性 | 审计失效 |
| R11 | 不把所有候选参数的 OOS 收益排序后再选最优 | 二次数据泄漏 | 过拟合 |
| R12 | 不把窗口级统计冒充连续账户收益 | 统计语义透明 | 错误解读 |

---

## 第三部分：已完成工作总览

### 3.1 阶段演进总表

| 阶段 | 内容 | 核心交付文件 | 验收标准 | 交付报告 |
|---|---|---|---|---|
| Step 1-5 | 基础回测引擎 + API + React 前端 | `backtest/`、`api/`、`frontend/` | — | — |
| Step 6.2 | SQLite 持久化层 | `persistence/`（RunRecord / RunDocument） | — | — |
| Step 6.3 | 历史回测 API 路由 | `api/routes/backtests.py` | — | — |
| Step 6.4 | 完整性审计（schema / source_sha256 / fee_source / fill_date） | `persistence/repro_checklist.py` | — | — |
| Step 6.5 | 复现清单 | `persistence/repro_checklist.py` | — | — |
| Step 6.6-6.7 | 对比 API + 前端历史详情页 | 前端组件 | — | — |
| Step 7.0 | 回测时序、未来函数、交易成本方向可信度审查 | `tests/backtest/test_credibility.py`（17 条测试） | — | — |
| Step 7.1 | 固定参数 Walk-Forward 滚动 OOS 验证 | `walkforward/__init__.py` | 13/13 通过 | STEP_7.1_DELIVERY_REPORT.md |
| Step 7.2 | 训练期网格搜索 + 参数选择 + 独立 OOS | `walkforward/parameter_selection.py` | 13/13 通过 | STEP_7.2_DELIVERY_REPORT.md |
| Step 7.3 | 参数敏感性分析 + 8 条过拟合风险规则 | `walkforward/sensitivity.py` | 12/12 通过 | STEP_7.3_DELIVERY_REPORT.md |
| Step 7.4 | 真实 A 股数据接入（akshare）+ 扩展数据质量校验 | `data/validation/checks_extended.py` | 11/11 通过 | STEP_7.4_DELIVERY_REPORT.md |
| Step 7.5 | 6 标的 × 2 策略 × 3 费用 Walk-Forward 研究 | `akshare_loader.py` / `fee_presets.py` / `mean_reversion.py` | 11/11 通过 | STEP_7.5_DELIVERY_REPORT.md |

### 3.2 Step 7.1 引擎最小改动（关键设计决策）

Step 7.1 对引擎做了**唯一一次最小改动**（约 30 行）：

**改动前**：`run_backtest` 先按 `start_date`/`end_date` 裁剪 df，再调 `strategy.generate_signals(df)`
**改动后**：先在完整 df 上调 `strategy.generate_signals(df_full)`（拿到含预热的全量 signals），再按 `start_date`/`end_date` 裁剪 signals

**原因**：让策略能看到 `start_date` 之前的预热数据（MA 等指标在 OOS 起点即可有效）。对常规调用（无预热）行为完全不变——483 既有测试零回归。

### 3.3 Step 7.2 参数选择 8 步流程

每个 Walk-Forward 窗口严格执行：

1. 读取该窗口的训练期行情
2. 构造候选参数集合（确定性网格）
3. 对每组候选参数单独运行训练期回测（独立策略实例 + 独立账户）
4. 仅根据训练期结果计算选择指标
5. 按预先指定的选择规则选出一组参数（训练期累计收益 desc → 最大回撤 asc → 参数字典序）
6. 固定选定参数
7. 再使用选定参数运行该窗口的 OOS 回测（独立策略实例 + 独立初始账户）
8. 记录训练期选择结果和 OOS 评估结果

### 3.4 Git 历史

```
e636048 docs: rewrite handoff guide for Tencent WorkBuddy expert team  ← HEAD（已推送）
36a9b4d docs: add Workday handoff guide (project overview...)
6a4a1b7 docs: add Step 7.4 and Step 7.5 delivery reports
e954923 Step 7.5: akshare loader, fee presets, mean reversion...
c197e49 Step 7.4: extend data quality validation
2430989 Steps 6.2 - 7.3: persistence, credibility, Walk-Forward...
6e5e32d feat: add backtest API and React frontend (Steps 5.5-5.8)
```

### 3.5 当前测试基线

| 检查项 | 结果 | 测试数 | 测试文件分布 |
|---|---|---|---|
| 后端 pytest | ✅ 通过 | 573 passed, 1 skipped | 20+ 测试文件 |
| 前端 Vitest | ✅ 通过 | 157 passed | — |
| TypeScript | ✅ 通过 | — | — |
| Vite build | ✅ 通过 | — | — |
| git diff --check | ✅ 通过 | — | — |

**后端测试文件分布**（关键）：
- `tests/backtest/test_engine.py` — 25+ 条引擎测试
- `tests/backtest/test_config.py` — 配置校验
- `tests/backtest/test_costs.py` — 费用计算
- `tests/backtest/test_models.py` — 模型
- `tests/backtest/test_credibility.py` — 17 条可信度测试（Step 7.0）
- `tests/market/test_ashare.py` / `test_rules.py` — A 股规则
- `tests/strategies/test_ma_cross.py` — MA 策略
- `tests/strategies/test_mean_reversion.py` — MR 策略（12 条，Step 7.5）
- `tests/performance/test_metrics.py` — 绩效计算
- `tests/api/test_backtests_api.py` / `test_backtest_history_api.py` — API 集成
- `tests/walkforward/test_walkforward.py` — 22 条 WF 测试（Step 7.1）
- `tests/walkforward/test_parameter_selection.py` — 22 条参数选择测试（Step 7.2）
- `tests/walkforward/test_sensitivity.py` — 25 条敏感性测试（Step 7.3）

---

## 第四部分：用户新增需求（尚未实现，需专家团队推进）

> 以下是用户在开发过程中**断断续续提出的新方向**，尚未在代码中实现。专家团队接手后应优先评估并推进。

### 4.1 引入"信号"（Signal）概念

**用户需求**：引入标准化的信号概念，让策略输出不再是直接的买卖指令，而是结构化的信号对象。

**当前状态**：现有策略（`MaCrossStrategy`、`MeanReversionStrategy`）直接在 `generate_signals(df)` 中返回 `Signals`（包含 buy / sell 列），信号和执行指令耦合。

**期望方向**：
1. 定义独立的 `Signal` 数据结构（如 dataclass），包含：
   - 信号方向（看多 / 看空 / 中性）
   - 信号强度（置信度，0-1）
   - 信号生成时间
   - 信号来源策略
   - 信号有效期
2. 策略只负责生成 `Signal`，不直接决定下单
3. 引入独立的"信号 → 订单"转换层（SignalExecutor）
4. 支持多信号组合（如投票、加权、过滤）

**价值**：
- 多策略对比时，信号层面可比
- 可以做信号组合（ensemble）
- 信号和执行解耦，便于研究信号本身的预测能力

**约束**：不要破坏现有策略接口，573 条测试必须全过。

### 4.2 与基准（Benchmark）对比

**用户需求**：策略结果要能和基准对比，而不是孤立看收益。

**当前状态**：`walkforward` 结果只看策略自身的 OOS 收益，没有和 Buy & Hold 或指数基准对比。

**期望方向**：
1. 实现 Buy & Hold 基准（同一标的、同一区间、同一初始资金）
2. 实现相对收益（alpha）计算
3. 在 Walk-Forward 每个窗口都计算策略 vs 基准的相对表现
4. 引入指数基准（如沪深 300、中证 500）——需要相应数据源
5. 跨窗口汇总 alpha 的统计量

**价值**：
- 判断策略是否真的"跑赢市场"，而不是绝对收益
- 区分"市场 beta"和"策略 alpha"

**约束**：基准要和策略用同一数据 / 费用 / 窗口配置，否则对比不公平。

### 4.3 多策略异质对比

**用户需求**：不同原理的策略之间要能公平对比。

**当前状态**：Step 7.5 已实现 MA（趋势）和 MR（均值回归）两种异质策略，但对比口径还不够标准化。

**期望方向**：
1. 统一策略接口（参数 / 信号 / 评价指标）
2. 同一数据集、同一费用、同一 WF 配置下的横向对比
3. 不同市场时期的策略表现对比（熊 / 牛 / 震荡）

### 4.4 研究结果的结构化持久化与可复查

**用户需求**：实验结果要能保存、检索、复查，而不是每次重新跑。

**当前状态**：Walk-Forward 结果只在内存中，`step75_research_summary.json` 是手动导出的。

**期望方向**：
1. 设计 `WFResultRecord` 持久化结构（窗口级 + 汇总级）
2. 复用现有 `persistence/` 的 SQLite 层
3. 提供只读检索 API
4. 前端研究结果展示页

---

## 第五部分：核心设计原则

### 5.1 严格的时间隔离

1. **参数选择只看训练期**：`run_walkforward_v2` 先跑所有候选的训练期回测，按训练期指标排序选最优，**然后**才跑 OOS
2. **OOS 不参与选参**：OOS 结果在参数选定后才读取
3. **预热数据不交易**：预热段只用于指标计算，引擎按 `start_date` 裁剪后只撮合区间内信号
4. **独立账户**：每个窗口 / 标的 / 候选都用独立 `BacktestConfig`（独立 initial_cash）

**关键回归测试**：`test_oos_data_perturbation_does_not_change_selection` —— 把 OOS close 改成 999，选中参数完全不变。

### 5.2 纯函数设计

- `walkforward/`、`backtest/costs.py`、`strategies/*.py` 都是纯函数
- 不修改输入 DataFrame，不依赖全局状态
- 相同输入 → 相同输出

### 5.3 费用透明

- 每个 `BacktestConfig` 有 `fee_source` 字段
- 默认 `simulation_placeholder`（明确占位）
- Step 7.5 新增 `FeePreset`，每个预设标记 `verified_fields`（哪些费率经过核实）
- 已核实：印花税 0.05%（仅卖）、过户费 0.001%（双向）
- 未核实：佣金比例 / 滑点（标记为参考值 / 假设）

### 5.4 数据可追溯

- 每份导入数据有 SHA-256 哈希
- akshare 拉取的真实数据哈希记录在 `step75_research_summary.json`
- 原始行情 CSV 不进 Git（公开可重下，哈希可验证）

---

## 第六部分：代码架构

### 6.1 目录结构

```
backend/src/deepquant/
├── api/                    # FastAPI 路由
│   ├── app.py
│   ├── routes/backtests.py
│   └── schemas.py
├── backtest/               # 回测核心
│   ├── engine.py           # run_backtest（信号在完整 df 生成，再按 start_date 裁剪）
│   ├── config.py           # BacktestConfig（6 费率 + fee_source）
│   ├── costs.py            # 纯函数费用计算
│   ├── fee_presets.py      # 3 个费用预设（simulation / reference / high_cost）
│   └── models.py
├── data/                   # 数据管道
│   ├── pipeline.py         # ingest_daily_csv
│   ├── loaders/
│   │   ├── csv_loader.py
│   │   └── akshare_loader.py   # 腾讯 K 线接口
│   ├── validation/
│   │   ├── checks.py           # 基础 8 条校验
│   │   └── checks_extended.py  # 时序 gap / 价格跳变 / 成交量 / 复权口径
│   └── storage/io.py
├── market/                 # A 股规则（T+1、整手、涨跌停）
│   ├── ashare.py
│   └── rules.py
├── performance/            # 绩效计算
│   ├── metrics.py
│   └── comparison.py
├── persistence/            # SQLite 持久化
│   ├── connection.py
│   ├── store.py
│   └── repro_checklist.py
├── strategies/             # 策略
│   ├── base.py             # Strategy 抽象基类
│   ├── ma_cross.py         # 双均线交叉
│   └── mean_reversion.py   # z-score 均值回归
├── walkforward/            # Walk-Forward 研究框架
│   ├── __init__.py             # 固定参数 WF
│   ├── parameter_selection.py  # 网格搜索 + 参数选择 + OOS
│   └── sensitivity.py          # 敏感性 + 8 条风险规则
└── analysis/rolling.py
```

### 6.2 关键数据结构

| 结构 | 位置 | 说明 |
|---|---|---|
| `BacktestConfig` | `backtest/config.py` | 回测配置（symbol / start / end / initial_cash / 6 费率 / fee_source） |
| `FeePreset` | `backtest/fee_presets.py` | 费用预设 + verified_fields |
| `MaCrossStrategy` | `strategies/ma_cross.py` | 双均线交叉（fast_window / slow_window，约束 slow > fast） |
| `MeanReversionStrategy` | `strategies/mean_reversion.py` | z-score 均值回归（lookback / threshold） |
| `GridSearchConfig` | `walkforward/parameter_selection.py` | 参数网格配置（fast/slow 候选 / min_periods / min_trades / max_combinations） |
| `WindowWFResult` | `walkforward/parameter_selection.py` | 单窗口结果（窗口信息 / 参数选择 / OOS 评估） |
| `WalkForwardSummaryV2` | `walkforward/parameter_selection.py` | 跨窗口汇总（selection 成功 / oos 成功 / valid metrics 分离） |
| `SensitivityReport` | `walkforward/sensitivity.py` | 敏感性 + 风险审计 |

### 6.3 WalkForwardConfig 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `train_window` | int | 训练期交易日数 |
| `oos_window` | int | OOS 交易日数 |
| `step` | int | 滚动步长（通常 = oos_window 为无重叠） |
| `warmup` | int | 指标预热交易日数（0 表示不用额外预热） |
| `min_train` / `min_oos` | int | 最小有效长度 |
| `start_date` / `end_date` | Timestamp | 整体时间边界（可选） |

### 6.4 费用预设（Step 7.5）

| 预设 | fee_source | 佣金 | 印花税 | 过户费 | 滑点 | verified_fields |
|---|---|---|---|---|---|---|
| `simulation` | simulation_placeholder | 0.025% | 0.05%（卖） | 0.001% | 0.01% | （无） |
| `reference` | verified_reference | 0.025% | 0.05%（卖） | 0.001% | 0.01% | stamp_duty, transfer_fee |
| `high_cost` | research_assumption | 0.05% | 0.05%（卖） | 0.001% | 0.05% | （无，标记为假设） |

### 6.5 8 条过拟合风险规则（Step 7.3）

| ID | 规则 | 触发条件 | 级别 |
|---|---|---|---|
| R1 | 有效 OOS 窗口数不足 | < 5 | warning |
| R2 | 正收益 OOS 窗口数不足 | < 3 | warning |
| R3 | OOS 正收益占比过低 | < 30% | elevated |
| R4 | 参数选择不稳定 | 相邻窗口参数过渡率 > 60% | warning |
| R5 | OOS 正收益集中 | Top 25% 窗口贡献 > 80% 正收益 | warning |
| R6 | 训练期 alpha 不足 | 训练期累计收益中位数 < 0 | warning |
| R7 | 费用为占位 | fee_source == simulation_placeholder | warning |
| R8 | 参数网格过大 | 候选数 / 有效窗口数 > 1 | warning |

**注意**：`not_triggered` 只表示未触发当前规则，**不代表策略没有过拟合**。

### 6.6 akshare 字段映射（腾讯 K 线接口）

| akshare 字段 | 标准字段 | 说明 |
|---|---|---|
| `date` | `date` | 交易日期 |
| `open` | `open` | 开盘价 |
| `close` | `close` | 收盘价 |
| `high` | `high` | 最高价 |
| `low` | `low` | 最低价 |
| `amount` | `volume` | 成交量（注意：腾讯接口字段名是 amount，实际含义是成交量） |

**数据源接口**：`ak.stock_zh_a_hist_tx(symbol='sh600519', start_date='20180101', end_date='20241231', adjust='qfq')`

---

## 第七部分：已完成的真实 A 股研究（Step 7.5）

### 7.1 数据集

| 标的 | 行业 | 行数 | 范围 | 复权 | SHA-256（前 16 位） |
|---|---|---|---|---|---|
| 600519.SH 贵州茅台 | 白酒 | 1699 | 2018-2024 | qfq | （见 step75_research_summary.json） |
| 000001.SZ 平安银行 | 银行 | 1699 | 2018-2024 | qfq | （同上） |
| 601318.SH 中国平安 | 保险 | 1699 | 2018-2024 | qfq | （同上） |
| 300750.SZ 宁德时代 | 新能源 | 1594 | 2018-06→2024 | qfq | （同上） |
| 000858.SZ 五粮液 | 白酒 | 1699 | 2018-2024 | qfq | （同上） |
| 600900.SH 长江电力 | 公用事业 | 1688 | 2018-2024 | qfq | （同上） |

### 7.2 Walk-Forward 配置

- train=180, oos=60, step=60
- MA 网格：fast ∈ (3,5,10,20), slow ∈ (15,25,40,60)
- MR 网格：lookback ∈ (10,20,30,50), threshold ∈ (1.5,2.0,2.5)
- 3 种费用预设：simulation / reference（印花税+过户费已核实）/ high_cost

### 7.3 核心发现

- **所有 180 个 OOS 窗口收益中位数为 0**
- MA 正收益均值 32%，MR 26%；但 MR 最大回撤更小（0.017 vs 0.046）
- 跨标的分化严重（贵州茅台 MA 7% vs. 中国平安 MA 47%）
- 费用敏感性低：三种费用下正收益占比基本不变（收益太稀薄，费用差异被摊薄）

### 7.4 研究结果文件

- `data/step75/step75_research_summary.json` — 完整结构化结果（已进 Git）
- `data/step75/research.py` — 研究脚本（已进 Git，可直接运行）
- `data/step75/*.qfq.csv` — 6 只股票原始行情（**未进 Git**，可重下）

---

## 第八部分：环境与工具信息

### 8.1 已验证环境

| 工具 | 版本 | 路径 |
|---|---|---|
| Python | 3.14.8 | `backend/.venv/bin/python3` |
| Node.js | v24.1.0 | — |
| npm | 11.4.2 | — |
| akshare | 1.19.1 | 已安装 |

### 8.2 常用命令

```bash
# 后端测试
cd /workspace/DeepQuant/backend && .venv/bin/python3 -m pytest tests/ -q

# 前端测试
cd /workspace/DeepQuant/frontend && npx vitest run

# TypeScript 检查
cd /workspace/DeepQuant/frontend && npx tsc --noEmit

# Vite build
cd /workspace/DeepQuant/frontend && npm run build

# git diff 检查
cd /workspace/DeepQuant && git diff --check

# 真实数据研究
cd /workspace/DeepQuant/backend && .venv/bin/python3 ../data/step75/research.py
```

### 8.3 数据源状态

| 数据源 | 状态 | 备注 |
|---|---|---|
| akshare `stock_zh_a_hist_tx`（腾讯） | ✅ 可用 | 当前主源 |
| akshare `stock_zh_a_hist`（东方财富） | ❌ proxy 拦截 | 云端出口拦截 |
| baostock | ❌ login 超时 | proxy 影响 |
| yfinance | ❌ rate limited | 雅虎限流 |

### 8.4 Git 认证

- `gh auth login --web` 已完成，`gh auth setup-git` 已配置
- `git push origin main` 可直接执行

### 8.5 数据目录结构

```
data/
└── step75/
    ├── research.py                    # 研究脚本（已进 Git）
    ├── step75_research_summary.json   # 研究结果（已进 Git）
    ├── 600519.SH.qfq.csv              # 贵州茅台（未进 Git）
    ├── 000001.SZ.qfq.csv              # 平安银行（未进 Git）
    ├── 601318.SH.qfq.csv              # 中国平安（未进 Git）
    ├── 300750.SZ.qfq.csv              # 宁德时代（未进 Git）
    ├── 000858.SZ.qfq.csv              # 五粮液（未进 Git）
    └── 600900.SH.qfq.csv              # 长江电力（未进 Git）
```

---

## 第九部分：未完成事项与优先级

### P0：核心研究问题（最优先）

1. **解决"收益中位数全 0"**
   - 方案 A：扩大 / 调整参数网格（MA 试 fast ∈ (5,10,20,30,40), slow ∈ (20,40,60,80,120)）
   - 方案 B：调整 WF 窗口配置（train=360 / oos=120 / step=120，更长训练期和 OOS）
   - 方案 C：如果调整后仍为 0，承认策略 alpha 不足，转向信号层研究

2. **引入 Signal 概念（用户明确要求）**
   - 定义 `Signal` 数据结构
   - 策略输出 Signal 而非直接订单
   - 引入 SignalExecutor 转换层

### P1：数据基础设施

3. **交易日历精确校验** —— 区分法定节假日 vs 数据源遗漏
4. **akshare 双源 fallback** —— 东财优先 → 腾讯 fallback
5. **pipeline 集成扩展校验** —— metadata 增加 adjust_status / processed_sha256 / source_type

### P2：研究设计

6. **Buy & Hold 基准对比（用户明确要求）** —— 每个窗口计算 alpha
7. **佣金费率核实** —— 找具体券商费率表，标记 broker_name + 有效日期
8. **除权除息处理** —— 前复权历史价格回溯调整问题
9. **更合理的 WF 配置** —— train=360 / oos=120 / step=120

### P3：扩展研究

10. **更多策略** —— 动量、布林带突破、ADX 过滤
11. **跨市场时期分析** —— 2018-2020（熊）/ 2020-2022（震荡）/ 2022-2024（后疫情）
12. **Walk-Forward 结果持久化 + 前端入口** —— SQLite + 只读页面
13. **多信号组合（ensemble）** —— 投票 / 加权 / 过滤

### P4：工程优化

14. **metadata schema 版本号** —— 兼容旧 metadata
15. **数据目录 .gitignore 规则** —— `data/**/*.csv` 加入忽略
16. **akshare_loader 单元测试** —— 用 mock，不依赖网络

---

## 第十部分：建议的下一步路线图

专家团队接手后，建议按以下顺序推进：

### 路线图 1：信号层重构（用户核心新需求）

```
Step 8.1: 定义 Signal 数据结构 + SignalExecutor 接口
Step 8.2: 改造 MaCrossStrategy / MeanReversionStrategy 输出 Signal
Step 8.3: 引擎适配 Signal → Order 转换
Step 8.4: 回归测试（确保所有 573 测试仍通过）
Step 8.5: 新增信号层面的评价指标（信号准确率、信号延迟等）
```

### 路线图 2：基准对比（用户核心新需求）

```
Step 9.1: 实现 BuyAndHoldStrategy（基准策略）
Step 9.2: Walk-Forward 每个窗口计算策略 vs 基准 alpha
Step 9.3: 跨窗口汇总 alpha 统计量
Step 9.4: 引入沪深 300 指数基准（需数据源）
```

### 路线图 3：研究结果持久化

```
Step 10.1: 设计 WFResultRecord 表结构
Step 10.2: 实现 SQLite 存储 + 检索
Step 10.3: 新增只读 API 路由
Step 10.4: 前端研究结果展示页
```

### 路线图 4：更多策略 + 更大规模研究

```
Step 11.1: 新增动量策略、布林带策略
Step 11.2: 扩到 20-30 只股票
Step 11.3: 拆分市场时期分析
Step 11.4: 多信号组合实验
```

---

## 第十一部分：接手检查清单

专家团队接手后，**第一步**执行以下命令确认环境：

```bash
# 1. 确认 Git 状态
cd /workspace/DeepQuant
git log --oneline -5          # 应看到 e636048, 36a9b4d, 6a4a1b7, e954923, c197e49
git status --short            # 应只有 6 个 CSV untracked
git pull origin main          # 确认远程同步

# 2. 跑测试基线
cd backend && .venv/bin/python3 -m pytest tests/ -q --tb=no
# 期望：573 passed, 1 skipped

# 3. 确认数据源可用
.venv/bin/python3 -c "
import akshare as ak
df = ak.stock_zh_a_hist_tx(symbol='sh600519', start_date='20240101', end_date='20241231', adjust='qfq')
print(f'rows={len(df)}')
"
# 期望：rows > 200

# 4. 阅读关键交付报告
# STEP_7.2_DELIVERY_REPORT.md（参数选择）
# STEP_7.3_DELIVERY_REPORT.md（敏感性 + 风险规则）
# STEP_7.4_DELIVERY_REPORT.md（真实数据接入）
# STEP_7.5_DELIVERY_REPORT.md（多标的研究）
# 本文件（交接指导）
```

---

## 第十二部分：关键文件速查表

| 你想做的事 | 看哪个文件 |
|---|---|
| 改回测引擎逻辑 | `backtest/engine.py` |
| 改费用计算 | `backtest/costs.py`（纯函数）+ `backtest/fee_presets.py`（预设） |
| 加新策略 | 仿照 `strategies/mean_reversion.py`，继承 `strategies/base.py` |
| 加新费用预设 | `backtest/fee_presets.py` 的 `ALL_PRESETS` |
| 改 Walk-Forward 窗口生成 | `walkforward/__init__.py` |
| 改参数选择逻辑 | `walkforward/parameter_selection.py` 的 `_select_best_candidate` |
| 改风险规则 | `walkforward/sensitivity.py` 的 `_risk_rules` |
| 加新数据源 | `data/loaders/akshare_loader.py` |
| 改数据校验 | `data/validation/checks.py`（基础）+ `checks_extended.py`（扩展） |
| 改数据导入流程 | `data/pipeline.py` |
| 跑真实数据研究 | `data/step75/research.py` |
| 看持久化结构 | `persistence/connection.py` + `persistence/store.py` |
| 看 API 路由 | `api/routes/backtests.py` |
| 看前端组件 | `frontend/src/` |

---

## 第十三部分：交付报告索引

| 报告 | 路径 | 内容 |
|---|---|---|
| Step 7.2 交付报告 | `STEP_7.2_DELIVERY_REPORT.md` | 训练期参数选择 + OOS 隔离 |
| Step 7.3 交付报告 | `STEP_7.3_DELIVERY_REPORT.md` | 参数敏感性 + 8 条风险规则 |
| Step 7.4 交付报告 | `STEP_7.4_DELIVERY_REPORT.md` | 真实 A 股数据接入 |
| Step 7.5 交付报告 | `STEP_7.5_DELIVERY_REPORT.md` | 6 标的 × 2 策略 × 3 费用研究 |
| 交接指导（本文件） | `DEEPQUANT_HANDOFF_TO_WORKBUDDY.md` | 完整交接指导 |

---

## 第十四部分：用户偏好与沟通风格

1. **自主推进，不频繁询问** —— 用户明确要求"在不需要用户介入的前提下，尽可能完成整个阶段"
2. **诚实报告，不包装** —— 失败如实报告，不为好看而弱化测试或伪造结果
3. **最小改动原则** —— 能复用就不重写；改引擎前必须证明现有接口无法满足需求
4. **完整交付报告** —— 每个阶段生成 `STEP_X.X_DELIVERY_REPORT.md`，包含环境、基线、修改文件、测试结果、未完成事项
5. **Git 安全** —— 未经明确授权不 commit / push；绝不 reset / clean / restore
6. **不要求用户在本地执行命令** —— 所有开发 / 测试在云端完成

---

## 第十五部分：重要提醒

1. **不要为了好看修改 Step 7.3 风险规则阈值** —— 如需修改，必须同时报告旧规则和新规则的结果
2. **不要把 6 只股票 + 2 策略 + 3 费用的研究结果包装成"策略有效"** —— 收益中位数全 0 是事实
3. **akshare 腾讯接口可能随时变化** —— 字段映射表在 `akshare_loader.py`
4. **云端 proxy 可能拦截某些域名** —— 东财被拦，腾讯通；换环境可能不同
5. **所有真实数据研究都要记录 fee_source** —— 不能让费用假设含糊
6. **引入 Signal 概念时不要破坏现有策略接口** —— 保持向后兼容，573 测试必须全过
7. **基准对比要和策略用同一数据 / 费用 / 窗口配置** —— 否则对比不公平

---

## 第十六部分：项目"完成"的定义

用户希望专家团队接手后**持续推进直到完成**。这里定义"完成"的标准：

### 工程完成标准
- [ ] 真实数据接入、质量校验、来源追踪完整
- [ ] 可配置费用模型（有核实来源，非占位）
- [ ] 至少 2 种异质策略经过测试
- [ ] Walk-Forward + 参数选择 + OOS 隔离机制完整
- [ ] 参数敏感性 + 过拟合风险审计完整
- [ ] 全量回归测试（后端 + 前端 + 构建）持续通过

### 研究完成标准
- [ ] 至少 10-20 只 A 股的真实数据研究
- [ ] 策略 vs 基准（Buy & Hold / 指数）的 alpha 分析
- [ ] 跨市场时期（熊 / 牛 / 震荡）的表现分析
- [ ] 多策略异质对比
- [ ] 研究结果可持久化、可复查

### 研究结论标准
- [ ] 明确回答：当前策略在 A 股上是否有稳定 alpha？
- [ ] 如果有，alpha 的来源是什么（趋势 / 均值回归 / 其他）？
- [ ] 如果没有，是策略问题、参数问题、还是市场环境问题？
- [ ] 所有结论都有数据支撑，不夸大、不隐瞒

---

*本文件由 Step 7.5 完成后生成，面向腾讯 WorkBuddy 专家团队。*
*所有代码已推送到 `origin/main`，HEAD = `e636048`。*
*专家团队接手后，请先执行 §11 的检查清单，再阅读 §9 的未完成事项，选择方向推进。*
