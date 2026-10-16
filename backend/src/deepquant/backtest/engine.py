"""日线回测执行引擎（V1：单标的、单策略、单持仓）。

完整链路：
    行情(规范化) → 策略信号 → 订单 → 市场规则检查 → 模拟成交 → 滑点/费用
    → 现金与持仓记账 → 每日权益估值 → BacktestResult

时序（严格禁止未来数据）：
    T 日收盘后产生信号 → 订单创建(order_date=T) → 最早 T+1 以开盘价成交
    理论基准价 = T+1 开盘价 → 按配置施加滑点得到拟成交价 → 用**拟成交价**做市场规则检查
    → 通过后才模拟成交 → 计算费用并更新现金/持仓 → 每日收盘后记录账户状态。

模型限制（同时写入 BacktestResult.limitations）：
    - 日线简化成交模型：不模拟订单簿、排队与部分成交，模拟成交不代表真实交易所必然能够成交；
    - 单标的、单持仓：不支持加仓/分批买入/部分卖出；已有持仓时的新买入信号被拒绝；
    - 卖出为整仓平仓（卖出当前全部可卖数量）；
    - 涨跌停须显式配置（price_limit）；未配置时不得默认套用 10%；
    - 若目标日行情缺失/非法或收盘价缺失，不伪造价格、不把持仓市值记为 0。
"""

from __future__ import annotations

import math

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees, execution_price, fee_model_notes, round_money
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fill,
    Order,
    OrderStatus,
    Position,
    ValuationStatus,
    assess_completeness,
)
from deepquant.market.ashare import (
    BLOCK_LIMIT_DOWN,
    BLOCK_LIMIT_UP,
    BLOCK_NO_PRICE,
    affordable_lot_quantity,
    limit_prices,
    open_fill_block_reason,
    sellable_quantity,
)
from deepquant.market.rules import Side, price_limit_notes
from deepquant.performance.metrics import compute_performance
from deepquant.performance.reproducibility import (
    capture_strategy_metadata,
    dataset_summary,
)
from deepquant.strategies.base import Signal, Strategy

# 引擎所需的最小行情字段（其余列可保留但非必需）
REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# 引擎级未成交原因码（市场规则相关的原因码复用 market.ashare 的 BLOCK_*）
REASON_INSUFFICIENT_CASH = "insufficient_cash"
REASON_INSUFFICIENT_POSITION = "insufficient_position"
REASON_ALREADY_HOLDING = "already_holding"

_ALLOWED_SIGNALS = {int(Signal.SELL), int(Signal.HOLD), int(Signal.BUY)}
_EPS = 1e-9


def normalize_daily_data(data: pd.DataFrame) -> pd.DataFrame:
    """把行情规范化为「日期升序、无重复、无时区」的 DatetimeIndex 表。

    严格校验（对应 D1）：日期可解析、不重复、升序、必需字段完整；
    不做静默删除、不去重、不填补。
    """
    if not isinstance(data, pd.DataFrame):
        raise TypeError(f"data 必须是 pandas.DataFrame，收到 {type(data)!r}")

    df = data.copy()
    if "date" in df.columns:
        parsed = pd.to_datetime(df["date"], errors="coerce", format="mixed")
        if parsed.isna().any():
            raise ValueError("存在无法解析的日期：不允许静默删除或填补")
        df = df.drop(columns=["date"])
        df.index = pd.DatetimeIndex(parsed)
    elif isinstance(df.index, pd.DatetimeIndex):
        pass
    else:
        raise ValueError("行情必须包含 date 列，或已使用 DatetimeIndex")

    if df.index.tz is not None:
        raise ValueError("日期必须为无时区（tz-naive）")

    if df.index.has_duplicates:
        raise ValueError("日期存在重复：不允许静默去重")

    if not df.index.is_monotonic_increasing:
        raise ValueError("日期未按升序排列")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需字段 {missing}")

    return df


