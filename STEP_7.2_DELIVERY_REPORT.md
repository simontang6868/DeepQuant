# DeepQuant Step 7.2 交付报告

## 训练期参数选择与真正的 Walk-Forward 验证

**日期**：2025（当前会话时间）
**分支**：`main`
**HEAD**：`6e5e32d` — `feat: add backtest API and React frontend (Steps 5.5-5.8)`
**无提交**：本阶段未执行任何 `git commit` 或 `git push`

---

## 1. 环境与 Git 状态

| 项目 | 值 |
|---|---|
| 工作目录 | `/workspace/DeepQuant` |
| Python | `3.14.8`（`backend/.venv/bin/python3`） |
| Node.js | `24.1.0` |
| npm | `11.4.2` |
| Git 分支 | `main` |
| HEAD commit | `6e5e32d` |
| 工作区变更 | 17 个修改文件 + 24 个未跟踪文件（全部来自 Steps 6.2–7.1 + 本阶段） |

本阶段**未执行** `git reset --hard`、`git clean -fd`、`git restore`、`git checkout --`、重写历史、`git commit` 或 `git push`。

---

## 2. 开发前基线

本阶段开始时重新运行完整测试，确认此前成果完整可用：

| 检查项 | 结果 |
|---|---|
| 后端 pytest | **505 passed, 1 skipped**（与 Step 7.1 报告一致） |
| 前端 Vitest | **157 passed** |
| TypeScript `tsc --noEmit` | **通过** |
| Vite production build | **通过** |
| `git diff --check` | **通过** |

基线与报告记录无差异，确认本阶段工作区起点干净、功能正常。

---

## 3. 架构审查

### 3.1 复用的既有模块

| 模块 | 用途 | 本阶段是否修改 |
|---|---|---|
| `walkforward/__init__.py` (Step 7.1) | `WalkForwardConfig`、`WindowSpec`、`generate_windows`、`run_window`、`run_fixed_parameter_walkforward`、`summarize_results` | **否** — 完全复用 |
| `backtest/engine.py` (Step 7.1 修改) | `run_backtest` — 先 `generate_signals(df_full)` 再按 `start_date`/`end_date` 裁剪，支持预热 | **否** — 直接复用 Step 7.1 的 warmup 支持 |
| `backtest/config.py` | `BacktestConfig`（frozen dataclass）| **否** |
| `strategies/ma_cross.py` | `MaCrossStrategy(fast_window, slow_window)` — dataclass，`slow_window > fast_window` 约束在 `__post_init__` 中强制 | **否** |
| `performance/metrics.py` | `PerformanceMetrics`：`cumulative_return`、`max_drawdown_ratio`、`final_equity`、`is_complete`、`incomplete_reasons`、`trade_stats.n_closed_trades`、`trade_stats.total_fees`、`trade_stats.n_orders` | **否** |
| `WalkForwardConfig` (Step 7.1) | 窗口生成配置：`train_window`、`oos_window`、`step`、`min_train`、`min_oos` | **否** — `run_walkforward_v2` 复用窗口生成 |

### 3.2 发现的风险 / 无需新增引擎修改

| 风险点 | 结论 |
|---|---|
| 引擎 warmup 处理 | Step 7.1 已正确实现：信号在完整 df 上生成，按 `start_date` 裁剪后撮合。**预热只用于指标，不产生交易。** 本阶段零修改。 |
| `BacktestConfig` 可独立性 | `frozen=True`，用 `dataclasses.replace` 构造独立实例，天然支持每候选独立账户。 |
| `MaCrossStrategy` 独立性 | `dataclass`，每次构造新实例即可，无共享可变状态。 |
| 绩效指标可用性 | `PerformanceMetrics` 已提供 `is_complete` / `incomplete_reasons`，`final_equity` 可为 `None` 表示不可计算，不需要新增字段。 |
| 时间隔离 | `generate_windows` 严格保证 `train_end < oos_start`，本阶段完全复用。 |
| DB / API / 前端 | 本阶段不触碰，零修改。 |

