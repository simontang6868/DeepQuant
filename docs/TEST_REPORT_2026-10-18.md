# DeepQuant 测试报告（2026-10-18）

> 本报告是「阶段 A–E」交付物 #6，由 **A 线（纯加法 / pytest 自证 / 不改 src）** 产出。
> 所有数字均为 **2026-10-18 夜间本机实测**，不是估算，也不是从别处抄来的。
> 报告只描述「测了什么、跑出什么、哪些是已知缺陷」，**不对策略是否有优势下任何结论**。

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-18 23:45 – 2026-10-19 00:10 GMT+8 |
| 代码根目录 | `D:\DeepQuant power by txm\DeepQuant-main` |
| Python | 3.13.14 |
| 覆盖率工具 | pytest-cov（coverage 7.x），`--cov=deepquant --cov-report=term-missing` |
| 分支 | `workbuddy/a-2026-10-18-final`（本地无 `.git`，走 GitHub API 推送） |
| 红线遵守 | 全程**未改 `src/` 一行**；未弱化、未跳过、未删除既有测试；未为了让测试通过而改实现 |

---

## 1. 测试命令（可复现）

### 1.1 解释器（⚠️ 有一处环境勘误）

任务书里写的 `C:\Users\77436\.workbuddy\binaries\python\envs\dq_demo\Scripts\python.exe`
**没有安装 pytest**（`No module named pytest`）。

真实可用的 venv 是：

```bash
PY="D:/DeepQuant power by txm/venv/Scripts/python.exe"
```

### 1.2 回归守卫命令（A 线每轮必跑）

```bash
cd "D:/DeepQuant power by txm/DeepQuant-main/backend"
PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" \
  -m pytest tests/ -q --ignore=tests/research
```

### 1.3 带覆盖率的命令（本报告数字来源）

```bash
cd "D:/DeepQuant power by txm/DeepQuant-main/backend"
PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" \
  -m pytest tests/ -q --ignore=tests/research \
  --cov=deepquant --cov-report=term-missing
```

### 1.4 其他常用命令

```bash
# 只跑在途的 research 子集（单独标注，不计入守卫）
PYTHONPATH=src $PY -m pytest tests/research -q

# 只跑需要联网的真实数据用例（默认跳过）
DEEPQUANT_LIVE=1 PYTHONPATH=src $PY -m pytest -m network -q

# 列出所有跳过 / xfail 及其原因
PYTHONPATH=src $PY -m pytest tests/ -q --ignore=tests/research -rsxX
```

### 1.5 启动后端必须显式设数据根

```bash
DEEPQUANT_DATA_ROOT="D:/DeepQuant power by txm/DeepQuant-main/data"
```

默认值锚定到 `<repo>/backend/data`，该目录 **不存在**；不显式设置会「能起来但一个数据集都找不到」。

---

## 2. 基线数字（对账）

### 2.1 本轮守卫结果

| 轮次 | 时间 | 结果 | 说明 |
|---|---|---|---|
| 守卫跑 #1 | 10-18 23:45 | **1718 passed / 1 skipped / 7 xfailed / 0 failed**（106.82s） | ✅ 绿。此时 `gate1_out/` 尚不存在 |
| 守卫跑 #2 | 10-18 23:58 | 1717 passed / 1 skipped / 7 xfailed / **1 failed** | ❌ 1 红，根因见 §5.2，**与 A 线改动无关** |

**结论：A 线本轮零代码改动（只新增文档），守卫跑 #1 全绿且数字高于基线；跑 #2 的唯一红灯由 B 线执行 Gate 1 产生的外部目录状态导致，非回归。**

### 2.2 与基线的对账

| 指标 | 上一轮基线（末次对账） | 本轮 | 判定 |
|---|---:|---:|---|
| passed | 1611 | **1718** | ✅ +107，不低于基线 |
| skipped | 1 | 1 | ✅ 持平 |
| xfailed | 6 | **7** | ✅ +1（新增固化一条 `generate_windows` 缺陷，见 §5.4） |
| **failed** | 0 | **0**（守卫跑 #1） | ✅ 零 failure |

