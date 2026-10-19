# 美股股债利差研究（US Equity-Bond Spread, 1871–2026）

> 本模块是**研究性质**的，不接入实盘、不生成交易指令、不连接券商。

## 1. 研究问题（可证伪）

把 10 年期国债收益率从盈利收益率里减掉，是否比单看估值更能识别长期买点？

- **S** = `100 / CAPE − Y10`（百分点）
- **EY** = `100 / CAPE`（对照组：只用估值）
- **Y10** = 美国 10 年期国债收益率（对照组：只用利率）

目标变量为未来 12/36/60/120 个月的**真实总收益**（含股息、扣 CPI）。

三条预登记的主检验：

1. **预测力**：S 与未来真实收益的 Spearman IC，配 Newey-West t 与块自举 p；
2. **增量信息**：`R = a + b·z(EY) + c·z(Y10)`，c 是否显著异于 0；
3. **阈值事件**：S 的历史百分位 ≥ 90% 的事件，之后收益是否优于无条件分布。

> 完整结论见仓库根 `US_EQUITY_BOND_SPREAD_REPORT.md`。

## 2. 目录结构

```
backend/src/deepquant/research/equity_bond_spread_us/
├── config.py       参数集中管理与校验（USConfig）
├── data.py         面板装载、派生收益序列、结构校验、SHA-256
├── indicators.py   盈利收益率、利差、扩展/固定窗口历史百分位
├── signals.py      阈值信号、事件合并、分位分组
├── stats.py        前视收益、Newey-West OLS、块自举、功效、样本外 R²
├── experiment.py   IC / 增量回归 / 分位 / 事件 / 子区间 / 相对收益附录
├── strategy.py     规则化策略 + 匹配暴露随机基准
└── cases.py        10 个预登记历史时点核查
backend/tests/research/test_us_*.py
scripts/
├── prepare_us_ebs_data.py      抓取 → 面板 + manifest + 双源校验
├── run_us_ebs_study.py         主研究入口
├── us_sensitivity.py           阈值口径敏感性（窗口 × 阈值）
└── render_us_ebs_dashboard.py  零依赖 SVG 仪表盘
data/equity_bond_spread_us/
├── raw/                        原始 HTML / XLS / CSV（均记 SHA-256）
├── panel_us.csv                月度面板（1871-02 ~ 2026-10）
├── manifest_us.json            来源、字段定义、拼接说明、哈希
├── validation_us.json          双源校验与缺口检查
├── results/                    全部结果（JSON / CSV / 表格片段）
└── dashboard/index.html        仪表盘
```

## 3. 数据口径

| 字段 | 来源 | 说明 |
|---|---|---|
| price / cape / div_yield / cpi / earnings | multpl.com 月度表 | 已与 Shiller 原始 xls 逐月比对 |
| y10 | 1990-12 前：Shiller GS10；之后：akshare 日频月均 | 重叠 394 月，平均绝对差 0.0044pp |

总收益近似：`1 + r_t = (P_t + D_{t-1}/12) / P_{t-1}`，股息取**上月末**滚动 12 个月股息额。

**不做插值、不做回填**；未走完的当月用 `partial_month` 标记并在分析中剔除。

## 4. 使用

```bash
python scripts/prepare_us_ebs_data.py          # 抓数据（--offline 用本地原始文件重建）
PYTHONPATH=backend/src python scripts/run_us_ebs_study.py
PYTHONPATH=backend/src python scripts/us_sensitivity.py
PYTHONPATH=backend/src python scripts/render_us_ebs_dashboard.py
cd backend && PYTHONPATH=src python -m pytest tests/research -q
```

## 5. 不可协商的纪律

- `exec_lag >= 1`：信号月判定、下月成交，禁止同月成交（配置层直接报错）；
- 百分位只用截至当前的历史（有单元测试锁死「截断未来数据后历史百分位不变」）；
- 事件必须合并（间隔 ≤ 12 个月视为同一事件），避免同一波机会重复计数；
- 重叠窗口必须用 Newey-West / 块自举，禁止用朴素 t 检验下结论；
- 结论以三种显著性口径中最保守者为准，并报告总检验次数与 Bonferroni 阈值。

## 6. 已知局限（详见报告第 11 节）

- CAPE 存在回溯修订风险（拿不到「当时发布值」）；
- 月度均价而非月末收盘，执行价是「次月均价」；
- 债券收益近似忽略久期损益，仅用于附录的机械成分分析；
- 多重比较：140 项检验，Bonferroni 阈值 0.00036，无一通过。
