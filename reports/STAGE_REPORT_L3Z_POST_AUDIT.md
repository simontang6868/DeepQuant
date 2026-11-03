# 阶段报告：L3z 多重检验审计 + L3aa 任意依赖复核

> 本报告由 `scripts/l3z_audit_reports.py` 生成。两个阶段都**没有**新增因子、**没有**新增回测、**没有**启动实盘，也不以「提高显著结论数量」为目标。

**本文件包含两件事，请分开读：**

- **第 1–10 节 = L3z**：对多重检验校正做独立技术复核，发现并修正 3 处实现缺陷，把对外可引结论从 9 条修订为 6 条。
- **第 11–13 节 = L3aa**：L3z 第 8 节留下的一项缺口（「BH 的依赖结构未验证」）的补充复核。做法不是去验证那个假设，而是换成在任意依赖下都成立的方法（Benjamini–Yekutieli），看结论还在不在。**两阶段的结论互不依赖，可以单独质疑。**

## 1. 审计范围与版本

| 项目 | 内容 |
| --- | --- |
| 审计对象 | L3z 多重检验实现与结论；L3y 对外定稿的分级 |
| 代码 | `backend/src/deepquant/gates/{multiplicity,claims,deliverable,unseal,citation}.py` |
| 脚本 | `scripts/l3z_multiplicity.py` `l3z_pvalue_validation.py` `l3z_family_sensitivity.py` `l3z_lag_sensitivity.py` `l3z_relink_chain.py` `l3z_audit_reports.py` |
| 预登记 | `docs/gates/l3z_prereg.json`、`l3y_prereg.json`、`l3v_prereg.json` |
| 冻结输入 | 见 `inputs_frozen` 字段（sha256 硬编码在脚本里，不一致即拒跑） |

## 2. 执行环境与复现命令

```
PYTHONPATH=backend/src python scripts/l3z_multiplicity.py
PYTHONPATH=backend/src python scripts/l3z_pvalue_validation.py
PYTHONPATH=backend/src python scripts/l3z_family_sensitivity.py
PYTHONPATH=backend/src python scripts/l3z_lag_sensitivity.py
PYTHONPATH=backend/src python scripts/l3z_relink_chain.py --reason '<理由>' --dry-run
PYTHONPATH=backend/src python scripts/l3z_audit_reports.py
PYTHONPATH=backend/src python scripts/l3z_deliverable.py   # 即 l3y_deliverable.py
```

Python 3.13.14｜NumPy 2.5.4｜SciPy UNAVAILABLE (pip install failed: no network)

## 3. 发现的问题

| 编号 | 问题 | 性质 | 是否改变 α=0.05 下的结论 |
| --- | --- | --- | --- |
| I1 | α 扫描把 `alpha` 与 BH 的 `q` 绑成同一变量 | 实现缺陷 | 否 |
| I2 | 把「B 类（原始显著 − 校正后拒绝）」当成校正结果来谈非单调 | 表述缺陷 | 否 |
| I3 | 按 `series` 路径去重得 27（等于没去重） | 实现缺陷 | 否 |
| I4 | 只计名额的条目仍带 t，首版被误纳入评分 | 实现缺陷 | 是：BH 由 10 修正为 7 |
| I5 | SciPy 独立参考未完成（无网络） | 验证缺口 | 否，但须标记未完成 |
| I6 | 预登记 P1~P3 为事后补写 | 纪律缺陷 | 否 |
| I7 | 封印流转的两个条数来自不同口径 | 表述缺陷 | 否 |
| I8 | 「多门槛」分类过宽：7 条中 4 条其实是单一均值检验 | 分类缺陷 | 否（敏感性验证） |

## 4. 原结果与修正结果对照

| 项目 | 原 | 修正后 | 原因 |
| --- | --- | --- | --- |
| 参与评分的检验数 | 27 | 20 | 7 条多门槛只计名额，不出 p |
| BH 通过 | 10 | 7 | 剔除只计名额的条目 |
| Bonferroni 通过 | — | 6 | 新增实现 |
| 「掉档集合非单调」 | 结论 | **表述错误**：拒绝集合单调；非单调的是 B 类 | 参数耦合 |
| 对外可引用 | 9 条 | **6 条** | 3 条 DIRECTION_ONLY 未通过校正，降级为探索性观察 |
| 封印 / 无分辨力 | 6 / 12 | 6 / 12（不变） | — |

