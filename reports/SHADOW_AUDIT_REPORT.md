# 影子复盘异常审计报告

- 生成时间：2026-10-27T06:53:18.067324+00:00
- 基线版本：t6-baseline-v1（摘要 `287c03334a2810d9ed2301d51b87c132b4eb5346468d1d3b380a4d67659ee9b4`）
- 扫描会话：167，标记会话：111
- 标记率（标记会话 / 扫描会话）：66.5%　——这是「有多少会话被标记」，不是准确率，也不代表真实异常率

## 一、命中规则统计

| 规则码 | 规则说明 | 维度 | 严重度 | 命中会话数 |
| --- | --- | --- | --- | --- |
| `param_skeleton_unseen` | 参数结构与基线不同（键集合或数量级变化） | 条件模式 | 中 | 30 |
| `step_order_unseen` | 出现基线中从未出现的相邻操作跳转 | 顺序 | 中 | 26 |
| `status_unseen` | 该操作出现基线中未曾出现的执行结果 | 条件模式 | 中 | 23 |
| `dangling_reject` | 会话以被拒绝的操作收尾（未重试成功） | 会话终态 | 低 | 20 |
| `reject_storm` | 同一会话内连续 ≥3 次被拒绝 | 顺序 | 高 | 15 |
| `operator_unknown` | 操作人不在基线名单内 | 上下文 | 高 | 15 |
| `session_multi_operator` | 同一会话出现多个操作人 | 上下文 | 高 | 15 |
| `duration_outlier` | 单步耗时显著超过基线上界 | 时长 | 低 | 15 |
| `prerequisite_missing` | 缺少业务前置动作（如对比前未完成两次回测） | 顺序 | 中 | 10 |

## 二、逐条明细（按严重度排序）

### eval:operator_swap:session:s0013#5（高）

- 操作人：bob｜开始时间：2026-10-27T06:42:53.781229+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `87dbed05060a64391bf00a6a11c055c7` / 40 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['bob', 'outsider'] | `e981c19b9ff12dc0b4eb7d2d2f8152ac` / 41 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0023#4（高）

- 操作人：carol｜开始时间：2026-10-27T06:43:07.663838+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `6bc2255f5ab71724333771d392060d1d` / 69 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `53a91dcef811c41a46182c9d154d2708` / 70 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0037#1（高）

- 操作人：bob｜开始时间：2026-10-27T06:43:27.737298+00:00
- 操作序列：strategy.list → dataset.list
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `c973497efcb31be08e3b0d667d1dc974` / 111 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['bob', 'outsider'] | `c973497efcb31be08e3b0d667d1dc974` / 111 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0042#8（高）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `37c8591069a0f6fb7386765d7c98f8f4` / 122 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['alice', 'outsider'] | `6921e402b87eb956a02a7b5dad3d5d50` / 123 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0045#12（高）

- 操作人：alice｜开始时间：2026-10-27T06:43:36.624490+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `e6bc30f14a55cff6f8e4cb93946f10a0` / 129 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['alice', 'outsider'] | `5519bf3c55d91d66a8a6364722d84280` / 130 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0076#10（高）

- 操作人：bob｜开始时间：2026-10-27T06:44:17.816565+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `05e2131434ba3297c24e34d41a095cc6` / 216 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['bob', 'outsider'] | `05e2131434ba3297c24e34d41a095cc6` / 216 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0077#2（高）

- 操作人：carol｜开始时间：2026-10-27T06:44:19.244115+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `1c0257e003c61ff364f80fbc00c0713c` / 218 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `b302adcc2619b13d355a73d57cebb636` / 219 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0077#9（高）

- 操作人：carol｜开始时间：2026-10-27T06:44:19.244115+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `b302adcc2619b13d355a73d57cebb636` / 219 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `b302adcc2619b13d355a73d57cebb636` / 219 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0080#7（高）

- 操作人：carol｜开始时间：2026-10-27T06:44:24.289786+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `8b20b321a2bece70a78c0ca43d627f9c` / 228 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `f758983add190e402f2217cbae492c02` / 229 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0086#6（高）