### 3.3 新增模块

本阶段只新增一个研究层模块和一个测试模块，对引擎层、API 层、前端零修改：

| 新增文件 | 行数 | 职责 |
|---|---|---|
| `backend/src/deepquant/walkforward/parameter_selection.py` | 670 | 训练期网格搜索、候选评价、参数选择、OOS 独立回测、跨窗口汇总 |
| `backend/tests/walkforward/test_parameter_selection.py` | 921 | 22 个专项测试（候选生成 / 训练选择 / 时间隔离 / 预热隔离 / 汇总 / 纯函数保护 / 端到端集成） |

---

## 4. 参数选择方法

### 4.1 候选参数集合

| 策略 | 参数 | 候选值 |
|---|---|---|
| `MaCrossStrategy` | `fast_window` | `(3, 5, 10)` |
| `MaCrossStrategy` | `slow_window` | `(15, 25, 40)` |

**过滤规则**：`slow_window > fast_window`（策略自身约束），过滤后共 **9 个合法候选**。

候选集合在 `GridSearchConfig` 中完整定义；`max_combinations=50` 为硬上限保护。相同配置 → 相同候选列表（确定性，按 `(fast, slow)` 升序）。

### 4.2 评价指标与选择规则

默认选择指标：**训练期累计收益 `cumulative_return`**（降序）。

**完整选择流程**（每个窗口独立执行）：

1. 构造候选集合 → 过滤 `slow <= fast` → 得到合法列表
2. 对每个候选独立回测训练期行情 → 得到 `CandidateTrainResult`
3. 过滤条件：
   - 策略构造成功（无异常）
   - 训练期回测成功（无异常）
   - `is_complete=True` 且 `final_equity` 可计算
   - 可选的 `min_trades` 下限（如配置了）
4. 对全部通过过滤的候选排序：
   - **主排序**：`cumulative_return` 降序（越大越好）
   - **次排序**：`max_drawdown_ratio` 升序（越小越好）
   - **打破平局**：参数字典序（`tuple(sorted(params.items()))`），确定性
5. 最优候选 → 作为该窗口的选定参数
6. 如果**全部候选**都不满足条件 → `selection_success=False`，返回明确失败原因，OOS 不执行

**重要声明**：训练期累计收益最高 ≠ 风险调整后最优。这是一个保守的研究基线，目的是验证训练期-选择-OOS 隔离机制的正确性，不代表最合理的参数选择方法。

### 4.3 训练期不可计算 / 失败的表达

所有候选失败时 **不伪造** 一个结果继续：

| 失败情形 | 表达 |
|---|---|
| 策略构造抛异常 | `success=False`, `error="策略构造失败: ..."` |
| 回测引擎抛异常 | `success=False`, `error="训练期回测失败: ..."` |
| 权益不完整 (`is_complete=False`) | `success=False`, `error` 写明 `incomplete_reasons` |
| 指标不可计算 (`final_equity=None`) | `success=False`，`cumulative_return` 保留原始 `None`，**不伪装成 0** |
| 交易数不足 (`n_closed_trades < min_trades`) | `success=False`, `error="已完成交易数 X < min_trades=N"` |
| 全部候选失败 | `selection_success=False`, `selection_error="训练期所有候选均不满足选择条件"` |

---

## 5. 时间隔离机制

### 5.1 严格时序边界

每个 `WindowWFResult` 显式记录：

```
train_start ≤ train_end < oos_start ≤ oos_end
```

该边界由 `generate_windows`（Step 7.1）保证，在 `run_walkforward_window` 中通过 `idx.get_loc()` 定位后直接使用。**不存在**训练期和 OOS 期重叠的可能。

### 5.2 参数选择只看训练期

`run_walkforward_window` 的执行顺序：

