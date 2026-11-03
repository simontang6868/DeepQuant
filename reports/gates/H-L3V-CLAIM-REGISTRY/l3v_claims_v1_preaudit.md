# L3v 结论卡片注册表：27 条结论的对外表述唯一来源

> 预登记 `docs/gates/l3v_prereg.json` sha256 `16f102726526…`（脚本启动校验）｜对照 L3u `f7d1a66d64c3…`（启动校验）｜卡片表 sha256 `9da9b0977107…`（只记录不冻结）｜**只读审计，不重跑策略**

## 一、结论先行

**27 条结论里，能放心单引点估计的有 1 条。**按强度分：QUOTABLE（1）：`phasec_d`；QUOTABLE_MARGINAL（4）：`gate6_d`、`phasec2_d`、`phasef_delta`、`phaseo_netBASE`；DIRECTION_ONLY（3）：`gate3_d`、`phases_net`、`phaset_net`；NULL_CONFIRMED（1）：`phasel_alpha`；UNDERPOWERED（12）：`gate1_d`、`gate2_d`、`gate4_d`、`gate5_d`、`phasec3_d`、`phasee_alpha`、`phaseg_alpha`、`phaseq_max5`、`l3r_delta`、`l3s_ic`、`l3_test`、`l3p_test`；NOT_GRADED（6）：`phasem_spread`、`phasej_netA`、`phasek_m1net`、`phasep_late`、`phaser_alpha`、`phasev_net`。

另外两个结构性事实：**27 条只对应 24 份独立数字**（3 组重复，共 6 个 key 共用同一份序列）；**6 条因为 L3u 核对未通过而不参与分级**——它们的参考分级只作内部提示，任何对外材料都不得使用。

其中 **9 条的分级不是来自 L3u，而是来自 L3w / L3x 解封复核**（`tier_basis="l3w_unseal"`）：它们当初被判「核对未通过」，复核证明要么是 L3t 抄错了列或窗口，要么只是 t 用了 iid 而登记表用 NW(3)，判定量与序列本身完全同源。解封要求卡片的分级**逐字等于**复核给出的一档，不能自定；复核还要求在 NW 滞后网格上不跨桶。

闸门本次检出 **0 处违规**，最终状态 `CLAIMS_CONSISTENT`。

## 二、可对外的 9 条（含 1 条零效应判定）

其余条目要么是 `UNDERPOWERED`（只能说「无法判断」）、要么是 `NOT_GRADED`（核对未通过，不参与分级）。下面每一条都可以直接抄进对外材料，但**必须连同限定一起抄**。

### 1. `gate3_d`｜DIRECTION_ONLY

**`gate3_d`**（A_cross_spread，单位 pp）
> Gate 3 判定窗口（n=108）的 d 均值为 +0.48pp（t=+2.28）、MDE 0.59pp，|效应|/MDE 0.81 倍；另有另一次重跑给出 −0.40pp、t=−1.69、n=125，两者窗口不同，不可混引。

- 窗口：segment_2007_2015（Gate 3 的判定窗口；L3t 误用了该实验 2016-2026 的重跑文件）
- 判定估计量：nw3
- d 定义：0.5*long-0.5*short
- 限定：本条的 −0.40pp 是另一个窗口的数字，不得与本窗口的 +0.48pp 混引
- 限定：判定窗口不可跨期比较；样本起点（2005）年末仅 6 只，不纳入
- 可引用强度：DIRECTION_ONLY
- 分级依据：L3u 判核对未通过（L3t 取了 2016-2026 重跑文件，n 与 mean 均不同）；L3w 复核证明判定量与序列同源，|效应|/MDE=0.814，NW 滞后 0/1/2/3/4/6 全档恒为 DIRECTION_ONLY
- 分级来源：`l3w_unseal`（解封复核，非 L3u 正式分级；L3u 记为 NOT_GRADED）
- 来源：L3u 正式分级 NOT_GRADED｜序列 `gate3_out/9b7235d77689/monthly_paired.csv::d`（n=108）

### 2. `gate6_d`｜QUOTABLE_MARGINAL

**`gate6_d`**（A_cross_spread，单位 pp）
> 全 A as-of 口径的 d 均值为 +0.71pp（t=+4.39 iid／+4.64 NW3），MDE 0.43pp、事后功效 100%，但 |效应|/MDE 只有 1.66 倍。