- 操作人：carol｜开始时间：2026-10-27T06:44:33.074632+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `0aab4e5e7af3c44ffe77acebac504014` / 246 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `0aab4e5e7af3c44ffe77acebac504014` / 246 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0098#3（高）

- 操作人：carol｜开始时间：2026-10-27T06:44:50.703005+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `0344cdbaa974a342b9fcf5b193becd26` / 282 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['carol', 'outsider'] | `0344cdbaa974a342b9fcf5b193becd26` / 282 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0103#14（高）

- 操作人：bob｜开始时间：2026-10-27T06:44:59.082591+00:00
- 操作序列：dataset.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `8a0d30f3a72c85dfcb7e3a516c2c53d8` / 298 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['bob', 'outsider'] | `8a0d30f3a72c85dfcb7e3a516c2c53d8` / 298 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0108#11（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:05.776234+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `f0c2dbc1efbf837a33d38b870001b561` / 311 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['alice', 'outsider'] | `f0c2dbc1efbf837a33d38b870001b561` / 311 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0123#13（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:25.163095+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `c987648ee0e5ba13c04fbe3ae831bb7f` / 351 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['alice', 'outsider'] | `c987648ee0e5ba13c04fbe3ae831bb7f` / 351 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:operator_swap:session:s0126#15（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:30.768526+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `operator_swap` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `operator_unknown` | 操作人 'outsider' 不在基线名单 ['alice', 'bob', 'carol'] 内 | `192163e5a1664b899c5fed8dcfaa589f` / 362 | 核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条 |
| `session_multi_operator` | 同一会话出现 2 个操作人：['alice', 'outsider'] | `192163e5a1664b899c5fed8dcfaa589f` / 362 | 核实会话是否被共享或凭证被转借；共享会话会破坏追责链 |

### eval:reject_burst:session:s0001#10（高）

- 操作人：bob｜开始时间：2026-10-27T06:42:36.067309+00:00
- 操作序列：strategy.list → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `4f43922bfa92cb99cda239d73b2543f9` / 3 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `d6b345cdc3147b8f72c823c614bf3376` / 4 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `ff4a3444f5a8e9486f90f1b935980da1` / 5 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `ff4a3444f5a8e9486f90f1b935980da1` / 5 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0013#4（高）

- 操作人：bob｜开始时间：2026-10-27T06:42:53.781229+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `87dbed05060a64391bf00a6a11c055c7` / 40 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `632d7f1e37ca8562f3f931c508494f8e` / 42 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |

### eval:reject_burst:session:s0030#11（高）

- 操作人：alice｜开始时间：2026-10-27T06:43:17.477513+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `349c1856beb55aa152c41a81fcaa34d3` / 89 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `db060d1af8e459da69f94cf12b7631b9` / 91 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `db060d1af8e459da69f94cf12b7631b9` / 91 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0045#2（高）

- 操作人：alice｜开始时间：2026-10-27T06:43:36.624490+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `e6bc30f14a55cff6f8e4cb93946f10a0` / 129 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `status_unseen` | 操作 portfolio.backtest 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `6dddca4cb66a377000b1bdad353d1aeb` / 131 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `6dddca4cb66a377000b1bdad353d1aeb` / 131 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `6dddca4cb66a377000b1bdad353d1aeb` / 131 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0057#12（高）

- 操作人：alice｜开始时间：2026-10-27T06:43:51.938523+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `d7d528666d2da8a73bd2f89710c38d6f` / 161 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `a6fef1c41288e0b52fd2c4251a7887ad` / 163 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |

### eval:reject_burst:session:s0058#15（高）

- 操作人：bob｜开始时间：2026-10-27T06:43:53.858608+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `ba3dd0aab08d525179d42af06b677d7d` / 165 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `84b3a8131f00c956e317986aa315252a` / 167 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `84b3a8131f00c956e317986aa315252a` / 167 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0069#14（高）

- 操作人：alice｜开始时间：2026-10-27T06:44:07.848113+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `d5e5edadb870f940fa3f6b80dcb3abf8` / 195 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `40aeeddd27fea909567c13ae33287988` / 197 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `40aeeddd27fea909567c13ae33287988` / 197 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0073#9（高）

