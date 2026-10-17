# DeepQuant

面向个人投资者的量化研究、策略回测与自动化交易平台（个人独立项目）。

> 名称说明：DeepQuant 的命名表达了对 DeepSeek 的敬意，但本项目为**独立个人项目**，
> 与 DeepSeek 无官方合作或隶属关系。

## 当前状态

**第一阶段 · Step 0 – Step 5.8 进行中**（回测 API 与可视化前端已完成开发，尚未提交 Git）：

- Step 0：仓库骨架；
- Step 1：后端最小可跑（venv + 最小依赖 + `/health` 接口 + pytest）；
- Step 2：数据层（CSV 导入、行情校验、确定性清洗、原始/处理分离落盘 + 元数据）；
- Step 3：时间序列分析（简单/对数收益率、滚动均值/标准差、年化波动率）+ Parquet 存储；
- Step 4：策略层（信号契约 + 双均线交叉策略，事件型信号、无未来函数）；
- Step 5.2.1–5.2.2：市场规则基础模块 + 回测配置与核心数据结构（Order / Fill / Position / AccountState / BacktestResult）；
- Step 5.2.3：费用与滑点模块（佣金 / 印花税 / 过户费 / 比例滑点，纯函数）；
- Step 5.2.4：回测执行引擎（T+1、涨跌停、手数、逐手回退、权益曲线、完整性判定）；
- Step 5.2.5：绩效分析、JSON 序列化与可复现性（每日收益率、最大回撤、交易统计、数据集摘要、策略元数据）；
- Step 5.2.6：系统级验收与集成（引擎自动填充绩效指标、跨模块端到端测试）；
- Step 5.3：数据层加固（CSV UTF-8/GB18030 编码兼容）、端到端数据契约测试与工程文档；
- Step 5.5：Windows 跨平台验收通过（310 passed），路径回归测试补充；
- Step 5.6：回测 API 首版（`GET /datasets`、`GET /strategies`、`POST /backtests`，显式策略注册表 + 数据路径安全 + 统一异常映射）；
- Step 5.7：跨平台回归验证、代码安全审查与版本基线固化（新增 junction 逃逸测试；Linux 回归验证待具备环境后执行）；
- Step 5.8：回测结果可视化前端（React + Vite，见下文「前端」章节）。
- Step 8.1–8.5'（信号研究旁路层，纯加法）：结构化信号层 `strategies/signals.py`、信号级持久化 `persistence/signal_store.py`、信号评价指标 `analysis/signal_metrics.py`、多信号组合 `strategies/ensemble*.py`、ensemble 评价接入 `analysis/ensemble_eval.py` 与评价结果导出 `analysis/ensemble_report.py`，以及端到端冒烟脚本 `tools/run_signal_pipeline.py`；详见下文「信号研究与 ensemble 模块（Step 8.x）」章节。

截至本版文档，研究层与回测层**全部测试通过**；本地 `pytest -q` 实测 **799 passed**、零回归
（含 Step 8 信号研究系列与 B 组真实数据链路加固）。1 个符号链接文件测试在本机无权限时跳过，
Windows 上以目录联接（junction）逃逸测试补充真实 reparse point 验证，Linux 下符号链接测试会实际执行；
**1 个既有警告**（fastapi testclient 的 Starlette 弃用提示）。
尚未实现：实盘交易、多标的组合、异步任务与结果持久化、数据库迁移、生产部署形态。

> 费用与滑点为**模拟占位参数**（`fee_source=simulation_placeholder`），仅用于开发、测试与模拟，
> **不代表任何券商真实费率或最新市场标准**。

## 第一阶段范围

只做：项目初始化、Python 环境配置、历史行情 CSV 导入、行情数据校验、基础时间序列分析、
一个简单策略、基础历史回测、回测结果与自动化测试。

不做：连接真实交易账户、执行真实订单、购买服务器或付费行情、依赖付费 AI API 执行策略、
高频交易、一次性生成完整系统。

## 目录结构

