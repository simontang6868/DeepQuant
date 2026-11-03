> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# 阶段 L3af 结题报告：成本后净收益重建与当前引用清单审计

> **最终状态**：`VERIFIED_WITH_DOCUMENTED_LIMITATIONS`
> **预登记**：`docs/gates/l3af_prereg.json`（sha256 `ed2a03162ce3907bef3e75b839a201cfad0799973ff14b0a145a51eb60d58cb8`）
> **产物**：`reports/gates/H-L3AF-COST-NET/`

## 0. 一句话

逐月净收益可以重建（换手在 phase O 的产物里，且与 phasei 逐位同源）；重建后主判定不变，但 L3ae 的「t 是上界」这句话方向错了，必须撤回；7 条正面结论在四种 m 口径下完全一致；其中带成本后证据的仍是 **1 条**（另 1 条分档相反）。

## 1. 哪些结果真正复现成功

| 复现项 | 判据 | 结果 |
| --- | --- | --- |
| phase O 与 phase I 的 alpha 同源 | max\|diff\| ≤ 1e-12 | 0.0（237 月） |
| 成本算术 | \|cost_BASE − 0.102·to\| ≤ 6e-7 | 5.320e-07 |
| phase O 净列恒等式 | 残差 ≤ 4.58e-06 | 2.950e-06 |
| 构造 Q 的 se 复现 phase O summary | 相对 ≤ 1e-3 | 最大 1.007e-05 |
| L3z 的 p 链（22/22） | 逐位 | 由 `family.reproduce_from_series` 沿用，本阶段未改动 |
| 四种校正两套实现 | 逐条一致 | 全部一致 |

## 2. 哪些结论的状态发生变化

| 条目 | 旧状态（L3ad/L3ae） | 新状态（L3af） | 原因 |
| --- | --- | --- | --- |
| `phasei_v1` | 「成本吸收」（仅两档披露） | `QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST` | 补 5 档逐月净收益，档间结论相反 |
| `phaseo_netBASE` | 可引（净收益） | `QUOTABLE_WITH_COST_EVIDENCE` | 经逐月序列独立复算，se 来自净序列而非毛序列 |
| `gate6_d` / `phasec2_d` / `phasec_d` / `phasef_delta` | 可引 | `QUOTABLE_STAT_ONLY` | 新增 S3 检查：判定量不是净收益，不存在净收益版本 |
| `phasei_v0` | 未登记 | `QUOTABLE_STAT_ONLY`（S3 ABSENT） | 已纳入家族（m 25）；V0 换手未落盘，不能借 V1 |
| L3ae 的「t 是上界」 | 断言 | **撤回** | 方向错（真实档 se_net < se_gross），幅度可忽略 |

## 3. phasei 的真实费率净收益显著性是否成立

**分两句话答，缺一句都会误导：**

1. **按冻结的判定口径（0.3%）**：净额 +0.050716 pp/月，t(iid) +1.0474、t(NW3) +1.4162 ⇒ 不显著，`EXQ5_COST_ABSORBED` **不变**。
2. **按真实等价费率（0.051%，1 亿档）**：净额 +0.144253 pp/月，t(iid) +2.9798、t(NW3) +4.0046 ⇒ 显著。这是**敏感性**结果，不是判定结果。

所以「真实费率下的净收益显著性」在统计上成立、在流程上**不构成通过**：判定档是预登记选定的 0.3%，改成 0.051% 属于换主口径，须新预登记。本阶段不动它，只把它作为披露。

## 4. m=25 的口径依据是否充分

**分三句话说：**

1. **作为「名额」口径充分**：判据只问「是否逐位相同且完全覆盖」，与 p 无关；FWER 论证（同一 p 读两次，计 2 名额的实际拒绝率是 α/2）表明计 2 是保守偏差而非正确性。
2. **作为「假设」口径不充分**：A 类重复登记 0 条，说明同值条目对应的科学问题并不同。「独立随机变量数」是名额数，不是假设数。
3. **但对对外集合没有影响**：m=24/25/27/29 与子族口径下，7 条正面结论完全相同（P7、P8 均成立）。所以 m 之争**不改变结论**，只改变「怎么说」。

## 5. 当前有哪些可引用的统计结论

**7 条**通过 m=25 四种校正：

