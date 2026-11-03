# L3u 引用规范登记：27 条结论的判定统计量核对与可引用强度

> 预登记 `docs/gates/l3u_prereg.json` sha256 `3510c5670aef…`（脚本启动校验）｜审计对象 `reports/gates/H-L3T-RESOLUTION/l3t_registry.json`｜**只读审计，不重跑实验**

## 一、结论先行

**27 条登记行里，12 条的「登记值」不是该实验判定时用的统计量。**其中 10 条取错了列或窗口，2 条样本数不对（窗口不同）。另有 13 条的**判定字符串在转写时失真**，包括一条语义反转（`INCREMENTAL_ABSORBED` 被写成 `MAX5_INCREMENTAL`）。

所以 L3t 报告里的 verdict 列与 below_family_best 名单**不能直接引用**；本文件给出更正后的版本。L3t 原始产物按 R5 保留未改。

## 二、核对结果：27 条逐条

| 条目 | 族 | 核对 | 判定原文 | L3t 记录 | 登记 mean | 判定 mean | MDE（重算） | 分级 |
|---|---|---|---|---|---|---|---|---|
| `gate1_d` | A_cross_spread | 判定统计量不一致（仅 t） | failure | INCONCLUSIVE | -0.396pp | -0.396pp | 1.822pp | 核对未通过，不参与分级 |
| `gate2_d` | A_cross_spread | 判定统计量不一致（仅 t） | FAILURE | FAIL | -0.385pp | -0.385pp | 0.676pp | 核对未通过，不参与分级 |
| `gate3_d` | A_cross_spread | 样本数不一致 | SUCCESS | PASS | -0.398pp | +0.484pp | 0.595pp | 核对未通过，不参与分级 |
| `gate4_d` | A_cross_spread | 判定统计量不一致（仅 t） | FAILURE | FAIL | +0.166pp | +0.166pp | 0.481pp | 核对未通过，不参与分级 |
| `gate5_d` | A_cross_spread | 判定统计量不一致（仅 t） | FAILURE | FAIL | +0.024pp | +0.024pp | 0.426pp | 核对未通过，不参与分级 |
| `gate6_d` | A_cross_spread | 判定统计量不一致（仅 t） | SUCCESS | PASS | +0.708pp | +0.708pp | 0.427pp | 核对未通过，不参与分级 |
| `phasec_d` | A_cross_spread | 判定统计量不一致 | POOL_EFFECT_CONFIRMED | POOL_EFFECT_CONFIRMED | +0.014pp | +1.939pp | 0.853pp | 核对未通过，不参与分级 |
| `phasec2_d` | A_cross_spread | 判定统计量不一致 | SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT | POOL_EFFECT_CONFIRMED | +0.658pp | +0.107pp | 0.060pp | 核对未通过，不参与分级 |
| `phasec3_d` | A_cross_spread | 判定统计量不一致 | LAG_COST_NEGLIGIBLE | POOL_EFFECT_CONFIRMED | +0.658pp | +0.047pp | 0.220pp | 核对未通过，不参与分级 |
| `phasee_alpha` | B_long_only | 一致 | LONGONLY_ALPHA_NOT_DETECTED | SINGLE_SIDE_NOT_DETECTED | +0.277pp | +0.277pp | 0.408pp | 只能说「无法判断」 |
| `phasef_delta` | A_cross_spread | 一致 | MAX5_EFFECT_CONFIRMED | MAX5_EFFECT_CONFIRMED | +0.740pp | +0.740pp | 0.609pp | 可引用，须标 MDE |
| `phaseg_alpha` | B_long_only | 一致 | MAX5_LONGONLY_NOT_EXECUTABLE | NOT_EXECUTABLE | +0.181pp | +0.181pp | 0.288pp | 只能说「无法判断」 |
| `phasel_alpha` | B_long_only | 一致 | SURGE_AVOID_ABSORBED | SURGE_AVOID_ABSORBED | +0.106pp | +0.106pp | 0.095pp | 可引用为「确认无效应」 |
| `phasem_spread` | A_cross_spread | 该实验无单一判定均值 | SHORT_SIDE_REJECTED | SHORT_SIDE_REJECTED | +0.722pp | 不适用 | 0.709pp | 可引用为「确认无效应」 |
| `phasej_netA` | B_long_only | 判定统计量不一致 | NET_AFTER_EXECUTION_ABSORBED | NET_ABSORBED | +0.032pp | +0.042pp | 0.087pp | 核对未通过，不参与分级 |
| `phasek_m1net` | B_long_only | 判定统计量不一致（仅 t） | LOW_TURNOVER_INSUFFICIENT | LOW_TURNOVER_INSUFFICIENT | +0.040pp | +0.040pp | 9.356pp | 核对未通过，不参与分级 |
| `phaseo_netBASE` | B_long_only | 一致 | EXECUTABLE_UNDER_BASE_FEES | EXECUTABLE_UNDER_BASE_FEES | +0.136pp | +0.136pp | 9.431pp | 点估计不可信 |
| `phasep_late` | C_regression | 样本数不一致 | SEGMENT_DIFFERENCE_NOT_DETECTED | SEGMENT_DIFF_NOT_DETECTED | +0.129pp | +0.129pp | 0.140pp | 核对未通过，不参与分级 |
| `phaser_alpha` | B_long_only | 该实验无单一判定均值 | SURGE_SCALE_DEPENDENT | SURGE_SCALE_DEPENDENT | +0.106pp | 不适用 | 0.095pp | 核对未通过，不参与分级 |
| `phases_net` | B_long_only | 一致 | MODERN_ERA_ALPHA_DETECTED | MODERN_ERA_ALPHA_DETECTED | +0.111pp | +0.111pp | 14.045pp | 点估计不可信 |
| `phaset_net` | B_long_only | 一致 | T_FRAGILE | T_FRAGILE | +0.111pp | +0.111pp | 14.045pp | 点估计不可信 |
| `phasev_net` | B_long_only | 该实验无单一判定均值 | COST_CONVENTION_SENSITIVE | COST_CONVENTION_SENSITIVE | +0.136pp | 不适用 | 9.431pp | 核对未通过，不参与分级 |
| `phaseq_max5` | C_regression | 一致 | INCREMENTAL_ABSORBED | MAX5_INCREMENTAL | +0.042pp | +0.042pp | 0.468pp | 只能说「无法判断」 |
| `l3r_delta` | A_cross_spread | 该实验无单一判定均值 | INCONCLUSIVE_POWER | INCONCLUSIVE_POWER | -0.329pp | 不适用 | 0.955pp | 只能说「无法判断」 |
| `l3s_ic` | D_ic | 该实验无单一判定均值 | IC_INCONCLUSIVE_POWER | IC_INCONCLUSIVE_POWER | -0.0232 | 不适用 | 0.0382 | 只能说「无法判断」 |
| `l3_test` | E_fold | 该实验无单一判定均值 | L3_BLOCKED_PREREQUISITE | L3_BLOCKED_PREREQUISITE | 3.9953 | 不适用 | 7.8915 | 只能说「无法判断」 |
| `l3p_test` | E_fold | 该实验无单一判定均值 | L3P_BLOCKED_PREREQUISITE | L3P_BLOCKED_PREREQUISITE | 5.2844 | 不适用 | 7.7237 | 只能说「无法判断」 |