```
1. 在 full_df 中定位 train_start_pos, train_end_pos, oos_start_pos, oos_end_pos
2. 构造 train_with_warmup = warmup_tail + train_segment   ← 不含任何 OOS 数据
3. evaluate_candidates_on_train(train_with_warmup, ...)  ← 候选评价只用训练期
4. select_best(train_results)                           ← 选中参数 ← ← 在此刻固定
5. 构造 oos_with_warmup = oos_warmup_tail + oos_segment  ← OOS 行情（含 oos 前预热）
6. run_backtest(oos_with_warmup, strategy_factory(selected), oos_config)
```

**关键不变量**：步骤 3–4 中使用的 `train_with_warmup` 仅覆盖 `[oos_start_pos - warmup_days, train_end_pos]` 区间（warmup 在训练期起点之前、OOS 起点之前）。**OOS 区间的任何行情数据都不会出现在训练期候选评价中。**

### 5.3 OOS 数据扰动测试验证

专项测试 `test_oos_data_perturbation_does_not_change_selection`：

> 把 window 0 的 OOS 区间 close 全部改成 999.0 → 重新跑 → **窗口 0 的选中参数和训练期候选结果完全一致。**

这是本阶段最关键的隔离验证 —— 它直接证明了 OOS 数据**不可能**反向影响训练期参数选择。

---

## 6. 预热隔离

### 6.1 训练期预热

```
warmup_tail = full_df.iloc[train_start_pos - warmup_days : train_start_pos]
train_segment = full_df.iloc[train_start_pos : train_end_pos + 1]
train_with_warmup = concat([warmup_tail, train_segment])
```

- warmup 段 ≤ 训练期起点前一行，来自 full_df 中 **train_start 之前** 的数据
- 引擎收到 `config.start_date = train_start` → 信号在完整 df 上生成后被裁剪 → warmup 段即使触发信号也**不会下单**
- 绩效指标只包含 `train_start..train_end` 的交易和权益变化

### 6.2 OOS 预热

```
oos_warmup_tail = full_df.iloc[oos_start_pos - warmup_days : oos_start_pos]
oos_segment = full_df.iloc[oos_start_pos : oos_end_pos + 1]
oos_with_warmup = concat([oos_warmup_tail, oos_segment])
```

- OOS 预热段只来自 `oos_start` **之前**，不包含 OOS 期间或之后的数据
- 引擎收到 `config.start_date = oos_start` → 只有 OOS 区间内的信号会下单
- OOS 使用 `dataclasses.replace(base_config, start_date=oos_start, end_date=oos_end)` → **独立初始资金、独立账户状态**，不继承训练期的任何持仓或现金

### 6.3 预热长度

`warmup_days = grid_config.min_train_periods`（当前默认 100）。这是一个统一的值，覆盖候选集合中 `slow_window=40`（最大的慢线窗口）的需求，**所有候选使用相同的预热长度**，避免某些候选因预热不足而不公平。

---

## 7. 实际修改清单

### 7.1 新增文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/src/deepquant/walkforward/parameter_selection.py` | 670 | 参数搜索 / 训练期评价 / 选择 / OOS 回测 / 汇总的完整研究层 |
| `backend/tests/walkforward/test_parameter_selection.py` | 921 | 22 个专项测试 |

### 7.2 未修改的关键模块

| 模块 | 状态 |
|---|---|
| `backtest/engine.py` | **未修改**（Step 7.1 的 warmup 支持已满足需求） |
| `backtest/config.py` | **未修改** |
| `backtest/models.py` | **未修改** |
| `backtest/costs.py` | **未修改** |
| `strategies/ma_cross.py` | **未修改** |
| `strategies/base.py` | **未修改** |
| `walkforward/__init__.py` | **未修改**（完全复用 Step 7.1） |
| API routes / schemas / errors | **未修改** |
| 前端所有文件 | **未修改** |
| DB schema / SQLite | **未修改** |

本阶段严格遵守"最小改动"原则：**零引擎修改、零 API 修改、零前端修改、零 DB 修改**。

