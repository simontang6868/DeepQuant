"""券商适配层的异常与原因码。

为什么要机读原因码，而不是让券商返回一段话
------------------------------------------
拒单原因要能被**统计**：本阶段要回答「失败原因分类」，Phase 3 要给出
「失败原因分布（中位/p90）」。如果原因只以自由文本存在，每次券商改一句措辞，
分类就断一次。

所以每个拒绝都带 ``RejectCode``；自由文本只作为人工排查的补充，**不参与判定**。

⚠️ ``MISSING_INPUT`` 是刻意存在的
--------------------------------
本项目在闸门上反复犯同一个错：「拿不到数据 ⇒ 跳过检查 ⇒ 当作通过」。
``MISSING_INPUT`` 把这件事变成一次**显式拒绝**，而不是一次静默放行。
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "BrokerError",
    "RejectCode",
    "OrderRejected",
    "BrokerTimeout",
    "UnknownOrderState",
    "NotConnected",
    "DuplicateOrder",
]


class BrokerError(RuntimeError):
    """券商适配层的所有异常的基类。"""


class RejectCode(str, Enum):
    """拒单原因码（机读）。"""

    # ---- 市场规则类 ----
    PRICE_CAGE = "price_cage"                # 超出价格笼子（创业板/科创板 2%/0.1 元）
    LIMIT_UP = "limit_up"                    # 涨停买不进
    LIMIT_DOWN = "limit_down"                # 跌停卖不出
    INVALID_LOT = "invalid_lot"              # 非整手 / 低于最小申报单位
    SUSPENDED = "suspended"                  # 停牌

    # ---- 资金与权限类 ----
    INSUFFICIENT_CASH = "insufficient_cash"
    INSUFFICIENT_POSITION = "insufficient_position"
    BLACKLIST = "blacklist"
    NOT_AUTHORIZED = "not_authorized"        # 超出授权文件范围（金额/板块/有效期）
    NO_AUTHORIZATION = "no_authorization"    # 压根没有授权文件

    # ---- 环境类 ----
    NOT_TRADING_HOURS = "not_trading_hours"
    STALE_QUOTE = "stale_quote"
    BROKER_UNAVAILABLE = "broker_unavailable"

    # ---- 风控闸门类 ----
    POSITION_LIMIT = "position_limit"        # 单票上限
    ORDER_NOTIONAL_LIMIT = "order_notional_limit"   # 单笔金额上限
    DAILY_ORDER_LIMIT = "daily_order_limit"  # 单日委托笔数上限
    PRICE_DEVIATION = "price_deviation"      # 委托价偏离最新价超阈值
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    CIRCUIT_BREAKER = "circuit_breaker"      # 熔断线
    HALT_SWITCH = "halt_switch"              # 停机开关
    UNKNOWN_ORDER_PENDING = "unknown_order_pending"  # 存在 UNKNOWN 委托

    # ---- 缺失输入（不得视为通过）----
    MISSING_INPUT = "missing_input"

    # ---- 兜底 ----
    OTHER = "other"


class OrderRejected(BrokerError):
    """委托被拒。**必须**带原因码。

    ``order`` 可选：券商往往同时"留下一条废单记录"并拒绝本次请求。
    保留它便于对账时解释"为什么本地有一笔 REJECTED"。
    """

    def __init__(self, code: RejectCode, detail: str = "", order: object | None = None) -> None:
        if not isinstance(code, RejectCode):
            raise TypeError(f"拒单原因必须是 RejectCode，收到 {type(code).__name__}")
        self.code = code
        self.detail = detail
        self.order = order
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


class BrokerTimeout(BrokerError):
    """请求超时。**后果是不确定，不是失败**——必须落到 ``UNKNOWN`` 状态。"""


class UnknownOrderState(BrokerError):
    """存在状态未知的委托，禁止继续发新单。"""


class NotConnected(BrokerError):
    """适配器未就绪（断网、未登录、未初始化）。"""


class DuplicateOrder(BrokerError):
    """同一 ``client_order_id`` 已存在且内容不同。

    ⚠️ 幂等键**相同但内容不同**是真正的事故（同一 id 代表两个意图），
    重放相同内容则应当返回既有委托、不报错。
    """