## 三、口径错配的逐条说明

### `gate1_d` — 判定统计量不一致

- 重建的判定序列：`gate1_out/032a11f591553e25/monthly_paired.csv::d`
- L3t 登记：n=48、mean=-0.396106
- 判定统计量：n=48、mean=-0.396106
- 差异：mean 一致但 t 不同：L3t=-0.6092 vs 判定=-0.5687（多为 NW 滞后或估计量口径差异）

### `gate2_d` — 判定统计量不一致

- 重建的判定序列：`gate2_out/c440c77d284a/monthly_paired.csv::d`
- L3t 登记：n=125、mean=-0.384578
- 判定统计量：n=125、mean=-0.384578
- 差异：mean 一致但 t 不同：L3t=-1.5946 vs 判定=-1.6415（多为 NW 滞后或估计量口径差异）

### `gate3_d` — 样本数不一致

- 重建的判定序列：`gate3_out/9b7235d77689/monthly_paired.csv::d`
- L3t 登记：n=125、mean=-0.398187
- 判定统计量：n=108、mean=+0.484148
- 差异：n: L3t=125 vs 判定=108；mean: -0.3982 vs +0.4841

### `gate4_d` — 判定统计量不一致

- 重建的判定序列：`gate4_out/94988f7c57d1/monthly_paired.csv::d`
- L3t 登记：n=237、mean=+0.166361
- 判定统计量：n=237、mean=+0.166360
- 差异：mean 一致但 t 不同：L3t=+0.9689 vs 判定=+0.8679（多为 NW 滞后或估计量口径差异）

### `gate5_d` — 判定统计量不一致

- 重建的判定序列：`gate5_out/7c0c89916b0c/monthly_paired.csv::d`
- L3t 登记：n=249、mean=+0.023588
- 判定统计量：n=249、mean=+0.023588
- 差异：mean 一致但 t 不同：L3t=+0.1550 vs 判定=+0.1552（多为 NW 滞后或估计量口径差异）