> 说明：`xfailed` 增加是**刻意**的——每发现一个真实缺陷，就用 `xfail(strict=True)` 把「当前错误行为」钉成可执行断言，
> 缺陷一旦被修好，测试会 XPASS 并触发 strict 报错，强制提醒移除标记。**这不是放水，是把 bug 变成会喊的哨兵。**

### 2.3 全量集合（含 `tests/research`）

| 指标 | 值 |
|---|---:|
| 测试文件数 | **112** |
| 用例收集总数 | **1885** |
| 其中 `tests/research`（在途，守卫 `--ignore`） | 11 文件 / **159** 用例 |
| 守卫实际执行 | **1726** = 1718 passed + 1 skipped + 7 xfailed |

### 2.4 按测试目录分布

| 测试目录 | 用例数 | 文件数 |
|---|---:|---:|
| `tests/backtest` | 416 | 19 |
| `tests/performance` | 334 | 15 |
| `tests/api` | 241 | 17 |
| `tests/data` | 165 | 11 |
| `tests/research`（在途，守卫排除） | 159 | 11 |
| `tests/walkforward` | 156 | 7 |
| `tests/strategies` | 128 | 8 |
| `tests/persistence` | 100 | 8 |
| `tests/analysis` | 68 | 6 |
| `tests/` 根目录 | 71 | 6 |
| `tests/market` | 40 | 3 |
| `tests/tools` | 7 | 1 |
| **合计** | **1885** | **112** |

---

## 3. 分模块覆盖率

### 3.1 两个口径必须分开看

`src/deepquant/research/**` 是**研究脚本子包**（股债利差 A 股版 + 美股版），
其测试全部位于 `tests/research/`，被守卫 `--ignore`，因此**在守卫口径下覆盖率为 0%**。
把它算进总覆盖率会把数字拉低 20 多个百分点，属于**口径污染**。两个都列。

| 口径 | Stmts | Miss | Cover |
|---|---:|---:|---:|
| 全仓库（含 `research/**`） | 8304 | 2177 | **73.78%** |
| `research/**` 子包 | 1968 | 1968 | **0%**（被守卫排除，非缺陷） |
| **非 research（A 线守卫口径）** | **6336** | **209** | **96.70%** |

### 3.2 分包覆盖率（守卫口径，excl. `research/**`）

| 包 | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `(root) version.py` | 4 | 0 | 100.0% |
| `analysis` | 189 | 3 | 98.4% |
| `api` | 991 | 26 | 97.4% |
| `backtest` | 1192 | 50 | 95.8% |
| `data` | 395 | 5 | 98.7% |
| `market` | 103 | 0 | **100.0%** |
| `performance` | 1753 | 84 | 95.2% |
| `persistence` | 534 | 21 | 96.1% |
| `strategies` | 351 | 3 | 99.1% |
| `walkforward` | 824 | 17 | 97.9% |

**非 research 模块共 76 个，其中 45 个达到 100%。**

### 3.3 未达 100% 的模块（按 Miss 降序）

