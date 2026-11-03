# L3x 仅 t 口径不同的 6 条：解封复核

> 预登记 `docs/gates/l3x_prereg.json` sha256 `ffdff3b75f9c…`（脚本启动校验）｜对照 L3u `f7d1a66d64c3…` 与 L3w `ce10baa7dabb…`｜**只读审计，不重跑策略**

## 一、结论先行

这 6 条的「核对未通过」与 L3w 那 6 条性质不同：**它们的均值与分母本来就逐位一致**，唯一差异是判定用 iid t、登记表用 NW(3) t。5 条解封、1 条维持封印。

最高的 `gate6_d` 解封为 `QUOTABLE_MARGINAL`（|效应|/MDE = 1.658），全项目 `QUOTABLE_MARGINAL` 因此变为 4 条——**但这 6 条没有一条产生新的强结论**，它们的效应量本来就在 1~2 倍或以下。

合并 L3w + L3x 后：`NOT_GRADED` 15 → **6**，`QUOTABLE` 0 → 1，共 9 条分级发生变化。

## 二、6 条逐条

| 条目 | 均值同源 | t 同源 | 分母同源 | ratio(NW3) | 解封分级 | 结论 |
|---|---|---|---|---|---|---|
| `gate1_d` | ✓ | t_iid | ✓ | 0.217 | UNDERPOWERED | UNSEALED |
| `gate2_d` | ✓ | t_iid | ✓ | 0.569 | UNDERPOWERED | UNSEALED |
| `gate4_d` | ✓ | t_iid | ✓ | 0.346 | UNDERPOWERED | UNSEALED |
| `gate5_d` | ✓ | t_iid | ✓ | 0.055 | UNDERPOWERED | UNSEALED |
| `gate6_d` | ✓ | t_iid | ✓ | 1.658 | QUOTABLE_MARGINAL | UNSEALED |
| `phasek_m1net` | ✓ | ✗ | ✗ | 0.426 | NOT_GRADED | STAY_SEALED |

## 三、t 同源意味着什么

这 6 条的 `stat.t` 大部分等于重建序列的 **t_iid**，而 L3t 登记表记的是 **t_nw3**。两者差 0.05~0.25，`mean` 完全一致——所以 L3u 判的是「t 的估计量不同」，不是「取错了量」。把这种情况永久封印，等于要求所有结论必须用 NW(3) 报告，而 Gate 1~6 的原始 summary 只给 iid。

**但 `t_only` 这个标记不等于「t 只是换了个口径」。** L3u 的 t_only 只比较「登记表的 t_nw3」与「summary 的 t」，没有比较「summary 的 t」与「重算的 t_iid / t_nw3」。所以被标成 t_only 的条目里，仍可能有 t 与两种标准误都对不上、甚至 summary 没给 n 的情况——本阶段就有 1 条是这样，被 U2/U3 拦下。这是闸门比标记更严格的地方，也是「标记不能替代核对」的直接证据。

仍未解封：

- `phasek_m1net`：判定 t=+1.3469 与序列的 t_iid=+0.8917／t_nw3=+1.1946 均不符：该 t 不是这条序列均值的 t（回归系数 / 另一模型），不能与序列 MDE 配对；判定分母 None ≠ 序列 n 237：必须确认是回归分母还是子样本窗口

## 四、分级稳定性：NW 滞后网格

| 条目 | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 | 稳定 |
|---|---|---|---|---|---|---|---|
| `gate1_d` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `gate2_d` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `gate4_d` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `gate5_d` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |
| `gate6_d` | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | QUOTABLE_MARGINAL | 是 |
| `phasek_m1net` | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | UNDERPOWERED | 是 |

各档 ratio（= |效应| / MDE，detected=True 才看这一列）：

| 条目 | L=0 | L=1 | L=2 | L=3 | L=4 | L=6 |
|---|---|---|---|---|---|---|
| `gate1_d` | 0.205 | 0.196 | 0.206 | 0.217 | 0.235 | 0.264 |
| `gate2_d` | 0.588 | 0.569 | 0.559 | 0.569 | 0.580 | 0.591 |
| `gate4_d` | 0.310 | 0.318 | 0.333 | 0.346 | 0.346 | 0.355 |
| `gate5_d` | 0.056 | 0.054 | 0.055 | 0.055 | 0.056 | 0.056 |
| `gate6_d` | 1.569 | 1.525 | 1.601 | 1.658 | 1.690 | 1.734 |
| `phasek_m1net` | 0.319 | 0.366 | 0.400 | 0.426 | 0.446 | 0.481 |