### `gate6_d` — 判定统计量不一致

- 重建的判定序列：`gate6_out/b8c4f1ecba44/monthly_paired.csv::d`
- L3t 登记：n=237、mean=+0.708446
- 判定统计量：n=237、mean=+0.708446
- 差异：mean 一致但 t 不同：L3t=+4.6443 vs 判定=+4.3861（多为 NW 滞后或估计量口径差异）

### `phasec_d` — 判定统计量不一致

- 重建的判定序列：`phasec_out/3a01759a37f0/five_cells.csv::+1×d[B] + -1×d[A]`
- L3t 登记：n=237、mean=+0.014071
- 判定统计量：n=237、mean=+1.938968
- 差异：mean: L3t=+0.014071 vs 判定=+1.938968（差 -1.924897）

### `phasec2_d` — 判定统计量不一致

- 重建的判定序列：`phasec2_out/953c32fdc556/cells.csv::+1×spread_v1 + -1×spread`
- L3t 登记：n=237、mean=+0.658190
- 判定统计量：n=237、mean=+0.106944
- 差异：mean: L3t=+0.658190 vs 判定=+0.106944（差 +0.551245）

### `phasec3_d` — 判定统计量不一致

- 重建的判定序列：`phasec3_out/6a76a10687b4/cells.csv::+1×spread_v1 + -1×spread_v0`
- L3t 登记：n=237、mean=+0.658190
- 判定统计量：n=237、mean=+0.046804
- 差异：mean: L3t=+0.658190 vs 判定=+0.046804（差 +0.611385）

### `phasej_netA` — 判定统计量不一致

- 重建的判定序列：`phasej_out/f76de8a277f6/monthly.csv::net_A_pp`
- L3t 登记：n=237、mean=+0.032441
- 判定统计量：n=237、mean=+0.042423
- 差异：mean: L3t=+0.032441 vs 判定=+0.042423（差 -0.009982）

### `phasek_m1net` — 判定统计量不一致

- 重建的判定序列：`phasek_out/3bc31a35903d/monthly.csv::m1_net`
- L3t 登记：n=237、mean=+0.039891
- 判定统计量：n=None、mean=+0.039891
- 差异：mean 一致但 t 不同：L3t=+1.1946 vs 判定=+1.3469（多为 NW 滞后或估计量口径差异）

### `phasep_late` — 样本数不一致

- 重建的判定序列：`phasep_out/3d19f7508e10/monthly.csv::net_BASE_3000w_pp`
- L3t 登记：n=129、mean=+0.129408
- 判定统计量：n=237、mean=+0.129408
- 差异：n: L3t=129 vs 判定=237；mean: +0.1294 vs +0.1294

## 四、判定字符串的转写失真

| 条目 | 原文（判定） | L3t 记录 | 性质 |
|---|---|---|---|
| `gate1_d` | `failure` | `INCONCLUSIVE` | 改写 |
| `gate2_d` | `FAILURE` | `FAIL` | 缩写/截断 |
| `gate3_d` | `SUCCESS` | `PASS` | 改写 |
| `gate4_d` | `FAILURE` | `FAIL` | 缩写/截断 |
| `gate5_d` | `FAILURE` | `FAIL` | 缩写/截断 |
| `gate6_d` | `SUCCESS` | `PASS` | 改写 |
| `phasec2_d` | `SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT` | `POOL_EFFECT_CONFIRMED` | 改写 |
| `phasec3_d` | `LAG_COST_NEGLIGIBLE` | `POOL_EFFECT_CONFIRMED` | 改写 |
| `phasee_alpha` | `LONGONLY_ALPHA_NOT_DETECTED` | `SINGLE_SIDE_NOT_DETECTED` | 改写 |
| `phaseg_alpha` | `MAX5_LONGONLY_NOT_EXECUTABLE` | `NOT_EXECUTABLE` | 缩写/截断 |
| `phasej_netA` | `NET_AFTER_EXECUTION_ABSORBED` | `NET_ABSORBED` | 改写 |
| `phasep_late` | `SEGMENT_DIFFERENCE_NOT_DETECTED` | `SEGMENT_DIFF_NOT_DETECTED` | 缩写/截断 |
| `phaseq_max5` | `INCREMENTAL_ABSORBED` | `MAX5_INCREMENTAL` | **语义反转**（原文为「被吸收＝未成立」） |

> 判定字符串不是装饰：`ABSORBED` 与 `CONFIRMED` 是相反的意思。本文件的所有分级一律以**原文**为准。

## 五、更正后的分辨率

