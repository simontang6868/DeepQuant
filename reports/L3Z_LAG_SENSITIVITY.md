# L3z 附：标准误（NW 滞后）敏感性 —— 补充分析

> **本分析是 L3z 阶段新增的补充分析，不是原预登记的一部分。** 主口径仍为 NW(3)（L3u 预登记规定），不因任何滞后更有利而替换。

## 一、为什么要补

L3w / L3x 预登记规定「解封需在 NW 滞后网格上不跨档」，并已对复核的 9 条执行。但另有若干可引用结论从未在该网格上验证过，本轮补齐。

网格：(0, 1, 2, 3, 4, 6)（lag=0 的 NW 不等于 iid，两者差 √((n−1)/n)）。

## 二、结果

| 结论 | 定稿分级 | n | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 | 跨档 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gate1_d` | UNDERPOWERED | 48 | 0.21 | 0.20 | 0.21 | 0.22 | 0.24 | 0.26 | 否 |
| `gate2_d` | UNDERPOWERED | 125 | 0.59 | 0.57 | 0.56 | 0.57 | 0.58 | 0.59 | 否 |
| `gate3_d` | DIRECTION_ONLY | 108 | 0.76 | 0.74 | 0.78 | 0.81 | 0.85 | 0.90 | 否 |
| `gate4_d` | UNDERPOWERED | 237 | 0.31 | 0.32 | 0.33 | 0.35 | 0.35 | 0.35 | 否 |
| `gate5_d` | UNDERPOWERED | 249 | 0.06 | 0.05 | 0.05 | 0.06 | 0.06 | 0.06 | 否 |
| `gate6_d` | QUOTABLE_MARGINAL | 237 | 1.57 | 1.52 | 1.60 | 1.66 | 1.69 | 1.73 | 否 |
| `phasec2_d` | QUOTABLE_MARGINAL | 237 | 1.93 | 1.90 | 1.83 | 1.79 | 1.79 | 1.72 | 否 |
| `phasec3_d` | UNDERPOWERED | 237 | 0.22 | 0.22 | 0.22 | 0.21 | 0.21 | 0.22 | 否 |
| `phasec_d` | QUOTABLE | 237 | 2.21 | 2.20 | 2.27 | 2.27 | 2.26 | 2.15 | 否 |
| `phasee_alpha` | UNDERPOWERED | 237 | 0.65 | 0.62 | 0.65 | 0.68 | 0.69 | 0.71 | 否 |
| `phasef_delta` | QUOTABLE_MARGINAL | 237 | 1.22 | 1.21 | 1.23 | 1.22 | 1.20 | 1.19 | 否 |
| `phaseg_alpha` | UNDERPOWERED | 237 | 0.61 | 0.61 | 0.62 | 0.63 | 0.64 | 0.65 | 否 |
| `phasej_netA` | NOT_GRADED | 237 | 0.28 | 0.32 | 0.35 | 0.37 | 0.39 | 0.42 | 否 |
| `phasek_m1net` | NOT_GRADED | 237 | 0.32 | 0.37 | 0.40 | 0.43 | 0.45 | 0.48 | 否 |
| `phasel_alpha` | NULL_CONFIRMED | 237 | 1.03 | 1.04 | 1.08 | 1.12 | 1.15 | 1.21 | 否 |
| `phaseo_netBASE` | QUOTABLE_MARGINAL | 237 | 1.08 | 1.24 | 1.36 | 1.44 | 1.51 | 1.62 | 否 |
| `phasep_late` | NOT_GRADED | 129 | 0.66 | 0.77 | 0.86 | 0.92 | 0.95 | 1.05 | **是** |
| `phaseq_max5` | UNDERPOWERED | 237 | 0.10 | 0.10 | 0.09 | 0.09 | 0.09 | 0.09 | 否 |
| `phases_net` | DIRECTION_ONLY | 129 | 0.57 | 0.66 | 0.74 | 0.79 | 0.82 | 0.90 | 否 |
| `phaset_net` | DIRECTION_ONLY | 129 | 0.57 | 0.66 | 0.74 | 0.79 | 0.82 | 0.90 | 否 |

表中数值为 |效应| / MDE 的比值；跨档指该比值在网格上落进了不同的分级桶。

**跨档条目：1 条**

- `phasep_late`：['DIRECTION_ONLY', 'QUOTABLE_MARGINAL']（主口径 L=3 为 `DIRECTION_ONLY`）

## 四、结论

- 主口径 NW(3) 保持不变。网格上跨档的有 1 条，引用时须说明其分级依赖滞后选择。
- 本分析不改变任何一条的定稿分级；跨档信息作为限定补充说明。

## 五、复现

```bash
PYTHONPATH=backend/src python scripts/l3z_lag_sensitivity.py
```

