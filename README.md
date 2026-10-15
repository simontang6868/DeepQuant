# DeepQuant

面向个人投资者的量化研究、策略回测与自动化交易平台（个人独立项目）。

> 名称说明：DeepQuant 的命名表达了对 DeepSeek 的敬意，但本项目为**独立个人项目**，
> 与 DeepSeek 无官方合作或隶属关系。

## 当前状态

**第一阶段 · Step 0 / Step 1 / Step 2 / Step 3 已完成**：
- Step 0：仓库骨架；
- Step 1：后端最小可跑（venv + 最小依赖 + `/health` 接口 + pytest）；
- Step 2：数据层（CSV 导入、行情校验、确定性清洗、原始/处理分离落盘 + 元数据）；
- Step 3：时间序列分析（简单/对数收益率、滚动均值/标准差、年化波动率）+ Parquet 存储。

当前 30 个 pytest 全部通过。尚未实现：策略、回测、绩效计算、前端。

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
│   │   ├── performance/        # 绩效指标：累计收益 / 最大回撤等
│   │   ├── market/             # 市场规则适配层（先 A 股日线，不写死）
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

在 `backend/` 目录下操作。建议 Python 3.12 / 3.13。

```bash
# 1) 创建虚拟环境
python -m venv .venv

# 2) 安装依赖（含可编辑安装本包，使 import deepquant 可用）
#    Windows:  .venv\Scripts\python -m pip install -r requirements.txt
#              .venv\Scripts\python -m pip install --no-deps -e .
#    Linux/mac: .venv/bin/pip install -r requirements.txt
#               .venv/bin/pip install --no-deps -e .

# 3) 运行测试
#    Windows:  .venv\Scripts\python -m pytest -v
#    Linux/mac: .venv/bin/python -m pytest -v

# 4) 启动服务（http://127.0.0.1:8000/health，文档 /docs）
#    Windows:  .venv\Scripts\python -m uvicorn deepquant.api.app:app --reload
#    Linux/mac: .venv/bin/python -m uvicorn deepquant.api.app:app --reload
```

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

## 下一步

Step 4：一个简单策略 —— 只产出信号（含明确的信号产生时间），并配套自动化测试。
