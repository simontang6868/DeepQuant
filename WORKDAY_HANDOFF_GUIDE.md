# DeepQuant 工作交接指导（Workday Handoff Guide）

> **目的**：让下一位 Agent（Workday）能无缝接手 DeepQuant 项目的全部编程工作。
> 本文件整合了项目目标、设计原则、已完成阶段、代码架构、当前状态、未完成事项和下一步方向。
> **更新日期**：2026-10-16

---

## 一、项目核心目标与用户意图

### 1.1 项目是什么

DeepQuant 是一个 **A 股回测与量化研究平台**。它不只是"回测引擎"，而是一套从数据接入 → 回测 → Walk-Forward 验证 → 参数敏感性分析 → 过拟合风险审计的**完整研究流程**。

### 1.2 用户的真实目的（最重要）

用户**不是**要一个能自动赚钱的交易系统。用户的核心目的是：

1. **建立可信的、可审计的量化研究流程**
   - 每一步（数据、费用、参数选择、OOS 评估）都要有明确的来源和可复现的证据
   - 不允许"看起来不错"但实际上有未来函数 / 数据泄漏 / 费用不透明的结果

2. **诚实暴露策略的不足，而不是包装漂亮结果**
   - Step 7.5 在 6 只真实 A 股上发现：双均线 MA 正收益均值 32%、均值回归 MR 26%，**收益中位数全为 0**
   - 这不是失败，而是研究框架的价值所在 —— 它诚实地告诉你"当前策略 + 参数 + 费用 + 数据范围内，没有看到稳定 alpha"

3. **严格的时间隔离和防泄漏**
   - 参数选择只能用训练期数据
   - OOS 数据在参数选定之前绝对不能被读取
   - 预热数据不能产生窗口外交易
   - 不同窗口 / 不同标的的账户状态完全独立

4. **不做的事情（项目红线）**
   - 不做实盘交易 / 自动下单 / 券商 API
   - 不把合成行情标记为真实行情
   - 不把未经核实的费率标记为真实券商费率
   - 不用 OOS 结果反向调参
   - 不删除 / 弱化失败窗口和失败策略的记录
   - 不声称历史样本外表现 = 未来收益保证

### 1.3 当前研究的诚实结论

截至 Step 7.5，**没有证据支持 MaCrossStrategy 或 MeanReversionStrategy 在 A 股 2018-2024 上有稳定 alpha**。所有收益中位数为 0，跨标的分化严重（贵州茅台 MA 7% vs. 中国平安 MA 47%）。这是客观事实，不是 bug。

---

## 二、已完成阶段总览

项目从 Step 1 逐步迭代到 Step 7.5。以下是 **Step 6.2 之后** 的关键阶段（更早的基础回测引擎 / API / 前端在 commit `6e5e32d` 之前已完成）：

| 阶段 | 内容 | 交付 |
|---|---|---|
| **Step 6.2** | SQLite 持久化层（RunRecord / RunDocument） | `persistence/` |
| **Step 6.3** | 历史回测 API 路由 | `api/routes/backtests.py` 扩展 |
| **Step 6.4** | 完整性审计（schema / source_sha256 / fee_source / fill_date） | `persistence/repro_checklist.py` |
| **Step 6.5** | 复现清单 | `persistence/repro_checklist.py` |
| **Step 6.6-6.7** | 对比 API + 前端历史详情页 | 前端 `HistoryDetail/List` 等组件 |
| **Step 7.0** | 回测时序、未来函数、交易成本方向的可信度审查 | `tests/backtest/test_credibility.py` |
| **Step 7.1** | 固定参数 Walk-Forward 滚动 OOS 验证 | `walkforward/__init__.py` |
| **Step 7.2** | 训练期网格搜索 + 真参数选择 + 独立 OOS | `walkforward/parameter_selection.py` |
| **Step 7.3** | 参数敏感性分析 + 参数稳定性 + 8 条过拟合风险规则 | `walkforward/sensitivity.py` |
| **Step 7.4** | 真实 A 股数据接入（akshare）+ 扩展数据质量校验 | `data/validation/checks_extended.py` |
| **Step 7.5** | 多标的（6 只）× 多策略（MA + MR）× 多费用 Walk-Forward 研究 | `akshare_loader.py` / `fee_presets.py` / `mean_reversion.py` |

### Git 历史

```
6a4a1b7 docs: add Step 7.4 and Step 7.5 delivery reports     ← HEAD (已推送)
e954923 Step 7.5: akshare loader, fee presets, mean reversion...
c197e49 Step 7.4: extend data quality validation
2430989 Steps 6.2 - 7.3: persistence, credibility, Walk-Forward...
6e5e32d feat: add backtest API and React frontend (Steps 5.5-5.8)
```