## 5. m=24 / m=27 / 分族 三方案对照

**全族方案**（主口径与替代口径）：

| 方案 | m | Bonferroni | Holm | BH |
| --- | --- | --- | --- | --- |
| m = 24（数值指纹去重，主口径） | 24 | 6 | 6 | 7 |
| m = 27（登记条目数，替代口径） | 27 | 5 | 6 | 7 |

**分族方案**（仅作敏感性参考，不采用）：

| 方案 | m | Bonferroni | Holm | BH |
| --- | --- | --- | --- | --- |
| 分族 · A_cross_spread | 12 | 4 | 4 | 4 |
| 分族 · B_long_only | 10 | 2 | 2 | 2 |
| 分族 · C_regression | 2 | 1 | 1 | 1 |
| 分族 · D_ic | 1 | 0 | 0 | 0 |
| 分族 · E_fold | 2 | 0 | 0 | 0 |

**主口径仍是 m=24**。分族方案会让 1 条（phasep_late）在 Bonferroni 下额外通过——这正是「事后拆族提高通过率」的样子，本阶段明确不采用，只作为「结论对家族边界有多敏感」的度量。

## 6. 三种校正方法的完整结果

主口径 m=24，参与评分 20 条，只计名额 7 条。

| 结论 | p | 原始显著 | Bonferroni | Holm | BH | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| `phasec_d` | 0.000000 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `phasec2_d` | 0.000001 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `gate6_d` | 0.000006 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `phaseo_netBASE` | 0.000073 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `phasef_delta` | 0.000768 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `phasel_alpha` | 0.001972 | 是 | 通过 | 通过 | 通过 | SURVIVES_ALL |
| `phasep_late` | 0.010970 | 是 | — | — | 通过 | SURVIVES_FDR_ONLY |
| `gate3_d` | 0.024529 | 是 | — | — | — | DOWNGRADED |
| `phases_net` | 0.028317 | 是 | — | — | — | DOWNGRADED |
| `phaset_net` | 0.028317 | 是 | — | — | — | DOWNGRADED |
| `phasee_alpha` | 0.058478 | 否 | — | — | — | NEITHER |
| `phaseg_alpha` | 0.079017 | 否 | — | — | — | NEITHER |
| `gate2_d` | 0.113348 | 否 | — | — | — | NEITHER |
| `phasek_m1net` | 0.233460 | 否 | — | — | — | NEITHER |
| `phasej_netA` | 0.298312 | 否 | — | — | — | NEITHER |
| `gate4_d` | 0.333570 | 否 | — | — | — | NEITHER |
| `gate1_d` | 0.545309 | 否 | — | — | — | NEITHER |
| `phasec3_d` | 0.550840 | 否 | — | — | — | NEITHER |
| `phaseq_max5` | 0.799747 | 否 | — | — | — | NEITHER |
| `gate5_d` | 0.876952 | 否 | — | — | — | NEITHER |

## 7. 9 条可引用结论的修订状态（现为 6 条）

