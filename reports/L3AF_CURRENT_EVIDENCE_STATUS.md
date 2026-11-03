# L3af：当前证据状态（S1–S4 四分法）

> 预登记 `ed2a03162ce3…`｜产物 `reports/gates/H-L3AF-COST-NET/l3af_evidence.json`

## 1. 为什么要拆成四张标签

「这条结论站得住」至少混了四件事，历史报告里常被压成一句：

| 标签 | 问题 | 通过条件 |
| --- | --- | --- |
| **S1 统计** | 在给定家族与 m 下，是不是噪声？ | p 可复现 + 四种校正（m=25）全过 |
| **S2 经济** | 效应量够不够大？ | \|效应\| ≥ 0.3pp/月，且该门槛对该条目**事前**适用 |
| **S3 成本后** | 扣掉成本、且成本的不确定性也算进去，还剩多少？ | 有逐月净序列，且该序列自身四种校正全过、均值为正 |
| **S4 可交易** | 能不能真下单？ | 独立样本外 + 成交模型 + 容量 + 风险，四项齐备 |

**禁止的自动推导**：统计显著 ⇒ 经济有意义；效应/MDE 比值大 ⇒ 经济有意义（那是功效，不是价值）；样本内净收益为正 ⇒ 可实盘。

## 2. 七条正面结论的四张标签

