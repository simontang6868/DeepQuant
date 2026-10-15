"""交易费用与滑点计算（纯函数，无 IO、无状态、无副作用）。

职责边界：
- 只根据「方向 + 价格 + 数量 + 配置」计算费用明细与滑点调整后的价格；
- **不改动**账户现金、持仓、订单状态；不实现撮合、拒绝策略、逐手回退或权益曲线；
- 不读写网络/行情；相同输入 + 相同配置 ⇒ 相同结果。

费用参数映射（来自 BacktestConfig，共 6 个，复用既有定义，不重复造概念）：
- commission_rate + min_commission  → 佣金 / 每笔最低佣金
- stamp_duty_rate                   → 印花税（仅卖出方）
- transfer_fee_rate                 → 过户费（买卖双向）
- slippage_buy / slippage_sell      → 买入 / 卖出比例滑点

重要说明：
- 默认费率来自 D6 的**模拟占位参数**，fee_source="simulation_placeholder"；
  这些数值仅用于开发、测试与模拟，**不得**被描述为已核实的最新 A 股实际费率；
- 印花税仅卖出、过户费双向、佣金按成交额并受最低佣金约束，均为本项目的建模约定；
- 金额统一四舍五入到 0.01（分，ROUND_HALF_UP）；
- 滑点为简化比例模型，调整后的价格**不代表真实市场必然可成交**，
  不模拟订单簿、排队与部分成交；涨跌停等成交约束由执行引擎（Step 5.2.4）在市场规则校验后处理。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from deepquant.backtest.config import BacktestConfig, require_non_negative, require_positive
from deepquant.backtest.models import Fees
from deepquant.market.ashare import round_to_tick
from deepquant.market.rules import Side

# 金额精度：统一到「分」，半值向上（不使用 Python 默认的银行家舍入）
MONEY_DECIMALS = 2
MONEY_QUANT = Decimal("0.01")

# 最小报价单位默认值（建模参数，可由调用方覆盖；不是费用费率）
DEFAULT_PRICE_TICK = 0.01


def _require_positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} 必须是正整数，收到 {value!r}")
    return value


def _require_side(value: object) -> Side:
    if not isinstance(value, Side):
        raise ValueError(f"side 必须是 Side，收到 {value!r}")
    return value


def round_money(value: float) -> float:
    """把金额四舍五入到 0.01（分），半值向上；非法值抛出 ValueError。

    使用 Decimal(str(value)) 避免二进制浮点误差影响「分」位结果。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"金额必须是有限数值，收到 {value!r}")
    return float(Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# 费用计算
# --------------------------------------------------------------------------- #


def compute_fees(
    side: Side,
    price: float,
    quantity: int,
    config: BacktestConfig,
) -> Fees:
    """计算单笔成交的费用明细。

    规则：
    1. 佣金基数 = 成交额 = price × quantity；佣金 = max(成交额 × commission_rate, min_commission)，
       按**每笔成交**单独计算最低佣金；
    2. 印花税仅卖出方：成交额 × stamp_duty_rate；买入方为 0；
    3. 过户费买卖双向：成交额 × transfer_fee_rate；
    4. 各费用分别四舍五入到 0.01（分，ROUND_HALF_UP）后写入 Fees。
    """
    side = _require_side(side)
    price = require_positive(price, "price")
    quantity = _require_positive_int(quantity, "quantity")
    if not isinstance(config, BacktestConfig):
        raise ValueError(f"config 必须是 BacktestConfig，收到 {config!r}")

    gross = price * quantity
    commission = max(gross * config.commission_rate, config.min_commission)
    stamp_duty = gross * config.stamp_duty_rate if side is Side.SELL else 0.0
    transfer_fee = gross * config.transfer_fee_rate

    return Fees(
        commission=round_money(commission),
        stamp_duty=round_money(stamp_duty),
        transfer_fee=round_money(transfer_fee),
    )


# --------------------------------------------------------------------------- #
# 滑点计算
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PriceAdjustment:
    """滑点调整结果（结构化）。

    区分「理论价格(base_price)」与「滑点调整后价格(adjusted_price)」。
    """

    side: Side
    base_price: float
    adjusted_price: float
    slippage_rate: float

    def __post_init__(self) -> None:
        _require_side(self.side)
        require_positive(self.base_price, "base_price")
        require_positive(self.adjusted_price, "adjusted_price")
        require_non_negative(self.slippage_rate, "slippage_rate")

    @property
    def slippage_per_share(self) -> float:
        """单位滑点（调整后 − 基准）；买入为正、卖出为负。"""
        return self.adjusted_price - self.base_price


def apply_slippage(
    side: Side,
    base_price: float,
    slippage_rate: float,
    *,
    tick_size: float = DEFAULT_PRICE_TICK,
) -> float:
    """按比例滑点调整基准价，并按最小报价单位取整。

    方向（对账户保守）：
    - 买入：base_price × (1 + slippage_rate)
    - 卖出：base_price × (1 - slippage_rate)

    slippage_rate = 0 时，结果等于按 tick 取整后的 base_price。
    卖出时若 (1 - slippage_rate) <= 0，视为滑点率过大导致卖出价格无效，抛出 ValueError。
    取整方式：按 tick_size 就近取整、半值向上（作用于实际计算出的浮点值）；恰好落在
    半 tick 上的极端情形由二进制浮点表示决定，同一输入始终得到同一结果（确定性）。
    返回的只是「调整后价格」，不代表真实市场必然可成交。
    """
    side = _require_side(side)
    base_price = require_positive(base_price, "base_price")
    slippage_rate = require_non_negative(slippage_rate, "slippage_rate")

    factor = 1.0 + slippage_rate if side is Side.BUY else 1.0 - slippage_rate
    if factor <= 0:
        raise ValueError(
            f"滑点率过大，导致卖出价格无效（base_price={base_price}, slippage_rate={slippage_rate}）"
        )

    return round_to_tick(base_price * factor, tick_size)


def adjust_price(
    side: Side,
    base_price: float,
    config: BacktestConfig,
    *,
    tick_size: float = DEFAULT_PRICE_TICK,
) -> PriceAdjustment:
    """从配置读取方向对应的滑点率，返回结构化滑点调整结果。"""
    if not isinstance(config, BacktestConfig):
        raise ValueError(f"config 必须是 BacktestConfig，收到 {config!r}")
    side = _require_side(side)
    rate = config.slippage_buy if side is Side.BUY else config.slippage_sell
    adjusted = apply_slippage(side, base_price, rate, tick_size=tick_size)
    return PriceAdjustment(
        side=side,
        base_price=float(base_price),
        adjusted_price=adjusted,
        slippage_rate=rate,
    )


def execution_price(
    side: Side,
    base_price: float,
    config: BacktestConfig,
    *,
    tick_size: float = DEFAULT_PRICE_TICK,
) -> float:
    """便捷接口：返回滑点调整后的成交基准价。"""
    return adjust_price(side, base_price, config, tick_size=tick_size).adjusted_price


# --------------------------------------------------------------------------- #
# 规则说明（供回测结果记录，保留 fee_source 语义）
# --------------------------------------------------------------------------- #


def fee_model_notes(config: BacktestConfig) -> list[str]:
    """返回费用/滑点模型的口径说明，供 BacktestResult.limitations / assumptions 记录。"""
    return [
        f"费用来源标注：fee_source={config.fee_source}"
        "（默认 simulation_placeholder，为模拟占位参数，不代表已核实的最新 A 股实际费率）。",
        "佣金 = max(成交额 × commission_rate, min_commission)，按每笔成交单独计算最低佣金。",
        "印花税仅卖出方收取（成交额 × stamp_duty_rate），买入方为 0。",
        "过户费买卖双向收取（成交额 × transfer_fee_rate）。",
        "金额（各项费用）四舍五入到 0.01（分，ROUND_HALF_UP）。",
        "滑点以成交价体现：买入价 = 基准价 × (1 + slippage_buy)，"
        "卖出价 = 基准价 × (1 - slippage_sell)，并按最小报价单位取整。",
        "滑点为简化比例模型，调整后价格不代表真实市场必然可成交"
        "（不模拟订单簿、排队、部分成交；涨跌停等约束由执行引擎在市场规则校验后处理）。",
    ]
