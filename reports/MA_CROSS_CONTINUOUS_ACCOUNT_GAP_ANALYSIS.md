# MA-CROSS 独立复核 P0-5：连续账户回测的差距分析

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-27（GMT+8） |
| 目的 | 判断"窗口级独立账户"能否直接当作连续净值；若不能，给出**最小修复方案** |
| 判定 | **`CONTINUOUS_ACCOUNT_NOT_SUPPORTED`** —— 现有引擎**不具备**跨窗口延续持仓的能力 |
| 处置 | **本审计不生成任何连续账户净值**（任务书 §七：账本逻辑与测试通过前不得产出） |

---

## 0. 结论

现有装置可以回答"每个 75 日窗口里，这套参数赚了多少"，**不能**回答"从 2017 年起连续按规则交易，期末资产是多少、最大回撤多少"。

原因不是"没跑"，而是**三层结构缺口**：

| 层 | 缺口 | 后果 |
|---|---|---|
| **引擎层** | `BacktestConfig` 只有 `initial_cash`，**没有初始持仓字段** | 无法把上一窗口末的持仓带进下一窗口；只能推倒重来 |
| **调度层** | walk-forward 每个窗口用**独立账户**，且每窗**重新选参** | 现金/持仓不连续；参数在中途切换，持仓状态无法继承（引擎内 strategy 实例固定） |
| **指标层** | 有日频 MDD peak/trough，但**无 Sharpe、无换手率**；窗口汇总自述为"窗口级统计" | 即使拼出曲线，也缺少风险调整指标与换手口径 |

**因此**：`median_ret`（窗口收益中位数）**不可**解释为可实现年化，**不可**用于推算真实组合的最大回撤。

---

## 1. 十项能力逐条核查

| # | 能力 | 现状 | 证据 | 判定 |
|---|---|---|---|---|
| 1 | 现金/资金连续性 | 单次回测内连续；跨窗口**重置为 100,000** | `parameter_selection.py:12`「窗口之间不传递现金 / 持仓」；`research.py` 每窗 `BacktestConfig(initial_cash=100000)` | **FAIL** |
| 2 | 持仓跨窗口延续 | **不支持** | `BacktestConfig` 字段清单（`config.py:96-110`）无 `initial_positions` / `PositionLedger`；`run_backtest(data, strategy, config)` 只接受初始现金 | **FAIL** |
| 3 | 参数更新与持仓状态 | 引擎一次运行内 strategy 固定；换参只能重开 | `run_walkforward_v2` 每窗新建 strategy 实例（`parameter_selection.py:145`「每个候选使用独立策略实例 + 独立 BacktestConfig」） | **FAIL** |
| 4 | 信号 / 下单 / 成交日期 | **已正确实现** | `engine.py:8-9`：T 日收盘信号 → 订单 T 日 → 最早 T+1 开盘价（经滑点）成交；`signal_lag=1` | **PASS** |
| 5 | 交易费用 | **已实现**：佣金（含最低）+ 印花仅卖 + 过户双向 + 滑点 | `backtest/costs.py`；`fee_presets.py` 三档 | **PASS** |
| 6 | 整手与可负担性 | **已实现**：`_target_buy_quantity` 满仓 / `_affordable_buy_quantity` 逐手回退 / 买不起 1 手 → REJECTED | `engine.py:120-141, 249-254` | **PASS** |
| 7 | 停牌 / 缺失行情 | **未显式建模**：无 `volume==0` 或缺失行拦截，tick 级无观测；停牌期间订单只能顺延到"下一行" | `engine.py` 仅要求 `REQUIRED_COLUMNS=("open","high","low","close","volume")`，全文无 halt 判定 | **FAIL** |
| 8 | 涨跌停 / 未成交 | **已实现（v3）**：`open_fill_block_reason()` 以开盘价与涨跌停边界判定；v2 未启用 | `market/ashare.py:95`；`research.py` `market_rules_for()`；`b20` 的 `price_limit_primary=null` | **PASS（v3）/ N/A（v2）** |
| 9 | 分红 / 复权 | 用前复权价（隐含分红再投资），**未单列现金流** | 数据为 `.qfq`；`B20 §2` 已声明「前复权 ≠ 全收益」 | **PARTIAL** |
| 10 | 期末估值 / 基准 / MDD | 期末按收盘估值**不平仓**（`liquidate_at_end=false`）；有 MDD peak/trough；**无内建 B1 / 无指数基准** | `b20` `window_generation.liquidate_at_end=false`；`performance/metrics.py:97-117`；`B18 §2.2 P5`「全代码库无 Buy&Hold、无指数基准实现」 | **PARTIAL** |

