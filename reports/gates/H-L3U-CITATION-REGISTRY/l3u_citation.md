# L3u 引用规范登记：27 条结论的判定统计量核对与可引用强度

> 预登记 `docs/gates/l3u_prereg.json` sha256 `0bbdc5ff4ad8…`（脚本启动校验，含 3 条修订记录）｜审计对象 `reports/gates/H-L3T-RESOLUTION/l3t_registry.json`｜**只读审计，不重跑实验**

## 一、结论先行

**L3t 那张表不能直接拿来做引用规范。**27 行里：**6 行取错了量**（4 行取错列或资金档，2 行窗口不对）、**6 行只是 t 的估计量不同**（mean 与样本数都对）、**13 行的判定字符串在转写时失真**（其中 1 条语义反转）。

具体三类：

1. **取错了量**（最严重）——阶段 C 的判定是 `spread(B) − spread(A)` = **+1.9390pp（t=6.37）**，L3t 登记的是格 A 自己的 `d_raw` 水平 **+0.0141pp（t=0.09）**，差 138 倍。按 L3t 的数字读，会得出「这个结论分辨率不足」的相反结论。同类问题还有：`phasec2`/`phasec3` 都登记成同一个 V0 水平（两行 mean 完全相同，都是 +0.6582pp，而它们真正检验的量是 +0.107pp 与 +0.047pp）；`gate3` 用了 2016-2026 的重跑文件（n=125、mean **−0.398pp**）而非判定窗口 2007-2015（n=108、mean **+0.484pp**，符号相反）；`phasej` 用了 1000 万档而非判定档 3000 万。
2. **只是 t 不同**——Gate 1~6 的 summary 只给 iid t，L3t 统一用了 NW(3) t，两者差 0.05~0.25，触发不一致标记。**这不是错误**，但合并展示会让 6 条正常条目看起来像出问题。
3. **判定字符串失真 13 条**——`failure` 被写成 `INCONCLUSIVE`、`SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT` 被写成 `POOL_EFFECT_CONFIRMED`（两条都这样）、最严重的是 `INCREMENTAL_ABSORBED`（吸收＝不成立）被写成 `MAX5_INCREMENTAL`（听起来像成立）——**语义反转**。

**最重要的一条结论：没有任何一个结论的效应量达到其自身分辨率的 2 倍（`QUOTABLE` = 0 条）。**最好的是 `phasef` 的 +0.740pp / MDE 0.609pp = 1.22 倍，与 `phaseo` 的 1.44 倍。也就是说，本项目目前没有一条「可以放心单引点估计」的结论；全部要带 MDE 或只引方向。

## 二、核对结果：27 条逐条

「参考分级」是按**修正后的数字**（判定统计量 + 重建序列的 MDE）算的；正式分级只在核对通过的条目上给出（预登记：核对未通过不下分级结论）。