def validate_signals(signals: pd.Series, index: pd.DatetimeIndex, source_name: str) -> pd.Series:
    """严格校验策略信号：索引必须与行情索引完全一致，取值必须属于 {-1, 0, 1}。"""
    if not isinstance(signals, pd.Series):
        raise TypeError(f"策略信号必须是 pandas.Series，收到 {type(signals)!r}")
    if not signals.index.equals(index):
        raise ValueError(
            f"信号索引与行情索引不一致（禁止依赖 pandas 自动对齐）；source={source_name}"
        )
    for value in signals.to_numpy():
        if pd.isna(value) or int(value) not in _ALLOWED_SIGNALS:
            raise ValueError(
                f"信号包含非法取值 {value!r}（允许 {sorted(_ALLOWED_SIGNALS)}）；source={source_name}"
            )
    return signals.astype(int)


def _target_buy_quantity(close_at_signal: float, cash: float, lot_size: int) -> int:
    """信号日按当日收盘价与现金估算买入目标数量（整手）。

    该值只作为「请求上限」；执行时会按手数规则逐手回退以覆盖费用与价格跳空。
    若无法估算（收盘价缺失/非法）或不足一手，则按最小 1 手请求，执行时再决定。
    """
    if pd.isna(close_at_signal) or not math.isfinite(close_at_signal) or close_at_signal <= 0:
        return lot_size
    affordable = affordable_lot_quantity(float(cash), float(close_at_signal), lot_size)
    return affordable if affordable >= lot_size else lot_size


def _affordable_buy_quantity(target: int, price: float, cash: float, config: BacktestConfig, lot_size: int) -> int:
    """从 target 起按整手逐手回退，返回「成交金额 + 费用」不超过现金的最大整手数量。"""
    max_lots = target // lot_size
    for lots in range(max_lots, 0, -1):
        qty = lots * lot_size
        gross = round_money(price * qty)
        fees = compute_fees(Side.BUY, price, qty, config)
        if gross + fees.total <= cash + _EPS:
            return qty
    return 0