| 模块 | Stmts | Miss | Cover | 未覆盖行 |
|---|---:|---:|---:|---|
| `backtest/portfolio.py` | 418 | 33 | 92% | 112,146,206,238,360,368-371,382,392,396,410,413-414,423,476,522,538,540,542,570,624,644,668,671-675,714-715,751,759,853,876 |
| `performance/window_records.py` | 313 | 18 | 94% | 116,118,120,122,144,342,354,408,415,420,461-462,594,657-659,695,727 |
| `performance/window_records_integrity.py` | 209 | 18 | 91% | 105,111,115,124,131,157,203,231,239-240,276-278,374,393-396,429,454 |
| `persistence/store.py` | 246 | 18 | 93% | 239-240,287-288,309-310,367-368,387-388,419-420,498-499,551-552,555,563 |
| `walkforward/__init__.py` | 199 | 12 | 94% | 230,263-264,285,314-316,320,325-326,530,548 |
| `performance/benchmarks.py` | 150 | 10 | 93% | 144,148,189,191,198,233,318-319,403,405 |
| `performance/gate1_judgement.py` | 160 | 10 | 94% | 67,75,78,85,88,100,118,140,165,325 |
| `api/portfolio.py` | 112 | 9 | 92% | 95-96,102-103,199,255,340,381,383 |
| `backtest/engine.py` | 186 | 9 | 95% | 74,86,107,109,127,151,153,220,403 |
| `backtest/cross_section.py` | 167 | 8 | 95% | 77,115,118,126,161,171,173,296 |
| `api/routes/backtests.py` | 142 | 7 | 95% | 77-78,103-104,350,410-411 |
| `performance/serialize.py` | 130 | 7 | 95% | 75-76,81,87,120-121,549 |
| `performance/gate1_report.py` | 170 | 6 | 96% | 91,105,136,180,266,319 |
| `performance/portfolio_metrics.py` | 109 | 6 | 94% | 133,186,209,219,248,298 |
| `performance/metrics.py` | 165 | 5 | 97% | 161,166-167,177,208 |
| `api/ui.py` | 73 | 4 | 95% | 111-112,146-147 |
| `walkforward/sensitivity.py` | 317 | 4 | 99% | 194,257,315,345 |
| `data/loaders/akshare_loader.py` | 99 | 3 | 97% | 147,197,285 |
| `performance/curve.py` | 65 | 3 | 95% | 71,73,75 |
| `persistence/repro_checklist.py` | 147 | 3 | 98% | 106-107,349 |
| `analysis/ensemble_report.py` | 37 | 2 | 95% | 56-57 |
| `api/buildinfo.py` | 44 | 2 | 95% | 87-88 |
| `api/datasets.py` | 168 | 2 | 99% | 280,288 |
| `data/loaders/csv_loader.py` | 46 | 2 | 96% | 50-51 |
| `strategies/ensemble_voting.py` | 84 | 2 | 98% | 80,182 |
| `analysis/ensemble_eval.py` | 42 | 1 | 98% | 121 |
| `api/routes/datasets.py` | 32 | 1 | 97% | 52 |
| `api/schemas.py` | 225 | 1 | 99%* | 505 |
| `performance/window_distribution.py` | 64 | 1 | 98% | 103 |
| `strategies/ensemble.py` | 73 | 1 | 99% | 222 |
| `walkforward/design_audit.py` | 96 | 1 | 99% | 111 |

\* `api/schemas.py` 的 1 miss 为分支部分覆盖，coverage 四舍五入显示 100%，此处按实测 miss=1 如实列出。

### 3.4 本轮补齐的模块（A 线本批成果）

| 模块 | 补齐前 | 现在 | 手段 |
|---|---:|---:|---|
| `api/config.py` | 9 / 3 miss = 67% | **9 / 0 miss = 100%** | `tests/api/test_config_contracts.py`（10 条） |
| `api/datasets.py` | 92% | **99%** | `tests/api/test_datasets_contracts.py`（10 条） |
| `api/errors.py` | 93% | **100%** | `tests/api/test_errors_contracts.py`（4 条） |
| `api/registry.py` | 82% | **100%** | `tests/api/test_registry_contracts.py`（10 条） |
| `backtest/config.py` | 96% | **100%** | `tests/backtest/test_backtest_config_contracts.py`（19 条） |
| `market/rules.py` + `ashare.py` | — | **100%** | `tests/market/test_price_limit_rate.py`（16 条） |
| `backtest/fee_presets.py` | 0% | **100%** | `tests/backtest/...`（A9） |
| `data/validation/checks_extended.py` | 74% | **100%** | `tests/data/test_checks_extended.py`（A10） |
| `performance/json_encoding.py` | — | **100%** | `tests/performance/test_serialization_contracts.py`（64 条，A12） |
| `performance/reproducibility.py` | — | **100%** | 同上 |
| `walkforward/parameter_selection.py` | 91% | **100%** | `tests/walkforward/test_parameter_selection_coverage.py`（20 条） |

> `api/config.py` 即任务清单 A-5（A16#7，3 miss）的目标模块，**现已 100%，A-5 完成**。