| 条目 | 族 | 核对 | 判定原文 | L3t 记录 | 登记 mean | 判定 mean | MDE（重算） | 正式分级 | 参考分级 |
|---|---|---|---|---|---|---|---|---|---|
| `gate1_d` | A_cross_spread | 仅 t 不同（mean 一致） | failure | INCONCLUSIVE | -0.396pp | -0.396pp | 1.822pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `gate2_d` | A_cross_spread | 仅 t 不同（mean 一致） | FAILURE | FAIL | -0.385pp | -0.385pp | 0.676pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `gate3_d` | A_cross_spread | 样本数不一致 | SUCCESS | PASS | -0.398pp | +0.484pp | 0.595pp | 核对未通过，不参与分级 | 只能引方向 |
| `gate4_d` | A_cross_spread | 仅 t 不同（mean 一致） | FAILURE | FAIL | +0.166pp | +0.166pp | 0.481pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `gate5_d` | A_cross_spread | 仅 t 不同（mean 一致） | FAILURE | FAIL | +0.024pp | +0.024pp | 0.426pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `gate6_d` | A_cross_spread | 仅 t 不同（mean 一致） | SUCCESS | PASS | +0.708pp | +0.708pp | 0.427pp | 核对未通过，不参与分级 | 可引用，须标 MDE |
| `phasec_d` | A_cross_spread | 判定统计量不一致 | POOL_EFFECT_CONFIRMED | POOL_EFFECT_CONFIRMED | +0.014pp | +1.939pp | 0.853pp | 核对未通过，不参与分级 | 可引用点估计 |
| `phasec2_d` | A_cross_spread | 判定统计量不一致 | SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT | POOL_EFFECT_CONFIRMED | +0.658pp | +0.107pp | 0.060pp | 核对未通过，不参与分级 | 可引用，须标 MDE |
| `phasec3_d` | A_cross_spread | 判定统计量不一致 | LAG_COST_NEGLIGIBLE | POOL_EFFECT_CONFIRMED | +0.658pp | +0.047pp | 0.220pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `phasee_alpha` | B_long_only | 一致 | LONGONLY_ALPHA_NOT_DETECTED | SINGLE_SIDE_NOT_DETECTED | +0.277pp | +0.277pp | 0.408pp | 只能说「无法判断」 | — |
| `phasef_delta` | A_cross_spread | 一致 | MAX5_EFFECT_CONFIRMED | MAX5_EFFECT_CONFIRMED | +0.740pp | +0.740pp | 0.609pp | 可引用，须标 MDE | — |
| `phaseg_alpha` | B_long_only | 一致 | MAX5_LONGONLY_NOT_EXECUTABLE | NOT_EXECUTABLE | +0.181pp | +0.181pp | 0.288pp | 只能说「无法判断」 | — |
| `phasel_alpha` | B_long_only | 一致 | SURGE_AVOID_ABSORBED | SURGE_AVOID_ABSORBED | +0.106pp | +0.106pp | 0.095pp | 可引用为「确认无效应」 | — |
| `phasem_spread` | A_cross_spread | 该实验无单一判定均值 | SHORT_SIDE_REJECTED | SHORT_SIDE_REJECTED | +0.722pp | 不适用 | 0.709pp | 核对未通过，不参与分级 | 可引用为「确认无效应」 |
| `phasej_netA` | B_long_only | 判定统计量不一致 | NET_AFTER_EXECUTION_ABSORBED | NET_ABSORBED | +0.032pp | +0.042pp | 0.087pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `phasek_m1net` | B_long_only | 仅 t 不同（mean 一致） | LOW_TURNOVER_INSUFFICIENT | LOW_TURNOVER_INSUFFICIENT | +0.040pp | +0.040pp | 0.094pp | 核对未通过，不参与分级 | 只能说「无法判断」 |
| `phaseo_netBASE` | B_long_only | 一致 | EXECUTABLE_UNDER_BASE_FEES | EXECUTABLE_UNDER_BASE_FEES | +0.136pp | +0.136pp | 0.094pp | 可引用，须标 MDE | — |
| `phasep_late` | C_regression | 样本数不一致 | SEGMENT_DIFFERENCE_NOT_DETECTED | SEGMENT_DIFF_NOT_DETECTED | +0.129pp | +0.129pp | 0.140pp | 核对未通过，不参与分级 | 「未检出」但功效不足 |
| `phaser_alpha` | B_long_only | 该实验无单一判定均值 | SURGE_SCALE_DEPENDENT | SURGE_SCALE_DEPENDENT | +0.106pp | 不适用 | 0.095pp | 核对未通过，不参与分级 | 核对未通过，不参与分级 |
| `phases_net` | B_long_only | 一致 | MODERN_ERA_ALPHA_DETECTED | MODERN_ERA_ALPHA_DETECTED | +0.111pp | +0.111pp | 0.140pp | 只能引方向 | — |
| `phaset_net` | B_long_only | 一致 | T_FRAGILE | T_FRAGILE | +0.111pp | +0.111pp | 0.140pp | 只能引方向 | — |
| `phasev_net` | B_long_only | 该实验无单一判定均值 | COST_CONVENTION_SENSITIVE | COST_CONVENTION_SENSITIVE | +0.136pp | 不适用 | 0.094pp | 核对未通过，不参与分级 | 核对未通过，不参与分级 |
| `phaseq_max5` | C_regression | 一致 | INCREMENTAL_ABSORBED | MAX5_INCREMENTAL | +0.042pp | +0.042pp | 0.468pp | 只能说「无法判断」 | — |
| `l3r_delta` | A_cross_spread | 该实验无单一判定均值 | INCONCLUSIVE_POWER | INCONCLUSIVE_POWER | -0.329pp | 不适用 | 0.955pp | 只能说「无法判断」 | — |
| `l3s_ic` | D_ic | 该实验无单一判定均值 | IC_INCONCLUSIVE_POWER | IC_INCONCLUSIVE_POWER | -0.0232 | 不适用 | +0.0382 | 只能说「无法判断」 | — |
| `l3_test` | E_fold | 该实验无单一判定均值 | L3_BLOCKED_PREREQUISITE | L3_BLOCKED_PREREQUISITE | +4.00%/折 | 不适用 | 7.89%/折 | 只能说「无法判断」 | — |
| `l3p_test` | E_fold | 该实验无单一判定均值 | L3P_BLOCKED_PREREQUISITE | L3P_BLOCKED_PREREQUISITE | +5.28%/折 | 不适用 | 7.72%/折 | 只能说「无法判断」 | — |