| 量 | L3t | 本文件（重建判定序列后） |
|---|---|---|
| pp 口径内 MDE 极差 | 20.89× | 235.08× |
| pp 口径内 MDE 最小 | 0.087pp | 0.060pp |
| pp 口径内 MDE 最大 | 1.822pp | 14.045pp |

极差基本没变，说明 L3t 的**族内排序**大体成立；变的是个别条目的归属——阶段 C 三条改用配对差后 MDE 从 0.42~0.45pp 降到 0.06~0.85pp，其中 phasec2 的 0.0597pp 成为 pp 口径内的新最小值。

## 六、更正后的「低于同族最好分辨率」名单

| 条目 | 族 | 效应量 | 同族最好 MDE | 比值 |
|---|---|---|---|---|
| `phaseq_max5` | C_regression | +0.042pp | 0.468pp | 0.09 |

L3t 原名单 5 条，更正后 1 条。读法不变：**不是这些结论错**，而是「换同族更强的设计去测同样大小的效应，可能检不出」。

## 七、可引用强度分级

| 分级 | 含义 | 条目数 | 条目 |
|---|---|---|---|
| QUOTABLE_MARGINAL | 可引用，须标 MDE | 1 | `phasef_delta` |
| NOT_QUOTABLE | 点估计不可信 | 3 | `phaseo_netBASE`, `phases_net`, `phaset_net` |
| NULL_CONFIRMED | 可引用为「确认无效应」 | 2 | `phasel_alpha`, `phasem_spread` |
| UNDERPOWERED | 只能说「无法判断」 | 7 | `l3_test`, `l3p_test`, `l3r_delta`, `l3s_ic`, `phasee_alpha`, `phaseg_alpha`, `phaseq_max5` |
| NOT_GRADED | 核对未通过，不参与分级 | 14 | `gate1_d`, `gate2_d`, `gate3_d`, `gate4_d`, `gate5_d`, `gate6_d`, `phasec2_d`, `phasec3_d`, `phasec_d`, `phasej_netA`, `phasek_m1net`, `phasep_late`, `phaser_alpha`, `phasev_net` |

> **怎么用**：`QUOTABLE` 可以引点估计；`QUOTABLE_MARGINAL` 引的时候必须同时给 MDE；`DIRECTION_ONLY` 只能写方向、不能写大小；`NOT_QUOTABLE` 的点估计不可信。未检出类里 `UNDERPOWERED` **不得**写成「没有效应」——按项目铁律，功效不足 ≠ 无效应。

## 八、逐条核对预登记预测

| 编号 | 预测 | 实际 | 被反驳 |
|---|---|---|---|
| P1 | 判定字符串与 L3t 记录完全一致的条目 ≥ 18/27 | 14/27 一致；不一致 13 条 | **是** |
| P2 | 判定统计量与 L3t 行一致的条目 ≥ 18/27 | 8/27 一致（MATCH） | **是** |
| P3 | 需要重建序列（非单列）的条目恰好 3 条 | 3 条（phasec_d, phasec2_d, phasec3_d） | 否 |
| P4 | 更正后 pp 口径内 MDE 极差 < 20.9× | 235.08×（L3t 原值 20.89×） | **是** |
| P5 | 更正后 below_family_best 条目数 ≤ 3（L3t 为 5 条） | 1 条 | 否 |
| P6 | detected=true 中 ratio ≥ 2（QUOTABLE）的 ≥ 3 条 | 0 条（无） | **是** |
| P7 | UNVERIFIABLE 条目数为 0 | 0 条 | 否 |

## 九、限制

- 本次只核对「登记值 vs 判定值」，不重跑实验；MDE 是在**重建后的判定序列**上用 L3t 同一实现重算的。
- phasem / phaser / phasev / l3r / l3s / l3 / l3p 七条的判定不是单一均值（区间判定 / 多门槛 / 自持有序列），标记为 NOT_APPLICABLE，只核对了它们的序列与 MDE，未做 mean/t 对比。
- 分级阈值（2×/1×/0.5× 与功效 0.8/0.5）在预登记中固定，未因结果调整；落在边界附近的条目按数值原样报告。
- 「可引用强度」是**引用规范**，不是对结论对错的重新判定；L3t 与各阶段原判定字符串均未被修改。
- phasej 的判定档是 3000 万，而 CSV 里的 net_A_pp 对应 1000 万档；本实验按预登记比对两者的差异，不改用另一档重算（那属于新实验）。

## 十、复现

```bash
PYTHONPATH="backend/src" python scripts/l3u_citation.py
```

耗时 0.1 秒；27 条全部读自仓库内既有产物，无网络、无重跑。