| 结论 | 原分级 | 现分级 | L3z p | L3z 判定 | 修订理由 |
| --- | --- | --- | --- | --- | --- |
| `gate3_d` | DIRECTION_ONLY | **EXPLORATORY** | 0.024529 | DOWNGRADED | 单条显著，但全族校正下未拒绝 → 降级为探索性观察 |
| `gate6_d` | QUOTABLE_MARGINAL | **QUOTABLE_MARGINAL** | 0.000006 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phasec_d` | QUOTABLE | **QUOTABLE** | 0.000000 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phasec2_d` | QUOTABLE_MARGINAL | **QUOTABLE_MARGINAL** | 0.000001 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phasef_delta` | QUOTABLE_MARGINAL | **QUOTABLE_MARGINAL** | 0.000768 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phasel_alpha` | NULL_CONFIRMED | **NULL_CONFIRMED** | 0.001972 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phaseo_netBASE` | QUOTABLE_MARGINAL | **QUOTABLE_MARGINAL** | 0.000073 | SURVIVES_ALL | 校正后仍拒绝，分级不变 |
| `phases_net` | DIRECTION_ONLY | **EXPLORATORY** | 0.028317 | DOWNGRADED | 单条显著，但全族校正下未拒绝 → 降级为探索性观察 |
| `phaset_net` | DIRECTION_ONLY | **EXPLORATORY** | 0.028317 | DOWNGRADED | 单条显著，但全族校正下未拒绝 → 降级为探索性观察 |

### 7.1 DIRECTION_ONLY 的处理

独立验证确认校正实现正确（完整家族、单调、p 值误差 1e-12 量级），因此按审计原则「若校正正确且结论未通过：降级或标记为探索性观察」执行：

| 条目 | 原分级 | 处理 | 保留 |
| --- | --- | --- | --- |
| `gate3_d` | `DIRECTION_ONLY` | 降级为 `EXPLORATORY` | 原分级记录在卡片 `quotability_note` 中 |
| `phases_net` | `DIRECTION_ONLY` | 降级为 `EXPLORATORY` | 原分级记录在卡片 `quotability_note` 中 |
| `phaset_net` | `DIRECTION_ONLY` | 降级为 `EXPLORATORY` | 原分级记录在卡片 `quotability_note` 中 |

降级**不等于**证伪：这三条的效应方向与量级没有任何变化，变的只是「在同时报告几十条结论时，它是否还够格被称作显著」。定稿为此单列一节，并写明「不得表述为已得到统计支持的方向规律」。

### 7.2 原 9 条的逐条核查矩阵

按审计要求，每条同时查八项。凡是能由产物判定的项一律给出取值，不做主观打分：

| 结论 | 多重检验 | 效应量 | MDE | 效应/MDE | 成本口径 | 经济意义 | 前瞻证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gate3_d` | DOWNGRADED | +0.4841pp | 0.5946 | 0.81× | 非净收益口径 | 横截面两格之差，非组合收益 | 无（全为样本内） |
| `gate6_d` | SURVIVES_ALL | +0.7084pp | 0.4274 | 1.66× | 非净收益口径 | 横截面两格之差，非组合收益 | 无（全为样本内） |
| `phasec_d` | SURVIVES_ALL | +1.9390pp | 0.8531 | 2.27× | 非净收益口径 | 横截面两格之差，非组合收益 | 无（全为样本内） |
| `phasec2_d` | SURVIVES_ALL | +0.1069pp | 0.0597 | 1.79× | 非净收益口径 | 横截面两格之差，非组合收益 | 无（全为样本内） |
| `phasef_delta` | SURVIVES_ALL | +0.7405pp | 0.6086 | 1.22× | 非净收益口径 | 横截面两格之差，非组合收益 | 无（全为样本内） |
| `phasel_alpha` | SURVIVES_ALL | +0.1061pp | 0.0949 | —（零效应判定，不按比值分级） | O_BASE_3000w | 多头组合净 alpha（已带成本口径） | 无（全为样本内） |
| `phaseo_netBASE` | SURVIVES_ALL | +0.1360pp | 0.0943 | 1.44× | O_BASE_3000w | 多头组合净 alpha（已带成本口径） | 无（全为样本内） |
| `phases_net` | DOWNGRADED | +0.1112pp | 0.1405 | 0.79× | S_modern_era | 多头组合净 alpha（已带成本口径） | 无（全为样本内） |
| `phaset_net` | DOWNGRADED | +0.1112pp | 0.1405 | 0.79× | S_modern_era | 多头组合净 alpha（已带成本口径） | 无（全为样本内） |

几条读法：

- **效应/MDE** 是分级依据，不是 t 也不是 p。低于 1 表示这个检验分不清「有这个量级」与「没有」；
- **效应/MDE** 若显示「零效应判定」，表示该条按「确认无效应」规则分级，不适用比值门槛。
- **成本口径** 只有净 alpha 类结论才有；横截面差距与 IC 不带成本，因为它们不是组合收益，扣成本在定义上无处可扣；
- **经济意义** 一律标「非组合收益」：本节没有任何一条是「按此交易能赚多少」。要把横截面差距变成收益，必须过一次现金流、整手、限额与规模模拟——不在本阶段范围。

