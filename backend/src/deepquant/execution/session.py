"""交易时段与委托生命周期：把验收标准 2.2-c / 2.2-d 变成可执行的时间闸。

为什么需要这个模块
------------------
``planner`` 回答"发哪些单"，``risk_gate`` 回答"能不能发"，``state_machine`` 回答
"券商回报之后是什么状态"。缺的是中间那层：**什么时候发、什么时候能改价、改几次、
什么时候认输**。这一层没有代码时，它就以"人记得每天 10:30 去看一眼"的形式存在——
而人是会忘的，忘了的表现是"那天没成交"，且事后无法归因。

⚠️ 条款里的一处歧义（本模块的选择与理由）
----------------------------------------
- **2.2-c**：「10:30、14:30 各最多重定价一次」——字面像"每日各一次"。
- **2.2-d**：「同一 ``client_order_id`` 的重定价次数上限为 2，超出即失败」——
  按字面是**该 id 的累计上限**，不随日期重置。

两者数值都是 2，但一个是"每日"、一个是"总计"。**本模块取更严格的一侧：总计 2 次、
跨日累计不重置。** 理由：2.2-d 的标题是「不得频繁撤单重报」，它的目的就是压制总次数；
若按"每日 2 次"实现，顺延 10 天最多可撤单重报 20 次，与该条目的相反。

代价（必须写明，不藏）：顺延到第 2 天起，订单价格就**冻结**在原限价上，只能等价格回来。
若业务上要"每天都能重定价"，须由用户书面改成 :data:`MAX_REPRICE_PER_DAY` 口径，
那是一次**口径变更**，不是配置调整——届时历史台账里的 ``reprice_count`` 语义会变。

⚠️ 顺延计数只能用交易日
----------------------
「最多 10 个交易日」不能用自然日近似：国庆/春节前后会把 10 个交易日压成 5 个自然日，
订单被提前判死。本模块要求调用方传入**交易日序列**，内部只认序列下标；
拿不到交易日历就**失败关闭**（``raise``），不按星期几猜。

时间口径
--------
所有函数都接受显式 ``now``（``datetime``），**不读系统时钟**。这不是洁癖：
读系统时钟的函数无法测"午休时段"和"14:57 之后"这两种边界，而它们正是最容易出错的地方。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "MAX_CARRY_DAYS",
    "MAX_REPRICE",
    "Phase",
    "SessionTimes",
    "DEFAULT_TIMES",
    "CloseOutcome",
    "CarryExhausted",
    "RepriceNotAllowed",
    "OrderTracker",
    "phase_of",
    "is_trading_time",
    "reprice_slots_passed",
    "next_trading_day",
]

#: 同一 ``client_order_id`` 的累计重定价上限（验收 2.2-d）。
MAX_REPRICE = 2

#: 顺延上限，单位=**交易日**（验收 2.2-c）。
MAX_CARRY_DAYS = 10


@dataclass(frozen=True)
class SessionTimes:
    """A 股交易时段（北京时间）。默认值即验收 2.2-c 的预登记口径。"""

    #: 连续竞价开始。
    open: dt.time = dt.time(9, 30)
    #: 允许发单的最早时点（避开开盘 5 分钟）。
    first_place: dt.time = dt.time(9, 35)
    #: 上午连续竞价结束。
    am_close: dt.time = dt.time(11, 30)
    #: 下午连续竞价开始。
    pm_open: dt.time = dt.time(13, 0)
    #: 深市收盘集合竞价开始（此后不接受连续竞价单）。
    closing_auction: dt.time = dt.time(14, 57)
    #: 收盘。
    close: dt.time = dt.time(15, 0)
    #: 允许重定价的时点（各最多一次，见模块 docstring 的歧义说明）。
    reprice_slots: tuple[dt.time, ...] = (dt.time(10, 30), dt.time(14, 30))

    def __post_init__(self) -> None:
        if not (self.open <= self.first_place < self.am_close):
            raise ValueError("时段非法：需 open ≤ first_place < am_close")
        if not (self.am_close <= self.pm_open < self.closing_auction < self.close):
            raise ValueError("时段非法：需 am_close ≤ pm_open < closing_auction < close")
        if tuple(sorted(self.reprice_slots)) != self.reprice_slots:
            raise ValueError("reprice_slots 必须按时间升序")
        for t in self.reprice_slots:
            if not (self.first_place <= t < self.closing_auction):
                raise ValueError(f"重定价时点 {t} 不在可发单窗口内")


DEFAULT_TIMES = SessionTimes()


class Phase(str, Enum):
    """某一时刻所处的交易阶段。"""

    PRE_OPEN = "pre_open"                 #: 09:30 前
    PLACE_AM = "place_am"                 #: 09:35–11:30，可发单/可重定价
    LUNCH = "lunch"                       #: 11:30–13:00，不可发单
    PLACE_PM = "place_pm"                 #: 13:00–14:57，可发单/可重定价
    CLOSING_AUCTION = "closing_auction"   #: 14:57–15:00，不可发连续竞价单
    CLOSED = "closed"                     #: 15:00 后


def phase_of(now: dt.datetime, times: SessionTimes = DEFAULT_TIMES) -> Phase:
    """返回 ``now`` 所处阶段。

    09:30–09:35 归入 :attr:`Phase.PRE_OPEN`：虽然已在连续竞价，但策略约定
    09:35 之前不发单（避开开盘 5 分钟），故对发单而言与开盘前同义。
    """
    t = now.time()
    if t < times.first_place:
        return Phase.PRE_OPEN
    if t < times.am_close:
        return Phase.PLACE_AM
    if t < times.pm_open:
        return Phase.LUNCH
    if t < times.closing_auction:
        return Phase.PLACE_PM
    if t < times.close:
        return Phase.CLOSING_AUCTION
    return Phase.CLOSED


def is_trading_time(now: dt.datetime, times: SessionTimes = DEFAULT_TIMES) -> bool:
    """是否处于**可发连续竞价单**的窗口（含重定价）。午休与收盘竞价返回 ``False``。"""
    return phase_of(now, times) in (Phase.PLACE_AM, Phase.PLACE_PM)


def reprice_slots_passed(now: dt.datetime, times: SessionTimes = DEFAULT_TIMES) -> int:
    """当日**已经到达**的重定价时点个数（0/1/2）。仅按时刻统计，不含日累计次数。"""
    t = now.time()
    return sum(1 for s in times.reprice_slots if t >= s)


def next_trading_day(day: str, trading_days: "list[str] | tuple[str, ...]") -> str:
    """返回 ``day`` 之后的下一个交易日。

    失败关闭：``day`` 不在序列里、或是最后一个交易日，都 ``raise``——
    猜一个日期会让顺延计数从此偏移，而偏移在账面上看不出来。
    """
    seq = list(trading_days)
    if day not in seq:
        raise KeyError(f"{day} 不在交易日序列中，无法推算次一交易日（不按自然日猜）")
    i = seq.index(day)
    if i + 1 >= len(seq):
        raise KeyError(f"{day} 是序列最后一个交易日，无法顺延（需先刷新交易日历）")
    return seq[i + 1]


class CarryExhausted(RuntimeError):
    """顺延超过 :data:`MAX_CARRY_DAYS` 个交易日。"""


class RepriceNotAllowed(RuntimeError):
    """重定价请求违反时间窗或次数上限（验收 2.2-c / 2.2-d）。"""


class CloseOutcome(str, Enum):
    """收盘时的处置结果。"""

    FILLED = "filled"        #: 已成交/部分成交后撤余，不再跟踪
    CARRY = "carry"          #: 未成交，记 FAILED 并顺延到次一交易日
    ABANDONED = "abandoned"  #: 顺延期满，彻底放弃（终态）


@dataclass
class OrderTracker:
    """一笔委托（``client_order_id``）从规划到了结的时间账本。

    只记**与时间有关**的事实：何时首次可发、改过几次价、顺延了几天。
    成交数量、成交价不在这里——那些是券商回报的事实，属于 ``state_machine``。

    >>> tr = OrderTracker("DQ-20261130-600000-B-1000", decision_day="20261130")
    >>> tr.place(dt.datetime(2026, 12, 1, 9, 40))
    >>> tr.reprice(dt.datetime(2026, 12, 1, 10, 35))
    >>> tr.reprice_count
    1
    """

    client_order_id: str
    decision_day: str
    #: 首次允许发单的日期（通常 = 决策日的次一交易日）。
    first_day: str | None = None
    #: 实际首次发单时间（None = 尚未发出）。
    placed_at: dt.datetime | None = None
    #: 累计重定价次数（跨日不重置，见模块 docstring）。
    reprice_count: int = 0
    #: 已顺延的交易日数。
    carry_days: int = 0
    #: 已用掉的当日 slot 下标（用 slot 序号而非时刻，便于跨日一致）。
    used_slots: set[int] = field(default_factory=set)
    #: 已顺延到的日期（用于判定是否真的跨了交易日）。
    carried_to: str | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id:
            raise ValueError("client_order_id 不能为空")
        if not (self.decision_day.isdigit() and len(self.decision_day) == 8):
            raise ValueError(f"decision_day 必须是 YYYYMMDD，收到 {self.decision_day!r}")

    # ---- 发单 ----
    def place(self, now: dt.datetime, times: SessionTimes = DEFAULT_TIMES) -> None:
        """登记首次发单。窗口外、或重复发单都 ``raise``。"""
        if self.placed_at is not None:
            raise RuntimeError(f"{self.client_order_id} 已发过单（{self.placed_at}），不得重复登记")
        if not is_trading_time(now, times):
            raise RepriceNotAllowed(
                f"{self.client_order_id} 在 {now:%H:%M}（{phase_of(now, times).value}）不能发单："
                f"允许窗口 {times.first_place:%H:%M}–{times.am_close:%H:%M}、"
                f"{times.pm_open:%H:%M}–{times.closing_auction:%H:%M}")
        self.placed_at = now
        if self.first_day is None:
            self.first_day = now.strftime("%Y%m%d")

    # ---- 重定价 ----
    def due_slots(self, now: dt.datetime, times: SessionTimes = DEFAULT_TIMES) -> tuple[int, ...]:
        """当前**该做但还没做**的重定价 slot 下标。

        规则：只返回已经到达、当日未用过、且总次数未超上限的 slot。
        注意它**不**判断是否跨日——同一天里 10:30 用过、14:30 再用是允许的。
        """
        if self.placed_at is None or now.date() != self.placed_at.date():
            return ()
        if not is_trading_time(now, times):
            return ()
        if self.reprice_count >= MAX_REPRICE:
            return ()
        passed = reprice_slots_passed(now, times)
        return tuple(i for i in range(passed) if i not in self.used_slots)

    def reprice(self, now: dt.datetime, times: SessionTimes = DEFAULT_TIMES,
                slot: int | None = None) -> int:
        """登记一次重定价，返回用掉的 slot 下标。

        ``slot=None`` 时取最早一个到期未用的 slot。
        """
        if self.placed_at is None:
            raise RepriceNotAllowed(f"{self.client_order_id} 尚未发单，不能重定价")
        if self.reprice_count >= MAX_REPRICE:
            # 2.2-d：超出即失败。**这是硬失败，不是"跳过"**——跳过会让账本显示"没超"。
            raise RepriceNotAllowed(
                f"{self.client_order_id} 重定价已达上限 {MAX_REPRICE} 次（2.2-d），不得再改价")
        if now.date() != self.placed_at.date():
            raise RepriceNotAllowed(
                f"{self.client_order_id} 只在发单当日（{self.placed_at:%Y-%m-%d}）可重定价，"
                f"收到 {now:%Y-%m-%d}；跨日重定价属口径变更，须先改预登记")
        if not is_trading_time(now, times):
            raise RepriceNotAllowed(f"{self.client_order_id} 在非交易时段 {now:%H:%M} 不能重定价")
        cand = self.due_slots(now, times)
        if not cand:
            raise RepriceNotAllowed(
                f"{self.client_order_id} 当前无可用重定价时点（已到达 "
                f"{reprice_slots_passed(now, times)} 个，已用 {sorted(self.used_slots)}）")
        use = cand[0] if slot is None else slot
        if use not in cand:
            raise RepriceNotAllowed(f"slot {use} 不可用（可用 {cand}）")
        if use >= len(times.reprice_slots):
            raise RepriceNotAllowed(f"slot {use} 超出时点数 {len(times.reprice_slots)}")
        self.used_slots.add(use)
        self.reprice_count += 1
        return use

    # ---- 收盘处置 ----
    def on_close(self, day: str, *, filled: bool) -> CloseOutcome:
        """收盘清账。

        已成交 → :attr:`CloseOutcome.FILLED`（终态）。
        未成交 → 顺延计数 +1；达 :data:`MAX_CARRY_DAYS` → ``ABANDONED``。

        必须先 ``place`` 过；否则是调用方顺序错误，直接 ``raise``（不静默返回）。
        """
        if self.placed_at is None:
            raise RuntimeError(f"{self.client_order_id} 未发单却收到收盘回报")
        if filled:
            return CloseOutcome.FILLED
        self.carry_days += 1
        if self.carry_days >= MAX_CARRY_DAYS:
            return CloseOutcome.ABANDONED
        return CloseOutcome.CARRY

    def carry_to(self, next_day: str, trading_days: "list[str] | tuple[str, ...]") -> None:
        """把订单顺延到 ``next_day``，并校验它确实是次一交易日。

        校验存在的理由：顺延计数以"交易日"为单位，若把非交易日写进去，
        10 天上限会被自然日稀释——国庆一次能吃掉一半额度。
        """
        if self.first_day is None:
            raise RuntimeError(f"{self.client_order_id} 尚未发单，无法顺延")
        # first_day 始终代表"当前所在的那个交易日"（place 时设为发单日，每次顺延后推进）
        expect = next_trading_day(self.first_day, trading_days)
        if next_day != expect:
            raise ValueError(
                f"顺延目标 {next_day} 不是次一交易日（应为 {expect}）——"
                f"顺延计数以交易日为单位，不接受自然日")
        self.carried_to = next_day
        self.first_day = next_day
        # 跨日：当日 slot 使用权失效（次日重新计算），但累计次数**不重置**。
        self.used_slots.clear()

    # ---- 只读 ----
    @property
    def exhausted(self) -> bool:
        return self.carry_days >= MAX_CARRY_DAYS

    @property
    def reprice_remaining(self) -> int:
        return max(0, MAX_REPRICE - self.reprice_count)

    def to_dict(self) -> dict:
        """落盘用。时间统一 ISO 字符串，``used_slots`` 转有序列表。"""
        return {
            "client_order_id": self.client_order_id,
            "decision_day": self.decision_day,
            "first_day": self.first_day,
            "placed_at": self.placed_at.isoformat() if self.placed_at else None,
            "reprice_count": self.reprice_count,
            "carry_days": self.carry_days,
            "used_slots": sorted(self.used_slots),
            "carried_to": self.carried_to,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OrderTracker":
        placed = d.get("placed_at")
        return cls(
            client_order_id=d["client_order_id"],
            decision_day=d["decision_day"],
            first_day=d.get("first_day"),
            placed_at=dt.datetime.fromisoformat(placed) if placed else None,
            reprice_count=int(d.get("reprice_count", 0)),
            carry_days=int(d.get("carry_days", 0)),
            used_slots=set(d.get("used_slots") or ()),
            carried_to=d.get("carried_to"),
        )
