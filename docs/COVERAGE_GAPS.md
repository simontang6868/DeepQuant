# 覆盖率缺口清单（COVERAGE_GAPS）

> 本文档**只列事实，不给建议**。所有数字来自一次本地实测，复现命令见文末。
> 生成时间：2026-10-17 ｜ 关联 PR：A6（CI 三合一，引入 `--cov` 后才能量化）。

## 1. 总览

| 指标 | 数值 |
|---|---|
| 语句总数 (Stmts) | 4085 |
| 未覆盖语句 (Miss) | 272 |
| **总覆盖率** | **93%** |
| 测试通过 | 852 passed / 1 skipped / 1 warning |
| 覆盖率工具 | pytest-cov 7.1.0 + coverage 7.16.2（Python 3.13.14）|

## 2. 分模块覆盖率（按包分组，逐字抄自 `term-missing` 输出）

| 模块 | Stmts | Miss | Cover | Missing |
|---|---:|---:|---:|---|
| **analysis/** | | | | |
| analysis/ensemble_eval.py | 42 | 1 | 98% | 121 |
| analysis/ensemble_report.py | 37 | 2 | 95% | 56-57 |
| analysis/returns.py | 12 | 0 | 100% | |
| analysis/rolling.py | 17 | 0 | 100% | |
| analysis/signal_metrics.py | 81 | 0 | 100% | |
| **api/** | | | | |
| api/app.py | 13 | 0 | 100% | |
| api/config.py | 9 | 3 | 67% | 23-25 |
| api/datasets.py | 112 | 9 | 92% | 75-76, 83, 92, 101, 111, 115, 118, 195 |
| api/errors.py | 86 | 6 | 93% | 159-160, 168-169, 201-202 |
| api/registry.py | 55 | 10 | 82% | 85-93, 107 |
| api/routes/backtests.py | 142 | 7 | 95% | 77-78, 103-104, 350, 410-411 |
| api/routes/datasets.py | 12 | 0 | 100% | |
| api/routes/health.py | 5 | 0 | 100% | |
| api/routes/strategies.py | 8 | 0 | 100% | |
| api/schemas.py | 165 | 0 | 100% | |
| **backtest/** | | | | |
| backtest/config.py | 76 | 3 | 96% | 120, 135, 139 |
| backtest/costs.py | 67 | 0 | 100% | |
| backtest/engine.py | 186 | 9 | 95% | 74, 86, 107, 109, 127, 151, 153, 220, 403 |
| backtest/fee_presets.py | 36 | 36 | **0%** | 14-129 |
| backtest/models.py | 242 | 17 | 93% | 79, 125, 129, 137, 160, 224, 226, 238, 240, 248, 257, 264, 322, 388, 390, 392, 396 |
| **data/** | | | | |
| data/loaders/akshare_loader.py | 99 | 3 | 97% | 147, 197, 285 |
| data/loaders/csv_loader.py | 46 | 2 | 96% | 50-51 |
| data/pipeline.py | 63 | 2 | 97% | 129-130 |
| data/storage/io.py | 29 | 0 | 100% | |
| data/validation/checks.py | 69 | 1 | 99% | 53 |
| data/validation/checks_extended.py | 69 | 18 | 74% | 62, 70, 82, 116, 129, 144-160, 188, 205, 208, 214 |
| **market/** | | | | |
| market/ashare.py | 55 | 0 | 100% | |
| market/rules.py | 32 | 0 | 100% | |
| **performance/** | | | | |
| performance/comparison.py | 29 | 0 | 100% | |
| performance/json_encoding.py | 66 | 9 | 86% | 54, 56, 68, 75, 77, 82, 87, 89, 118 |
| performance/metrics.py | 165 | 5 | 97% | 161, 166-167, 177, 208 |
| performance/reproducibility.py | 123 | 15 | 88% | 49, 60, 71, 75-76, 78, 81-82, 93, 152-153, 168-169, 203, 213 |
| performance/serialize.py | 130 | 7 | 95% | 75-76, 81, 87, 120-121, 549 |
| **persistence/** | | | | |
| persistence/connection.py | 29 | 0 | 100% | |
| persistence/repro_checklist.py | 147 | 3 | 98% | 106-107, 349 |
| persistence/signal_store.py | 107 | 6 | 94% | 173-174, 233-234, 313-314 |
| persistence/store.py | 246 | 29 | 88% | 239-240, 249-251, 253, 287-288, 309-310, 350, 352, 365-368, 385-388, 419-420, 498-499, 531, 551-552, 555, 563, 601 |
| **strategies/** | | | | |
| strategies/base.py | 18 | 0 | 100% | |
| strategies/ensemble.py | 73 | 1 | 99% | 222 |
| strategies/ensemble_voting.py | 84 | 2 | 98% | 80, 182 |
| strategies/ma_cross.py | 36 | 0 | 100% | |
| strategies/mean_reversion.py | 36 | 0 | 100% | |
| strategies/signals.py | 104 | 9 | 91% | 61, 63, 69, 121, 123, 189, 221, 232, 234 |
| **walkforward/** | | | | |
| walkforward/design_audit.py | 96 | 1 | 99% | 111 |
| walkforward/parameter_selection.py | 206 | 18 | 91% | 98, 155, 177-184, 204-219, 244-247, 358-359, 428-429, 459-460, 636-655 |
| walkforward/sensitivity.py | 317 | 4 | 99% | 194, 257, 315, 345 |
| **TOTAL** | **4085** | **272** | **93%** | |

> `__init__.py` 与 `version.py` 均为 0 语句或 100%，已省略。

## 3. 覆盖率低于 50% 的模块（逐条列出缺失行号）

| 模块 | 覆盖率 | 缺失行号 |
|---|---:|---|
| `backend/src/deepquant/backtest/fee_presets.py` | **0%** | 14-129（全部 36 条语句均未覆盖）|

> 说明：`fee_presets.py` 当前没有任何测试触达。这是唯一一个覆盖率低于 50% 的模块。

## 4. 无同名/相关测试文件的模块（文件名对照 `src/**/*.py` vs `tests/**/test_*.py`）

以下 10 个模块**没有同名或名称相关的测试文件**。注意：文件名对照只反映"是否有专属测试文件"，
不等同于"运行时零覆盖"——其中多数仍被其他测试间接执行（右侧为其 `term-missing` 实测覆盖率）。

| 模块 | 运行时覆盖率 | 备注 |
|---|---:|---|
| `api/app.py` | 100% | 被 API 路由测试间接执行 |
| `api/errors.py` | 93% | |
| `api/registry.py` | 82% | |
| `api/schemas.py` | 100% | |
| `backtest/fee_presets.py` | **0%** | 真正未被任何测试触达（见第 3 节）|
| `data/validation/checks.py` | 99% | |
| `data/validation/checks_extended.py` | 74% | |
| `performance/json_encoding.py` | 86% | |
| `persistence/connection.py` | 100% | |
| `strategies/base.py` | 100% | |

> 事实陈述（非建议）：10 个"无专属测试文件"的模块里，仅 `fee_presets.py` 在运行时也确实零覆盖；
> 其余 9 个虽无同名文件，但已被其他测试间接执行，覆盖率在 74%–100% 区间。

## 5. 2026-10-18 刷新（A 线下一阶段前置）

> **本節为对 2026-10-17 快照的刷新，原第 1~4 節（10-17 实测）全部保留不覆写（R5）。**
> 复现命令同文末「附」；本次实测时间 2026-10-18，Python 3.13.14（venv），`--ignore=tests/research`。

### 5.1 总览（刷新后真实数字）

| 指标 | 数值 |
|---|---|
| 语句总数 (Stmts) | **5883**（10-17 为 4085，增加约 1798 条） |
| 未覆盖语句 (Miss) | **1208** |
| **总覆盖率** | **79%**（10-17 为 93%） |
| 测试通过（A 线口径，--ignore=tests/research） | **1208 passed / 1 skipped / 6 xfailed / 0 failed** |
| 覆盖率工具 | pytest-cov 7.x + coverage 7.x（Python 3.13.14） |

**总覆盖率从 93% 降到 79% 的原因（非回归）**：10-17 之后 B 线 B18（Gate 0 装置，PR #35，`workbuddy/b18-gate0-device`，**未合 main**）新增了约 1800 条语句的 `performance/` 模块（`benchmarks.py` / `curve.py` / `portfolio_metrics.py` / `window_distribution.py` 等）。这些模块**不在 main 上、属在途工作**，不应计入 A 线缺口分析，但被 coverage 统计进了分母，拉低了总百分比。**A 线真正负责的源码（`api/` `backtest/` `data/` `strategies/` `walkforward/` `persistence/` `analysis/` `market/` 中已在 main 的部分）覆盖率仍高。**

### 5.2 扣除 A9~A12 后的真实剩余缺口（A 线候选）

A9~A12 已合并且其目标模块实测已 100%，**从缺口清单中移除**：

| 已闭环模块 | 10-17 缺口 | 2026-10-18 实测 |
|---|---|---|
| `backtest/fee_presets.py`（A9） | 0%（36/36 miss） | **100%**（已闭环） |
| `data/validation/checks_extended.py`（A10） | 74%（18 miss） | **100%**（已闭环） |
| `walkforward/parameter_selection.py`（A11） | 91%（18 miss） | **100%**（已闭环） |
| `performance/json_encoding.py` + `reproducibility.py`（A12） | 86% + 88% | **均 100%**（已闭环） |

**仍 <100% 且属 A 线范畴（main 上、非 research、非 B18 在途）的模块**，按「未覆盖语句数」降序：

| 模块 | Stmts | Miss | Cover | Missing（行） | 备注 |
|---|---:|---:|---:|---|---|
| `persistence/store.py` | 246 | **29** | 88% | 239-240,249-251,253,287-288,309-310,350,352,365-368,385-388,419-420,498-499,531,551-552,555,563,601 | 回测结果持久化地基；多为异常/边界分支 |
| `walkforward/__init__.py` | 199 | 32 | 84% | 90,98,197,201,205,211-225,230,263-264,285,314-320,325-326,368-369,514,516,518,530-548 | `run_fixed_parameter_walkforward` 等高层入口 |
| `backtest/models.py` | 242 | 17 | 93% | 79,125,129,137,160,224,226,238,240,248,257,264,322,388,390,392,396 | Order/Fill/Position/AccountState 地基 |
| `api/registry.py` | 55 | 10 | 82% | 85-93,107 | 策略注册表 |
| `strategies/signals.py` | 104 | 9 | 91% | 61,63,69,121,123,189,221,232,234 | |
| `api/datasets.py` | 112 | 9 | 92% | 75-76,83,92,101,111,115,118,195 | |
| `api/errors.py` | 86 | 6 | 93% | 159-160,168-169,201-202 | |
| `api/config.py` | 9 | 3 | **67%** | 23-25 | 百分比最低（但绝对语句少） |
| `data/pipeline.py` | 63 | 2 | 97% | 129-130 | |
| `backtest/portfolio.py` | 417 | 38 | 91% | 103,137,197,229,351,359-362,369,… | B18 组合层，属在途（PR #35） |
| `performance/benchmarks.py` | 150 | 10 | 93% | … | B18 在途（PR #35） |
| `performance/curve.py` | 65 | 3 | 95% | 71,73,75 | B18 在途（PR #35） |
| `performance/portfolio_metrics.py` | 109 | 6 | 94% | 133,186,209,219,248,298 | B18 在途（PR #35） |
| `performance/window_distribution.py` | 64 | 1 | 98% | 103 | B18 在途（PR #35） |

> 事实陈述（非建议）：A 线下一阶段若按「缺失语句数 × 非判断类」排，优先序为
> `store.py`(29) → `walkforward/__init__.py`(32) → `models.py`(17) → `registry.py`(10) →
> `signals.py`(9) / `datasets.py`(9) → `errors.py`(6) → `config.py`(3) → `pipeline.py`(2)。
> `walkforward/__init__.py`、`backtest/portfolio.py` 含高层编排，挑「纯契约/不变量」分支做，不碰判断逻辑。
> 所有 `performance/*` 与 `research/*` 为 B 线在途，**A 线不碰**（见本批次裁决）。

### 5.3 A 线基线说明

- A 线基线以 `pytest tests/ -q --ignore=tests/research` 为准：**1208 passed / 1 skipped / 6 xfailed / 0 failed**。
- `tests/research/`（equity_bond_spread 在途）的失败**不计入 A 线**，按本批次裁决「在途工作，不修、不据以回退」。
- 此前「完整顺序 25 failed」经核对**完全来自 `tests/research/`**，A 线作用域（忽略 research）下 0 failed；故不存在 A 线作用域内的测试隔离缺陷（详见 A14 处置记录）。

## 附：复现命令

```bash
cd backend
PYTHONPATH=src python -m pytest tests/ -q --cov=deepquant --cov-report=term-missing
# 依赖：pytest-cov（开发依赖，见 backend/requirements-dev.txt）
# 真实网络链路测试默认跳过，需：DEEPQUANT_LIVE=1 python -m pytest tests/data/test_akshare_live.py -q -m network
```

## 6. 2026-10-18 刷新（A16 契约链收官后）

> 本节为对第 5 节（同为 2026-10-18 刷新）的**二次刷新**。**第 1~5 节全部保留不覆写（R5）。**
> 实测时间 2026-10-18（A16#11 完成后），Python 3.13.14（venv），`--ignore=tests/research`。
> 复现命令见文末「附」，但须用 venv 的 `pytest.exe` 且显式 `PYTHONPATH=backend/src`，
> 以避免 `python -m pytest` 触发 numpy `_multiarray_umath` 双重加载（`ImportError: cannot load module more than once per process`）。

### 6.1 总览（真实数字）

| 指标 | 数值 |
|---|---|
| 语句总数 (Stmts) | **8052**（§5 为 5883，增加约 2169 条：A16 新增 ~1500 测试间接带动 + B 线在途代码） |
| 未覆盖语句 (Miss) | **2170** |
| **总覆盖率（含 research/）** | **73.1%** |
| 排除 research/*（B 线实验层，0% by design） | 6084 stmts / 202 miss = **96.7%** |
| **A 线作用域**（再排除 10 个 B 线在途 NEW 文件） | 4194 stmts / 80 miss = **98.1%** |
| 测试通过（含 A16#11 新文件） | **1644 passed / 1 failed / 2 skipped / 7 xfailed** |
| 测试通过（不含 A16#11 新文件，验证用） | **1656 passed / 0 failed / 2 skipped / 6 xfailed** |
| 覆盖率工具 | pytest-cov 7.x + coverage 7.x |

**关于 1 failed 的说明（非回归）**：`tests/data/test_instrument_name.py::test_name_does_not_reach_the_market_data`
在全量运行（含 A16#11 新文件）时报错，但：
- 单独运行该用例 → **1 passed**；
- 剔除 A16#11 新文件后的全量 → **0 failed**；
- A16#11 新增文件自身 **18 passed / 1 xfailed**，与目标模块无关。

即该失败是**先存的「测试隔离 / 收集顺序相关」脆弱性**（属 A14 研究过的同类缺陷），
仅因新增文件改变了 pytest 收集顺序而被再次暴露，**不是 A16#11 的代码回归**。
A16#11 未改动任何 `src/` 源码，也未改动该用例。
建议后续按 A14 思路对该用例做 `xfail(strict=True)` 固化或隔离其全局状态依赖（不在 A16#11 范围内）。

**A16#12（2026-10-18 晚）**：新增 `tests/persistence/test_signal_store_contracts.py`（4 passed），将 `signal_store.py` 由 94%（6 miss）推至 **100%（0 miss）**，补齐其三个「底层 sqlite3.Error → PersistenceError」守卫分支（save/list/count 的 DB 故障映射）。A 线作用域未覆盖语句由 86 降至 **80（98.1%）**。

### 6.2 A16 契约链收官表（#1~#11，全部本地、未提 PR）

| A16# | 目标模块 | 起点 | 终点 | 新增契约测试文件 |
|---|---|---|---|---|
| #1 | persistence/store.py | 88% | **93%**（246 stmts / 18 miss，上限受 ~14 行死代码限制：逐函数 `except sqlite3.Error` 不可达） | tests/persistence/test_store_contracts.py (14) |
| #2 | backtest/models.py | 93% | **100%** | tests/backtest/test_models_contracts.py (33) |
| #3 | api/registry.py | 82% | **100%** | tests/api/test_registry_contracts.py (10) |
| #4 | strategies/signals.py | 91% | **100%** | tests/strategies/test_signals_contracts.py (11) |
| #5 | api/datasets.py | 92% | **100%** | tests/api/test_datasets_contracts.py (10) |
| #6 | api/errors.py | 93% | **100%** | tests/api/test_errors_contracts.py (4) |
| #7 | api/config.py | 67% | **100%** | tests/api/test_config_contracts.py (10) |
| #8 | data/pipeline.py | 97% | **100%** | tests/data/test_pipeline_contracts.py (2) |
| #9 | backtest/config.py | 97% | **100%** | tests/backtest/test_backtest_config_contracts.py (19) |
| #10 | persistence/store.py 边界/错误（延续 #1） | — | 补充 13 条边界契约（本地漂移收尾） | tests/persistence/test_store_edge_contracts.py (13) |
| #11 | walkforward/__init__.py | 84% | **94%**（199 stmts / 12 miss；上限受死代码 + 1 处 BUG 限制，见 6.3） | tests/walkforward/test_walkforward_contracts.py (18 passed + 1 xfailed) |
| #12 | persistence/signal_store.py | 94% | **100%**（107 stmts / 0 miss；补齐三个 `sqlite3.Error→PersistenceError` 守卫 173-174/233-234/313-314） | tests/persistence/test_signal_store_contracts.py (4) |

> A16 链共 12 项、11 个目标模块，全部纯加法（未改 src/ 一行），本地落盘未提 PR（R8 仍待用户授权）。
> 9 个模块达 100%；store.py 上限 ~95%（死代码）、walkforward/__init__.py 上限 ~94%（死代码 + BUG）。

### 6.3 walkforward/__init__.py 真实覆盖率上限（A16#11 发现）

A16#11 将 walkforward/__init__.py 从 84%（32 miss）推到 **94%（12 miss）**。剩余 12 行不可达，分两类：

1. **死代码（~11 行，设计冗余）**：`generate_windows` 的 `valid=False` 两个分支（约 259-261 / 263-264）
   永远走不到——`WalkForwardConfig.__post_init__` 已强制 `train_window≥min_train` 且 `oos_window≥min_oos`，
   窗口内 `n_train/n_oos` 恒等于二者，故不会触发 skip；同理 `step≤0` 守卫（285）、
   `_slice_with_warmup` 的 `get_loc` 非 int 兜底（314-320 / 325-326）、
   `run_fixed_parameter_walkforward` 的无效窗口占位分支（530-548，因 specs 永远 valid）均不可达。
   已由不变量测试 `test_generate_windows_all_specs_valid_for_valid_config` 钉死「合法 config ⇒ 所有 spec.valid 恒为 True」。

2. **一处真实 BUG（1 行，已 xfail 固化）**：`generate_windows` 的 end_date 裁剪在 `__init__.py:222`
   用 `(~mask)` 而非 `mask` 求 `last_true_pos`，导致 `end_idx` 恒等于 `len(index)`，**`end_date` 参数被完全忽略**。
   已用 `test_generate_windows_end_date_respects_boundary`（`@pytest.mark.xfail(strict=True)`）固化：
   修复后该用例将 XPASS 触发 strict 报错，强制移除标记。此 BUG 同时使其后
   「可用区间为空时返回 `[]`（line 230）」也变为不可达。

> 此 BUG 仅影响 end_date 裁剪语义，不破坏现有正常回测（现有调用未传 end_date）。是否修复属 src 改动，
> 超出 A16「只写测试」范围，留待用户决策。

### 6.4 A 线作用域分包覆盖率（post-A16#11，excl research + B 线在途）

| 包 | Stmts | Miss | Cover |
|---|---:|---:|---:|
| analysis | 189 | 3 | 98.4% |
| api | 740 | 16 | 97.8% |
| backtest | 1191 | 53 | 95.5%（含 engine 9 行死守 + B 线 portfolio/cross_section） |
| data | 395 | 5 | 98.7% |
| market | 103 | 0 | 100% |
| performance | 1753 | 84 | 95.2%（几乎全为 B 线在途代码） |
| persistence | 534 | 27 | 94.9% |
| strategies | 351 | 3 | 99.1% |
| walkforward | 824 | 37 | 95.5% |

### 6.5 后续候选（A 线可继续的方向）

A16 链已收官，剩余 A 线作用域内缺口均为**个位数小缺口**或**受限分支**：
- 高价值契约：api/routes/backtests.py（7 miss，HTTP 层守卫）、api/ui.py（4）、persistence/signal_store.py（6）、
  persistence/repro_checklist.py（3）、data/loaders/*（各 2-3）。
- store.py / walkforward/__init__.py 已达真实上限，除非先清死代码/修 BUG 否则不再提升。
- 其余 `research/*`（1968 stmts，0%）、及 B 线在途 `performance/*` / `backtest/portfolio` / `backtest/cross_section`
  均**非 A 线范畴**（B 线负责）。
- 未决事项：①COVERAGE_GAPS.md 已二次刷新（本节）；②`test_instrument_name` 隔离缺陷建议 xfail 固化（见 6.1）；
  ③双线仍全部本地未提 PR（R8 待授权）。

---

## 7. 2026-10-18 收官刷新（A 线）

> **本节为追加章节，不覆写、不修改上文任何一节的 10-17 快照（R5）。**
> 上文 §1–§3 是 2026-10-17 的原始实测快照（4085 stmts / 272 miss / 93%），当时 `research/**` 尚未大规模进入仓库，
> 口径与本节不同，**两节不可直接相减**，请分别按各自的复现命令理解。

### 7.1 复现命令与口径

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-18 23:52 GMT+8 |
| 命令 | `cd backend && PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" -m pytest tests/ -q --ignore=tests/research --cov=deepquant --cov-report=term-missing` |
| 解释器 | Python 3.13.14（⚠️ 任务书写的 `~/.workbuddy/.../dq_demo` **没装 pytest**，实际 venv 是 `D://DeepQuant power by txm\venv`） |
| 测试结果 | 1718 passed / 1 skipped / 7 xfailed / 0 failed |
| 数据源 | `.coverage` → `coverage json` → `probe_out/coverage_2026-10-18.json` |

### 7.2 三个口径必须分开看

`src/deepquant/research/**`（股债利差 A 股版 + 美股版）的测试全部在 `tests/research/`，
被守卫 `--ignore`，所以在守卫口径下**覆盖率为 0%**。把它算进总分会把数字拉低 20 多个百分点，属于口径污染。

| 口径 | Stmts | Miss | Cover |
|---|---:|---:|---:|
| 全仓库（含 `research/**`） | 8304 | 2177 | 73.78% |
| `research/**` 子包 | 1968 | 1968 | 0%（被守卫排除，**非缺陷**） |
| **非 research（A 线守卫口径）** | **6336** | **209** | **96.70%** |

> 对照 10-17 快照：4085 stmts / 272 miss / 93%（全仓库口径）。
> 守卫口径从 93% 提升到 **96.70%**，同时代码量增长约 55%（新增 portfolio / cross_section / gate1_* / window_records_integrity 等）。

### 7.3 分包覆盖率（守卫口径，excl. `research/**`）

| 包 | Stmts | Miss | Cover | 对比 10-17 §6.4 |
|---|---:|---:|---:|---|
| `(root) version.py` | 4 | 0 | 100.0% | 新增行 |
| `analysis` | 189 | 3 | 98.4% | 持平 |
| `api` | 991 | 26 | 97.4% | 740→991 stmts（新增 portfolio/ui/buildinfo），97.8%→97.4% |
| `backtest` | 1192 | 50 | 95.8% | 95.5%→95.8% |
| `data` | 395 | 5 | 98.7% | 持平 |
| `market` | 103 | 0 | **100.0%** | 持平 |
| `performance` | 1753 | 84 | 95.2% | 持平（几乎全为 B 线在途代码） |
| `persistence` | 534 | 21 | 96.1% | 94.9%→96.1% |
| `strategies` | 351 | 3 | 99.1% | 持平 |
| `walkforward` | 824 | 17 | 97.9% | 95.5%→97.9% |

**非 research 模块共 76 个，其中 45 个达到 100%。**

### 7.4 本轮补齐到 100% 的模块

| 模块 | 10-17 | 现在 | 手段 |
|---|---:|---:|---|
| `api/config.py` | 9 / 3 miss = 67% | **100%** | `tests/api/test_config_contracts.py`（10 条，A16#7 / A-5） |
| `api/errors.py` | 86 / 6 = 93% | **100%** | `tests/api/test_errors_contracts.py`（4 条） |
| `api/registry.py` | 55 / 10 = 82% | **100%** | `tests/api/test_registry_contracts.py`（10 条） |
| `backtest/config.py` | 76 / 3 = 96% | **100%** | `tests/backtest/test_backtest_config_contracts.py`（19 条） |
| `data/validation/checks_extended.py` | 74% | **100%** | `tests/data/test_checks_extended.py`（A10） |
| `backtest/fee_presets.py` | 0% | **100%** | `tests/backtest/...`（A9） |
| `performance/json_encoding.py` | — | **100%** | `tests/performance/test_serialization_contracts.py`（64 条，A12） |
| `performance/reproducibility.py` | — | **100%** | 同上（合计 99%） |
| `walkforward/parameter_selection.py` | 91% | **100%** | `tests/walkforward/test_parameter_selection_coverage.py`（20 条） |
| `api/datasets.py` | 112 / 9 = 92% | **168 / 2 = 99%** | `tests/api/test_datasets_contracts.py`（10 条） |

### 7.5 当前缺口（按 Miss 降序，Top 15）

| 模块 | Stmts | Miss | Cover | 归属 |
|---|---:|---:|---:|---|
| `backtest/portfolio.py` | 418 | 33 | 92% | B 线在途 |
| `performance/window_records.py` | 313 | 18 | 94% | B 线在途 |
| `performance/window_records_integrity.py` | 209 | 18 | 91% | A 线（B-1 配套） |
| `persistence/store.py` | 246 | 18 | 93% | A 线（已近真实上限） |
| `walkforward/__init__.py` | 199 | 12 | 94% | A/B 共有 |
| `performance/benchmarks.py` | 150 | 10 | 93% | B 线在途 |
| `performance/gate1_judgement.py` | 160 | 10 | 94% | B 线在途 |
| `api/portfolio.py` | 112 | 9 | 92% | B 线在途 |
| `backtest/engine.py` | 186 | 9 | 95% | **死守**（R：不碰 engine） |
| `backtest/cross_section.py` | 167 | 8 | 95% | B 线在途 |
| `api/routes/backtests.py` | 142 | 7 | 95% | A 线可做（HTTP 层守卫） |
| `performance/serialize.py` | 130 | 7 | 95% | A 线可做 |
| `performance/gate1_report.py` | 170 | 6 | 96% | B 线在途 |
| `performance/portfolio_metrics.py` | 109 | 6 | 94% | B 线在途 |
| `performance/metrics.py` | 165 | 5 | 97% | A 线可做 |

完整 31 个未达 100% 模块的逐行缺失清单见 `docs/TEST_REPORT_2026-10-18.md` §3.3。

### 7.6 收官结论

1. **A 线作用域内已无「0% 或极低覆盖」的模块**——`fee_presets.py`（0%）、`checks_extended.py`（74%）、
   `parameter_selection.py`（91%）、`api/config.py`（67%）、`api/registry.py`（82%）全部补齐。
2. 剩余缺口集中在 **B 线在途代码**（`portfolio.py` / `gate1_*` / `window_records.py` / `benchmarks.py`），
   这些模块的缺失行大多对应**尚未实现的分支或错误处理路径**，等 B 线功能定稿后再补才有意义。
3. `backtest/engine.py` 的 9 行缺失**刻意不补**（硬约束：不碰 `backtest/engine.py`）。
4. `research/**` 1968 stmts 全 0% 是**口径使然**（其测试被守卫 `--ignore`），单独跑 `tests/research` 时为 158 passed / 1 failed，
   详见 `docs/TEST_REPORT_2026-10-18.md` §5.3。
5. 本节**只列事实不给建议**；是否继续提升由人工决策。

