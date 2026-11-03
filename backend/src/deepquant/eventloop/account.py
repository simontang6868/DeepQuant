"""账户记账：现金、持仓、T+1 可卖、成本与权益。

三条硬规则
----------
1. **费用只能来自 ``backtest.costs.compute_fees``**。本项目在手写费率上错过三次
   （年化/月度混用、pp 与小数混用、分母用初始资金），所以这里不再有第二份费率公式。
2. **价格缺失不得默认 0 或成本价**。持仓市值算不出来时抛 ``MissingPriceError``，
   由调用方决定是跳过估值还是终止——静默记 0 会让权益曲线凭空跳水，且事后无法归因。
3. **T+1 是结构性的**：当日买入计入 ``bought_today``，不进 ``available``；
   只有 ``settle()`` 把它转成可卖。所以"当日买入当日卖出"在账户层面就不存在。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees, round_money
from deepquant.backtest.models import Fees
from deepquant.market.rules import Side


class AccountError(RuntimeError):
    """账户层面的非法操作（资金不足、持仓不足、数量非法）。"""


class InsufficientCash(AccountError):
    """现金不足以支付成交金额与费用。"""


class InsufficientPosition(AccountError):
    """可卖数量不足（含 T+1 冻结导致的不足）。"""


class MissingPriceError(AccountError):
    """估值所需价格缺失或非法。不允许用 0 或成本价顶替。"""


@dataclass
class Holding:
    """单标的持仓。数量单位为**股**。"""

    quantity: int = 0
    available: int = 0  # 可卖（已过 T+1）
    bought_today: int = 0  # 当日买入，尚不可卖
    cost: float = 0.0  # 持仓总成本（含买入费用）

    @property
    def avg_cost(self) -> float | None:
        if self.quantity <= 0:
            return None
        return self.cost / self.quantity

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "available": self.available,
            "bought_today": self.bought_today,
            "cost": self.cost,
            "avg_cost": self.avg_cost,
        }


@dataclass
class Account:
    """现金 + 持仓。不含撮合逻辑，只做记账与不变量自检。"""

    cash: float
    config: BacktestConfig
    frozen: float = 0.0
    holdings: dict[str, Holding] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.config, BacktestConfig):
            raise ValueError(f"config 必须是 BacktestConfig，收到 {type(self.config)!r}")
        if not isinstance(self.cash, (int, float)) or isinstance(self.cash, bool):
            raise ValueError(f"cash 必须是数值，收到 {self.cash!r}")
        if not math.isfinite(self.cash) or self.cash < 0:
            raise ValueError(f"cash 必须是有限的非负数，收到 {self.cash!r}")
        self.cash = round_money(float(self.cash))

    # ---------------- 持仓视图 ---------------- #
    def holding(self, symbol: str) -> Holding:
        return self.holdings.setdefault(symbol, Holding())

    def quantity(self, symbol: str) -> int:
        item = self.holdings.get(symbol)
        return item.quantity if item else 0

    def available(self, symbol: str) -> int:
        item = self.holdings.get(symbol)
        return item.available if item else 0

    def positions(self) -> dict[str, int]:
        return {symbol: item.quantity for symbol, item in self.holdings.items() if item.quantity > 0}

    # ---------------- 结算 ---------------- #
    def settle(self) -> None:
        """新交易日开始：把昨日买入转为可卖（T+1 落地点）。"""
        for item in self.holdings.values():
            item.available += item.bought_today
            item.bought_today = 0

    # ---------------- 成交记账 ---------------- #
    def buy(self, symbol: str, quantity: int, price: float) -> Fees:
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise AccountError(f"买入数量必须是正整数（股），收到 {quantity!r}")
        if not math.isfinite(price) or price <= 0:
            raise AccountError(f"买入价必须是有限正数，收到 {price!r}")
        fees = compute_fees(Side.BUY, price, quantity, self.config)
        outflow = round_money(price * quantity + fees.total)
        if outflow > self.cash + 1e-9:
            raise InsufficientCash(
                f"买入 {symbol} {quantity}@{price} 需 {outflow}，现金仅 {self.cash}"
            )
        self.cash = round_money(self.cash - outflow)
        item = self.holding(symbol)
        item.quantity += quantity
        item.bought_today += quantity
        item.cost = round_money(item.cost + outflow)
        return fees

    def sell(self, symbol: str, quantity: int, price: float) -> Fees:
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise AccountError(f"卖出数量必须是正整数（股），收到 {quantity!r}")
        if not math.isfinite(price) or price <= 0:
            raise AccountError(f"卖出价必须是有限正数，收到 {price!r}")
        item = self.holdings.get(symbol)
        if item is None or quantity > item.available:
            have = 0 if item is None else item.available
            raise InsufficientPosition(
                f"卖出 {symbol} {quantity} 超过可卖 {have}（T+1：当日买入 {0 if item is None else item.bought_today} 股不可卖）"
            )
        fees = compute_fees(Side.SELL, price, quantity, self.config)
        inflow = round_money(price * quantity - fees.total)
        self.cash = round_money(self.cash + inflow)
        prior = item.quantity
        item.quantity -= quantity
        item.available -= quantity
        if item.quantity == 0:
            item.cost = 0.0
        else:
            item.cost = round_money(item.cost * (item.quantity / prior))
        return fees

    # ---------------- 估值 ---------------- #
    def market_value(self, prices: Mapping[str, float]) -> float:
        total = 0.0
        for symbol, item in self.holdings.items():
            if item.quantity == 0:
                continue
            if symbol not in prices:
                raise MissingPriceError(f"缺少 {symbol} 的估值价格，拒绝以 0 或成本价顶替")
            price = prices[symbol]
            if not isinstance(price, (int, float)) or isinstance(price, bool):
                raise MissingPriceError(f"{symbol} 的估值价格非法：{price!r}")
            if not math.isfinite(price) or price <= 0:
                raise MissingPriceError(f"{symbol} 的估值价格非正：{price!r}")
            total += item.quantity * price
        return round_money(total)

    def equity(self, prices: Mapping[str, float]) -> float:
        return round_money(self.cash + self.frozen + self.market_value(prices))

    def snapshot(self, prices: Mapping[str, float]) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "frozen": self.frozen,
            "market_value": self.market_value(prices),
            "equity": self.equity(prices),
            "positions": self.positions(),
        }

    # ---------------- 自检 ---------------- #
    def check_invariants(self) -> list[str]:
        """返回违反不变量的描述列表（空列表 = 全部成立）。

        刻意不检查 ``equity == cash + frozen + mv``——那是定义式，永远成立，
        查它只会让人误以为"账目已核对"。
        """
        problems: list[str] = []
        if self.cash < 0:
            problems.append(f"现金为负：{self.cash}")
        if self.frozen < 0:
            problems.append(f"冻结资金为负：{self.frozen}")
        for symbol, item in self.holdings.items():
            if item.quantity < 0:
                problems.append(f"{symbol} 持仓为负：{item.quantity}")
            if item.available < 0:
                problems.append(f"{symbol} 可卖为负：{item.available}")
            if item.available > item.quantity:
                problems.append(
                    f"{symbol} 可卖 {item.available} 超过持仓 {item.quantity}"
                )
            if item.bought_today < 0:
                problems.append(f"{symbol} 当日买入为负：{item.bought_today}")
            if item.bought_today > item.quantity:
                problems.append(
                    f"{symbol} 当日买入 {item.bought_today} 超过持仓 {item.quantity}"
                )
            if item.quantity > 0 and item.cost <= 0:
                problems.append(f"{symbol} 有持仓但成本为 {item.cost}")
        return problems
