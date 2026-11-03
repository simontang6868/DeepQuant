# L3ab 检验家族审计

> 本报告由 `scripts/l3ab_family_audit.py` 生成。本阶段**不新增因子、不新增回测、不启动实盘**，不改动任何历史产物，也不为增加或减少通过条数而调整家族边界。

## 1. 审计范围

回答三个问题：

1. 现在被同时报告并一起做多重检验的 27 条结论，是不是一个**有依据的**检验家族？
2. m=24 这个数字，是「独立检验数」还是「独立数字数」？两者该不该一样？
3. 这个家族**覆盖全了吗**——有没有已产生结论却没被算进来的实验？

明确不在范围内：新因子、新回测、实盘、以及「按经济意义重组结论」。

## 2. 开工时状态

| 项目 | 值 |
| --- | --- |
| 分支 | `workbuddy/2026-10-20-consolidated` |
| HEAD | `5cfed94 阶段 U 结题：NW 滞后稳健性全项目审计 —— NW_LAG_SENSITIVITY_CONFIRMED` |
| 工作区改动条目（含未跟踪） | 214——本项目阶段产物目录多为未跟踪，并非全部由本次引入 |
| 测试基线 | 后端 3015 通过 / 1 跳过；性质检查 9/9；脚本侧 332 通过 / 1 跳过；交易层验收 PASS |

⚠️ 上表的「工作区改动条目」是**运行时刻快照**（重跑会变）；其余字段是定值。测试基线也是开工时的值，本阶段结束后后端为 3047（新增 32 项）。

冻结输入（sha256 前 12 位）：

| 输入 | sha256 |
| --- | --- |
| `l3u_prereg` | `0bbdc5ff4ad8…` |
| `l3u_result` | `f7d1a66d64c3…` |
| `cards` | `5b4173c7265e…` |
| `l3z_result` | `a138f8803ea4…` |
| `l3aa_result` | `dffcf4645cfe…` |

判定记录 `docs/gates/l3ab_family_decisions.json` 单独校验（`afdf3fded8de…`）：它承载本审计的**判断**，与数字分开，改一个分类就会改 sha，脚本随即拒跑。

## 3. 家族划分的方法

审计提纲给了四类关系。实际排查发现它不够用，补了第五类：

| 类 | 含义 | 本项目命中 |
| --- | --- | --- |
| A | 同一检验被重复登记（同数据 + 同假设） | **0 条** |
| B | 数据重叠或同源，但对应不同研究假设 | 6 条 |
| C | 一条序列是另一条的子窗口 | 1 条（`phasep_late`） |
| D | 不同数据或不同统计量产生的独立问题 | 17 条 |
| **E（本次补）** | **同一假设在不同窗口/样本上重复检验** | **3 条**（`gate2_d`/`gate3_d`/`gate6_d`） |

E 类不在提纲里，但它恰恰是最常被忽略的多重性来源：同一个问题换三个窗口问三次，在族系错误率下占三个名额，语义却是「一问多答」。把它归进 D 会说「这是三个独立问题」（不准确），归进 A 会说「重复登记」（也不准确，因为数据确实不同）。所以单列。

**所有分类都基于登记判定字符串 + 卡片 headline 的反推**，不是原始设计文本——各阶段预登记没有假设字段。这一点记在 UNKNOWN 里，见第 8 节。

## 4. 检验清单

### 4.1 假设与统计量

