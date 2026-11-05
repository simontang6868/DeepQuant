"""实盘执行层：订单规划、风控闸门、对账、台账。

分层（不得跨层，见 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §2）::

    Strategy → TargetPortfolio → OrderPlanner → RiskGate → BrokerAdapter
                                                              ↓
                                                  FillReconciler → Ledger

⚠️ 与 ``deepquant.risk.gateway`` 的区别
-------------------------------------
``risk.gateway`` 是 **API 层**的 fail-closed 检查点网关（数据集在不在、字段全不全）。
本包的 ``RiskGate`` 是**交易风控闸门**（授权金额、单票上限、委托笔数、熔断线）。

一个是"这个请求能不能进系统"，一个是"这笔单能不能发到市场"。**不得合并**。
"""

from deepquant.execution.board import (
    BOARD_LABELS,
    Board,
    BoardPermissions,
    board_of,
    require_checked,
)
from deepquant.execution.order_loop import (
    Action,
    LoopAction,
    LoopState,
    reprice_price,
    tick,
)
from deepquant.execution.planner import (
    PlanInput,
    PlanResult,
    PlannedOrder,
    SkippedItem,
    make_client_order_id,
    plan_orders,
)
from deepquant.execution.session import (
    MAX_CARRY_DAYS,
    MAX_REPRICE,
    OrderTracker,
    SessionTimes,
    phase_of,
)

__all__ = [
    "BOARD_LABELS",
    "Board",
    "BoardPermissions",
    "MAX_CARRY_DAYS",
    "MAX_REPRICE",
    "Action",
    "LoopAction",
    "LoopState",
    "OrderTracker",
    "PlanInput",
    "PlanResult",
    "PlannedOrder",
    "SessionTimes",
    "SkippedItem",
    "board_of",
    "make_client_order_id",
    "phase_of",
    "plan_orders",
    "reprice_price",
    "require_checked",
    "tick",
]