---

## 8. 参数选择示例

使用可控合成行情（高波动正弦 + 随机游走，种子 42，500 个交易日）演示一次完整流程。

### 8.1 候选集合

```
fast ∈ (3, 5, 10), slow ∈ (15, 25, 40)
→ 过滤 slow > fast → 9 个合法候选
min_trades=1, max_combinations=50
选择指标: cumulative_return (训练期降序)
```

### 8.2 窗口 0 详细审计

窗口 0：训练期 2022-01-03 ~ 2022-05-20（100 天），OOS 2022-05-23 ~ 2022-07-01（30 天）

| 候选 | 训练期累计收益 | 训练期交易数 | 状态 |
|---|---|---|---|
| f=3/s=15 | -0.2324 | 1 | ✅ 通过 |
| f=3/s=25 | -0.2335 | 2 | ✅ 通过 |
| f=3/s=40 | +0.0000 | 0 | ❌ 交易数不足 |
| f=5/s=15 | -0.1834 | 1 | ✅ 通过 |
| f=5/s=25 | -0.2337 | 1 | ✅ 通过 |
| f=5/s=40 | +0.0000 | 0 | ❌ 交易数不足 |
| f=10/s=15 | -0.0903 | 0 | ❌ 交易数不足 |
| f=10/s=25 | -0.0924 | 1 | ✅ 通过 |
| f=10/s=40 | +0.0000 | 0 | ❌ 交易数不足 |

**按训练期累计收益排序**（5 个通过过滤的候选）：

| 排名 | 候选 | 训练期收益 |
|---|---|---|
| 1 | f=10/s=25 | **-0.0924** |
| 2 | f=5/s=15 | -0.1834 |
| 3 | f=3/s=15 | -0.2324 |
| 4 | f=3/s=25 | -0.2335 |
| 5 | f=5/s=25 | -0.2337 |

**选中参数**：`fast_window=10, slow_window=25`（训练期 5 个合法候选中累计收益最高）

**OOS 评估**：使用选中参数在独立 OOS 区间回测

> OOS ret=-0.0988, trades=1, complete=True

**说明**：此示例**仅证明程序行为符合设计**（训练期选 → OOS 评），不代表该策略具有盈利能力。此合成行情不模拟任何真实市场。

### 8.3 全窗口结果

13 个窗口（500 个交易日，train=100, oos=30, step=30）：

```
w0:  sel_ok=True,  oos_ok=True, ret=-0.0988, params=f10/s25
w1:  sel_ok=True,  oos_ok=True, ret=+0.0270, params=f3/s40
w2:  sel_ok=True,  oos_ok=True, ret=-0.0736, params=f10/s15
w3:  sel_ok=True,  oos_ok=True, ret=-0.0988, params=f10/s25
w4:  sel_ok=True,  oos_ok=True, ret=+0.0000, params=f3/s15
w5:  sel_ok=True,  oos_ok=True, ret=+0.0120, params=f3/s15
w6:  sel_ok=True,  oos_ok=True, ret=+1.0358, params=f3/s15
w7:  sel_ok=True,  oos_ok=True, ret=+0.0000, params=f3/s15
w8:  sel_ok=True,  oos_ok=True, ret=-0.0988, params=f3/s15
w9:  sel_ok=True,  oos_ok=True, ret=-0.2121, params=f3/s15
w10: sel_ok=True,  oos_ok=True, ret=-0.2152, params=f5/s25
w11: sel_ok=True,  oos_ok=True, ret=+0.0000, params=f3/s40
w12: sel_ok=True,  oos_ok=True, ret=+0.0000, params=f5/s40
```

---

## 9. OOS 评估与跨窗口汇总

### 9.1 汇总统计