**所有代码已 push 到 `origin/main`**。远程完全同步。

---

## 三、核心设计原则（必须遵守）

### 3.1 严格的时间隔离

1. **参数选择只看训练期**：`run_walkforward_v2` 在每个窗口内，先用训练期数据跑所有候选参数的回测，根据训练期指标排序选最优，**然后**才跑 OOS
2. **OOS 不参与选参**：OOS 结果只在参数选定后读取，绝不参与排序 / 过滤 / 调整
3. **预热数据不交易**：预热数据只用于策略指标计算（如 MA 填充窗口），引擎按 `start_date` 裁剪后只撮合区间内信号
4. **独立账户**：每个窗口 / 每个标的 / 每个候选参数都用独立的 `BacktestConfig`（独立 initial_cash），互不污染

### 3.2 纯函数设计

- `walkforward/` 下所有函数都是**纯函数**：不修改输入 DataFrame，不依赖全局状态
- `backtest/costs.py`、`strategies/*.py` 也都是纯函数
- 相同输入 → 相同输出

### 3.3 费用透明

- 每个 `BacktestConfig` 必须有 `fee_source` 字段
- 默认 `simulation_placeholder`（明确是占位，不是真实费率）
- Step 7.5 新增 `fee_presets.py`，提供 3 个预设，每个预设标记 `verified_fields`（哪些字段经过核实）
- 印花税 0.05%（仅卖方）、过户费 0.001%（双向）已核实；佣金 / 滑点是参考值 / 假设

### 3.4 数据可追溯

- 每份导入的数据都有 SHA-256 哈希（`data/pipeline.py`）
- akshare 拉取的真实数据 SHA-256 记录在 `data/step75/step75_research_summary.json`
- 原始行情 CSV 不进 Git（几 MB，公开可重复下载）

### 3.5 红线（绝对不能做）

| 行为 | 原因 |
|---|---|
| git reset / clean / restore / checkout | 保护历史成果 |
| 删除 / 弱化既有测试 | 保持质量基线 |
| 用 OOS 数据选参 | 数据泄漏 |
| 把合成行情标记为真实 | 研究诚信 |
| 把占位费率标记为真实券商费率 | 费用透明 |
| 实盘交易 / 自动下单 / 券商 API | 项目定位是研究 |
| 大范围重构引擎 | 最小改动原则 |

---

## 四、代码架构与关键文件

### 4.1 目录结构

```
backend/src/deepquant/
├── api/                    # FastAPI 路由（历史回测、对比、完整性、复现）
│   ├── app.py
│   ├── routes/backtests.py
│   └── schemas.py
├── backtest/               # 回测核心
│   ├── engine.py           # run_backtest（最小改动：信号在完整 df 上生成，再按 start_date 裁剪）
│   ├── config.py           # BacktestConfig（含 6 个费率参数 + fee_source）
│   ├── costs.py            # 纯函数费用计算（佣金 max/gross×rate, min; 印花税仅卖; 过户费双向）
│   ├── fee_presets.py      # Step 7.5 新增：3 个费用预设
│   └── models.py
├── data/                   # 数据管道
│   ├── pipeline.py         # ingest_daily_csv（load → validate → clean → 落盘 → metadata）
│   ├── loaders/
│   │   ├── csv_loader.py   # CSV 加载（utf-8-sig / gb18030 编码尝试）
│   │   └── akshare_loader.py  # Step 7.5 新增：腾讯 K 线接口
│   ├── validation/
│   │   ├── checks.py           # 基础 8 条校验（ERROR + WARNING）
│   │   └── checks_extended.py  # Step 7.4 新增：时序 gap / 价格跳变 / 成交量 / 复权口径
│   └── storage/io.py       # Parquet / JSON 读写
├── market/                 # A 股规则（T+1、整手、涨跌停、tick）
│   ├── ashare.py
│   └── rules.py
├── performance/            # 绩效计算（DailyReturn / EquityPoint / PerformanceMetrics）
│   ├── metrics.py
│   └── comparison.py
├── persistence/            # SQLite 持久化（RunRecord / RunDocument / 完整性审计 / 复现清单）
│   ├── connection.py
│   ├── store.py
│   └── repro_checklist.py
├── strategies/             # 策略
│   ├── base.py             # Strategy 抽象基类（name / version / params / generate_signals）
│   ├── ma_cross.py         # 双均线交叉（fast_window < slow_window）
│   └── mean_reversion.py   # Step 7.5 新增：滚动 z-score 均值回归
├── walkforward/            # Walk-Forward 研究框架
│   ├── __init__.py             # 固定参数 WF（run_fixed_parameter_walkforward）
│   ├── parameter_selection.py  # Step 7.2：网格搜索 + 参数选择 + OOS（run_walkforward_v2）
│   └── sensitivity.py          # Step 7.3：敏感性 + 参数稳定性 + 8 条风险规则
└── analysis/rolling.py     # rolling_mean 工具
```

