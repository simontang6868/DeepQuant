"""bar 内撮合：价格可及性判定、参与率额度、排队位置、部分成交。

能力边界（必须与任何引用一起出现）
----------------------------------
本仓库只有日线数据。一根 bar 能告诉我们的只有四件事：开盘价、最高价、最低价、收盘价
与当日总成交量。因此本模块的"撮合"是**参数化模型**，不是对真实撮合的复现：

- 不知道订单簿，所以"队列位置"是一个 0~1 的参数，不是观测值；
- 不知道逐笔，所以"部分成交"由参与率额度决定，不是排队消耗得到的；
- 不知道盘中路径，所以限价单只判"价格是否被 bar 的区间覆盖"，
  不判"先到高点还是先到低点"。

三条由此而来的**保守**约定：
1. 触价成交一律按**限价**成交（不用 bar 的更优价），避免把"当天最低价"当成自己能拿到；
2. 队列位置 > 0 时按比例压缩可成交量，方向永远对交易者不利；
3. 涨跌停只判"开盘价是否已被封死"，不推断盘中是否会打开——封死即不可成交。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from deepquant.eventloop.impact import SlippageModel
from deepquant.eventloop.orders import Order, OrderType
from deepquant.market.ashare import round_down_to_lot, round_to_tick
from deepquant.market.rules import Side

PRICE_TICK = 0.01

# ---------------- 未成交/拒单原因码 ---------------- #
NO_TOUCH = "no_touch"  # 限价未被 bar 区间覆盖
SUSPENDED = "suspended"  # 停牌或无有效行情
LIMIT_UP = "limit_up"  # 买入：开盘价触及涨停价
LIMIT_DOWN = "limit_down"  # 卖出：开盘价触及跌停价
LIQUIDITY_CAP = "liquidity_cap"  # 参与率额度不足（不足一手）
QUEUE_EXHAUSTED = "queue_exhausted"  # 队列位置吃掉了全部额度
INVALID_PRICE = "invalid_price"  # bar 价格非法

BLOCKING_REASONS = frozenset({SUSPENDED, LIMIT_UP, LIMIT_DOWN, INVALID_PRICE})


class MatchingError(ValueError):
    """撮合输入非法（bar 自身矛盾、参数越界）。"""


@dataclass(frozen=True)
class Bar:
    """单个交易日的行情。``volume`` 单位为**股**（不是手）。

    ``limit_up`` / ``limit_down`` 为空表示"未提供涨跌停价"——
    此时不做涨跌停判定，并在结果里显式记录 ``limit_check="not_available"``，
    绝不能因为"没提供"就当成"没有涨跌停"。
    """

    day: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    limit_up: float | None = None
    limit_down: float | None = None
    #: 尺度无关的封板标记（由交易层不复权数据算出）。给了标记就优先用它，
    #: 因为策略价格可能是复权价，用复权价算涨跌停价是错的。
    limit_up_hit: bool | None = None
    limit_down_hit: bool | None = None
    tradable: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise MatchingError(f"{name} 必须是数值，收到 {value!r}")
            if not math.isfinite(value) or value <= 0:
                raise MatchingError(f"{name} 必须是有限正数，收到 {value!r}")
        if not math.isfinite(self.volume) or self.volume < 0:
            raise MatchingError(f"volume 必须是有限非负数（股），收到 {self.volume!r}")
        if self.high < max(self.open, self.close) - PRICE_TICK / 2:
            raise MatchingError(
                f"bar 自相矛盾：high={self.high} 小于 max(open,close)={max(self.open, self.close)}"
            )
        if self.low > min(self.open, self.close) + PRICE_TICK / 2:
            raise MatchingError(
                f"bar 自相矛盾：low={self.low} 大于 min(open,close)={min(self.open, self.close)}"
            )
        if self.limit_up is not None and self.limit_down is not None and self.limit_up < self.limit_down:
            raise MatchingError(f"涨停价 {self.limit_up} 低于跌停价 {self.limit_down}")

    @property
    def has_limits(self) -> bool:
        """是否具备涨跌停判定依据（价格或布尔标记任一即可）。"""
        by_price = self.limit_up is not None and self.limit_down is not None
        by_flag = self.limit_up_hit is not None or self.limit_down_hit is not None
        return by_price or by_flag


@dataclass(frozen=True)
class MatchingParams:
    """成交模型参数。全部为**模型假设**，非实测标定值。"""

    #: 单日最多吃掉当日成交量的比例（所有订单共享这一份额度）。
    participation_limit: float = 0.10
    #: 队列位置：0 = 队首（触价即拿满额度），1 = 队尾（触价也拿不到）。
    queue_position: float = 0.5
    #: 是否允许部分成交。False 时"不够就是全部不成交"。
    allow_partial: bool = True
    #: 最小成交单位（股），A 股买入为 100。
    min_lot: int = 100

    def __post_init__(self) -> None:
        if not (0.0 < self.participation_limit <= 1.0):
            raise MatchingError(f"participation_limit 必须在 (0,1]，收到 {self.participation_limit}")
        if not (0.0 <= self.queue_position <= 1.0):
            raise MatchingError(f"queue_position 必须在 [0,1]，收到 {self.queue_position}")
        if not isinstance(self.min_lot, int) or self.min_lot <= 0:
            raise MatchingError(f"min_lot 必须是正整数，收到 {self.min_lot!r}")


@dataclass
class LiquidityLedger:
    """当日（或单个 bar）的流动性额度，多订单共享。"""

    capacity: float
    consumed: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.capacity - self.consumed)

    def consume(self, amount: float) -> None:
        if amount < 0:
            raise MatchingError(f"消耗额度不能为负：{amount}")
        self.consumed += amount


@dataclass
class MatchResult:
    """一次撮合尝试的结果。``reason == ""`` 表示至少部分成交。"""

    filled_quantity: int = 0
    fill_price: float | None = None
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def filled(self) -> bool:
        return self.filled_quantity > 0

    @property
    def blocked(self) -> bool:
        """是否属于"根本不允许成交"（而非"这次没轮到"）。"""
        return self.reason in BLOCKING_REASONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


def _limit_block_reason(order: Order, bar: Bar, at_open_price: float) -> str:
    """涨跌停判定：开盘价已被封死则不可成交（保守，不推断盘中是否打开）。

    优先用布尔标记（来自交易层不复权数据）；没有标记时才退回价格比较。
    两者都没有时**不判定**，由调用方在 limitations 里声明"未执行涨跌停判定"。
    """
    if order.side is Side.BUY:
        if bar.limit_up_hit is True:
            return LIMIT_UP
        if bar.limit_up_hit is None and bar.limit_up is not None:
            if at_open_price >= bar.limit_up - PRICE_TICK / 2:
                return LIMIT_UP
    else:
        if bar.limit_down_hit is True:
            return LIMIT_DOWN
        if bar.limit_down_hit is None and bar.limit_down is not None:
            if at_open_price <= bar.limit_down + PRICE_TICK / 2:
                return LIMIT_DOWN
    return ""


def _fillable_quantity(
    order: Order,
    ledger: LiquidityLedger,
    params: MatchingParams,
    *,
    touch: bool,
) -> tuple[int, str, dict[str, Any]]:
    """在额度与整手约束下，算出这一单最多能成交多少股。"""
    left = ledger.remaining
    if left <= 0:
        return 0, LIQUIDITY_CAP, {"capacity": ledger.capacity, "remaining": 0.0}
    if touch:
        # 触价但未穿越：队列里排在前面的量先吃额度，只有剩下的轮到我们
        left_after_queue = left * (1.0 - params.queue_position)
        if left_after_queue <= 0:
            return 0, QUEUE_EXHAUSTED, {
                "capacity": ledger.capacity,
                "remaining": left,
                "queue_position": params.queue_position,
            }
        left = left_after_queue
    want = order.remaining
    if not params.allow_partial:
        if left < want:
            return 0, LIQUIDITY_CAP, {"capacity": ledger.capacity, "remaining": left, "wanted": want}
        quantity = want
    else:
        quantity = round_down_to_lot(min(want, left), params.min_lot)
    if quantity < params.min_lot:
        return 0, LIQUIDITY_CAP, {
            "capacity": ledger.capacity,
            "remaining": left,
            "wanted": want,
            "lot_capped": quantity,
        }
    return quantity, "", {"capacity": ledger.capacity, "remaining": left, "touch": touch}


def match_on_open(
    order: Order,
    bar: Bar,
    params: MatchingParams,
    ledger: LiquidityLedger,
    slippage: SlippageModel,
) -> MatchResult:
    """开盘撮合：市价单必到；限价单只有在开盘价已优于限价时才成交。"""
    if not bar.tradable:
        return MatchResult(reason=SUSPENDED, detail={"note": bar.note or "tradable=False"})

    reference = bar.open
    touch = False
    if order.order_type is OrderType.LIMIT:
        assert order.limit_price is not None
        if order.side is Side.BUY:
            if reference > order.limit_price:
                return MatchResult(
                    reason=NO_TOUCH,
                    detail={"reference": reference, "limit": order.limit_price, "stage": "open"},
                )
        else:
            if reference < order.limit_price:
                return MatchResult(
                    reason=NO_TOUCH,
                    detail={"reference": reference, "limit": order.limit_price, "stage": "open"},
                )

    blocked = _limit_block_reason(order, bar, reference)
    if blocked:
        return MatchResult(
            reason=blocked,
            detail={
                "reference": reference,
                "limit_up": bar.limit_up,
                "limit_down": bar.limit_down,
                "limit_check": "applied",
            },
        )

    quantity, reason, detail = _fillable_quantity(order, ledger, params, touch=touch)
    if quantity == 0:
        detail.update({"stage": "open", "limit_check": "applied" if bar.has_limits else "not_available"})
        return MatchResult(reason=reason, detail=detail)

    price, slip_detail = slippage.apply(reference, order.side, quantity)
    ledger.consume(quantity)
    return MatchResult(
        filled_quantity=quantity,
        fill_price=price,
        detail={
            "stage": "open",
            "touch": touch,
            "slippage": slip_detail,
            "limit_check": "applied" if bar.has_limits else "not_available",
            **detail,
        },
    )


def match_on_close(
    order: Order,
    bar: Bar,
    params: MatchingParams,
    ledger: LiquidityLedger,
    slippage: SlippageModel,
) -> MatchResult:
    """收盘撮合：只处理"盘中触价"的限价单，成交价按限价（保守）。"""
    if order.order_type is not OrderType.LIMIT:
        # 市价单在开盘未成交（额度不足）后不在收盘二次尝试：
        # 日线模型下"当天没成交"就是没成交，重复尝试会凭空制造流动性。
        return MatchResult(reason=NO_TOUCH, detail={"stage": "close", "note": "market_order_not_retried"})
    if not bar.tradable:
        return MatchResult(reason=SUSPENDED, detail={"note": bar.note or "tradable=False"})

    assert order.limit_price is not None
    if order.side is Side.BUY:
        if bar.low > order.limit_price:
            return MatchResult(
                reason=NO_TOUCH,
                detail={"reference": bar.low, "limit": order.limit_price, "stage": "close"},
            )
    else:
        if bar.high < order.limit_price:
            return MatchResult(
                reason=NO_TOUCH,
                detail={"reference": bar.high, "limit": order.limit_price, "stage": "close"},
            )

    quantity, reason, detail = _fillable_quantity(order, ledger, params, touch=True)
    if quantity == 0:
        detail.update({"stage": "close"})
        return MatchResult(reason=reason, detail=detail)

    price, slip_detail = slippage.apply(order.limit_price, order.side, quantity)
    price = round_to_tick(price, PRICE_TICK)
    # 触价成交不得劣于限价：滑点后若越过限价，则用限价（宁可少赚也不能假装成交）
    if order.side is Side.BUY and price > order.limit_price:
        price = order.limit_price
    if order.side is Side.SELL and price < order.limit_price:
        price = order.limit_price
    ledger.consume(quantity)
    return MatchResult(
        filled_quantity=quantity,
        fill_price=price,
        detail={"stage": "close", "touch": True, "slippage": slip_detail, **detail},
    )


def new_ledger(bar: Bar, params: MatchingParams) -> LiquidityLedger:
    """按当日成交量开一份额度。停牌或零成交量的 bar 额度为 0。"""
    if not bar.tradable:
        return LiquidityLedger(capacity=0.0)
    return LiquidityLedger(capacity=bar.volume * params.participation_limit)
