"""W1 逐窗明细落盘：把「窗口」变成一等公民的行，而不是事后无处可查的分位数。

为什么必须有这个模块
--------------------
B20 暴露了一个结构性缺陷：历史 288 组结果只落盘了 ``window_stats``（分位数），
没有逐窗收益明细。后果是 B1 买入持有基准**只能做分布级对照**，无法做逐窗配对，
也就无法回答「这只股票在这个窗口里到底跑赢没跑赢躺着不动」。

这条限制不是数据问题，是**落盘设计问题**。Gate 1 若沿用同一装置，第一轮结束
还会掉进同一个坑。因此本模块的唯一职责是：在一次 walk-forward 运行中，
把每个窗口的输入条件、结果、同期基准、成交明细、异常**原样写成一行**，
使后续任何对照都能按 ``window_key`` 精确配对。

三条不能让步的设计约束
----------------------
1. **失败窗口照样落盘**。选择失败 / OOS 失败 / 估值不完整的窗口必须各占一行，
   状态写在 ``status`` 里。少一行就等于「静默忽略失败」，会让后续汇总的
   分母悄悄变小、正收益率虚高。
2. **不填 0**。不可计算的收益保持空字符串（读回为 ``None``），不写 0。
   0 是「恰好持平」这个事实，不是「不知道」。
3. **不同配置不互相覆盖**。``experiment_id`` 由「口径版本 + 标的 + 策略 +
   费率档 + 数据指纹 + 区间 + 参数网格」的内容哈希派生：同配置重跑落到同一目录
   （幂等），不同配置必然落到不同目录；若目标文件已存在且内容不同，直接报错，
   不做「悄悄覆盖」。

配对语义
--------
``window_key`` = ``experiment_id|symbol|strategy|fee_source|w{index:04d}``。
``align_records`` 只在两边 key 集合、OOS 起止日期**完全一致**时返回配对列表；
任何缺失/错位都抛 ``WindowAlignmentError`` 并列出差异，而不是按位置 zip 硬凑。
按位置拼两个长度不同的列表，是这类对照最典型的事故来源。

口径边界（必须随结论声明）
--------------------------
- 策略侧 ``cumulative_return`` 来自引擎 ``PerformanceMetrics``；基准侧来自
  ``performance.curve.curve_stats``。两者定义同为 ``final / initial - 1``，
  但**不是同一次记账**：策略有中途成交与费用，基准只有期初一笔买入。
- B1 期末按收盘价估值（不平仓），因此 ``excess_return`` 不含期末卖出费用。
  需要含平仓成本的口径时用 ``liquidate_at_end=True`` 另存一份实验。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.performance.benchmarks import BuyAndHoldResult, buy_and_hold

SCHEMA_VERSION = "w1-window-record-v1"
CSV_NAME = "windows.csv"

# --- 窗口状态（取值固定，便于跨实验聚合） ---
STATUS_OK = "ok"                          # 选择成功 + OOS 成功 + 估值完整
STATUS_INCOMPLETE = "incomplete"           # OOS 跑通但逐日估值不完整（不可比）
STATUS_SELECTION_FAILED = "selection_failed"
STATUS_OOS_FAILED = "oos_failed"
STATUS_NO_METRICS = "no_metrics"           # OOS 成功但绩效指标缺失

# 差值不可计算的原因（写进列里，避免读者把空值当 0）
NA_BENCH_MISSING = "benchmark_missing"
NA_BENCH_NOT_EXECUTABLE = "benchmark_not_executable"
NA_STRATEGY_MISSING = "strategy_return_missing"


class WindowAlignmentError(ValueError):
    """两个实验的窗口集合无法一对一配对。"""


# =========================================================================== #
# 实验级上下文（同一实验内所有窗口共享，禁止逐窗漂移）
# =========================================================================== #


@dataclass(frozen=True)
class ExperimentContext:
    """一次 walk-forward 运行的固定条件。

    把这些字段提升到「实验级」而不是逐窗传入，是为了从类型上排除
    「同一实验里不同窗口用了不同费率/不同数据版本」这种不可能被发现的污染。
    """

    experiment_id: str
    design_version: str
    symbol: str
    strategy_name: str
    strategy_version: str
    initial_cash: float
    fee_source: str
    commission_rate: float
    min_commission: float
    stamp_duty_rate: float
    transfer_fee_rate: float
    slippage_buy: float
    slippage_sell: float
    price_limit_mode: str
    price_limit_rate: float | None
    dataset_digest: str
    dataset_rows: int
    dataset_start: str
    dataset_end: str
    universe_id: str
    universe_size: int
    oos_window: int
    step: int

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("experiment_id 不能为空")
        if not self.dataset_digest:
            raise ValueError("dataset_digest 不能为空（缺它就无法判断数据版本）")
        if self.dataset_rows < 0:
            raise ValueError(f"dataset_rows 不能为负，收到 {self.dataset_rows}")
        if self.universe_size < 0:
            raise ValueError(f"universe_size 不能为负，收到 {self.universe_size}")

    @classmethod
    def from_config(
        cls,
        config: BacktestConfig,
        *,
        experiment_id: str,
        design_version: str,
        strategy_name: str,
        strategy_version: str,
        price_limit_mode: str,
        dataset_digest: str,
        dataset_rows: int,
        dataset_start: str,
        dataset_end: str,
        universe_id: str,
        universe_size: int,
        oos_window: int,
        step: int,
    ) -> "ExperimentContext":
        """从 ``BacktestConfig`` 抄成本字段——**不重新推导**，避免第二份费率定义。"""
        return cls(
            experiment_id=experiment_id,
            design_version=design_version,
            symbol=config.symbol,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            initial_cash=float(config.initial_cash),
            fee_source=config.fee_source,
            commission_rate=float(config.commission_rate),
            min_commission=float(config.min_commission),
            stamp_duty_rate=float(config.stamp_duty_rate),
            transfer_fee_rate=float(config.transfer_fee_rate),
            slippage_buy=float(config.slippage_buy),
            slippage_sell=float(config.slippage_sell),
            price_limit_mode=price_limit_mode,
            price_limit_rate=config.market_rules.price_limit.rate
            if config.market_rules.price_limit is not None
            else None,
            dataset_digest=dataset_digest,
            dataset_rows=int(dataset_rows),
            dataset_start=dataset_start,
            dataset_end=dataset_end,
            universe_id=universe_id,
            universe_size=int(universe_size),
            oos_window=int(oos_window),
            step=int(step),
        )


def make_experiment_id(
    *,
    design_version: str,
    symbol: str,
    strategy_name: str,
    fee_source: str,
    price_limit_mode: str,
    dataset_digest: str,
    request_range: tuple[str, str],
    params_grid: dict[str, object],
) -> str:
    """内容哈希派生实验 ID（同配置幂等、异配置必不同）。

    为什么不用时间戳：时间戳会让「同配置重跑」产生新目录，两次结果无法区分
    「跑了两遍」还是「改了东西」。用内容哈希则相反——改了任何一项都会换 ID，
    没改就必然同名（写盘时再做一次内容比对，见 ``write_records``）。
    """
    payload = {
        "schema": SCHEMA_VERSION,
        "design_version": design_version,
        "symbol": symbol,
        "strategy_name": strategy_name,
        "fee_source": fee_source,
        "price_limit_mode": price_limit_mode,
        "dataset_digest": dataset_digest,
        "request_range": list(request_range),
        "params_grid": {k: list(v) if isinstance(v, (list, tuple)) else v
                        for k, v in sorted(params_grid.items())},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def window_key(experiment_id: str, symbol: str, strategy_name: str,
               fee_source: str, window_index: int) -> str:
    """配对键：实验 + 标的 + 策略 + 费率档 + 窗口序号。

    包含实验 ID 是为了让「跨实验误配」在键层面就不可能通过——即使两个实验
    窗口序号完全一样，键也不相等，``align_records`` 会直接拒绝。
    """
    return f"{experiment_id}|{symbol}|{strategy_name}|{fee_source}|w{int(window_index):04d}"


# =========================================================================== #
# 单窗记录
# =========================================================================== #


@dataclass(frozen=True)
class WindowRecord:
    """一个窗口的完整事实行（字段与 CSV 列一一对应，无隐藏字段）。"""

    # --- 身份与可追溯性 ---
    schema_version: str
    experiment_id: str
    window_key: str
    design_version: str
    symbol: str
    strategy_name: str
    strategy_version: str
    params_json: str | None

    # --- 数据版本 ---
    dataset_digest: str
    dataset_rows: int
    dataset_start: str
    dataset_end: str
    universe_id: str
    universe_size: int

    # --- 窗口边界 ---
    window_index: int
    train_start: str
    train_end: str
    oos_start: str
    oos_end: str
    train_periods: int
    oos_periods: int
    warmup_days: int

    # --- 成本与交易规则 ---
    initial_cash: float
    fee_source: str
    commission_rate: float
    min_commission: float
    stamp_duty_rate: float
    transfer_fee_rate: float
    slippage_buy: float
    slippage_sell: float
    price_limit_mode: str
    price_limit_rate: float | None

    # --- 策略绩效 ---
    status: str
    selection_success: bool
    oos_success: bool | None
    oos_is_complete: bool | None
    strategy_final_equity: float | None
    strategy_cumulative_return: float | None
    strategy_max_drawdown: float | None
    trade_count: int | None
    order_count: int | None
    total_fees: float | None

    # --- 同期基准（同窗口/同初始资金/同费率档/同涨跌停规则） ---
    benchmark_kind: str | None
    benchmark_label: str | None
    benchmark_executable: bool
    benchmark_blocked_reason: str | None
    benchmark_buy_date: str | None
    benchmark_buy_price: float | None
    benchmark_quantity: int | None
    benchmark_final_value: float | None
    benchmark_cumulative_return: float | None
    benchmark_max_drawdown: float | None
    benchmark_is_complete: bool | None

    # --- 策略 − 基准 ---
    excess_return: float | None
    excess_return_na_reason: str | None

    # --- 成交明细（紧凑 JSON；空列表表示该窗口无成交，不是缺失） ---
    n_fills: int
    fills_json: str
    n_orders: int
    orders_json: str

    # --- 异常 ---
    error: str | None
    oos_incomplete_reasons: str | None


# CSV 列顺序 = dataclass 字段顺序（两者由 ``_columns()`` 统一，不各写一份）
def _columns() -> list[str]:
    return [f.name for f in fields(WindowRecord)]


_NUMERIC_FIELDS = {
    "dataset_rows", "universe_size", "window_index", "train_periods", "oos_periods",
    "warmup_days", "trade_count", "order_count", "n_fills", "n_orders",
    "benchmark_quantity",
}
_FLOAT_FIELDS = {
    "initial_cash", "commission_rate", "min_commission", "stamp_duty_rate",
    "transfer_fee_rate", "slippage_buy", "slippage_sell", "price_limit_rate",
    "strategy_final_equity", "strategy_cumulative_return", "strategy_max_drawdown",
    "total_fees", "benchmark_buy_price", "benchmark_final_value",
    "benchmark_cumulative_return", "benchmark_max_drawdown", "excess_return",
}
_BOOL_FIELDS = {
    "selection_success", "oos_success", "oos_is_complete",
    "benchmark_executable", "benchmark_is_complete",
}
_NA = ""  # CSV 里的「空」——读回为 None


def _fmt(value: object) -> str:
    """把 Python 值写成 CSV 单元格。

    浮点用 ``repr``（最短往返表示），保证写盘→读回→再写盘字节一致，
    这样 ``write_records`` 才能用「内容是否相同」判断幂等，而不会被
    ``%.6f`` 之类的格式化误差误判成「内容变了」。
    """
    if value is None:
        return _NA
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return _NA  # inf/nan 不进 CSV：NaN 会被读成「缺失」，与 None 混同
        return repr(value)
    return str(value)


def _parse_bool(text: str) -> bool | None:
    if text == _NA:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"无法解析布尔值：{text!r}")


def _parse_float(text: str) -> float | None:
    return None if text == _NA else float(text)


def _parse_int(text: str) -> int | None:
    return None if text == _NA else int(text)


# =========================================================================== #
# 构造记录
# =========================================================================== #


def benchmark_for_window(
    window_df: pd.DataFrame,
    config: BacktestConfig,
    *,
    liquidate_at_end: bool = False,
) -> BuyAndHoldResult:
    """在**同一 OOS 窗口、同一 config** 上构造 B1 基准。

    只传 OOS 段的行情（不含预热段）：基准不需要指标预热，把预热段算进去
    会让它比策略多出几天的持有期，两边就不再是同一段行情。
    """
    opens = window_df["open"].astype(float).tolist()
    closes = window_df["close"].astype(float).tolist()
    dates = list(window_df.index)
    return buy_and_hold(dates, opens, closes, config, liquidate_at_end=liquidate_at_end)


def _status_of(wf) -> str:
    if not wf.selection_success:
        return STATUS_SELECTION_FAILED
    if wf.oos_success is False or wf.oos_success is None:
        return STATUS_OOS_FAILED
    if wf.final_equity is None or wf.cumulative_return is None:
        return STATUS_NO_METRICS
    if wf.oos_is_complete is False:
        return STATUS_INCOMPLETE
    return STATUS_OK


def _validate_metrics_consistency(wf) -> None:
    """拒绝自相矛盾的输入，而不是把它原样写进 CSV。

    「选择失败却有 OOS 收益」「OOS 失败却有期末权益」这类组合在语义上不存在，
    一旦被写成一行，读表的人只会看到数字，看不到矛盾。宁可在构造阶段就报错。
    """
    if not wf.selection_success and (
        wf.selected_params is not None or wf.cumulative_return is not None
    ):
        raise ValueError(
            f"窗口 {wf.window_index}: selection_success=False 却带有选中参数/收益，"
            "输入自相矛盾，拒绝落盘"
        )
    if wf.oos_success is False and (
        wf.cumulative_return is not None or wf.final_equity is not None
    ):
        raise ValueError(
            f"窗口 {wf.window_index}: oos_success=False 却带有收益/期末权益，"
            "输入自相矛盾，拒绝落盘"
        )
    if wf.selection_success and wf.oos_success is None and wf.cumulative_return is not None:
        raise ValueError(
            f"窗口 {wf.window_index}: oos_success=None（未执行）却带有收益，"
            "输入自相矛盾，拒绝落盘"
        )


def build_window_record(
    ctx: ExperimentContext,
    wf,
    *,
    benchmark: BuyAndHoldResult | None,
) -> WindowRecord:
    """把一个 ``WindowWFResult`` 转成一行事实。

    ``benchmark=None`` 表示**没有构造基准**（例如窗口无效、或基准模块抛错），
    此时 ``excess_return`` 留空并写明原因——不能因为懒就把它记成 0。
    """
    _validate_metrics_consistency(wf)
    status = _status_of(wf)

    selection_success = bool(wf.selection_success)
    oos_success = wf.oos_success
    oos_is_complete = wf.oos_is_complete

    bench = benchmark.summary if benchmark is not None else None
    bench_ret = bench.cumulative_return if bench is not None else None
    strat_ret = wf.cumulative_return

    if strat_ret is not None and bench_ret is not None:
        excess = strat_ret - bench_ret
        na_reason = None
    elif strat_ret is None:
        excess = None
        na_reason = NA_STRATEGY_MISSING
    elif benchmark is None:
        excess = None
        na_reason = NA_BENCH_MISSING
    elif not benchmark.is_executable:
        excess = None
        na_reason = NA_BENCH_NOT_EXECUTABLE
    else:
        excess = None
        na_reason = NA_BENCH_MISSING

    fills_json = json.dumps(list(wf.fills), ensure_ascii=False, default=str, separators=(",", ":"))
    orders_json = json.dumps(list(wf.orders), ensure_ascii=False, default=str, separators=(",", ":"))

    return WindowRecord(
        schema_version=SCHEMA_VERSION,
        experiment_id=ctx.experiment_id,
        window_key=window_key(
            ctx.experiment_id, ctx.symbol, ctx.strategy_name, ctx.fee_source,
            wf.window_index,
        ),
        design_version=ctx.design_version,
        symbol=ctx.symbol,
        strategy_name=ctx.strategy_name or wf.strategy_name,
        strategy_version=ctx.strategy_version or wf.strategy_version,
        params_json=json.dumps(wf.selected_params, sort_keys=True, ensure_ascii=False, default=str)
        if wf.selected_params is not None else None,
        dataset_digest=ctx.dataset_digest,
        dataset_rows=ctx.dataset_rows,
        dataset_start=ctx.dataset_start,
        dataset_end=ctx.dataset_end,
        universe_id=ctx.universe_id,
        universe_size=ctx.universe_size,
        window_index=int(wf.window_index),
        train_start=str(pd.Timestamp(wf.train_start).date()),
        train_end=str(pd.Timestamp(wf.train_end).date()),
        oos_start=str(pd.Timestamp(wf.oos_start).date()),
        oos_end=str(pd.Timestamp(wf.oos_end).date()),
        train_periods=int(wf.train_periods),
        oos_periods=int(wf.oos_periods),
        warmup_days=int(wf.warmup_days),
        initial_cash=ctx.initial_cash,
        fee_source=ctx.fee_source,
        commission_rate=ctx.commission_rate,
        min_commission=ctx.min_commission,
        stamp_duty_rate=ctx.stamp_duty_rate,
        transfer_fee_rate=ctx.transfer_fee_rate,
        slippage_buy=ctx.slippage_buy,
        slippage_sell=ctx.slippage_sell,
        price_limit_mode=ctx.price_limit_mode,
        price_limit_rate=ctx.price_limit_rate,
        status=status,
        selection_success=selection_success,
        oos_success=oos_success,
        oos_is_complete=oos_is_complete,
        strategy_final_equity=wf.final_equity,
        strategy_cumulative_return=strat_ret,
        strategy_max_drawdown=wf.max_drawdown_ratio,
        trade_count=wf.trade_count,
        order_count=wf.order_count,
        total_fees=wf.total_fees,
        benchmark_kind="buy_and_hold" if bench is not None else None,
        benchmark_label=bench.label if bench is not None else None,
        benchmark_executable=bool(benchmark.is_executable) if benchmark is not None else False,
        benchmark_blocked_reason=benchmark.blocked_reason if benchmark is not None else None,
        benchmark_buy_date=None if benchmark is None or benchmark.buy_date is None
        else str(pd.Timestamp(benchmark.buy_date).date()),
        benchmark_buy_price=benchmark.buy_price if benchmark is not None else None,
        benchmark_quantity=benchmark.quantity if benchmark is not None else None,
        benchmark_final_value=bench.final_value if bench is not None else None,
        benchmark_cumulative_return=bench_ret,
        benchmark_max_drawdown=bench.max_drawdown_ratio if bench is not None else None,
        benchmark_is_complete=bench.is_complete if bench is not None else None,
        excess_return=excess,
        excess_return_na_reason=na_reason,
        n_fills=len(wf.fills),
        fills_json=fills_json,
        n_orders=len(wf.orders),
        orders_json=orders_json,
        error=wf.oos_error or wf.selection_error,
        oos_incomplete_reasons=";".join(wf.oos_incomplete_reasons) or None,
    )


def build_records(
    ctx: ExperimentContext,
    wf_results: Sequence,
    window_frames: dict[int, pd.DataFrame],
    *,
    liquidate_at_end: bool = False,
) -> list[WindowRecord]:
    """批量构造；``window_frames`` 给出每个窗口的 OOS 行情（index/columns 见上）。

    缺行情时基准留空并标 ``benchmark_missing``，**不跳过整行**——
    跳过等于把该窗口从分母里删掉。
    """
    # 用 ctx 回填成本字段：调用方另传一个 config 就可能出现「算的基准」与
    # 「落盘的费率」不是同一套，事后无法从 CSV 复现。
    config = _config_from_context(ctx)

    out: list[WindowRecord] = []
    for wf in wf_results:
        frame = window_frames.get(int(wf.window_index))
        benchmark = None
        if frame is not None and len(frame) > 0:
            benchmark = benchmark_for_window(frame, config, liquidate_at_end=liquidate_at_end)
        out.append(build_window_record(ctx, wf, benchmark=benchmark))
    return out


def _config_from_context(ctx: ExperimentContext) -> BacktestConfig:
    """按落盘记录的成本字段重建 config，保证「写进去的」= 「算出来的」。"""
    from deepquant.market.rules import a_share_rules

    return BacktestConfig(
        symbol=ctx.symbol,
        initial_cash=ctx.initial_cash,
        market_rules=a_share_rules(price_limit_rate=ctx.price_limit_rate),
        commission_rate=ctx.commission_rate,
        min_commission=ctx.min_commission,
        stamp_duty_rate=ctx.stamp_duty_rate,
        transfer_fee_rate=ctx.transfer_fee_rate,
        slippage_buy=ctx.slippage_buy,
        slippage_sell=ctx.slippage_sell,
        fee_source=ctx.fee_source,
    )


# =========================================================================== #
# 写盘 / 读回
# =========================================================================== #


def write_records(records: Sequence[WindowRecord], out_dir: str | Path) -> Path:
    """写 ``<out_dir>/<experiment_id>/windows.csv``（原子替换 + 拒覆盖）。

    - 同一实验重跑且内容一致 → 不重写，返回路径（幂等，便于反复验证）；
    - 同一实验重跑但内容不一致 → 抛 ``RuntimeError``，让人去看发生了什么，
      **不允许**悄悄把上一版结果盖掉（R5：不删失败记录）。
    """
    if not records:
        raise ValueError("records 为空：拒绝写出一个「零窗口」的结果文件")

    experiment_ids = {r.experiment_id for r in records}
    if len(experiment_ids) != 1:
        raise ValueError(
            f"一次写盘只能属于一个实验，收到 {sorted(experiment_ids)}；"
            "不同实验请分别调用（否则不同配置会互相覆盖）"
        )
    exp_id = records[0].experiment_id
    keys = [r.window_key for r in records]
    if len(set(keys)) != len(keys):
        raise ValueError("window_key 重复：同一实验内窗口序号必须唯一，否则配对会错位")

    target_dir = Path(out_dir) / exp_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / CSV_NAME

    columns = _columns()
    rows = [[_fmt(getattr(r, c)) for c in columns] for r in records]
    # 按窗口序号排序后再落盘：CSV 顺序即读者看到的顺序，乱序会让「第几个窗口」
    # 与人工核对时的直觉对不上（配对本身靠 window_key，不依赖顺序）。
    idx_pos = columns.index("window_index")
    rows.sort(key=lambda row: int(row[idx_pos]))

    payload = _render_csv(columns, rows)

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing == payload:
            return target
        raise RuntimeError(
            f"{target} 已存在且内容不同：拒绝覆盖。"
            "若确需替换，请先人工确认并移走旧文件（本模块不做自动删除）。"
        )

    tmp = target.with_suffix(".csv.tmp")
    tmp.write_text(payload, encoding="utf-8", newline="")
    tmp.replace(target)
    return target


def _render_csv(columns: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    import io

    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def read_records(path: str | Path) -> list[WindowRecord]:
    """读回 CSV → ``WindowRecord`` 列表（空单元格 → None，不填 0）。

    读回时校验表头与当前 schema 完全一致：列增删会改变语义，
    静默兼容读会产出「看着有值、其实错位」的数据。
    """
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        expected = _columns()
        if list(header) != expected:
            missing = [c for c in expected if c not in header]
            extra = [c for c in header if c not in expected]
            raise ValueError(
                f"表头与 schema {SCHEMA_VERSION} 不一致；缺失={missing} 多余={extra}"
            )
        out: list[WindowRecord] = []
        for raw in reader:
            values: dict[str, object] = {}
            for name, text in raw.items():
                if name in _BOOL_FIELDS:
                    values[name] = _parse_bool(text)
                elif name in _NUMERIC_FIELDS:
                    values[name] = _parse_int(text)
                elif name in _FLOAT_FIELDS:
                    values[name] = _parse_float(text)
                else:
                    values[name] = None if text == _NA else text
            out.append(WindowRecord(**values))
    return out


# =========================================================================== #
# 配对与汇总
# =========================================================================== #


def align_records(
    left: Sequence[WindowRecord],
    right: Sequence[WindowRecord],
) -> list[tuple[WindowRecord, WindowRecord]]:
    """按 ``window_key`` 做一对一配对；任何错位都抛错，绝不按位置 zip。

    校验三件事：key 集合相同、OOS 起止日期相同、无重复 key。
    """
    def index(records: Sequence[WindowRecord], side: str) -> dict[str, WindowRecord]:
        out: dict[str, WindowRecord] = {}
        for r in records:
            if r.window_key in out:
                raise WindowAlignmentError(f"{side} 存在重复 window_key：{r.window_key}")
            out[r.window_key] = r
        return out

    lmap, rmap = index(left, "left"), index(right, "right")
    only_left = sorted(set(lmap) - set(rmap))
    only_right = sorted(set(rmap) - set(lmap))
    if only_left or only_right:
        raise WindowAlignmentError(
            f"窗口集合不一致：仅左侧 {len(only_left)} 个（示例 {only_left[:3]}）、"
            f"仅右侧 {len(only_right)} 个（示例 {only_right[:3]}）"
        )

    pairs: list[tuple[WindowRecord, WindowRecord]] = []
    for key in sorted(lmap):
        a, b = lmap[key], rmap[key]
        if (a.oos_start, a.oos_end) != (b.oos_start, b.oos_end):
            raise WindowAlignmentError(
                f"{key} 的 OOS 区间不一致：{a.oos_start}~{a.oos_end} vs "
                f"{b.oos_start}~{b.oos_end}"
            )
        pairs.append((a, b))
    return pairs


def summarize_records(records: Sequence[WindowRecord]) -> dict[str, object]:
    """窗口级汇总 + **配对差**（有逐窗明细才能算的那个数）。

    ``median_paired_excess`` 与本模块之外常见的「中位数之差」是**两个不同的数**，
    必须分别命名。B20 的历史结果只能给后者，本表能给前者——这是 W1 的直接价值。
    """
    if not records:
        return {"n_windows": 0}

    strat = [r.strategy_cumulative_return for r in records
             if r.strategy_cumulative_return is not None]
    bench = [r.benchmark_cumulative_return for r in records
             if r.benchmark_cumulative_return is not None]
    paired = [r.excess_return for r in records if r.excess_return is not None]

    def _median(values: list[float]) -> float | None:
        return float(pd.Series(values).median()) if values else None

    status_counts: dict[str, int] = {}
    for r in records:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    med_strat, med_bench = _median(strat), _median(bench)
    return {
        "n_windows": len(records),
        "status_counts": status_counts,
        "n_strategy_returns": len(strat),
        "n_benchmark_returns": len(bench),
        "n_paired": len(paired),
        "median_strategy_return": med_strat,
        "median_benchmark_return": med_bench,
        # 两个不同的量，名字必须不同（R12 同族陷阱）
        "median_paired_excess": _median(paired),
        "median_strategy_minus_median_benchmark": (
            None if med_strat is None or med_bench is None else med_strat - med_bench
        ),
        "n_windows_strategy_beats_benchmark": sum(1 for v in paired if v > 0),
        "n_windows_strategy_loses_to_benchmark": sum(1 for v in paired if v < 0),
        "na_reason_counts": _count_na(records),
        "note": (
            "窗口级统计，不是连续账户净值；median_paired_excess 是逐窗差的中位数，"
            "median_strategy_minus_median_benchmark 是两个中位数之差，两者不相等。"
        ),
    }


def _count_na(records: Sequence[WindowRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        if r.excess_return_na_reason:
            counts[r.excess_return_na_reason] = counts.get(r.excess_return_na_reason, 0) + 1
    return counts
