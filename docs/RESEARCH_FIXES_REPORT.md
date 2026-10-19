# 研究装置修复报告（B 线）

| 项 | 内容 |
|---|---|
| 日期 | 2026-10-19 |
| 归属 | **B 线（`backend/src/**` + `scripts/**`）** |
| 范围 | Gate 1 结论所依赖的 7 条装置缺陷（2×P0、2×P1、D1、D2、#9） |
| 不做 | 不改 `backend/tests/**` 一行；不改预登记；不覆盖既有 `gate1_out/` 两个 run 目录；不 commit / push / 开 PR |
| 证据目录 | `D:\DeepQuant power by txm\fix_evidence\`（`before.txt` / `after.txt` / `before_pytest_runxfail.log` / `after_pytest_runxfail.log`） |

> 本报告只写「研究与工程口径」的修复。软件功能缺陷清单仍由 A 线的 `docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md` 负责，两者不交叉。

---

## 0. 一页结论

7 条缺陷**全部修好并经实测验证**，其中 2 条 P0 与 2 条 P1 有 `xfail(strict=True)` 用例固化：

| # | 缺陷 | 修复文件 | 修复前（实测） | 修复后（实测） |
|---|---|---|---|---|
| P0-1 | `generate_windows` 忽略 `end_date` | `walkforward/__init__.py` | `end_date=2021-02-24` 与 `None` 同样返回 **44** 个窗口，末窗 `oos_end=2023-10-31`，**35 个窗口越界** | 返回 **9** 个窗口，末窗 `oos_end=2021-02-23`，**越界 0** |
| P0-2 | `price_jump` 计数＝行数（且零跳变也报警） | `data/validation/checks_extended.py` | 传 DataFrame 时 100 行平盘 → `count=99`；公开路径恒报警 | 传 Series：平盘 `count=0`、3 处真跳变 `count=3`；传 DataFrame 直接 `TypeError` |
| P1-1 | OOS 策略构造失败分支崩 `UnboundLocalError` | `walkforward/parameter_selection.py` | `UnboundLocalError: cannot access local variable 'strategy'` | 干净返回 `oos_success=False`，`oos_error="OOS 策略构造失败: RuntimeError: …"` |
| P1-2 | `_num()` 用 `v is not bool` 挡不住布尔 | `api/routes/backtests.py` | `True` 通过校验 → 被 Pydantic 转成 `1.0`（胜率凭空 100%） | `True/False` 一律返回 `None`；`1`、`0.5` 正常保留 |
| D1 | 排除跑的「对照组」是同一份数据重跑 | `scripts/gate1_run.py` | `monthly_paired.csv` 与 `monthly_paired_excluded.csv` **sha256 完全相同** | 对照组回到**全池 50 只**独立重跑；两个 CSV 不同；对照结果与原始全池主实验**逐字节一致** |
| D2 | 「累计收益」列是全程值，不随窗口截断 | `scripts/gate1_run.py` + `performance/gate1_report.py` | S12（只取 OOS 前半 24 个月）的累计与主实验**同值** `+334.69% / +357.06%` | 拆成 `*_total_return_eval`（窗口内）与 `*_total_return_full`（全程）；S12 现为 `eval=+8.60%`，`full=+334.69%` |
| #9 | `make_run_id` / `pool_fingerprint` 只哈希形状，不哈希价格 | `scripts/gate1_run.py` | 形状、日期相同而价格不同 → **同一个 run_id**（输出目录撞车 / 误判「已存在」） | 改为**逐值内容哈希**（`pd.util.hash_pandas_object`）；价格变动即换 id，相同输入仍碰撞（退化仍可检测） |

**9 条固化用例的验证方式**：用 `pytest --runxfail` 把 xfail 当普通用例跑（绕过标记、看真实行为）。

- 修复前：`8 failed, 115 passed`（B 线 8 条）+ A 线 01:55 新增的第 9 条为 `xfail`
- 修复后：`123 passed`（前 5 个文件）+ `4 passed`（`tests/research_fixes/test_ap1_control_input_separation.py`，含第 9 条）

（`--runxfail` 不影响仓库里的标记；正式套件里这 9 条现在会 `XPASS(strict)` 报错，**这是设计好的转正信号，须由 A 线删除标记**，见 §4。）

**闭环状态（2026-10-19 02:46 复核）**：A 线已在 A-P1 摘除全部 9 条标记并新增 26 条回归守卫，
B 线复核全量 = **1780 passed / 1 skipped / 0 failed / 0 xfailed**。
此外，`price_jump` 缺陷修完后，历史文档里引用过的跳变数已用真实数据**重算核对**
（50 只池：修复前会报 88,346 次 → 真实 **280 次**，30/50 只是纯误报），见 §2.1。

---

## 1. P0-1 `generate_windows` 忽略 `end_date`

**位置**：`backend/src/deepquant/walkforward/__init__.py`（原 216–225 行，`generate_windows`）

**症状**：给 `WalkForwardConfig.end_date` 设边界后，窗口数量与末窗结束日**完全不变**——边界是静默失效的，不报错、不警告。

**根因**：求「最后一个 True 的位置」时写成了

```python
last_true_pos = len(index) - 1 - int((~mask)[::-1].argmax())
```

`~mask` 在尾部（`index > end_date` 的部分）全为 True，反转后第一位就是 True，`argmax()` 恒为 0 ⇒ `last_true_pos` 恒等于 `len(index)-1` ⇒ `end_idx` 恒为全长。上一行 `end_idx = int(mask.sum())` 是死代码（立刻被覆盖），它恰好是正确思路的一半（`mask.sum()` 只在「True 连续在头部」时可用）。

**修复**：

```python
last_true_pos = len(index) - 1 - int(mask[::-1].argmax())
end_idx = last_true_pos + 1
```

并删掉死代码行、在注释里写清「为什么不能用 `~mask`」。

**实测**（`fix_evidence/before.txt` vs `after.txt`，同一脚本）：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 1000 个交易日，`end_date = 2021-02-24` | 44 个窗口，末窗 `2023-10-31` | 9 个窗口，末窗 `2021-02-23` |
| `oos_end > end_date` 的窗口数 | **35** | **0** |

**影响面**：仓库内三个调用点（`parameter_selection.run_walkforward_v2`、`walkforward.run_walkforward`、`scripts/b20_window_benchmark.py`）**都没有传 `end_date`**（默认 `None`），因此历史 W1 / B20 结果不受影响。该缺陷属「潜伏型」：一旦有人用 `end_date` 裁边界，边界会被静默忽略——**这类静默失效比报错危险**，因为它产出的是「看起来正常」的结果。

**固化用例**：`tests/walkforward/test_walkforward_contracts.py::test_generate_windows_end_date_respects_boundary`（现 XPASS）。

---

## 2. P0-2 `price_jump` 计数失真

**位置**：`backend/src/deepquant/data/validation/checks_extended.py`（`validate_daily_extended` 第 73 行 + `_check_price_jumps`）

**症状**：一是「跳变个数」恒等于行数左右（100 行平盘报 99 个跳变）；二是**零跳变也报警**。

**根因**：`df_sorted[["close"]]`（双层方括号）取到的是**单列 DataFrame**，不是 Series。`pct_change[pct_change > threshold]` 在 DataFrame 上走的是布尔掩码语义：返回**同形**对象、不满足位置填 NaN，`len()` 恒等于行数（`dropna()` 后为行数−1）。于是「跳变数」退化成「有几行数据」。

**修复**：

1. 取列改单层方括号：`close_series = df_sorted["close"].astype("float64").dropna()`；
2. `_check_price_jumps` 入口加防御：非 `pd.Series` 直接 `raise TypeError`，并在消息里写明「请用 `df['close']`」。防御比注释可靠——传错类型不会再静默产出错误计数。

**实测**：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 100 行平盘 | 公开路径恒报 `price_jump`，`count=99` | 不报警 |
| 100 行、3 处真实跳变（+20%、−25%、+22.2%） | `count=99`（与真实值无关） | `count=3` |
| 直接传 DataFrame | 静默返回 `count=99` | `TypeError`（显式拒绝） |

**影响面**：`validate_daily_extended` 在 `backend/src` 与 `scripts/` 内**没有调用点**（属对外校验接口 / 测试直调）。因此 Gate 1 的收益计算链完全不受影响（`scripts/gate1_run.py` 对 `price_jump` / `walkforward` 的引用数为 0）。受影响的只有**历史校验 JSON 里的 `price_jump` 计数**——那些数字不是真实跳变数，引用过它的分析需重算。按 A-P2 的 `PRICE_JUMP_IMPACT_AUDIT.md` 口径归为「需重算」。

**固化用例**（5 条，A 线已全部摘除标记转正，断言一条未改）：
`tests/data/test_checks_extended.py::test_price_jump_count_should_equal_true_violations`、
`…::test_flat_prices_should_not_report_price_jump`、
`tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_public_path_reports_actual_jump_count`、
`…::test_below_threshold_reports_nothing`、
`…::test_threshold_raising_keeps_check_silent`。

### 2.1 修复后重算：历史引用过的「跳变数」到底成立多少

缺陷修好只说明**以后的计数是对的**；历史文档里已经写下的数字是否成立，必须用真实数据重算才算闭环。
`scripts/price_jump_recount.py`（只读，不跑回测）对 50 只池逐只跑修复后的公开路径，
并用**独立实现**（pandas 原生 `pct_change` + 阈值比较，不经 `checks_extended`）复算一遍：
**50/50 逐只相等**，说明修复本身没有引入新口径。

| 指标 | 值 |
|---|---|
| 池规模 | 50 只 / 88,396 行 |
| 修复后真实跳变合计 | **280 次** |
| 修复前会报 | **88,346 次**（= Σ(行数−1)，与行数几乎相等） |
| 有真实跳变的标的 | **20 / 50** |
| **零跳变却被报警的标的** | **30 / 50**（纯误报） |

280 次再按「能否是真实行情」三分（A 股单日涨跌幅上限 20%，留 1pp 容差）：

| 类别 | 判据 | 次数 | 能否当作真实跳变 |
|---|---|---|---|
| 复权噪声 | 相邻两日 close 至少一端 ≤ 0 | **55** | 不能：该日价格本身失真 |
| 超涨跌幅上限 | 两端均 > 0 但变动 > 21% | **95** | 不能：纯正价格序列不可能出现 |
| 疑似真实 | 两端均 > 0 且 ≤ 21% | **130** | **仅「可能」**，逐条仍需人工核 |

第三档给的是筛查结果，不是结论。减法复权会把价格压到 1 元以下，此时 19% 的「涨跌停」
只是分红利差（0.8 元跳 0.15 元）——`within_limit` 那批跳变的**最低端点价**因此一并落盘：
`600809.SH` **0.23 元**、`601899.SH` 0.71 元、`000568.SZ` 1.30 元、`601872.SH` 1.58 元、
`000807.SZ` 1.92 元、`600415.SH` 2.16 元。这几只的「疑似真实」不能直接采信。

**历史引用逐条核对**（结论：一条成立、两条无法核对）：

| 文档 | 原文 | 重算 | 裁决 |
|---|---|---|---|
| `docs/EXPANSION_2026-10-17.md:123` | 泸州老窖 `count` 报 1942、真实 **41** | `000568.SZ` = **41** | ✅ **成立**。1942 恰为 1943 行 − 1，与缺陷机制吻合；「41」当时已独立算对，该文档无需改写 |
| `docs/B15_8Y_30GROUPS_2026-10-18.md:44` | 茅台 `count` = 行数−1（1,942/1,943） | `600519.SH` **不在本池** | ⚠️ **无法核对**（该股属历史 30 组实验宇宙；B15 原文已声明「不将其作为跳变频率使用」，故不影响其结论） |
| `docs/B15_8Y_30GROUPS_2026-10-18.md:42` | 宁德 1,593/1,594 行跳变、最大 3,683.33% | `300750.SZ` **不在本池** | ⚠️ **无法核对**（同上，且原文用它证明「被拦是正确结果」，该论证不依赖跳变计数） |

**一处容易误改的口径**（本轮实测排掉）：历史审计写的「132 行非正价（600809 131 + 000568 1）」
指的是**四价中任一 ≤ 0**（预登记 P2 的过滤口径）；若按 `close` 单列数，则是 **115 + 0**。
两个数都对，差在列的选择上。**不要**把 115 当成对 131 的「纠错」——那是换口径，不是改错数。

> 证据：`probe_out/price_jump_recount.json`（逐只 `reported_count` / `independent_count` /
> 三分类明细 / 最低端点价 / 历史引用裁决）。A 线的 `PRICE_JUMP_IMPACT_AUDIT.md` 可直接引用本表，
> 不必重跑。

---

## 3. P1 两条

### 3.1 `run_walkforward_window` 的失败分支自己崩掉

**位置**：`walkforward/parameter_selection.py`（OOS 策略构造的 `try/except`）

**根因**：`strategy = strategy_factory(selected)` 在 `try` 内赋值；工厂抛异常时 `strategy` 未绑定，而 `except` 分支里要读 `strategy.name`/`strategy.version` ⇒ `UnboundLocalError`。**「显式失败」被降级成崩溃**，调用方拿不到 `oos_success=False`。

**修复**：`except` 分支首行 `strategy = None`（随后 `hasattr(None, "name")` 为 False，字段落空串，不伪装成功）。

**实测**：修复前 `RAISED UnboundLocalError`；修复后 `oos_success=False`，`oos_error="OOS 策略构造失败: RuntimeError: factory boom at OOS"`。

**固化用例**：`tests/walkforward/test_parameter_selection_coverage.py::TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result`（现 XPASS）。

### 3.2 `_num()` 挡不住布尔

**位置**：`api/routes/backtests.py`（`_extract_*` 内部的 `_num`）

**根因**：`isinstance(v, (int, float)) and v is not bool` —— 后半个条件是**拿值与类型做身份比较**，`True is bool` 恒为 False，于是条件恒真，bool 从未被挡。`True` 一路通过并被 Pydantic 转成 `1.0`，比较接口可能显示「100% 胜率」。

**修复**：`not isinstance(v, bool)`。

**实测**：修复后 `True/False → None`，`1 → 1`，`0.5 → 0.5`。

**固化用例**：`tests/api/test_backtests_routes_contracts.py::test_bool_metrics_are_rejected_not_coerced_to_one`（现 XPASS）。

---

### 3.3 缺陷 #9：`make_run_id` / `pool_fingerprint` 不是内容哈希

**位置**：`scripts/gate1_run.py`（`make_run_id`、`pool_fingerprint`）

**症状**：两份**形状相同、日期相同、价格不同**的数据集，`make_run_id` 得到**同一个 id**。
而 `run_id` 同时是输出目录名与 `ensure_no_overwrite` 的判据 —— 后果有二：
不同数据的产物被归到同一目录；或者第二次运行被误判为「已存在」而直接拒绝。

**根因**：实现只哈希 `标的代码 + 行数 + 首末日期`，**完全没有覆盖价格数值**，
docstring 却称「内容哈希」。这是最典型的一类假防线：看起来有指纹，实际只区分了形状。

**修复**：新增 `_frame_digest()`，对每只标的做「列名 + 形状 + `pd.util.hash_pandas_object` 逐值哈希」，
`make_run_id` 与 `pool_fingerprint` 都改用它。

**实测**：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 同形状、同日期、价格 +1.0 | 同一 `run_id` | **不同** `run_id` |
| 完全相同的输入 | 同一 `run_id` | 同一 `run_id`（退化仍可检测） |
| 排除一只标的 | 不同 | 不同 |

**副作用（必须知道）**：`run_id` 算法变了，**同一份数据现在会算出与旧目录不同的 id**。
既有 `gate1_out/032a11f591553e25` 与 `a027865f26ea237a` **保持原名、不迁移、不重算**（R5 保留原始记录）。
因此「用同一份数据重跑会得到同名目录」这条只对**新算法之后**的运行成立；
引用旧目录时不要用新脚本算出的 id 去核对。

**固化用例**：`tests/research_fixes/test_ap1_control_input_separation.py::test_fingerprint_is_sensitive_to_content_not_just_symbol_set`（A 线 01:55 新发现，现已修复、待删标记）。

---

## 4. D1：对照组退化（Gate 1 装置）

**位置**：`scripts/gate1_run.py`（`main()` 的对照段）

**症状**：`--exclude-symbols` 跑的 `monthly_paired.csv` 与 `monthly_paired_excluded.csv` **sha256 完全相同**（`74be84da8d315a8e…`）。也就是说，`summary.json` 里的 `control_excluded` 是「在同一份已剔除数据上重跑同一配置」，不是「含 vs 排除」的对照。

**根因**：`main()` 先 `prices = apply_exclusion(prices, excluded)`，之后对照段又用 `run_one(MAIN_SPEC, prices, …)` —— 传的是已被剔除的 `prices`。

**修复**：

1. 保留全池原件 `prices_full`，对照组改为 `run_one(MAIN_SPEC, prices_full, …)`；
2. 新增 `pool_fingerprint(prices)`（标的集合 + 每只行数 + 首末日期 → sha256 前 16 位），把 `main_pool` / `control_pool` 的标的数与指纹写进 `summary.json` 与 `manifest.json`；
3. **退化守卫**：若两个指纹相同，直接 `RuntimeError`，不允许拿退化解当对照；
4. 报告层（`performance/gate1_report.py`）在 §4.2 增加「池内标的数 / 输入指纹」两行，让读者可以自己核对这不是同一份数据。

**实测**（`--out-root D:\DeepQuant power by txm\gate1_fix_verify`，不碰既有 run 目录）：

| 项 | 值 |
|---|---|
| 主实验池 | 49 只，指纹 `f9e6f8679612bd8a` |
| 对照池 | 50 只，指纹 `115107cf7ff61d84` |
| 主实验 mean(d) | −0.003284（与原 `a027865f26ea237a` 主实验**逐字节一致**） |
| 对照 mean(d) | −0.003961（与原全池主实验 `032a11f591553e25` **逐字节一致**） |
| `monthly_paired.csv` vs `monthly_paired_excluded.csv` | **不再相同**（修复前相同） |
| 对照的 `monthly_paired_excluded.csv` vs 原始全池 `monthly_paired.csv` | **sha256 相同** ✅ 真对照 |

**对 Gate 1 结论的影响**：**无**。原始判定用的是跨跑比对（全池 run vs 排除 run），不是 `control_excluded` 字段；两版 `mean(d)` 同号（−0.003961 / −0.003284），§10.2 第三条未触发，结论仍为 `failure`。修复后重跑得到的正是同号结果，且数值与原两次运行逐字节一致——**说明修复改变了装置的可解释性，没有改变数据与计算**。

### 4.1 转正信号（✅ 已由 A 线闭环，2026-10-19 02:0x）

修复后，以下 9 条 `xfail(strict=True)` 会 **XPASS**，从而让正式套件报错。这是设计好的「缺陷已修好，请移除标记」信号，**B 线不改测试**（红线：不为让测试变绿去改测试）。

1. `tests/walkforward/test_walkforward_contracts.py::test_generate_windows_end_date_respects_boundary`
2. `tests/data/test_checks_extended.py::test_price_jump_count_should_equal_true_violations`
3. `tests/data/test_checks_extended.py::test_flat_prices_should_not_report_price_jump`
4. `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_public_path_reports_actual_jump_count`
5. `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_below_threshold_reports_nothing`
6. `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_threshold_raising_keeps_check_silent`
7. `tests/walkforward/test_parameter_selection_coverage.py::TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result`
8. `tests/api/test_backtests_routes_contracts.py::test_bool_metrics_are_rejected_not_coerced_to_one`
9. `tests/research_fixes/test_ap1_control_input_separation.py::test_fingerprint_is_sensitive_to_content_not_just_symbol_set`（A 线新增，B 线已修）

**闭环结果**（A-P1 实际执行）：9 条标记全部摘除、**断言一条未改**，另新增 26 条回归守卫
（`tests/research_fixes/`：`generate_windows` 9 / `price_jump` 9 / D1 4 / D2 4）。
B 线 02:46 复核全量：**1780 passed / 1 skipped / 0 failed / 0 xfailed**
——`xfailed` 已归零，上一轮那 7 个 `fixture 'gate1_run' not found` 的 error 也随之消失
（根因是 `tests/research_fixes/conftest.py` 与 `tests/conftest.py` 同名遮蔽，A 线改名为普通模块后解决）。

> 转正方式确立为：**只删 `@pytest.mark.xfail(...)` 装饰器与相应 import（如无其它用途），不改断言**。
> 断言是契约，删标记只是承认契约已满足。B 线全程未碰 `backend/tests/**`。

---

## 5. D2：累计收益口径

**位置**：`scripts/gate1_run.py`（`write_outputs` 的 `sensitivity.csv`）+ `performance/gate1_report.py`（§4.1 表头）

**症状**：`strategy_total_return` / `benchmark_total_return` 取的是 `r.strategy.cumulative_return`，即**全程**（`BACKTEST_START` ~ `BACKTEST_END`，2017-02 ~ 2024-12）累计，不随 `spec.eval_end` 截断。S12（`eval_window=OOS前半`，只 24 个月）那一行却显示与主实验完全相同的 `+334.69% / +357.06%` —— 看上去像「24 个月赚了 334%」。

**修复**：

- `RunResult` 增加 `strategy_total_return_eval` / `benchmark_total_return_eval`，由该窗口的月度收益复利得到：`(1+r).prod() - 1`，其中 `r` 就是算 `mean(d)` 用的同一串月收益（口径自洽）；
- `sensitivity.csv` 列名改为 4 列：`strategy_total_return_full` / `benchmark_total_return_full` / `strategy_total_return_eval` / `benchmark_total_return_eval`；
- 报告 §4.1 表头改为「策略累计(窗口) / 基准累计(窗口)」；**旧结果目录**（只有旧列）自动降级为「(全程\*)」并加脚注说明「不能读成窗口内累计」，不伪造新口径。

**实测**（验证跑 `gate1_fix_verify`）：

| 组 | `eval`（窗口内） | `full`（全程） | 修复前显示 |
|---|---|---|---|
| main（49 只） | +41.16% | +334.69% | +334.69%（被读成窗口内） |
| S1 | +50.60% | +586.41% | +586.41% |
| S12（24 个月） | **+8.60%** | +334.69% | +334.69% |

主实验（50 只）的窗口内累计：策略 **+35.20%**、基准 **+66.36%**；同口径全程为 `+331.43% / +357.89%`。**同一份数据、同一策略，两个口径差近 10 倍** —— 这就是为什么必须拆字段，而不是在正文里加一句提醒。

**对 Gate 1 结论的影响**：**无**。判定用的是 `mean(d)` / `t` / 敏感性正组数，三者都按窗口计算，从未依赖这两列。

---

## 6. 验证汇总

**（1）单元层**：`pytest --runxfail` 于 5 个受影响文件

- 修复前：`8 failed, 115 passed`
- 修复后：`123 passed`

**（2）契约层**：`tests/performance/test_gate1_report.py` + `tests/backtest/test_gate1_run_contract.py`

- `82 passed`（证明新旧两种 CSV 都能渲染、`write_outputs` 的老调用方式仍兼容）

**（3）装置层**：真实跑一遍 `--exclude-symbols 600809.SH` 到**新输出目录**（`gate1_fix_verify/`，未覆盖既有结果）

- 主实验与原始 run **逐字节一致**（可复现性再次成立）
- 对照回到全池，且与原始全池 run **逐字节一致**
- 两个 CSV 不再相同；`inputs` 指纹可核对
- 判定未变（`failure`），`insufficient_flags` 为空

**（4）全量回归**：`pytest tests/ -q --ignore=tests/research`

本轮三次实测：

| 时点 | 结果 |
|---|---|
| 修完 6 条、A 线刚落下 `research_fixes/` 时 | `1777 passed / 1 skipped / 3 failed`（2 条 `tests/api` 导入冲突 + 1 条缺陷 #9 的 XPASS） |
| 修完 #9 之后 | `1772 passed / 1 skipped / 1 xfailed / 7 errors` |
| **A 线转正 + 新增 26 条之后（02:46 复核）** | **`1780 passed / 1 skipped / 0 failed / 0 xfailed`**（112.39 s） |

**7 个 error 已随 A 线改动消失**，根因如上一版所述（`tests/research_fixes/conftest.py` 与
`tests/conftest.py` 同名，pytest prepend 模式下互相遮蔽 → 整目录跑丢 fixture，单跑该目录正常）。
A 线把辅助代码改名为普通模块 `_ap1_support.py` 后彻底解决 —— 与本报告 02:05 版的判断一致。

> 结论：**B 线本轮所有源码改动均在 0 failed / 0 xfailed 的套件下成立**，
> 不存在「靠 xfail 掩盖」或「改了测试才变绿」的情况。

**B 线源码侧的可判定证据**：受影响 6 个文件的定向测试全绿（`123 passed` + `4 passed`）、
Gate 1 契约与报告测试 `82 passed`、真实跑一遍装置验证通过（见下）。

---

## 7. 未做的事（明确边界）

1. **没有改任何测试**。9 条标记的移除由 A 线在 A-P1 完成（断言未改），B 线全程未碰 `backend/tests/**`。
2. **没有覆盖 `gate1_out/032a11f591553e25` 与 `gate1_out/a027865f26ea237a`**。旧结果按 R5 原样保留；它们用的是旧列名与旧对照组语义，**引用时必须带版本说明**。
3. **没有重跑新实验、没有调任何参数**。验证跑用的是同一份预登记配置、同一份数据；`--out-root` 指到仓库外，仅用于装置验证，**不作为新的实验结论**。
4. **没有重新生成 `docs/GATE1_EXPERIMENT_REPORT.md`**。它由修复前的渲染器产出、sha256 `f0f1f962…`，其中敏感性表的「策略累计」列是**全程值**（D2 的旧口径）。选择不重生成的理由：`gate1_out/` 里同一 run 的报告副本受 R5 保护不能覆盖，若只重生成 docs 副本，就会出现**同一 run 的两份「机械生成」报告内容不同**——那比标注一个已知口径更糟。正确读法见 §5 与本报告 §5 的实测表：main 窗口内 `+35.20%`、全程 `+331.43%`。
5. **没有处理 D3（BH q 值全相同）**——那是算法正确性质，不是缺陷。
6. **没有处理 D4（测试与输出目录耦合）**——A 线 00:26 已修（改为运行前后快照对比），B 线未碰。
7. **`price_jump` 重算只覆盖 50 只池**。茅台 / 宁德不在该池内，其历史引用**无法核对**，本报告不对它们下成立或不成立的判断。

---

## 8. 引用方式

引用本报告任何数字，须同时给出：

- 报告版本：2026-10-19（B 线装置修复轮）
- 证据目录：`D:\DeepQuant power by txm\fix_evidence\`
- 验证跑目录：`D:\DeepQuant power by txm\gate1_fix_verify\a027865f26ea237a`（仓库外，装置验证用）
- 原始结果目录：`gate1_out/032a11f591553e25`（全池）、`gate1_out/a027865f26ea237a`（排除）