- 窗口：segment_2016_2026（as-of 全 A，n=237）
- 判定估计量：iid
- d 定义：0.5*long-0.5*short
- 限定：头条 +4.39 是 iid t，同序列 NW3 为 +4.64，引用时须同列两数
- 限定：这是横截面月度差距，不是可交易组合的账户收益
- 限定：d 与文献 spread 差 2 倍
- 可引用强度：QUOTABLE_MARGINAL
- 分级依据：L3u 判核对未通过（仅 t 口径不同：判定用 iid、L3t 用 NW3）；L3x 复核：stat.t 与 t_iid 一致、mean 与 n 全等，ratio 1.658，NW 滞后网格全档 QUOTABLE_MARGINAL
- 分级来源：`l3w_unseal`（解封复核，非 L3u 正式分级；L3u 记为 NOT_GRADED）
- 来源：L3u 正式分级 NOT_GRADED｜序列 `gate6_out/b8c4f1ecba44/monthly_paired.csv::d`（n=237）

### 3. `phasec_d`｜QUOTABLE

**`phasec_d`**（A_cross_spread，单位 pp）
> 池效应的判定量 spread(B)−spread(A) 为 +1.94pp（t=+6.37）、MDE 0.85pp，|效应|/MDE 2.27 倍；格 A 自身的水平是 +0.01pp，与本条的差值不可混用。

- 窗口：single_window（阶段 C 五格设计，n=237）
- 判定估计量：nw3
- d 定义：long_minus_short
- 限定：该值是两格 spread 之差，不是任何单一组合的收益水平
- 限定：spread 与 d 差 2 倍，跨文献引用须换算
- 可引用强度：QUOTABLE
- 分级依据：L3u 判核对未通过（L3t 取了格 A 自身水平 +0.0141pp）；L3w 复核证明判定量与序列同源，|效应|/MDE=2.273，NW 滞后 0/1/2/3/4/6 全档恒为 QUOTABLE
- 分级来源：`l3w_unseal`（解封复核，非 L3u 正式分级；L3u 记为 NOT_GRADED）
- 来源：L3u 正式分级 NOT_GRADED｜序列 `phasec_out/3a01759a37f0/five_cells.csv::+1×d[B] + -1×d[A]`（n=237）

### 4. `phasec2_d`｜QUOTABLE_MARGINAL

**`phasec2_d`**（A_cross_spread，单位 pp）
> 停牌效应的增量 +0.11pp（t=+5.01）、MDE 0.06pp，|效应|/MDE 1.79 倍；池效应本身的水平是 +0.66pp，不是本条的增量。

- 窗口：single_window（阶段 C-2 五格设计，n=237）
- 判定估计量：nw3
- d 定义：long_minus_short
- 限定：检验的是相对基准格的增量，不是停牌效应的绝对规模
- 限定：统计显著但量级小：0.11pp/月 对一个组合的实际影响需另算
- 可引用强度：QUOTABLE_MARGINAL
- 分级依据：L3u 判核对未通过（L3t 记成 POOL_EFFECT_CONFIRMED，取的是池效应水平）；L3w 复核证明判定量与序列同源，|效应|/MDE=1.790，NW 滞后 0/1/2/3/4/6 全档恒为 QUOTABLE_MARGINAL
- 分级来源：`l3w_unseal`（解封复核，非 L3u 正式分级；L3u 记为 NOT_GRADED）
- 来源：L3u 正式分级 NOT_GRADED｜序列 `phasec2_out/953c32fdc556/cells.csv::+1×spread_v1 + -1×spread`（n=237）

### 5. `phasef_delta`｜QUOTABLE_MARGINAL

**`phasef_delta`**（A_cross_spread，单位 pp）
> MAX5 的 delta 为 +0.74pp（t=+3.41），MDE 0.61pp，|效应|/MDE 1.22 倍，是本项目最接近可引用的横截面结论。

- 窗口：full_sample（全样本月度，n=237）
- 判定估计量：nw3
- d 定义：0.5*long-0.5*short
- 限定：1.22 倍仍低于 2 倍门槛，只能作为「可引用且须标 MDE」处理
- 限定：delta 是两格 d 之差，不是组合收益
- 可引用强度：QUOTABLE_MARGINAL
- 分级依据：L3u 核对一致，|效应|/MDE=1.22 落 1~2 倍档
- 来源：L3u 正式分级 QUOTABLE_MARGINAL｜序列 `phasef_out/aec46d88bbda/monthly.csv::delta`（n=237）

