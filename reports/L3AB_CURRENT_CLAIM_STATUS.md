# L3ab 当前引用状态：6 条结论的逐条核对

> 由 `scripts/l3ab_current_claims.py` 生成，**禁止手工编辑**。
> 版本 `L3AB-CURRENT-v1`｜生成时间 2026-10-29 22:38 +0800

本报告回答一个问题：**当前被列为可引用的 6 条结论，各自凭什么、能说到哪一步。**

每条的九项核查都给出取值，不给「总体可靠」这类结论。缺证据的写「无」，不写「不适用」。

## 0. 一览

| claim_id | 分级 | 经济属性 | 多重检验（m=24） | 扣成本正收益证据 | 前瞻证据 |
| --- | --- | --- | --- | --- | --- |
| `phasel_alpha` | NULL_CONFIRMED | 毛利 alpha | 四种全过 | 无 | 无 |
| `phasec_d` | QUOTABLE | 横截面价差 | 四种全过 | 无 | 无 |
| `gate6_d` | QUOTABLE_MARGINAL | 横截面价差 | 四种全过 | 无 | 无 |
| `phasec2_d` | QUOTABLE_MARGINAL | 横截面价差 | 四种全过 | 无 | 无 |
| `phasef_delta` | QUOTABLE_MARGINAL | 横截面价差 | 四种全过 | 无 | 无 |
| `phaseo_netBASE` | QUOTABLE_MARGINAL | 净收益 | 四种全过 | 有 | 无 |

⚠️ 最要紧的一行：**6 条里只有 1 条（`phaseo_netBASE`）带扣成本后的正收益证据，而且它是样本内的。** 其余 5 条是价差或毛利 alpha，不能读成「能赚多少」。

## `phasel_alpha`｜NULL_CONFIRMED

**结论句（对外原话，直接抄）**：SURGE 避让后的 alpha 只剩 +0.11pp（t=+3.13），MDE 0.09pp、事后功效 88%：判「净效应被吸收」，即确认无增量。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MATCH`；— |
| 2 统计方法独立核对 | PASS（L3u `MATCH`；后续层 `None`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni —｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL`（不跨档）；m 口径影响：有 |
| 5 经济解释 | 毛利 alpha（未扣成本） |
| 6 扣成本后正向收益证据 | **无**（该判定量不是组合收益，扣成本在定义上无处可扣） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | SURGE 有可交易 alpha |

**引用时必须同时给出的限定**：

- 「确认无效应」限定于相对同池平均的增量，不是绝对收益为零
- 该序列与 phaser 完全同一份数字，不能当作两次独立验证

⚠️ **本条是零效应判定**：它的「通过」含义是「这个非零信号本身是真的」，**不是**「存在可利用的净收益」。任何把它读成正向收益的表述都越界。

## `phasec_d`｜QUOTABLE

**结论句（对外原话，直接抄）**：池效应的判定量 spread(B)−spread(A) 为 +1.94pp（t=+6.37）、MDE 0.85pp，|效应|/MDE 2.27 倍；格 A 自身的水平是 +0.01pp，与本条的差值不可混用。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MISMATCH_MEAN`；mean: L3t=+0.014071 vs 判定=+1.938968（差 -1.924897） |
| 2 统计方法独立核对 | PASS（L3u `MISMATCH_MEAN`；后续层 `l3w_unseal`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE、QUOTABLE、QUOTABLE、QUOTABLE、QUOTABLE、QUOTABLE`（不跨档）；m 口径影响：无 |
| 5 经济解释 | 横截面价差（不是任何组合的收益水平） |
| 6 扣成本后正向收益证据 | **无**（该判定量不是组合收益，扣成本在定义上无处可扣） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | 见下方限定 |

**引用时必须同时给出的限定**：

- 该值是两格 spread 之差，不是任何单一组合的收益水平
- spread 与 d 差 2 倍，跨文献引用须换算

## `gate6_d`｜QUOTABLE_MARGINAL

**结论句（对外原话，直接抄）**：全 A as-of 口径的 d 均值为 +0.71pp（t=+4.39 iid／+4.64 NW3），MDE 0.43pp、事后功效 100%，但 |效应|/MDE 只有 1.66 倍。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MISMATCH_MEAN`；mean 一致但 t 不同：L3t=+4.6443 vs 判定=+4.3861（多为 NW 滞后或估计量口径差异） |
| 2 统计方法独立核对 | PASS（L3u `MISMATCH_MEAN`；后续层 `l3w_unseal`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL`（不跨档）；m 口径影响：无 |
| 5 经济解释 | 横截面价差（不是任何组合的收益水平） |
| 6 扣成本后正向收益证据 | **无**（该判定量不是组合收益，扣成本在定义上无处可扣） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | 见下方限定 |

**引用时必须同时给出的限定**：

- 头条 +4.39 是 iid t，同序列 NW3 为 +4.64，引用时须同列两数
- 这是横截面月度差距，不是可交易组合的账户收益
- d 与文献 spread 差 2 倍

## `phasec2_d`｜QUOTABLE_MARGINAL

