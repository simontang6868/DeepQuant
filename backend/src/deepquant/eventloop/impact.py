"""滑点与市场冲击模型。

三种模式，按"可信度递增、假设递减"排列
--------------------------------------
``NONE``      无滑点。存在的唯一目的是做**无摩擦对齐**：把同一策略、同一区间
              放到两个引擎上跑，若此时结果仍不一致，说明实现有问题而非模型差异。
``CONSTANT``  常数 bps。与既有 ``backtest.costs.apply_slippage`` 同口径，
              便于与 L1a 逐位比较。
``SQRT_IMPACT`` 平方根冲击模型：``impact = eta * sigma * sqrt(Q / ADV)``。
              文献常见形式，但在本项目中**没有实证标定**——
              项目阶段 J/R 的实测口径是"参与率中位 0.0054%、η=1 的冲击比
              真实成本小 25 倍"，不能直接搬成这里的 η。

关于参数默认值
--------------
``eta`` 默认 1.0 只是平方根模型的教科书写法，**不是本项目标定值**。
凡使用 ``SQRT_IMPACT``，报告的"模型假设"段必须写明 η、σ、ADV 的取值来源；
三者任一缺失即抛异常，不允许用 0 顶替（0 会让冲击恒等于 0，看起来"成本很低"）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from deepquant.market.ashare import round_to_tick
from deepquant.market.rules import Side

#: A 股最小报价单位（元）。滑点调整后价格必须落在报价单位上。
PRICE_TICK = 0.01


class SlippageMode(str, Enum):
    NONE = "none"
    CONSTANT = "constant"
    SQRT_IMPACT = "sqrt_impact"


class SlippageModelError(ValueError):
    """滑点参数不完整或非法。"""


@dataclass(frozen=True)
class SlippageModel:
    """滑点模型：把"理论成交价"调整为"模型估计的成交价"。"""

    mode: SlippageMode = SlippageMode.NONE
    #: CONSTANT 模式的单边滑点（基点，1bp = 0.01%）。
    bps: float = 0.0
    #: SQRT_IMPACT 模式的冲击系数。
    eta: float = 1.0
    #: 日波动率（小数，如 0.02 = 2%）。
    sigma: float = 0.0
    #: 日均成交量（股）。
    adv: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SlippageMode):
            raise SlippageModelError(f"mode 必须是 SlippageMode，收到 {self.mode!r}")
        if self.bps < 0 or not math.isfinite(self.bps):
            raise SlippageModelError(f"bps 必须是有限非负数，收到 {self.bps!r}")

    @property
    def is_frictionless(self) -> bool:
        return self.mode is SlippageMode.NONE or (
            self.mode is SlippageMode.CONSTANT and self.bps == 0.0
        )

    def unit_impact(self, quantity: int) -> float:
        """返回「买方向上 / 卖方向下」的价格冲击比例（小数）。"""
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise SlippageModelError(f"quantity 必须是正整数（股），收到 {quantity!r}")
        if self.mode is SlippageMode.NONE:
            return 0.0
        if self.mode is SlippageMode.CONSTANT:
            return self.bps / 10_000.0
        # SQRT_IMPACT
        if self.adv <= 0 or not math.isfinite(self.adv):
            raise SlippageModelError(
                "SQRT_IMPACT 需要正的 ADV（日均成交量），拒绝以 0 顶替——那会让冲击恒为 0"
            )
        if self.sigma < 0 or not math.isfinite(self.sigma):
            raise SlippageModelError(f"SQRT_IMPACT 需要有限的非负 sigma，收到 {self.sigma!r}")
        return self.eta * self.sigma * math.sqrt(quantity / self.adv)

    def apply(self, price: float, side: Side, quantity: int) -> tuple[float, dict[str, Any]]:
        """返回 (调整后价格, 说明)。价格取整到报价单位，方向对交易者不利。"""
        if not math.isfinite(price) or price <= 0:
            raise SlippageModelError(f"price 必须是有限正数，收到 {price!r}")
        if not isinstance(side, Side):
            raise SlippageModelError(f"side 必须是 Side，收到 {side!r}")
        impact = self.unit_impact(quantity)
        signed = impact if side is Side.BUY else -impact
        adjusted = round_to_tick(price * (1.0 + signed), PRICE_TICK)
        if adjusted <= 0:
            raise SlippageModelError(f"滑点后价格非正：{price} → {adjusted}")
        return adjusted, {
            "mode": self.mode.value,
            "unit_impact": impact,
            "theoretical_price": price,
            "adjusted_price": adjusted,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "bps": self.bps,
            "eta": self.eta,
            "sigma": self.sigma,
            "adv": self.adv,
            "frictionless": self.is_frictionless,
        }


def sqrt_impact(quantity: int, adv: float, sigma: float, eta: float) -> float:
    """独立函数形式（便于单元测试与外部复用）。"""
    if adv <= 0:
        raise SlippageModelError("adv 必须为正")
    if sigma < 0:
        raise SlippageModelError("sigma 必须非负")
    return eta * sigma * math.sqrt(quantity / adv)


FRICTIONLESS = SlippageModel(mode=SlippageMode.NONE)