### 6. `phasel_alpha`｜NULL_CONFIRMED

**`phasel_alpha`**（B_long_only，单位 pp）
> SURGE 避让后的 alpha 只剩 +0.11pp（t=+3.13），MDE 0.09pp、事后功效 88%：判「净效应被吸收」，即确认无增量。

- 窗口：full_sample（全样本月度，n=237）
- 判定估计量：nw3
- 成本口径：O_BASE_3000w
- 限定：「确认无效应」限定于相对同池平均的增量，不是绝对收益为零
- 限定：该序列与 phaser 完全同一份数字，不能当作两次独立验证
- 可引用强度：NULL_CONFIRMED
- 分级依据：L3u 核对一致，未检出且事后功效 87.9%，可引为「确认无效应」
- 来源：L3u 正式分级 NULL_CONFIRMED｜序列 `phasel_out/9ffc3710cf6f/monthly.csv::alpha_v1_pp`（n=237）

### 7. `phaseo_netBASE`｜QUOTABLE_MARGINAL

**`phaseo_netBASE`**（B_long_only，单位 pp）
> 3000 万档、BASE 费率（单边 0.051%）下净 alpha 为 +0.14pp（t=+4.04），MDE 0.09pp，|效应|/MDE 1.44 倍。

- 窗口：full_sample（全样本月度，n=237）
- 判定估计量：nw3
- 成本口径：O_BASE_3000w
- 限定：1.44 倍仍低于 2 倍门槛，引用必须同时给 MDE
- 限定：EXECUTABLE_UNDER_BASE_FEES 不等于「可用」：换到压力费率档后净 alpha 逼近 0
- 限定：该序列与 phasev 完全同一份数字，不能当作两次独立验证
- 可引用强度：QUOTABLE_MARGINAL
- 分级依据：L3u 核对一致，|效应|/MDE=1.44 落 1~2 倍档
- 来源：L3u 正式分级 QUOTABLE_MARGINAL｜序列 `phaseo_out/ac7aa2830ab0/monthly.csv::net_BASE_3000w`（n=237）

### 8. `phases_net`｜DIRECTION_ONLY

**`phases_net`**（B_long_only，单位 pp）
> 现代段净 alpha 为 +0.11pp（t=+1.58 iid／+2.22 NW3），MDE 0.14pp、事后功效 60%：只能引方向。

- 窗口：modern_era（2023-01 起的现代段，n=129）
- 判定估计量：nw3
- 成本口径：S_modern_era
- 限定：iid 与 NW3 两个 t 分列 1.58 与 2.22，只引其一等于挑显著性，必须同列
- 限定：bootstrap 95% 区间跨 0，L 剖面只在 L≥2 通过，属边缘证据
- 限定：该序列与 phaset 完全同一份数字，不能当作两次独立验证
- 可引用强度：DIRECTION_ONLY
- 分级依据：L3u 核对一致，|效应|/MDE=0.79 落 0.5~1 倍档
- 来源：L3u 正式分级 DIRECTION_ONLY｜序列 `phases_out/f098a59fb1b5/monthly.csv::net_S_3000w`（n=129）

### 9. `phaset_net`｜DIRECTION_ONLY

**`phaset_net`**（B_long_only，单位 pp）
> 同一序列的稳健性检查判 T_FRAGILE：剔除极端月后显著性消失，现代段的 +0.11pp（MDE 0.14pp）不承重。

- 窗口：modern_era（现代段子样本，n=129）
- 判定估计量：nw3
- 成本口径：S_modern_era
- 限定：alpha 高度集中在 ADV 最低的一组（阶段 T 的 T3 分解），分层后结论改变
- 限定：本条是对 phases 的脆弱性判定，不能与 phases 并列当作两个正面结果
- 可引用强度：DIRECTION_ONLY
- 分级依据：L3u 核对一致，按 |效应|/MDE 落 0.5~1 倍档；稳健性判定本身另见阶段 T
- 参考（不得用于对外）：DIRECTION_ONLY｜
- 来源：L3u 正式分级 DIRECTION_ONLY｜序列 `phaset_out/ef29a743997b/monthly.csv::net_S_3000w`（n=129）

## 三、27 条逐条