```text
DeepQuant/
├── .gitignore
├── README.md
├── docs/                       # 设计记录、数据字典、回测约定
├── scripts/                    # 多终端同步脚本（git-sync.sh / git-sync.ps1）
├── backend/
│   ├── src/deepquant/
│   │   ├── data/               # 数据层：导入 / 校验 / 清洗 / 存储
│   │   │   ├── loaders/        #   CSV 等格式导入
│   │   │   ├── validation/     #   重复日期 / 缺失字段 / 异常价格检查
│   │   │   ├── storage/        #   本地文件读写（CSV / Parquet / JSON / 校验和）
│   │   │   └── pipeline.py     #   导入流水线：载入→校验→清洗→分离落盘
│   │   ├── analysis/           # 基础时间序列分析（收益率 / 滚动统计 / 波动率）
│   │   ├── strategies/         # 策略逻辑（只产出信号，不含撮合）
│   │   ├── backtest/           # 回测执行：撮合、持仓、现金、权益、成本
│   │   │   ├── config.py       #   回测配置（费率/滑点/T+1/执行价规则）
│   │   │   ├── costs.py        #   费用与滑点纯函数
│   │   │   ├── models.py       #   Order / Fill / Position / AccountState / BacktestResult
│   │   │   └── engine.py       #   run_backtest：逐日撮合与账务
│   │   ├── performance/        # 绩效与结果管理
│   │   │   ├── metrics.py      #   收益 / 最大回撤 / 交易统计（口径定义处）
│   │   │   ├── serialize.py    #   BacktestResult 的 JSON 往返序列化
│   │   │   ├── reproducibility.py # 数据集摘要与策略元数据
│   │   │   ├── comparison.py   #   结果元数据比较基础
│   │   │   └── json_encoding.py#   通用 JSON 安全编码
│   │   ├── market/             # 市场规则适配层（先 A 股日线，不写死）
│   │   ├── version.py          # 项目版本 / 结果 schema 版本（唯一来源）
│   │   └── api/                # FastAPI 路由（薄层）
│   └── tests/                  # pytest，与 src 目录镜像
├── frontend/                   # 回测结果可视化前端（Step 5.8，不入 Git：node_modules/dist）
│   ├── src/api/                #   API 客户端（错误分类/超时）与手写 TS 类型（与 OpenAPI 对齐）
│   ├── src/components/         #   数据集/策略/回测表单/绩效卡片/图表/明细表等界面组件
│   ├── src/utils/              #   表单校验与请求体构造、格式化、图表数据变换（纯函数）
│   ├── src/hooks/              #   后端健康状态
│   ├── src/test/               #   Vitest 设置；测试文件与被测组件同目录
│   └── vite.config.ts          #   开发代理 /api → http://127.0.0.1:8123 + Vitest 配置
└── data/                       # 本地行情数据（不入 Git）
    ├── raw/                    # 原始行情（只读，不修改）
    ├── processed/              # 处理后行情
    └── metadata/               # 数据来源、时间范围、处理方式记录
```

### 模块边界约定

- `strategies/` 只负责**生成信号**，不负责成交。
- `backtest/` 负责**撮合时点、手续费、滑点、持仓 / 现金 / 权益**，不写策略逻辑。
- `performance/` 只做**绩效计算**，不参与撮合。
- `market/` 是**可扩展适配层**，第一阶段的 A 股规则在此实现，但核心系统不写死为 A 股专用。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React 18 · TypeScript · Vite · Recharts · 原生 CSS（未使用 Tailwind） |
| 后端 | Python · FastAPI |
| 数据处理 | pandas · NumPy |
| 数据存储 | CSV · Parquet · SQLite |
| 测试 | pytest（后端）· Vitest + Testing Library（前端） |
| 依赖管理 | venv + requirements.txt |
| 版本管理 | Git · GitHub |

## 关键约定（摘要）

- 原始行情与处理后行情**分开保存**，并记录来源、时间范围、处理方式。
- 不把缺失行情随意填充为虚假价格；不使用未来数据生成历史决策。
- 回测默认**不假设所有订单按收盘价成交**，支持配置手续费与滑点。
- 保存策略版本、参数、数据集标识和回测设置，以便复现结果。
- 策略运行**不依赖大语言模型持续在线**。
- 交易密钥不入库、不写入前端；接入实盘前必须先做权限隔离与风险控制。

## 后端快速开始

所有命令在 **`backend/` 目录**下执行。要求 **Python ≥ 3.12**（开发环境实测 3.14.7）。

### Linux / macOS

```bash
# 1) 创建虚拟环境
python3 -m venv .venv

# 2) 安装依赖 + 以可编辑方式安装本项目（使 import deepquant 可用）
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --no-deps -e .

# 3) 运行测试（本地 pytest 实测 799 passed、零回归；本机无权限时跳过 1 个符号链接测试）
.venv/bin/python -m pytest -q

# 4) 启动服务（http://127.0.0.1:8000/health，文档 /docs）
.venv/bin/python -m uvicorn deepquant.api.app:app --reload
```

### Windows（PowerShell）

```powershell
# 在 backend 目录下执行（示例：cd D:\path\to\DeepQuant\backend）

# 1) 创建虚拟环境（要求已安装 Python ≥ 3.12；开发环境实测 3.14.7）
python -m venv .venv
# 若激活脚本被执行策略拦截，可临时放开：
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

# 2) 安装依赖 + 以可编辑方式安装本项目
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .

# 3) 运行测试（Windows 实测：799 passed, 1 skipped, 1 warning）
python -m pytest -q

# 4) 启动服务
python -m uvicorn deepquant.api.app:app --reload
```

