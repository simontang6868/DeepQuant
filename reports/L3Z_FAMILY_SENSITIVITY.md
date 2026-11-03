# L3z 附：检验家族定义复核与敏感性

## 一、五种计数必须分开

| 计数口径 | 数量 | 含义 |
| --- | --- | --- |
| 登记条目数 | 27 | |
| 不同序列路径数 | 27 | |
| 不同数值序列数 | 24 | |
| 不同原假设数 | UNKNOWN | |
| 多门槛结论数 | 7 | |
| 实际参与校正的检验数 | 20 | |
| 最终报告的结论数 | 27 | |

⚠️ **「不同数值序列数」不等于「统计独立的检验数」。去重只合并了逐位相同的重复登记；余下检验之间仍可能高度相关（同期、同池、同族）。因此 24 是不同数值序列的数目，不是独立检验数的估计，也不应被当作相关结构的证据。**

「不同原假设数」标记为 **UNKNOWN**：预登记未逐条登记假设，而 L3u 已证明 13 条判定字符串失真，因此不能用判定字符串反推假设。此项不补全。

## 二、重复登记（数值逐位相同）

| 组 | 成员 |
| --- | --- |
| — | phasel_alpha、phaser_alpha |
| — | phaseo_netBASE、phasev_net |
| — | phases_net、phaset_net |

这 3 组是**同一次检验被登记了两次**，判定不同但数字逐位相同。把它们算成 6 次检验会凭空抬高其余结论的门槛。

## 三、逐条清单

| 结论 | 族 | n | p (NW3) | 共用序列 | 重复登记 | 多门槛 | 进入家族 | 排除/纳入理由 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gate1_d` | A_cross_spread | 48 | 0.545309 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `gate2_d` | A_cross_spread | 125 | 0.113348 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `gate3_d` | A_cross_spread | 108 | 0.024529 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `gate4_d` | A_cross_spread | 237 | 0.333570 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `gate5_d` | A_cross_spread | 249 | 0.876952 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `gate6_d` | A_cross_spread | 237 | 0.000006 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasec_d` | A_cross_spread | 237 | 0.000000 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasec2_d` | A_cross_spread | 237 | 0.000001 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasec3_d` | A_cross_spread | 237 | 0.550840 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasee_alpha` | B_long_only | 237 | 0.058478 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasef_delta` | A_cross_spread | 237 | 0.000768 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phaseg_alpha` | B_long_only | 237 | 0.079017 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasel_alpha` | B_long_only | 237 | 0.001972 | phaser_alpha | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasem_spread` | A_cross_spread | 237 | 0.004751 | — | 否 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `phasej_netA` | B_long_only | 237 | 0.298312 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasek_m1net` | B_long_only | 237 | 0.233460 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phaseo_netBASE` | B_long_only | 237 | 0.000073 | phasev_net | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasep_late` | C_regression | 129 | 0.010970 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phaser_alpha` | B_long_only | 237 | 0.001972 | phasel_alpha | 是 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `phases_net` | B_long_only | 129 | 0.028317 | phaset_net | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phaset_net` | B_long_only | 129 | 0.028317 | phases_net | 是 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `phasev_net` | B_long_only | 237 | 0.000073 | phaseo_netBASE | 是 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `phaseq_max5` | C_regression | 237 | 0.799747 | — | 否 | 否 | 计分 | 具备可核实的单一均值与 t，参与校正 |
| `l3r_delta` | A_cross_spread | 197 | 0.335784 | — | 否 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `l3s_ic` | D_ic | 198 | 0.089673 | — | 否 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `l3_test` | E_fold | 27 | 0.167951 | — | 否 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |
| `l3p_test` | E_fold | 28 | 0.065903 | — | 否 | 是 | 只计名额 | 判定由多门槛给出，无单一 p 值；计入 m 但不参与评分 |

## 四、三种 m 口径下的校正结果

| 口径 | m | 参与评分 | 只计名额 | Bonferroni | Holm | BH |
| --- | --- | --- | --- | --- | --- | --- |
| m = 24（数值指纹去重，主口径） | 24 | 20 | 7 | 6 | 6 | 7 |
| m = 27（登记条目数，替代口径） | 27 | 20 | 7 | 5 | 6 | 7 |

**m = 24（数值指纹去重，主口径）**（把逐位相同的重复登记合并为一次检验；被封印的检验仍计入 m）

- Bonferroni 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phasel_alpha、phaseo_netBASE
- Holm 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phasel_alpha、phaseo_netBASE
- BH 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phasel_alpha、phaseo_netBASE、phasep_late

**m = 27（登记条目数，替代口径）**（不做去重，每条登记都算一次检验；这是最保守的上界）

- Bonferroni 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phaseo_netBASE
- Holm 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phasel_alpha、phaseo_netBASE
- BH 拒绝：gate6_d、phasec2_d、phasec_d、phasef_delta、phasel_alpha、phaseo_netBASE、phasep_late

两口径的 BH 拒绝集合差异：无。Bonferroni 差异见上（phasel_alpha 在 m=24 下通过、m=27 下不通过）。

## 五、分族方案

| 族 | m | 参与评分 | BH 拒绝 |
| --- | --- | --- | --- |
| A_cross_spread | 12 | 10 | gate6_d、phasec2_d、phasec_d、phasef_delta |
| B_long_only | 10 | 8 | phasel_alpha、phaseo_netBASE |
| C_regression | 2 | 2 | phasep_late |
| D_ic | 1 | 0 | 无 |
| E_fold | 2 | 0 | 无 |

分族依据是 L3t 定义的**口径族**（横截面价差 / 单边 / 回归 / IC / 折），不是为了让更多条目通过而事后拆分。族内 m 更小，门槛更松，但族内假设的相关性也更高——BH 对正相关相对稳健，Bonferroni 则会因此偏保守。

## 六、BH 的适用性

BH 要求 p 值满足特定的依赖结构（独立或 PRDS）。本项目的检验之间**存在已知的正相关**（同池、同期、同一份序列被登记多次），但**没有做依赖结构的实证检验**。因此：

- 不能把「一般意义上正相关时 BH 相对稳健」当作已经证明满足所需条件；
- 本轮把 BH 作为**参考口径**，把 Bonferroni / Holm 作为 FWER 口径并列报告；
- 结论以三者一致的部分为准；只在 BH 下通过、FWER 下不通过的条目，引用时必须说明用的是 FDR 口径。

## 七、复现

```bash
PYTHONPATH=backend/src python scripts/l3z_family_sensitivity.py
```

