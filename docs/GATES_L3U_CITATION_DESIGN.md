# L3u 引用规范登记：设计与验收

> 预登记 `docs/gates/l3u_prereg.json` sha256 `0bbdc5ff…`｜产物 `reports/gates/H-L3U-CITATION-REGISTRY/`｜**audit_only，不重跑实验**

## 1. 为什么做这一轮

上一轮（L3t）用统一实现重算了 27 条月度/折级序列的 MDE，产出一张分辨率表，并列出 5 条「效应量低于同族最好分辨率」的结论，准备据此整理引用口径。

复核时发现这张表**不能直接用来定引用规范**：阶段 C 的登记值是「格 A 自己的 `d_raw` 水平」（+0.0141pp、t=0.09），而它的主判定是「`spread(B) − spread(A)`」（+1.9390pp、t=6.37）。差 138 倍，且方向一个是「几乎没有」、一个是「极显著」。

这不是抄错一个数字，而是**「取某列均值」与「判定时用的统计量」不是同一个东西**。若不先核对口径就去做引用规范，会把 t=6.37 的结论写成「分辨率不足」。

所以本轮的目标改了两层：

1. 先核对：27 条登记行，每一条登记的是不是该实验**判定时真正用的那个量**；
2. 再分级：核对通过的，按「效应量 / 该设计的 MDE」给出可引用强度。

## 2. 判据

### 2.1 三类核对（预登记固定）

| 分类 | 含义 |
|---|---|
| `MATCH` | mean、t、n 全部在容差内一致（mean 1e-6、t 1e-4） |
| `MISMATCH_MEAN` | mean 或 t 越界。子标记 `t_only=True` 表示 **mean 与 n 都对、只有 t 不同** |
| `MISMATCH_N` | n 不一致。子标记 `mean_identical=True` 表示效应量本身没错、只是分母口径不同 |
| `NOT_APPLICABLE` | 该实验的判定不是单一均值（区间判定 / 多门槛 / 自持有序列） |
| `UNVERIFIABLE` | 原产物缺字段，无法核对。**不得记为通过** |

`t_only` 与 `mean_identical` 这两个子标记是必须的：Gate 1~6 的 summary 只给 iid t，而 L3t 统一用 NW(3)，两者必然差 0.05~0.25。若不区分，6 条正常条目会被写成「取错列」。

### 2.2 判定字符串按**原文**分类

```
先查否定词：NOT_DETECTED / NOT_EXECUTABLE / FAILURE / failure / ABSORBED /
            INSUFFICIENT / NEGLIGIBLE / BLOCKED / INCONCLUSIVE / REJECTED
再查肯定词：CONFIRMED / SUCCESS / DETECTED / SIGNIFICANT / EXECUTABLE / FRAGILE / ...
两者都不含 → None（不得默认成任一边）
```

顺序不能反。`INCREMENTAL_ABSORBED` 里没有肯定词，但 `NOT_DETECTED_BUT_CONFIRMED_BY_OTHER` 这类写法必须判「未检出」——原文的否定是主句。

### 2.3 分级规则（检出与未检出用两把尺子）

| 情形 | 判据 | 分级 |
|---|---|---|
| 检出（detected=True） | \|效应\| / MDE ≥ 2 | `QUOTABLE` |
| | 1 ≤ ratio < 2 | `QUOTABLE_MARGINAL`（须标 MDE） |
| | 0.5 ≤ ratio < 1 | `DIRECTION_ONLY`（只引方向） |
| | ratio < 0.5 | `NOT_QUOTABLE` |
| 未检出（detected=False） | 事后功效 ≥ 0.8 | `NULL_CONFIRMED`（可引为「确认无效应」） |
| | 0.5 ≤ 功效 < 0.8 | `NULL_WEAK` |
| | 功效 < 0.5 | `UNDERPOWERED`（只能说「无法判断」） |

两把尺子**不可互换**：检出类看「效应相对分辨率有多大」，未检出类看「这个设计有没有能力发现效应」。这就是项目铁律「功效不足 ≠ 无效应」的落地形式。

