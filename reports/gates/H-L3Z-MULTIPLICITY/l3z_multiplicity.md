# L3z：多重检验校正

登记条目 **27** 条，去重后独立检验 **24** 份；在 α=0.05 下，若全部零效应，期望假阳性 **1.20 条**。

## 一、为什么要有这一层

此前各层（L3t 分辨率、L3u 引用核对、L3v 卡片、L3w/L3x 解封、L3y 定稿）管的都是**单条**结论的数字对不对。这一层管的是另一件事：这些结论是**一起报出来的**。

单条 α=0.05 显著的有 10 条——但这恰恰是问题所在：在全部零效应的世界里，报告这么多条检验，期望也会冒出 1.20 条「显著」。

## 二、三种 m 口径与两种估计量

| 口径 | m | 参与评分 | 只计名额 | Bonferroni 门槛 | 通过 | BH 门槛 | 通过 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| m_all / t_nw3 | 27 | 20 | 7 | 0.001852 | 5 |  0.010970 | 7 |
| m_all / t_iid | 27 | 20 | 7 | 0.001852 | 4 |  0.004448 | 6 |
| m_independent / t_nw3 | 24 | 20 | 7 | 0.002083 | 6 |  0.010970 | 7 |
| m_independent / t_iid | 24 | 20 | 7 | 0.002083 | 4 |  0.004448 | 6 |

⚠️ **去重不能用序列路径**：共享同一份数字的条目路径不同（`phasel_out/…` 与 `phaser_out/…` 数值逐位相同），只能按数值指纹去重。

## 三、逐条结果（主口径：NW(3)，m = 独立检验数）

| 结论 | p | 功效 | 单条显著 | Bonf | Holm | BH | 校正后 | 定稿分级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `phasec_d` | 0.000000 | 1.00 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | QUOTABLE |
| `phasec2_d` | 0.000001 | 1.00 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | QUOTABLE_MARGINAL |
| `gate6_d` | 0.000006 | 1.00 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | QUOTABLE_MARGINAL |
| `phaseo_netBASE` | 0.000073 | 0.98 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | QUOTABLE_MARGINAL |
| `phasef_delta` | 0.000768 | 0.93 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | QUOTABLE_MARGINAL |
| `phasel_alpha` | 0.001972 | 0.88 | ✓ | ✓ | ✓ | ✓ | `SURVIVES_ALL` | NULL_CONFIRMED |
| `phasep_late` | 0.010970 | 0.73 | ✓ | · | · | ✓ | `SURVIVES_FDR_ONLY` | NOT_GRADED |
| `gate3_d` | 0.024529 | 0.63 | ✓ | · | · | · | `DOWNGRADED` | EXPLORATORY |
| `phases_net` | 0.028317 | 0.60 | ✓ | · | · | · | `DOWNGRADED` | EXPLORATORY |
| `phaset_net` | 0.028317 | 0.60 | ✓ | · | · | · | `DOWNGRADED` | EXPLORATORY |
| `phasee_alpha` | 0.058478 | 0.48 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `phaseg_alpha` | 0.079017 | 0.42 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `gate2_d` | 0.113348 | 0.36 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `phasek_m1net` | 0.233460 | 0.22 | · | · | · | · | `NEITHER` | NOT_GRADED |
| `phasej_netA` | 0.298312 | 0.18 | · | · | · | · | `NEITHER` | NOT_GRADED |
| `gate4_d` | 0.333570 | 0.16 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `gate1_d` | 0.545309 | 0.09 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `phasec3_d` | 0.550840 | 0.09 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `phaseq_max5` | 0.799747 | 0.06 | · | · | · | · | `NEITHER` | UNDERPOWERED |
| `gate5_d` | 0.876952 | 0.05 | · | · | · | · | `NEITHER` | UNDERPOWERED |

判定含义：

- `SURVIVES_ALL`：三种校正全部通过，可以宣称显著。
- `SURVIVES_FDR_ONLY`：只在 FDR 口径下通过，FWER 口径下不成立——引用需说明用的是 FDR。
- `DOWNGRADED`：单条显著，但在同时报告 N 条的背景下不成立。
- `NEITHER`：单条就不显著，且功效 < 0.8——**只能说无法判断，不能说无效应**。
- `NOT_APPLICABLE`：结论不由单一均值的显著性给出，多重性校正对它不适用。

## 四、与对外定稿的交叉

定稿标注为「可引用」的条目，在多重检验校正下分别落在哪一档：

| 定稿分级 | 条数 | 存活 | 掉档 | 不适用 |
| --- | --- | --- | --- | --- |
| `UNDERPOWERED` | 12 | 0 | 0 | 4 |
| `EXPLORATORY` | 3 | 0 | 3 | 0 |
| `QUOTABLE_MARGINAL` | 4 | 4 | 0 | 0 |
| `QUOTABLE` | 1 | 1 | 0 | 0 |
| `NULL_CONFIRMED` | 1 | 1 | 0 | 0 |
| `NOT_GRADED` | 6 | 1 | 0 | 3 |

**掉档**的含义是「在同时报告 N 条的背景下不足以宣称显著」，**不是**「效应不存在」——这一点与「功效不足 ≠ 无效应」是同一条纪律。

## 五、族内校正

| 族 | m | BH 门槛 | 存活 | 掉档 |
| --- | --- | --- | --- | --- |
| `A_cross_spread` | 12 | 0.000768 | gate6_d、phasec_d、phasec2_d、phasef_delta | gate3_d |
| `B_long_only` | 10 | 0.001972 | phasel_alpha、phaseo_netBASE | phases_net、phaset_net |
| `C_regression` | 2 | 0.010970 | phasep_late | — |
| `D_ic` | 1 | — | — | — |
| `E_fold` | 2 | — | — | — |