| claim_id | 研究假设（反推） | 判定量 / 登记字段 | 序列窗口 | n | p | 引用状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `gate1_d` | Gate 1 窗口内，多空价差 d 是否为正 | 序列均值 / main.t_stat | 2021-01~2024-12 | 48 | 0.545309 | UNDERPOWERED |
| `gate2_d` | 反转信号在 2016-2026 窗口内 d 是否为正 | 序列均值 / main.t_stat | 2016-06~2026-10 | 125 | 0.113348 | UNDERPOWERED |
| `gate3_d` | 反转信号在 2007-2015 判定窗口内 d 是否为正 | 序列均值 / main.t_stat | 2007-02~2016-01 | 108 | 0.024529 | EXPLORATORY |
| `gate4_d` | 低波动分组 d 是否为正 | 序列均值 / main.t_stat | 2007-02~2026-10 | 237 | 0.333570 | UNDERPOWERED |
| `gate5_d` | 活跃度分组 d 是否为正 | 序列均值 / main.t_stat | 2006-02~2026-10 | 249 | 0.876952 | UNDERPOWERED |
| `gate6_d` | 反转信号在 as-of 全A 池、全样本上 d 是否为正 | 序列均值 / main.t_stat | 2007-02~2026-10 | 237 | 0.000006 | QUOTABLE_MARGINAL |
| `phasec_d` | 池效应：spread(B) 与 spread(A) 之差是否为零 | 序列均值 / main_test.t_nw | 2007-01~2026-09 | 237 | 0.000000 | QUOTABLE |
| `phasec2_d` | 加入停牌约束后 spread 的增量是否为零 | 序列均值 / main_test.t_nw | 2007-02~2026-10 | 237 | 0.000001 | QUOTABLE_MARGINAL |
| `phasec3_d` | 剔除涨停后 spread 的增量是否为零 | 序列均值 / main_test.t_nw | 2007-02~2026-10 | 237 | 0.550840 | UNDERPOWERED |
| `phasee_alpha` | 单边多头组合相对基准的 alpha 是否为零 | 序列均值 / main.t_nw | 2007-02~2026-10 | 237 | 0.058478 | UNDERPOWERED |
| `phasef_delta` | MAX5 五分层中最高与最低层的 delta 是否为零 | 序列均值 / main.t_nw | 2007-02~2026-10 | 237 | 0.000768 | QUOTABLE_MARGINAL |
| `phaseg_alpha` | MAX5 约束后的单边 alpha 是否可执行 | 序列均值 / main.t_nw | 2007-02~2026-10 | 237 | 0.079017 | UNDERPOWERED |
| `phasel_alpha` | SURGE 避免型效应是否留下未被吸收的 alpha | 序列均值 / V1_executable.t_nw | 2007-01~2026-09 | 237 | 0.001972 | NULL_CONFIRMED |
| `phasem_spread` | SURGE 的空头侧是否成立 | 判定非单一均值（stat.applicable=false） | 全样本月度，n=237 | 237 | — | NOT_GRADED |
| `phasej_netA` | 计入执行冲击后净收益是否为正 | 序列均值 / attribution_best_tier.t_net | 2007-01~2026-09 | 237 | 0.298312 | NOT_GRADED |
| `phasek_m1net` | 降到 1 个月持仓后净收益是否仍为正 | 序列均值 / by_period.1.t_nw6 | 2007-01~2026-09 | 237 | 0.233460 | NOT_GRADED |
| `phaseo_netBASE` | BASE 费率、3000 万资金下净 alpha 是否为正 | 序列均值 / results.W_3000万|BASE.t_nw | 2007-01~2026-09 | 237 | 0.000073 | QUOTABLE_MARGINAL |
| `phasep_late` | 后段（2016-2026）与整体是否存在净收益差异 | 序列均值 / main_test.t_nw | 2016-01~2026-09 | 129 | 0.010970 | NOT_GRADED |
| `phaser_alpha` | SURGE alpha 是否随规模变化 | 判定非单一均值（stat.applicable=false） | 2007-01~2026-09 | 237 | — | NOT_GRADED |
| `phases_net` | 现代段（2016-2026）净 alpha 是否为正 | 序列均值 / primary.t_nw | 2016-01~2026-09 | 129 | 0.028317 | EXPLORATORY |
| `phaset_net` | 现代段结论在剔除极端月后是否仍成立 | 序列均值 / baseline_modern.t_nw | 2016-01~2026-09 | 129 | 0.028317 | EXPLORATORY |
| `phasev_net` | 净收益是否对成本口径敏感 | 判定非单一均值（stat.applicable=false） | 2007-01~2026-09 | 237 | — | NOT_GRADED |
| `phaseq_max5` | 控制 REV21 后 MAX5 的增量系数是否为零 | 序列均值 / main_test_REV21.coefficients.MAX5.t_nw | 2007-01~2026-09 | 237 | 0.799747 | UNDERPOWERED |
| `l3r_delta` | 选股环节（12 只 vs 24 只）是否带来正贡献 | 判定非单一均值（stat.applicable=false） | 197 期配对剔除，中位 +0.060pp、胜率 51.78% | 197 | — | UNDERPOWERED |
| `l3s_ic` | 518 只池上的截面秩相关 IC 是否为零 | 判定非单一均值（stat.applicable=false） | 198 期、378 只有效标的 | 198 | — | UNDERPOWERED |
| `l3_test` | walk-forward 折级测试收益是否为正 | 判定非单一均值（stat.applicable=false） | 27 个滚动折 | 27 | — | UNDERPOWERED |
| `l3p_test` | 另一批 walk-forward 折级测试收益是否为正 | 判定非单一均值（stat.applicable=false） | 28 个滚动折 | 28 | — | UNDERPOWERED |

