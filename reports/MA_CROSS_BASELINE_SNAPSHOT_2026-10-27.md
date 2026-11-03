# MA-CROSS 独立复核：执行前基线快照

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-27 09:42:50 +0800 |
| git HEAD | `5cfed94a4283` |
| 工作区变更条目数 | 65 |
| `data/step75` 是否被 git 跟踪 | 是 |
| 主池数据可用性 | backup 内有 **51** 只 raw CSV / **51** 只 processed parquet |
| universe 规模 | 50 只（csi300_50.json） |

## 1. git 近 5 次提交

```
5cfed94 阶段 U 结题：NW 滞后稳健性全项目审计 —— NW_LAG_SENSITIVITY_CONFIRMED
aa4266e 阶段 T 结题：exQ5 现代段显著性不承重 —— T_FRAGILE
a768937 阶段 S 结题：exQ5 现代段（2016-2026）成本后 alpha 可检出 —— MODERN_ERA_ALPHA_DETECTED
294c57c API 推送脚本：远端头改存 .git/api_push_remote_head，推送成功后自动更新，消除自我指涉
d7bfd89 推送工具落地仓库：git 直连走代理时反复 502，API 备用通道不再依赖临时目录
```

## 2. 关键文件哈希（sha256 原始字节）

| 文件 | 存在 | 大小 | sha256 前 16 |
|---|---|---:|---|
| `backend/src/deepquant/strategies/ma_cross.py` | ✅ | 2195 | `e0b215d9d16791ea` |
| `backend/src/deepquant/walkforward/parameter_selection.py` | ✅ | 31140 | `cca0e3007ed53209` |
| `backend/src/deepquant/walkforward/sensitivity.py` | ✅ | 32783 | `327bb417fa509228` |
| `backend/src/deepquant/backtest/engine.py` | ✅ | 17808 | `78bdf7807bc8009e` |
| `backend/src/deepquant/backtest/config.py` | ✅ | 6510 | `31fb477389ea522c` |
| `backend/src/deepquant/backtest/costs.py` | ✅ | 8975 | `a344e96d2504468e` |
| `backend/src/deepquant/backtest/fee_presets.py` | ✅ | 5368 | `db1dc08f9b425b2d` |
| `backend/src/deepquant/market/rules.py` | ✅ | 5188 | `9d68cdd3c94067dd` |
| `backend/src/deepquant/market/ashare.py` | ✅ | 4953 | `24b1764adb905d4e` |
| `backend/src/deepquant/performance/window_records.py` | ✅ | 31053 | `2690e644e39fb6ce` |
| `backend/src/deepquant/performance/benchmarks.py` | ✅ | 19544 | `0e68a50a722753d7` |
| `backend/src/deepquant/data/step75/research.py` | ❌ |  | `—` |
| `data/step75/research.py` | ✅ | 31660 | `50a8b2b0d92ccf7f` |
| `data/step75/build_universe.py` | ✅ | 7922 | `d3e638a8b2515baa` |
| `scripts/b20_window_benchmark.py` | ✅ | 19114 | `2a648a8a6254cf7f` |
| `data/step75/universe/csi300_50.json` | ✅ | 9103 | `024a7b214ec8c844` |
| `data/step75/step75_research_summary.json` | ✅ | 73731 | `f1df1b7ed3d0fbc6` |
| `docs/B15_8Y_30GROUPS_2026-10-18.md` | ✅ | 17218 | `42277a10bd5bb124` |
| `docs/B16_DIRECTION_VERDICT_2026-10-18.md` | ✅ | 11482 | `89c4559a554daed3` |
| `docs/B17_CLOSURE_REPORT_2026-10-18.md` | ✅ | 23933 | `624dc744c5cdb0bf` |
| `docs/B18_RESEARCH_VERDICT_2026-10-18.md` | ✅ | 38776 | `5bc024a922c530b0` |
| `docs/B20_WINDOW_BH_2026-10-18.md` | ✅ | 14898 | `f943c5d909bc658a` |
| `docs/OOS_VERDICT_2026-10-19.md` | ✅ | 15401 | `6c1041c7d575a7de` |
| `docs/OOS_RESULTS_2026-10-19.json` | ✅ | 565933 | `c58c878ac5e34129` |
| `docs/oos_prereg_2026-10-19.json` | ✅ | 6647 | `b6076e3213b4b397` |
| `docs/DIAG_OOS_ZERO_MEDIAN.md` | ✅ | 8915 | `f53dead7f6ad1aa0` |
| `docs/COMPREHENSIVE_ASSESSMENT_2026-10-20.md` | ✅ | 36629 | `bdfdcbc7098363ba` |
| `docs/MA_CROSS_STRATEGY_SUMMARY_2026-10-27.md` | ✅ | 17478 | `fb44c16e5e4e3d39` |

## 3. 数据资产（backup，51 只）

| 项 | 值 |
|---|---|
| 目录 | `data/_backup_step75_baseline_20261020/` |
| 结构 | `raw/*.raw.csv`（51）｜`processed/*.daily.parquet`（51）｜`metadata/*.meta.json`（51） |
| 数据源（metadata 记录） | `step75/000001.SZ.qfq.csv` |
| 覆盖区间 | 2017-01-03 ~ 2024-12-31 |
| 摄取时间 | 2026-10-18T15:06:39+08:00 |
| 主池 48 只是否齐全 | **是**（universe 50 − 数据闸门 2 + 手挑 3 = 51） |

> ⚠️ **数据来源链条**：metadata 记录 `source.sha256` 指向原始 `step75/{code}.qfq.csv`，但该原始缓存文件**本身不在工作区**；backup 里的 `raw.csv` 是再导出副本。二者是否逐字节一致**不可验证**（UNAVAILABLE），只能验证数值一致（见 P0-1）。

## 4. 结论

- 本次复核**不需要联网、不需要修改任何既有代码**：`data/step75/research.py` 已支持
  `DEEPQUANT_DATA_ROOT` / `DEEPQUANT_UNIVERSE` / `DEEPQUANT_PRICE_LIMIT` / `DEEPQUANT_W1_OUT`，
  可在独立目录内重跑 v2（`PRICE_LIMIT=off`）与 v3（`PRICE_LIMIT=board`），并落逐窗明细。
- **48 只主池数据已恢复**（来自 backup），48 只**实验结果 JSON 仍未恢复**（见 P0-1）。

## 5. 产物

- `reports/MA_CROSS_BASELINE_HASHES_2026-10-27.csv`（28 个文件）
- `reports/MA_CROSS_BASELINE_DATA_2026-10-27.csv`（51 只）