## 六、敏感性

**α 与 q 分开扫。** α 是 FWER 类（Bonferroni / Holm）的水平，q 是 BH 的目标错误发现率；把它们绑成同一个值会让下面的 B 类集合看起来「非单调」，而那只是两个参数被同时改动了。

### α 扫描（q 固定 = 0.05）

| α | A 校正后拒绝（BH） | B 原始显著但未拒绝 | 原始显著数 |
| --- | --- | --- | --- |
| 0.01 | 7 | 0 （无） | 6 |
| 0.05 | 7 | 3 （gate3_d、phases_net、phaset_net） | 10 |
| 0.1 | 7 | 5 （gate3_d、phasee_alpha、phaseg_alpha、phases_net、phaset_net） | 12 |

C 类（不具备单一 p 值、无法参与该校正流程）恒为 7 条，不随 α 变化。

### q 扫描（α 固定 = 0.05）

| q | BH 拒绝 | BH 未拒绝 |
| --- | --- | --- |
| 0.01 | 6 | 14 |
| 0.05 | 7 | 13 |
| 0.1 | 10 | 10 |

### 单调性检查

| 检查 | 结果 |
| --- | --- |
| bonferroni_over_alpha | ✓ 满足 |
| holm_over_alpha | ✓ 满足 |
| bh_over_alpha | ✓ 满足 |
| bh_over_q | ✓ 满足 |

拒绝集合随水平放宽必须单调增大，这是标准校正方法的基本性质。上表是**对实现的检查**，不是对结论的判断。

| 其余检验 | 结果 |
| --- | --- |
| 实删重复条目（27→24 条） | BH 通过 7 条（基线 7） |
| 错误地把只计名额的 7 条纳入评分 | BH 通过 10 条（基线 7） |

## 七、预测对照

| 预测 | 时机 | 内容 | 观测 | 结果 |
| --- | --- | --- | --- | --- |
| P1 | post_hoc | NW(3) 口径下，单条 α=0.05 显著的共 10 条；Bonferroni(m=24) 后剩 6 条，BH 后剩 7 条。 | {"raw_significant": 10, "bonferroni": 6, "bh": 7} | 成立 |
| P2 | post_hoc | 定稿里 3 条 DIRECTION_ONLY（gate3_d、phases_net、phaset_net）在校正后全部掉档——它们的单条 p 在 0.024~0.028，看着显著，但在 24 条同时报告的背景下不成立。 | {"downgraded": ["gate3_d", "phases_net", "phaset_net"], "downgraded_direction_only": ["gate3_d", "phases_net", "phaset_net"]} | 成立 |
| P3 | post_hoc | m 从 27 降到 24 时 phasel_alpha 从 SURVIVES_FDR_ONLY 升为 SURVIVES_ALL（Bonferroni 门槛 0.001852 → 0.002083，它的 p=0.001972 恰好跨越）。即：把同一份数字登记两次，会把真正的结论挤出门外。 | {"phasel_m_all": "SURVIVES_FDR_ONLY", "phasel_m_independent": "SURVIVES_ALL"} | 成立 |
| P4 | priori | α 敏感性：收紧到 0.01 时掉档集合会扩大（≥4 条且包含 gate3_d/phases_net/phaset_net）；放宽到 0.10 时掉档集合不会变大（仍是这 3 条的子集）。若放宽 α 反而掉得更多，说明我对方向的理解是错的。 | {"0.01": [], "0.05": ["gate3_d", "phases_net", "phaset_net"], "0.10": ["gate3_d", "phasee_alpha", "phaseg_alpha", "phases_net", "phaset_net"]} | **被反驳** |
| P5 | priori | 把 3 组重复条目各删掉一条（27 条实删为 24 条，而不是只把 m 改成 24）后，BH 通过的条数仍然是 7——BH 的通过集合由最小的那几个 p 决定，删掉的是 p 较大的重复项。 | {"baseline_bh": 7, "drop_duplicates_bh": 7} | 成立 |
| P6 | priori | 若把 7 条「只计名额」的条目错误地纳入评分（而不是只占 m），BH 通过的条数会变化 ≥1——用以确认「只计名额」这个处理不是无所谓的记账。 | {"baseline_bh": 7, "include_counts_only_bh": 10} | 成立 |

⚠️ 其中 3 条标 `post_hoc`：它们是**观测结果的复述，不是预测**——本阶段先做了探索性计算才回头写预登记，违反 R2。偏差不修饰。其余真预测中，成立 2/3。

被反驳：P4。原样保留，不改判据。

**P4 为什么错**：我把它当成「α 只收紧校正门槛」，但 α 同时定义了「哪些条算单条显著」。收紧 α 会让进入待校正集合的条目变少，掉档反而变少——α=0.01 时掉档集合是空的。所以「掉档」不是条目本身的性质，而是「在给定 α 约定下，单条显著却没通过校正」的相对状态；引用时必须带 α 前提（见预登记 A1）。

## 八、闸门自检

G1~G5 全部通过。

## 九、限制

- 这些检验不是预先设计的固定族，而是陆续追加的实验，**m 的界定本身就有选择空间**（27 / 24 / 分族给出不同答案）。
- BH 对正相关稳健是理论性质，本阶段未做 Monte-Carlo 验证实际 FDR。
- 多重检验只回答「这个非零信号是不是真的」，不回答「它有没有经济意义」。`phasel_alpha` 的 p 很小但结论是「净效应被成本吸收」，两者不矛盾。
- 本阶段不改对外定稿；是否据此修订定稿，是一次需要单独授权的改动。