---

## 4. 本轮新增测试清单

2026-10-18 当天新建 / 改写的测试文件共 **59 个、1008 条用例**。
下表按目录分组，逐文件列出用例数与落盘时间。

### `tests/api`（+145）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_registry_contracts.py` | 10 | 14:56 |
| `test_datasets_contracts.py` | 10 | 17:49 |
| `test_errors_contracts.py` | 4 | 18:37 |
| `test_config_contracts.py` | 10 | 19:00 |
| `test_backtest_config_contracts.py`* | 19 | 19:37 |
| `test_ui_single_port.py` | 16 | 20:18 |
| `test_start_launcher.py` | 29 | 20:35 |
| `test_dataset_display_names.py` | 11 | 20:55 |
| `test_portfolio_backtest.py` | 23 | 22:48 |
| `test_dataset_unavailable.py` | 11 | 22:52 |
| `test_datasets_api.py` | 11 | 22:55 |

\* 位于 `tests/backtest/`，此处一并列出便于对照。

### `tests/backtest`（+137）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_portfolio_engine_parity.py` | 3 | 12:04 |
| `test_portfolio_accounting.py` | 13 | 12:06 |
| `test_portfolio_end_to_end.py` | 4 | 12:06 |
| `test_models_contracts.py` | 33 | 13:37 |
| `test_cross_section_execution.py` | 6 | 16:39 |
| `test_cross_section.py` | 25 | 16:44 |
| `test_gate1_trigger_reachability.py` | 13 | 19:25 |
| `test_gate1_prereg_contract.py` | 8 | 19:29 |
| `test_history_audit.py` | 5 | 19:09 |
| `test_gate1_run_contract.py` | 59 | 18:35 |
| `test_portfolio_consistency_tolerance.py` | 5 | 22:51 |

### `tests/data`（+66）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_pipeline_contracts.py` | 2 | 19:20 |
| `test_instrument_name.py` | 17 | 20:54 |
| `test_import_pool.py` | 18 | 20:57 |
| `test_checks_extended.py` | 24 | 01:21 |
| `test_checks_extended_coverage.py` | 31 | 01:18 |

### `tests/performance`（+152）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_window_distribution.py` | 14 | 11:58 |
| `test_w1_completeness.py` | 7 | 16:17 |
| `test_serialization_contracts.py` | 64 | 02:00 |
| `test_reproducibility_coverage.py` | 39 | 01:44 |
| `test_portfolio_metrics.py` | 17 | 02:15 |
| `test_benchmarks.py` | 16 | 02:14 |
| `test_window_records.py` | 23 | 15:50 |
| `test_window_records_integrity.py` | 23 | 16:22 |
| `test_gate1_judgement.py` | 31 | 17:38 |
| `test_gate1_report.py` | 23 | 18:44 |

### `tests/persistence` / `tests/strategies` / `tests/walkforward` / `tests/market`（+112）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_store_contracts.py` | 14 | 12:14 |
| `test_r12_window_stats_defense.py` | 5 | 12:13 |
| `test_signals_contracts.py` | 11 | 15:12 |
| `test_store_edge_contracts.py` | 13 | 20:05 |
| `test_signal_store_contracts.py` | 4 | 22:50 |
| `test_parameter_selection_coverage.py` | 20 | 01:34 |
| `test_walkforward_contracts.py` | 19 | 20:49 |
| `test_price_limit_rate.py` | 16 | 02:15 |
| `test_portfolio_schedule.py` | 26 | 02:22 |

### `tests/` 根目录（+44）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_b20_script_contract.py` | 14 | 11:58 |
| `test_research_script_contract.py` | 30 | 02:15 |

### `tests/research`（+159，在途，守卫 `--ignore`）

| 文件 | 用例 | 时间 |
|---|---:|---|
| `test_cases.py` | 3 | 11:52 |
| `test_us_data.py` | 9 | 15:29 |
| `test_us_indicators_signals.py` | 14 | 15:29 |
| `test_us_experiment.py` | 6 | 15:30 |
| `test_us_stats.py` | 23 | 16:23 |
| `test_config_report.py` | 14 | 02:13 |
| `test_indicators.py` | 17 | 02:16 |
| `test_signals.py` | 18 | 02:16 |
| `test_data.py` | 20 | 02:18 |
| `test_experiment.py` | 9 | 02:18 |
| `test_daily_exec.py` | 26 | 12:31 |