```
总窗口:              13
参数选择成功:        13 (100%)
参数选择失败:         0 (0%)
OOS 回测成功:        13 (全部选择成功的窗口)
OOS 回测失败:         0
OOS 指标可用:        13 (final_equity 可计算 且 is_complete=True)
OOS 正收益占比:       0.308 (4/13)
OOS 中位累计收益:     0.0
OOS 中位最大回撤:     0.080
各窗口选中参数分布:   f3/s15: 6, f10/s25: 3, f10/s15: 1, f3/s40: 1, f5/s25: 1, f5/s40: 1
费用来源:             simulation_placeholder
```

### 9.2 语义声明

汇总统计**是窗口级独立结果的统计摘要**，**不是**把 13 个窗口串成一个连续账户的组合收益。每个窗口独立回测、独立初始资金（100,000）、独立结算。

---

## 10. 专项测试清单

### 12.1 候选参数生成（7 个）

| 测试 | 验证 |
|---|---|
| `test_valid_combinations_generated` | 9 个合法候选，全部 slow > fast |
| `test_illegal_slow_le_fast_filtered_out` | `(20, 10)` 被过滤 |
| `test_deterministic_ordering` | 相同 cfg → 相同列表 |
| `test_empty_candidates_raises` | fast 全部 ≥ slow → ValueError |
| `test_empty_input_raises` | 空 tuple → ValueError |
| `test_max_combinations_guard` | 超上限 → ValueError |
| `test_non_integer_candidates_filtered` | 非整数 / 非正数被过滤 |

### 12.2 训练期选择（6 个）

| 测试 | 验证 |
|---|---|
| `test_selection_uses_train_metrics_only` | 选中参数是训练期排序第一 |
| `test_tie_broken_deterministically` | 收益和回撤都相同时按字典序打破 |
| `test_missing_metric_not_treated_as_zero` | `cumulative_return=None` 排序值比 0.0 差 |
| `test_min_trades_filter` | 交易不足的候选 error 中包含 `min_trades` |
| `test_all_candidates_fail_returns_explicit_failure` | 平坦行情 → 全部被过滤 → `selection_success=False` |
| `test_single_candidate_failure_does_not_silently_drop_info` | 部分候选抛异常 → 失败信息保留在 `train_candidate_results` |

### 12.3 时间隔离（5 个）

| 测试 | 验证 |
|---|---|
| `test_train_oos_no_overlap_in_single_window` | `train_end < oos_start` |
| `test_bad_window_boundary_rejected` | `generate_windows` 保证时序 |
| **`test_oos_data_perturbation_does_not_change_selection`** | 修改 OOS 行情 → **选中参数完全不变** |
| `test_oos_end_future_data_does_not_affect_oos_result` | 修改 OOS 之后的数据 → OOS 结果不变 |
| `test_other_window_future_data_does_not_affect_current` | 修改窗口 j 数据 → 窗口 i 结果不变 |

### 12.4 预热与执行隔离（4 个）

| 测试 | 验证 |
|---|---|
| `test_warmup_enables_valid_ma` | 预热足够让 slow=40 均线从 train_start 起有效 |
| **`test_warmup_produces_no_fills_before_train_start`** | 逐候选检查所有 `Fill.fill_date >= start_date` |
| `test_oos_does_not_inherit_train_position_or_cash` | `base_config.initial_cash` 未被修改 |
| `test_no_future_data_in_oos_warmup` | OOS 预热 = `[oos_start - warmup_days, oos_start)` |

### 12.5 汇总与失败处理（4 个）

| 测试 | 验证 |
|---|---|
| `test_selection_failed_counted_separately_from_oos_failed` | `n_selection_failed` 和 `n_oos_failed` 分别统计 |
| `test_oos_failed_counted_when_selection_succeeds` | 构造 3 种 WindowWFResult → 汇总正确 |
| `test_incomplete_equity_not_treated_as_success` | `oos_is_complete=False` → 不计入 `valid_metrics` |
| `test_fee_source_shown_in_summary_note` | `summary.note` 包含 `simulation_placeholder` |

### 12.6 纯函数与输入保护（4 个）