### 4.2 家族判定

| claim_id | 类 | 假设簇 | 占名额 | 出 p | 纳入理由 |
| --- | --- | --- | --- | --- | --- |
| `gate1_d` | D | `H_gate1_pool` | 是 | 是 | 独立信号（Gate 1 自建池），窗口 2021-2024 与其余不重叠 |
| `gate2_d` | E | `H_reversal` | 是 | 是 | 与 gate3/gate6 同一经济假设、不同窗口；按「都占名额」处理 |
| `gate3_d` | E | `H_reversal` | 是 | 是 | 同上；注意本条判定窗口与 L3t 引用的 2016-2026 重跑不同（L3u 已纠） |
| `gate4_d` | D | `H_lowvol` | 是 | 是 | 独立信号 |
| `gate5_d` | D | `H_activity` | 是 | 是 | 独立信号 |
| `gate6_d` | E | `H_reversal` | 是 | 是 | 同一假设、不同池构造（as-of 全A）与全样本窗口 |
| `phasec_d` | D | `H_pool_effect` | 是 | 是 | 五格设计的主检验 |
| `phasec2_d` | D | `H_pool_increment` | 是 | 是 | 对 phasec 的增量检验，统计量是两列之差（spread_v1 − spread），不是池效应本身 |
| `phasec3_d` | D | `H_lag_cost` | 是 | 是 | 执行滞后/可成交性的增量检验，与 phasec2 的统计量不同（spread_v1 − spread_v0） |
| `phasee_alpha` | D | `H_single_side` | 是 | 是 | 首个 long-only 单边检验，与价差类统计量不同 |
| `phasef_delta` | D | `H_max5_layer` | 是 | 是 | 分层设计，统计量是 delta 不是价差 |
| `phaseg_alpha` | D | `H_max5_single` | 是 | 是 | 与 phasef 同族但统计量与问题不同（单边 alpha 而非分层 delta） |
| `phasel_alpha` | B | `H_surge_avoid` | 是 | 是 | 与 phaser_alpha 共用同一份序列（237 月逐位相同），但登记判定不同：本条问「是否被吸收」，phaser 问「是否规模依赖」——数据同源、假设不同 |
| `phasem_spread` | D | `H_surge_short` | 是 | 否 | 判定不由单一均值给出（stat.applicable=false），占名额不出 p |
| `phasej_netA` | D | `H_net_impact` | 是 | 是 | 冲击模型 A 下的净收益，与 phaseo 的成本口径不同 |
| `phasek_m1net` | D | `H_turnover` | 是 | 是 | 换手维度，统计量 m1_net |
| `phaseo_netBASE` | B | `H_net_base` | 是 | 是 | 与 phasev_net 共用同一份序列（237 月逐位相同）；本条问净 alpha，phasev 问成本口径敏感性。同时是 phasep_late 的超集（见 phasep） |
| `phasep_late` | C | `H_segment` | 是 | 是 | 数据是 phaseo_netBASE 的严格子窗口（129 月逐位相同），但登记判定是段差异（回归系数），不是「alpha 是否为正」。⚠️ 该判定统计量与序列不同源（L3w 已判），L3z 用序列均值给它算出 p=0.010970，属**代理 p**，不是它假说的正确检验 |
| `phaser_alpha` | B | `H_surge_scale` | 是 | 否 | 与 phasel_alpha 同源；判定不由单一均值给出，占名额不出 p |
| `phases_net` | B | `H_modern_era` | 是 | 是 | 与 phaset_net 共用同一份序列（129 月逐位相同） |
| `phaset_net` | B | `H_modern_robust` | 是 | 是 | 与 phases_net 同源，但假设是稳健性；⚠️ T_FRAGILE 的判定来自剔极端月计算，不可能由该序列均值给出，因此它的 p 与 phases 完全相等（0.028317）属**同一个 p 被算了两次** |
| `phasev_net` | B | `H_cost_convention` | 是 | 否 | 与 phaseo_netBASE 同源；判定不由单一均值给出，占名额不出 p |
| `phaseq_max5` | D | `H_max5_increment` | 是 | 是 | 月度截面回归系数，与分层/单边检验统计量不同 |
| `l3r_delta` | D | `H_selection` | 是 | 否 | 折级序列，判 INCONCLUSIVE_POWER；占名额不出 p |
| `l3s_ic` | D | `H_ic` | 是 | 否 | IC 量纲与收益类不同；占名额不出 p |
| `l3_test` | D | `H_fold` | 是 | 否 | 折级（n=27）而非月度；占名额不出 p |
| `l3p_test` | D | `H_fold_p` | 是 | 否 | 与 l3_test 不同的折批次；占名额不出 p |