| ID | 效应（pp/月） | t(NW3) | 状态 |
| --- | --- | --- | --- |
| `gate6_d` | +0.7084 | +4.6443 | `QUOTABLE_STAT_ONLY` |
| `phasec2_d` | +0.1069 | +5.0149 | `QUOTABLE_STAT_ONLY` |
| `phasec_d` | +1.9390 | +6.3675 | `QUOTABLE_STAT_ONLY` |
| `phasef_delta` | +0.7405 | +3.4086 | `QUOTABLE_STAT_ONLY` |
| `phasei_v0` | +0.2307 | +6.3572 | `QUOTABLE_STAT_ONLY` |
| `phasei_v1` | +0.1634 | +4.5284 | `QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST` |
| `phaseo_netBASE` | +0.1360 | +4.0386 | `QUOTABLE_WITH_COST_EVIDENCE` |

此外 `phasel_alpha` 只在 m=24/25（去重口径）下四种校正全过，m=27/29 下 Bonferroni 不过，且其判定是 `SURGE_AVOID_ABSORBED`（净效应被吸收）——**不是正面结论**，单列。

## 6. 哪些结论有成本后证据

- `phaseo_netBASE`：**有**（判定量本身即净收益，3000 万 / BASE 口径）。
- `phasei_v1`：**有但分档相反** —— 0.3% 档不显著、0.051%~0.1% 档显著。引用必须带档位。
- `phasei_v0`：**无**（V0 逐月换手未落盘；借用 V1 换手违反「换手不沿用别的信号」）。
- 其余 4 条：**无**（判定量是价差/差值，不存在净收益版本）。

## 7. 是否存在独立样本外证据

**不存在。** 全部窗口止于 2026-09，没有前瞻期数据参与。S4 在 7 条上全部为 ABSENT，且四项（样本外/成交模型/容量/风险）逐项都不完整：成交模型只有冲击上限常数、容量只有覆盖率描述、风险只到 MDD。

## 8. 为什么本阶段仍然不能据此启动实盘

1. **没有样本外**：所有结论都是同一段历史内的估计，不能排除样本内选择。
2. **没有成交回放**：成交按 bar 收盘价、只在信号日剔除不可成交标的；真实下单的价格路径与部分成交未建模。
3. **成本不确定性的来源没查完**：改费率档可以算，但券商实际成交回报（唯一外部输入）缺失，所以「真实费率」本身仍是一个**情景假设**，不是测得的事实。
4. **只有 1 条带成本后证据**，且它 |效应|/MDE 只有 1.44 倍；分档后压力口径净值趋近 0。
5. **m 的问题没解决，只是不影响这 7 条**：研究历史缺口的关闭需要新数据，不是新计算。

## 9. 下一阶段必须解决的问题

1. **拿到券商成交回报**（两家）：这是唯一能替代「情景费率」的东西，也是 S3→S4 的必经之路。
2. **V0 的逐月换手**：需要重跑 V0 并落盘 `to`，否则 `phasei_v0` 的 S3 永远是 ABSENT。
3. **经济门槛的重新预登记**：现存的 0.30pp 门槛是为价差类写的，净收益类没有门槛；且 C2 文档与 C/E/F 三处矛盾（D2）。这两件事都要新预登记，不能回头改旧文。
4. **研究历史缺口**：尝试过但未报告的策略/窗口/成本档位无法从产物恢复。要关闭它只能靠外部记录（实验日志/对话记录），本仓库内做不到。
5. **成本波动幅度**：本阶段算出了逐月成本序列（sd 已报），但「成本为什么波动」（换手驱动 vs 费率驱动）需要逐月换手分解到个股层面，产物里没有。

## 10. 闸门与预测

### 10.1 闸门

| 阶段 | 闸门数 | 违规 |
| --- | --- | --- |
| 成本重建 | 8 | 0 |
| 家族台账 | 4 | 0 |
| 证据分级 | 3 | 0 |


### 10.2 预测核对