| 条目 | 一句话（须照抄，不得改写数字） | 强度 | 核对 |
|---|---|---|---|
| `gate1_d` | Gate 1 窗口的 d 均值为 −0.40pp（t=−0.57 iid／−0.61 NW3），MDE 1.82pp 是效应本身的 4.6 倍，该窗口对 1pp 量级没有分辨力。 | UNDERPOWERED | MISMATCH_MEAN（仅 t 不同） |
| `gate2_d` | Gate 2 的 d 均值为 −0.38pp（t=−1.64 iid／−1.59 NW3），MDE 0.68pp、事后功效 36%：未检出，但不足以判「无效应」。 | UNDERPOWERED | MISMATCH_MEAN（仅 t 不同） |
| `gate3_d` | Gate 3 判定窗口（n=108）的 d 均值为 +0.48pp（t=+2.28）、MDE 0.59pp，\|效应\|/MDE 0.81 倍；另有另一次重跑给出 −0.40pp、t=−1.69、n=125，两者窗口不同，不可混引。 | DIRECTION_ONLY | MISMATCH_N |
| `gate4_d` | Gate 4 的 d 均值为 +0.17pp（t=+0.87 iid／+0.97 NW3），MDE 0.48pp、事后功效 16%，无法判断。 | UNDERPOWERED | MISMATCH_MEAN（仅 t 不同） |
| `gate5_d` | Gate 5 的 d 均值为 +0.02pp（t=+0.16 iid／+0.15 NW3），MDE 0.43pp、事后功效 5%：这里的「没有证据」等于「没有分辨力」。 | UNDERPOWERED | MISMATCH_MEAN（仅 t 不同） |
| `gate6_d` | 全 A as-of 口径的 d 均值为 +0.71pp（t=+4.39 iid／+4.64 NW3），MDE 0.43pp、事后功效 100%，但 \|效应\|/MDE 只有 1.66 倍。 | QUOTABLE_MARGINAL | MISMATCH_MEAN（仅 t 不同） |
| `phasec_d` | 池效应的判定量 spread(B)−spread(A) 为 +1.94pp（t=+6.37）、MDE 0.85pp，\|效应\|/MDE 2.27 倍；格 A 自身的水平是 +0.01pp，与本条的差值不可混用。 | QUOTABLE | MISMATCH_MEAN |
| `phasec2_d` | 停牌效应的增量 +0.11pp（t=+5.01）、MDE 0.06pp，\|效应\|/MDE 1.79 倍；池效应本身的水平是 +0.66pp，不是本条的增量。 | QUOTABLE_MARGINAL | MISMATCH_MEAN |
| `phasec3_d` | 滞后成本的增量 +0.05pp（t=+0.60）、MDE 0.22pp、事后功效 9%，无法判断（登记表同样写成 +0.66pp）。 | UNDERPOWERED | MISMATCH_MEAN |
| `phasee_alpha` | 非流动性的单边 alpha 为 +0.28pp（t=+1.90），MDE 0.41pp、事后功效 48%：未检出且功效不足。 | UNDERPOWERED | MATCH |
| `phasef_delta` | MAX5 的 delta 为 +0.74pp（t=+3.41），MDE 0.61pp，\|效应\|/MDE 1.22 倍，是本项目最接近可引用的横截面结论。 | QUOTABLE_MARGINAL | MATCH |
| `phaseg_alpha` | MAX5 的单边 alpha 为 +0.18pp（t=+1.76），MDE 0.29pp、事后功效 42%：不可执行，且未检出。 | UNDERPOWERED | MATCH |
| `phasel_alpha` | SURGE 避让后的 alpha 只剩 +0.11pp（t=+3.13），MDE 0.09pp、事后功效 88%：判「净效应被吸收」，即确认无增量。 | NULL_CONFIRMED | MATCH |
| `phasem_spread` | 空头腿 spread 为 +0.72pp（t=+2.85）、MDE 0.71pp；判定由区间规则给出，序列均值不是判定量。 | NOT_GRADED | NOT_APPLICABLE |
| `phasej_netA` | 执行成本后的净 alpha 只剩 +0.03pp（t=+1.04），MDE 0.09pp、事后功效 18%：毛效应被成本吸收。 | NOT_GRADED | MISMATCH_MEAN |
| `phasek_m1net` | 降换手方案 m1 的净收益为 +0.04pp（t=+1.19），MDE 0.09pp、事后功效 22%：省下的佣金约等于 alpha 衰减。 | NOT_GRADED | MISMATCH_MEAN（仅 t 不同） |
| `phaseo_netBASE` | 3000 万档、BASE 费率（单边 0.051%）下净 alpha 为 +0.14pp（t=+4.04），MDE 0.09pp，\|效应\|/MDE 1.44 倍。 | QUOTABLE_MARGINAL | MATCH |
| `phasep_late` | 晚段净 alpha 的回归系数为 +0.13pp（t=+2.58，n=129），MDE 0.14pp；全样本回归分母是 237，效应量本身一致。 | NOT_GRADED | MISMATCH_N |
| `phaser_alpha` | SURGE 的 alpha 为 +0.11pp（t=+3.13）、MDE 0.09pp，随资金规模衰减到不显著；判定由多门槛规则给出，非单一均值。 | NOT_GRADED | NOT_APPLICABLE |
| `phases_net` | 现代段净 alpha 为 +0.11pp（t=+1.58 iid／+2.22 NW3），MDE 0.14pp、事后功效 60%：只能引方向。 | DIRECTION_ONLY | MATCH |
| `phaset_net` | 同一序列的稳健性检查判 T_FRAGILE：剔除极端月后显著性消失，现代段的 +0.11pp（MDE 0.14pp）不承重。 | DIRECTION_ONLY | MATCH |
| `phasev_net` | 同一份净 alpha 为 +0.14pp（MDE 0.09pp），但换到压力费率后大幅衰减：结论对成本口径敏感，不能脱离口径单独引用。 | NOT_GRADED | NOT_APPLICABLE |
| `phaseq_max5` | 控制波动率后 MAX5 的增量系数为 +0.04pp（t=+0.25），MDE 0.47pp、事后功效 6%：增量被吸收。 | UNDERPOWERED | MATCH |
| `l3r_delta` | 选择效应的月均为 −0.33pp（t=−0.96），MDE 0.95pp、事后功效 16%：测不出来，不等于没有。 | UNDERPOWERED | NOT_APPLICABLE |
| `l3s_ic` | 截面 IC 均值为 −0.023（t=−1.71），MDE_IC 0.038、事后功效 40%：IC 路线同样无法判断。 | UNDERPOWERED | NOT_APPLICABLE |
| `l3_test` | 折均收益为 +4.00%/折（t=+1.74 iid／+1.42 NW3），MDE 7.89%/折、事后功效 29%。 | UNDERPOWERED | NOT_APPLICABLE |
| `l3p_test` | 折均收益为 +5.28%/折（t=+2.00 iid／+1.92 NW3），MDE 7.72%/折、事后功效 48%。 | UNDERPOWERED | NOT_APPLICABLE |