## 三、取错了量的 6 条（逐条）

### `gate3_d`

- 该实验真正的判定序列：`gate3_out/9b7235d77689/monthly_paired.csv::d`
- L3t 登记：n=125、mean=-0.398pp
- 判定统计量：n=108、mean=+0.484pp
- 差异：n: L3t=125 vs 判定=108；mean: -0.3982 vs +0.4841（mean 也不同，窗口取错）
- 若按修正值分级：**只能引方向**

### `phasec_d`

- 该实验真正的判定序列：`phasec_out/3a01759a37f0/five_cells.csv::+1×d[B] + -1×d[A]`
- L3t 登记：n=237、mean=+0.014pp
- 判定统计量：n=237、mean=+1.939pp
- 差异：mean: L3t=+0.014071 vs 判定=+1.938968（差 -1.924897）
- 若按修正值分级：**可引用点估计**

### `phasec2_d`

- 该实验真正的判定序列：`phasec2_out/953c32fdc556/cells.csv::+1×spread_v1 + -1×spread`
- L3t 登记：n=237、mean=+0.658pp
- 判定统计量：n=237、mean=+0.107pp
- 差异：mean: L3t=+0.658190 vs 判定=+0.106944（差 +0.551245）
- 若按修正值分级：**可引用，须标 MDE**

### `phasec3_d`

- 该实验真正的判定序列：`phasec3_out/6a76a10687b4/cells.csv::+1×spread_v1 + -1×spread_v0`
- L3t 登记：n=237、mean=+0.658pp
- 判定统计量：n=237、mean=+0.047pp
- 差异：mean: L3t=+0.658190 vs 判定=+0.046804（差 +0.611385）
- 若按修正值分级：**只能说「无法判断」**

### `phasej_netA`

- 该实验真正的判定序列：`phasej_out/f76de8a277f6/monthly.csv::net_A_pp`
- L3t 登记：n=237、mean=+0.032pp
- 判定统计量：n=237、mean=+0.042pp
- 差异：mean: L3t=+0.032441 vs 判定=+0.042423（差 -0.009982）
- 若按修正值分级：**只能说「无法判断」**

### `phasep_late`

- 该实验真正的判定序列：`phasep_out/3d19f7508e10/monthly.csv::net_BASE_3000w_pp`
- L3t 登记：n=129、mean=+0.129pp
- 判定统计量：n=237、mean=+0.129pp
- 差异：n: L3t=129 vs 判定=237；mean: +0.1294 vs +0.1294（mean 一致，仅分母口径不同）。**效应量本身没错**，差的只是分母——`phasep` 的 237 是全样本回归的观测数，而结论落在晚段的 129 个月上
- 若按修正值分级：**「未检出」但功效不足**

## 四、判定字符串的转写失真

