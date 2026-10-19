# W1 逐窗明细落盘 —— 实现报告（阶段 B：B-1）

> 生成时间：2026-10-18 15:58 GMT+8 ｜ 执行线：**B 线**（判断 / 真实数据）
> 对应任务书：阶段 B「W1 持久化」之 B1（落盘）+ B2（字段完整性）
> **本轮不 commit / 不 push / 不 merge / 不提 PR**，代码改动全部留在本地工作副本。

---

## 0. 一句话结论

历史 288 组结果**只落盘了分位数**（`window_stats`），导致 B20 只能做「分布级对照」而不能做
逐窗配对。本轮补上了这个缺口：新增 `windows.csv` 逐窗明细，**每窗一行**，含 13 类信息
（身份 / 数据指纹 / 窗口边界 / 股池 / 成交 / 成本 / 策略绩效 / 同期基准 / 差值 / 状态 / 异常），
并已用真实行情端到端跑通、两次运行逐字节一致。

**关键限定**：这只解决「以后能配对」，**不解决「历史 288 组能配对」**——那些结果跑在没有
明细的装置上，无法事后重建。这条限制没有被绕过，也没有被淡化。

---

## 1. 先查了现有实现，避免重复造

任务书明确要求「先查现有实现，避免重复实现」。查的结果：

| 已有 | 能复用什么 | 不能复用什么 |
|---|---|---|
| `walkforward/parameter_selection.py`：`WindowWFResult` | 窗口边界、选中参数、OOS 收益/回撤/费用/trade_count、失败原因 | **不含逐笔成交**：`oos_result` 抽出 `performance` 后即丢，`fills`/`orders` 没被保留 |
| `walkforward/__init__.py`：`WindowResult`（v1 固定参数版） | 语义参考 | 与 v2 不是同一装置（v2 才有训练期选参），不能混用 |
| `performance/benchmarks.py`：`buy_and_hold` | **直接复用**，含整手回退、期初涨停拦截、`is_executable` 语义 | — |
| `performance/curve.py`：`curve_stats` | 收益/年化/波动/回撤的唯一定义 | — |
| `performance/window_distribution.py` | `paired` 标记语义 | 它的 `compare_windows` 是**分布级**对照，不能冒充配对 |
| `data/step75/research.py` | 现有 walk-forward 主循环、数据闸门、费率三档 | 只写 `step75_research_summary.json`（分位数），无逐窗行 |

结论：**没有任何现成的逐窗落盘实现**。最接近的 B20 脚本是「读分位数 + 事后重算基准」，
拿不到策略逐窗成交与逐窗净值，所以它自己也写明了「B1 只能分布级」。

---

## 2. 改了什么（4 个文件）

### 2.1 新增 `backend/src/deepquant/performance/window_records.py`（约 600 行）

核心对象：

| 对象 | 职责 |
|---|---|
| `ExperimentContext` | **实验级**固定条件（标的/策略/费率/滑点/涨跌停/数据指纹/股池/窗口长度）。提升到实验级是为了从类型上排除「同实验内不同窗口用了不同费率」这种无法事后发现的污染 |
| `WindowRecord` | 一个窗口的事实行；字段与 CSV 列**一一对应、无隐藏字段** |
| `make_experiment_id()` | 内容哈希派生 ID（口径版本+标的+策略+费率+涨跌停模式+数据指纹+区间+参数网格）→ 同配置幂等、异配置必不同 |
| `build_records()` | `WindowWFResult[]` + 各窗口 OOS 行情 → `WindowRecord[]`；基准用**同一 config**在**同一 OOS 段**构造 |
| `write_records()` / `read_records()` | 原子写 + 幂等 + **拒覆盖**；读回时校验表头 schema，列增删直接报错 |
| `align_records()` | 按 `window_key` 一对一配对，校验 key 集合 + OOS 起止日期，任何错位抛 `WindowAlignmentError` |
| `summarize_records()` | 同时给 `median_paired_excess`（逐窗差的中位数）与 `median_strategy_minus_median_benchmark`（两个中位数之差），**两个名字不同、数值不同** |

### 2.2 修改 `walkforward/parameter_selection.py`（纯加法，默认零影响）

- `WindowWFResult` 末尾新增 `fills: tuple[dict,...] = ()`、`orders: tuple[dict,...] = ()`。
- `run_walkforward_window(..., capture_trades: bool = False)`、`run_walkforward_v2(..., capture_trades=False)`。
- **默认 `False`**：既有 288 组结果的指标、内存占用完全不变。这一条**有测试钉死**
  （`test_capture_trades_off_keeps_metrics_and_on_adds_fills`：同输入下 `median_cumulative_return`、
  `total_oos_trades`、`total_oos_fees` 三项在开/关采集时完全相同）。
