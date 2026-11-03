"""订单规划：目标持仓 → 差额订单。

本模块只做一件事：**把"想持有什么"翻译成"要发哪些单"**。
它不下单、不查风控、不碰券商——那些分别是 ``RiskGate`` 与 ``BrokerAdapter`` 的事。

三条被刻意做成"结构性"而非"检查"的性质
--------------------------------------
1. **先卖后买**。不是排序习惯，是资金约束：换仓时买入的钱来自卖出回款。
   若先买后卖，同一笔钱会被用两次。
2. **整手是取整规则，不是事后校验**。目标股数在生成时就是 ``lot_size`` 的整数倍，
   不靠"发出去被拒"来兜底。
3. **``client_order_id`` 是确定性派生**。同一「决策日 + 代码 + 方向 + 股数」
   永远得到同一个 id（见验收标准缺陷 P2）。id 里**不含时间戳、随机数、进程号**——
   加了之后，重试就是新单，而单次运行里完全看不出来。

⚠️ 买入预算**不假定**卖出所得可立即使用
--------------------------------------
A 股卖出资金当日可用于买入、当日买入的股票当日不可卖——两件事常被混为一谈。
本模块取保守侧：买单预算 = **下单前可用现金** + 调用方显式声明的
``settled_cash_from_sales``（默认 0）。

于是换仓会变成两步：先卖 → 等成交 → 重新调用本模块生成买单。
这是**有意**的保守，不是缺陷：宁可多跑一轮，也不要发出可能因资金不足被拒的单，
``planner`` 因此必须是幂等、可重入的。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from deepquant.broker.models import AccountSnapshot, Quote
from deepquant.execution.board import BoardPermissions
from deepquant.market.ashare import round_down_to_lot
from deepquant.market.rules import Side

__all__ = [
    "PlannedOrder",
    "SkippedItem",
    "PlanInput",
    "PlanResult",
    "make_client_order_id",
    "plan_orders",
]

#: 单边价格让步上限（任务书 2.2：对手价最多让 0.3%）。
#: 没有盘口数据，只能用最新价 ± 该比例近似"对手价"，这一点必须在台账里写明。
DEFAULT_PRICE_DEVIATION = 0.003


@dataclass(frozen=True)
class PlannedOrder:
    """一笔待提交的委托。**本对象不含任何券商字段**——那是下单后才有的事。"""

    client_order_id: str
    code: str
    side: Side
    shares: int
    limit_price: float
    #: 人读的生成理由（"新建仓" / "加至目标" / "减至目标" / "清仓"）。
    reason: str = ""

    def __post_init__(self) -> None:
        if self.shares <= 0:
            raise ValueError(f"{self.code} 委托股数必须为正，收到 {self.shares}")
        if self.limit_price <= 0 or not math.isfinite(self.limit_price):
            raise ValueError(f"{self.code} 限价非法：{self.limit_price}")


@dataclass(frozen=True)
class SkippedItem:
    """跳过的标的。**必须带原因**——静默跳过会让"该买没买"永远无法归因。"""

    code: str
    side: Side | None
    reason: str


@dataclass(frozen=True)
class PlanInput:
    """规划输入。全部显式，无默认魔数。"""

    decision: str                              # 决策日 YYYYMMDD（确定性，不是运行时刻）
    targets: Sequence[str]                     # 目标持仓，顺序即买入优先级
    snapshot: AccountSnapshot                  # 策略仓的持仓与现金
    quotes: Mapping[str, Quote]                # 代码 → 行情
    target_weight: float                       # 每只目标权重
    lot_size: int = 100
    price_deviation: float = DEFAULT_PRICE_DEVIATION
    #: 显式声明"已成交卖出回款中可用于买入的部分"。默认 0 = 不假定。
    settled_cash_from_sales: float = 0.0
    #: 涨跌停价（代码 → (跌停, 涨停)）。缺失即不 clamp，但会在结果里记一条提示。
    price_limits: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    #: 账户板块交易权限（创业板/科创板等）。**只拦买入**，卖出不受限。
    #: None = 未检查（兼容旧调用），但 ``PlanResult.permission_unchecked`` 会是 True，
    #: 发单入口必须断言它为 False（见 board.require_checked）。
    permissions: BoardPermissions | None = None

    def __post_init__(self) -> None:
        if not self.decision.isdigit() or len(self.decision) != 8:
            raise ValueError(f"decision 必须是 YYYYMMDD，收到 {self.decision!r}")
        if not (0.0 < self.target_weight <= 1.0):
            raise ValueError(f"target_weight 必须在 (0,1]，收到 {self.target_weight}")
        if self.settled_cash_from_sales < 0:
            raise ValueError("settled_cash_from_sales 不得为负")


@dataclass(frozen=True)
class PlanResult:
    orders: tuple[PlannedOrder, ...]
    skipped: tuple[SkippedItem, ...]
    #: 规划时用的策略仓总权益（现金 + 持仓市值）。
    equity: float = 0.0
    #: True = 规划时没传板块权限（permissions=None）⇒ 发单入口必须拦截。
    #: 见 deepquant.execution.board.require_checked。
    permission_unchecked: bool = True

    @property
    def sells(self) -> tuple[PlannedOrder, ...]:
        return tuple(o for o in self.orders if o.side is Side.SELL)

    @property
    def buys(self) -> tuple[PlannedOrder, ...]:
        return tuple(o for o in self.orders if o.side is Side.BUY)


def make_client_order_id(decision: str, code: str, side: Side, shares: int,
                         revision: int = 0) -> str:
    """幂等键。**纯函数，不读时钟**。

    ``revision`` 用于重定价：撤旧单 + 发新单在业务上是**同一意图的第二次尝试**，
    所以保留同一前缀（可追溯），但 id 必须不同（券商侧是两笔委托）。

    >>> make_client_order_id("20261030", "600990", Side.BUY, 200)
    '20261030-600990-B-200'
    >>> make_client_order_id("20261030", "600990", Side.BUY, 200, revision=1)
    '20261030-600990-B-200-r1'
    """
    if not decision.isdigit() or len(decision) != 8:
        raise ValueError(f"decision 必须是 YYYYMMDD，收到 {decision!r}")
    if not code or not code.isdigit() or len(code) != 6:
        raise ValueError(f"code 必须是 6 位数字，收到 {code!r}")
    if shares <= 0:
        raise ValueError(f"shares 必须为正，收到 {shares}")
    if revision < 0:
        raise ValueError(f"revision 不得为负，收到 {revision}")
    flag = "B" if side is Side.BUY else "S"
    base = f"{decision}-{code}-{flag}-{shares}"
    return base if revision == 0 else f"{base}-r{revision}"


def _limit_price(quote: Quote, side: Side, deviation: float,
                 limits: tuple[float, float] | None) -> float:
    """限价：买入让价、卖出让价，再被涨跌停夹住。

    ⚠️ 我们没有盘口，"对手价"用最新价 ± 让步比例近似。这不是精确的对手价，
    必须在台账里注明，Phase 3 用真实成交价校准。
    """
    raw = quote.last * (1.0 + deviation) if side is Side.BUY else quote.last * (1.0 - deviation)
    if limits is not None:
        down, up = limits
        raw = min(max(raw, down), up)
    return round(raw, 2)


def plan_orders(inp: PlanInput) -> PlanResult:
    """生成差额订单。卖单在前、买单在后。幂等可重入。"""
    skipped: list[SkippedItem] = []

    # ---- 1. 总权益：现金 + 持仓市值 ----
    # 价格缺失的持仓不能按 0 计市值（会让权益凭空缩水、目标股数全部变小），
    # 直接抛错由调用方处理。
    equity = inp.snapshot.cash.total
    for p in inp.snapshot.positions:
        if p.quantity == 0:
            continue
        q = inp.quotes.get(p.code)
        if q is None or q.stale or q.last <= 0:
            raise LookupError(
                f"持仓 {p.code} 缺有效行情，无法计算权益。"
                "不得用 0 或成本价顶替——那会让所有目标股数系统性偏小。")
        equity += p.quantity * q.last

    held = {p.code: p for p in inp.snapshot.positions}
    targets = list(dict.fromkeys(inp.targets))       # 去重且保序

    # ---- 2. 目标股数 ----
    # ⚠️ 权限闸在"算预算"**之前**：无权限的标的不得占用预算，也不得因为
    #    "反正会跳过"而少算权益——权益是全部持仓的市值，与能不能买无关。
    #    拦下的标的进 skipped（带板块名），后续标的按原优先级**不补位**：
    #    补位 = 改了组合，那是策略变更，不是执行细节。
    target_shares: dict[str, int] = {}
    for code in targets:
        if inp.permissions is not None and not inp.permissions.allows(code):
            skipped.append(SkippedItem(code, Side.BUY,
                                       inp.permissions.reason_if_denied(code)))
            continue
        q = inp.quotes.get(code)
        if q is None or q.stale or q.last <= 0:
            skipped.append(SkippedItem(code, Side.BUY,
                                       "无有效行情（停牌/陈旧/缺失），无法定价"))
            continue
        budget = equity * inp.target_weight
        n = round_down_to_lot(budget / q.last, inp.lot_size)
        if n <= 0:
            skipped.append(SkippedItem(code, Side.BUY,
                                       f"单只预算 {budget:.2f} 元不足一手"
                                       f"（{q.last:.2f}×{inp.lot_size}）"))
            continue
        target_shares[code] = n

    # ---- 3. 卖单（先） ----
    sells: list[PlannedOrder] = []
    for code, p in sorted(held.items()):
        want = target_shares.get(code, 0)
        if p.quantity <= want:
            continue                      # 不需要减，先判这个再判能不能卖
        qty = min(p.available, p.quantity - want)
        qty = round_down_to_lot(qty, inp.lot_size)
        if qty <= 0:
            # ⚠️ 必须记 skipped：这是"该减没减"。T+1 冻结、不足一手都会走到这里，
            #    静默跳过会让次日持仓与目标不符且无法归因。
            skipped.append(SkippedItem(
                code, Side.SELL,
                f"应减 {p.quantity - want} 股，可卖仅 {p.available} 股"
                f"（T+1 冻结或不足一手）"))
            continue
        q = inp.quotes.get(code)
        if q is None or q.stale or q.last <= 0:
            skipped.append(SkippedItem(code, Side.SELL, "无有效行情，无法定价"))
            continue
        reason = "清仓" if code not in target_shares else "减至目标"
        sells.append(PlannedOrder(
            client_order_id=make_client_order_id(inp.decision, code, Side.SELL, qty),
            code=code, side=Side.SELL, shares=qty,
            limit_price=_limit_price(q, Side.SELL, inp.price_deviation,
                                     inp.price_limits.get(code)),
            reason=reason,
        ))
    # 大单优先：先回收更多现金
    sells.sort(key=lambda o: (-o.shares * o.limit_price, o.code))

    # ---- 4. 买单（后）：预算只用「下单前可用现金 + 显式声明的回款」 ----
    budget = inp.snapshot.cash.available + inp.settled_cash_from_sales
    buys: list[PlannedOrder] = []
    for code in targets:                              # 顺序 = 优先级
        want = target_shares.get(code)
        if want is None:
            continue
        cur = held.get(code).quantity if code in held else 0
        delta = want - cur
        if delta <= 0:
            continue
        delta = round_down_to_lot(delta, inp.lot_size)
        if delta <= 0:
            continue
        q = inp.quotes[code]
        # ⚠️ 这里只按**成交金额**卡现金，不含费用。费用由券商侧校验（MockBroker 会算
        # 佣金/印花/过户并可能因此拒单），planner 不重复实现一遍费率——
        # 本项目在手写费率上错过四次，宁可让券商拒，也不在这里多一份公式。
        cost = delta * _limit_price(q, Side.BUY, inp.price_deviation,
                                    inp.price_limits.get(code))
        if cost > budget + 1e-9:
            affordable = round_down_to_lot(budget / q.last, inp.lot_size)
            if affordable <= 0:
                skipped.append(SkippedItem(
                    code, Side.BUY,
                    f"需买 {delta} 股约 {cost:.2f} 元，可用 {budget:.2f} 元不足一手"))
                continue
            delta = affordable
            cost = delta * _limit_price(q, Side.BUY, inp.price_deviation,
                                        inp.price_limits.get(code))
        budget -= cost
        reason = "新建仓" if cur == 0 else "加至目标"
        buys.append(PlannedOrder(
            client_order_id=make_client_order_id(inp.decision, code, Side.BUY, delta),
            code=code, side=Side.BUY, shares=delta,
            limit_price=_limit_price(q, Side.BUY, inp.price_deviation,
                                     inp.price_limits.get(code)),
            reason=reason,
        ))

    return PlanResult(orders=tuple(sells) + tuple(buys),
                      skipped=tuple(skipped), equity=equity,
                      permission_unchecked=inp.permissions is None)