### 4.2 关键数据结构

| 结构 | 位置 | 说明 |
|---|---|---|
| `BacktestConfig` | `backtest/config.py` | 回测配置：symbol / initial_cash / 6 费率 / fee_source |
| `FeePreset` | `backtest/fee_presets.py` | 费用预设：3 预设 + verified_fields |
| `MaCrossStrategy` | `strategies/ma_cross.py` | 双均线交叉 |
| `MeanReversionStrategy` | `strategies/mean_reversion.py` | z-score 均值回归 |
| `GridSearchConfig` | `walkforward/parameter_selection.py` | 参数网格：fast_candidates / slow_candidates / min_train / min_oos / max_combinations |
| `WindowWFResult` | `walkforward/parameter_selection.py` | 单窗口结果：训练期候选 + 选中参数 + OOS 结果 |
| `WalkForwardSummaryV2` | `walkforward/parameter_selection.py` | 跨窗口汇总 |
| `SensitivityReport` | `walkforward/sensitivity.py` | 敏感性 + 风险审计（8 条规则） |

### 4.3 8 条过拟合风险规则（Step 7.3）

| ID | 规则 | 级别触发条件 |
|---|---|---|
| R1 | 有效 OOS 窗口数不足 | < 5 → warning |
| R2 | 正收益 OOS 窗口数不足 | < 3 → warning |
| R3 | OOS 正收益占比过低 | < 30% → elevated |
| R4 | 参数选择不稳定 | 相邻窗口参数过渡率 > 60% → warning |
| R5 | OOS 正收益集中 | Top 25% 窗口贡献 > 80% 正收益 → warning |
| R6 | 训练期 alpha 不足 | 训练期累计收益中位数 < 0 → warning |
| R7 | 费用为占位 | fee_source == simulation_placeholder → warning |
| R8 | 参数网格过大 | 候选数 / 有效窗口数 > 1 → warning |

### 4.4 数据流

```
akshare / CSV → data.loaders → data.validation → data.pipeline (落盘 + metadata)
    → strategies.generate_signals → backtest.engine (撮合 + 费用)
    → performance.metrics → walkforward.parameter_selection (WF + 选参)
    → walkforward.sensitivity (审计 + 风险规则)
```

---

## 五、环境与工具信息

### 5.1 已验证环境

| 工具 | 版本 | 路径 |
|---|---|---|
| Python | 3.14.8 | `backend/.venv/bin/python3` |
| Node.js | v24.1.0 | — |
| npm | 11.4.2 | — |
| pytest | — | `backend/.venv/bin/python3 -m pytest` |
| vitest | — | `cd frontend && npx vitest run` |

### 5.2 常用命令

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

