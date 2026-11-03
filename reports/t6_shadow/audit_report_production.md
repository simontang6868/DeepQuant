# 影子复盘异常审计报告（生产形态：全库扫描）

- 生成时间：2026-10-27T06:53:18.053963+00:00
- 基线版本：t6-baseline-v1（摘要 `287c03334a2810d9ed2301d51b87c132b4eb5346468d1d3b380a4d67659ee9b4`）
- 扫描会话：150，标记会话：0
- 标记率（标记会话 / 扫描会话）：0.0%　——这是「有多少会话被标记」，不是准确率，也不代表真实异常率

## 一、命中规则统计

本次没有命中任何规则。**这不等于没有问题**：先看第三节的停用规则，被停用的维度本次没有能力判断。

## 二、逐条明细（按严重度排序）

（无命中）

## 三、本次停用的规则（未判断 ≠ 正常）

| 规则码 | 停用原因 | 涉及会话数 |
| --- | --- | --- |
| `off_hours` | 基线覆盖小时跨度仅 0 小时（<4），无法定义工作时段 | 150 |
| `duration_outlier` | 操作 portfolio.backtest 基线耗时样本仅 14 条（<20），分位数不可靠 | 18 |

## 四、本次已启用但未命中的规则（判断过、结果正常）

`burst_repeat`、`dangling_reject`、`flow_without_terminal`、`operator_unknown`、`param_skeleton_unseen`、`prerequisite_missing`、`reject_storm`、`session_multi_operator`、`status_unseen`、`step_order_unseen`

这些维度本次没有发现异常，可以当作「检查过」；第三节的规则则是「没检查」。