| ID | 事前/事后 | 结论 | 是否成立 |
| --- | --- | --- | --- |
| P1 | priori（侦察时已观察） | phaseo 的 alpha_pp 与 phasei 的 alpha_v1_pp 在 237 个月上逐位相同（max|diff| = 0.0） | 成立 |
| P2 | priori（侦察时已观察） | cost_BASE_pp = 0.102×to，max|残差| ≤ 6e-7 | 成立 |
| P3 | priori（侦察时已观察） | 五档净收益随费率单调递减，且相邻档差 = 2×mean(to)×Δrate×100 | 成立 |
| P4 | priori（侦察时已观察） | Var(cost)/Var(gross) < 1e-2（全部五档） | 成立 |
| P5 | priori（侦察时已观察） | se_net/se_gross ∈ [0.99, 1.01]（全部五档）——即成本对标准误的影响 < 1% | 成立 |
| P6 | priori（侦察时已观察） | 「se_net ≥ se_gross」在 T_0p3（压力档）成立，在 T_1e8（真实档）**不成立** | 成立 |
| P10 | priori | 真实费率档下 phasei 的净收益 iid t ∈ [2.8, 3.2]、NW(3) t ∈ [3.8, 4.2]，但**这不改变** phasei 的 EXQ5_COST_ABSORBED 判定（判定档是 0.3%） | 成立 |
| P12 | priori | L3ae 的月度成本被高估：它用 236 个非零交易月的均值 0.188620，而 237 个月的序列均值是 0.187824，差 0.000796 ⇒ 0.3% 档成本高估 4.8e-4 pp | 成立 |
| P7 | priori | m=24/25/27/29 下 7 条正面结论的通过集合完全相同 | 成立 |
| P8 | priori | 子族口径（族内独立校正）下 7 条仍全部通过 | 成立 |

## 10.3 变异测试（闸门能不能真的抓）

共 10 条变异，按预期判定 10 条，不符 0 条；反向对照（未变异）违规 0 条。

| ID | 变异 | 期望被谁抓到 | 实际 | 说明 |
| --- | --- | --- | --- | --- |
| M1 | 把单边换手当双边（成本 ×2） | `G2_COST_ARITHMETIC` | 抓到 | 命中 ['G2_COST_ARITHMETIC'] |
| M2 | 净收益 t 复用毛收益标准误 | `G6_SE_FROM_NET` | 抓到 | 命中 ['G6_SE_FROM_NET'] |
| M3 | 某一档少一个月（月份未对齐） | `G4_MONTH_ALIGNMENT` | 抓到 | 命中 ['G4_MONTH_ALIGNMENT'] |
| M4 | 把成本当逐月常数（se_net 恒等于 se_gross） | `summarize_se_claim 的 TIER_DEPENDENT` | 抓到 | 判为 HOLDS_ALL_TIERS |
| M5 | m 算术写死 24（与成员数不符） | `G9_FAMILY_CONSISTENCY` | 抓到 | 命中 ['G9_FAMILY_CONSISTENCY'] |
| M6 | BY 漏乘 H_m（真实数据）→ 预期抓不到 | `cross_check 的 by 不一致` | **没抓到** | by 一致=True；本数据上 BH 与 BY 的拒绝集合恰好相同 ⇒ 抓不到。这不是闸门弱，是**数据不给区分力**（L3ac M6 完全同型） |
| M6b | BY 漏乘 H_m（合成数据，BH≠BY） | `cross_check 的 by 不一致` | 抓到 | by 一致=False（mine 拒 0 条，ref 拒 12 条） |
| M7 | 无净序列的条目被标为成本后证据 | `G10_NO_AUTO_PROMOTION` | 抓到 | 命中 ['G10_NO_AUTO_PROMOTION'] |
| M8 | phasei_v0 借用 V1 换手并直接判 PASS | `G10_NO_AUTO_PROMOTION` | 抓到 | 命中 ['G10_NO_AUTO_PROMOTION'] |
| M9 | 常数成本时 Var(c) 应为精确 0 | `variance_decomposition 的 var_cost==0` | 抓到 | var_cost=0.0 |

> M4 的期望不是「闸门报错」而是「结论发生变化」：常数成本不会违反任何恒等式，

> 它只会让 se_net 恰好等于 se_gross ——所以能不能抓到只取决于有没有把四个方差分量算出来。

> 这就是本阶段不用「多写几条断言」而用「多算几个分量」的理由。

> M6 未抓到，原因是真实数据上 BH 与 BY 的拒绝集合恰好相同 —— 两法一致不能当作实现正确的证据，所以另加 M6b 用合成数据把两者分开；M6b 抓到即证明互校机制本身有效。

## 11. 更正日志