> 合计 1008（若把 `test_backtest_config_contracts.py` 计入 `tests/backtest` 则分组边界略有出入，总量不变）。

---

## 5. 已知失败与原因

### 5.1 守卫范围内：0 failed（首次跑）

`--ignore=tests/research` 口径下 **1718 passed / 1 skipped / 7 xfailed / 0 failed**。
唯一 skipped 是 `tests/data/test_akshare_live.py`（`@pytest.mark.network` + `skipif(not DEEPQUANT_LIVE)`），
需联网抓真实行情，**默认跳过是有意为之**，不是漏测。

### 5.2 ⚠️ 环境序依赖失败（1 条，由 B 线 Gate 1 真跑触发）

| 项 | 内容 |
|---|---|
| 用例 | `tests/backtest/test_gate1_run_contract.py::test_main_dry_run_with_exclusion_runs_no_backtest` |
| 结果 | 守卫跑 #1 通过 → 守卫跑 #2 **失败**（单跑复现，确定性失败） |
| 断言 | `assert not (REPO_ROOT / "gate1_out").exists() or _gate1_out_untouched()` |
| 根因 | 该用例用「仓库根 `gate1_out/` 目录为空或不存在」来证明 *dry-run 没有落盘*。B 线于 **2026-10-18 23:58 起**执行 Gate 1 主实验，真的在 `gate1_out/` 下写了运行目录与 `INDEX.md`（该目录 23:45 尚不存在）。于是断定「目录存在且非空」→ 断言失败。 |
| 归属 | **不是 A 线回归**（A 线本轮零代码改动）；不是被测代码 `scripts/gate1_run.py` 的缺陷（dry-run 确实没写东西）。 |
| 处置 | **不改测试、不删 B 线产物**。该用例的弱点是对**仓库级全局目录状态**做断言，属于非 hermetic 测试；正确修法是让 `gate1_run.py` 支持可注入输出目录，再把断言改为 tmp_path 内的隔离目录——**这需要改 src/scripts，属 B 线与判断类工作，已登记为待办**。 |
| 复现 | `PYTHONPATH=src $PY -m pytest "tests/backtest/test_gate1_run_contract.py::test_main_dry_run_with_exclusion_runs_no_backtest" -q` |

### 5.3 ⚠️ `tests/research` 在途失败（1 条，单独标注，不计入守卫）

| 项 | 内容 |
|---|---|
| 用例 | `tests/research/test_config_report.py::test_render_report_is_deterministic` |
| 结果 | **158 passed / 1 failed / 1 warning**（单独跑 `tests/research`） |
| 断言 | `md1 = render_report(inp); md2 = render_report(inp); assert md1 == md2` |
| 根因 | `render_report` 把**挂钟时间戳**（生成时间，精确到秒）写进 Markdown 正文。两次渲染跨秒时字符串必然不同。失败 diff 显示**唯一差异就是秒数**（`23:58:33` vs `23:58:32`），正文其余 6310 字符完全相同。 |
| 性质 | **Flaky（间歇性）**：两次渲染落在同一秒就通过，跨秒就红。这不是 `render_report` 的业务缺陷（报告带生成时间是合理设计），而是**测试断言选错了颗粒度**——应比较去时间戳后的正文，或注入固定时钟。 |
| 归属 | `tests/research` + `src/deepquant/research/**` 属在途研究线（B 线研究范畴），A 线**只记录不修改**。 |
| 建议 | 修法二选一：把时间戳生成抽成可注入的 `now=` 参数，或断言前用正则抹掉「生成时间」行再比较。**属判断类，交回研究线。** |

### 5.4 已用 `xfail(strict=True)` 固化的已知缺陷（7 条，零 failure 但都是真 bug）