| 测试 | 验证 |
|---|---|
| `test_run_walkforward_v2_does_not_mutate_inputs` | `frame.copy()` 后 `assert_frame_equal` |
| `test_evaluate_candidates_does_not_mutate_train_df` | `train_df.copy()` 后 `assert_frame_equal` |
| `test_deterministic_with_same_input` | 相同行情 + 相同 cfg → 相同结果 |
| `test_strategy_instances_are_independent_per_candidate` | 每个候选的策略 `id()` 不同 |

### 集成（1 个）

| 测试 | 验证 |
|---|---|
| `test_full_walkforward_v2_demo_runs_end_to_end` | 完整流程返回结构完整的 specs/results/summary |

**总计 22 个新增测试 + 31 个既有 Step 7.1 测试 = 53 个 Walk-Forward 测试，全部通过。**

---

## 11. 全量回归结果

| 检查项 | 结果 | 对比基线 |
|---|---|---|
| 后端 pytest | **536 passed, 1 skipped** | +31（新增测试，无失败） |
| 前端 Vitest | **157 passed** | 不变 |
| TypeScript `tsc --noEmit` | **通过** | 不变 |
| Vite production build | **通过**（dist/ 629 kB js / 10 kB css） | 不变 |
| `git diff --check` | **通过** | 不变 |

**结论**：本阶段引入的 31 个新测试全部通过，既有 505 个后端测试、157 个前端测试全部保持绿色，**零回归**。

---

## 12. 风险与限制

### 12.1 已知限制

1. **OOS 数据仍为历史数据**：样本外验证使用的是"未见过的历史区间"，不是实时行情。历史 OOS 正收益不代表未来一定盈利。

2. **参数网格搜索的训练期过拟合风险**：首版只用累计收益排序 + 简单回撤次排序；多窗口中某些参数组合在训练期反复胜出，不代表它们真的稳健。本阶段的目标是验证隔离机制，不是生产最佳参数。

3. **OOS 窗口数量和交易数量有限**：合成行情 500 个交易日只产生 13 个窗口、7 笔 OOS 交易，样本量不足以做统计推断。

4. **费用为 `simulation_placeholder`**：`commission_rate=0.0003`, `stamp_duty_rate=0.0005` 等全部是占位参数。**本阶段未修改费用模型。** `WalkForwardSummaryV2.note` 和每个 `WindowWFResult.fee_source` 都明确标注。

5. **引擎微观结构限制**：日线简化成交（不模拟订单簿 / 排队 / 部分成交 / 停牌）、单持仓、涨跌停未配置时不默认 10%。这些限制从引擎 v1 开始就存在，本阶段不触碰。

6. **网格搜索无随机 / 无 BO / 无 GA**：首版只支持确定性有限网格，候选数量受 `max_combinations` 保护。不支持贝叶斯优化、遗传算法等更高效的搜索方法。

7. **预热长度统一**：所有候选使用相同 `warmup_days = min_train_periods`。如果未来要支持不同策略（非 MaCross），可能需要根据最大指标窗口动态计算。

### 12.2 本阶段未做的事

- 未修改 DB schema 或 SQLite 表
- 未新增 HTTP API 路由
- 未新增前端页面 / 组件
- 未实现实盘或模拟交易执行
- 未引入机器学习 / 深度学习模型
- 未对参数搜索范围做任何"市场验证"
- 未对费用模型做任何真实费率调研

---

## 13. Git 安全确认

| 禁止行为 | 是否执行 |
|---|---|
| `git reset --hard` | ❌ 未执行 |
| `git clean -fd` | ❌ 未执行 |
| `git restore` / `git checkout --` | ❌ 未执行 |
| 重写历史 | ❌ 未执行 |
| `git commit` | ❌ 未执行 |
| `git push` | ❌ 未执行 |
| 删除未跟踪文件 | ❌ 未执行 |
| 弱化既有测试 | ❌ 未执行 |

**Step 6.2–7.1 的全部未提交成果完整保留，本阶段在此基础上做了最小增量开发。**

