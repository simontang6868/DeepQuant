# DeepQuant 已知问题清单 —— 软件功能侧（2026-10-18）

> **本文档只覆盖 A 线这一半：软件功能 / 测试 / 环境 / 已知缺陷 / 下一步建议。**
> 研究口径、回测可信度、策略有效性的问题在 **B 线**的 `docs/KNOWN_ISSUES_RESEARCH_2026-10-18.md`，
> **两份分开写，由协调者汇总**。本文不对「策略是否有统计优势」下任何结论。
>
> 生成时间：2026-10-18 23:45 – 2026-10-19 00:15 GMT+8 ｜ 产出方：A 线（纯加法 / pytest 自证 / 不改 src）
> 事实依据：`docs/TEST_REPORT_2026-10-18.md`、`docs/COVERAGE_GAPS.md` §7、本机实测。

---

## 1. 一句话结论

**软件工程层面**：1718 条用例零失败，非 research 代码语句覆盖率 **96.70%**，45 个模块 100%，可判定为「可复现、可回归」。

**但这只证明「代码按它自己的写法跑对了」，不证明「回测可信」，更不能替代「策略有优势」。**
这四层结论必须分开，不得互相替代。

---

## 2. 环境基线（含一处任务书勘误）

### 2.1 ⚠️ venv 勘误（会让人白跑一轮）

| 项 | 说明 |
|---|---|
| 任务书里写的 | `C:\Users\77436\.workbuddy\binaries\python\envs\dq_demo\Scripts\python.exe` |
| 实际情况 | 该 venv **没有安装 pytest**（`No module named pytest`），只装了 fastapi/numpy/pandas 等运行时依赖 |
| **正确的解释器** | `D:\DeepQuant power by txm\venv\Scripts\python.exe`（Python 3.13.14） |

### 2.2 ⚠️ 数据根必须显式设置

`api/config.py` 的默认数据根锚定到 `<repo>/backend/data`，**该目录在仓库里不存在**（真实数据集在 `<repo>/data`）。
不显式设置的表现是「后端能起来、健康检查 200，但一个数据集都找不到」——属于**看起来正常但不对**。

```bash
DEEPQUANT_DATA_ROOT="D:/DeepQuant power by txm/DeepQuant-main/data"
```

该行为已被 10 条契约测试钉死（`tests/api/test_config_contracts.py`），`api/config.py` 现为 **100% 覆盖**。

### 2.3 其他环境事实

| 项 | 值 |
|---|---|
| 操作系统 | Windows（win32） |
| 本地仓库 | `D:\DeepQuant power by txm\DeepQuant-main`，**无 `.git`**，所有推送走 GitHub API |
| 真正的 git 仓库 | `D:\DeepQuant`（branch `main`），**内容已与工作副本不同源**，不要拿它做合并/对比 |
| 前端 | Node + Vite；`npm run build` 通过（672 modules，dist JS ≈ 629 kB） |
| 覆盖率工具 | pytest-cov（coverage 7.x） |

---

## 3. 测试现状

| 指标 | 值 |
|---|---:|
| 测试文件 | 112 |
| 用例收集总数 | 1885 |
| 守卫口径（`--ignore=tests/research`） | **1718 passed / 1 skipped / 7 xfailed / 0 failed**（106.82s） |
| `tests/research`（在途，单独跑） | 158 passed / **1 failed** / 1 warning |
| 非 research 语句覆盖率 | 6336 stmts / 209 miss = **96.70%** |
| 100% 覆盖的模块 | 45 / 76 |

对账：基线 1611 passed → 本轮 **1718 passed**（+107），**不低于基线，零 failure**。
`xfailed` 6 → 7 是刻意的（新增固化一条 `generate_windows` 缺陷，见 §5.1 #7）。

---

## 4. 软件功能：已验证 / 未验证

### 4.1 已实测通过

- 后端可启动、`/health` 与 `/api/health` 均 200；`/api` 前缀返回 JSON 404（不再误返回 index.html）。
- 前端 `npm run build` 通过并可启动（Vite dev 5173 / 单端口模式 8123）。
- `/api` 代理联通；5 个已登记数据集可被发现。
- 真实历史数据回测可完成并返回 `reproducibility` 指纹；**两次独立进程结果 sha256 全同**。
- 数据校验、序列化、可复现性、窗口记录完整性等契约均有专项测试。

> 单端口可用版（双击 `start_deepquant.bat`）的实测细节见 B 线 `docs/PRODUCT_READY_2026-10-18.md`
> 与 `docs/SOFTWARE_AUDIT_2026-10-18.md`，此处不重复。

### 4.2 未验证（如实声明）

