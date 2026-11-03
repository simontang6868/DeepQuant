# 当前有效引用清单（L3ab）

> **版本** `L3AB-CURRENT-v1`｜**生成时间** 2026-10-29 22:38 +0800｜**状态** `CURRENT_WITH_DOCUMENTED_LIMITATIONS`
>
> 本文件由 `scripts/l3ab_current_claims.py` 生成，**禁止手工编辑**；改数字会导致重建失败。
> 它取代的不是历史报告，而是「需要读者自己拼接当前结论」这件事：
> 历史报告与更正记录一律保留（见 §6），但**当前有效的引用状态只以本文件为准**。

## 1. 一句话结论

登记在册的 27 条结论中，**6 条**当前可对外引用：
1 条 `QUOTABLE`、4 条 `QUOTABLE_MARGINAL`、1 条 `NULL_CONFIRMED`。

**但这 6 条里只有 1 条带扣成本后的正收益证据，且全部是样本内结果。**
「统计上站得住」与「能赚多少」「能不能实盘」是三件事，本文件分三栏写。

## 2. 校正口径（引用时必须一并说明）

| 项目 | 取值 |
| --- | --- |
| 检验家族 | L3u 登记在册的 27 条（**不含**家族外实验，见 §5） |
| 主口径 m | **24**（按 `(mean, t, n)` 数值指纹去重；A 类重复登记 0 条） |
| 替代口径 m | 27（全部登记条目）。仅影响 `phasel_alpha`，不影响任何正面结论 |
| 校正方法 | Bonferroni / Holm / BH / Benjamini–Yekutieli，四种结果并列 |
| 标准误 | Newey-West(3)，Bartlett 核；p 用 t 分布双侧，df = n − 1 |
| 显著性水平 | α = 0.05（FWER 类）；q = 0.05（FDR 类） |
| 依赖结构 | BY 不要求独立性或 PRDS；BH 要求 PRDS，该假设**未验证**，故 BH 的结果只作对照 |

## 3. 可引用清单

| claim_id | 分级 | 结论句 | 统计（m=24） | 经济属性 | 扣成本正收益 | 前瞻 |
| --- | --- | --- | --- | --- | --- | --- |
| `phasel_alpha` | NULL_CONFIRMED | SURGE 避让后的 alpha 只剩 +0.11pp（t=+3.13），MDE 0.09pp、事后功效 88%：判「净效应被吸收」，即确认无增量。 | 四种全过=是 | 毛利 alpha | 无 | 无 |
| `phasec_d` | QUOTABLE | 池效应的判定量 spread(B)−spread(A) 为 +1.94pp（t=+6.37）、MDE 0.85pp，\|效应\|/MDE 2.27 倍；格 A 自身的水平是 +0.01pp，与本条的差值不可混用。 | 四种全过=是 | 横截面价差 | 无 | 无 |
| `gate6_d` | QUOTABLE_MARGINAL | 全 A as-of 口径的 d 均值为 +0.71pp（t=+4.39 iid／+4.64 NW3），MDE 0.43pp、事后功效 100%，但 \|效应\|/MDE 只有 1.66 倍。 | 四种全过=是 | 横截面价差 | 无 | 无 |
| `phasec2_d` | QUOTABLE_MARGINAL | 停牌效应的增量 +0.11pp（t=+5.01）、MDE 0.06pp，\|效应\|/MDE 1.79 倍；池效应本身的水平是 +0.66pp，不是本条的增量。 | 四种全过=是 | 横截面价差 | 无 | 无 |
| `phasef_delta` | QUOTABLE_MARGINAL | MAX5 的 delta 为 +0.74pp（t=+3.41），MDE 0.61pp，\|效应\|/MDE 1.22 倍，是本项目最接近可引用的横截面结论。 | 四种全过=是 | 横截面价差 | 无 | 无 |
| `phaseo_netBASE` | QUOTABLE_MARGINAL | 3000 万档、BASE 费率（单边 0.051%）下净 alpha 为 +0.14pp（t=+4.04），MDE 0.09pp，\|效应\|/MDE 1.44 倍。 | 四种全过=是 | 净收益 | 有 | 无 |

### 3.1 三件事分开说

| 层次 | 这些结论能支持什么 | 不能支持什么 |
| --- | --- | --- |
| **统计** | 在给定家族与 m 下，这些差异不是随机噪声 | 不能说明差异有多大用；不能说明其他家族外的结论也成立 |
| **经济** | `phaseo_netBASE` 在 BASE 费率、3000 万资金、样本内的净 alpha 为正 | 其余 5 条没有扣成本收益；`phasel_alpha` 是零效应判定，方向相反 |
| **交易可用性** | **没有任何一条提供可交易性证据** | 无前瞻样本、无券商交割单核对、无实盘 |

## 4. 逐条限定（引用时不可省略）

### `phasel_alpha`｜NULL_CONFIRMED

- **可引用句**：SURGE 避让后的 alpha 只剩 +0.11pp（t=+3.13），MDE 0.09pp、事后功效 88%：判「净效应被吸收」，即确认无增量。
- **限定**：「确认无效应」限定于相对同池平均的增量，不是绝对收益为零
- **限定**：该序列与 phaser 完全同一份数字，不能当作两次独立验证
- **禁止表述**：SURGE 有可交易 alpha
- **经济属性**：毛利 alpha（未扣成本）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

