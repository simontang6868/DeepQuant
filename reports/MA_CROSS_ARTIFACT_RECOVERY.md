# MA-CROSS 独立复核 P0-1：原始研究产物恢复

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-27 09:48:21 +0800 |
| 检索范围 | 仓库内 `data/`、`docs/`、`dist/*.zip`；**仓库外同级目录** `../b50_*`、`../b15_out`、`../b20_out`；`.workbuddy/memory/` 运行日志 |
| 结论 | **原始 48 只主结果恢复成功，且哈希与既有文档记录一致** → 「48 只数字不可复核」这一条**现已解除** |
| 仍缺 | 逐窗收益明细、逐窗选参、**涨跌停修复版（v3）结果** → 由本审计重跑补齐（不伪造） |

---

## 0. 关键结论

1. **`../b50_dist/step75_research_summary.json` 就是 v2 主结果**：288 条记录、`design_version=oos75-v2`、
   `run_at=2026-10-17T23:50:05+08:00`，sha256 = `f677e24baa4c11f0…`，
   **与 B20 文档记录的 `source.summary_sha256` 完全一致**（`f677e24baa4c11f0…`）→ 哈希链自证。
2. **48 只行情数据可验证**：`b50_dist` 的 48 个 `{code}.qfq.csv` 与 `data/_backup_step75_baseline_20261020/raw/`
   **逐字节一致 48/48** → 备份目录是原始数据的忠实副本，可作为重跑输入。
3. **「b50_v3」不是涨跌停修复版**（见 §4）→ B18 承诺的 v3 修复结果**至今从未产出**，必须重跑。
4. **逐窗明细从未落盘**（B20 §3 已自述）→ P0-4 的配对比较只能靠重跑生成，**不能用汇总数字反推**。

## 1. 恢复清单

| ID | 产物 | 状态 | 校验 | 缺失影响 |
|---|---|---|---|---|
| A01 | v2 主结果（288 组） | **RECOVERED** | n_results=288; design_version=oos75-v2; oos=75; run_at=2026- | 无法独立复核 48 只核心数字 |
| A02 | v2 早期汇总（288 组，无 window_stats） | **RECOVERED** | n_results=288; design_version=oos75-v2; oos=75; run_at=2026- | 无法独立复核 48 只核心数字 |
| A03 | v1 结果（288 组，OOS=50） | **RECOVERED** | n_results=288; design_version=None; oos=50; run_at=2026-10-1 | 无法独立复核 48 只核心数字 |
| A04 | 目录名为 b50_v3 的汇总（96 组，单费率） | **RECOVERED** | n_results=96; design_version=None; oos=75; run_at=2026-10-17 | 无法独立复核 48 只核心数字 |
| A05 | 48 只行情 CSV（48 个） | **RECOVERED** | 48 只全部存在；与 backup raw 逐字节一致 48/48 | 无法复现 v2/v3（必须重新抓取，数据可能已变） |
| A06 | 被数据闸门剔除的 2 只（000568.SZ,600809.SH） | **RECOVERED** | CSV 存在但未进入结果（data_quality_gate: invalid_price） | 池边界不可解释 |
| A07 | 6 只手挑池 CSV（B15 口径） | **RECOVERED** | 6 只目录存在（含 300750 被闸门拦） | 6 只对照不可复现 |
| A08 | B1 买入持有逐窗对照（聚合） | **RECOVERED** | 含 aggregates/groups；**不含逐窗明细**（B20 自述 paired=False） | 无法做逐窗配对（本审计重算 B1） |
| A09 | 运行日志 | **RECOVERED** | 73KB，可辅助确认运行参数与耗时 | 无法核对运行细节 |
| A10 | 48 只**逐窗收益明细** | **UNAVAILABLE** | 原始运行只落 window_stats 聚合（p10..p90/亏窗占比） | P0-4 配对比较无法基于原始产物完成 |
| A11 | **逐窗参数选择记录** | **UNAVAILABLE** | 仅有 param_dist 频次分布，无窗口级选参 | 无法核对换参率 0.667 的来源 |
| A12 | **涨跌停修复版（v3）48 只主结果** | **UNAVAILABLE** | A04 的 b50_v3 早于修复（run_at 22:26 < 修复 10-18），且仅单费率 96 组 | **v3 是否改变结论无法回答** |

完整路径与 sha256 见 `reports/MA_CROSS_ARTIFACT_MANIFEST.csv`。

## 2. 验证链（六项全过）