- 为什么不用「事后重跑一次取成交」：那会让「落盘的指标」与「落盘的成交」来自两次运行，
  一旦有非确定性就无法判断哪一份是真的。

### 2.3 修改 `data/step75/research.py`（`DEEPQUANT_W1_OUT` 开关）

- **默认不开启**，设 `DEEPQUANT_W1_OUT=<目录>` 才落盘 → 既有产出一字不变。
- 每个 (标的 × 策略 × 费率档) 一个实验目录 `<out>/<experiment_id>/windows.csv`。
- 另写 `<out>/w1_index.json`（本次运行的实验清单 + 每组 `n_paired` 与两个「差值」）。
- 写盘冲突（同实验重跑但内容不同）→ 打印 `[W1-CONFLICT]`，**继续跑其它组**，索引里留痕。
  不静默覆盖，也不因为一组冲突就中断 30 组的长跑。

### 2.4 新增 `backend/tests/performance/test_window_records.py`（23 条）

| 组 | 测试 | 守的是什么 |
|---|---|---|
| 字段 | `test_schema_covers_required_field_groups` | 任务书 13 类信息必须都有列（缺一即红） |
| | `test_ok_window_excess_return_is_strategy_minus_benchmark` | 差值定义 |
| 失败 | `test_selection_failed_window_is_recorded_and_written` | 失败窗口**照样落盘**，不得丢弃 |
| | `test_oos_failed_window_keeps_error_and_no_fake_return` | 失败不填 0 |
| | `test_incomplete_valuation_is_marked_not_treated_as_ok` | 估值不完整单列状态 |
| | `test_success_without_metrics_is_no_metrics` | 无指标 ≠ 持平 |
| | `test_missing_values_stay_empty_not_zero_after_roundtrip` | 往返后仍为空 |
| 基准 | `test_unaffordable_benchmark_is_not_zero_return` | 买不起 1 手 → 不可执行，不是 0% |
| | `test_benchmark_without_frame_is_marked_missing` | 缺行情标原因 |
| 写盘 | `test_write_then_read_roundtrip_preserves_all_fields` | **逐字段**往返一致 |
| | `test_write_is_idempotent_for_same_content` | 同内容不重写 |
| | `test_write_refuses_to_overwrite_different_content` | 拒覆盖 |
| | `test_two_experiments_never_collide` | 不同配置不互相覆盖 |
| | `test_write_rejects_mixed_experiments_in_one_call` | 一次写盘只能一个实验 |
| | `test_duplicate_window_index_is_rejected` | 重复窗口号拒绝 |
| | `test_experiment_id_changes_with_config_and_is_stable` | 幂等且区分 |
| 配对 | `test_align_pairs_by_key_not_by_position` | **按 key 不按位置**（乱序入参仍正确配对） |
| | `test_align_rejects_missing_window` | 缺窗口拒绝 |
| | `test_align_rejects_oos_date_mismatch` | 日期不一致拒绝 |
| | `test_align_rejects_cross_experiment_pairing` | 跨实验拒绝 |
| 汇总 | `test_summarize_separates_paired_median_from_median_difference` | 两个「差值」用经典反例证明确实不相等 |
| | `test_summarize_counts_statuses_including_failures` | 分母含失败 |
| 无回归 | `test_capture_trades_off_keeps_metrics_and_on_adds_fills` | 默认关闭时指标不变 |

---

## 3. 端到端冒烟（真实行情，**不是研究结论**）

配置：`600519.SH`，2019-01-01~2024-12-31，单标的池，仅 `simulation_placeholder` 一档费率，
`ma_slow_max=25`（为省时间收窄网格）。**这是装置验证，不是正式口径**——参数与正式研究不同，
任何收益数字都不得引用。

### 3.1 输出规模

| 实验 | 窗口数 | 状态分布 | 成交笔数 | 订单数 |
|---|---|---|---|---|
| `ma_cross` / `af433a28161b8b80` | 23 | `ok` 11 / `selection_failed` 12 | 35 | 53 |
| `mean_reversion` / `b10b676fd91e941c` | 23 | `ok` 10 / `selection_failed` 13 | 24 | 73 |

- 23 窗 × 2 = 46 行全部落盘，**包含全部 25 个选择失败窗口**（未丢弃、未填 0）。
- 可配对窗口数 `n_paired` = 9 / 8（只有真正算出策略收益的窗口才配对，失败窗口不硬凑）。

### 3.2 确定性

两个独立进程运行，`windows.csv` 的 sha256：

