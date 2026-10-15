# DeepQuant

面向个人投资者的量化研究、策略回测与自动化交易平台（个人独立项目）。

> 名称说明：DeepQuant 的命名表达了对 DeepSeek 的敬意，但本项目为**独立个人项目**，
> 与 DeepSeek 无官方合作或隶属关系。

## 当前状态

**第一阶段 · Step 0 – Step 5.2.6 已完成**（Step 5.3 为工程化收尾，见下）：

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
- Step 5.3：数据层加固（CSV UTF-8/GB18030 编码兼容）、端到端数据契约测试与工程文档。

当前 **308 个 pytest 全部通过，1 个既有警告**（fastapi testclient 的 Starlette 弃用提示）。
尚未实现：前端界面、实盘交易、多标的组合、数据库迁移。

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
| 前端（本阶段暂不启用） | React · TypeScript · Vite · Tailwind CSS · Recharts |
| 后端 | Python · FastAPI |
| 数据处理 | pandas · NumPy |
| 数据存储 | CSV · Parquet · SQLite |
| 测试 | pytest（+ 前端测试工具，后续） |
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

# 3) 运行测试（预期：308 passed, 1 warning）
.venv/bin/python -m pytest -q

# 4) 启动服务（http://127.0.0.1:8000/health，文档 /docs）
.venv/bin/python -m uvicorn deepquant.api.app:app --reload
```

### Windows（PowerShell）

```powershell
# 在 backend 目录下执行（示例：cd D:\path\to\DeepQuant\backend）

# 1) 创建虚拟环境
py -3.12 -m venv .venv
# 若激活脚本被执行策略拦截，可临时放开：
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

# 2) 安装依赖 + 以可编辑方式安装本项目
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .

# 3) 运行测试（预期：308 passed, 1 warning）
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

## 下一步

- **Step 5.3 已完成**：CSV 编码兼容、数据契约端到端测试、工程文档。
- 待办：Windows 本地验收（按上方 PowerShell 步骤在用户 Windows 机器执行并回传结果）；
  按人工审核后的清单建立 Git 版本基线（本仓库当前**尚未**提交 Step 5.2.x/5.3 的新增文件）。
- 后续开发（Step 5.4+）：前端绩效展示、结果比较、更多策略与数据源。