| 编号 | 标题 | 影响 |
| --- | --- | --- |
| D1 | L3ae 的月度成本用了 236 个非零交易月的均值 | 0.3% 档成本被高估 0.000478 pp/月、0.1% 档高估 0.000159、0.051% 档高估 0.000081。t(iid) 相应从 1.0377 变为 1.0474（0.3% 档），判定不变。 |
| D2 | 经济意义门槛的记录自相矛盾 | 对 7 条正面结论的 S2 判定**无影响**：受门槛约束的三条（phasec_d 1.94、phasec2_d 0.107、phasef_delta 0.740）在 0.30 与 0.60 两种取值下过/不过都不变。 |
| D3 | 「真实净收益标准误不小于毛收益标准误、t 只能当上界」方向错 | 纯 se 效应带来的 t 变化最大 3.1e-4，对任何判定都无影响。但「上界」暗示存在保守余量，会误导下一位使用者。 |
| D4 | phasei 的 summary.json 在磁盘上是 CRLF，与 manifest 记录的 sha256 不符 | 若只认 raw 哈希，会把「换行被改」误报成「内容被改」；若只认 LF 哈希，会漏掉真正的内容篡改。 |
| D5 | net_CONS_1000w 无法用落盘的成本列校验 | 无数据损失。1000 万档 CONSERVATIVE 的成本无法事后校验，只能由脚本重算。 |
| D6 | phasei_v0 的逐月换手未落盘，S3 只能不判 | phasei_v0 的 S3 为 ABSENT。若强行用 V1 换手估 V0 成本，等于「换手沿用别的信号」。 |
| D7 | phasei 从未登记进检验家族 | 纳入后 m 由 24（指纹）变为 25（独立随机变量），7 条正面结论的集合不变（G9B 验证 4 种 m 口径）。纳入只会抬高门槛，不会降低。 |
| D8 | 归档上一版清单时，重跑 L3ab 脚本把归档件的时间戳改了 | 历史件的**内容**未变，但哈希变了。任何以 0d16c355 为基准的外部引用需要按新哈希更新。 |

## 12. 交付物

| 文件 | 内容 |
| --- | --- |
| `docs/gates/l3af_prereg.json` | |
| `docs/gates/l3af_corrections.json` | |
| `backend/src/deepquant/gates/cost_net.py` | |
| `backend/src/deepquant/gates/family_af.py` | |
| `backend/src/deepquant/gates/evidence.py` | |
| `backend/tests/gates/test_gates_l3af_cost.py` | |
| `backend/tests/gates/test_gates_l3af_family.py` | |
| `backend/tests/gates/test_gates_l3af_evidence.py` | |
| `scripts/l3af_reports.py` | |
| `scripts/l3af_mutation_test.py` | |
| `reports/gates/H-L3AF-COST-NET/l3af_cost_net.json` | |
| `reports/gates/H-L3AF-COST-NET/l3af_family.json` | |
| `reports/gates/H-L3AF-COST-NET/l3af_evidence.json` | |
| `reports/gates/H-L3AF-COST-NET/l3af_monthly_net.csv` | |
| `reports/L3AF_COST_NET_RETURN_RECONSTRUCTION.md` | |
| `reports/L3AF_TEST_FAMILY_AUDIT.md` | |
| `reports/L3AF_CURRENT_EVIDENCE_STATUS.md` | |
| `reports/STAGE_REPORT_L3AF_FINAL.md` | |
| `docs/DELIVERABLE_QUOTE_SHEET_CURRENT.md` | |

## 13. 九问速答

1. **哪些结果真正复现成功？** alpha 同源（逐位相同）、成本算术、phase O 净列恒等式、构造 Q 的 se、四种校正两套实现——五项全部通过，违规 0。
2. **哪些结论的状态发生变化？** `phasei_v1` 由「成本吸收」升级为「分档相反」；`phaseo_netBASE` 的 S3 由「推断」变为「逐月复核」；L3ae 的「t 是上界」撤回。
3. **phasei 的真实费率净收益显著性是否成立？** 统计上成立（真实费率档 t(NW3)=+4.00），流程上不构成通过——判定档是 0.3%，改档须新预登记。
4. **m=25 的口径依据是否充分？** 作为名额口径充分、作为假设口径不充分；但四种 m 与子族口径下 7 条正面结论完全相同，故不影响对外集合。
5. **当前有哪些可引用的统计结论？** 7 条通过 m=25 四种校正；`phasel_alpha` 只在去重口径下成立且方向为零效应，单列。
6. **哪些结论有成本后证据？** 1 条（`phaseo_netBASE`）+ 1 条分档相反（`phasei_v1`）；`phasei_v0` 因换手未落盘不判。
7. **是否存在独立样本外证据？** 不存在。S4 在 7 条上全部 ABSENT。
8. **为什么本阶段仍然不能据此启动实盘？** 无样本外、无成交回放、无券商交割单、只有 1 条带成本后证据且压力口径趋零、研究历史缺口未关闭。
9. **哪些问题必须由下一阶段解决？** 券商成交回报、V0 逐月换手、净收益类的经济门槛预登记、研究历史缺口、成本波动的个股级分解。

