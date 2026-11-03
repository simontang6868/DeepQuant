"""券商适配层的数据模型。

为什么单独一层 DTO 而不是复用 ``eventloop.orders``
-------------------------------------------------
``eventloop.orders.Order`` 是**回测撮合**里的订单：它知道 bar、知道队列位置、
知道参与率。而 ``BrokerOrder`` 是**券商告诉我们的事实**：我们只该知道券商给了什么
（委托号、状态、已成数量），不知道也不该猜它内部怎么撮合的。

两者混用会出两类错：
1. 回测里能"改"订单状态（因为是我们自己撮合的），实盘里不能——那由券商决定；
2. 把回测的 ``OrderStatus`` 直接当券商状态用，于是 ``ACCEPTED`` 被当成"一定成交"。

因此本层只做一件事：**忠实表达券商的返回，并对不知道的状态显式留白**。

⚠️ ``BrokerOrderStatus.UNKNOWN`` 不是可选装饰
--------------------------------------------
真实接口一定会出现"我不确定这笔单现在怎样"的时刻：回报丢失、请求超时、进程崩溃。
把它建模成 ``PENDING`` 或 ``ACCEPTED`` 都会诱导下游做出错误决定（再发一笔 = 重复下单）。
``UNKNOWN`` 是**独立状态**，且下游必须对它做特殊处理（禁止发新单）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from deepquant.backtest.models import Fees
from deepquant.market.rules import Side

__all__ = [
    "BrokerOrderStatus",
    "TERMINAL_STATUSES",
    "Position",
    "Cash",
    "BrokerOrder",
    "Fill",
    "Quote",
    "TradingCalendar",
    "AccountSnapshot",
    "OrderRequest",
]


class BrokerOrderStatus(str, Enum):
    """券商侧委托状态。

    与 ``eventloop.orders.OrderStatus`` **不是**同一套枚举，刻意不互相转换：
    回测的 ``PARTIALLY_FILLED`` 由我方撮合模型算出，券商的 ``partially_filled``
    是柜台回报的事实。同名不同源，混用会让"成交数量"出现两个真值。
    """

    PENDING_NEW = "pending_new"      # 已发出，尚未收到券商确认
    ACCEPTED = "accepted"            # 券商已接受，尚未成交
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"              # 回报缺失/超时/崩溃 —— 必须禁止新单


#: 终态。进入终态后**不得**再转移（含被"改回"部分成交这种）。
TERMINAL_STATUSES = frozenset({
    BrokerOrderStatus.FILLED,
    BrokerOrderStatus.CANCELLED,
    BrokerOrderStatus.REJECTED,
})


@dataclass(frozen=True)
class Position:
    """单标的持仓。数量单位是**股**（不是手）。"""

    code: str
    quantity: int = 0
    #: 可卖数量。T+1 下 ``available <= quantity``，差额是当日买入未解冻的部分。
    available: int = 0
    avg_cost: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity < 0 or self.available < 0:
            raise ValueError(f"持仓数量不得为负：{self.code} qty={self.quantity} avail={self.available}")
        if self.available > self.quantity:
            raise ValueError(
                f"可卖数量 {self.available} 大于持仓 {self.quantity}（{self.code}）——"
                "这说明上游把 T+1 解冻写错了")


@dataclass(frozen=True)
class Cash:
    """资金。单位元。"""

    available: float = 0.0      # 可用于买入
    frozen: float = 0.0         # 挂单冻结
    total: float = 0.0          # 含冻结

    def __post_init__(self) -> None:
        if min(self.available, self.frozen, self.total) < 0:
            raise ValueError("资金不得为负")
        # 只做量级合理性检查，不做精确相等：分项来自券商，四舍五入可能差几分钱。
        if abs(self.available + self.frozen - self.total) > 0.01:
            raise ValueError(
                f"资金分项对不上：available {self.available} + frozen {self.frozen} "
                f"!= total {self.total}")


@dataclass(frozen=True)
class OrderRequest:
    """下单请求（我方 → 券商）。

    ``client_order_id`` 是幂等键：**同一决策的同一意图必须得到同一个 id**。
    因此它只能由「决策日 + 代码 + 方向 + 目标股数」这类确定性输入算出，
    **不得包含时间戳、随机数、进程号**——否则重试就是新单（见验收标准缺陷 P2）。
    """

    client_order_id: str
    code: str
    side: Side
    shares: int
    limit_price: float

    def __post_init__(self) -> None:
        if not self.client_order_id:
            raise ValueError("client_order_id 不能为空——它是幂等键")
        if self.shares <= 0:
            raise ValueError(f"委托股数必须为正：{self.shares}")
        if self.limit_price <= 0:
            raise ValueError(f"限价必须为正：{self.limit_price}")


@dataclass(frozen=True)
class BrokerOrder:
    """券商侧的委托事实。"""

    client_order_id: str
    broker_order_id: str | None
    code: str
    side: Side
    shares: int
    limit_price: float
    status: BrokerOrderStatus
    filled_shares: int = 0
    avg_fill_price: float = 0.0
    #: 券商给出的拒单原因（自由文本，仅供人读；机读请用 ``reject_code``）。
    reject_text: str = ""
    #: 机读原因码。空字符串 = 没有原因（正常路径）。
    reject_code: str = ""
    #: 逻辑时间戳（不是 wall clock）。用于断言"时间不得倒退"。
    updated_ts: int = 0

    def __post_init__(self) -> None:
        if self.filled_shares < 0 or self.filled_shares > self.shares:
            raise ValueError(
                f"已成 {self.filled_shares} 超出委托 {self.shares}（{self.client_order_id}）")
        if self.status is BrokerOrderStatus.FILLED and self.filled_shares != self.shares:
            raise ValueError(
                f"状态为 FILLED 但已成 {self.filled_shares} != 委托 {self.shares}——"
                "状态与数量矛盾时以数量为准，不得报 FILLED")


@dataclass(frozen=True)
class Fill:
    """一笔成交回报。

    ``fees`` 复用 ``backtest.models.Fees``，**不在这里另立一套费用结构**：
    验收标准 2.6-b 要求成本只能来自 ``backtest.costs.compute_fees``，
    而它返回的就是那个类型。另立一份会立刻产生两个真源。
    """

    fill_id: str
    client_order_id: str
    code: str
    side: Side
    shares: int
    price: float
    fees: Fees = field(default_factory=Fees)
    ts: int = 0

    def __post_init__(self) -> None:
        if not self.fill_id:
            raise ValueError(
                "fill_id 不能为空——它是『同一笔成交到达两次』的去重键。"
                "没有它就无法区分『重复回报』与『真的又成交了一笔』")


@dataclass(frozen=True)
class Quote:
    """行情快照。"""

    code: str
    last: float
    #: 逻辑时间戳（交易日，YYYYMMDD 的整数形式），用于判陈旧。
    ts: int = 0
    #: 该报价相对请求时刻是否已过期。**由数据源判定，不由调用方猜**。
    stale: bool = False


@dataclass(frozen=True)
class TradingCalendar:
    """交易日历。只回答两个问题，且都只对**已知日期**作答。"""

    #: 已知的交易日集合（升序）。未来日期不在其中——不可知就是不可知。
    days: tuple[int, ...] = ()

    def is_trading_day(self, day: int) -> bool | None:
        """``True``/``False``，或 ``None`` 表示**超出已知范围、无法判断**。

        ⚠️ 返回 ``None`` 而不是 ``False``：把"不知道"当成"不是交易日"会让
        决策在月末最后一天被静默跳过，而把"不知道"当成"是"会发出无法成交的单。
        """
        if not self.days:
            return None
        if day < self.days[0] or day > self.days[-1]:
            return None
        import bisect
        i = bisect.bisect_left(self.days, day)
        return i < len(self.days) and self.days[i] == day

    def next_trading_day(self, day: int) -> int | None:
        """严格晚于 ``day`` 的下一个已知交易日。未知则 ``None``。"""
        if not self.days:
            return None
        import bisect
        i = bisect.bisect_right(self.days, day)
        return self.days[i] if i < len(self.days) else None


@dataclass(frozen=True)
class AccountSnapshot:
    """账户快照（对账的输入）。"""

    cash: Cash = field(default_factory=Cash)
    positions: tuple[Position, ...] = ()
    ts: int = 0

    def position_of(self, code: str) -> Position:
        for p in self.positions:
            if p.code == code:
                return p
        return Position(code=code)
