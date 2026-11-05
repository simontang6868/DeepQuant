# 审查包 · 总索引（**自审**材料，待独立审查）

> **性质更正（R5-1，2026-11-05）**：这 5 份是执行本项目的 agent 自己写的**自审材料**，
> 不是独立审查结论。独立审查须由用户指定的另一个 agent 或人，按
> `docs/REVIEWER_GUIDE_2026-11-05.md` 自行复算，**不得以本包结论为依据**。

**目的**：让外部审查者在不读全部代码的前提下，判断这 5 个模块**是否真的做到了它声称的事**。
每个包给出：源码路径、接口契约、测试清单、**已知缺陷**、变异测试结果、**建议重点质疑的地方**。

**当前状态**：执行层代码完成，`LIVE_ENABLED = false`，**没有任何真实委托**。审查针对的是代码与测试，不是运行记录。

| # | 模块 | 源码 | 测试数 | 变异体 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | RiskGate 硬限闸门 | `backend/src/deepquant/execution/risk_gate.py` | 67 | 9 | 12 条检查固定顺序，任一条不过即拒 |
| 2 | 委托状态机 | `backend/src/deepquant/execution/state_machine.py` | 30 | 11 | 非法转移抛异常；`UNKNOWN` 阻断新单 |
| 3 | `roll_day` 日切 | 同上 `risk_gate.py:239` | 5（专项） | 2 | 跨日只清当日计数，不清券商事实 |
| 4 | SHADOW 比对 | `backend/src/deepquant/execution/shadow.py` | 22 | — | 生成"本应下的单"，差异必须可解释 |
| 5 | 日终对账 | `backend/src/deepquant/execution/reconciler.py` | 30 | — | 四对象 × 双向 × 逐字段 |

**复现命令**（需 `PYTHONPATH=<repo>/backend/src`）：

```bash
pytest tests/broker tests/execution -q                 # 344 passed
pytest tests/execution/test_risk_gate_mutation.py -q   # 9 个变异体，须全绿
pytest tests/execution/test_state_machine_mutation.py -q
```

**共性设计取舍**（五个模块都一样，请重点看这一条是否成立）：

> 宁可**拒绝**，也不猜。缺输入、状态不明、差异无法解释、时钟倒退——全部选择"拒单/报错/停下来"，
> 代价是可用性下降（例如数据源临时抖动会导致当天不出决策），换的是**不会在错误状态上继续作业**。