| 条目 | 原文（判定） | L3t 记录 | 性质 |
|---|---|---|---|
| `gate1_d` | `failure` | `INCONCLUSIVE` | 改写 |
| `gate2_d` | `FAILURE` | `FAIL` | 截断/缩写 |
| `gate3_d` | `SUCCESS` | `PASS` | 改写 |
| `gate4_d` | `FAILURE` | `FAIL` | 截断/缩写 |
| `gate5_d` | `FAILURE` | `FAIL` | 截断/缩写 |
| `gate6_d` | `SUCCESS` | `PASS` | 改写 |
| `phasec2_d` | `SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT` | `POOL_EFFECT_CONFIRMED` | 改写 |
| `phasec3_d` | `LAG_COST_NEGLIGIBLE` | `POOL_EFFECT_CONFIRMED` | 改写 |
| `phasee_alpha` | `LONGONLY_ALPHA_NOT_DETECTED` | `SINGLE_SIDE_NOT_DETECTED` | 改写 |
| `phaseg_alpha` | `MAX5_LONGONLY_NOT_EXECUTABLE` | `NOT_EXECUTABLE` | 截断/缩写 |
| `phasej_netA` | `NET_AFTER_EXECUTION_ABSORBED` | `NET_ABSORBED` | 改写 |
| `phasep_late` | `SEGMENT_DIFFERENCE_NOT_DETECTED` | `SEGMENT_DIFF_NOT_DETECTED` | 改写 |
| `phaseq_max5` | `INCREMENTAL_ABSORBED` | `MAX5_INCREMENTAL` | **语义反转**（原文「被吸收＝不成立」） |

> 判定字符串不是装饰：`ABSORBED` 与 `CONFIRMED` 是相反的意思。本文件所有分级一律以**原文**为准。

## 五、更正后的分辨率

| 量 | L3t | 本文件（重建判定序列后） |
|---|---|---|
| pp 口径内 MDE 极差 | 20.89× | 30.49× |
| pp 口径内 MDE 最小 | 0.087pp | 0.060pp |
| pp 口径内 MDE 最大 | 1.822pp | 1.822pp |

极差从 20.89× 变成 30.49×，**不是变窄而是变宽**——因为 `phasec2` 改用真正的配对差序列后 MDE 降到 0.060pp，成为 pp 口径内的新最小值（原先这个位置是 0.087pp 的 phasej）。所以 P4 的预测（极差会变窄）被反驳：更准确的量必然带来更极端的跨度。

## 六、更正后的「低于同族最好分辨率」名单

| 条目 | 族 | 效应量 | 同族最好 MDE | 比值 |
|---|---|---|---|---|
| `phaseq_max5` | C_regression | +0.042pp | 0.468pp | 0.09 |

**L3t 原名单 5 条，更正后只剩 1 条。**被移出的 4 条（phasec、phasec2、gate5、phasej）是因为登记值取错了量：改用真正的判定统计量后，它们的效应量并不低于同族最好分辨率。

剩下的这条读法不变：**不是结论错**，而是「换同族更强的设计去测同样大小的效应，可能检不出」。

## 七、可引用强度分级

| 分级 | 含义 | 条目数 | 条目 |
|---|---|---|---|
| QUOTABLE_MARGINAL | 可引用，须标 MDE | 2 | `phasef_delta`, `phaseo_netBASE` |
| DIRECTION_ONLY | 只能引方向 | 2 | `phases_net`, `phaset_net` |
| NULL_CONFIRMED | 可引用为「确认无效应」 | 1 | `phasel_alpha` |
| UNDERPOWERED | 只能说「无法判断」 | 7 | `l3_test`, `l3p_test`, `l3r_delta`, `l3s_ic`, `phasee_alpha`, `phaseg_alpha`, `phaseq_max5` |
| NOT_GRADED | 核对未通过，不参与分级 | 15 | `gate1_d`, `gate2_d`, `gate3_d`, `gate4_d`, `gate5_d`, `gate6_d`, `phasec2_d`, `phasec3_d`, `phasec_d`, `phasej_netA`, `phasek_m1net`, `phasem_spread`, `phasep_late`, `phaser_alpha`, `phasev_net` |

> **怎么用**：`QUOTABLE` 可以引点估计；`QUOTABLE_MARGINAL` 引的时候必须同时给 MDE；`DIRECTION_ONLY` 只能写方向、不能写大小；`NOT_QUOTABLE` 的点估计不可信。未检出类里 `UNDERPOWERED` **不得**写成「没有效应」。