- 操作人：bob｜开始时间：2026-10-27T06:44:13.732006+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `119c7101ea40a60c14ceeb263c363594` / 207 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `e2ce83655f2ea176508037ead3f26c31` / 209 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `e2ce83655f2ea176508037ead3f26c31` / 209 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0076#1（高）

- 操作人：bob｜开始时间：2026-10-27T06:44:17.816565+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `1ee09a4283e3183377b9e3a8cb99346c` / 215 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `d109d02c29b37206cfdbb47798362950` / 217 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `d109d02c29b37206cfdbb47798362950` / 217 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0076#5（高）

- 操作人：bob｜开始时间：2026-10-27T06:44:17.816565+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `1ee09a4283e3183377b9e3a8cb99346c` / 215 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `d109d02c29b37206cfdbb47798362950` / 217 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `d109d02c29b37206cfdbb47798362950` / 217 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0117#6（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:18.050273+00:00
- 操作序列：strategy.list → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `aa0e2b85f9e00c0c024d6c541538c3a2` / 335 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `1a2e026ed1d6b8f73fd1884c30bff7fd` / 336 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `8623923b43180f0a96d5de7c7aaafeea` / 337 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `8623923b43180f0a96d5de7c7aaafeea` / 337 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0123#8（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:25.163095+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 backtest.compare 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `288736230cf8cfec070995391ea819c6` / 354 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `288736230cf8cfec070995391ea819c6` / 354 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `288736230cf8cfec070995391ea819c6` / 354 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0124#7（高）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 backtest.compare 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `707b5cd5844fdeed6631058ef975fe40` / 357 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:reject_burst:session:s0126#13（高）

- 操作人：alice｜开始时间：2026-10-27T06:45:30.768526+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `reject_storm` | 连续 3 次操作被拒绝 | `7a7ca0395636f25465f2b22d3ee7356f` / 364 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |

### eval:reject_burst:session:s0145#3（高）

- 操作人：bob｜开始时间：2026-10-27T06:45:54.701291+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `reject_burst` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `03316167c1ca373d0cfe5d6d844e8de0` / 410 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `reject_storm` | 连续 3 次操作被拒绝 | `9836cf79729630a197b203042d094f6a` / 412 | 判断是参数反复试错还是上游故障；连续拒绝应触发人工介入 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `9836cf79729630a197b203042d094f6a` / 412 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:drop_step:session:s0057#12（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:51.938523+00:00
- 操作序列：dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `f92ac2545b4eb4381ecb56f6c2afc360` / 164 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0057#5（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:51.938523+00:00
- 操作序列：dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `f92ac2545b4eb4381ecb56f6c2afc360` / 164 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0117#13（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:18.050273+00:00
- 操作序列：strategy.list → backtest.run
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 strategy.list → backtest.run 这一跳转 | `8623923b43180f0a96d5de7c7aaafeea` / 337 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:drop_step:session:s0124#15（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0124#3（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0124#9（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0143#1（中）

- 操作人：carol｜开始时间：2026-10-27T06:45:51.289429+00:00
- 操作序列：dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `88c4c0a190dbb7cd8c79616074d5d19a` / 406 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:drop_step:session:s0143#2（中）

- 操作人：carol｜开始时间：2026-10-27T06:45:51.289429+00:00
- 操作序列：dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `drop_step` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 1 次 | `88c4c0a190dbb7cd8c79616074d5d19a` / 406 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |

### eval:insert_rare_op:session:s0006#15（中）