数字一致性一列没有单列成表，因为它已被两层机器校验覆盖：L3u 逐条把卡片数字与源序列重算值比对（27 条全查），L3v 再校验卡片 headline 里的每个 token；L3y 又在生成定稿时重新回源实算一次。任一层不一致都会拒绝出文件。

## 8. 仍未解决的缺口

| 缺口 | 状态 | 为什么不能补 |
| --- | --- | --- |
| SciPy 独立参考实现 | **未完成** | 本机无网络，装不上 scipy；已用两条替代路径交叉验证，但不等于完成了审计要求的 SciPy 比对 |
| 不同原假设数 | **UNKNOWN** | 预登记无逐条假设字段；L3u 已证实判定字符串有 13 条失真，不可用其反推 |
| 未进入最终报告的试验 | **UNKNOWN** | 历史记录未系统登记「尝试过但没报告」的检验，无法补全，也不伪造 |
| BH 的依赖结构 | **未验证** | 只能观察「同期、同池、同族」，未验证 PRDS 或具体依赖模型 |
| 经济意义 / 可交易性 | **不在本阶段范围** | 统计显著不等于可利用收益；要过现金流、整手、限额与前瞻样本 |
| 前瞻期证据 | **不存在** | 全部为历史样本内；不得称为前瞻证据 |

## 9. 测试与变异测试结果

审计第 9 节要求覆盖十类检查。对应关系：

| 要求 | 落在哪个文件 | 测试名（节选） |
| --- | --- | --- |
| 1 完整家族校正 | `test_gates_l3z_audit.py` | `test_prescreening_would_inflate_but_impl_does_not` |
| 2 m 不因 alpha 改变 | 同上 | `test_m_constant_across_alpha` |
| 3 alpha 改变时单调 | 同上 | `test_rejected_sets_are_nested_across_alpha` |
| 4 与独立参考一致 | 同上 | `test_p_value_matches_independent_integration`（Simpson 独立实现） |
| 5 p 值边界与极端尾部 | 同上 + `test_gates_multiplicity.py` | `test_p_extreme_tail_positive_and_tiny` |
| 6 重复登记与家族成员 | 同上 | `test_duplicate_registration_shrinks_m_not_family` |
| 7 多门槛不被赋单一等级 | 同上 | `test_counts_only_gets_no_p_and_no_verdict` |
| 8 DIRECTION_ONLY 不绕过校正 | 同上 | `test_direction_only_entry_still_gets_downgraded` |
| 9 封印流转条数自洽 | 同上 | `test_unseal_arithmetic` / `test_status_counts_sum_to_total` |
| 10 定稿数字可追溯 | `test_gates_deliverable.py` | 数字扫描 / facts 回源 / 分级表一致 |

**变异测试**：对关键检查制造了 5 处错误，抓到 **5/5**，逐条结果见 `L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md` 第六之二节。源码在每次变异后按 sha256 校验已恢复。

⚠️ **有一条变异首版未被抓到**（Holm 去掉 step-down）：原反例在有无 step-down 时结果相同，无法区分。补例子后才生效。这条失败记录保留在审计报告里，不因为最终全绿而删掉。

## 10. L3z 最终状态

**最终状态**：`CORRECTED_MULTIPLICITY_RESULTS`

判定依据：

- 存在**已确认的实现缺陷并被修正**（I1 参数耦合、I3 去重口径、I4 评分集合），所以不是 `VERIFIED_NO_MULTIPLICITY_IMPLEMENTATION_ERROR`；
- 修正后结果完整可算、拒绝集合单调、p 值经独立路径验证，所以不是 `BLOCKED_BY_STATISTICAL_IMPLEMENTATION`；
- 关键产物齐备、三种口径（24 / 27 / 分族）全部可重建，所以不是 `INCONCLUSIVE_DUE_TO_MISSING_ARTIFACTS`；
- 封印与解封的条数差异已逐条对账并给出公式，所以不是 `BLOCKED_BY_CLAIM_STATUS_INCONSISTENCY`。

⚠️ 有一项验证**未完成**（SciPy 参考），已在第 8 节与 p 值报告中标为未完成；它不改变上述状态判定，但也不应被读作「已通过全部验证」。

## 11. 阶段 L3aa：任意依赖下的多重检验复核（BY）

### 11.0 复现与涉及文件