### 2.4 `grading_basis`：防「净效应被吸收」被当成「序列没效应」

`stat.mean` 为空的 7 条要显式声明分级依据：

- `self_series`（l3r / l3s / l3 / l3p）：本实验自己持有判定序列，序列均值即判定量，可正常分级；
- `multi_criteria`（phasem / phaser / phasev）：判定由多门槛或区间规则给出，序列均值不是判定量，**只报参考分级**。

没有这条声明时，`phasel_alpha` 会被判成 `NULL_CONFIRMED`（= 确认无效应）。但它的毛 alpha 是 +0.106pp、t=3.13 显著的——判定原文 `SURGE_AVOID_ABSORBED` 说的是「成本后净效应被吸收」，与「这个序列没有效应」是两件事。

## 3. 结果

| 项 | 值 |
|---|---|
| 核对一致（MATCH） | 8 / 27 |
| 取错了量 | **6 条**（4 条取错列或资金档 + 2 条窗口不对） |
| 只是 t 不同（mean 一致） | 6 条 |
| 判定不是单一均值 | 7 条 |
| 判定字符串失真 | **13 条**（含 1 条语义反转） |
| pp 口径内 MDE 极差 | 20.89× → **30.49×** |
| 「低于同族最好分辨率」名单 | 5 条 → **1 条** |
| `QUOTABLE`（可放心引点估计） | **0 条** |

### 3.1 取错了量的 6 条

| 条目 | L3t 登记 | 判定统计量 | 性质 |
|---|---|---|---|
| `phasec_d` | +0.0141pp（t=0.09） | **+1.9390pp（t=6.37）** | 取单格水平而非格间配对差 |
| `phasec2_d` | +0.6582pp（t=4.41） | +0.1069pp（t=5.01） | 取 V0 水平而非 `spread(V1)−spread(V0)` |
| `phasec3_d` | +0.6582pp（t=4.41） | +0.0468pp（t=0.60） | 同上（与 phasec2 登记值完全相同，说明都没反映各自结论） |
| `gate3_d` | −0.3982pp（n=125） | **+0.4841pp（n=108）** | 用了 2016-2026 重跑文件而非判定窗口 2007-2015，**符号相反** |
| `phasej_netA` | +0.0324pp | +0.0424pp | 用了 1000 万档而非判定档 3000 万 |
| `phasep_late` | +0.1294pp（n=129） | +0.1294pp（n=237） | 仅分母口径不同（回归全样本 vs 分段月数），**效应量没错** |

### 3.2 判定字符串失真 13 条（节选）

| 条目 | 原文 | L3t 记录 | 性质 |
|---|---|---|---|
| `phaseq_max5` | `INCREMENTAL_ABSORBED` | `MAX5_INCREMENTAL` | **语义反转** |
| `phasec2_d` / `phasec3_d` | `SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT` / `LAG_COST_NEGLIGIBLE` | 都写成 `POOL_EFFECT_CONFIRMED` | 张冠李戴 |
| `gate1_d` | `failure`（小写） | `INCONCLUSIVE` | 改写 |
| `phasee_alpha` | `LONGONLY_ALPHA_NOT_DETECTED` | `SINGLE_SIDE_NOT_DETECTED` | 改写 |

### 3.3 分级分布

| 分级 | 条目 |
|---|---|
| `QUOTABLE_MARGINAL` | `phasef_delta`（1.22×）、`phaseo_netBASE`（1.44×） |
| `DIRECTION_ONLY` | `phases_net`、`phaset_net`（各 0.79×） |
| `NULL_CONFIRMED` | `phasel_alpha` |
| `UNDERPOWERED` | `phasee`、`phaseg`、`phaseq_max5`、`l3r`、`l3s`、`l3`、`l3p` |
| `NOT_GRADED` | 15 条（核对未通过 12 + 多门槛判定 3） |