- 操作人：alice｜开始时间：2026-10-27T06:42:44.220731+00:00
- 操作序列：dataset.list → portfolio.backtest → backtest.run
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 portfolio.backtest → backtest.run 这一跳转 | `ae7c734e7dc35c5e5ab6a2726754e0c4` / 21 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:33292c4be6ad45ed` / 21 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0012#11（中）

- 操作人：alice｜开始时间：2026-10-27T06:42:52.377840+00:00
- 操作序列：dataset.list → portfolio.backtest → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 portfolio.backtest → backtest.run 这一跳转 | `3c14761af805d11edad52a3ceb10e4c3` / 38 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:99d05b77afd64de9` / 38 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0061#8（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:58.004620+00:00
- 操作序列：strategy.list → dataset.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:5b3838f6c769e3c8` / 175 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0068#14（中）

- 操作人：carol｜开始时间：2026-10-27T06:44:06.897416+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:a361355b0d565272` / 194 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0070#3（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:09.242880+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:12ddd3697dd2e836` / 199 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0073#7（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:13.732006+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest → backtest.run
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 portfolio.backtest → backtest.run 这一跳转 | `e2ce83655f2ea176508037ead3f26c31` / 209 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:d0eeb3f30f528ab2` / 209 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0098#9（中）

- 操作人：carol｜开始时间：2026-10-27T06:44:50.703005+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:72bafd718dbd4355` / 282 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0103#10（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:59.082591+00:00
- 操作序列：dataset.list → backtest.compare → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 dataset.list → backtest.compare 这一跳转 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `step_order_unseen` | 基线中从未出现 backtest.compare → portfolio.backtest 这一跳转 | `8a0d30f3a72c85dfcb7e3a516c2c53d8` / 298 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 0 次 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |
| `param_skeleton_unseen` | 操作 backtest.compare 的参数结构与基线 2 种形态均不同 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0103#12（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:59.082591+00:00
- 操作序列：dataset.list → backtest.compare → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 dataset.list → backtest.compare 这一跳转 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `step_order_unseen` | 基线中从未出现 backtest.compare → portfolio.backtest 这一跳转 | `8a0d30f3a72c85dfcb7e3a516c2c53d8` / 298 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 0 次 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |
| `param_skeleton_unseen` | 操作 backtest.compare 的参数结构与基线 2 种形态均不同 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0103#13（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:59.082591+00:00
- 操作序列：dataset.list → portfolio.backtest → backtest.compare
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 portfolio.backtest → backtest.compare 这一跳转 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `prerequisite_missing` | backtest.compare 前应已完成 ≥2 次 backtest.run，本会话只有 0 次 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 补齐前置动作后重做；前置不成立的操作结果不可引用 |
| `param_skeleton_unseen` | 操作 backtest.compare 的参数结构与基线 2 种形态均不同 | `injected:synthetic:56b0a1b4936fd8ad` / 298 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0107#2（中）

- 操作人：carol｜开始时间：2026-10-27T06:45:04.737994+00:00
- 操作序列：dataset.list → backtest.run → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:34a1d54b71cfce8e` / 309 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0121#1（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:23.292714+00:00
- 操作序列：dataset.list → strategy.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 strategy.list → portfolio.backtest 这一跳转 | `injected:synthetic:9d103adfce9c2cac` / 347 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:9d103adfce9c2cac` / 347 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0126#6（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:30.768526+00:00
- 操作序列：dataset.list → portfolio.backtest → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 portfolio.backtest → backtest.run 这一跳转 | `192163e5a1664b899c5fed8dcfaa589f` / 362 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:ad0d741706df5dcd` / 362 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0139#5（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:47.325015+00:00
- 操作序列：dataset.list → strategy.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 strategy.list → portfolio.backtest 这一跳转 | `injected:synthetic:3eaa43ede04948d3` / 396 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:3eaa43ede04948d3` / 396 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:insert_rare_op:session:s0143#4（中）

- 操作人：carol｜开始时间：2026-10-27T06:45:51.289429+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare → portfolio.backtest
- 评估标记：本次命中来自注入的 `insert_rare_op` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.compare → portfolio.backtest 这一跳转 | `injected:synthetic:09ed2424f38a11f9` / 406 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |
| `param_skeleton_unseen` | 操作 portfolio.backtest 的参数结构与基线 1 种形态均不同 | `injected:synthetic:09ed2424f38a11f9` / 406 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0003#8（中）

- 操作人：alice｜开始时间：2026-10-27T06:42:39.413424+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `dc517fe3140315428a6e370b30d70bd5` / 11 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0013#11（中）