| 项 | 状态 |
|---|---|
| 券商实盘下单 | **从未连接**，也不打算连 |
| 交易成本真实性 | `backtest/fee_presets.py` 是**模拟占位费率**（万 3），已用断言钉死 `verified_fields` 永不含 commission/min_commission/slippage（R4） |
| 涨跌停按板块 20% | **未实现**。组合层 `price_limit` 是单一标量 flat 10%，`a_share_price_limit_rate` 是死 import（已删并加测试禁止其被接线） |
| 回测引擎撮合层 | A 线**硬约束不碰** `backtest/engine.py`（9 行缺失刻意不补） |

---

## 5. 已知缺陷（全部「只测不修」，修 bug 属判断类，交回判断线）

### 5.1 已用 `xfail(strict=True)` 固化的 7 条

> 不是「跳过」，是**把当前错误行为写成会喊的断言**：一旦修好，测试 XPASS 并因 strict 报错，强制提醒移除标记。

| # | 位置 | 缺陷 | 影响 |
|---|---|---|---|
| 1 | `tests/data/test_checks_extended.py::test_price_jump_count_should_equal_true_violations` | `validate_daily_extended` 用**双方括号** `df_sorted[["close"]]` 取到 DataFrame，使 `pct_change[pct_change > threshold]` 走 DataFrame 布尔掩码语义，`len(jumps)` 恒等于**行数-1**。100 行 3 处跳变、真实超阈值 5 次却报 99。 | **任何引用过 `price_jump` 计数的历史结论都需重核** |
| 2 | `test_checks_extended.py::test_flat_prices_should_not_report_price_jump` | 同一缺陷的更严重后果：**零跳变也误报**。历史研究 JSON 里「1594 行 → 1593 个跳变」就是这么来的。 | 同上 |
| 3 | `test_checks_extended_coverage.py::TestPriceJumps::test_public_path_reports_actual_jump_count` | 同上：60 行、真实跳变 3 个，实际报 59。 | 同上 |
| 4 | `test_checks_extended_coverage.py::TestPriceJumps::test_below_threshold_reports_nothing` | 全部变化低于阈值时仍报警。 | 数据质量检查形同虚设 |
| 5 | `test_checks_extended_coverage.py::TestPriceJumps::test_threshold_raising_keeps_check_silent` | 阈值 15% > 实际变化 10%，理应静默仍报警。 | 同上 |
| 6 | `test_parameter_selection_coverage.py::TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result` | `run_walkforward_window` 的「OOS 策略构造失败」分支在 `except` 里访问**尚未绑定**的局部变量 `strategy`（源码 447-448 行），函数抛 `UnboundLocalError` 崩溃，而非按契约返回 `oos_success=False`。 | 违反模块自述原则 4「失败显式表达」；失败被伪装成崩溃 |
| 7 | `test_walkforward_contracts.py::test_generate_windows_end_date_respects_boundary` | `generate_windows` 的 end_date 裁剪在 `walkforward/__init__.py:222` 用 `(~mask)` 而非 `mask` 求 `last_true_pos`，`end_idx` 恒等于 `len(index)`，**`end_date` 被完全忽略**。 | 窗口切分可能超出指定结束日（**对未来数据泄漏敏感，研究线必须知悉**） |

### 5.2 真实红灯 ①：环境序依赖导致的失败（**非 A 线回归**）

| 项 | 内容 |
|---|---|
| 用例 | `tests/backtest/test_gate1_run_contract.py::test_main_dry_run_with_exclusion_runs_no_backtest` |
| 现象 | 23:45 守卫跑**通过**；23:58 守卫跑**失败**（单跑确定性复现） |
| 断言 | `assert not (REPO_ROOT / "gate1_out").exists() or _gate1_out_untouched()` |
| 根因 | 该用例用「仓库根 `gate1_out/` 为空或不存在」证明 dry-run 没落盘。B 线于 **23:58 起**执行 Gate 1 主实验，真的写了 `gate1_out/`（含 `INDEX.md` 与运行目录）。于是「目录存在且非空」→ 断言失败。 |
| 定性 | **不是被测代码的缺陷**（dry-run 确实没写东西），是**测试对仓库级全局目录状态做断言 = 非 hermetic**。 |
| 处置 | 不改测试、不删 B 线产物。正确修法：让 `gate1_run.py` 支持可注入输出目录，断言改到 `tmp_path` 隔离目录——**要改 src/scripts，属判断类，已登记待办**。 |

### 5.3 真实红灯 ②：`tests/research` 在途 flaky（**不计入守卫**）

| 项 | 内容 |
|---|---|
| 用例 | `tests/research/test_config_report.py::test_render_report_is_deterministic` |
| 现象 | `158 passed / 1 failed`；**间歇性**（两次渲染落在同一秒就过，跨秒就红） |
| 根因 | `render_report` 把**挂钟时间戳**（精确到秒）写进 Markdown 正文；断言 `md1 == md2` 比较了整串文本。失败 diff 显示**唯一差异是秒数**（`23:58:33` vs `23:58:32`），其余 6310 字符全同。 |
| 定性 | 不是 `render_report` 的业务缺陷（报告带生成时间合理），是**测试断言颗粒度选错**。 |
| 建议 | 二选一：把时间戳抽成可注入的 `now=` 参数；或断言前正则抹掉「生成时间」行再比较。**属研究线在途，A 线只记录未修改。** |