| 项目 | 内容 |
| --- | --- |
| 预登记 | `docs/gates/l3aa_prereg.json`（sha256 `d189847274a4…`，脚本启动即校验） |
| 代码 | `backend/src/deepquant/gates/{multiplicity,dependence}.py` |
| 脚本 | `scripts/l3aa_dependence_audit.py`、`scripts/l3aa_mutation_test.py` |
| 测试 | `backend/tests/gates/test_gates_l3aa_dependence.py`（50 项） |
| 冻结输入 | `l3u_prereg`、`l3u_citation`、`l3v_claims`、`l3z_multiplicity`、`L3Z_LAG_SENSITIVITY`（sha256 硬编码，不一致即拒跑） |
| 产物 | `reports/gates/H-L3AA-DEPENDENCE/{l3aa_dependence.json,l3aa_mutation.json}` |

```
PYTHONPATH=backend/src python scripts/l3aa_dependence_audit.py
PYTHONPATH=backend/src python scripts/l3aa_mutation_test.py
PYTHONPATH=backend/src python scripts/l3z_audit_reports.py   # 重新生成本文件
```

⚠️ **没有重跑 L3z 的任何脚本。** L3z 产物里含 `elapsed_seconds`，重跑会改 sha，破坏本阶段硬编码的冻结链；L3z 的正确性由既有测试覆盖。

### 11.1 这一节补的是哪个缺口

第 8 节列了六项未解决缺口，其中「**BH 的依赖结构**」是唯一**不需要新数据**就能动的一项。
具体问题是：Benjamini–Hochberg 的正确性依赖检验之间的正回归相依（PRDS）或独立性。
本项目的家族是「同一份月度面板上反复切池、切窗口、切成本口径」，
这个前提既没有被检验过，也很难靠论证确立——而「BH 通过 7 条」这个数字本身就带着它。

本阶段的处理**不是去证明 PRDS**，而是**放弃它**：

```
BH:  p_(k) ≤ k·q / m
BY:  p_(k) ≤ k·q / (m·H_m)        H_24 = 3.775958
```

BY 在**任意依赖结构**下控制 FDR（Benjamini & Yekutieli, 2001），代价是门槛收紧 H_m 倍。
方向是单向的：H_m ≥ 1 ⇒ BY 的每一档门槛都不高于 BH ⇒ **BY 拒绝集合必然是 BH 的子集**。
因此「换成 BY」在逻辑上不可能被读成「为提高通过数而换口径」——它只能让结论变少。

### 11.2 结果：主口径 NW(3) 下 BY 拒绝 6 条，与 Bonferroni 相同

m=24（与 L3z 相同的数值指纹去重口径）、q=0.05、估计量 NW(3)。

| 口径 | 拒绝数 | 拒绝集合 |
| --- | --- | --- |
| 单条 α=0.05 | 10 | — |
| Bonferroni（FWER） | 6 | `gate6_d`、`phasec2_d`、`phasec_d`、`phasef_delta`、`phasel_alpha`、`phaseo_netBASE` |
| Holm（FWER） | 6 | `gate6_d`、`phasec2_d`、`phasec_d`、`phasef_delta`、`phasel_alpha`、`phaseo_netBASE` |
| BH（FDR，需 PRDS） | 7 | `gate6_d`、`phasec2_d`、`phasec_d`、`phasef_delta`、`phasel_alpha`、`phaseo_netBASE`、`phasep_late` |
| **BY（FDR，任意依赖）** | **6** | `gate6_d`、`phasec2_d`、`phasec_d`、`phasef_delta`、`phasel_alpha`、`phaseo_netBASE` |

BY 的拒绝集合与 Bonferroni **完全相同**。含义是：

> 对外可引的 6 条结论，在一个**不假设任何相依结构**的口径下仍然成立。

BY 比 BH 少的那 1 条是 `phasep_late`。
该条在 L3z 的定稿里本来就是 `NOT_GRADED`（判定统计量与序列不同源），
不进对外可引清单，因此它掉出 BY **不改变任何可引结论**。

### 11.3 逐条对照