# 真实数据研究（需 akshare）
cd /workspace/DeepQuant/backend && .venv/bin/python3 ../data/step75/research.py
```

### 5.3 数据源状态

| 数据源 | 状态 | 备注 |
|---|---|---|
| akshare `stock_zh_a_hist_tx`（腾讯） | ✅ 可用 | 当前主源，`symbol="sh600519"` 格式 |
| akshare `stock_zh_a_hist`（东方财富） | ❌ proxy 拦截 | `push2his.eastmoney.com` 被云端出口拦截 |
| baostock | ❌ login 超时 | 同样受 proxy 影响 |
| yfinance | ❌ rate limited | 雅虎限流 |

### 5.4 Git 认证

- `gh auth login --web` 已完成，`gh auth setup-git` 已配置
- `git push origin main` 可直接执行，不需要手动输密码

---

## 六、当前测试基线

| 检查项 | 结果 | 测试数 |
|---|---|---|
| 后端 pytest | ✅ | 573 passed, 1 skipped |
| 前端 Vitest | ✅ | 157 passed |
| TypeScript | ✅ | 通过 |
| Vite build | ✅ | 通过 |
| git diff --check | ✅ | 通过 |

### 测试文件分布

| 目录 | 测试数 | 覆盖 |
|---|---|---|
| `tests/strategies/` | 12 + | ma_cross + mean_reversion |
| `tests/walkforward/` | 31+22+25 | WF + 参数选择 + 敏感性 |
| `tests/backtest/` | 25+ | engine + config + costs + credibility |
| `tests/data/` | — | CSV loader + pipeline + validation |
| `tests/persistence/` | — | store + integrity + repro |
| `tests/api/` | — | backtests + history + compare + integrity + reproducibility |

---

## 七、已完成的真实 A 股研究（Step 7.5）

### 7.1 数据集

| 标的 | 行业 | 行数 | 范围 | 复权 |
|---|---|---|---|---|
| 600519.SH 贵州茅台 | 白酒 | 1699 | 2018-2024 | qfq |
| 000001.SZ 平安银行 | 银行 | 1699 | 2018-2024 | qfq |
| 601318.SH 中国平安 | 保险 | 1699 | 2018-2024 | qfq |
| 300750.SZ 宁德时代 | 新能源 | 1594 | 2018-06→2024 | qfq |
| 000858.SZ 五粮液 | 白酒 | 1699 | 2018-2024 | qfq |
| 600900.SH 长江电力 | 公用事业 | 1688 | 2018-2024 | qfq |

### 7.2 Walk-Forward 配置

- train=180, oos=60, step=60（每只约 30 窗口）
- MA 网格：fast ∈ (3,5,10,20), slow ∈ (15,25,40,60)
- MR 网格：lookback ∈ (10,20,30,50), threshold ∈ (1.5,2.0,2.5)
- 3 种费用预设：simulation / reference(已核实印花税+过户费) / high_cost

### 7.3 核心发现

- **所有 180 个 OOS 窗口收益中位数为 0** —— 两个策略在 2018-2024 A 股上无稳定 alpha
- **跨标的分化严重**：贵州茅台 MA 7% vs. 中国平安 MA 47%
- **MA 略好于 MR**（正收益 32% vs 26%），但 MR MDD 更小（0.017 vs 0.046）
- **费用敏感性低**：三种费用下正收益占比基本不变

### 7.4 研究结果文件

- `data/step75/step75_research_summary.json` —— 完整结构化结果（已进 Git）
- `data/step75/research.py` —— 研究脚本（已进 Git）
- `data/step75/*.qfq.csv` —— 6 只股票原始行情（**未进 Git**，可重下）

---

## 八、未完成事项与下一步方向

### P0：最需要先做

1. **解决"收益中位数全 0"的根本问题**
   - 可能性 A：参数网格范围不对（MA 试 (5,10,20,30,40)/(20,40,60,80,120)）
   - 可能性 B：策略本身 alpha 不足 → 需要承认，不强迫正结果
   - 可能性 C：Walk-Forward 窗口太短（60 天 OOS），改为 train=360 / oos=120 / step=120

2. **到家 push 到 GitHub** —— 已完成（commit `6a4a1b7` 已推送）

### P1：数据基础设施

3. **交易日历精确校验**
   - 当前无法区分"法定节假日" vs "数据源遗漏"
   - 方案：安装 baostock 或拉上交所/深交所交易日历 JSON

4. **akshare_loader 主源切换**
   - 当前腾讯可用，东财被拦
   - 方案：实现"东财优先 → 腾讯 fallback"双源

5. **pipeline 集成扩展校验**
   - `validate_daily_extended` 已实现但 `ingest_daily_csv` 还没自动调用
   - metadata 里缺 adjust_status / processed_sha256 / source_type

### P2：费用与研究设计

6. **佣金费率核实** —— 找一家具体券商的费率表，标记 broker_name + 有效日期
7. **除权除息处理** —— 前复权数据会回溯调整历史价格；严格回测应用不复权 + 模拟除权事件
8. **更合理的 WF 配置** —— train=360 / oos=120 / step=120

### P3：扩展研究

9. **更多策略类型** —— 动量、布林带突破、ADX 过滤
10. **跨市场时期分析** —— 拆成 2018-2020（熊）/ 2020-2022（震荡）/ 2022-2024（后疫情）
11. **Walk-Forward 结果持久化 + 前端入口** —— 把 WindowWFResult 存 SQLite，前端加只读页面

### P4：工程优化

12. **metadata schema 版本号** —— 兼容旧 metadata 读取
13. **数据目录 .gitignore 规则** —— 当前 CSV 留在 untracked，应加 `data/**/*.csv` 到 .gitignore
14. **akshare_loader 的单元测试** —— 当前未写（用 mock，不依赖网络）

---

## 九、关键设计决策记录

### 9.1 引擎的最小改动（Step 7.1）

`backtest/engine.py` 的 `run_backtest` 原本先裁剪 df 再调 strategy.generate_signals。Step 7.1 改为**先在完整 df 上生成信号，再按 start_date 裁剪信号**——这样策略能看到预热数据（MA 窗口填充），但预热段信号不会下单。

对常规无预热调用行为完全不变，483 既有测试零回归。

### 9.2 参数选择的隔离保证（Step 7.2）

`run_walkforward_v2` 的执行顺序：
1. 训练期跑所有候选 → 得到每个候选的训练期 PerformanceMetrics
2. 按训练期累计收益排序（+ MDD 次排序 + 参数字典序平局）
3. 选中参数 → 用选中参数跑 OOS
4. OOS 结果在第 3 步之后才读取

关键测试：`test_oos_data_perturbation_does_not_change_selection` —— 把 OOS close 改成 999，选中参数完全不变。

### 9.3 费用预设的 verified_fields（Step 7.5）

`FeePreset` 有 `verified_fields` 元组，明确哪些费率经过核实：
- `FEE_PRESET_REFERENCE`: verified_fields = ("stamp_duty_rate", "transfer_fee_rate")
- 佣金 / 滑点不在 verified_fields 里 → 明确是参考值 / 假设

### 9.4 原始行情数据不进 Git

6 只 A 股 CSV（每只 ~100KB）留在 untracked。理由：
- 公开数据，可通过相同 akshare 配置重新下载
- SHA-256 哈希已记录在 `step75_research_summary.json`
- 避免 Git 仓库膨胀

---

## 十、用户偏好与沟通风格

1. **自主推进，不频繁询问** —— 用户明确说"在不需要用户介入的前提下，尽可能完成整个阶段"
2. **诚实报告，不包装** —— 失败要如实报告，不要为了好看而弱化测试或伪造结果
3. **最小改动原则** —— 能复用就不重写；改引擎前必须证明现有接口无法满足需求
4. **完整交付报告** —— 每个阶段必须生成 `STEP_X.X_DELIVERY_REPORT.md`，包含环境、基线、修改文件、测试结果、未完成事项
5. **Git 安全** —— 未经明确授权不 commit / push；绝不 reset / clean / restore
6. **不要求用户在本地执行命令** —— 所有开发 / 测试 / 审查在云端完成

---

## 十一、接手检查清单（Workday 第一步）

```bash
# 1. 确认环境
cd /workspace/DeepQuant
git log --oneline -3          # 应该看到 6a4a1b7, e954923, c197e49
git status --short            # 应该只有 6 个 CSV untracked
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