> 依赖：`requirements.txt` 固定了**直接依赖**版本；项目**没有锁文件**（无 requirements.lock /
> poetry.lock / uv.lock），传递依赖未固定，因此**安装环境并非完全可复现**。如需强复现，
> 后续可引入带哈希的锁文件。

## 数据层用法示例

```python
from deepquant.data.pipeline import ingest_daily_csv

# 导入单个标的日线 CSV：原始数据原样存 data/raw/，清洗后存 data/processed/，
# 并写出 data/metadata/<symbol>.meta.json（来源、时间范围、清洗与校验记录）
result = ingest_daily_csv(
    "data/raw_src/600000.csv",
    symbol="600000",
    data_root="data",
    source_name="demo-vendor",   # 记录数据来源，便于核对数据许可
)
print(result.metadata["rows"], result.metadata["date_range"])
```

清洗规则（确定性、绝不伪造数据）：重复日期保留首次出现；数值缺失的行直接丢弃（不填充）；
按日期升序排列。硬错误（缺列、空表、日期无法解析、OHLC 矛盾、价格非正、负成交量）会
抛出 `DataValidationError` 并中止，不做自动修复。

> 存储格式：原始数据用 CSV（原样保存）；处理后数据用 Parquet（保留列类型、体积更小），
> 读取走 `deepquant.data.storage.io.read_daily_parquet`。依赖 pyarrow。

### CSV 编码策略（Windows 中文兼容）

```python
from deepquant.data.loaders.csv_loader import load_daily_csv

df = load_daily_csv("data/raw_src/600000.csv")                 # 自动尝试 utf-8-sig → gb18030
df = load_daily_csv("data/raw_src/600000.csv", encoding="gbk")  # 或显式指定单一编码
```

- **读取**：默认按 `utf-8-sig`（兼容带/不带 BOM 的 UTF-8）→ `gb18030`（Windows 中文
  GBK/GB2312 的超集）顺序尝试，以「整文件可成功解码」为准，避免把错误解码当成有效行情；
  两种都不成功时抛出 `DataLoadError`（**不会静默返回空数据**）。也支持显式 `encoding=` 参数。
- **写出**：`storage.io.write_daily_csv` 统一使用 **UTF-8**（不再依赖 Windows 本地默认 GBK）。
- 空文件、缺失文件、无法识别的编码均抛出 `DataLoadError`；仅表头文件返回 0 行规范表，
  由校验层判为 `empty`。

## 时间序列分析用法示例

```python
from deepquant.analysis.returns import simple_returns
from deepquant.analysis.rolling import rolling_mean, rolling_volatility
from deepquant.data.storage import io as storage_io

df = storage_io.read_daily_parquet("data/processed/600000.daily.parquet")
ret = simple_returns(df["close"])                                # 日简单收益率
ma20 = rolling_mean(df["close"], 20)                             # 20 日均线
vol20 = rolling_volatility(ret, 20, periods_per_year=252)        # 20 日年化波动率
```

约定：所有滚动计算只使用「当前及过去」的数据（**无未来函数**）；`min_periods` 默认等于
`window`，窗口未满返回 NaN；不做任何前向填充。

## 策略层用法示例

```python
from deepquant.strategies.ma_cross import MaCrossStrategy

strategy = MaCrossStrategy(fast_window=5, slow_window=20)
signals = strategy.generate_signals(df)   # 与 df 同索引：1=BUY / -1=SELL / 0=HOLD
print(strategy.describe())                # 记录名称/版本/参数，便于复现
```

信号契约：策略**只产出信号、不做撮合**；信号是**事件型**（仅在穿越当日给 BUY/SELL）；
信号在 t 日收盘后产生，因此回测最早应在 **t+1 日**成交（避免未来函数）。

## 信号研究与 ensemble 模块（Step 8.x）

在既有「策略层（只产出 `pd.Series` 信号）→ 回测引擎（撮合）」链路之外，项目新增了一套
**信号研究旁路层**，用于「信号可比 / 可组合 / 可评价 / 可持久化」的量化研究。该层是纯加法旁路：
**回测引擎默认不读取它**，两者无代码依赖；是否接入引擎属于后续决策（须先证明现有内嵌转换无法满足需求）。

### 红线与诚实性（该层所有模块共同遵守）

- **仅评价，不选择**：`signal_metrics` / `ensemble_eval` 使用未来收益评估信号预测能力，
  这对「评价」是必需的，但**绝不得用于参数选择**（R2 不用 OOS 数据选参、R11 不对候选参数
  按 OOS 收益排序择优）。这些模块**刻意不提供**任何 `best_*` / `select_*` / 网格搜索 /
  排序择优入口。