```
af433a28...  b6eec789222accdfab8b4c76af98f2a741d81b252212b746f9b8cc531445ecfa  (32754 B)
b10b676f...  f48f127ae847281390f7fa3de4a9118921503ec5d802fe11e909094d292defef  (36285 B)
```

两次完全一致。`run_at` 被刻意排除在 `windows.csv` 之外，否则无法用哈希证明「结果一致」。

### 3.3 冒烟里抓到的两个真问题

**问题 1（已修）：`capture_trades` 忘了透传 → 成交列全是空。**

第一版只加了 `run_walkforward_v2(capture_trades=...)` 参数，却没在 `research.py` 里传，
结果 CSV 出现「`order_count=8` 但 `n_fills=0`」。这正是**必须端到端跑一次**才能发现的错——
单元测试里手动构造 `WindowWFResult` 是发现不了的。修复后 35/24 笔成交正常落盘。

**问题 2（记录，不改）：`trade_count` 是「已平仓交易数」，不是「成交笔数」。**

冒烟第一窗口：`final_equity=152,121`、`cumulative_return=+52.1%`、`trade_count=0`、`n_fills=1`。
看起来像「没交易却赚了 52%」，实际是**买入后持有到窗口结束未卖出**，所以已平仓交易数为 0。
这不是缺陷，是 `performance.metrics` 的既有定义。但它是**极容易误读的组合**，因此：

- `windows.csv` 同时保留 `trade_count`（已平仓）与 `n_fills`（成交笔数）两列；
- 本报告明确写出两者语义不同，**不得互相替代**。

### 3.4 顺带观测（诊断用，不作结论）

- 被拒订单原因分布：`insufficient_position`（11 / 38 笔）、`insufficient_cash`（7 / 2 笔）、
  `already_holding`（9 笔，仅 mr）。
- `insufficient_position` 数量不小，指向「策略在预热段/窗口起点就发出卖出信号」这一既有行为。
  这是**下一步值得单独查的口径问题**，本轮只记录，不解释、不修（属引擎行为，红线）。
- `selection_failed` 占一半以上：这是 `min_trades=2` 门槛 + 收窄网格（慢线≤25）+ 单标的所致，
  **不能外推到正式口径**。它同时说明一件事：如果只看「成功窗口的收益中位数」，
  分母会从 23 掉到 11——这正是必须把失败窗口写进 CSV 的原因。

---

## 4. 这批数据能回答什么 / 不能回答什么

**能回答**（Gate 1 起）：
- 某只股票某个窗口，策略跑赢买入持有多少 → `excess_return` 逐窗可算；
- 逐窗差的中位数（`median_paired_excess`）与两个中位数之差**是两个数**，可分别报告；
- 某个窗口为什么没成交 → `orders_json` 里有 `status` 与 `reason`；
- 两组配置是否真的同数据同口径 → `dataset_digest` + `experiment_id`。

**不能回答**：
- **历史 288 组**的逐窗配对（明细不存在，无法重建）；
- 连续账户收益（窗口仍相互独立，R12 未变）；
- 策略是否有效（本报告不含任何有效性判断）。

---

## 5. 验证与基线

```
pytest tests/ --ignore=tests/research -q
→ 1308 passed, 1 skipped, 6 xfailed, 0 failed   (62s)
```

- 本轮新增 **23 条**，零 failure，零 skip 变化。
- 口径对齐：A 线 15:35 实测基线为 **1285 passed**（`docs/ENGINEERING_BASELINE_REPORT.md`），
  1285 + 23 = **1308**，与本次实测完全吻合。更早文档里的 1212 是 #38~#42 契约测试尚未加入时的快照，
  不是同一时点，不可直接比。
- 未触碰 `backtest/engine.py`、未调任何策略参数、未改任何研究口径默认值。

---

## 6. 下一步（依赖关系）

1. **B-2 Gate 1 预登记**（可立即开工，与本轮无冲突）——预登记必须先落盘，且**不得据实验结果反改**。
2. **A-3 完整性测试**：现在具备条件了，可直接针对 `windows.csv` 读回层写
   （多配置不覆盖 / 失败窗口不静默 / 配对不错配 已有 23 条，A-3 应补「跨实验汇总可重读」与「数据版本可追溯」两条）。
3. **B-3 执行 Gate 1 主实验**：⏸ 仍被硬闸门挡住，需 A-1✅ A-2✅ B-1✅ B-2⏳ + **用户另行授权**。
4. 若要「存量 30 组也带逐窗明细」，需**重跑**（约 30 组 × 23~35 窗）。这属于「跑新回测」，
   需用户单独批准；且必须标明新结果是 `oos75-v3` 口径、与历史 v2 结果不可直接比较。