合计 27 条登记；其中 **7 条**判定不由单一均值给出，占名额但不出 p；**20 条**参与评分。

## 5. 同源与子窗口：数据层面的事实

逐条比较「共同月份上的数值」，不看均值、不看 t、不看相关系数——用均值和 t 去判同源，本项目出过错（曾把不同源的两条判成同源）。

| a | b | 共同月 | n_a | n_b | 最大逐月差 | 关系 |
| --- | --- | --- | --- | --- | --- | --- |
| `phasel_alpha` | `phaser_alpha` | 237 | 237 | 237 | 0 | 等长同值 |
| `phaseo_netBASE` | `phasep_late` | 129 | 237 | 129 | 0 | 子窗口 |
| `phaseo_netBASE` | `phasev_net` | 237 | 237 | 237 | 0 | 等长同值 |
| `phasep_late` | `phasev_net` | 129 | 129 | 237 | 0 | 子窗口 |
| `phases_net` | `phaset_net` | 129 | 129 | 129 | 0 | 等长同值 |

共 5 对逐位相同，涉及 **7 条结论**，但它们只落在 **3 份序列**上（三组，2+3+2）。

### 5.0 一个容易算错的地方：5 对不等于 5 次合并

5 对配对里有两组是「三条互相同值」——{`phaseo_netBASE`, `phasep_late`, `phasev_net`} 每两条都构成一对。所以按数据同一性完全合并是 **27 − 7 + 3 = 23**，不是 24。

那 24 是怎么来的？L3z 的数值指纹是 `(mean, t, n)`：`phasep_late` 的 n=129 与 `phaseo_netBASE` 的 237 不同，指纹不同，于是被单独算。**所以 24 不是「数据去重数」，而是「指纹去重数」**——这两个数在别的数据集上会分叉，引用时必须说清是哪一个。

⚠️ 本条来自测试的反驳：本审计初稿写的是「27 − 3 对 = 24」，算式与结论都对不上。是同名测试（`test_duplicate_data_is_not_double_counted_as_cross_validation`）把它逼出来的，记录在此。

### 5.1 但数据相同不等于假设相同

对上面每一对，再比一次**登记判定字符串**：

| 配对 | 判定（a） | 判定（b） | 同假设？ |
| --- | --- | --- | --- |
| `phasel_alpha` / `phaser_alpha` | SURGE_AVOID_ABSORBED | SURGE_SCALE_DEPENDENT | 否 |
| `phaseo_netBASE` / `phasep_late` | EXECUTABLE_UNDER_BASE_FEES | SEGMENT_DIFFERENCE_NOT_DETECTED | 否 |
| `phaseo_netBASE` / `phasev_net` | EXECUTABLE_UNDER_BASE_FEES | COST_CONVENTION_SENSITIVE | 否 |
| `phasep_late` / `phasev_net` | SEGMENT_DIFFERENCE_NOT_DETECTED | COST_CONVENTION_SENSITIVE | 否 |
| `phases_net` / `phaset_net` | MODERN_ERA_ALPHA_DETECTED | T_FRAGILE | 否 |