**结论句（对外原话，直接抄）**：停牌效应的增量 +0.11pp（t=+5.01）、MDE 0.06pp，|效应|/MDE 1.79 倍；池效应本身的水平是 +0.66pp，不是本条的增量。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MISMATCH_MEAN`；mean: L3t=+0.658190 vs 判定=+0.106944（差 +0.551245） |
| 2 统计方法独立核对 | PASS（L3u `MISMATCH_MEAN`；后续层 `l3w_unseal`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL`（不跨档）；m 口径影响：无 |
| 5 经济解释 | 横截面价差（不是任何组合的收益水平） |
| 6 扣成本后正向收益证据 | **无**（该判定量不是组合收益，扣成本在定义上无处可扣） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | 停牌效应就是池效应 |

**引用时必须同时给出的限定**：

- 检验的是相对基准格的增量，不是停牌效应的绝对规模
- 统计显著但量级小：0.11pp/月 对一个组合的实际影响需另算

## `phasef_delta`｜QUOTABLE_MARGINAL

**结论句（对外原话，直接抄）**：MAX5 的 delta 为 +0.74pp（t=+3.41），MDE 0.61pp，|效应|/MDE 1.22 倍，是本项目最接近可引用的横截面结论。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MATCH`；— |
| 2 统计方法独立核对 | PASS（L3u `MATCH`；后续层 `None`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL`（不跨档）；m 口径影响：无 |
| 5 经济解释 | 横截面价差（不是任何组合的收益水平） |
| 6 扣成本后正向收益证据 | **无**（该判定量不是组合收益，扣成本在定义上无处可扣） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | 见下方限定 |

**引用时必须同时给出的限定**：

- 1.22 倍仍低于 2 倍门槛，只能作为「可引用且须标 MDE」处理
- delta 是两格 d 之差，不是组合收益

## `phaseo_netBASE`｜QUOTABLE_MARGINAL

**结论句（对外原话，直接抄）**：3000 万档、BASE 费率（单边 0.051%）下净 alpha 为 +0.14pp（t=+4.04），MDE 0.09pp，|效应|/MDE 1.44 倍。

| 核查项 | 结果 |
| --- | --- |
| 1 数字可追溯 | L3u 核对类别 `MATCH`；— |
| 2 统计方法独立核对 | PASS（L3u `MATCH`；后续层 `None`） |
| 3 多重检验（m=24） | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 3b 替代口径 m=27 | Bonferroni 通过｜Holm 通过｜BH 通过｜BY 通过 |
| 4 标准误/滞后敏感性 | NW(3) 为主口径；滞后网格分级轨迹 `QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL、QUOTABLE_MARGINAL`（不跨档）；m 口径影响：无 |
| 5 经济解释 | 扣除成本后的组合净收益（已带成本口径与资金档） |
| 6 扣成本后正向收益证据 | **有**（净收益口径） |
| 7 样本外/前瞻证据 | **无**。全部为历史样本内结果；无前瞻期数据参与 |
| 8 允许的引用措辞 | 见上方结论句；附带限定见下 |
| 9 不允许的推论 | 见下方限定 |

**引用时必须同时给出的限定**：

- 1.44 倍仍低于 2 倍门槛，引用必须同时给 MDE
- EXECUTABLE_UNDER_BASE_FEES 不等于「可用」：换到压力费率档后净 alpha 逼近 0
- 该序列与 phasev 完全同一份数字，不能当作两次独立验证

## 全族层面的五条限制

1. 多重检验主口径 m=24（数值指纹去重）；m=27 为替代口径，两者对 5 条正面结论的结论相同，仅 `phasel_alpha` 在 m=27 + Bonferroni 下掉出。
2. Benjamini–Yekutieli 在任意依赖下控制错误发现率；它不验证正相依（PRDS）假设，只是绕开。
3. 标准误一律 Newey-West(3)，p 用 t 分布双侧；滞后网格 {0,1,2,3,4,6} 下的分级轨迹单独列出。
4. 全部结论都在历史样本内产生，不存在前瞻期证据；「统计显著」不等于「经济有意义」，「净收益为正（样本内）」也不等于「可实盘交易」。
5. 本清单只覆盖 L3u 登记在册的 27 条。至少有一个已产生结论的实验（`phasei_out`，exQ5 可成交）不在该家族内，因此也不在本清单的校正范围内。

## 依凭的产物与哈希

| 文件 | sha256 |
| --- | --- |
| `reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json` | `f7d1a66d64c386e1…` |
| `reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json` | `a138f8803ea45ca0…` |
| `reports/gates/H-L3AA-DEPENDENCE/l3aa_dependence.json` | `dffcf4645cfe8c2b…` |
| `reports/gates/H-L3AB-FAMILY/l3ab_family.json` | `116a06aa68393205…` |
| `docs/gates/l3v_claims.json` | `5b4173c7265e5dd5…` |
| `docs/gates/l3ab_family_decisions.json` | `afdf3fded8def1a0…` |
| `reports/L3Z_LAG_SENSITIVITY.json` | `b60428758948e261…` |

## 复现

```
PYTHONPATH=backend/src python scripts/l3ab_family_audit.py
PYTHONPATH=backend/src python scripts/l3ab_current_claims.py
```