### `phasec_d`｜QUOTABLE

- **可引用句**：池效应的判定量 spread(B)−spread(A) 为 +1.94pp（t=+6.37）、MDE 0.85pp，|效应|/MDE 2.27 倍；格 A 自身的水平是 +0.01pp，与本条的差值不可混用。
- **限定**：该值是两格 spread 之差，不是任何单一组合的收益水平
- **限定**：spread 与 d 差 2 倍，跨文献引用须换算
- **经济属性**：横截面价差（不是任何组合的收益水平）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

### `gate6_d`｜QUOTABLE_MARGINAL

- **可引用句**：全 A as-of 口径的 d 均值为 +0.71pp（t=+4.39 iid／+4.64 NW3），MDE 0.43pp、事后功效 100%，但 |效应|/MDE 只有 1.66 倍。
- **限定**：头条 +4.39 是 iid t，同序列 NW3 为 +4.64，引用时须同列两数
- **限定**：这是横截面月度差距，不是可交易组合的账户收益
- **限定**：d 与文献 spread 差 2 倍
- **经济属性**：横截面价差（不是任何组合的收益水平）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

### `phasec2_d`｜QUOTABLE_MARGINAL

- **可引用句**：停牌效应的增量 +0.11pp（t=+5.01）、MDE 0.06pp，|效应|/MDE 1.79 倍；池效应本身的水平是 +0.66pp，不是本条的增量。
- **限定**：检验的是相对基准格的增量，不是停牌效应的绝对规模
- **限定**：统计显著但量级小：0.11pp/月 对一个组合的实际影响需另算
- **禁止表述**：停牌效应就是池效应
- **经济属性**：横截面价差（不是任何组合的收益水平）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

### `phasef_delta`｜QUOTABLE_MARGINAL

- **可引用句**：MAX5 的 delta 为 +0.74pp（t=+3.41），MDE 0.61pp，|效应|/MDE 1.22 倍，是本项目最接近可引用的横截面结论。
- **限定**：1.22 倍仍低于 2 倍门槛，只能作为「可引用且须标 MDE」处理
- **限定**：delta 是两格 d 之差，不是组合收益
- **经济属性**：横截面价差（不是任何组合的收益水平）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

### `phaseo_netBASE`｜QUOTABLE_MARGINAL

- **可引用句**：3000 万档、BASE 费率（单边 0.051%）下净 alpha 为 +0.14pp（t=+4.04），MDE 0.09pp，|效应|/MDE 1.44 倍。
- **限定**：1.44 倍仍低于 2 倍门槛，引用必须同时给 MDE
- **限定**：EXECUTABLE_UNDER_BASE_FEES 不等于「可用」：换到压力费率档后净 alpha 逼近 0
- **限定**：该序列与 phasev 完全同一份数字，不能当作两次独立验证
- **经济属性**：扣除成本后的组合净收益（已带成本口径与资金档）
- **前瞻**：无（全部为历史样本内结果；无前瞻期数据参与）

## 5. 适用范围（容易越界的一条）

本清单的「全族校正」只覆盖 **L3u 登记在册的 27 条**。审计已确认至少有一个已产生完整结论的实验**不在**该家族内（`phasei_out`，exQ5 可成交，V0 +0.2307pp / t=6.357、V1 +0.1634pp / t=4.528）。

> 因此不能把「通过了全族校正」推广成「项目全部结论都通过了校正」。

## 6. 历史与更正（保留，不覆盖）

| 文件 | 角色 |
| --- | --- |
| `docs/DELIVERABLE_QUOTE_SHEET.md` | L3y 的对外定稿（9→6 修订后版本）。本清单不改它 |
| `reports/L3Z_MULTIPLICITY_INDEPENDENT_AUDIT.md` | L3z 审计：3 处实现缺陷的更正 |
| `reports/L3AA_ARBITRARY_DEPENDENCE_AUDIT.md` | L3aa：BY 复核。其中「m=24 偏大」一句已被本阶段撤回 |
| `reports/L3AB_TEST_FAMILY_AUDIT.md` | 本阶段：家族审计与撤回记录 |

## 7. 依凭产物

| 文件 | sha256 |
| --- | --- |
| `reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json` | `f7d1a66d64c386e1…` |
| `reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json` | `a138f8803ea45ca0…` |
| `reports/gates/H-L3AA-DEPENDENCE/l3aa_dependence.json` | `dffcf4645cfe8c2b…` |
| `reports/gates/H-L3AB-FAMILY/l3ab_family.json` | `116a06aa68393205…` |
| `docs/gates/l3v_claims.json` | `5b4173c7265e5dd5…` |
| `docs/gates/l3ab_family_decisions.json` | `afdf3fded8def1a0…` |
| `reports/L3Z_LAG_SENSITIVITY.json` | `b60428758948e261…` |

## 8. 复现

```
PYTHONPATH=backend/src python scripts/l3ab_family_audit.py
PYTHONPATH=backend/src python scripts/l3ab_current_claims.py
```

