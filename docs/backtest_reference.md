# 回测与绩效参考（开发者文档）

本文件说明 `backtest` 与 `performance` 两个包对外接口的口径，供后续开发对照实现。
所有内容以实际代码为准（`src/deepquant/backtest/*`、`src/deepquant/performance/*`）。

## 1. 调用链

```
行情 DataFrame ──► normalize_daily_data ──► 策略 generate_signals ──► validate_signals
        │                                                                     │
        └─ dataset_summary（内容摘要）                          run_backtest 逐日撮合
                                                                              │
                          BacktestResult（orders / fills / account_states / 元数据）
                                                                              │
                                              compute_performance（自动填充）─┘
```

`run_backtest(data, strategy, config)` 在返回前会调用 `compute_performance(result)`
自动填充 `result.performance`。该计算**只读**：不重跑策略、不重新撮合、不改变账户状态。

## 2. BacktestResult 主要字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `config` | `BacktestConfig` | 回测配置（标的、初始资金、费率、滑点、市场规则、区间） |
| `period_start` / `period_end` | `Timestamp \| None` | 实际回测区间（无时区） |
| `orders` | `list[Order]` | 全部订单（含被拒订单） |
| `fills` | `list[Fill]` | 实际成交（含费用明细） |
| `account_states` | `list[AccountState]` | 每日现金 / 市值 / 总权益 / 估值状态 |
| `data_quality_issues` | `list[str]` | 行情数据质量问题（如开盘价缺失） |
| `limitations` | `list[str]` | 模型限制说明（涨跌停、费用来源等） |
| `is_complete` | `bool` | 权益序列是否可完整计算（默认 `False`，由 `assess_completeness` 评估） |
| `incomplete_reasons` | `list[str]` | 不完整的原因 |
| `schema_version` | `str` | 结果 JSON schema 版本（当前 `"0.1.0"`） |
| `engine_version` | `str` | 引擎/项目版本（来自 `version.py`） |
| `strategy` | `dict \| None` | 策略元数据（name / version / params） |
| `dataset` | `dict \| None` | 数据集元数据（dataset_id / n_rows / digest / date_range 等） |
| `performance` | `PerformanceMetrics \| None` | 绩效指标（run_backtest 自动填充） |

## 3. 绩效指标口径

`compute_performance(result, *, periods_per_year=252)`：

| 指标 | 口径 |
|---|---|
| 初始权益 | `config.initial_cash` |
| 最终权益 | 末个 `AccountState.total_equity`（缺失则 `None`） |
| 总盈亏金额 | `round_money(final − initial)` |
| 累计收益率 | `final / initial − 1` |
| 每日收益率 | `equity_t / prev − 1`；首日 `prev = 初始权益`（非硬设 0） |
| 最大回撤金额 | 历史高水位下 `max(peak − equity)` |
| 最大回撤比例 | `回撤金额 / peak`（`peak > 0` 时） |
| 年化收益率 (CAGR) | `(final / initial) ** (1 / years) − 1`，`years = 交易日数 / periods_per_year` |
| 年化波动率 | `stdev(可计算日收益率, ddof=1) × sqrt(periods_per_year)` |

**缺失权益处理**：`total_equity is None` 的日期 → 收益率记 `None`（不当 0、不删日），
并在 `incomplete_reasons` 记录原因；该缺失日的**次日**因基准未知同样记 `None`。
依赖完整序列的指标通过 `is_complete` / `unavailable_metrics` 明确标记可用性。

## 4. 交易统计（trade_stats）

以**完整买入—卖出周期**为一笔已完成交易（本项目为单标的、整仓买卖）：

- 买入成本 = 成交金额 + 买入费用；卖出收入 = 成交金额 − 卖出费用；盈亏 = 收入 − 成本。
- 卖出按数量比例结转买入成本；费用已由引擎从现金扣除（已反映在权益中）。
- 未平仓持仓记为 `open_position_quantity`，**不计入已完成交易、不参与胜率**。
- 胜率 = 盈利交易数 / 已完成交易数；恰为 0 记「持平」，不计入盈利或亏损；无已完成交易 → `win_rate = None`。

> 注意：**订单数量 ≠ 成交笔数**；单笔卖出不能脱离对应买入成本单独计为盈利。

## 5. JSON 序列化格式

`result_to_json(result)` → 标准 JSON；`result_from_json(text)` → `BacktestResult`（恢复核心类型）。

- 日期/时间戳：无时区 ISO-8601（如 `"2024-01-03T00:00:00"`）。
- 枚举：稳定字符串值（`Side.BUY → "BUY"`，`OrderStatus.FILLED → "filled"`）。
- `Decimal`：标签化 `{"__type__": "decimal", "value": "..."}`（避免金额精度丢失）。
- 缺失值：`null`；**禁止**输出 `NaN` / `Infinity` / `-Infinity`（`json.dumps(allow_nan=False)`）。
- `schema_version` 必须保留；未知版本、缺失必填字段、非法枚举、不支持类型均明确报错（含字段路径）。
- 反序列化不依赖原进程对象（已用独立子进程加载验证）。

## 6. 可复现性

三层含义**不等价**，需分别判定：

1. **结果可序列化**：`result_to_json` 成功；
2. **输入可识别**：有数据集摘要（`dataset.digest`，sha256）且策略被标识（name/version）；
3. **过程可复现**：策略参数可序列化 + 关键配置齐备。

数据集摘要的规范化：列序（`open,high,low,close,volume` 固定 + 其余字母序）、日期 `YYYY-MM-DD`
升序、数值最短可往返表示、缺失记 `NaN`；**不修改**输入 DataFrame；**重复日期**明确报错。
相同逻辑数据 ⇒ 相同摘要；内容变化 ⇒ 摘要变化。

`compare_metadata(a, b)`：关键配置（标的、数据集摘要、市场规则、费用、滑点、执行价规则、
信号延迟、区间）一致才 `fairly_comparable=True`；策略差异属**合法比较维度**，不阻断可比性。

## 7. 已知限制

- 费用/滑点为 **模拟占位参数**（`fee_source=simulation_placeholder`），非真实券商费率。
- 涨跌停默认**未配置**（`price_limit=None`），此时不做涨跌停判定，不冒充已实现。
- 仅单标的、单持仓、整仓买卖；无部分成交、无订单簿、无多标的组合。
- 滑点为简化比例模型，调整后价格不代表真实市场必然可成交。