> 这些不是「跳过的测试」，而是**把当前错误行为写成会喊的断言**：缺陷一旦被修，测试会 XPASS 并因 `strict=True` 报错，强制提醒移除标记。

| # | 用例 | 缺陷 |
|---|---|---|
| 1 | `tests/data/test_checks_extended.py::test_price_jump_count_should_equal_true_violations` | `validate_daily_extended` 用**双方括号** `df_sorted[["close"]]` 取到 DataFrame，使 `pct_change[pct_change > threshold]` 走 **DataFrame 布尔掩码语义**（返回同形 NaN 填充的 DataFrame 而非过滤子集），`len(jumps)` 因此恒等于 **行数-1**。最小复现：100 行、3 处跳变、真实超阈值 5 次，却报 `count=99`。 |
| 2 | `tests/data/test_checks_extended.py::test_flat_prices_should_not_report_price_jump` | 同一缺陷的更严重后果：掩码 DataFrame 的 `len()` 恒 > 0，**即使零跳变也误报** price_jump。历史研究 JSON 里「1594 行 → 1593 个跳变」就是这么来的，那不是真实跳变数。 |
| 3 | `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_public_path_reports_actual_jump_count` | 同上，60 行、真实跳变 3 个，实际报 59。 |
| 4 | `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_below_threshold_reports_nothing` | 同一缺陷另一面：全部变化低于阈值时仍报警。 |
| 5 | `tests/data/test_checks_extended_coverage.py::TestPriceJumps::test_threshold_raising_keeps_check_silent` | 阈值 15% > 实际变化 10%，理应静默，仍报警。 |
| 6 | `tests/walkforward/test_parameter_selection_coverage.py::TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result` | `run_walkforward_window` 的「OOS 策略构造失败」分支在 `except` 里访问**尚未绑定**的局部变量 `strategy`（源码 447-448 行），函数抛 `UnboundLocalError` 崩溃，而非按契约返回 `oos_success=False`。违反模块自述原则 4「失败显式表达」。 |
| 7 | `tests/walkforward/test_walkforward_contracts.py::test_generate_windows_end_date_respects_boundary` | `generate_windows` 的 end_date 裁剪在 `walkforward/__init__.py:222` 用 `(~mask)` 而非 `mask` 求 `last_true_pos`，导致 `end_idx` 恒等于 `len(index)`，**`end_date` 被完全忽略**。正确行为应为「末窗 `oos_end ≤ end_date`」。 |

> 以上 7 条**只测不修**（A 线硬约束：不改 `src`）。修 bug 属判断类工作，需 B 线/人工决策。
> 风险提示：#1~#5 会让**任何**用到 `price_jump` 检查的历史结论里的「跳变数」失真——历史报告若引用过该数字，需重新核。

### 5.5 未决的测试隔离问题（历史，已复核为假象）

`test_instrument_name` 相关的「全序失败 / 分目录通过」现象已于 2026-10-18 复核为**合并中途脏磁盘的瞬态假象**，
全量复测 1293 passed / 0 failed，**不存在持久隔离缺陷**。按硬约束「不得造假测试」，未为其开 xfail。

---

## 6. 结论

1. **软件功能层面**：守卫口径 **1718 条用例零失败**，非 research 代码**语句覆盖率 96.70%**（6336/209），
   45 个模块 100%。工程侧可判定为「可复现、可回归」。
2. **这只能证明「代码按它的写法跑对了」，不能证明「回测可信」或「策略有优势」。**
   回测可信度与策略有效性的判断在 B 线的 Gate 1 报告里，两者**不得互相替代**。
3. 已知缺陷 7 条已固化为 xfail（§5.4），另有 2 条真实红灯（§5.2 环境序依赖、§5.3 research 在途 flaky），
   均已写明根因与修复方向，**未做任何掩盖**。
4. 覆盖率缺口清单见 `docs/COVERAGE_GAPS.md`（含 10-17 原始快照 + 10-18 收官刷新）。
   已知问题（软件侧）见 `docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md`。

---

*本报告由 A 线自动化产出；所有数字可复现，复现命令见 §1。*