## 四、违规明细

无。每个数字四舍五入后与重算字段字面相等、符号一致；分级未上调；限定齐全。

## 五、数值指纹：6 个 key 共用 3 份数字

同一份月度序列被复制到不同阶段目录，判据不同但数字逐位相同。**这不是两次独立验证。**

| 组 | 条目 | 共用序列 |
|---|---|---|
| 2 条 | `phasel_alpha`、`phaser_alpha` | `phasel_out/9ffc3710cf6f/monthly.csv::alpha_v1_pp` |
| 2 条 | `phaseo_netBASE`、`phasev_net` | `phaseo_out/ac7aa2830ab0/monthly.csv::net_BASE_3000w` |
| 2 条 | `phases_net`、`phaset_net` | `phases_out/f098a59fb1b5/monthly.csv::net_S_3000w` |

去重后独立数字 = 27 − 6 + 3 = **24**。

## 六、抽样复算：链条没断

从原始序列重算 3 条（头条 / 可引用 / 不同单位族各一），与 L3u 存的 `recomputed` 对比：

| 条目 | n | 重算 mean | 存档 mean | 最大绝对差 | 结论 |
|---|---|---|---|---|---|
| `gate6_d` | 237 | 0.708446 | 0.708446 | 0.00e+00 | 一致 |
| `phaseo_netBASE` | 237 | 0.135959 | 0.135959 | 0.00e+00 | 一致 |
| `l3s_ic` | 198 | -0.023248 | -0.023248 | 0.00e+00 | 一致 |

## 七、首跑对照：仪器错与数据错必须分开