- **组合不承诺 alpha**：多个无 alpha 的策略组合起来依然无 alpha；合成信号命中率高于单源
  **不能**作为「应该组合」的证据。
- **不把合成行情标为真实**（R3）：测试与冒烟脚本使用确定性合成数据，返回值带 `synthetic`
  标志；任何人不应把合成数据当真实行情。
- 命中率不含费用、滑点、涨跌停、T+1 与整手约束，也不模拟撮合；命中率高 ≠ 策略盈利。

### 8.1 结构化信号层 `strategies/signals.py`

把既有 `pd.Series` 信号提升为可携带元信息的结构化事件：

```python
from deepquant.strategies.signals import SignalEvent, events_from_signals

# SignalEvent 字段：date, direction(Signal 枚举), strength(0..1), source,
#                   valid_until(有效期截止日), metadata(策略私有信息)
# 二值策略（MA / MR）没有强度概念，strength 一律填 1.0 —— 不虚构强度。
events = events_from_signals(
    signals_series,                # 与行情同索引，取值 ∈ {-1,0,1}
    source="ma_cross",
    valid_days=1,                  # 默认 1：T 收盘 → T+1 开盘，与引擎时序一致
)
```

`SignalEvent` 是不可变数据类，构造期校验方向 / 强度 / 源合法性；`events_from_signals` 默认
只产出非 HOLD 的可操作事件（`include_hold=True` 可调）。另含 `SignalExecutor` 接口与
默认实现 `NextOpenExecutor`（T 日信号 → T+1 开盘执行），**仅定义接口、未接入引擎**。

### 8.2' 信号级持久化 `persistence/signal_store.py`

把信号事件落库（SQLite），支持按多维度检索、无损往返：

```python
from deepquant.persistence.signal_store import (
    save_signal_events, list_signal_events, count_signal_events,
)

batch_id = save_signal_events(events, db_path="signals.db")  # 落库，返回批次号
rows = list_signal_events(                                    # 只读检索
    source="ma_cross",                                        # 精确匹配
    start=pd.Timestamp("2024-01-01"), end=pd.Timestamp("2024-06-30"),
    symbol="600000",                                          # 按 metadata["symbol"] 过滤（A 组新增）
    limit=100,
    db_path="signals.db",
)
n = count_signal_events(batch_id=batch_id, db_path="signals.db")
```

- `symbol` 参数为 A 组新增（纯加法）：按每条事件的 `metadata["symbol"]` 精确匹配，可与
  时间窗 / 源 / 批次组合过滤；不影响既有接口签名，也不迁移旧库 schema。
- 检索函数只读取、不修改；往返读取的事件内容与落库前一一对应（测试覆盖）。

### 8.3' 信号评价指标 `analysis/signal_metrics.py`

评估信号序列的方向性预测能力（**只读、纯函数、不参与参数选择**）：

```python
from deepquant.analysis.signal_metrics import evaluate_signals

report = evaluate_signals(series, close, source="ma_cross", horizon=5)
# report: density / buy_hit_rate / sell_hit_rate / overall_hit_rate
#         mean_return_after_buy / mean_return_after_sell
#         mean_gap_days / max_gap_days / excluded_tail / notes
```

辅助函数：`forward_returns(close, horizon)`（未来收益，仅评价用途）、`signal_density`、
`signal_gaps`。末尾 `horizon` 个交易日的信号无未来数据，从统计中剔除并在
`excluded_tail` 报告数量（不静默丢弃、不用 0 填补）。`report.to_dict()` 可程序化再读取。

### 8.4' 多信号组合 `strategies/ensemble_voting.py` + `strategies/ensemble.py`

投票内核（`ensemble_voting.py` 提供 `validate_trading_days` / `validate_inputs` /
`validate_weights` / `collect_votes` / `decide`）负责纯投票；`ensemble.py` 负责编排与
结果封装：

```python
from deepquant.strategies.ensemble import combine_series, combine_events, MAJORITY

result = combine_series(
    {"ma_cross": s1, "momentum": s2},       # 各序列索引必须完全一致
    method=MAJORITY,                         # MAJORITY | WEIGHTED | UNANIMOUS
    weights={"ma_cross": 0.6, "momentum": 0.4},   # 仅 WEIGHTED；由调用方给定
    min_sources=2,                           # 当日至少 N 个源给信号才合成
)
# result.signals 合成信号序列 / result.events 结构化事件 / result.votes 投票明细
# result.agreement 逐日一致性比例（= events[*].strength，共识度非胜率）
```