| # | 检查 | 结果 | 实测 |
|---|---|---|---|
| 1 | A01 记录数 = 288 | ✅ PASS | 288 |
| 2 | A01 股票数 = 48 | ✅ PASS | 48 |
| 3 | A01 skipped = 2 | ✅ PASS | 2 |
| 4 | A01 design_version = oos75-v2 | ✅ PASS | oos75-v2 |
| 5 | A01 sha256 = B20 记录的 source.summary_sha256 | ✅ PASS | f677e24baa4c11f0 |
| 6 | A01 复算 B18 §1.4 三档中位数与亏损窗占比 | ✅ PASS | 3 |

**复算明细**（从恢复的 JSON 现算，与 B18 §1.4 对照）：

| 策略 / 费率 | n | median_ret 中位 | 亏损窗占比中位 | B18 记录 |
|---|---:|---:|---:|---|
| ma_cross / simulation | 48 | -2.15% | 0.565 | −2.15% / 0.565 |
| ma_cross / broker_ref | 48 | -2.13% | 0.565 | −2.13% / 0.565 |
| ma_cross / high_cost | 48 | -3.11% | 0.609 | −3.11% / 0.609 |
| mean_reversion / simulation | 48 | +0.70% | 0.435 | +0.70% / 0.435 |
| mean_reversion / broker_ref | 48 | +0.72% | 0.435 | +0.72% / 0.435 |
| mean_reversion / high_cost | 48 | +0.03% | 0.445 | +0.03% / 0.445 |

> 六格**全部复现**。这说明：B18/B20 的 48 只核心数字**确实来自这份 JSON**，不存在"文档数字无出处"的问题。

## 3. 四份 summary 的关系（避免混用）

| 目录 | run_at | design_version | OOS | n | 定位 |
|---|---|---|---|---|---|
| `b50_out` | 2026-10-17T21:56:49+08:00 | None | 50 | 288 | v1（OOS=50），**与 v2 不可比** |
| `b50_v3` | 2026-10-17T22:26:04+08:00 | None | 75 | 96 | 单费率试跑，**非涨跌停修复版** |
| `b50_oos75` | 2026-10-17T23:04:42+08:00 | oos75-v2 | 75 | 288 | v2 早期版（无 window_stats） |
| **`b50_dist`** | **2026-10-17T23:50:05+08:00** | **oos75-v2** | **75** | **288** | **v2 主结果（B18/B20 引用）** |

## 4. ⚠️ 命名陷阱：「b50_v3」不是 v3

- `b50_v3/step75_research_summary.json` 的 `run_at = 2026-10-17T22:26:04+08:00`，**早于**涨跌停修复
  （修复记录在 `docs/B18_RESEARCH_VERDICT` §3.10 D2，2026-10-18；数据摄取时间 2026-10-18T20:49）。
- 它只有 **96 条**记录 = 48 × 2 策略 × **1 档费率（simulation）**，且 `design_version` 为空。
- `b20_out` 的 `window_generation.price_limit_primary = null` 亦确认 v2 口径**未启用涨跌停**。
- **结论**：目录名里的 "v3" 指早期实验批次编号，与本报告语境下的 `DESIGN_VERSION=oos75-v3`（启用涨跌停）**不是一回事**。
  直接拿它当"修复版结果"会得出错误结论。

## 5. 仍缺的三项与处置

| 项 | 状态 | 处置（本审计执行） |
|---|---|---|
| 逐窗收益明细 | UNAVAILABLE（原运行未落盘） | 用 `DEEPQUANT_W1_OUT` 重跑生成 `windows/*.csv`；**不从汇总反推** |
| 逐窗参数选择 | UNAVAILABLE（仅 param_dist） | 重跑产出 `params_json`，可核对换参率 |
| **v3（涨跌停）48 只结果** | **从未产出** | 重跑 `DEEPQUANT_PRICE_LIMIT=board`，同池同窗同费率 |

## 6. 本阶段未做的事（合规声明）

- 未修改 `data/step75/research.py`、`universe/csi300_50.json` 或任何 B 线文档；
- 未覆盖 `b50_*` / `b15_out` / `b20_out` 中任何文件（只读）；
- 未用汇总数字生成任何"逐窗明细"；
- 未把 `b50_v3` 冒充为涨跌停修复版。

## 7. 复现方式

```bash
# 1) 清单与哈希（只读）
python scripts/ma_cross_audit/p00_baseline_snapshot.py
python scripts/ma_cross_audit/p02_artifact_recovery.py
# 2) 搭审计工作区（复制 48 只 CSV，固定池）
python scripts/ma_cross_audit/p01_prepare_workspace.py
```