统计：**PASS 4 / FAIL 4 / PARTIAL 2**。

---

## 2. 关键证据（可直接核对）

### 2.1 引擎不接受"带着持仓开工"

`backend/src/deepquant/backtest/config.py:96-110`（节选）

```python
class BacktestConfig:
    symbol: str
    initial_cash: float
    start_date: pd.Timestamp | None = None
    end_date: pd.Timestamp | None = None
    exec_price_rule: ExecutionPrice = ExecutionPrice.NEXT_OPEN
    signal_lag: int = 1
    market_rules: MarketRules = field(default_factory=a_share_rules)
    commission_rate: float = ...
    # 没有 initial_positions / portfolio / position_ledger
```

### 2.2 walk-forward 明文声明窗口不连续

`backend/src/deepquant/walkforward/parameter_selection.py:12`

> 「（含独立 initial_cash），窗口之间不传递现金 / 持仓」

`parameter_selection.py:145`

> 「每个候选使用独立策略实例 + 独立 BacktestConfig（含相同 initial_cash）」

### 2.3 停牌没有被建模（容易被忽略的一条）

引擎遍历的是**该标的 CSV 里存在的行**。停牌日的行情根本不存在，因此：

- 持仓市值在停牌期间"跳过"（不产生日收益观测）；
- 停牌前一日产生的信号，会在**复牌后第一行**的 open 成交——延迟可能是几个月，但**没有任何标记**；
- 与 SP-5 侧已审计出的问题同源（`p04` 用 `ffill` 静默填补缺口），只是这里换成了"跳过"。

---

## 3. 为什么不能"把窗口收益连乘"当作连续净值

1. **账户重置**：每窗都从 100,000 起，窗口首日满仓买入、窗口末日按收盘估值。连乘等于假设"每 75 天准时清仓再满仓"，与规则不符（规则没有强制平仓）。
2. **参数不连续**：每窗重选参（换参率中位 0.667），连乘出的曲线是"每 75 天换一套参数"的复合体，无法对应任何单一可执行策略。
3. **现金不连续**：整手取整残差每窗重新产生 / 丢弃，累计现金拖累不可复现。
4. **可负担性断崖被摊平**：某窗"买不起 1 手"只影响该窗，连乘后看不出它在连续账户里会变成"长期空仓"。

---

## 4. 最小修复方案（设计，**本次未实施**）

按依赖顺序，四步：

| 步骤 | 改动 | 影响面 | 验收 |
|---|---|---|---|
| S1 | `BacktestConfig` 增加 `initial_positions: tuple[Position, ...]`（symbol/shares/cost_basis/acquired_date），引擎在 t0 前把持仓灌入账本，并保留 T+1 锁定信息 | 引擎核心（需评审） | 现金+持仓市值恒等式；T+1 锁不丢 |
| S2 | 新增 `run_continuous_walkforward()`：窗口边界**不平仓**、按收盘价结转权益与持仓、把上一窗选中的参数用于下一窗 | 新增调度层（不改旧函数） | 结转前后 NAV 连续（差 ≤ 1 个 tick 的估值误差） |
| S3 | 停牌显式化：缺失行标注 `HALT`，订单顺延并记录 `deferred_from/to`；复牌跳空单独统计 | 引擎 + 记录 | 停牌期间无成交；顺延天数入账 |
| S4 | 基准与风险指标：内建 B1（同窗口同费率、期末不平仓）+ 换手率 + Sharpe（默认无风险利率 0） | performance | 基准与策略同窗口可配对 |

**前置铁律**：S1~S4 全部通过测试前，**不得**生成或发布任何连续净值曲线。若只做 S2 而不做 S1，等于"用现金重置伪装连续"，属造假。

---

## 5. 与本次任务其他部分的关系

| 关系 | 说明 |
|---|---|
| P0-2（v2 复现） | 复现的是**窗口级**结果；与连续账户无关，不受本缺口影响 |
| P0-3（v3） | 同上；v3 只是加了涨跌停约束，账户仍是窗口独立 |
| P0-4（配对） | 逐窗配对在窗口级口径内成立；**不能**外推为连续收益差 |
| P1（池偏差） | 48 只中有 11 只晚上市（最晚 2022-08），连续账户会碰到"上市前不可交易"，需要额外规则——这本身就是 S2 的一部分 |

---

## 6. 未做与不做

- **未**修改引擎或 walk-forward 任何代码（属规则/核心改动，需授权）；
- **未**用窗口收益连乘"拼"一条连续净值；
- **未**把窗口级 MDD 当作组合 MDD 报出。