**没有一对判定相同。** 所以按「同数据 + 同假设」的严格定义，A 类（重复登记）是 **0 条**。

## 6. m=24 的审查

### 6.1 这个数字实际是什么

m=24 是**独立数字数**（按 `(mean, t, n)` 数值指纹去重），不是独立假设数。两者在有同源配对时必然不等。问题不是「哪个数字对」，而是「FDR/FWER 该按哪个计数」。

按族系错误率的定义，该计的是**假设**数：同一个假设换数据重测，仍然是一个假设；不同假设共用同一份数据，则仍是两个假设（其相关性由 BY 之类的任意依赖方法吸收）。

### 6.2 四种口径的比较

成员表完全相同（27 条，20 条出 p），只改 m。这样差异只能来自门槛，不会混入「换了一批检验」。

| 口径 | m | 补位 | Bonferroni 门槛 | BY 第 1 档门槛 | Bonf | Holm | BH | BY | 采用 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| m=24（数值指纹去重，L3z 预登记主口径） | 24 | 4 | 0.00208333 | 0.00055174 | 6 | 6 | 7 | 6 | **是** |
| m=27（全部登记条目） | 27 | 7 | 0.00185185 | 0.00047588 | 5 | 6 | 7 | 6 | 否 |
| m=23（按数据同一性完全去重） | 23 | 3 | 0.00217391 | 0.00058215 | 6 | 6 | 7 | 6 | 否 |
| 按研究问题分族（m = 假设簇数） | 25 | 5 | 0.00200000 | 0.00052411 | 6 | 6 | 7 | 6 | 否 |

- **m=24（数值指纹去重，L3z 预登记主口径）**（primary）：「独立数字」不等于「独立假设」。若把同源而假设不同的配对视为同一假设，则该口径偏大；若视为不同假设，则该口径偏小（应取 27）。记录不足，方向无法确定。
- **m=27（全部登记条目）**（sensitivity）：把 3 对同源数据算成 6 个假设；其中 phaser/phasev 尚不产生 p（只占名额），phases/phaset 的 p 完全相同——等于同一个 p 占两个名额。
- **m=23（按数据同一性完全去重）**（sensitivity）：它假设「同数据 = 同假设」，与 A 类为 0 的证据直接冲突——那 7 条的登记判定互不相同。列出来的用途只有一个：证明**即使按最激进的合并口径，结论仍是 6 条**，所以 24 与 23 之争不改变对外表述。
- **按研究问题分族（m = 假设簇数）**（sensitivity_only）：⚠️ **明确不采用**。分族要用到「哪些条目问的是同一个问题」这一判断，而该判断没有原始设计记录支持（各阶段预登记不含假设字段）。L3z 已因同一理由拒绝过分族方案；本次重复该结论：分族会让结论看起来更好，而这正是事后拆族的形态。

### 6.3 结论：保留 m=24，但理由不是「更保守」

保留 m=24 的四条理由，按强度排：

1. **它是已冻结的口径**。L3z 预登记写定 m=24 并被 sha 冻结；换口径必须新预登记，不能在本轮审计里就地改。
2. **A 类重复登记为 0**，所以不存在「同一假设被算两次」这一最明确的虚高情形——原以为的虚高并不存在。
3. **24 与 27 的分歧方向是 UNKNOWN**，不是「24 更保守」。这一点必须说清：m 越小门槛越松，m=24 比 m=27 **更宽松**。若同源配对其实是独立假设，就该用 27，结论会更严。
4. **对外结论对这处分歧不敏感**：见第 7 节，5 条正面结论在四种口径 × 四种校正下全部通过；唯一受影响的是 `phasel_alpha`（零效应判定），且只在 m=27 与 Bonferroni 的组合下掉出。