- 操作人：bob｜开始时间：2026-10-27T06:42:53.781229+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `e981c19b9ff12dc0b4eb7d2d2f8152ac` / 41 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0019#14（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:01.788685+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `e3bb926d4b471c8ead4eac0db016bf14` / 59 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0042#1（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `b87fd9bdfe276212e4a05ab7b78f945e` / 124 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0042#2（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `6921e402b87eb956a02a7b5dad3d5d50` / 123 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0042#5（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `6921e402b87eb956a02a7b5dad3d5d50` / 123 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0042#9（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `6921e402b87eb956a02a7b5dad3d5d50` / 123 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0064#7（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:00.772359+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `b2a327bb7e2fc3017cd75775d427db2e` / 181 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0070#3（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:09.242880+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `bd73a6e500cefa8fb6484824084e9794` / 199 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0070#6（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:09.242880+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `bd73a6e500cefa8fb6484824084e9794` / 199 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0073#4（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:13.732006+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `e7d07347ea34800d4daa313e4fffd6eb` / 208 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0076#12（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:17.816565+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `05e2131434ba3297c24e34d41a095cc6` / 216 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0080#15（中）

- 操作人：carol｜开始时间：2026-10-27T06:44:24.289786+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `f758983add190e402f2217cbae492c02` / 229 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0098#13（中）

- 操作人：carol｜开始时间：2026-10-27T06:44:50.703005+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `bddbb1a0a01c21447852f022d67c6556` / 281 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:param_drift:session:s0124#10（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `param_drift` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `param_skeleton_unseen` | 操作 backtest.run 的参数结构与基线 14 种形态均不同 | `5e4648baee035eec6fe22bfe318a2070` / 356 | 核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条 |

### eval:status_flip:session:s0030#3（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:17.477513+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `349c1856beb55aa152c41a81fcaa34d3` / 89 | 确认是否为新增的失败模式；连续出现应按故障处理 |

### eval:status_flip:session:s0044#6（中）

- 操作人：carol｜开始时间：2026-10-27T06:43:35.729072+00:00
- 操作序列：strategy.list → dataset.list
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `06ae044d367f55f184ac0c067ca79c63` / 128 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `06ae044d367f55f184ac0c067ca79c63` / 128 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0061#1（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:58.004620+00:00
- 操作序列：strategy.list → dataset.list
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `c6596eeb1eed23beaac47509f02bc8e9` / 174 | 确认是否为新增的失败模式；连续出现应按故障处理 |

### eval:status_flip:session:s0061#8（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:58.004620+00:00
- 操作序列：strategy.list → dataset.list
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `c6596eeb1eed23beaac47509f02bc8e9` / 174 | 确认是否为新增的失败模式；连续出现应按故障处理 |

### eval:status_flip:session:s0068#11（中）

- 操作人：carol｜开始时间：2026-10-27T06:44:06.897416+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 dataset.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `d5ab0da2aa476245cce7560bfe1bff8a` / 193 | 确认是否为新增的失败模式；连续出现应按故障处理 |

### eval:status_flip:session:s0117#12（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:18.050273+00:00
- 操作序列：strategy.list → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `aa0e2b85f9e00c0c024d6c541538c3a2` / 335 | 确认是否为新增的失败模式；连续出现应按故障处理 |

### eval:status_flip:session:s0124#7（中）

- 操作人：bob｜开始时间：2026-10-27T06:45:27.865884+00:00
- 操作序列：backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 backtest.compare 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `707b5cd5844fdeed6631058ef975fe40` / 357 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `707b5cd5844fdeed6631058ef975fe40` / 357 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0126#9（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:30.768526+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 backtest.compare 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `d9220448585310b7bd1f380f3ea3d473` / 365 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `d9220448585310b7bd1f380f3ea3d473` / 365 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0138#2（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:46.444793+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `status_unseen` | 操作 strategy.list 出现了基线未见的执行结果 'rejected'（基线：['ok']） | `4187866c9369182f7d48f2b3378fe865` / 394 | 确认是否为新增的失败模式；连续出现应按故障处理 |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `4187866c9369182f7d48f2b3378fe865` / 394 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:swap_order:session:s0013#11（中）

