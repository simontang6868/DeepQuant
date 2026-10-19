# 阶段 0a 预登记 v1.1（数据层修订）

> **状态：`FROZEN_BEFORE_RESULTS`**
> 冻结时间 2026-10-19 21:35（**以文件 mtime 为准**；json 里 `frozen_at` 手写为 21:55，属笔误，见文末勘误）。
> 机器可读：`docs/stage0_prereg_v1.1_2026-10-19.json`（sha256 `504c0dbd…`）
> 取代：`docs/stage0_prereg_2026-10-19.json` v1.0（sha256 `8e35d762…`，**原文一字未改，保留作审计痕迹**）

> **勘误（2026-10-19 22:10）**：json 的 `frozen_at` 写的是 `21:55`，实际落盘时刻是 `21:35`（`ls -l docs/stage0_prereg_v1.1_2026-10-19.json`）。
> 该字段写错**不影响任何判定**，但为了不给后来者留下"冻结晚于结果"的想象空间，在此声明以 mtime 为准。
> 相关时序：21:35 冻结 v1.1 → 21:41 乘性主臂完成 → 21:45 另一进程按 v1.0 跑完加性全量（我 22:0x 才发现，未读取）→ 21:51 v1.1 加性臂完成。
> 两份加性结果逐窗口完全一致（4,961 窗口 0 差异），见 `docs/STAGE0_VERDICT_2026-10-19.md` §5.1。

---

## 1. 为什么有 v1.1

v1.0 冻结之后、任何结果出来之前，发现**行情口径缺陷**：
腾讯/东财的 `qfq` 是加性口径 `qfq_t = raw_t − 未来累计分红_t`，累计分红超过历史价位时价位趋零甚至变负
（`300750.SZ` 2018-06-11 `close = −2.07`；`600809.SH` 2018-11-26 `close = −0.46`；`000568.SZ` 2016 段最低 `−12.98`）。
价位过零后日收益率的分母失去意义，且误差随「持有期长度 × 分红率」单调放大 —— 恰好与「在市 vs 退市」分组相关。
详细证据见 `docs/QFQ_ADDITIVE_ARTIFACT_2026-10-19.md`。

**改口径前看过什么**（必须交代，否则"先冻结后跑"就是空话）：

| 冒烟 | 内容 | 是否产生组间统计量 |
|---|---|---|
| ① | `--only B --limit 3`（000748/600005/000033） | 否。三只全是 `too_short`，`valid_windows = 0` |
| ② | `--only A --limit 5`（4 只出 90 个窗口、000568 被闸门拦） | 否。只有 A 组单边分布，没有 B 组、没有检验 |

两次冒烟都没有产出可解读的裁定，因此本次修订不构成"看到结果再改设计"。

## 2. 改了什么

**只有一处**：行情层从加性前复权换成乘性前复权。

```
qfq_mult_t = hfq_t × (raw_anchor / hfq_anchor)
```

`hfq`（后复权）本身乘性，其收益率即正确的前复权收益率；缩放只改水平值，不改任何收益率。
脚本 `scripts/repair_qfq_mult.py`，逐只 `scale / 行数 / sha256` 见 `docs/QFQ_MULT_MANIFEST_2026-10-19.json`。

## 3. 没改什么（逐条对上 v1.0 §2 ~ §3）

- 两组定义与数量：A = csi300 50 只样本；B = 2017 年后退市全量 262 只
- 策略 `ma_cross`；网格 fast(3,5,10,20) × slow(15,25,40,60)、`min_train=150`、`min_oos=75`、`min_trades=2`、`max_combinations=50`
- 费率 `simulation_placeholder`、初始资金 100,000、区间 2017-01-01~2024-12-31、`board` 涨跌停
- 观测单元 = 每只每个 walk-forward 窗口的 `cumulative_return`，须满足 `selection_success & oos_success & final_equity 非 None & oos_is_complete`
- 检验 = Mann-Whitney U 单侧（B<A），α = 0.05，效应量 = `median(B) − median(A)`
- 判定 S0 / S1 / S2 / S3 四条及其优先级、`S3` 的固定措辞
- 预登记 §4 五项必报项、§5 五条已知局限

## 4. 新增的两条必报项

1. **加性 vs 乘性口径对照**（同一批标的、同一区间，两套口径的买入持有收益差）——见审计 json
2. **两臂都落盘**：`docs/STAGE0_RESULTS_MULT_2026-10-19.json`（主）与 `docs/STAGE0_RESULTS_LEGACY_2026-10-19.json`（敏感性）。
   判定若一致 → 结论对口径稳健；若不一致 → 以乘性为主，结论必须写明分歧。

## 5. 已知代价

13 只退市股（11 只 hfq 锚点非正、2 只腾讯无 hfq）与 1 只在市股（688981.SH 腾讯无 hfq）无法生成乘性口径，
**整只缺席，不做任何填充**，逐条进 `skipped`。缺席偏向"更早退市、更差"的一端 → 方向上是**低估**组间差。

## 6. 判据（与 v1.0 逐字相同，此处只复述）

| 编号 | 判定 | 条件 |
|---|---|---|
| S0 | `INSUFFICIENT_DATA` | 任一组有效窗口 < 30 |
| S1 | `SURVIVORSHIP_MATERIAL` | p<sub>单侧</sub> < 0.05 且 median(B) < median(A) |
| S2 | `SURVIVORSHIP_REVERSED` | p<sub>双侧</sub> < 0.05 且 median(B) > median(A) |
| S3 | `SURVIVORSHIP_IMMATERIAL` | 以上皆不满足（**不等于"偏差不存在"**） |