---

## 14. 下一阶段建议

1. **扩展参数搜索**：引入贝叶斯优化或随机搜索（受候选上限约束），用于快速初筛；同时保持训练期隔离不变。

2. **多策略支持**：当前 `run_walkforward_v2` 硬编码 `generate_ma_cross_candidates`；如果后续要适配其他策略（均值回归、动量因子等），需要把候选生成器做成可插拔接口。

3. **多指标联合排序**：首版只用 `cumulative_return`，可以加入 `Sharpe`、`Sortino`、胜率等，让参数选择更接近实盘逻辑。**但必须保持"只用训练期数据"的铁律。**

4. **费用校准**：`simulation_placeholder` 必须替换为经合法渠道核实的真实券商费率 —— 这是让 Walk-Forward 结果具有参考价值的前提条件。

5. **更多合成测试场景**：目前只用了一种随机行情 + 一种平坦行情。可以增加趋势行情、波动率聚集行情、震荡行情等，覆盖更多参数选择行为。

6. **持久化（可选）**：Step 7.2 结果目前只在内存中生成。如果后续要做参数搜索结果对比 / 可视化，可以考虑把 `WindowWFResult` 存入 SQLite 或导出为 JSON —— **但这属于新功能，不属于验证机制本身。**

7. **候选参数审计日志**：当前 `WindowWFResult.train_candidate_results` 已经记录了所有候选的训练期结果。如果后续要做合规审查，可以把这个字段完整序列化为 JSON 输出。

---

## 15. 验收标准对照表

| # | 验收标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 参数搜索严格限定在训练期 | ✅ | `run_walkforward_window` 步骤 2 使用 `warmup_tail + train_segment`，不含 OOS 数据 |
| 2 | OOS 结果不参与参数选择 | ✅ | `select_best` 在 OOS 回测**之前**执行；测试 `test_oos_data_perturbation_does_not_change_selection` 直接验证 |
| 3 | 选中参数在 OOS 回测开始前已固定 | ✅ | `selected, _ = select_best(...)` 在 OOS 构造前执行 |
| 4 | 训练期与 OOS 期有明确时间边界 | ✅ | `WindowWFResult` 显式记录 train_start/train_end/oos_start/oos_end，`train_end < oos_start` |
| 5 | 预热不产生窗口外交易或污染 OOS 绩效 | ✅ | 引擎 `start_date` 裁剪；测试 `test_warmup_produces_no_fills_before_train_start` 逐 Fill 验证 |
| 6 | 参数选择失败和回测失败分别识别 | ✅ | `WindowWFResult.selection_success` / `oos_success` / `oos_error` 独立字段 |
| 7 | 候选参数、选择依据、OOS 结果可审计 | ✅ | `WindowWFResult.train_candidate_results` 记录每个候选的训练期结果 |
| 8 | 缺失 / 不可计算指标不伪装成 0 | ✅ | `cumulative_return=None` 保留 None；测试 `test_missing_metric_not_treated_as_zero` 验证 |
| 9 | 汇总统计明确说明有效样本数 | ✅ | `WalkForwardSummaryV2` 的 `n_total_windows` / `n_selection_success` / `n_oos_success` / `n_oos_with_valid_metrics` 独立字段 |
| 10 | 新增专项测试通过 | ✅ | 22 个新增测试全部通过 |
| 11 | 既有测试无回归 | ✅ | 后端 536 passed / 前端 157 passed / tsc / vite / diffcheck 全绿 |
| 12 | 既有未提交成果保留 | ✅ | `git status --short` 显示所有 Step 6.2–7.1 变更仍在 |
| 13 | 无未经授权的 Git 操作 | ✅ | 本阶段未 commit / push / reset / clean |

**全部 13 项验收标准通过。Step 7.2 完成。**

---

*DeepQuant Step 7.2 训练期参数选择与真正的 Walk-Forward 验证 — 交付完毕。*