| claim_id | S1 | S2 | S3 | S4 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| `gate6_d` | PASS | NOT_GRADED | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` |
| `phasec2_d` | PASS | FAIL | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` |
| `phasec_d` | PASS | PASS | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` |
| `phasef_delta` | PASS | PASS | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` |
| `phasei_v0` | PASS | NOT_GRADED | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` |
| `phasei_v1` | PASS | NOT_GRADED | TIER_DEPENDENT | ABSENT | `QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST` |
| `phaseo_netBASE` | PASS | NOT_GRADED | PASS | ABSENT | `QUOTABLE_WITH_COST_EVIDENCE` |

通过数：S1 **7/7**、S2 **2/7**、S3 **1/7**、S4 **0/7**。

### 2.1 S2 只过 2 条——这不是坏消息，是门槛在起作用

| 条目 | 效应 | 是否受事前门槛约束 | 结论 |
| --- | --- | --- | --- |
| `gate6_d` | +0.7084 | 否 | 不判（参照 0.3pp，高于该参照） |
| `phasec2_d` | +0.1069 | 是 | 不过 |
| `phasec_d` | +1.9390 | 是 | 过 |
| `phasef_delta` | +0.7405 | 是 | 过 |
| `phasei_v0` | +0.2307 | 否 | 不判（参照 0.3pp，低于该参照） |
| `phasei_v1` | +0.1634 | 否 | 不判（参照 0.3pp，低于该参照） |
| `phaseo_netBASE` | +0.1360 | 否 | 不判（参照 0.3pp，低于该参照） |

净收益类（`phaseo_netBASE`）不判 S2：0.30pp 门槛的理由是「= 0.1% 档月度成本的 2 倍」，而这条本身已经扣掉成本，门槛的理由不迁移，也从未为它预登记过门槛。

### 2.2 S3 的两条路径必须分开说

#### `phasei_v1` —— `TIER_DEPENDENT`

- 路径：`reconstructed_l3af`
- 预登记主成本档（0.3%）下不显著 ⇒ 冻结判定 EXQ5_COST_ABSORBED 不变；真实费率档下显著，但那是**敏感性**，不是判定

| 档位 | 费率 | 净值（pp/月） | t(iid) | t(NW3) | iid 显著 | NW3 显著 |
| --- | --- | --- | --- | --- | --- | --- |
| `T_0p3` | 0.3000% | +0.050716 | +1.0474 | +1.4162 | 否 | 否 |
| `T_0p1` | 0.1000% | +0.125846 | +2.5997 | +3.4990 | 是 | 是 |
| `T_1e8` | 0.0510% | +0.144253 | +2.9798 | +4.0046 | 是 | 是 |
| `T_3e7` | 0.0585% | +0.141435 | +2.9216 | +3.9274 | 是 | 是 |
| `T_1e7` | 0.1234% | +0.117056 | +2.4181 | +3.2568 | 是 | 是 |

#### `phaseo_netBASE` —— `PASS`

- 路径：`judged_on_net_series`
- 该条判定量即扣成本净收益，成本口径与资金档随条目登记

`phasei_v0` 为什么是 ABSENT 而不是沿用 V1 的换手：V0 的持仓含不可成交标的，其逐月换手与 V1 **不是同一条序列**。拿 V1 的换手估 V0 的成本，正是「换手禁止沿用别的信号」要防的事。这条宁可不判，也不借用。

## 3. 其余在册条目

| ID | S1 | S2 | S3 | S4 | 状态 | 登记判定 |
| --- | --- | --- | --- | --- | --- | --- |
| `gate1_d` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `gate2_d` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `gate3_d` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | EXPLORATORY |
| `gate4_d` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `gate5_d` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `l3_test` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `l3p_test` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `l3r_delta` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `l3s_ic` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `phasec3_d` | FAIL | FAIL | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `phasee_alpha` | FAIL | FAIL | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `phaseg_alpha` | FAIL | FAIL | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `phasej_netA` | FAIL | NOT_GRADED | FAIL | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |
| `phasek_m1net` | FAIL | NOT_GRADED | FAIL | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |
| `phasel_alpha` | PASS | NOT_GRADED | ABSENT | ABSENT | `QUOTABLE_STAT_ONLY` | NULL_CONFIRMED |
| `phasem_spread` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |
| `phasep_late` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |
| `phaseq_max5` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | UNDERPOWERED |
| `phaser_alpha` | FAIL | NOT_GRADED | ABSENT | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |
| `phases_net` | FAIL | NOT_GRADED | FAIL | ABSENT | `NOT_QUOTABLE_STAT` | EXPLORATORY |
| `phaset_net` | FAIL | NOT_GRADED | FAIL | ABSENT | `NOT_QUOTABLE_STAT` | EXPLORATORY |
| `phasev_net` | FAIL | NOT_GRADED | FAIL | ABSENT | `NOT_QUOTABLE_STAT` | NOT_GRADED |

## 4. 全族层面的限制

- 全部结论都在历史样本内产生（窗口止于 2026-09），不存在前瞻期证据。
- 「统计显著」不等于「经济有意义」；「样本内净收益为正」不等于「可实盘」。
- 本清单只覆盖 L3u 登记在册的 27 条 + L3af 纳入的 phasei 2 条；不在家族内的实验（如 phaseu 只读审计）不在此校正范围内。
- 研究历史无法完整恢复：尝试过但未报告的策略/窗口/成本档位没有落盘，因此不能声称已校正全部研究者自由度。
- m=25 依赖「同一随机变量只占一个名额」这一判据；若改用 m=27/29，7 条正面结论的集合不变（G9B 验证），变化只出现在 phasel_alpha（非正面）。

### 4.1 D2｜经济门槛的记录矛盾

- docs/PHASE_C2_SUSPEND_PREREG_2026-10-21.md 第 93 行写「0.30pp/月 等于阶段 C 主检验经济门槛的一半」，即隐含 C 的门槛是 0.60；而 PHASE_C 第 156 行、PHASE_E 第 86 行、PHASE_F 第 116 行三处都说 C 的门槛是 0.30。4 份文件中 3 份一致、1 份矛盾。
- 影响：对 7 条正面结论的 S2 判定**无影响**：受门槛约束的只有 phasec_d（1.94）、phasec2_d（0.107）、phasef_delta（0.740），无论门槛取 0.30 还是 0.60，三条的过/不过都不变。
- 处置：采用 0.30（三份文件一致），把矛盾作为记录缺陷保留，不改原文

