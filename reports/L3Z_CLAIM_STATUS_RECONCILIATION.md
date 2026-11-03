# 27 条结论的状态流转对账

> 本报告由 `scripts/l3z_audit_reports.py` 从产物生成。目的：解释「解封 9 条 / 维持封印 2 条」与「封印 6 条」为何对不上。

## 一、被质疑的不一致

阶段性报告里同时出现两个说法：

- L3w/L3x 状态表：解封 9 条、**维持封印 2 条**；
- 第六节：封印 **6 条**，分「不同源」与「非单一均值」两类。

9 + 2 = 11，与 6 差 5。这不是笔误，是**三个不同口径被写在同一行**。

## 二、逐阶段重建

| 结论 | 族 | n | L3u 分级 | L3u 核对 | 复核阶段 | 复核结果 | L3z 判定 | L3z p | 最终分级 | 分级依据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gate1_d` | A_cross_spread | 48 | NOT_GRADED | MISMATCH_MEAN | L3x | UNSEALED | NEITHER | 0.545309 | **UNDERPOWERED** | l3w_unseal |
| `gate2_d` | A_cross_spread | 125 | NOT_GRADED | MISMATCH_MEAN | L3x | UNSEALED | NEITHER | 0.113348 | **UNDERPOWERED** | l3w_unseal |
| `gate3_d` | A_cross_spread | 108 | NOT_GRADED | MISMATCH_N | L3w | UNSEALED | DOWNGRADED | 0.024529 | **EXPLORATORY** | l3z_audit |
| `gate4_d` | A_cross_spread | 237 | NOT_GRADED | MISMATCH_MEAN | L3x | UNSEALED | NEITHER | 0.333570 | **UNDERPOWERED** | l3w_unseal |
| `gate5_d` | A_cross_spread | 249 | NOT_GRADED | MISMATCH_MEAN | L3x | UNSEALED | NEITHER | 0.876952 | **UNDERPOWERED** | l3w_unseal |
| `gate6_d` | A_cross_spread | 237 | NOT_GRADED | MISMATCH_MEAN | L3x | UNSEALED | SURVIVES_ALL | 0.000006 | **QUOTABLE_MARGINAL** | l3w_unseal |
| `phasec_d` | A_cross_spread | 237 | NOT_GRADED | MISMATCH_MEAN | L3w | UNSEALED | SURVIVES_ALL | 0.000000 | **QUOTABLE** | l3w_unseal |
| `phasec2_d` | A_cross_spread | 237 | NOT_GRADED | MISMATCH_MEAN | L3w | UNSEALED | SURVIVES_ALL | 0.000001 | **QUOTABLE_MARGINAL** | l3w_unseal |
| `phasec3_d` | A_cross_spread | 237 | NOT_GRADED | MISMATCH_MEAN | L3w | UNSEALED | NEITHER | 0.550840 | **UNDERPOWERED** | l3w_unseal |
| `phasee_alpha` | B_long_only | 237 | UNDERPOWERED | MATCH | 未复核 | — | NEITHER | 0.058478 | **UNDERPOWERED** | l3u_grade |
| `phasef_delta` | A_cross_spread | 237 | QUOTABLE_MARGINAL | MATCH | 未复核 | — | SURVIVES_ALL | 0.000768 | **QUOTABLE_MARGINAL** | l3u_grade |
| `phaseg_alpha` | B_long_only | 237 | UNDERPOWERED | MATCH | 未复核 | — | NEITHER | 0.079017 | **UNDERPOWERED** | l3u_grade |
| `phasel_alpha` | B_long_only | 237 | NULL_CONFIRMED | MATCH | 未复核 | — | SURVIVES_ALL | 0.001972 | **NULL_CONFIRMED** | l3u_grade |
| `phasem_spread` | A_cross_spread | 237 | NOT_GRADED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **NOT_GRADED** | l3u_grade |
| `phasej_netA` | B_long_only | 237 | NOT_GRADED | MISMATCH_MEAN | L3w | STAY_SEALED | NEITHER | 0.298312 | **NOT_GRADED** | l3u_grade |
| `phasek_m1net` | B_long_only | 237 | NOT_GRADED | MISMATCH_MEAN | L3x | STAY_SEALED | NEITHER | 0.233460 | **NOT_GRADED** | l3u_grade |
| `phaseo_netBASE` | B_long_only | 237 | QUOTABLE_MARGINAL | MATCH | 未复核 | — | SURVIVES_ALL | 0.000073 | **QUOTABLE_MARGINAL** | l3u_grade |
| `phasep_late` | C_regression | 129 | NOT_GRADED | MISMATCH_N | L3w | STAY_SEALED | SURVIVES_FDR_ONLY | 0.010970 | **NOT_GRADED** | l3u_grade |
| `phaser_alpha` | B_long_only | 237 | NOT_GRADED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **NOT_GRADED** | l3u_grade |
| `phases_net` | B_long_only | 129 | DIRECTION_ONLY | MATCH | 未复核 | — | DOWNGRADED | 0.028317 | **EXPLORATORY** | l3z_audit |
| `phaset_net` | B_long_only | 129 | DIRECTION_ONLY | MATCH | 未复核 | — | DOWNGRADED | 0.028317 | **EXPLORATORY** | l3z_audit |
| `phasev_net` | B_long_only | 237 | NOT_GRADED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **NOT_GRADED** | l3u_grade |
| `phaseq_max5` | C_regression | 237 | UNDERPOWERED | MATCH | 未复核 | — | NEITHER | 0.799747 | **UNDERPOWERED** | l3u_grade |
| `l3r_delta` | A_cross_spread | 197 | UNDERPOWERED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **UNDERPOWERED** | l3u_grade |
| `l3s_ic` | D_ic | 198 | UNDERPOWERED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **UNDERPOWERED** | l3u_grade |
| `l3_test` | E_fold | 27 | UNDERPOWERED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **UNDERPOWERED** | l3u_grade |
| `l3p_test` | E_fold | 28 | UNDERPOWERED | NOT_APPLICABLE | 未复核 | — | COUNTS_ONLY | — | **UNDERPOWERED** | l3u_grade |

## 三、三个口径分别是什么

| 说法 | 实际含义 | 实测值 |
| --- | --- | --- |
| 「L3w 解封 4 条」 | L3w 单阶段（处理取错量的 6 条） | 复核 6 条 → 解封 4、维持 2 |
| 「L3x 解封 5 条」 | L3x 单阶段（处理仅 t 口径不同的 6 条） | 复核 6 条 → 解封 5、维持 1 |
| 「解封 9 条」 | L3w + L3x **合计** | 9 条 |
| 「维持封印 2 条」 | **只算 L3w**，漏掉 L3x 的维持项 | L3w 维持 2；两阶段合计维持 3 |
| 「封印 6 条」 | **最终** `NOT_GRADED` 总数 | 6 条 |

**对账公式**：

- 复核总数 = 12（L3w 6 + L3x 6）
- 其中解封 = 9、维持 = 3
- 从未复核 = 15
- 最终 `NOT_GRADED` = 维持封印 3 + 从未复核且仍未分级 3 = 6

所以「9 + 2 = 11」是**单阶段数字与合计数字相加**，而 6 是最终状态数。两者都不能直接互相校验，正确的关系是上面那条公式。

## 四、L3z 引入的第三次状态变化

L3z 审计后，3 条原 `DIRECTION_ONLY` 被降级为 `EXPLORATORY`。这些条目的分级依据因此变成 `l3z_audit`，与前两个来源并列：

| 分级依据 | 条数 | 含义 |
| --- | --- | --- |
| `l3u_grade` | 16 | 最初核对即通过，未经任何复核或降级 |
| `l3w_unseal` | 8 | 最初判「核对未通过」，复核证明判定量与序列同源后解封 |
| `l3z_audit` | 3 | 单条显著但在全族多重检验下未通过，审计后降级为探索性观察 |

## 五、最终状态

| 分级 | 条数 |
| --- | --- |
| `QUOTABLE` | 1 |
| `QUOTABLE_MARGINAL` | 4 |
| `EXPLORATORY` | 3 |
| `NULL_CONFIRMED` | 1 |
| `UNDERPOWERED` | 12 |
| `NOT_GRADED` | 6 |
| **合计** | **27** |

对外可引用 **6 条**；探索性观察 **3 条**；核对未通过 **6 条**；无分辨力 **12 条**。

⚠️ **一处命名缺陷（已知，未改）**：卡片的分级依据统一写作 `l3w_unseal`，但其中 5 条的解封其实由 **L3x** 给出（gate1_d、gate2_d、gate4_d、gate5_d、gate6_d）。代号是通用标签而非阶段编号，真实阶段见上表「复核阶段」列。之所以不改名：这个字面值已写进预登记与闸门判据，改名会引起一轮无信息量的 sha 重链，而追溯所需的信息已经完整存在。**引用时以「解封复核」统称，不要写成「L3w 解封的」。**

## 六、能否支持解释

**能**——三个阶段各自的产物齐备（L3w/L3x 的 decisions 数组、L3y 的卡片分级、L3z 的降级清单），每一处条数变化都能落到具体条目上，不需要推测。差异已解释，不构成阻塞。

## 七、一条需要单独说明的情形

`phasep_late` 处于「多重检验后仍显著、但对外分级为 `NOT_GRADED`」的状态。这**不是矛盾**：两者量的不是同一个东西。

- L3z 校正的是**该序列自身的均值**是否显著（t 来自重建后的逐期序列）；
- `NOT_GRADED` 的依据是**原始判定用的统计量**与该序列不同源（回归系数 / 不同资金档 / 缺分母），因此它的结论句不能按「均值比值」来分级。

⇒ 「序列本身显著」不能替代「原判定成立」。引用时必须用后者。

