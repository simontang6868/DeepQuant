# MA-CROSS 独立复核 P0-2：v2 主结果复现

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-27（GMT+8） |
| 目标 | 用**恢复的原始数据 + 原始代码**重新生成 48 只 / 三档费率 / 全窗口结果，并与原报告逐项对账 |
| 判定 | **`V2_FULLY_REPRODUCED`（2592/2592 字段逐位一致）** |
| 输入哈希 | 原始主结果 `f677e24baa4c11f0…`（与 B20 记录的 `source.summary_sha256` 一致） |
| 运行方式 | `DEEPQUANT_PRICE_LIMIT=off` + `DEEPQUANT_W1_OUT=…`（**未修改任何代码**） |

---

## 0. 结论

1. **288 个结果单元 × 9 个关键字段 = 2,592 项，全部逐位相同**（不是"接近"，是 `==`）。
   复现字段：`n_windows / n_sel_ok / n_oos_ok / positive_ratio / median_ret / median_mdd / total_trades / total_fees / transition_rate`。
2. **B18 §1.4 的六格汇总数全部复现**：ma_cross −2.15/−2.13/−3.11%，亏损窗占比 0.565/0.565/0.609；mean_reversion +0.70/+0.72/+0.03%，0.435/0.435/0.445。
3. **因此，"48 只数字不可复核"这一条正式解除**：原始产物存在、哈希自洽、重跑可 100% 复现。
4. **一个副作用（重要）**：本次开启了 `capture_trades`（逐窗明细），结果仍与未采集的原始运行**逐位相同** →
   证明"采集明细不改变任何指标"，也说明 P0-4 的配对比较用的是与主结果同一批窗口。

---

## 1. 复现口径（与 v2 的逐项对应）

| 维度 | 原始 v2 | 本次复现 | 是否一致 |
|---|---|---|---|
| 股票池 | universe 50 → 数据闸门剔 2 → **48** | `universe48.json` **固定 48 只**（见 §4 偏差 1） | 一致（成员相同） |
| 被剔标的 | 000568.SZ、600809.SH（`invalid_price`） | 同样不在池内 | 一致 |
| 区间 | 2017-01-01 ~ 2024-12-31 | 同 | 一致 |
| 复权 | qfq（腾讯源） | 同一批 CSV（**逐字节一致 48/48**） | 一致 |
| 训练 / OOS / 步长 | 150 / 75 / 75 | 同 | 一致 |
| `min_trades` | 2 | 2 | 一致 |
| 初始资金 | 100,000 | 100,000 | 一致 |
| 参数网格 | fast{3,5,10,20} × slow{15,25,40,60}（15 组合法） | 同（`MA_GRID`） | 一致 |
| 涨跌停 | **未启用** | `PRICE_LIMIT=off` | 一致 |
| 费率档 | 三档 | 三档 | 一致 |
| 逐窗明细 | 未落盘 | 开启（`W1_OUT`） | 新增，指标不变（§0 第 4 条） |
| 数据获取 | akshare 腾讯 K 线（2026-10-17 抓取） | **不联网**，直接用恢复的 CSV | 一致（数据同源同字节） |

---

## 2. 对账表（6 个汇总格）

| 策略 / 费率 | n | 原 `median_ret` 中位 | 复现 | 差 | 原亏损窗占比 | 复现 | 差 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ma_cross / sim | 48 | −2.15% | −2.15% | 0.00 | 0.565 | 0.565 | 0.000 |
| ma_cross / broker | 48 | −2.13% | −2.13% | 0.00 | 0.565 | 0.565 | 0.000 |
| ma_cross / high_cost | 48 | −3.11% | −3.11% | 0.00 | 0.609 | 0.609 | 0.000 |
| mean_reversion / sim | 48 | +0.70% | +0.70% | 0.00 | 0.435 | 0.435 | 0.000 |
| mean_reversion / broker | 48 | +0.72% | +0.72% | 0.00 | 0.435 | 0.435 | 0.000 |
| mean_reversion / high_cost | 48 | +0.03% | +0.03% | 0.00 | 0.445 | 0.445 | 0.000 |

