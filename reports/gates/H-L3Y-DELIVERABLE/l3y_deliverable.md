# L3y：对外表述定稿（生成与校验报告）

状态 **DELIVERABLE_CONSISTENT**｜违规 0 处｜事实项 5 条全部回源实算｜允许数字 118 个

## 一、这一阶段做了什么

L3v 保证了卡片里的数字等于 L3u 的重算值，但「卡片 → 对外文档」这一步仍然是手工的，而本项目历史上的数字事故全部发生在这一步。本阶段把这一步也交给机器：定稿由脚本从卡片渲染，散文里仅有的几个数字必须能从源文件按指针取出来。

定稿收录可引用结论 6 条，封印 6 条（不给数字）。

## 二、事实项实算（G1）

| 事实 | 定稿写 | 源文件实算 | 源 |
| --- | --- | --- | --- |
| `n_exec_symbols` | 5472 | 5472 | `reports/ASOF_UNIVERSE_SELFCHECK.json` :: `describe.n_total` |
| `n_qfq_symbols` | 5444 | 5444 | `reports/MAX5_FEASIBILITY.json` :: `data.n_symbols` |
| `n_ic_pool` | 518 | 518 | `reports/gates/H-L3S-IC-000001/l3s_result.json` :: `pool.loaded` |
| `first_investable_year` | 2005 | 2005 | `reports/ASOF_UNIVERSE_SELFCHECK.json` :: `investable_by_year` |
| `n_first_year_investable` | 6 | 6 | `reports/ASOF_UNIVERSE_SELFCHECK.json` :: `investable_by_year.2005` |

事实表里的 `display` 只是断言；每次运行都会回到源文件按指针重取一遍。源文件变了而断言不动，就报 `FACT_DRIFT` 并拒绝写出定稿。

## 三、分级表（G5 / G6 双源）

| 分级 | 卡片重算 | 冻结产物 | 一致 |
| --- | --- | --- | --- |
| `QUOTABLE` | 1 | 1 | ✓ |
| `QUOTABLE_MARGINAL` | 4 | 4 | ✓ |
| `EXPLORATORY` | 3 | 3 | ✓ |
| `NULL_CONFIRMED` | 1 | 1 | ✓ |
| `UNDERPOWERED` | 12 | 12 | ✓ |
| `NOT_GRADED` | 6 | 6 | ✓ |

两列来自不同文件：左边从卡片重算，右边是 L3v 冻结产物。只要有一行不等，就说明改了卡片却没重跑 L3v。

## 四、闸门与违规明细

全部七道闸门通过，违规 0 处。

## 五、封印条目的原因码出处

| 结论 | 原因码 | 出处 |
| --- | --- | --- |
| `phasem_spread` | `NOT_APPLICABLE` | L3u comparison |
| `phasej_netA` | `B_different_budget` | 解封复核产物 |
| `phasek_m1net` | `B_missing_stat` | 解封复核产物 |
| `phasep_late` | `B_regression_coef` | 解封复核产物 |
| `phaser_alpha` | `NOT_APPLICABLE` | L3u comparison |
| `phasev_net` | `NOT_APPLICABLE` | L3u comparison |

## 六、预测对照（先于测量写定）

| 预测 | 内容 | 观测 | 结果 |
| --- | --- | --- | --- |
| P1 | 定稿「可对外引用」章节收录 6 条，其中恰好 1 条为 QUOTABLE；另有 3 条降级为探索性观察（EXPLORATORY）单独成章。 | {"n_clip": 6, "quotable": 1} | 成立 |
| P2 | 可对外引用的 6 条里至少 4 条的分级来源是解封复核或审计降级（tier_basis 非 l3u_grade）。 | {"unseal_based_clip": 3} | **被反驳** |
| P3 | 封印的 6 条里，恰好 3 条的原因码来自解封复核产物（不同源），另 3 条回落到 L3u 的 NOT_APPLICABLE（非单一均值）。 | {"unseal": 3, "l3u": 3} | 成立 |
| P4 | 首跑 UNSOURCED_NUMBER ≤ 2 处。若超过，说明我对「定稿里会自然出现哪些数字」的估计是错的。 | {"unsourced_firstrun": 20, "unsourced_final": 0} | **被反驳** |
| P5 | 三条 facts 首跑全部从源文件实算通过（FACT_DRIFT = 0），指针一次写对。 | {"fact_drift": 0} | 成立 |
| P6 | SEALED_LEAK = 0 且 WINDOW_N_MISMATCH = 0——封印条目一个数字都不漏，引用块里的 n 全部对得上实算。 | {"sealed_leak": 0, "window_n_mismatch": 0} | 成立 |

成立 4/6。被反驳：P2、P4。被反驳的预测原样保留，不改判据。

## 七、限制

- **数字查重不区分位置**：`allowed` 集合全局有效，只能判断「这个数字有没有出处」，不能判断「这个位置的数字是否用对了出处」。恰好等于别处合法值的手误可以放过。
- **只校验结论句与结构计数**：引用块里的限定文字不受数字校验，删掉限定不会被机器发现。
- **孤立数字无法消歧**：同一数值在多处出现时无法分辨指的是哪一个量。
- **紧贴字母的数字不参与校验**：`NW3`、`MAX5`、`m1` 里的数字被视为口径标签。这是有意为之（否则 L3v 首跑会出 13 处假阳性），但代价是这类标签写错也抓不到。
- **本阶段不产生新结论**：定稿里没有任何新的 alpha、t 或 MDE，全部数字都是既有结论的搬运。
