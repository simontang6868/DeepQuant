# L3w 封印条目解封复核：6 条里哪些其实从未不可核对

> 预登记 `docs/gates/l3w_prereg.json` sha256 `709d76ddf176…`（脚本启动校验）｜对照 L3u `f7d1a66d64c3…`（启动校验）｜**只读审计，不重跑策略**

## 一、结论先行

**6 条「核对未通过」里，4 条从未不可核对，只是 L3t 登记时抄错了列或窗口。**另外 2 条不是抄错，而是**判定量与序列本来不是同一个东西**，拼在一起算 ratio 就是拼口径——这两种情况被 L3u 的同一条规则压成了同一格。

解封后全项目 `QUOTABLE` 从 0 条变为 **1 条**；最高的是 `phasec_d`，|效应|/MDE = 2.2728，分级 `QUOTABLE`。

也就是说：**我们不是没有可引用的点估计，而是把它锁在「核对未通过」这一格里没放出来。**反过来说，这条放出来之前已经在网格上验过 6 个滞后不至于跨桶（见 §四）。

## 二、6 条逐条

| 条目 | 核对分类 | 均值同源 | t 同源 | 分母同源 | ratio(NW3) | 解封分级 | 结论 |
|---|---|---|---|---|---|---|---|
| `gate3_d` | A_same_source | ✓ | t_iid | ✓ | 0.814 | DIRECTION_ONLY | UNSEALED |
| `phasec_d` | A_same_source | ✓ | t_nw3 | ✓ | 2.273 | QUOTABLE | UNSEALED |
| `phasec2_d` | A_same_source | ✓ | t_nw3 | ✓ | 1.790 | QUOTABLE_MARGINAL | UNSEALED |
| `phasec3_d` | A_same_source | ✓ | t_nw3 | ✓ | 0.213 | UNDERPOWERED | UNSEALED |
| `phasej_netA` | B_different_budget | ✗ | ✗ | ✓ | 0.372 | NOT_GRADED | STAY_SEALED |
| `phasep_late` | B_regression_coef | ✓ | ✗ | ✗ | 0.921 | NOT_GRADED | STAY_SEALED |

## 三、为什么 B 类不能放

### `phasej_netA`（B_different_budget）

- 判定均值 +0.0424 与序列均值 +0.0324 相对差 2.35e-01（阈值 1e-04）：判定档与序列档不同源，mean 与 MDE 不可拼
- 判定 t=+1.2688 与序列的 t_iid=+0.7785／t_nw3=+1.0424 均不符：该 t 不是这条序列均值的 t（回归系数 / 另一模型），不能与序列 MDE 配对

### `phasep_late`（B_regression_coef）

- 判定 t=-0.2158 与序列的 t_iid=+1.8372／t_nw3=+2.5813 均不符：该 t 不是这条序列均值的 t（回归系数 / 另一模型），不能与序列 MDE 配对
- 判定分母 237 ≠ 序列 n 129：必须确认是回归分母还是子样本窗口

这两条的 ratio 看起来也能算，但分子与分母来自不同的东西：一个把 3000 万档的判定均值配上 1000 万档序列的 MDE，另一个把回归系数的 t 配上序列均值的 MDE。**能算不等于该算**——放宽到这一步，分级就不再是「效应量相对自身分辨率」了。

## 四、分级稳定性：NW 滞后网格

同一序列、同一实现，只改滞后。**跨桶就不解封**——那说明结论对标准误口径敏感。

| 条目 | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 | 稳定 |
|---|---|---|---|---|---|---|---|
| `gate3_d` | DIRECTION_ONLY | DIRECTION_ONLY | DIRECTION_ONLY | DIRECTION_ONLY | DIRECTION_ONLY | DIRECTION_ONLY | 是 |
| `phasec_d` | QUOTABLE | QUOTABLE | QUOTABLE | QUOTABLE | QUOTABLE | QUOTABLE | 是 |
| `phasec2_d` | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | 是 |
| `phasec3_d` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `phasej_netA` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `phasep_late` | UNDERPOWERED | NULL_WEAK | NULL_WEAK | NULL_WEAK | NULL_WEAK | NULL_CONFIRMED | 否 |

各档 MDE（pp，= 2.8016 × se_NW）：

| 条目 | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 |
|---|---|---|---|---|---|---|
| `gate3_d` | 0.638 | 0.656 | 0.619 | 0.595 | 0.572 | 0.537 |
| `phasec_d` | 0.876 | 0.882 | 0.856 | 0.853 | 0.858 | 0.902 |
| `phasec2_d` | 0.055 | 0.056 | 0.058 | 0.060 | 0.060 | 0.062 |
| `phasec3_d` | 0.209 | 0.214 | 0.216 | 0.220 | 0.218 | 0.216 |
| `phasej_netA` | 0.117 | 0.102 | 0.093 | 0.087 | 0.083 | 0.077 |
| `phasep_late` | 0.197 | 0.169 | 0.151 | 0.140 | 0.136 | 0.123 |