| 结论 | p | BH（需 PRDS） | BY（任意依赖） | 定稿分级 |
| --- | --- | --- | --- | --- |
| `phasec_d` | 0.000000 | 通过 | **通过** | QUOTABLE |
| `phasec2_d` | 0.000001 | 通过 | **通过** | QUOTABLE_MARGINAL |
| `gate6_d` | 0.000006 | 通过 | **通过** | QUOTABLE_MARGINAL |
| `phaseo_netBASE` | 0.000073 | 通过 | **通过** | QUOTABLE_MARGINAL |
| `phasef_delta` | 0.000768 | 通过 | **通过** | QUOTABLE_MARGINAL |
| `phasel_alpha` | 0.001972 | 通过 | **通过** | NULL_CONFIRMED |
| `phasep_late` | 0.010970 | 通过 | — | NOT_GRADED |
| `gate3_d` | 0.024529 | — | — | EXPLORATORY |
| `phases_net` | 0.028317 | — | — | EXPLORATORY |
| `phaset_net` | 0.028317 | — | — | EXPLORATORY |
| `phasee_alpha` | 0.058478 | — | — | UNDERPOWERED |
| `phaseg_alpha` | 0.079017 | — | — | UNDERPOWERED |
| `gate2_d` | 0.113348 | — | — | UNDERPOWERED |
| `phasek_m1net` | 0.233460 | — | — | NOT_GRADED |
| `phasej_netA` | 0.298312 | — | — | NOT_GRADED |
| `gate4_d` | 0.333570 | — | — | UNDERPOWERED |
| `gate1_d` | 0.545309 | — | — | UNDERPOWERED |
| `phasec3_d` | 0.550840 | — | — | UNDERPOWERED |
| `phaseq_max5` | 0.799747 | — | — | UNDERPOWERED |
| `gate5_d` | 0.876952 | — | — | UNDERPOWERED |

### 11.4 m 口径敏感性

| m 口径 | BY 拒绝 | Bonferroni 拒绝 | BY 第 1 档门槛 |
| --- | --- | --- | --- |
| 24（数值指纹去重，主口径） | 6 | 6 | 0.00055174 |
| 27（登记条目数） | 6 | 5 | 0.00047588 |

**BY 在两种 m 下都给 6 条，Bonferroni 从 6 掉到 5。**
原因是 BY 是 step-up，第 k 档门槛按 k 线性放宽，补偿了 H_m 的收紧；Bonferroni 的门槛与 k 无关。
所以 BY 对「m 取 24 还是 27」这个本身有争议的口径更不敏感。

### 11.5 必须紧跟的限定：这个「6」只对 NW(3) 成立

| 滞后 | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 |
| --- | --- | --- | --- | --- | --- | --- |
| BY 拒绝数 | 5 | 5 | 6 | 6 | 6 | 7 |
| 同批 p 值上的 BH | 6 | 6 | 6 | 7 | 7 | 10 |

六档下 BY 的通过数是 **5/5/6/6/6/7**，会摆动。
**全档都在的只有 5 条**：`gate6_d`、`phasec2_d`、`phasec_d`、`phasef_delta`、`phaseo_netBASE`；
会摆动的是 `phasel_alpha`、`phasep_late`。

按分级摊开，说法可以更精确：

> **5 条正面结论（1 条 `QUOTABLE` + 4 条 `QUOTABLE_MARGINAL`）在六档滞后下全部通过 BY；
> 会摆动的第 6 条 `phasel_alpha` 是「确认无效应」那条（`NULL_CONFIRMED`），`phasep_late` 本来就是 `NOT_GRADED`。**

也就是说：**没有任何一条正面结论的 BY 通过性依赖滞后选择。**

⚠️ **这条限定来自一次失败的预测，不是事后的解释。**
预登记 P4 写的是「六档滞后 BY 通过数恒为 6」，被实测反驳。
原来的推断是：L3u 已测「20 条 × 6 档只有 1 条跨档」，所以 BY 应该也稳。
但 L3u 测的是「**分级**是否跨档」，而 BY 卡在 p 的绝对值上——
分级只看效应/MDE 的比值落在哪个区间，p 从 0.002 变到 0.004 不会跨档，BY 却会。
「分级稳」推不出「判定稳」，这两件事原先被混为一谈。预测被反驳的记录保留，判据不改。

**被反驳的预登记预测**：P4（共 6 条先验预测）。

### 11.6 依赖诊断：这些检验到底有多同步

