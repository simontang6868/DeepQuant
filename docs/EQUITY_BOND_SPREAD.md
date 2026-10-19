# 股债利差与长期市场机会识别研究（Equity-Bond Spread Research）

> 本模块是**研究性质**的，不接入实盘、不生成交易指令、不连接券商。

## 1. 研究问题（可证伪）

- **假设 A**：`S_A(t,H) = R_stock(t,H) − R_bond(t)`
  - `R_stock(t,H)`：截至 t 的过去 H 年沪深300 年化收益率
  - `R_bond(t)`：t 时点 10 年期中国国债收益率
- **假设 B**：`S_B(t) = EY(t) − R_bond(t)`
  - `EY(t) = 1 / PE(t)`，为盈利收益率

当利差达到**截至当时**的历史高分位（默认 90%）时，标记为「潜在长期机会」候选，
然后检验其后续 20/60/120/250 个交易日的收益是否优于**随机时点买入**的无条件分布。

## 2. 目录结构

```
backend/src/deepquant/research/equity_bond_spread/
├── config.py       参数集中管理与校验（含敏感性网格）
├── data.py         装载 / 清洗 / 对齐 / 覆盖率诊断 / SHA-256
├── indicators.py   利差、盈利收益率、滚动年化收益、历史百分位
├── signals.py      阈值信号、连续信号合并、执行时点
├── experiment.py   前瞻收益、基准比较、样本内外、敏感性
├── cases.py        重点历史阶段检查
└── report.py       中文研究报告生成
backend/tests/research/                 78 条测试
scripts/
├── prepare_equity_bond_spread_data.py  原始数据 → 清洗后数据 + manifest
├── run_equity_bond_spread.py           运行入口
└── render_equity_bond_spread_dashboard.py  自包含 HTML 仪表盘
data/equity_bond_spread/
├── raw/            原始抓取文件（只读）
├── csi300_monthly.csv / cn10y.csv      清洗后数据
├── manifest.json   来源、字段定义、清洗规则、SHA-256
└── results/        实验结果与报告
```

## 3. 复现步骤

```bash
cd <repo root>
export PYTHONPATH=backend/src          # Windows: set PYTHONPATH=backend/src

# 1) 整理数据（raw → cleaned，生成 manifest）
python scripts/prepare_equity_bond_spread_data.py \
    --equity-raw <path>/csi300_monthly_raw.csv \
    --bond-raw   <path>/cn10y_raw.csv

# 2) 跑研究（主实验 + 敏感性网格 + 报告）
python scripts/run_equity_bond_spread.py

# 3) 生成仪表盘
python scripts/render_equity_bond_spread_dashboard.py

# 4) 跑测试
cd backend && PYTHONPATH=src python -m pytest tests/research -q
```

## 4. 数据来源

| 数据 | 来源 | 口径 |
|---|---|---|
| 沪深300 收盘价 / PE / PB / 股息率 | 腾讯自选股行情接口（westock-data `quote sh000300 --date`） | 价格指数口径；PE 口径未经指数公司官方确认 |
| 中国 10 年期国债收益率 | 中国债券信息网 中债国债收益率曲线 | 到期收益率，百分数 |

每次运行都会把数据文件 SHA-256、配置、运行时间写进
`data/equity_bond_spread/results/summary.json`，保证结果可追溯。

## 5. 已知限制（务必阅读）

1. **观测频率为月频**。行情接口的历史逐点查询有速率限制，无法在合理时间内
   取到全历史日频序列；前瞻窗口 20/60/120/250 交易日按 252 交易日/年折算为
   1/3/6/12 个月，属**近似交易日**口径。
2. **股票序列为价格指数口径**，不是分红再投资的全收益指数。
   配置里的 `ReturnBasis.TOTAL_APPROX`（价格收益 + 窗口内平均股息率）是**近似值**，
   不等于真正的全收益指数收益。
3. **PE 口径未经官方确认**，缺失或非法 PE 一律置空，不参与信号。
4. 国债收益率曲线起始于 **2006-03-01**，此前区间的股债利差为缺失。
5. 样本外事件数很少，统计上不足以支持强结论。

## 6. 研究纪律（红线）

- R1 不使用未来数据：历史百分位只取 t 及之前；执行时点 = 信号后第 1 个观测，
  禁止用信号当日收盘价成交；前瞻收益只用执行时点之后的数据。
- R2 不根据样本外结果挑选参数：敏感性网格在跑之前定义好。
- R3 不把合成数据标为真实；数据缺口如实披露。
- R5 不删改失败案例：所有满足规则的信号事件全部出现在报告里。
- R6 不用「没有证据证明无效」冒充「已经证明有效」；结论只有三档：
  **有证据支持（弱）/ 暂时未发现支持证据 / 数据不足，无法判断**。

⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
