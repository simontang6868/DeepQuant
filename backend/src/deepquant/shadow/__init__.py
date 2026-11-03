"""影子复盘（T6）：从 T1 工件库抽取操作序列、建质检基线、自动出异常报告。

为什么不是「重新记一份业务日志」
--------------------------------
T6 的目标是「从历史操作日志中提取共性规则」。本仓库已经在 T1 落了
append-only、带哈希链的工件库，里面有操作人、发生时间、依据规则、执行结果
与注意事项五元组。再记一份「给复盘用的日志」等于制造第二个真相来源：
两份日志一旦不一致，没人能判断哪份是对的。所以本包**只消费** T1 工件，
不新增写入路径，也不改工件结构。

模块分工
--------
- :mod:`deepquant.shadow.sequences`：卡片 → 操作序列（切分口径、参数骨架归一）；
- :mod:`deepquant.shadow.baseline`：质检基线（频率 / 顺序 / 条件模式 / 时长剖面）；
- :mod:`deepquant.shadow.rules`：异常判定规则、证据定位、报告结构。

三条硬约束（写进实现，不靠纪律）
--------------------------------
1. **基线只用真实卡片**：本包不提供「往工件库里写合成卡片」的入口。评估用的
   异常注入发生在序列层（见 ``inject``），因为改写卡片会破坏哈希链——破坏链
   本身是篡改，不是业务异常。
2. **基线不足时规则必须显式停用**：样本量不够的维度直接跳过并登记到
   ``skipped_rules``，不允许用「0 次即正常」冒充结论（见 ``rules`` 模块说明）。
3. **报告必须可定位**：每条命中带 ``card_id`` 与链上 ``seq``，质检人员能按图索骥
   回到原始工件，而不是只看到一句「序列异常」。
"""

from __future__ import annotations

from deepquant.shadow.baseline import (
    BASELINE_VERSION,
    baseline_digest,
    build_baseline,
    load_baseline,
    save_baseline,
)
from deepquant.shadow.rules import (
    RULE_CATALOG,
    AnomalyHit,
    RuleSkipped,
    SessionFinding,
    audit_sequences,
    render_markdown,
)
from deepquant.shadow.sequences import (
    MISSING_SESSION_PREFIX,
    OperationSequence,
    Step,
    load_sequences,
    param_skeleton,
    sequences_from_cards,
)

__all__ = [
    "BASELINE_VERSION",
    "MISSING_SESSION_PREFIX",
    "RULE_CATALOG",
    "AnomalyHit",
    "OperationSequence",
    "RuleSkipped",
    "SessionFinding",
    "Step",
    "audit_sequences",
    "baseline_digest",
    "build_baseline",
    "load_baseline",
    "load_sequences",
    "param_skeleton",
    "render_markdown",
    "save_baseline",
    "sequences_from_cards",
]