## 五、解封后全项目分级

| 分级 | 解封前 | 解封后 |
|---|---|---|
| DIRECTION_ONLY | 2 | 3 |
| NOT_GRADED | 15 | 11 |
| NULL_CONFIRMED | 1 | 1 |
| QUOTABLE | 0 | 1 |
| QUOTABLE_MARGINAL | 2 | 3 |
| UNDERPOWERED | 7 | 8 |

发生变化的条目：

| 条目 | 解封前 | 解封后 |
|---|---|---|
| `gate3_d` | NOT_GRADED | DIRECTION_ONLY |
| `phasec_d` | NOT_GRADED | QUOTABLE |
| `phasec2_d` | NOT_GRADED | QUOTABLE_MARGINAL |
| `phasec3_d` | NOT_GRADED | UNDERPOWERED |

## 六、预测对照（先于测量写定）

| # | 预测 | 实测 | 结论 |
|---|---|---|---|
| P1 | 6 条里恰好 4 条通过三关（gate3_d、phasec_d、phasec2_d、phasec3_d），2 条不通过（phasej_netA 均值不同档、phasep_late 的 t 不是序列均值的 t）。 | {"n_unsealed": 4, "keys": ["gate3_d", "phasec2_d", "phasec3_d", "phasec_d"]} | 成立 |
| P2 | phasec_d 在 NW(3) 下 \|效应\|/MDE ≥ 2，解封为 QUOTABLE。 | {"ratio": 2.272806460750329, "tier": "QUOTABLE"} | 成立 |
| P3 | phasec_d 的分级在 lag ∈ {0,1,2,3,4,6} 全档恒为 QUOTABLE（不跨桶）。 | {"stable": true, "tiers": {"0": "QUOTABLE", "1": "QUOTABLE", "2": "QUOTABLE", "3": "QUOTABLE", "4": "QUOTABLE", "6": "QUOTABLE"}} | 成立 |
| P4 | phasec2_d 在网格内至少一档跨桶（跌出 QUOTABLE_MARGINAL）。 | {"stable": true} | **被反驳** |
| P5 | gate3_d 解封后为 DIRECTION_ONLY（\|效应\|/MDE 在 0.7~1.0 之间）。 | {"tier": "DIRECTION_ONLY", "ratio": 0.8141767632864441} | 成立 |
| P6 | 合并后全项目 QUOTABLE = 1，且相对 L3u 分级发生变化的恰好 4 条。 | {"quotable_after": 1, "n_changed": 4} | 成立 |

被反驳：P4。被反驳的预测原样保留，不改判据（R5）。

## 七、L3v 卡片补丁

L3v 的卡片与产物**不修改**（历史记录）。解封结果单独输出为 `l3v_claims_patch.json`；把它合并进 `docs/gates/l3v_claims.json` 并重跑 L3v 闸门，是另一个动作，需要单独的改动记录。

| 条目 | 卡片现值 | 建议值 |
|---|---|---|
| `gate3_d` | NOT_GRADED | DIRECTION_ONLY |
| `phasec_d` | NOT_GRADED | QUOTABLE |
| `phasec2_d` | NOT_GRADED | QUOTABLE_MARGINAL |
| `phasec3_d` | NOT_GRADED | UNDERPOWERED |

## 八、限制

- 本实验不重跑任何策略，只从 L3u 已重建的判定序列上重算 NW 滞后网格；L3u 产物 sha256 在启动时校验。
- 解封不等于推翻 L3u：L3u 的封印记录原样保留，本实验是追加的「分级意见」，两者并列可查。
- 解封后的条目仍然要带 MDE 与窗口口径引用，QUOTABLE 只表示「效应量达到自身分辨率的 2 倍」，不表示「可以拿去做产品」。
- phasej 与 phasep 的拒绝解封不代表它们不重要，只代表「现有 summary 与序列不可拼」，要分级必须先把 3000 万档的序列重新导出（新实验）。
- 稳定性网格只改 NW 滞后，不改窗口、不改 dof 修正；因此它检验的是「标准误口径敏感性」，不是「样本外稳健性」。
- B 类要保持可查而不是被忘掉：`phasej_netA` 与 `phasep_late` 仍列为封印，要分级必须先把对应资金档的序列单独导出（新实验）。

