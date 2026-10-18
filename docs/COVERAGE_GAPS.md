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