## 五、全项目合并（L3w + L3x）

| 分级 | L3u 原始 | L3w+L3x 后 |
|---|---|---|
| DIRECTION_ONLY | 2 | 3 |
| NOT_GRADED | 15 | 6 |
| NULL_CONFIRMED | 1 | 1 |
| QUOTABLE | 0 | 1 |
| QUOTABLE_MARGINAL | 2 | 4 |
| UNDERPOWERED | 7 | 12 |

| 条目 | 原始 | 现在 |
|---|---|---|
| `gate1_d` | NOT_GRADED | UNDERPOWERED |
| `gate2_d` | NOT_GRADED | UNDERPOWERED |
| `gate3_d` | NOT_GRADED | DIRECTION_ONLY |
| `gate4_d` | NOT_GRADED | UNDERPOWERED |
| `gate5_d` | NOT_GRADED | UNDERPOWERED |
| `gate6_d` | NOT_GRADED | QUOTABLE_MARGINAL |
| `phasec_d` | NOT_GRADED | QUOTABLE |
| `phasec2_d` | NOT_GRADED | QUOTABLE_MARGINAL |
| `phasec3_d` | NOT_GRADED | UNDERPOWERED |

剩下的 `NOT_GRADED` 才是真正不该分级的：**2 条不同源**（`phasej_netA` 资金档不同、`phasep_late` 回归系数）＋ **3 条判定非单一均值**（`phasem_spread`/`phaser_alpha`/`phasev_net`）。

## 六、预测对照（先于测量写定）

| # | 预测 | 实测 | 结论 |
|---|---|---|---|
| P1 | 6 条全部通过同源三关（U1/U2/U3 无一失败）。 | {"n_same_source": 5, "n_total": 6} | **被反驳** |
| P2 | 6 条全部解封，无一条因跨桶被拒。 | {"n_unsealed": 5} | **被反驳** |
| P3 | gate6_d 解封为 QUOTABLE_MARGINAL，ratio 落在 1.5~1.8。 | {"tier": "QUOTABLE_MARGINAL", "ratio": 1.657745315415111} | 成立 |
| P4 | gate2_d 解封为 UNDERPOWERED，功效在网格内始终低于 0.5。 | {"tier": "UNDERPOWERED", "tiers": {"0": "UNDERPOWERED", "1": "UNDERPOWERED", "2": "UNDERPOWERED", "3": "UNDERPOWERED", "4": "UNDERPOWERED", "6": "UNDERPOWERED"}} | 成立 |
| P5 | 合并后全项目 NOT_GRADED 从 11 降到 5。 | {"NOT_GRADED": 6} | **被反驳** |
| P6 | 解封后 QUOTABLE 仍为 1，QUOTABLE_MARGINAL 变为 4。 | {"QUOTABLE": 1, "QUOTABLE_MARGINAL": 4} | 成立 |

被反驳：P1、P2、P5。被反驳的预测原样保留，不改判据（R5）。

## 七、L3v 卡片补丁

| 条目 | 卡片现值 | 建议值 |
|---|---|---|
| `gate1_d` | NOT_GRADED | UNDERPOWERED |
| `gate2_d` | NOT_GRADED | UNDERPOWERED |
| `gate4_d` | NOT_GRADED | UNDERPOWERED |
| `gate5_d` | NOT_GRADED | UNDERPOWERED |
| `gate6_d` | NOT_GRADED | QUOTABLE_MARGINAL |

与 L3w 的补丁一起合并进 `docs/gates/l3v_claims.json` 后重跑 L3v 闸门；合并时 L3v 的 `unseal_source` 需要同时指向 L3w 与 L3x 两个产物。

## 八、限制

- 本实验不重跑任何策略，只从 L3u 已重建的判定序列上重算 NW 滞后网格；L3u 与 L3w 产物 sha256 在启动时校验。
- 解封不等于推翻 L3u：L3u 的封印记录原样保留，本实验是追加的分级意见。
- t_only 的解封理由比 L3w 更弱一档——它不涉及「判定量取错」，只涉及「同一个 t 的两种标准误」。所以这 6 条即使解封，分级也全部落在 MARGINAL 及以下，不会产生新的强结论。
- gate1 的 n=48、gate5 的 n=249 等窗口差异仍在，解封不改变「窗口级统计 ≠ 账户收益」这条限定。
- 解封只代表「这条结论的效应量可以被分级」，不代表它可交易；窗口、成本口径、横截面与账户收益的区别依旧适用。