def run_backtest(
    data: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
) -> BacktestResult:
    """执行一次日线回测，返回 BacktestResult。"""
    if not isinstance(config, BacktestConfig):
        raise ValueError(f"config 必须是 BacktestConfig，收到 {config!r}")
    if not isinstance(strategy, Strategy):
        raise ValueError(f"strategy 必须是 Strategy，收到 {strategy!r}")

    df = normalize_daily_data(data)

    # 记录输入数据的可复现信息（摘要基于规范化后的完整输入，不随日期区间裁剪而变化）
    dataset_meta = dataset_summary(df, dataset_id=config.symbol)
    strategy_meta = capture_strategy_metadata(strategy)

    # --------------------------------------------------------------------- #
    # 生成信号（先于区间裁剪，保证策略可以用 start_date 之前的预热数据）。
    #
    # Walk-Forward 场景中，调用方会把「训练尾部（预热）+ 样本外区间」拼接后一起传入；
    # 这里先让策略在完整 df 上生成信号，再裁剪到交易区间 —— 这样 start_date 之前
    # 的历史信息可以用来预热移动均线等滚动指标，但不会被计入交易统计 / 权益。
    #
    # 对于常规单次回测（没有额外预热），调用方只会传交易区间的数据，此时
    # df_full 与裁剪后的 df 相同，行为完全一致，不会引入回归。
    # --------------------------------------------------------------------- #
    signals_full = strategy.generate_signals(df)

    if config.start_date is not None:
        df = df.loc[df.index >= config.start_date]
    if config.end_date is not None:
        df = df.loc[df.index <= config.end_date]
    if df.empty:
        raise ValueError("回测区间内没有可用的行情数据")

    # 按 df.index 裁剪信号（仅保留交易区间内的信号日期）
    if not signals_full.index.equals(df.index):
        try:
            signals = signals_full.loc[df.index]
        except KeyError:
            raise ValueError(
                "策略信号索引与行情索引不一致：包含行情中不存在的日期"
            )
    else:
        signals = signals_full
    signals = validate_signals(signals, df.index, strategy.name)

    rules = config.market_rules
    lot_size = rules.lot_size
    dates = df.index
    opens = pd.to_numeric(df["open"], errors="coerce").astype(float).to_numpy()
    closes = pd.to_numeric(df["close"], errors="coerce").astype(float).to_numpy()
    sig = signals.to_numpy()
    n = len(df)

    cash = float(config.initial_cash)
    quantity = 0
    avg_cost: float | None = None

    orders: list[Order] = []
    fills: list[Fill] = []
    states: list[AccountState] = []
    data_quality: list[str] = []

    pending: Order | None = None
    order_seq = 0
    fill_seq = 0

    for i in range(n):
        t = dates[i]
        bought_today = 0

        # ---- 执行前一日创建的订单（今日为计划执行日）----
        if pending is not None:
            if pending.planned_exec_date != t:
                raise RuntimeError(
                    f"订单 {pending.order_id} 的计划执行日 {pending.planned_exec_date} 与当前交易日 {t} 不一致"
                )
            open_raw = opens[i]
            prev_close = None if i == 0 else closes[i - 1]

            if pd.isna(open_raw) or not math.isfinite(open_raw) or open_raw <= 0:
                pending.status = OrderStatus.REJECTED
                pending.reason = BLOCK_NO_PRICE
                data_quality.append(f"{t.date()} 开盘价缺失或非法，订单 {pending.order_id} 无法成交")
            else:
                # 滑点调整后的价格才是「拟成交价」，市场规则检查针对拟成交价进行
                price = execution_price(pending.side, float(open_raw), config)
                prev_close_valid = (
                    None
                    if prev_close is None or pd.isna(prev_close) or not math.isfinite(prev_close)
                    else float(prev_close)
                )
                boundary = limit_prices(prev_close_valid, rules.price_limit)
                block = open_fill_block_reason(pending.side, price, boundary)

                if block is not None:
                    pending.status = OrderStatus.REJECTED
                    pending.reason = block
                elif pending.side is Side.BUY:
                    if quantity > 0:
                        pending.status = OrderStatus.REJECTED
                        pending.reason = REASON_ALREADY_HOLDING
                    else:
                        qty = _affordable_buy_quantity(
                            pending.quantity, price, cash, config, lot_size
                        )
                        if qty < lot_size:
                            pending.status = OrderStatus.REJECTED
                            pending.reason = REASON_INSUFFICIENT_CASH
                        else:
                            gross = round_money(price * qty)
                            fees = compute_fees(Side.BUY, price, qty, config)
                            cash = round_money(cash - gross - fees.total)
                            quantity += qty
                            bought_today = qty
                            avg_cost = (gross + fees.total) / qty
                            fill_seq += 1
                            fill = Fill(
                                fill_id=f"F{fill_seq}",
                                order_id=pending.order_id,
                                symbol=config.symbol,
                                side=Side.BUY,
                                fill_date=t,
                                price=price,
                                quantity=qty,
                                fees=fees,
                            )
                            fills.append(fill)
                            pending.status = OrderStatus.FILLED
                            pending.fill_id = fill.fill_id
                else:  # SELL
                    sellable = sellable_quantity(quantity, bought_today, rules)
                    if sellable <= 0:
                        pending.status = OrderStatus.REJECTED
                        pending.reason = REASON_INSUFFICIENT_POSITION
                    else:
                        qty = sellable
                        gross = round_money(price * qty)
                        fees = compute_fees(Side.SELL, price, qty, config)
                        cash = round_money(cash + gross - fees.total)
                        quantity -= qty
                        if quantity == 0:
                            avg_cost = None
                        fill_seq += 1
                        fill = Fill(
                            fill_id=f"F{fill_seq}",
                            order_id=pending.order_id,
                            symbol=config.symbol,
                            side=Side.SELL,
                            fill_date=t,
                            price=price,
                            quantity=qty,
                            fees=fees,
                        )
                        fills.append(fill)
                        pending.status = OrderStatus.FILLED
                        pending.fill_id = fill.fill_id

            pending = None

        # ---- 每日收盘后记录账户状态与权益 ----
        position = Position(
            symbol=config.symbol, quantity=quantity, bought_today=bought_today, avg_cost=avg_cost
        )
        close_raw = closes[i]
        if quantity == 0:
            states.append(
                AccountState(
                    date=t,
                    cash=cash,
                    position=position,
                    close=None,
                    market_value=0.0,
                    total_equity=cash,
                    valuation_status=ValuationStatus.OK,
                )
            )
        elif pd.isna(close_raw) or not math.isfinite(close_raw) or close_raw <= 0:
            states.append(
                AccountState(
                    date=t,
                    cash=cash,
                    position=position,
                    close=None,
                    market_value=None,
                    total_equity=None,
                    valuation_status=ValuationStatus.MISSING_CLOSE,
                )
            )
            data_quality.append(f"{t.date()} 收盘价缺失，持仓权益无法估值（不记为 0）")
        else:
            close = float(close_raw)
            market_value = quantity * close
            states.append(
                AccountState(
                    date=t,
                    cash=cash,
                    position=position,
                    close=close,
                    market_value=market_value,
                    total_equity=cash + market_value,
                    valuation_status=ValuationStatus.OK,
                )
            )

        # ---- 收盘后读取信号，创建次日执行订单 ----
        if i + 1 < n and int(sig[i]) != int(Signal.HOLD):
            order_seq += 1
            if int(sig[i]) == int(Signal.BUY):
                side = Side.BUY
                target = _target_buy_quantity(closes[i], cash, lot_size)
            else:
                side = Side.SELL
                # 卖出意图 = 整仓平仓；空仓时按最小 1 手记录请求，执行时因无可卖持仓被拒
                target = quantity if quantity > 0 else lot_size
            pending = Order(
                order_id=f"O{order_seq}",
                symbol=config.symbol,
                side=side,
                signal_date=t,
                order_date=t,
                planned_exec_date=dates[i + 1],
                quantity=target,
                status=OrderStatus.PENDING,
            )
            orders.append(pending)

    limitations = [
        "日线简化成交模型：信号在 T 日收盘后产生，最早 T+1 以开盘价（经滑点调整）为基准成交；"
        "不模拟订单簿、排队与部分成交，模拟成交不代表真实交易所必然能够成交。",
        "买单数量在信号日按当日收盘价与现金估算，执行时按手数规则逐手回退以覆盖费用与价格跳空；"
        "卖出为整仓平仓。",
        "单标的、单持仓模型：不支持加仓/分批买入/部分卖出；已有持仓时的新买入信号被拒绝（already_holding）。",
        "最后一个交易日的信号在样本内没有可执行日，未生成订单。",
        "行情缺失/非法时不伪造价格；收盘价缺失时不把持仓市值记为 0，也不虚构权益。",
        *price_limit_notes(rules),
        *fee_model_notes(config),
    ]

    result = BacktestResult(
        config=config,
        period_start=dates[0],
        period_end=dates[-1],
        orders=orders,
        fills=fills,
        account_states=states,
        data_quality_issues=data_quality,
        limitations=limitations,
        strategy=strategy_meta,
        dataset=dataset_meta,
    )
    is_complete, reasons = assess_completeness(states)
    result.is_complete = is_complete
    result.incomplete_reasons = reasons

    issues = result.consistency_issues()
    if issues:
        raise RuntimeError(f"回测内部一致性检查失败：{issues}")

    # 统一在回测结束时填充绩效指标：仅读取已生成的账户状态/订单/成交，
    # 不重新运行策略、不重新撮合、不改变账户状态（compute_performance 是只读的）。
    # 这样使用者可通过 result.performance 直接获取基础绩效，无需自行拼装调用。
    result.performance = compute_performance(result)

    return result