20 条逐月序列、两两 190 对。

| 指标 | 值 |
| --- | --- |
| 窗口重叠率中位数（交集 / 较短窗口） | 1.0000 |
| 零重叠对数 | 2 |
| \|ρ\| 中位数（三偏移取最大） | 0.4135 |
| \|ρ\| 中位数（仅 lag 0） | 0.1207 |
| \|ρ\| > 0.5 的对数 | 87 / 185 |
| 最大 \|ρ\| | 1.0000 |
| 日期口径不一致的对数 | 99 |
| 平移 ±1 月后 \|ρ\| 提升 >0.10 的对数 | 84 |

**为什么主口径取「三偏移的 |ρ| 最大值」**：各阶段月度文件的日期列并不统一——
一部分有 `month`（持有月），一部分只有 `signal_date`（信号月），两者天然差 1 个月。
按单一偏移对齐会人为压低相关性。取三个偏移下的最大值，是对「这些检验同步性不强」
这一方向**最不利**的选择——若这样算出来仍不算高，那「同步性不强」就不可能是口径错位造的假象。

⚠️ **这一段不能证明什么，请务必按这个读法看**：
**两两 Pearson ρ 既不能证明也不能否证 PRDS。** PRDS 是关于联合分布的高维性质，而 ρ 只是二阶矩。
本节的作用只有两个：让读者自己判断这批检验大致有多同步；给 BY 提供一个参照。
**BY 的结论不依赖这里任何一个数字。**

**有效检验数（描述性）**：在相关矩阵完整的 14 条子集上，
Li-Ji 指标 m_eff = **12.326**（平均非对角 ρ=0.1039，平均 \|ρ\|=0.2282）。
⚠️ 这是**子集**上的量，不是全部 24 条；而且它只是「平均相关强度」的换算，
**不是 FDR 控制成立的依据**，也不能用来替代 BY。

### 11.7 顺带查出来的一件事：有一条是另一条的精确子窗口

把 \|ρ\| ≥ 0.9999 的对拉出来，再逐月比对数值：

| a | b | ρ | 共同月 | max\|逐月差\| | 逐位相同 |
| --- | --- | --- | --- | --- | --- |
| `phasek_m1net` | `phasep_late` | 0.999903 | 129 | 1.167e-01 | 否 |
| `phasek_m1net` | `phases_net` | 0.999903 | 129 | 9.849e-02 | 否 |
| `phasek_m1net` | `phaset_net` | 0.999903 | 129 | 9.849e-02 | 否 |
| `phaseo_netBASE` | `phasep_late` | 1.000000 | 129 | 0.000e+00 | **是** |
| `phaseo_netBASE` | `phases_net` | 1.000000 | 129 | 1.821e-02 | 否 |
| `phaseo_netBASE` | `phaset_net` | 1.000000 | 129 | 1.821e-02 | 否 |
| `phasep_late` | `phases_net` | 1.000000 | 129 | 1.821e-02 | 否 |
| `phasep_late` | `phaset_net` | 1.000000 | 129 | 1.821e-02 | 否 |
| `phases_net` | `phaset_net` | 1.000000 | 129 | 0.000e+00 | **是** |

其中 **2 对逐位相同**，但两者性质不同：

- `phases_net` 与 `phaset_net` 逐位相同——这一对 **L3v 已登记过**（27 条只对应 24 份独立数字，其中一组就是它们），不是新发现；
- `phaseo_netBASE` 与 `phasep_late` 逐位相同——**这一对是新的**。后者就是「前者取 late 段」，在 129 个共同月上是同一批数字，不是「高度相关」，是**同一条序列的截取**。

L3v 的数值指纹去重抓不到它：指纹是 `(mean, t, n)`，两条序列的 n 不同（237 vs 129），
指纹自然不同，于是被算成两次独立检验。**所以 m=24 其实是偏大的。**

**明知偏大，为什么不去把它改小**：把 m 改小会让每档门槛都变松，只会让更多条目通过——
这正是「为提高通过数而调 m」的样子，也是 L3z 预登记明令禁止的动作。
所以本阶段只把这个发现记录下来，**不重跑更小的 m**：现在的 6 条是在偏严的 m 下得到的，
属于保守结论；要处理应写成下一次预登记里的去重规则（把「同源子窗口」定义进去），
而不是这一次就地放宽。