- 操作人：bob｜开始时间：2026-10-27T06:42:54.305138+00:00
- 操作序列：backtest.run → dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `87dbed05060a64391bf00a6a11c055c7` / 40 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0030#1（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:17.928568+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `349c1856beb55aa152c41a81fcaa34d3` / 89 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0042#8（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.904543+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `37c8591069a0f6fb7386765d7c98f8f4` / 122 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0045#3（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:37.160862+00:00
- 操作序列：backtest.run → dataset.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `e6bc30f14a55cff6f8e4cb93946f10a0` / 129 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0045#5（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:37.160862+00:00
- 操作序列：backtest.run → dataset.list → portfolio.backtest
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `e6bc30f14a55cff6f8e4cb93946f10a0` / 129 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0058#10（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:54.288728+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `ba3dd0aab08d525179d42af06b677d7d` / 165 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0058#7（中）

- 操作人：bob｜开始时间：2026-10-27T06:43:54.288728+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `ba3dd0aab08d525179d42af06b677d7d` / 165 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0060#12（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:57.084822+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `9032a387c93d3ed67f691c274563cbea` / 171 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0060#13（中）

- 操作人：alice｜开始时间：2026-10-27T06:43:57.084822+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `9032a387c93d3ed67f691c274563cbea` / 171 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0064#14（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:01.239829+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `436a19e726a152c1033adc8c84864997` / 180 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0076#2（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:18.290478+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `1ee09a4283e3183377b9e3a8cb99346c` / 215 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0076#4（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:18.290478+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `1ee09a4283e3183377b9e3a8cb99346c` / 215 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0076#9（中）

- 操作人：bob｜开始时间：2026-10-27T06:44:18.290478+00:00
- 操作序列：backtest.run → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `1ee09a4283e3183377b9e3a8cb99346c` / 215 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0117#15（中）

- 操作人：alice｜开始时间：2026-10-27T06:45:18.481849+00:00
- 操作序列：dataset.list → strategy.list → backtest.run
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 strategy.list → backtest.run 这一跳转 | `8623923b43180f0a96d5de7c7aaafeea` / 337 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:swap_order:session:s0143#6（中）

- 操作人：carol｜开始时间：2026-10-27T06:45:51.832223+00:00
- 操作序列：backtest.run → dataset.list → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `swap_order` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `step_order_unseen` | 基线中从未出现 backtest.run → dataset.list 这一跳转 | `ac94d85588e824861d12d67da2f0459a` / 403 | 核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线 |

### eval:duration_spike:session:s0003#15（低）

- 操作人：alice｜开始时间：2026-10-27T06:42:39.413424+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 4700ms 超过阈值 755ms（基线 p99=151ms × 5） | `dc517fe3140315428a6e370b30d70bd5` / 11 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0006#14（低）

- 操作人：alice｜开始时间：2026-10-27T06:42:44.220731+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1800ms 超过阈值 100ms（基线 p99=20ms × 5） | `9c71860b83727af8efae19c296f112dd` / 20 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0042#9（低）

- 操作人：alice｜开始时间：2026-10-27T06:43:33.437270+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 5050ms 超过阈值 755ms（基线 p99=151ms × 5） | `b87fd9bdfe276212e4a05ab7b78f945e` / 124 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0043#12（低）

- 操作人：bob｜开始时间：2026-10-27T06:43:34.868429+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1800ms 超过阈值 100ms（基线 p99=20ms × 5） | `43721647eb178ad88262337f8f2b64d5` / 125 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0060#2（低）

- 操作人：alice｜开始时间：2026-10-27T06:43:56.638595+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1800ms 超过阈值 100ms（基线 p99=20ms × 5） | `9032a387c93d3ed67f691c274563cbea` / 171 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0068#5（低）

- 操作人：carol｜开始时间：2026-10-27T06:44:06.897416+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 4550ms 超过阈值 755ms（基线 p99=151ms × 5） | `9d7eb2579e7d7a22e41e4ca277586f33` / 194 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0073#3（低）

- 操作人：bob｜开始时间：2026-10-27T06:44:13.732006+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1850ms 超过阈值 100ms（基线 p99=20ms × 5） | `119c7101ea40a60c14ceeb263c363594` / 207 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0077#6（低）