三道硬边界（红线）：不拟合权重（`weights` 由调用方给定，无搜索 / 优化入口）、不评价
（本模块不 import `signal_metrics`）、不改引擎时序（`valid_until` 沿用 1 日有效期）。
合成 `strength` 是**一致性比例**，不是标定胜率；组合不承诺 alpha。

### 8.5' ensemble 评价接入 `analysis/ensemble_eval.py`

组合层不评价、评价层不组合，本模块是两者**唯一合法的接线点**：一次并列评价「合成信号 +
各源信号」：

```python
from deepquant.analysis.ensemble_eval import evaluate_ensemble

report = evaluate_ensemble(
    {"ma_cross": s1, "momentum": s2}, close,
    method=MAJORITY, horizon=5, evaluate_sources=True,
)
# report.ensemble 合成信号评估 / report.per_source 各源评估 / report.notes 红线声明
```

**红线（务必遵守）**：本函数返回的是**评价结果，不是选参依据**。请勿据此挑选
`weights` / `method` / `min_sources`（违反 R2 / R11）。本模块刻意不提供任何择优 / 搜索 /
优化入口；各源与合成信号并列展示仅为描述性对比，不构成排序择优。

#### 评价结果导出 `analysis/ensemble_report.py`（A 组新增，纯加法）

把 `EnsembleEvalReport` 序列化落盘，便于存档与人工查看：

```python
from deepquant.analysis.ensemble_report import export_ensemble_report

export_ensemble_report(report, "eval.json", fmt="json")   # 完整嵌套结构
export_ensemble_report(report, "eval.csv",  fmt="csv")    # 一行一个源 + ensemble 行
```

本模块**只序列化、不评价、不重新计算、不聚合、不排序择优**（R2 / R11）；原样写出
`EnsembleEvalReport.notes`（已声明「使用未来收益仅用于评价、不得用于参数选择」），
不删除、不改写（R3）。

### 端到端冒烟脚本 `tools/run_signal_pipeline.py`（A 组新增，纯加法）

把整条研究链路串起来跑一遍，验证「生成 → 组合 → 评价 → 持久化 → 读回校验」五环节在当前
代码库下能端到端打通。**全程确定性合成数据，返回值带 `synthetic: True`，不代表真实行情（R3）**：

```bash
python tools/run_signal_pipeline.py                 # 临时库，跑完即清
python tools/run_signal_pipeline.py --db smoke.db   # 落指定库
python tools/run_signal_pipeline.py --method weighted --n-days 120
```

编程入口 `run_signal_pipeline(n_days=60, seed=42, method="majority")` 返回含 `readback_ok`
的冒烟摘要；`evaluate_ensemble` 仅评价、不挑选任何参数（R2 / R11）。

## 回测引擎用法示例

```python
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.strategies.ma_cross import MaCrossStrategy

cfg = BacktestConfig(symbol="600000", initial_cash=100000.0)      # 默认模拟占位费率
result = run_backtest(df, MaCrossStrategy(fast_window=5, slow_window=20), cfg)

result.account_states[-1].total_equity   # 期末权益
result.fills                             # 成交明细（含费用）
result.performance                       # 绩效指标（run_backtest 已自动填充）
```

- 引擎**不修改**调用方传入的 DataFrame；日期必须升序、无重复、无时区。
- T+1：信号在 t 日收盘后产生，最早 t+1 日成交；买入当日不可卖。
- 手数、T+1、涨跌停由 `market_rules` 提供；涨跌停默认**未配置**（不做判定，不冒充已实现）。

## 数据质量边界

校验层 `validate_daily` 只做**检测**（不修改数据），分级如下：

| 级别 | 情形 |
|---|---|
| ERROR（拒绝） | 缺少必需列、空表、日期无法解析、价格非正/非有限、OHLC 关系矛盾、成交量为负 |
| WARNING（可清洗） | 日期未升序、重复日期、行情数值缺失 |
| 允许保留 | 停牌/缺失的开盘价或收盘价：**保留并标记**，由引擎按既有规则处理（记 `no_price`，不填 0） |

清洗规则（`pipeline._clean`，确定性）：丢重复日期（保首次）、丢数值缺失行（不填充）、按日期升序。

## 绩效指标口径

- 每日收益率 = `equity_t / prev − 1`，首日前一期取**初始权益**（不硬设 0）；`prev ≤ 0` 或权益缺失记为 `None`。
- 累计收益率 = `final/initial − 1`；总盈亏 = `final − initial`（到分）。
- 最大回撤：历史高水位下 `peak − equity`；比例 = `金额/peak`（peak>0）。
- 年化：`periods_per_year` 默认 252；年化收益 = `(final/initial)^(1/years) − 1`；年化波动率 = `stdev(日收益)×√252`。
- 交易统计以**完整买入—卖出周期**计一笔已完成交易；未平仓不计入胜率。