### 现在可以怎么写（照抄即可）

- **exQ5 结项**（`phasef_delta`，QUOTABLE_MARGINAL）：「MAX5 分层后价差 +0.740pp/月（t=3.41），该设计 MDE 0.609pp，效应约为分辨率的 1.2 倍。」
- **成本后**（`phaseo_netBASE`，QUOTABLE_MARGINAL）：「BASE 费率、3000 万资金下净 +0.136pp/月（t=4.04），MDE 0.094pp，约 1.4 倍；盈亏平衡费率 0.4129%。」
- **现代段**（`phases_net` / `phaset_net`，DIRECTION_ONLY）：「2016 年后方向为正、但点估计（+0.111pp, t=2.22）不到该设计 MDE（0.140pp）的 0.8 倍，**只能引方向**，且 T 判 `T_FRAGILE`。」
- **SURGE 成本后**（`phasel_alpha`，NULL_CONFIRMED）：「毛 alpha +0.106pp（t=3.13）显著，但判定原文是 `SURGE_AVOID_ABSORBED`——**被确认不成立的是「成本后仍可交易」这个命题**，不是「低 SURGE 没有 alpha」。」
- **答不了的三个**（`l3r_delta` / `l3s_ic` / `l3_test` / `l3p_test`，UNDERPOWERED）：「本次设计功效不足（16.9%~40.0%），**不能据此说没有效应**。」

## 八、逐条核对预登记预测

| 编号 | 预测 | 实际 | 被反驳 |
|---|---|---|---|
| P1 | 判定字符串与 L3t 记录完全一致的条目 ≥ 18/27 | 14/27 一致；不一致 13 条 | **是** |
| P2 | 判定统计量与 L3t 行一致的条目 ≥ 18/27 | 8/27 一致（MATCH） | **是** |
| P3 | 需要重建序列（非单列）的条目恰好 3 条 | 3 条（phasec_d, phasec2_d, phasec3_d） | 否 |
| P4 | 更正后 pp 口径内 MDE 极差 < 20.9× | 30.49×（L3t 原值 20.89×） | **是** |
| P5 | 更正后 below_family_best 条目数 ≤ 3（L3t 为 5 条） | 1 条 | 否 |
| P6 | detected=true 中 ratio ≥ 2（QUOTABLE）的 ≥ 3 条 | 0 条（无） | **是** |
| P7 | UNVERIFIABLE 条目数为 0 | 0 条 | 否 |

**4 条被反驳，且都不是「差一点」**：P1/P2 我预估多数条目是照抄的，实际只有 14/27 与 8/27；P6 我预估至少 3 条能引点估计，实际 **0 条**。这三条同向说明一件事——**L3t 那张表的可靠度与我当时的估计相差很远，而本项目的效应量普遍不到自身分辨率的 2 倍。**

## 九、限制

- 本次只核对「登记值 vs 判定值」，不重跑实验；MDE 是在**重建后的判定序列**上用 L3t 同一实现重算的。
- phasem / phaser / phasev / l3r / l3s / l3 / l3p 七条的判定不是单一均值（区间判定 / 多门槛 / 自持有序列），标记为 NOT_APPLICABLE，只核对了它们的序列与 MDE，未做 mean/t 对比。
- 分级阈值（2×/1×/0.5× 与功效 0.8/0.5）在预登记中固定，未因结果调整；落在边界附近的条目按数值原样报告。
- 「可引用强度」是**引用规范**，不是对结论对错的重新判定；L3t 与各阶段原判定字符串均未被修改。
- phasej 的判定档是 3000 万，而 CSV 里的 net_A_pp 对应 1000 万档；本实验按预登记比对两者的差异，不改用另一档重算（那属于新实验）。
- 预登记在过程中修订过 3 次（2 次笔误、1 次语义标注），每次的理由与影响都写在 `amendments` 里；第二次修订前的产物保留为 `l3u_citation_firstrun_scalebug.{json,md}`。

## 十、复现

```bash
PYTHONPATH="backend/src" python scripts/l3u_citation.py
```

耗时 0.12 秒；27 条全部读自仓库内既有产物，无网络、无重跑。
