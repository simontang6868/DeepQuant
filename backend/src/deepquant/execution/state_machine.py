"""委托状态机与崩溃恢复。

这个模块要回答两个问题，且两个都不许"差不多对"：

1. **一笔委托的状态能不能这么变？**（2.4-b）
2. **机器重启后，什么才算真相？**（2.4-c / 2.4-d）

贯穿全模块的硬规矩
------------------
1. **非法转移抛异常，不静默写字段**。静默写字段的后果不是"状态不太准"，
   而是账实不符却没人知道——对账时只能看到"本地记 FILLED、券商记 PARTIALLY"，
   却查不出是谁在什么时候改的。
2. **终态不可离开**。``FILLED``/``CANCELLED``/``REJECTED`` 之后任何状态变化
   都是 bug（含"改回部分成交"）。唯一例外见下条。
3. ⭐ **``UNKNOWN`` 只能由券商数据解封**，本地怎么推都不算。这是 2.4-c
   "不信本地内存/缓存"的可执行形式：如果本地能自行把 ``UNKNOWN`` 改成
   ``FILLED``，那"以券商为准"就只是文档里的一句话。
4. ⭐⭐ **重建只读**。``rebuild_from_broker`` 拿到的 adapter 被包进
   ``ReadOnlyBroker``，白名单之外的方法（``place_order``/``cancel_order``）
   一调即抛。2.4-d 不靠"我审阅了代码"来满足，靠运行时拦截。

一个刻意的不对称（不是疏漏，见 ``OrderStateMachine.reconcile_with_broker``）
--------------------------------------------------------------------------
推送式回报走 ``apply``，严格按转移表；拉取式重建走 ``reconcile_with_broker``，
**允许状态倒退**。因为本地的"往前推进"可能是错的：虚报成交（本地 FILLED、
券商 PARTIALLY）比漏报更危险——它让仓位看起来更满，可能触发超买。所以重建
必须能把状态改回去，但每一次改回都要留下记录，绝不静默。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from deepquant.broker.models import (
    TERMINAL_STATUSES,
    BrokerOrder,
    BrokerOrderStatus,
    Cash,
    Fill,
    Position,
)

__all__ = [
    "IllegalTransition",
    "ReadOnlyViolation",
    "TRANSITIONS",
    "OrderStateMachine",
    "Override",
    "OrderRegistry",
    "ReadOnlyBroker",
    "READ_ONLY_METHODS",
    "RebuiltState",
    "rebuild_from_broker",
    "apply_rebuild_to_gate_state",
]


# --------------------------------------------------------------------------- 异常


class IllegalTransition(RuntimeError):
    """非法状态转移。

    ⚠️ 必须是**异常**而不是返回值。返回 ``False`` 的写法，调用方一句
    ``ok = sm.apply(...)`` 不看返回值就白做了——本项目在闸门上已经吃过
    "写了警告却仍计入通过"的教训。
    """

    def __init__(self, client_order_id: str, src: BrokerOrderStatus,
                 dst: BrokerOrderStatus, reason: str) -> None:
        self.client_order_id = client_order_id
        self.src = src
        self.dst = dst
        self.reason = reason
        super().__init__(
            f"{client_order_id}: {src.value} → {dst.value} 非法（{reason}）")


class ReadOnlyViolation(RuntimeError):
    """重建期间调用了写操作。"""


# --------------------------------------------------------------------------- 转移表


#: ``src -> 允许到达的 dst``。
#:
#: ⭐ ``PENDING_NEW`` 允许直接跳到 ``PARTIALLY_FILLED``/``FILLED``：部分柜台把
#: 「接受」与「成交」合并成一条回报。**必须允许**——拒绝它等于把一笔真实成交
#: 丢掉，比让它"跳级"危险得多。跳级另记 ``jumped`` 供台账观察。
#:
#: ⭐ 终态映射为空集：不是"忘了填"，是终态不可离开。
TRANSITIONS: dict[BrokerOrderStatus, frozenset[BrokerOrderStatus]] = {
    BrokerOrderStatus.PENDING_NEW: frozenset({
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.UNKNOWN,
    }),
    BrokerOrderStatus.ACCEPTED: frozenset({
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.UNKNOWN,
    }),
    BrokerOrderStatus.PARTIALLY_FILLED: frozenset({
        # 自己到自己：再来一笔部分成交（数量须增加，由 filled_shares 单调性保证）
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.UNKNOWN,
    }),
    BrokerOrderStatus.UNKNOWN: frozenset({
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELLED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.UNKNOWN,          # 再次超时确认仍是未知
    }),
    BrokerOrderStatus.FILLED: frozenset(),
    BrokerOrderStatus.CANCELLED: frozenset(),
    BrokerOrderStatus.REJECTED: frozenset(),
}


#: 允许"跳级"的边。只用于标记，不用于放行判断（放行由 ``TRANSITIONS`` 定）。
_JUMP_EDGES: frozenset[tuple[BrokerOrderStatus, BrokerOrderStatus]] = frozenset({
    (BrokerOrderStatus.PENDING_NEW, BrokerOrderStatus.PARTIALLY_FILLED),
    (BrokerOrderStatus.PENDING_NEW, BrokerOrderStatus.FILLED),
    (BrokerOrderStatus.PENDING_NEW, BrokerOrderStatus.CANCELLED),
    (BrokerOrderStatus.PENDING_NEW, BrokerOrderStatus.REJECTED),
})


# --------------------------------------------------------------------------- 状态机


class OrderStateMachine:
    """单笔委托的状态机。

    只保管一笔委托的当前状态与转移历史。**不**管持仓、不管资金——那些由
    ``rebuild_from_broker`` 与后续对账负责。职责混在一起会让"谁改了持仓"
    变得不可追。
    """

    def __init__(self, order: BrokerOrder) -> None:
        self._order = order
        self._history: list[tuple[BrokerOrderStatus, str]] = [(order.status, "init")]
        self._jumped = False

    # ---------------------------------------------------------------- 只读
    @property
    def order(self) -> BrokerOrder:
        return self._order

    @property
    def status(self) -> BrokerOrderStatus:
        return self._order.status

    @property
    def history(self) -> tuple[tuple[BrokerOrderStatus, str], ...]:
        """``(状态, 触发来源)`` 序列。用于事后追"谁把它改成这样"。"""
        return tuple(self._history)

    @property
    def jumped(self) -> bool:
        """是否发生过跳级（回报合并）。"""
        return self._jumped

    def is_terminal(self) -> bool:
        return self._order.status in TERMINAL_STATUSES

    def is_blocking(self) -> bool:
        """是否应当阻断新单。目前只有 ``UNKNOWN`` 会阻断。"""
        return self._order.status is BrokerOrderStatus.UNKNOWN

    # ---------------------------------------------------------------- 写
    def apply(self, new: BrokerOrder, *, source: str = "local") -> BrokerOrder:
        """应用一条**推送式**回报。严格按转移表。

        ``source`` 只有 ``"local"`` 与 ``"broker"`` 两种。后者仅用于重建
        （见 ``reconcile_with_broker``），这里仍然走严格校验。
        """
        old = self._order
        self._assert_same_identity(old, new)

        # ---- 终态：只允许完全相同的重复回报（幂等），其余一律抛 ----
        if old.status in TERMINAL_STATUSES:
            if new.status is old.status and new.filled_shares == old.filled_shares:
                return old          # 重复回报，无操作
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                f"已处于终态 {old.status.value}，不可再变化")

        # ---- UNKNOWN 只能由券商解封 ----
        if old.status is BrokerOrderStatus.UNKNOWN \
                and new.status is not BrokerOrderStatus.UNKNOWN \
                and source != "broker":
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                "UNKNOWN 只能由券商侧数据解封；本地推断不算数（2.4-c）")

        # ---- 转移表 ----
        allowed = TRANSITIONS[old.status]
        if new.status is not old.status and new.status not in allowed:
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                f"{old.status.value} 的合法后继是 "
                f"{sorted(s.value for s in allowed) or '空（终态）'}")

        # ---- 不变量 ----
        if new.filled_shares < old.filled_shares:
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                f"已成股数不可减少：{old.filled_shares} → {new.filled_shares}")
        if new.status is BrokerOrderStatus.FILLED and new.filled_shares != new.shares:
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                f"报 FILLED 但已成 {new.filled_shares} != 委托 {new.shares}")
        if new.updated_ts < old.updated_ts:
            raise IllegalTransition(
                old.client_order_id, old.status, new.status,
                f"逻辑时间倒退：{old.updated_ts} → {new.updated_ts}")

        if (old.status, new.status) in _JUMP_EDGES:
            self._jumped = True

        self._order = new
        self._history.append((new.status, source))
        return new

    def reconcile_with_broker(self, new: BrokerOrder) -> "Override | None":
        """用券商侧的事实**覆盖**本地状态。允许倒退，但每次覆盖都留记录。

        ⭐ 为什么不复用 ``apply``：``apply`` 假定本地推进是对的，只校验"能不能
        往下走"。而重建的整个前提是**本地可能错了**——可能虚报成交、可能漏记
        撤单。若重建也走转移表，一旦本地先进了终态，券商说的真相就永远进不来，
        账就这么错到期末。

        返回 ``None`` 表示两边一致（无覆盖）；否则返回 ``Override``，调用方
        **必须**把它交给告警/台账，不得丢弃。
        """
        old = self._order
        self._assert_same_identity(old, new)
        if old.status is new.status and old.filled_shares == new.filled_shares \
                and old.reject_code == new.reject_code:
            return None

        ov = Override(
            client_order_id=old.client_order_id,
            field="status/filled_shares",
            local=f"{old.status.value}/{old.filled_shares}",
            broker=f"{new.status.value}/{new.filled_shares}",
            detail=("本地在终态但券商说未终态" if old.status in TERMINAL_STATUSES
                    and new.status not in TERMINAL_STATUSES
                    else "以券商为准覆盖"),
        )
        self._order = new
        self._history.append((new.status, "broker"))
        return ov

    @staticmethod
    def _assert_same_identity(old: BrokerOrder, new: BrokerOrder) -> None:
        """同一委托的身份字段不可变。

        没有这层，``apply`` 可以被用来"缩小委托量"——把 ``shares`` 从 2000
        改成 1000，就成了合法的部分成交，绕开一切校验。
        """
        if old.client_order_id != new.client_order_id:
            raise ValueError(
                f"client_order_id 不可变：{old.client_order_id} → {new.client_order_id}")
        for f in ("code", "side", "shares"):
            if getattr(old, f) != getattr(new, f):
                raise ValueError(
                    f"{old.client_order_id} 的身份字段 {f} 被改动："
                    f"{getattr(old, f)!r} → {getattr(new, f)!r}")


# --------------------------------------------------------------------------- 覆盖记录


@dataclass(frozen=True)
class Override:
    """重建/对账时被券商数据覆盖的一处差异。"""

    client_order_id: str
    field: str
    local: str
    broker: str
    detail: str = ""


# --------------------------------------------------------------------------- 委托集合


class OrderRegistry:
    """一批委托的状态机集合。"""

    def __init__(self, orders: Iterable[BrokerOrder] = ()) -> None:
        self._machines: dict[str, OrderStateMachine] = {}
        for o in orders:
            self._machines[o.client_order_id] = OrderStateMachine(o)

    # ---------------------------------------------------------------- 只读
    def __len__(self) -> int:
        return len(self._machines)

    def __contains__(self, client_order_id: object) -> bool:
        return client_order_id in self._machines

    def get(self, client_order_id: str) -> OrderStateMachine:
        try:
            return self._machines[client_order_id]
        except KeyError:
            raise KeyError(f"未知委托 {client_order_id}") from None

    def orders(self) -> tuple[BrokerOrder, ...]:
        return tuple(sorted((m.order for m in self._machines.values()),
                            key=lambda o: o.client_order_id))

    def unknown_ids(self) -> tuple[str, ...]:
        return tuple(sorted(coid for coid, m in self._machines.items()
                            if m.is_blocking()))

    def blocking(self) -> bool:
        """存在 ``UNKNOWN`` 委托 ⇒ 必须禁止新单（2.4-a）。"""
        return bool(self.unknown_ids())

    # ---------------------------------------------------------------- 写
    def apply(self, order: BrokerOrder, *, source: str = "local") -> BrokerOrder:
        m = self._machines.get(order.client_order_id)
        if m is None:
            # 推送路径上收到未知委托：可能是**别处**下的单（手工/其他终端）。
            # 不能丢，也不能当成新委托乐观接受——先按收到的状态登记，
            # 由对账（2.5）负责把它标成"券商有本地无"。
            self._machines[order.client_order_id] = OrderStateMachine(order)
            return order
        return m.apply(order, source=source)

    def reconcile(self, broker_orders: Sequence[BrokerOrder]) -> tuple[Override, ...]:
        """以券商返回的委托集合为准重建。返回全部覆盖记录。

        ⚠️ 券商返回里**本地没有**的委托会被采纳（``Override(field="adopted")``）。
        这正是缺陷预登记 P3 说的方向：「券商有、本地无」最容易漏——只遍历本地
        去券商侧查的实现，永远看不到这类。
        """
        overrides: list[Override] = []
        seen: set[str] = set()
        for bo in broker_orders:
            seen.add(bo.client_order_id)
            m = self._machines.get(bo.client_order_id)
            if m is None:
                self._machines[bo.client_order_id] = OrderStateMachine(bo)
                overrides.append(Override(
                    client_order_id=bo.client_order_id,
                    field="adopted",
                    local="(缺失)",
                    broker=f"{bo.status.value}/{bo.filled_shares}",
                    detail="券商有、本地无：可能是手工交易或漏录回报",
                ))
                continue
            ov = m.reconcile_with_broker(bo)
            if ov is not None:
                overrides.append(ov)
        # 本地有、券商无：不在这里删除——删了就没法出「本地有券商无」的差异单。
        # 由调用方比对 ``seen`` 与 ``self.unknown_ids()`` 之外的集合。
        return tuple(overrides)

    def missing_at_broker(self, broker_orders: Sequence[BrokerOrder]) -> tuple[str, ...]:
        """本地有、券商没有的委托 id。**只报告，不自动删**。"""
        seen = {o.client_order_id for o in broker_orders}
        return tuple(sorted(set(self._machines) - seen))


# --------------------------------------------------------------------------- 只读守卫


#: 重建期间允许调用的方法。**白名单**，不是黑名单——将来 adapter 新增了写方法
#: （比如 ``place_orders`` 批量下单），黑名单会漏掉它，白名单不会。
READ_ONLY_METHODS: frozenset[str] = frozenset({
    "get_positions",
    "get_cash",
    "get_orders",
    "get_fills",
    "get_quote",
    "get_calendar",
    "snapshot",
})


class ReadOnlyBroker:
    """把 adapter 包成只读视图。

    ⭐ 两道防线而不是一道：``place_order``/``cancel_order`` **显式**定义并抛
    （不依赖 ``__getattr__`` 是否被正确触发），其余方法走白名单转发。
    单靠 ``__getattr__`` 的话，一旦哪天 ``ReadOnlyBroker`` 不小心继承了
    ``BrokerAdapter``，这两个方法会从父类拿到实现，拦截就静默失效了。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.reads: list[str] = []

    # ---- 显式拦截写操作（第一道） ----
    def place_order(self, *args, **kwargs):
        raise ReadOnlyViolation(
            "重建期间禁止下单：2.4-d 要求重建过程只读券商。"
            "若重建需要「补单」，那是规划层的事，不属于恢复")

    def cancel_order(self, *args, **kwargs):
        raise ReadOnlyViolation("重建期间禁止撤单：2.4-d 要求重建过程只读券商。")

    # ---- 白名单转发（第二道） ----
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in READ_ONLY_METHODS:
            raise ReadOnlyViolation(
                f"重建期间禁止调用 {name}()：不在只读白名单内。"
                f"白名单={sorted(READ_ONLY_METHODS)}")
        inner = self._inner
        target = getattr(inner, name)

        def _recorded(*args, **kwargs):
            self.reads.append(name)
            return target(*args, **kwargs)

        return _recorded