完整的绩效口径、`BacktestResult` 字段、序列化格式与可复现性说明见
[docs/backtest_reference.md](docs/backtest_reference.md)。

## 结果序列化与可复现性

```python
from deepquant.performance.serialize import result_to_json, result_from_json

text = result_to_json(result)        # 标准 JSON（无 NaN/Infinity），schema_version = "0.1.0"
restored = result_from_json(text)    # 恢复核心数据结构与类型
```

结果同时记录：策略名称/版本/参数、数据集摘要（sha256）、费率来源、市场规则、起止日期、
引擎版本。三层可复现性（结果可序列化 / 输入可识别 / 过程可复现）需分别判定，
缺关键项时明确标记为不可完全复现。

## API 接口（Step 5.6 首版）

同步 HTTP API，复用既有回测引擎与序列化契约；**不含前端、实盘、多标的组合与异步任务**。
交互式文档：启动服务后访问 `http://127.0.0.1:8000/docs`（OpenAPI：`/openapi.json`）。

### 数据根目录配置

- 环境变量 **`DEEPQUANT_DATA_ROOT`** 指定数据根目录；未设置时默认使用仓库内 `backend/data/`
  （以模块文件位置锚定，不依赖启动目录）。
- 目录结构由导入管线生成：`raw/`（原始 CSV）、`processed/`（Parquet）、`metadata/`（`<symbol>.meta.json`）。
- API **不接受客户端提供的任何文件路径**；数据集只能通过 symbol 标识符引用
  （白名单字符：字母/数字开头，仅含字母/数字/`.`/`_`/`-`），所有文件访问都校验在数据根目录之内。
- 数据根目录不存在时，数据集与回测接口返回 `503 data_root_unavailable`。

### 路由表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查，返回 `{"status": "ok"}` |
| GET | `/datasets` | 可用数据集列表（symbol / 行数 / 起止日期 / 原始 CSV 的 SHA-256）；损坏元数据列入 `invalid_datasets` |
| GET | `/strategies` | 策略注册表（当前仅 `ma_cross`）：参数类型、默认值与约束说明 |
| POST | `/backtests` | 同步执行回测，返回结果 JSON |

### POST /backtests 请求字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `dataset_symbol` | string | 是 | 已登记的数据集标识 |
| `strategy_id` | string | 是 | 策略注册表标识（当前仅 `ma_cross`） |
| `strategy_params` | object | 否 | 策略参数：`fast_window`(int ≥1, 默认 5)、`slow_window`(int >fast_window, 默认 20) |
| `initial_cash` | number | 是 | 初始资金（>0） |
| `start_date` / `end_date` | string(YYYY-MM-DD) | 否 | 回测区间（闭区间），缺省为数据全区间 |
| `signal_lag` | int | 否 | 信号延迟，≥1，默认 1 |
| `commission_rate` / `min_commission` / `stamp_duty_rate` / `transfer_fee_rate` / `slippage_buy` / `slippage_sell` | number | 否 | 费用与滑点（≥0）；**默认值为模拟占位参数** |

未知字段一律拒绝（422）。客户端**不能**指定执行价规则、市场规则、模块路径或任意代码；
`exec_price_rule` 固定为 `next_open`，市场规则为 A 股规则，`fee_source` 固定为 `simulation_placeholder`。

### 响应结构

```json
{
  "api": {"name": "deepquant-api", "version": "0.0.1"},
  "result": { "schema_version": "0.1.0", "engine_version": "0.0.1", "config": {}, "period": {},
              "strategy": {}, "dataset": {}, "orders": [], "fills": [], "account_states": [],
              "performance": {}, "reproducibility": {} }
}
```

`result` 为 `result_to_dict` 的原始输出（既有 JSON 契约不变，标准 JSON、无 NaN/Infinity）；
`api` 为外层元信息。错误响应统一为 `{"error": {"code", "message", "details?"}}`：

| 状态码 | code | 场景 |
|---|---|---|
| 400 | `data_validation_failed` / `backtest_rejected` / `data_load_failed` | 数据质量 ERROR；区间内无行情；数据无法按约定载入（详见服务端日志） |
| 404 | `dataset_not_found` / `dataset_unavailable` / `strategy_not_found` | 数据集未登记 / 已登记但文件缺失或元数据无效 / 策略未注册 |
| 422 | `invalid_request` / `invalid_strategy_params` / `invalid_config` | 字段或参数不合法 |
| 503 | `data_root_unavailable` | 数据根目录不存在或不可访问 |
| 500 | `serialization_failed` / `internal_error` | 结果序列化失败 / 未预期错误（详情见服务端日志） |