### 11.8 仪器与变异测试

**闸门**：0 处违规。覆盖 BY ⊆ BH（逐档逐 m）、门槛公式自洽、H_24 已知值、m=1 退化、全零效应不放行、q 单调、重叠率子集性（交集等于较短窗口时必为 1）、m_eff 必被相关性压低。

**变异测试**：注入 6 处缺陷，抓到 **5/6**。源码恢复后与基线一致：是。

| # | 注入的缺陷 | 抓到了 | 触发点 |
| --- | --- | --- | --- |
| M1 | BY 丢掉 H_m（退化为 BH） | 是 | P1、P2、P3、P4 |
| M2 | step-up 丢掉 k 因子 | 是 | P1、P2、P3、P4 |
| M3 | 只计名额的条目不占 m | 是 | BY_M_MISMATCH |
| M4 | 重叠率分母用 max(n) | 是 | OVERLAP_RATIO_NOT_ONE_ON_SUBSET |
| M5 | m_eff 无视相关性 | 是 | M_EFF_NOT_REDUCED |
| M6 | 相关性只取 lag 0 | 否（预期内） | P4 |

未抓到的那一条（相关性只取 lag 0）**是设计如此**：它改变的是「披露得多保守」（\|ρ\| 中位数从 0.41 掉到 0.12），而 P6 是单侧阈值 0.5，两种口径都在下方，结论不变。
闸门只管算得对不对，不管披露得多保守。这条如实保留。

**回归**：后端全量测试全绿（含本阶段新增 50 项，覆盖 BY 数学性质、四种读取失败必须区分、以及「带索引重建」与既有 `rebuild_series` 的逐位一致）；性质检查 9/9 全过；脚本侧测试全绿；交易层验收 PASS。

### 11.9 这一节的状态

**状态**：`ROBUST_AT_MAIN_SPEC_LAG_SENSITIVE`

判定依据：主口径结论成立（BY = 6 = Bonferroni，且 BY 严格是 BH 的子集、闸门零违规），
但滞后全档稳定性**不成立**，故不写成 `ROBUST`，而写成「主口径稳健、对滞后敏感」。

## 12. 两个阶段的状态汇总

| 阶段 | 状态 | 对定稿的影响 |
| --- | --- | --- |
| L3z 多重检验审计 | `CORRECTED_MULTIPLICITY_RESULTS` | 可引 **9 → 6**；3 条降为 `EXPLORATORY`；定稿已改 |
| L3aa 任意依赖复核 | `ROBUST_AT_MAIN_SPEC_LAG_SENSITIVE` | **无需改动**：BY 给出同一 6 条，只是多了一个不依赖 PRDS 的理由 |

当前对外可引 6 条的完整状态：

- 主口径 NW(3)：BY 拒绝 6 条，与 Bonferroni 集合相同——**不依赖 PRDS**；
- 滞后稳健性：5 条恒过，第 6 条（`phasel_alpha`，`NULL_CONFIRMED`）在 L=0/1 下不通过 BY；
- 引用时必须带的两句话：**BY 不验证 PRDS**（只是绕开）；**两两 ρ 既不能证明也不能否证 PRDS**。

## 13. 仍未解决的

| 缺口 | 状态 |
| --- | --- |
| BH 的 PRDS 前提 | **仍未验证**（L3aa 改走 BY，绕开而非验证） |
| NW 滞后与 BY 的联合稳健性 | **已测但有摆动**（六档 5/5/6/6/6/7；主口径仍 NW(3)） |
| m 的精确口径 | **偏大**（`phaseo` 与 `phasep_late` 是同源子窗口，指纹抓不到）——留待新预登记 |
| SciPy 独立参考 | 未完成（本机无网络） |
| 不同原假设数 / 未报告的试验 | UNKNOWN（预登记无逐条假设字段；不伪造） |
| 经济意义 / 可交易性 | 不在这两个阶段范围内 |

> 最后一句说明：本报告里的每一条「显著」都仍是**统计**结论，不是「按此交易能赚多少」。
> 要把横截面差距变成收益，必须过一次现金流、整手、限额与规模模拟——不在本报告范围。