# 4. 阅读关键交付报告
# STEP_7.4_DELIVERY_REPORT.md （真实数据接入）
# STEP_7.5_DELIVERY_REPORT.md （多标的研究）
```

---

## 十二、如果要开始下一阶段（Step 8）

建议方向（按用户核心目标"可信研究流程"排序）：

1. **Step 8.1：Walk-Forward 结果持久化到 SQLite + 只读 API**
   - 复用 `persistence/` 现有结构
   - 设计 `WFResultRecord` 表（窗口级 + 汇总级）
   - 新增 `GET /walkforward/results` 路由

2. **Step 8.2：前端 Walk-Forward 研究入口**
   - 复用现有前端组件风格
   - 展示窗口划分 / 参数选择过程 / OOS 结果 / 风险审计

3. **Step 8.3：除权除息与不复权数据处理**
   - 解决前复权历史价格回溯调整问题
   - 这是真实研究结果可信度的关键

4. **Step 8.4：更多策略 + 更大规模研究**
   - 增加动量 / 布林带策略
   - 扩到 20-30 只股票
   - 拆分市场时期

---

## 十三、关键文件速查

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

---

## 十四、提醒

1. **不要为了好看而修改 Step 7.3 风险规则的阈值** —— 阈值是"初步研究规则"，如果要改必须同时报告旧规则和新规则的结果
2. **不要把 6 只股票 + 2 策略 + 3 费用的研究结果包装成"策略有效"** —— 收益中位数全 0 是事实
3. **akshare 腾讯接口可能随时变化** —— 字段映射表在 `akshare_loader.py` 里，变化时更新
4. **云端 proxy 可能拦截某些域名** —— 东财被拦，腾讯通；换环境可能情况不同
5. **所有真实数据研究都要记录 fee_source** —— 不能让费用假设含糊

---

*本文件由 Step 7.5 完成后生成。所有代码已推送到 `origin/main`，HEAD = `6a4a1b7`。*
*下一步 Agent 接手后，请先执行 §11 的检查清单，再阅读 §8 的未完成事项，选择一个方向推进。*