错误响应不包含服务器绝对路径或堆栈。

### 调用示例（PowerShell）

```powershell
# 准备数据集（一次性）：把 CSV 导入数据根目录
python -c "from deepquant.data.pipeline import ingest_daily_csv; ingest_daily_csv(r'D:\quotes\600000.csv', symbol='600000', data_root=r'D:\DeepQuant\backend\data', source_name='my-vendor')"

# 启动服务
python -m uvicorn deepquant.api.app:app --host 127.0.0.1 --port 8000

# 查询数据集与策略
Invoke-RestMethod http://127.0.0.1:8000/datasets
Invoke-RestMethod http://127.0.0.1:8000/strategies

# 执行回测
$body = @{
  dataset_symbol = "600000"
  strategy_id    = "ma_cross"
  strategy_params = @{ fast_window = 5; slow_window = 20 }
  initial_cash   = 100000
  start_date     = "2020-01-01"
  end_date       = "2024-12-31"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/backtests -ContentType "application/json" -Body $body
```

> 费用与滑点仍为**模拟占位参数**（响应 `config.fee_source=simulation_placeholder`），
> 仅用于开发、测试与模拟，**不代表任何券商真实费率或最新市场标准**。
> 首版为同步执行：2500 个交易日单标的回测约 0.22s（Step 5.7 Windows 本机实测），日线量级下无额外请求规模限制；
> 异步任务与结果持久化属于后续阶段。

## 前端（Step 5.8）

回测结果可视化前端：**本地个人研究工具**，只对接上述本地 API；不含实盘交易、真实资金与公网部署。

### 技术栈与目录

- React 18 · TypeScript ~5.6 · Vite 6 · Recharts 2 · Vitest 3 + Testing Library（jsdom）；样式为手写 CSS（未使用 Tailwind）。
- 代码位于 `frontend/`：`src/api/`（API 客户端与类型）、`src/components/`（界面组件）、
  `src/utils/`（表单校验与请求体构造、格式化、图表数据，纯函数）、`src/hooks/`（健康状态）；
  测试文件与被测组件同目录，Vitest 配置内嵌于 `vite.config.ts`。

### 环境要求与安装

- Node.js ≥ 18（Vite 6 官方支持 `^18 || ^20 || >=22`；开发环境实测 Node v24.21.0）与 npm。
- 在 `frontend/` 目录执行 `npm install`（依赖版本由 `package-lock.json` 锁定；后端目前仍无锁文件）。
- `npm install` 与 `npm ci` 的使用场景区分：日常开发（可能新增/更新依赖、允许改写 `package-lock.json`）用 `npm install`；
  全新克隆、CI 或需要严格按锁文件复现安装时用 `npm ci`（要求 `package-lock.json` 与 `package.json` 一致，
  安装前自动清空 `node_modules`，且不会修改锁文件）。

### 启动与前后端联调

1. 先启动后端（依赖安装见上文「后端快速开始」）。开发代理默认指向 `127.0.0.1:8123`：
   ```powershell
   python -m uvicorn deepquant.api.app:app --host 127.0.0.1 --port 8123
   ```
2. 再启动前端（`frontend/` 目录）：`npm run dev`，浏览器访问 `http://localhost:5173`。
3. 联调方式：Vite 开发服务器把 `/api/*` 转发到后端（转发时去掉 `/api` 前缀），
   **无需后端配置 CORS**。后端使用其他端口时，改 `frontend/vite.config.ts` 的
   `BACKEND_DEV_TARGET`；或在 `frontend/.env.local` 设置 `VITE_API_BASE_URL=http://127.0.0.1:<端口>`
   让前端直连后端（此时跨域由后端控制）。
4. 注意：代理只存在于 `npm run dev` 开发服务器；`npm run build` 产出的 `dist/` 静态文件**没有代理**，
   直连形态需 `VITE_API_BASE_URL` 在构建前指定（生产部署不属于本阶段范围）。

### 常用命令（均在 `frontend/` 目录执行）

| 命令 | 说明 |
|---|---|
| `npm run dev` | 启动开发服务器（默认 5173，含 `/api` 代理） |
| `npm test` | 运行全部前端测试（Vitest 单次执行；当前 11 个文件 82 条） |
| `npm run test:watch` | 测试监听模式 |
| `npm run typecheck` | TypeScript 检查（`tsc --noEmit`） |
| `npm run build` | 类型检查 + 生产构建（输出 `dist/`，不入 Git） |

### 当前支持的功能