⚠️ 因此不能说「保留 24 是因为它更保守」。正确的说法是：**保留它因为它已被冻结，且分歧方向的证据不足（U2），而对外结论对分歧不敏感。**

## 7. 覆盖面：这个家族不是全部结论

工作区共 26 个 `*_out` 产物目录，27 条登记的序列只覆盖其中 23 个。未覆盖的 3 个：

| 目录 | summary 数 | 实验 | 判定 | 类型 | 是否实质缺口 |
| --- | --- | --- | --- | --- | --- |
| `phasei_out` | 2 | phasei-exq5-formal-v1 | EXQ5_COST_ABSORBED | 含假设检验 | **是** |
| `phaseu_out` | 1 | phaseu-nw-lag-audit-v1 | NW_LAG_SENSITIVITY_CONFIRMED | 只读审计 | 否 |
| `probe_out` | 0 | — | — | 无 summary | 否 |

「有 verdict」不等于「是待校正的假设」：`phaseu_out` 是只读审计，它的 `NW_LAG_SENSITIVITY_CONFIRMED` 是审计自身的结论，不是新假设；`probe_out` 无 summary。所以真正的实质缺口是 **1 个**，就是 `phasei_out`。

`phasei_out` 是 exQ5 可成交实验：V0 均值 +0.2307pp / t=6.357，V1 可成交 +0.1634pp / t=4.528，有完整判定，却**不在** L3u 的 27 条里。它在项目内部被当作头部结果引用，但从未进入任何一次多重检验校正的家族。

> **⇒「可引 6 条」的适用范围是登记在册的 27 条，不是项目全部结论。**
> 对外表述必须带这个范围限定。

**本审计不把 phasei 补进家族重算 m。** 补进去会改动全部门槛，而它该不该纳入本身是判断；补齐家族应作为一次新预登记，而不是在审计里顺手改口径。

## 8. 未解决项

| 编号 | 项目 | 状态 | 对结论的影响 |
| --- | --- | --- | --- |
| U1 | 27 条各自的原始研究假设文本 | UNKNOWN | class 判定（尤其 B/E 的区分）建立在反推文本上；若原始设计本意不同，家族边界会变。 |
| U2 | 同源配对是否被原始设计视为「同一假设的稳健性检查」 | UNKNOWN | 决定 m 应取 24 还是 27；影响 Bonferroni 下 6 条与 5 条之别。 |
| U3 | 未进入最终报告的试验总数 | PARTIAL | 多重检验的家族可能仍不完整；但无法量化，只能声明该局限。 |
| U4 | phasep_late 的正确检验 | UNKNOWN | 它在家族里占的名额是对的（那次检验确实做过），但它通过/不通过校正都不能直接解释为其假说的成立/不成立。 |

## 9. 本次的自我更正

### R-L3AA-1

- **撤回的话**：L3aa 报告第 5.5–5.6 节：「phasep_late 与 phaseo_netBASE 逐位相同 ⇒ m=24 偏大」
- **为什么错**：该推论只用了数据同一性。审计提纲明确禁止「仅凭序列相同…就自动决定检验家族如何划分」。两者的登记判定不同（EXECUTABLE_UNDER_BASE_FEES vs SEGMENT_DIFFERENCE_NOT_DETECTED），且都参与评分，按假设计数应各占一个名额。
- **替换为**：A 类重复登记 0 条；m=24 偏大/偏小取决于 U2（同源配对是否为独立假设），方向 UNKNOWN。
- **原文处理**：reports/L3AA_ARBITRARY_DEPENDENCE_AUDIT.md 原文不改写；本记录为更正。

## 10. 状态

家族审计部分的可判性：

- 家族成员与 m 的口径**已明确**（27 条、24 份数字、20 条出 p），且有依据与敏感性；
- 但**「该按假设还是按数字计数」无法从记录判定**（U2），且**家族覆盖不全**（至少缺 phasei）。

⇒ 家族这部分不能给「已完全核实」，只能给「已核实到记录允许的程度，并写明缺口」。
最终状态由汇总报告统一给出。