# --------------------------------------------------------------------------- 重建


@dataclass(frozen=True)
class RebuiltState:
    """重建结果。**全部字段来自券商**，没有一个来自本地缓存。"""

    positions: tuple[Position, ...]
    cash: Cash
    orders: tuple[BrokerOrder, ...]
    fills: tuple[Fill, ...]
    reads: tuple[str, ...]
    ts: int = 0
    #: 覆盖记录（本地被券商数据改写的每一处）。
    overrides: tuple[Override, ...] = ()

    @property
    def unknown_client_order_ids(self) -> tuple[str, ...]:
        return tuple(sorted(o.client_order_id for o in self.orders
                            if o.status is BrokerOrderStatus.UNKNOWN))

    @property
    def blocked(self) -> bool:
        """有 UNKNOWN 委托 ⇒ 阻断新单。"""
        return bool(self.unknown_client_order_ids)

    def position_map(self) -> dict[str, int]:
        return {p.code: p.quantity for p in self.positions}


def rebuild_from_broker(adapter, *, ts: int = 0,
                        local: OrderRegistry | None = None) -> RebuiltState:
    """崩溃重启后以券商为准重建状态。

    ⚠️ 签名里**没有**持仓/资金的本地入参：本地数据进不来，就不存在"不小心
    用了缓存"这种可能。``local`` 只用于**生成差异记录**（本地有券商无 /
    券商有本地无），不参与重建结果本身。
    """
    ro = ReadOnlyBroker(adapter)
    positions = tuple(sorted(ro.get_positions(), key=lambda p: p.code))
    cash = ro.get_cash()
    orders = tuple(sorted(ro.get_orders(), key=lambda o: o.client_order_id))
    fills = tuple(sorted(ro.get_fills(), key=lambda f: (f.ts, f.fill_id)))

    overrides: tuple[Override, ...] = ()
    if local is not None:
        overrides = local.reconcile(orders)
        gone = local.missing_at_broker(orders)
        if gone:
            overrides = overrides + tuple(
                Override(client_order_id=coid, field="missing_at_broker",
                         local="(有)", broker="(缺失)",
                         detail="本地有、券商无：不得自动删除，须人工确认")
                for coid in gone)

    return RebuiltState(
        positions=positions, cash=cash, orders=orders, fills=fills,
        reads=tuple(ro.reads), ts=ts, overrides=overrides,
    )


def apply_rebuild_to_gate_state(rebuilt: RebuiltState, state):
    """把重建结论写进 ``GateState``。

    ⭐⭐ **只更新** ``has_unknown_orders``，**绝不碰** ``peak_equity`` 与
    ``breaker_tripped``。

    这是缺陷预登记 P5 的复发点：重建时顺手 ``GateState(peak_equity=当前权益)``
    会把峰值降到现在，于是"相对峰值的回撤"被重算成 0，熔断永远不会触发。
    熔断状态同理——重建不是"重新开始"，是"知道现在在哪"。
    """
    state.has_unknown_orders = rebuilt.blocked
    return state