- 数据集与策略列表**真实来自后端**（`GET /datasets` / `GET /strategies`），含失效数据集提示与手动刷新；
- 参数表单由后端返回的策略 schema 驱动（类型/最小值/默认值占位）；参数**留空 = 使用后端默认值**；
- 客户端即时校验（必填、整数/数字、日期格式与先后、快线窗口 < 慢线窗口）；后端校验仍是最终依据；
- 同步运行回测并展示：回测概要、绩效指标卡片、账户资产曲线（后端 `equity_curve` 的**账户总资产**，
  不是标准化净值或累计收益率曲线）、日收益率柱状图、最大回撤指标卡（后端仅提供汇总指标，无逐日回撤序列）、
  订单/成交/账户状态明细、数据质量横幅与可复现性信息（数据摘要 digest、原始结果 JSON 折叠查看）；
- 后端不可用 / 超时 / HTTP 错误 / 响应异常分类提示（含启动指引与重试）；请求失败时表单配置保留，可修改后重跑；
- 运行期间禁用提交按钮、不自动重复提交；页面刷新后自动重新加载数据集与策略。

### 当前未支持的功能

- 实盘交易、券商账户连接、自动下单、真实资金管理；
- 异步任务与进度条（后端为同步接口，页面如实显示「执行中 / 成功 / 失败」，不伪造任务 ID 或进度）；
- 回测结果持久化与历史结果列表（结果仅保存在当前页面内存中）；
- 多标的组合回测、策略编辑器、自定义策略代码执行、费率/滑点的前端配置项。

### 回测费用与滑点限制

- 前端**不提供**费率/滑点设置项；回测一律使用后端模拟占位默认值（`fee_source=simulation_placeholder`）。
- 页面在提交摘要与结果页明确声明：该费率为**模拟占位参数，不代表任何真实券商费率**；
  回测结果不构成对未来收益的任何保证。

### 已知问题

- `npm run build` 输出 Recharts 大 chunk 警告（本地研究工具可接受，未做代码分割优化）；
- 无「回撤曲线」：后端只返回最大回撤汇总指标，页面仅展示指标并注明原因；
- 响应式单栏布局（CSS 媒体查询 ≤960px）已实现，但未在真实移动设备或模拟视口下系统验收；
- 生产部署形态（静态构建 + 直连后端 + CORS）未在本阶段实施与验证。

### 启动失败与跨域排查

- 页面顶栏显示「后端不可用」：确认后端已启动且端口与代理目标一致
  （`curl http://127.0.0.1:8123/health` 应返回 `{"status":"ok"}`）；
- 端口被占用：后端启动命令加 `--port <其他端口>` 并同步修改 `BACKEND_DEV_TARGET`（或改用
  `VITE_API_BASE_URL` 直连）；前端端口冲突时修改 `vite.config.ts` 的 `server.port`；
- 直连模式（`VITE_API_BASE_URL` 指向后端）出现浏览器 CORS 报错：本阶段后端未开放跨域，
  **开发联调请使用默认代理模式**（`VITE_API_BASE_URL` 留空）；
- Windows PowerShell 提示「禁止运行脚本」导致 npm 无法执行：改用 `npm.cmd`，或先执行
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`。

## 多终端同步（GitHub）

远端仓库：`https://github.com/simontang6868/DeepQuant`（默认分支 `main`）。

**每台机器首次**（含 Windows 电脑）：

```bash
git config --global user.name  "simontang6868"
git config --global user.email "simontang6868@users.noreply.github.com"
gh auth login                 # 或用 Git Credential Manager 登录一次；gh auth status 可查看状态
git clone https://github.com/simontang6868/DeepQuant.git
```

**之后每次同步**（在仓库根目录执行）：

```bash
# Linux / macOS / WSL
scripts/git-sync.sh "本次改动说明"
```

```powershell
# Windows PowerShell
.\scripts\git-sync.ps1 "本次改动说明"
```

脚本行为：`git add -A` → 有改动则 `commit` → `git pull --rebase --autostash` → `git push`。
**绝不 force push**；如 rebase 出现冲突会停止，需人工解决后再次运行。省略说明参数时使用时间戳。

> 同步前请确认已登录（`gh auth status`）。`.venv/`、`data/`、`__pycache__/`、`*.parquet` 等
> 已被 `.gitignore` 排除，不会上传。多终端协作请遵循「先 `pull` 再改」，避免同一文件在两地同时改。

## 下一步

- **Step 5.5 / 5.6 / 5.7 已完成**：跨平台验收、回测 API 首版、安全审查与版本基线固化（Linux 回归验证待具备环境后执行）。
- **Step 5.8 已完成**：回测结果可视化前端开发与端到端验收（见上文「前端」章节），代码尚未提交 Git。
- 后续开发：结果比较接口、异步任务与结果持久化、更多策略与数据源、移动端验收与生产部署形态。

