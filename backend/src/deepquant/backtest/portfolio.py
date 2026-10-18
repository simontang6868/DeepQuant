"""组合层配置、调仓调度与记账循环（A1 / A2 / A3）。

范围声明
--------
- **A1** ``PortfolioBacktestConfig``：组合回测的输入契约（标的、资金、费率模板、口径）；
- **A2** 调仓日历与截面选股/权重（``month_end_signal_dates`` /
  ``equal_weight_targets`` / ``build_rebalance_schedule``）；
- **A3** ``run_portfolio_backtest``：组合记账循环（多标的、共享现金池、连续净值）。

A3 的四项口径已先定稿于 ``docs/A3_ACCOUNTING_SPEC.md``（卖出先于买入 / 权重记账
费用粒度与 min_commission / 停牌估值冻结 / 涨跌停顺延），本模块只负责照着实现。
**口径文档与实现不一致时，以文档为准并修代码**，不得反过来改文档迁就实现。

为什么需要独立的组合层
----------------------
``Strategy.generate_signals`` 是「单标的 → 单序列」的接口（``strategies/base.py``）。
横截面选股需要「同一时点多标的比较」，语义上不属于任何单标的策略。
把它放这里，避免有人为了「复用」而把多标的逻辑硬塞进单标的接口
——B18 §6.3 明令禁止「把多股票策略伪装成单股票策略」。

与 `backtest.engine` 的关系（重要）
-----------------------------------
``run_backtest`` 是「单标的、单持仓、单现金」模型，无法表达跨标的共享现金池，
因此**不能**把它套在循环里冒充组合（那是 N 个独立账户，不是组合）。
本模块的做法是复用引擎的**撮合原语**（``compute_fees`` / ``execution_price`` /
``round_money`` / ``limit_prices`` / ``open_fill_block_reason``），只重新实现「循环」。
两者口径是否真的一致，由 ``tests/backtest/test_portfolio_engine_parity.py`` 断言，
不是靠注释声明。

绝不做的事
----------
- 不修改 ``BacktestConfig`` / ``engine.py``：交易规则一处定义，组合层只调用；
- 不做参数搜索：``top_fraction`` 等是先验固定的设计参数，不是待优化超参；
- 不支持做空；不模拟部分成交；不单独记账现金分红（前复权隐含再投资）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees, execution_price, round_money
from deepquant.market.ashare import limit_prices, open_fill_block_reason
from deepquant.market.rules import Side
from deepquant.performance.portfolio_metrics import (
    PortfolioPerformance,
    compute_portfolio_performance,
)

AllocationMode = Literal["weights", "lots"]

# 权重和的容差：等权 1/k 在浮点下未必精确等于 1
WEIGHT_SUM_TOLERANCE = 1e-9

# 金额比较容差（分）：低于一分钱的差额视为 0，避免浮点噪声产生「1 股订单」
MONEY_EPS = 0.01

# 单期「冻结估值」权重超过该比例 → 该期标记低可信（A3 口径三）
STALE_WEIGHT_THRESHOLD = 0.05

# 连续冻结超过该交易日数 → 额外记录 prolonged_stale（仍估值，不清仓）
PROLONGED_STALE_DAYS = 60


@dataclass(frozen=True)
class PortfolioBacktestConfig:
    """组合回测的输入契约（A1）。

    Args:
        symbols: 组合可选的标的池（code 列表，顺序不影响结果）。
        initial_cash: 组合初始资金。
        fee_config: **费率与市场规则的模板**（复用 ``BacktestConfig``，
            其 ``symbol`` / ``initial_cash`` 字段不参与组合层计算）。
            这样费用口径与单标的回测**共用一处定义**，不会两边漂移。
        allocation: ``weights``（权重记账，主口径，不受整手约束）或
            ``lots``（整手约束，用于检验可执行性）。B18 Q3：(a) 主 + (c) 附。
        lot_size: 整手股数；``weights`` 模式下不生效，仅记录。
        max_weight_per_asset: 单标的上限（防止等权在小样本下过度集中）。
            ``None`` 表示不限制。

    Raises:
        ValueError: 标的为空/重复、资金非正、allocation 非法、权重上限不合法。
    """

    symbols: tuple[str, ...]
    initial_cash: float
    fee_config: BacktestConfig
    allocation: AllocationMode = "weights"
    lot_size: int = 100
    max_weight_per_asset: float | None = None

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("symbols 不能为空")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols 含重复代码")
        if not isinstance(self.initial_cash, (int, float)) or isinstance(self.initial_cash, bool):
            raise ValueError(f"initial_cash 必须是数值，收到 {self.initial_cash!r}")
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError(f"initial_cash 必须为有限正数，收到 {self.initial_cash!r}")
        if self.allocation not in ("weights", "lots"):
            raise ValueError(
                f"allocation 必须是 weights / lots，收到 {self.allocation!r}"
            )
        if self.lot_size < 1:
            raise ValueError(f"lot_size 必须 >= 1，收到 {self.lot_size}")
        if self.max_weight_per_asset is not None and not (
            0 < self.max_weight_per_asset <= 1
        ):
            raise ValueError(
                f"max_weight_per_asset 必须在 (0, 1]，收到 {self.max_weight_per_asset!r}"
            )


@dataclass(frozen=True)
class TargetPortfolio:
    """某一信号日的目标权重表（执行发生在**次一交易日开盘**）。

    ``weights`` 为空 dict 表示「本期空仓（全部持现金）」——这是合法状态，
    不是错误。把它当作错误会导致「选不出股票时被迫随便买点什么」。
    """

    signal_date: pd.Timestamp
    weights: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if total > 1.0 + WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"权重和 {total:.12f} 超过 1.0")
        for symbol, w in self.weights.items():
            if not math.isfinite(w):
                raise ValueError(f"{symbol} 的权重非有限值：{w!r}")
            if w < 0:
                raise ValueError(
                    f"{symbol} 的权重为负 {w!r}：本模块不支持做空"
                    "（引擎 Side.SELL 仅用于平仓，见 B18 §4）"
                )

    @property
    def is_cash_only(self) -> bool:
        """本期是否全现金（无持仓）。"""
        return not self.weights


def month_end_signal_dates(
    trading_days: Sequence[pd.Timestamp],
) -> tuple[pd.Timestamp, ...]:
    """返回每月**最后一个交易日**（信号日）。

    信号在月末收盘后产生，执行在次月首个交易日开盘 —— 与引擎
    「T 日收盘信号、T+1 开盘成交」一致（``engine.py``），不做例外。

    用「月末最后交易日」而不是「每月最后一个自然日」：自然日可能不是交易日，
    以它为信号日会让「次一交易日」变成不可预期的跳跃。
    """
    if len(trading_days) == 0:
        return ()
    index = pd.DatetimeIndex(pd.to_datetime(list(trading_days))).sort_values()
    if index.has_duplicates:
        raise ValueError("trading_days 含重复日期")
    series = pd.Series(index=index, data=index)
    return tuple(series.groupby([index.year, index.month]).last().tolist())


def equal_weight_targets(
    scores: Mapping[str, float | None],
    *,
    top_fraction: float = 0.2,
    min_assets: int = 1,
    max_weight_per_asset: float | None = None,
) -> dict[str, float]:
    """按分数取前 ``top_fraction`` 比例，等权分配（A2 的选股核心）。

    Args:
        scores: ``{symbol: 分数}``。``None`` / ``NaN`` 视为「当期不可参与排序」
            （例如历史长度不足或停牌），**不填 0**——填 0 会让缺数据的标的
            排到中间，凭空进入组合。
        top_fraction: 选前多少比例，``(0, 1]``。
        min_assets: 可参与排序的标的少于此数时返回空仓（不强行凑数）。
        max_weight_per_asset: 单标的上限；若 ``1/k`` 超过上限，则**降低总仓位**
            而不是把权重挪给别的标的（后者等于偷偷改变选股结果）。

    Returns:
        ``{symbol: weight}``，权重和 <= 1。空 dict 表示空仓。

    排序确定性：分数降序，**并列时按代码升序**。没有这条，同一份数据两次运行
    可能选出不同组合，回测就不可复现。
    """
    if not (0.0 < top_fraction <= 1.0):
        raise ValueError(f"top_fraction 必须在 (0, 1]，收到 {top_fraction}")
    if min_assets < 1:
        raise ValueError(f"min_assets 必须 >= 1，收到 {min_assets}")

    usable = {
        str(s): float(v)
        for s, v in scores.items()
        if v is not None and isinstance(v, (int, float)) and math.isfinite(float(v))
    }
    if len(usable) < min_assets:
        return {}

    ranked = sorted(usable.items(), key=lambda kv: (-kv[1], kv[0]))
    k = max(1, math.ceil(len(ranked) * top_fraction))
    picked = ranked[:k]

    weight = 1.0 / k
    if max_weight_per_asset is not None and weight > max_weight_per_asset:
        weight = max_weight_per_asset
    return {symbol: weight for symbol, _ in picked}


def build_rebalance_schedule(
    scores_by_date: Mapping[pd.Timestamp, Mapping[str, float | None]],
    *,
    top_fraction: float = 0.2,
    min_assets: int = 1,
    max_weight_per_asset: float | None = None,
) -> tuple[TargetPortfolio, ...]:
    """把「每个信号日的分数表」转成目标权重序列（A2 的调度产物）。

    信号日必须严格递增；同一日期重复出现会抛错（重复调仓等于改变了持有期）。
    """
    if not scores_by_date:
        return ()
    dates = [pd.Timestamp(d) for d in scores_by_date]
    if len(set(dates)) != len(dates):
        raise ValueError("scores_by_date 含重复信号日")
    ordered = sorted(dates)

    schedule: list[TargetPortfolio] = []
    for d in ordered:
        weights = equal_weight_targets(
            scores_by_date[d],
            top_fraction=top_fraction,
            min_assets=min_assets,
            max_weight_per_asset=max_weight_per_asset,
        )
        schedule.append(TargetPortfolio(signal_date=d, weights=weights))
    return tuple(schedule)


def validate_schedule(
    schedule: Sequence[TargetPortfolio],
    *,
    symbols: Sequence[str],
) -> None:
    """校验调度与标的池一致（在执行前拦掉「选了池外标的」这种错误）。

    这类错误如果留到回测循环里，表现是「某只权重永远买不到」——
    净值看起来正常，但组合其实少了持仓，很难被发现。
    """
    pool = set(symbols)
    prev: pd.Timestamp | None = None
    for i, target in enumerate(schedule):
        if prev is not None and target.signal_date <= prev:
            raise ValueError(
                f"第 {i + 1} 个信号日 {target.signal_date} 未晚于前一个 {prev}；"
                "调仓日必须严格递增"
            )
        prev = target.signal_date
        outside = set(target.weights) - pool
        if outside:
            raise ValueError(f"信号日 {target.signal_date} 含池外标的：{sorted(outside)}")


# =========================================================================== #
# A3：组合记账循环
# =========================================================================== #
#
# 口径来源：docs/A3_ACCOUNTING_SPEC.md（已定稿）。四条：
#   ① 卖出先于买入（A 股当日卖出所得资金当日可买入）；
#   ② 订单名义金额 = 权益 × |Δw|，min_commission 逐笔生效；
#   ③ 停牌用最后有效价冻结估值，只估值不交易，单期 stale 权重 > 5% 标低可信；
#   ④ 买入遇涨停放弃（不追），卖出遇跌停顺延（不放弃）。

# 未成交 / 被阻挡的原因码（撮合类原因码复用 market.ashare 的 BLOCK_*）
REASON_INSUFFICIENT_CASH = "insufficient_cash"
REASON_NO_POSITION = "no_position"
REASON_DEFERRED_SELL = "deferred_sell"
REASON_UNFILLED_AT_END = "unfilled_at_end"
REASON_BELOW_ONE_UNIT = "below_one_unit"


@dataclass(frozen=True)
class PortfolioFill:
    """一笔组合层成交（含费用明细，便于逐笔复核）。"""

    fill_id: str
    date: pd.Timestamp
    symbol: str
    side: Side
    price: float
    quantity: int
    gross: float
    fees_total: float
    reason: str  # rebalance / deferred_sell


@dataclass(frozen=True)
class BlockedOrder:
    """一笔**没有**成交的订单（被阻挡或资金不足）。

    保留它的理由与 R5 同源：如果只记录成交，「买不进」会从记录里消失，
    读者会以为组合按目标权重完整建仓了。
    """

    date: pd.Timestamp
    symbol: str
    side: Side
    reason: str
    requested_quantity: int
    filled_quantity: int


@dataclass(frozen=True)
class PortfolioState:
    """组合每日状态（连续净值的唯一来源）。"""

    date: pd.Timestamp
    cash: float
    market_value: float
    total_equity: float | None
    n_positions: int
    n_stale_positions: int
    stale_weight_ratio: float
    low_confidence_valuation: bool


@dataclass
class PortfolioBacktestResult:
    """组合回测结果。"""

    config: PortfolioBacktestConfig
    schedule: tuple[TargetPortfolio, ...]
    states: tuple[PortfolioState, ...]
    fills: tuple[PortfolioFill, ...]
    blocked: tuple[BlockedOrder, ...]
    performance: PortfolioPerformance | None
    limitations: tuple[str, ...]
    data_quality_issues: tuple[str, ...]
    is_complete: bool
    incomplete_reasons: tuple[str, ...]

    @property
    def equity_values(self) -> list[float | None]:
        return [s.total_equity for s in self.states]

    @property
    def final_equity(self) -> float | None:
        return self.states[-1].total_equity if self.states else None

    @property
    def cumulative_return(self) -> float | None:
        final = self.final_equity
        if final is None or self.config.initial_cash <= 0:
            return None
        return final / self.config.initial_cash - 1.0

    def consistency_issues(self) -> list[str]:
        """自检：权益恒等式、现金非负、成交金额自洽。"""
        issues: list[str] = []
        for st in self.states:
            if st.total_equity is None:
                continue
            expected = st.cash + st.market_value
            if abs(st.total_equity - expected) > MONEY_EPS:
                issues.append(
                    f"{st.date.date()} 权益 {st.total_equity:.2f} != 现金+市值 {expected:.2f}"
                )
            if st.cash < -MONEY_EPS:
                issues.append(f"{st.date.date()} 现金为负：{st.cash:.2f}")
        for f in self.fills:
            if f.quantity <= 0:
                issues.append(f"{f.fill_id} 成交数量非正：{f.quantity}")
            if abs(f.gross - round_money(f.price * f.quantity)) > MONEY_EPS:
                issues.append(f"{f.fill_id} 成交额 {f.gross} 与 价×量 不符")
            if f.fees_total < 0:
                issues.append(f"{f.fill_id} 费用为负：{f.fees_total}")
        return issues


def _normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    """复用引擎的行情校验（延迟导入：避免模块级循环依赖）。"""
    from deepquant.backtest.engine import normalize_daily_data

    return normalize_daily_data(df)


def _valid_price(value: object) -> float | None:
    """把「可能缺失/非有限/非正」的价格转成 float 或 None。"""
    if value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _order_quantity(delta_value: float, price: float, unit: int) -> int:
    """按「Δ金额 / 价格」折算下单数量，向下取整到 unit（1 股或 1 手）。"""
    if delta_value <= MONEY_EPS or price <= 0:
        return 0
    raw = delta_value / price
    # 减一个极小量避免 99.9999999 → 100 的浮点进位（宁可少买 1 股，不可超买）
    lots = math.floor(raw / unit + 1e-12)
    return max(0, int(lots) * unit)


def _max_affordable(price: float, cash: float, unit: int, cfg: BacktestConfig) -> int:
    """在现金约束下可买的最大数量（含费用，逐 unit 回退）。"""
    qty = int(math.floor(cash / price / unit + 1e-12)) * unit
    while qty > 0:
        gross = round_money(price * qty)
        fees = compute_fees(Side.BUY, price, qty, cfg)
        if gross + fees.total <= cash + 1e-9:
            return qty
        qty -= unit
    return 0


def _try_sell(
    *,
    quantity: int,
    open_raw: object,
    prev_close: float | None,
    cfg: BacktestConfig,
) -> tuple[int, float, float, float, str | None]:
    """尝试卖出，返回 (成交数量, 成交价, 成交额, 费用, 阻挡原因)。"""
    price_open = _valid_price(open_raw)
    if price_open is None:
        return 0, 0.0, 0.0, 0.0, "no_price"
    price = execution_price(Side.SELL, price_open, cfg)
    block = open_fill_block_reason(
        Side.SELL, price, limit_prices(prev_close, cfg.market_rules.price_limit)
    )
    if block is not None:
        return 0, price, 0.0, 0.0, block
    gross = round_money(price * quantity)
    fees = compute_fees(Side.SELL, price, quantity, cfg)
    return quantity, price, gross, fees.total, None


def _try_buy(
    *,
    quantity: int,
    open_raw: object,
    prev_close: float | None,
    cash: float,
    unit: int,
    cfg: BacktestConfig,
) -> tuple[int, float, float, float, str | None]:
    """尝试买入（含现金约束），返回 (成交数量, 成交价, 成交额, 费用, 阻挡原因)。"""
    price_open = _valid_price(open_raw)
    if price_open is None:
        return 0, 0.0, 0.0, 0.0, "no_price"
    price = execution_price(Side.BUY, price_open, cfg)
    block = open_fill_block_reason(
        Side.BUY, price, limit_prices(prev_close, cfg.market_rules.price_limit)
    )
    if block is not None:
        # 口径④：买入遇涨停**直接放弃**，不顺延、不追高
        return 0, price, 0.0, 0.0, block

    affordable = _max_affordable(price, cash, unit, cfg)
    qty = min(quantity, affordable)
    if qty <= 0:
        return 0, price, 0.0, 0.0, REASON_INSUFFICIENT_CASH
    gross = round_money(price * qty)
    fees = compute_fees(Side.BUY, price, qty, cfg)
    return qty, price, gross, fees.total, None


def run_portfolio_backtest(
    prices: Mapping[str, pd.DataFrame],
    schedule: Sequence[TargetPortfolio],
    config: PortfolioBacktestConfig,
    *,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
) -> PortfolioBacktestResult:
    """执行一次组合回测（多标的、共享现金池、连续净值）。

    Args:
        prices: ``{symbol: DataFrame}``，列需含 open/high/low/close/volume，
            或以 ``date`` 列提供日期。日期索引**不需要**对齐 —— 各标的缺失的交易日
            会被视为停牌（冻结估值、不可交易）。
        schedule: 目标权重序列（``build_rebalance_schedule`` 的产物），
            信号日在 T、执行在 T 之后的首个交易日开盘。
        config: 组合配置（``PortfolioBacktestConfig``）。
        start_date / end_date: 回测区间（作用于日历的并集）。

    Returns:
        ``PortfolioBacktestResult``，含连续净值 ``states``、逐笔成交、被阻挡订单、
        以及由**连续净值**计算出的组合绩效（A6）。

    Raises:
        ValueError: 配置与行情不匹配、调度含池外标的、信号日挤到同一执行日。
        RuntimeError: 持仓标的自买入起从未有过有效价格（这是逻辑缺陷，不静默估值）。
    """
    if not isinstance(config, PortfolioBacktestConfig):
        raise ValueError(f"config 必须是 PortfolioBacktestConfig，收到 {config!r}")

    symbols = tuple(config.symbols)
    missing = set(symbols) - set(prices)
    if missing:
        raise ValueError(f"缺少行情数据：{sorted(missing)}")

    schedule = tuple(schedule)
    validate_schedule(schedule, symbols=symbols)

    frames = {s: _normalize_price_frame(prices[s]) for s in symbols}

    # --- 交易日历：各标的日期并集（缺失日 = 停牌，不是「没有这一天」）---
    all_dates = sorted(set().union(*(set(f.index) for f in frames.values())))
    calendar = pd.DatetimeIndex(all_dates)
    if start_date is not None:
        calendar = calendar[calendar >= pd.Timestamp(start_date)]
    if end_date is not None:
        calendar = calendar[calendar <= pd.Timestamp(end_date)]
    if len(calendar) == 0:
        raise ValueError("回测区间内没有任何可用行情")

    # 重采样到统一日历：NaN = 当日无该标的行情
    open_arr = {}
    close_arr = {}
    prev_close_arr = {}
    for s in symbols:
        o = frames[s]["open"].reindex(calendar)
        c = frames[s]["close"].reindex(calendar)
        open_arr[s] = o.to_numpy()
        close_arr[s] = c.to_numpy()
        # 「前收盘」= 严格早于 t 的最后一个有效收盘（停牌期间沿用更早的价）
        prev_close_arr[s] = c.ffill().shift(1).to_numpy()

    cfg = config.fee_config
    unit = 1 if config.allocation == "weights" else config.lot_size
    price_limit = cfg.market_rules.price_limit

    # --- 执行日映射：信号日之后的首个交易日 ---
    exec_map: dict[pd.Timestamp, TargetPortfolio] = {}
    unmatched: list[pd.Timestamp] = []
    for target in schedule:
        pos = calendar.searchsorted(pd.Timestamp(target.signal_date), side="right")
        if pos >= len(calendar):
            unmatched.append(target.signal_date)
            continue
        exec_date = calendar[pos]
        if exec_date in exec_map:
            raise ValueError(
                f"信号日 {target.signal_date} 与前一个信号挤到同一执行日 {exec_date}；"
                "同一执行日只允许一个目标组合"
            )
        exec_map[exec_date] = target

    cash = float(config.initial_cash)
    quantity: dict[str, int] = {s: 0 for s in symbols}
    last_price: dict[str, float] = {}  # 最近一次已知有效价（收盘或成交价）
    pending_sell: dict[str, int] = {}
    stale_streak: dict[str, int] = {s: 0 for s in symbols}

    states: list[PortfolioState] = []
    fills: list[PortfolioFill] = []
    blocked: list[BlockedOrder] = []
    dq: list[str] = []
    fill_seq = 0
    prev_low_conf = False

    def record_fill(t: pd.Timestamp, symbol: str, side: Side, price: float,
                    qty: int, gross: float, fees_total: float, reason: str) -> None:
        nonlocal fill_seq
        fill_seq += 1
        fills.append(
            PortfolioFill(
                fill_id=f"PF{fill_seq}",
                date=t,
                symbol=symbol,
                side=side,
                price=price,
                quantity=qty,
                gross=gross,
                fees_total=fees_total,
                reason=reason,
            )
        )

    for i, t in enumerate(calendar):
        is_rebalance = t in exec_map

        # ---------------- 开盘：调仓或顺延卖出 ----------------
        if is_rebalance:
            # 新目标覆盖旧的顺延指令（口径④的终止条件②）
            pending_sell.clear()
            target = exec_map[t]

            def open_value(symbol: str) -> float:
                px = _valid_price(open_arr[symbol][i])
                if px is not None:
                    return px
                return last_price.get(symbol, 0.0)

            equity_open = cash + sum(quantity[s] * open_value(s) for s in symbols if quantity[s] > 0)
            if equity_open <= 0:
                dq.append(f"{t.date()} 调仓日权益为 0，跳过调仓")

            # ① 卖出先于买入（按代码升序，保证可复现）
            for s in sorted(symbols):
                if quantity[s] <= 0:
                    continue
                w = float(target.weights.get(s, 0.0))
                px_ref = open_value(s)
                cur_val = quantity[s] * px_ref
                tgt_val = equity_open * w
                if tgt_val >= cur_val - MONEY_EPS:
                    continue
                if w <= 0.0:
                    want = quantity[s]  # 清仓：按全部持仓卖，不按目标金额反算
                else:
                    # 卖出粒度固定为 1 股：A 股买入必须整手，**卖出允许零股**
                    # （零股需一次性卖出）。若这里也用 lot_size 取整，
                    # `lots` 口径会残留卖不掉的零股，与真实规则不符。
                    want = min(quantity[s], _order_quantity(cur_val - tgt_val, px_ref, 1))
                if want <= 0:
                    continue
                qty_f, price, gross, fees_t, reason = _try_sell(
                    quantity=want,
                    open_raw=open_arr[s][i],
                    prev_close=_valid_price(prev_close_arr[s][i]),
                    cfg=cfg,
                )
                if reason is not None:
                    blocked.append(
                        BlockedOrder(date=t, symbol=s, side=Side.SELL, reason=reason,
                                     requested_quantity=want, filled_quantity=0)
                    )
                    if reason in ("limit_down", "no_price"):
                        pending_sell[s] = want  # 口径④：卖不出则顺延
                    continue
                cash = round_money(cash + gross - fees_t)
                quantity[s] -= qty_f
                last_price[s] = price
                record_fill(t, s, Side.SELL, price, qty_f, gross, fees_t, "rebalance")

            # ② 再做买入
            for s in sorted(target.weights):
                w = float(target.weights[s])
                if w <= 0:
                    continue
                px_ref = _valid_price(open_arr[s][i])
                if px_ref is None:
                    blocked.append(
                        BlockedOrder(date=t, symbol=s, side=Side.BUY, reason="no_price",
                                     requested_quantity=0, filled_quantity=0)
                    )
                    continue
                tgt_val = equity_open * w
                cur_val = quantity[s] * px_ref
                delta = tgt_val - cur_val
                if delta <= MONEY_EPS:
                    continue
                want = _order_quantity(delta, px_ref, unit)
                if want <= 0:
                    blocked.append(
                        BlockedOrder(date=t, symbol=s, side=Side.BUY,
                                     reason=REASON_BELOW_ONE_UNIT,
                                     requested_quantity=0, filled_quantity=0)
                    )
                    continue
                qty_f, price, gross, fees_t, reason = _try_buy(
                    quantity=want,
                    open_raw=open_arr[s][i],
                    prev_close=_valid_price(prev_close_arr[s][i]),
                    cash=cash,
                    unit=unit,
                    cfg=cfg,
                )
                if reason is not None or qty_f <= 0:
                    blocked.append(
                        BlockedOrder(date=t, symbol=s, side=Side.BUY,
                                     reason=reason or REASON_INSUFFICIENT_CASH,
                                     requested_quantity=want, filled_quantity=0)
                    )
                    continue
                cash = round_money(cash - gross - fees_t)
                quantity[s] += qty_f
                last_price[s] = price
                record_fill(t, s, Side.BUY, price, qty_f, gross, fees_t, "rebalance")

        elif pending_sell:
            # 非调仓日：重试顺延的卖出（口径④的终止条件①）
            for s in sorted(pending_sell):
                want = pending_sell[s]
                if quantity.get(s, 0) <= 0:
                    pending_sell.pop(s, None)
                    continue
                qty_f, price, gross, fees_t, reason = _try_sell(
                    quantity=min(want, quantity[s]),
                    open_raw=open_arr[s][i],
                    prev_close=_valid_price(prev_close_arr[s][i]),
                    cfg=cfg,
                )
                if reason is not None:
                    blocked.append(
                        BlockedOrder(date=t, symbol=s, side=Side.SELL,
                                     reason=REASON_DEFERRED_SELL,
                                     requested_quantity=want, filled_quantity=0)
                    )
                    continue
                cash = round_money(cash + gross - fees_t)
                quantity[s] -= qty_f
                last_price[s] = price
                record_fill(t, s, Side.SELL, price, qty_f, gross, fees_t, "deferred_sell")
                pending_sell.pop(s, None)

        # ---------------- 收盘：估值与记录 ----------------
        market_value = 0.0
        n_positions = 0
        n_stale = 0
        stale_value = 0.0
        for s in symbols:
            if quantity[s] <= 0:
                continue
            n_positions += 1
            close_px = _valid_price(close_arr[s][i])
            if close_px is not None:
                last_price[s] = close_px
                stale_streak[s] = 0
            else:
                close_px = last_price.get(s)
                if close_px is None:
                    raise RuntimeError(
                        f"{s} 在 {t.date()} 持仓但从未有过有效价格：记账逻辑缺陷，"
                        "不得静默按 0 估值"
                    )
                n_stale += 1
                stale_streak[s] += 1
                stale_value += quantity[s] * close_px
                if stale_streak[s] == PROLONGED_STALE_DAYS:
                    dq.append(
                        f"{t.date()} {s} 已连续 {PROLONGED_STALE_DAYS} 个交易日无有效收盘价，"
                        "按冻结价估值（不清仓：无价可卖）"
                    )
            market_value += quantity[s] * close_px

        total_equity = round_money(cash + market_value)
        stale_ratio = (stale_value / total_equity) if total_equity > 0 else 0.0
        low_conf = stale_ratio > STALE_WEIGHT_THRESHOLD
        if low_conf and not prev_low_conf:
            dq.append(
                f"{t.date()} 冻结估值权重 {stale_ratio:.1%} 超过 "
                f"{STALE_WEIGHT_THRESHOLD:.0%} 阈值，该期组合估值低可信"
            )
        prev_low_conf = low_conf

        states.append(
            PortfolioState(
                date=t,
                cash=cash,
                market_value=round_money(market_value),
                total_equity=total_equity,
                n_positions=n_positions,
                n_stale_positions=n_stale,
                stale_weight_ratio=round(stale_ratio, 6),
                low_confidence_valuation=low_conf,
            )
        )

    # 回测结束仍未卖出的顺延指令：记下来，不虚构成交（口径④终止条件③）
    for s, want in sorted(pending_sell.items()):
        blocked.append(
            BlockedOrder(date=calendar[-1], symbol=s, side=Side.SELL,
                         reason=REASON_UNFILLED_AT_END,
                         requested_quantity=want, filled_quantity=0)
        )
        dq.append(f"回测结束时 {s} 仍有 {want} 股顺延卖单未成交（未虚构成交）")
    if unmatched:
        dq.append(
            f"{len(unmatched)} 个信号日之后没有可执行交易日，未生成订单："
            f"{[str(d.date()) for d in unmatched]}"
        )

    total_traded = round_money(sum(f.gross for f in fills))
    total_fees = round_money(sum(f.fees_total for f in fills))
    equity_values = [st.total_equity for st in states]
    performance = None
    if states and all(v is not None for v in equity_values):
        performance = compute_portfolio_performance(
            equity_values,  # type: ignore[arg-type]
            initial_value=float(config.initial_cash),
            total_traded_amount=total_traded,
            total_fees=total_fees,
            n_rebalance_periods=len(exec_map),
            n_tradable_assets=len(symbols),
        )

    missing_equity = [st.date for st in states if st.total_equity is None]
    is_complete = not missing_equity

    limitations = (
        "日线简化成交模型：信号在 T 日收盘后产生，最早 T+1 开盘（经滑点调整）成交；"
        "不模拟订单簿、排队与部分成交。",
        "口径①：同一执行日先卖后买，卖出所得资金当日可用于买入（A 股规则）；"
        "因此买入可用资金包含当日卖出所得，方向性偏乐观、幅度未量化。",
        "口径②：订单名义金额 = 期初权益 × |Δ权重|；`weights` 模式按 1 股粒度、"
        "`lots` 模式按整手粒度；min_commission 逐笔生效（组合越分散，实际费率越高）。",
        "口径③：停牌/缺价用最后有效价**冻结估值**，只估值不交易；"
        f"单期冻结权重 > {STALE_WEIGHT_THRESHOLD:.0%} 标记低可信，不清仓也不摊给别人。",
        "口径④：买入遇涨停当日**放弃**（不顺延、不追高）；卖出遇跌停**顺延**至下一交易日重试，"
        "顺延期间该市值仍计入权益、但不计入可用现金。",
        "不支持做空；不做加仓/分批（目标权重直接决定持仓）；不模拟部分成交。",
        "使用前复权价格，隐含分红按除权日再投资，不单独记账现金分红。",
        "涨跌停比例对全部标的取同一配置（组合层不按板块区分 10%/20%）。",
        "股票池为当日快照，**未使用时点成分股**：结果含幸存者偏差，只能作上界估计。",
    )

    result = PortfolioBacktestResult(
        config=config,
        schedule=schedule,
        states=tuple(states),
        fills=tuple(fills),
        blocked=tuple(blocked),
        performance=performance,
        limitations=limitations,
        data_quality_issues=tuple(dq),
        is_complete=is_complete,
        incomplete_reasons=tuple(
            f"{d.date()} 组合权益无法估值" for d in missing_equity
        ),
    )

    issues = result.consistency_issues()
    if issues:
        raise RuntimeError(f"组合回测内部一致性检查失败：{issues[:5]}")

    return result


def run_dual_allocation_backtest(
    prices: Mapping[str, pd.DataFrame],
    schedule: Sequence[TargetPortfolio],
    config: PortfolioBacktestConfig,
    *,
    start_date: pd.Timestamp | None = None,
    end_date: pd.Timestamp | None = None,
) -> dict[str, PortfolioBacktestResult]:
    """同一批信号、两种记账口径并行跑（A4 权重记账 + A5 整手约束）。

    两个结果**不是**「哪个更好」的候选：
    - ``weights`` 是主口径（衡量信号本身）；
    - ``lots`` 是可执行性检验（整手截断 + 最低佣金放大后还剩多少）。
    按 R11，**不得**以两者收益高低来选择口径。
    """
    from dataclasses import replace

    if config.allocation not in ("weights", "lots"):
        raise ValueError(f"allocation 非法：{config.allocation!r}")

    out: dict[str, PortfolioBacktestResult] = {}
    for mode in ("weights", "lots"):
        out[mode] = run_portfolio_backtest(
            prices,
            schedule,
            replace(config, allocation=mode),
            start_date=start_date,
            end_date=end_date,
        )
    return out