逐单元明细（288 行 × 18 列，含每个字段的原值/复现值/是否相等）：
`reports/MA_CROSS_V2_RECONCILIATION.csv`；逐窗明细：`reports/MA_CROSS_V2_WINDOW_RESULTS.csv`（2,982 行，ma_cross+mean_reversion，三档费率）。

---

## 3. 复现过程中发现的两个可追溯性问题

### 3.1 `design_version` 硬编码，与实际涨跌停模式无关（**影响分组**）

- `data/step75/research.py`：`DESIGN_VERSION = "oos75-v3"` 是模块常量；
- 本次以 `PRICE_LIMIT=off`（=v2 口径）运行，但每条逐窗记录仍写 `design_version = oos75-v3`；
- 只有 `price_limit_mode = off` 字段能区分真实口径。

**后果**：任何"按 design_version 分组统计"的脚本都会把 v2 与 v3 **静默合并**。本次对账因此**不使用** `design_version`，只用 `price_limit_mode` + 文件来源。

### 3.2 池必须显式固定，否则复现会变味

`research.py` 的行为是：CSV 不存在 → 联网抓取。若不固定 48 只，被闸门剔除的 2 只（000568、600809）会被重新抓取；若今日数据已修复，它们会进入池 → **池变成 50 只**，"复现"就变成了"换池重跑"。

本次因此生成了 `ma_cross_audit/universe48.json`（从原始结果的 48 个 symbol 反推，`meta` 里写明来源与剔除原因）。

---

## 4. 与原始运行的四处偏差（全部登记）

| # | 偏差 | 原因 | 影响 |
|---|---|---|---|
| 1 | 池以 `universe48.json` **固定 48 只**，而非"50 只 + 运行时闸门" | 防止联网重抓导致池变化 | 无（成员与结果完全一致，且 `n_results=288` 相同） |
| 2 | 数据来自 `b50_dist` 的 CSV 副本（非重新抓取） | 保证同源 | 无（与 backup raw 逐字节一致 48/48） |
| 3 | 开启 `capture_trades`（`W1_OUT`） | 需要逐窗明细做 P0-4 | 无（2592 项逐位相同即为证据） |
| 4 | 未调用 akshare | 离线可复现 | 无（数据版本被哈希固定，比"再抓一次"更可复现） |

---

## 5. 复现步骤（可直接重跑）

```bash
cd D:/DeepQuant power by txm/DeepQuant-main
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# 1) 恢复清单（只读）+ 搭工作区（复制 48 只 CSV、固定池）
$PY -u scripts/ma_cross_audit/p02_artifact_recovery.py
$PY -u scripts/ma_cross_audit/p01_prepare_workspace.py

# 2) v2 复现（PRICE_LIMIT=off），约 25 分钟
DEEPQUANT_UNIVERSE="$PWD/ma_cross_audit/universe48.json" \
DEEPQUANT_DATA_ROOT="$PWD/ma_cross_audit/data_v2" \
DEEPQUANT_PRICE_LIMIT=off \
DEEPQUANT_W1_OUT="$PWD/ma_cross_audit/runs/v2/windows" \
PYTHONPATH="$PWD/backend/src" \
$PY -u data/step75/research.py

# 3) 对账
$PY -u scripts/ma_cross_audit/p03_analyze.py
```

---

## 6. 未做与不做

- **未**修改 `research.py` / 引擎 / 策略 / 池定义；
- **未**覆盖 `b50_dist` 任何文件（哈希与 mtime 均未变，见测试 `test_11`）；
- **未**用汇总数字反推任何逐窗数据；
- **未**因为"要对齐"而调整任何参数（R2/R11 禁调参）。