**`QUOTABLE` = 0** 是本轮最重要的定量结论：没有任何一条结论的效应量达到其自身设计分辨率的 2 倍。最好的两条也只有 1.22 与 1.44 倍。

## 4. 预登记的 3 次修订（全部留痕）

| # | 时点 | 内容 | 理由 |
|---|---|---|---|
| 1 | 首次成功产出**之前**（第一次运行因读不到 phaseo 判定均值而抛错，未写出任何结果） | `phaseo` 字段 `mean_pp`→`net_pp`；补 t/n；`phasep` 的 t 路径 `beta1_t_nw`→`t_nw` | 定位字段的笔误，判据未动 |
| 2 | 第一次运行**之后**（产物保留为 `l3u_citation_firstrun_scalebug.{json,md}`） | 5 条 `scale` 100→1 | 这五列存的本就是 pp 值，乘 100 后 MDE 放大 100 倍（9.36~14.05pp vs 同族 0.09~0.14pp）。这是项目里已犯过三次的「pp÷小数」错误，**第四次** |
| 3 | 第二次运行之后 | 补 `grading_basis` | 防「净效应被吸收」被算成「序列无效应」（`phasel_alpha` 的毛 alpha 是 t=3.13 显著的） |

三次修订的共同点：**都不是放松判据**，而是修正「定位」与「语义标注」。每次的 `why_not_a_judgement_change` 都写在预登记里。

## 5. 本轮新增的防呆

1. **`MDE_MAGNITUDE_SUSPECT` 量级自检**：任一序列重建后的 MDE 若超过同族中位 20 倍即标记。第 2 次修订就是靠它发现的（phasek 9.36pp vs 同族 0.094pp）。当前运行 0 条报警。
2. **`t_only` 子标记**：把「t 的估计量不同」与「取错列」分开，避免把 6 条正常条目算成错误。
3. **`mean_identical` 子标记**：把「分母口径不同」与「窗口取错」分开。
4. **`read_stat` 读不到必须抛错**，不返回 `None`——否则「没找到」会静默变成「没问题」。
5. **少于 10 条样本必须抛错**：样本太少的分辨率没有意义。

## 6. 踩到的坑

1. **「取某列均值」不等于「判定统计量」**。三个 phasec 实验的判定都是格间配对差，用单格水平代表它们，得到的是完全不同的结论（其中一个符号相反）。这是本轮最贵的教训。
2. **`_dig` 按 `.` 分割路径**，遇到键名本身含 `.` 会坏。本次的键是 `W_3000万|BASE`（含 `|`，安全），但若将来有含 `.` 的键，需要改为列表式路径。
3. **第一次的 `scale` 错误没有被「数值能算出来」拦住**：MDE 9.36pp 也算得出来，只是没人会相信。必须用量级自检拦截。
4. **预登记修订 3 次，说明我对 27 条的口径理解在写预登记时并不到位**；这本身就是一条教训——涉及多来源产物的登记，应当先把「判定统计量在哪」全部走查一遍再冻结。

## 7. 复现

```bash
PYTHONPATH="backend/src" python scripts/l3u_citation.py          # 约 0.1 秒
PYTHONPATH=src pytest backend/tests/gates/test_gates_citation.py -q   # 47 项
```

变异测试（3 个，全部被抓）：
- 把否定词/肯定词的匹配顺序反过来 → `test_classify_verdict_negative_wins_over_positive` 失败；
- 去掉 `t_only` 分支 → `test_compare_row_t_only_is_flagged_not_called_wrong_column` 失败；
- 忽略 `multiplier` → `test_rebuild_pivot_reproduces_pair_difference` 失败。

## 8. 边界

- 本次**不重跑任何实验**，MDE 用 L3t 同一实现（`2.8016×se_NW3`）在重建后的判定序列上重算。
- **不修改** L3t 的产物与各阶段原判定字符串（R5：保留原始结果 + 出更正）。
- 「可引用强度」是引用规范，不是对结论对错的重新判定。
- `multi_criteria` 的 3 条只给参考分级，其判定依据（多门槛 / 区间）未在本轮重新核验。
