"""A 股费用模型预设（Step 7.5 新增）。

职责：在现有 BacktestConfig 可配置参数之上，提供「已核实 A 股市场统一规则」
和「研究假设情景」的可复用预设。

重要边界：
- 佣金由券商决定，交易所不强制；默认模拟占位（万 3），可按需替换为经核实费率；
- 最低佣金由券商决定；部分券商已取消，部分保留 5 元；
- 印花税、过户费为市场统一规则，可核实；
- 滑点始终是研究假设，无法精确核实；
- 所有预设仅作为 BacktestConfig 参数的便利封装，不修改 BacktestConfig 结构。
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# 已核实的 A 股市场统一规则（可公开查询）
# --------------------------------------------------------------------------- #
# 来源：
#   财政部《关于减半征收证券交易印花税的公告》(2023-08-28) — 印花税降至 0.05%
#   中国证券登记结算有限公司 — 过户费 0.001%（万 0.1）双向
# 适用：2023-08-28 之后的交易日
# 不适用：佣金（券商决定）、最低佣金（券商决定）、滑点（估计值）

# 市场统一规则（已核实）：
VERIFIED_STAMP_DUTY_RATE = 0.0005        # 0.05% — 印花税（仅卖方）
VERIFIED_TRANSFER_FEE_RATE = 0.00001    # 0.001% — 过户费（买卖双向，万 0.1）

# 佣金与最低佣金：以下仅为常见券商参考值（万 2.5，最低 5 元），
# 不代表所有券商，使用时必须标记为 "broker_reference_2024"
REFERENCE_COMMISSION_RATE = 0.00025      # 万 2.5（常见券商默认）
REFERENCE_MIN_COMMISSION = 5.0           # 单笔最低 5 元

# 滑点：研究假设（无法精确核实）
RESEARCH_SLIPPAGE_BUY = 0.001            # 0.1%
RESEARCH_SLIPPAGE_SELL = 0.001           # 0.1%
HIGH_COST_SLIPPAGE_BUY = 0.003           # 0.3%（高成本情景）
HIGH_COST_SLIPPAGE_SELL = 0.003          # 0.3%

# fee_source 常量（区分来源语义）
FEE_SOURCE_VERIFIED = "a_share_rules_verified_2023_2024"           # 仅印花税 + 过户费
FEE_SOURCE_REFERENCE = "broker_reference_common_2024"              # 佣金参考值
FEE_SOURCE_HIGH_COST = "research_scenario_high_cost"                # 研究假设
FEE_SOURCE_SIMULATION = "simulation_placeholder"                   # D6 原占位


@dataclass(frozen=True)
class FeePreset:
    """费用预设：把 6 个 BacktestConfig 费率参数打包 + 来源标记。"""
    commission_rate: float
    min_commission: float
    stamp_duty_rate: float
    transfer_fee_rate: float
    slippage_buy: float
    slippage_sell: float
    fee_source: str
    description: str
    verified_fields: tuple[str, ...]  # 已核实的字段名

    def apply(self, config):
        """应用到 BacktestConfig（返回新 frozen config）。"""
        return type(config)(
            symbol=config.symbol,
            initial_cash=config.initial_cash,
            start_date=config.start_date,
            end_date=config.end_date,
            exec_price_rule=config.exec_price_rule,
            signal_lag=config.signal_lag,
            market_rules=config.market_rules,
            commission_rate=self.commission_rate,
            min_commission=self.min_commission,
            stamp_duty_rate=self.stamp_duty_rate,
            transfer_fee_rate=self.transfer_fee_rate,
            slippage_buy=self.slippage_buy,
            slippage_sell=self.slippage_sell,
            fee_source=self.fee_source,
            assumptions=config.assumptions + (f"fee_preset={self.fee_source}: {self.description}",),
        )


# ---- 三个预设 ----
FEE_PRESET_SIMULATION = FeePreset(
    commission_rate=0.0003,
    min_commission=5.0,
    stamp_duty_rate=0.0005,
    transfer_fee_rate=0.0,
    slippage_buy=0.001,
    slippage_sell=0.001,
    fee_source=FEE_SOURCE_SIMULATION,
    description="Step 7.x 历史模拟占位参数。transfer_fee_rate=0 不真实，仅用于开发对比。",
    verified_fields=(),
)

FEE_PRESET_REFERENCE = FeePreset(
    commission_rate=REFERENCE_COMMISSION_RATE,
    min_commission=REFERENCE_MIN_COMMISSION,
    stamp_duty_rate=VERIFIED_STAMP_DUTY_RATE,
    transfer_fee_rate=VERIFIED_TRANSFER_FEE_RATE,
    slippage_buy=RESEARCH_SLIPPAGE_BUY,
    slippage_sell=RESEARCH_SLIPPAGE_SELL,
    fee_source=FEE_SOURCE_REFERENCE,
    description="佣金参考万 2.5（券商常见）+ 印花税/过户费已核实。滑点为估计。",
    verified_fields=("stamp_duty_rate", "transfer_fee_rate"),
)

FEE_PRESET_HIGH_COST = FeePreset(
    commission_rate=0.0004,
    min_commission=5.0,
    stamp_duty_rate=VERIFIED_STAMP_DUTY_RATE,
    transfer_fee_rate=VERIFIED_TRANSFER_FEE_RATE,
    slippage_buy=HIGH_COST_SLIPPAGE_BUY,
    slippage_sell=HIGH_COST_SLIPPAGE_SELL,
    fee_source=FEE_SOURCE_HIGH_COST,
    description="研究假设：较高佣金（万 4）+ 较高滑点（0.3%）。测试费用敏感性。",
    verified_fields=("stamp_duty_rate", "transfer_fee_rate"),
)


ALL_PRESETS = (FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST)


def get_preset(name: str) -> FeePreset:
    for p in ALL_PRESETS:
        if p.fee_source == name or p.description.startswith(name):
            return p
    raise KeyError(f"未找到费用预设 {name!r}")