- 操作人：carol｜开始时间：2026-10-27T06:44:19.244115+00:00
- 操作序列：dataset.list → backtest.run → backtest.run → backtest.run → backtest.compare
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 6300ms 超过阈值 755ms（基线 p99=151ms × 5） | `75c0a46841d77def0617eb5a31b6088a` / 221 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0079#11（低）

- 操作人：bob｜开始时间：2026-10-27T06:44:23.376504+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1800ms 超过阈值 100ms（基线 p99=20ms × 5） | `7e4921fb77176a2ae0deb252c84d3f41` / 226 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0079#13（低）

- 操作人：bob｜开始时间：2026-10-27T06:44:23.376504+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1800ms 超过阈值 100ms（基线 p99=20ms × 5） | `7e4921fb77176a2ae0deb252c84d3f41` / 226 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0080#4（低）

- 操作人：carol｜开始时间：2026-10-27T06:44:24.289786+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 6800ms 超过阈值 755ms（基线 p99=151ms × 5） | `f758983add190e402f2217cbae492c02` / 229 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0108#10（低）

- 操作人：alice｜开始时间：2026-10-27T06:45:05.776234+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1150ms 超过阈值 35ms（基线 p99=7ms × 5） | `f0c2dbc1efbf837a33d38b870001b561` / 311 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0109#1（低）

- 操作人：bob｜开始时间：2026-10-27T06:45:06.658070+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1750ms 超过阈值 100ms（基线 p99=20ms × 5） | `0032530bbdaa8285e37b522fad39af25` / 312 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0117#8（低）

- 操作人：alice｜开始时间：2026-10-27T06:45:18.050273+00:00
- 操作序列：strategy.list → dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 5100ms 超过阈值 755ms（基线 p99=151ms × 5） | `8623923b43180f0a96d5de7c7aaafeea` / 337 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:duration_spike:session:s0138#7（低）

- 操作人：alice｜开始时间：2026-10-27T06:45:46.444793+00:00
- 操作序列：dataset.list → strategy.list
- 评估标记：本次命中来自注入的 `duration_spike` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `duration_outlier` | 耗时 1150ms 超过阈值 35ms（基线 p99=7ms × 5） | `4187866c9369182f7d48f2b3378fe865` / 394 | 检查数据源或引擎是否劣化；结合 T3 延迟档案对照 |

### eval:status_flip:session:s0012#4（低）

- 操作人：alice｜开始时间：2026-10-27T06:42:52.377840+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `3f69d2471c922e69e1c8ba4180a79d39` / 39 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0030#14（低）

- 操作人：alice｜开始时间：2026-10-27T06:43:17.477513+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `db060d1af8e459da69f94cf12b7631b9` / 91 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0066#15（低）

- 操作人：alice｜开始时间：2026-10-27T06:44:03.080183+00:00
- 操作序列：dataset.list → backtest.run → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `52e9c435c34bd58e10d2c91c78d889f3` / 187 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

### eval:status_flip:session:s0128#10（低）

- 操作人：carol｜开始时间：2026-10-27T06:45:34.786835+00:00
- 操作序列：dataset.list → backtest.run
- 评估标记：本次命中来自注入的 `status_flip` 样本（仅供评估）

| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |
| --- | --- | --- | --- |
| `dangling_reject` | 会话最后一步被拒绝，之后没有重试或收尾动作 | `a5b9f193c7bb506b4a5815501908d86d` / 370 | 确认该次作业是否被放弃；放弃应在备注里留下原因 |

## 三、本次停用的规则（未判断 ≠ 正常）

| 规则码 | 停用原因 | 涉及会话数 |
| --- | --- | --- |
| `off_hours` | 基线覆盖小时跨度仅 0 小时（<4），无法定义工作时段 | 167 |
| `duration_outlier` | 操作 portfolio.backtest 基线耗时样本仅 14 条（<20），分位数不可靠 | 25 |

## 四、本次已启用但未命中的规则（判断过、结果正常）

`burst_repeat`、`flow_without_terminal`

这些维度本次没有发现异常，可以当作「检查过」；第三节的规则则是「没检查」。