### 5.4 已修 / 已澄清（不再算问题）

- **产品可用版 P0**：10 万本金买不起茅台 1 手导致 0 成交却报「回测完成」——已在 20:40 复审中修（默认资金改 100 万 + `NoFillNotice`）。细节见 B 线 `docs/SOFTWARE_AUDIT_2026-10-18.md`。
- **`/api` 前缀两个边界**：`/api`、`/api/` 曾返回 index.html（探活必误判），`/api/backtests/` 曾 404。已修。
- **测试隔离缺陷「25 failed / 全序」**：2026-10-18 复核为**合并中途脏磁盘的瞬态假象**，全量复测 1293 passed / 0 failed，**不存在持久隔离缺陷**。按硬约束「不得造假测试」未开 xfail。
- **`write_manifest` 假证据缺陷**（内存记录 A 配磁盘 CSV B）：已在 A-3 补充段修掉。

---

## 6. 测试基础设施问题

| 问题 | 说明 | 建议 |
|---|---|---|
| **非 hermetic 测试** | 至少 1 条用例（`test_main_dry_run_with_exclusion_runs_no_backtest`）断言**仓库级目录状态**，任何其他会话写该目录都会让它变红。 | 输出目录可注入化，测试一律用 `tmp_path` |
| **Flaky 测试** | `test_render_report_is_deterministic` 依赖挂钟秒。 | 时钟可注入，或断言前归一化时间戳 |
| **venv 文档不符** | 任务书写的 venv 没装 pytest。 | 已在本文件 §2.1 与 TEST_REPORT §1.1 勘误 |
| **覆盖率口径易被误读** | `research/**` 1968 stmts 全 0%（其测试被守卫 `--ignore`），算进总分会把 96.7% 拉到 73.8%。 | 报告里三个口径必须同时列出 |
| **并发会话风险（已二次复发）** | 曾出现两个会话并行跑同一条 A 线清单，产生重复产出（如 `checks_extended.py` 两份测试）。 | 同一时刻只应有一个会话执行 A 线；开工前除看锁外，扫 `find tests -newermt "-30 minutes"` |

---

## 7. 下一步建议（A 线视角，供人工决策）

**优先级排序**：

1. **修 7 条 xfail 缺陷中的 #1–#5（price_jump）** —— 影响面最大：任何历史结论里引用过「跳变数」的都要重核。修一行（把 `[["close"]]` 改成 `["close"]`）即可，但**属改 src，需判断线拍板**。
2. **修 #7（`generate_windows` end_date 被忽略）** —— 涉及窗口切分边界，对未来数据泄漏敏感，研究线开跑前应确认。
3. **修 #6（`UnboundLocalError`）** —— 违反「失败显式表达」，会让真实失败伪装成崩溃。
4. **消除非 hermetic 测试** —— 让 `gate1_out` 可注入，避免以后每次 B 线真跑都把 A 线守卫染红（**现在就会染红**）。
5. **消除 flaky** —— 时钟可注入化。
6. **可选提升覆盖**：`api/routes/backtests.py`（7）、`performance/serialize.py`（7）、`performance/metrics.py`（5）、`api/ui.py`（4）等个位数缺口。
7. **`backtest/engine.py` 的 9 行不补**（硬约束）。

**交付物状态（阶段 A–E）**：

| 交付物 | 状态 |
|---|---|
| `docs/ENGINEERING_BASELINE_REPORT.md` | ✅ 已产出并推送 |
| `docs/A1_PR_AUDIT_AND_A2_REVERIFY_2026-10-18.md` | ✅ 已产出并推送 |
| `docs/COVERAGE_GAPS.md` | ✅ 10-17 快照 + 10-18 收官刷新（§7） |
| `docs/TEST_REPORT_2026-10-18.md` | ✅ 本轮产出并推送 |
| `docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md` | ✅ 本文件 |
| `docs/KNOWN_ISSUES_RESEARCH_2026-10-18.md` | ⏳ B 线所有，**A 线不碰** |

---

## 8. 本文的边界与未做的事

- **未改 `src/` 任何一行**；未弱化、未跳过、未删除既有测试；未为了让测试通过而改实现。
- 未 merge 任何 PR，未 push main（硬约束 R8）。
- 未复核 `docs/SOFTWARE_AUDIT_2026-10-18.md` 的逐条结论（属 B 线），仅在 §4.2 / §5.4 引用。
- 未验证任何券商接口、未连接真实交易。
- 本文所有数字可复现，命令见 `docs/TEST_REPORT_2026-10-18.md` §1。