首跑检出 **33 处**：ESTIMATOR_MISMATCH 2、MISSING_CORE_FIELD 6、SIGN_MISMATCH 12、UNREGISTERED_NUMBER 13。其中 UNREGISTERED_NUMBER 一项就把 P2 的阈值（1~8）顶穿了。

| 代码 | 处数 | 根因 | 修在哪一侧 |
|---|---|---|---|
| UNREGISTERED_NUMBER | 13 | 首版数字 token 抽取没有排除紧跟字母的写法，`NW3`/`MAX5`/`m1` 里的数字被当成统计量 | 检查器（已修，预登记留 amendment） |
| SIGN_MISMATCH | 12 | display 只写数值、负号只出现在正文，校验拿不到对照 | 数据侧：负值字面补负号；校验改成 display 与正文两处互校 |
| MISSING_CORE_FIELD | 6 | 正文没有报 MDE，6 条结论缺分辨率 | 数据侧：补 MDE |
| ESTIMATOR_MISMATCH | 2 | 判定用 iid，正文只引了 NW3 的 t | 数据侧：同列 iid 与 NW3 |

**13 处是仪器错，20 处是被测对象的真错。**混在一起看会得出「卡片全是错的」这种错误印象；分开看才知道：检查器把口径标签当数字，而卡片本身漏了负号、漏了 MDE、漏了另一侧 t。仪器修完、数据补完再跑，违规归零。

但 P2 的判定**不改**：预登记写的是「1~8 处」，首跑实测 13 处，形式上就是被反驳；被反驳的预测原样保留，不因为事后发现是仪器问题就翻案。

首跑产物保留在 `reports/gates/H-L3V-CLAIM-REGISTRY/l3v_claims_firstrun_tokenbug.json`（R5：不删失败）。

## 八、预测对照（先于测量写定）

| # | 预测 | 实测 | 结论 |
|---|---|---|---|
| P1 | 覆盖双向差集为空：missing=0 且 orphan=0（27/27）。 | {"missing": 0, "orphan": 0} | 成立 |
| P2 | 数字类违规（VALUE_DRIFT + UNREGISTERED_NUMBER + UNUSED_NUMBER）合计在 1 到 8 之间。 | {"firstrun": 13, "final": 0} | **被反驳** |
| P3 | QUOTABLE 条目为 0，QUOTABLE_MARGINAL 为 2（phasef_delta、phaseo_netBASE）。 | {"QUOTABLE": 1, "QUOTABLE_MARGINAL": 4} | 已被 `H-L3W-UNSEAL-000001` 取代 |
| P4 | ESTIMATOR_MISMATCH = 0。 | {"ESTIMATOR_MISMATCH": 0} | 成立 |
| P5 | 数值指纹重复恰好 3 组、覆盖 6 个 key，27 条只对应 24 份独立数字。 | {"groups": 3, "keys": 6, "independent": 24} | 成立 |
| P6 | 撤回台账 7 条，且 BANNED_PHRASING = 0。 | {"retractions": 7, "BANNED_PHRASING": 0} | 成立 |

被反驳：P2。被反驳的预测原样保留，不改判据（R5）。

P2 的两个观测值都不落在 1~8：首跑 13 处（仪器把口径标签当数字）、终版 0 处（修完）。无论取哪个，这条预测都不成立——区别只在于「为什么不成立」，而这恰好是 §七 要分开的那件事。

## 九、限制

- 本实验不重跑任何策略，只读 L3u 产物与卡片表；L3u 产物 sha256 在启动时校验。
- 卡片 headline 是人工语义层，无法自动生成；能自动校验的是它里面的数字、分级与必带限定。
- literals 白名单是本闸门的已知缺口：把统计量谎报成 literal 可以绕过数字校验。因此产物会逐条列出 literals 及其 reason 供人工审，并限制每条不超过 4 个。
- phasej_netA 的序列取自 1000 万档而判定档是 3000 万，卡片沿用该序列并显式声明，不另算 3000 万档（那属于新实验）。
- 每条 literals 上限 4 个，超出即判违规（`LITERALS_OVERFLOW`）。
- 卡片只覆盖 L3u 收录的 27 条；未进入 L3t 登记表的结论不在本表内，也不得引用。
- **闸门只校验 headline 里的数字**；`limits_of_use` 与 `window_note` 里出现的数字是说明性背景（多来自其他阶段），不参与回指校验。若要把它们搬进正文，必须先登记。

