"""Walk-Forward 滚动样本外验证（v1：固定参数 / 纯函数 / 无持久化）。

本模块不实现任何参数搜索或机器学习训练；首版定位为「**固定策略参数的滚动
样本外评估**」。其目标是：

- 让研究人员在明确的时间隔离规则下评估策略在「未见过」的历史区间中的表现；
- 为后续参数敏感性分析和过拟合检查建立基础；
- 绝不伪装成已经完成训练 / 自动调参 / 机器学习模型。

核心原则（必须坚持）：

1. 时间顺序严格单向：训练区间严格早于样本外区间，窗口之间按时间推进。
2. 不随机打乱交易日：本项目是时间序列系统，随机 K-fold 会破坏时间语义。
3. 每个窗口独立账户：初始资金、持仓都独立；窗口之间资金 / 持仓不隐式串联。
4. 明确窗口级统计 vs. 组合收益：跨窗口汇总只是各窗口指标的统计量，不冒充
   把所有窗口串成一个连续账户的组合收益。
5. 费用假设透明：汇总结果必须写明 fee_source 或等效标识；当前 D6 为模拟占位，
   不得描述为真实券商费率。
6. 预热数据边界清晰：预热区间的数据仅用于指标预热（移动均线等），其交易 / 收益
   绝不计入样本外绩效。
7. 失败不掩盖：单个窗口失败（数据不足、引擎异常、绩效不可计算）不被静默当作
   成功窗口，汇总时单独统计。

与 ``backtest.engine.run_backtest`` 的协作方式：
- 本模块负责「生成窗口 → 准备拼接好的 (warmup + oos) 行情 → 调 run_backtest →
  从 result 中抽取样本外窗口的统计」；
- 引擎负责：在完整行情上生成信号（保证预热可用），按 config.start/end 裁剪，
  只在交易区间内撮合、记账、估值；
- 引擎本身不需要改，本次唯一改动是让引擎先调 strategy.generate_signals(df_full)
  再按 config 裁剪（允许策略看到预热数据）——这对常规单次回测零影响。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from deepquant.backtest.config import BacktestConfig

if TYPE_CHECKING:  # 仅用于类型标注，避免循环导入
    from deepquant.backtest.models import BacktestResult
    from deepquant.strategies.base import Strategy


# =========================================================================== #
# 配置与窗口定义
# =========================================================================== #


@dataclass(frozen=True)
class WalkForwardConfig:
    """滚动验证配置（交易日数量为单位，避免自然日 / 交易日混淆）。

    所有窗口长度以**交易日数量**计量（即 DataFrame index 中连续行的个数），
    不使用自然日。缺失交易日（节假日）在数据清洗阶段已被去除，此处无需处理。

    参数约束：
    - train_window、oos_window、step、min_train、min_oos 必须都是正整数；
    - step ≤ oos_window + train_window（否则会有跳过区间）；
    - start_date / end_date 为可选的整体时间边界（tz-naive pd.Timestamp）。
    """

    train_window: int
    oos_window: int
    step: int
    warmup: int | None = None
    min_train: int = 60
    min_oos: int = 20
    start_date: pd.Timestamp | None = None
    end_date: pd.Timestamp | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("train_window", self.train_window),
            ("oos_window", self.oos_window),
            ("step", self.step),
            ("min_train", self.min_train),
            ("min_oos", self.min_oos),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(
                    f"{name} 必须是正整数，收到 {value!r}（bool 不是正整数）"
                )
        if self.warmup is not None and (
            not isinstance(self.warmup, int) or isinstance(self.warmup, bool) or self.warmup < 0
        ):
            raise ValueError(
                f"warmup 必须是非负整数或 None，收到 {self.warmup!r}"
            )
        if self.train_window < self.min_train:
            raise ValueError(
                f"train_window({self.train_window}) 必须 >= min_train({self.min_train})"
            )
        if self.oos_window < self.min_oos:
            raise ValueError(
                f"oos_window({self.oos_window}) 必须 >= min_oos({self.min_oos})"
            )


@dataclass(frozen=True)
class WindowSpec:
    """单个滚动窗口定义（纯数据，无 IO）。"""

    window_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    n_train: int
    n_oos: int
    valid: bool
    skip_reason: str | None = None


@dataclass(frozen=True)
class WindowResult:
    """单个样本外窗口的执行结果（只展示实际存在的指标）。

    说明：
    - 所有指标都仅针对 oos_start..oos_end 区间；
    - final_equity / cumulative_return / max_drawdown_* 可能是 None
      （样本内极端短、或权益不完整时不可计算）；
    - is_complete / incomplete_reasons 直接来自 BacktestResult.is_complete。
    """

    window_index: int
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    success: bool
    error: str | None
    initial_equity: float
    final_equity: float | None
    cumulative_return: float | None
    max_drawdown_amount: float | None
    max_drawdown_ratio: float | None
    trade_count: int
    order_count: int
    total_fees: float
    fee_source: str
    is_complete: bool
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardSummary:
    """跨窗口汇总（**窗口级统计**，不冒充连续持仓组合收益）。

    本类所有统计量都是「对每个窗口的独立结果做统计」，并不把各窗口的资金 /
    持仓串联起来计算组合复合收益。调用方 / 前端展示时**必须**明确告知这一点。
    """

    n_total_windows: int
    n_valid_windows: int
    n_skipped_windows: int
    n_failed_windows: int
    window_indices: tuple[int, ...]
    window_returns: tuple[float | None, ...]
    median_return: float | None
    positive_ratio: float | None
    median_max_drawdown_amount: float | None
    median_max_drawdown_ratio: float | None
    total_trades: int
    total_orders: int
    total_fees: float
    note: str


# =========================================================================== #
# 窗口生成
# =========================================================================== #


def generate_windows(
    index: pd.DatetimeIndex,
    cfg: WalkForwardConfig,
) -> list[WindowSpec]:
    """按时间序列生成一组训练 / 样本外窗口。

    输入：
        index: 交易日 DatetimeIndex（必须已升序、无重复、无时区）；
        cfg:   滚动验证配置。

    输出：
        WindowSpec 列表，按时间顺序排列；valid=False 的窗口会列出 skip_reason
        （数据长度不足等）。

    规则：
    - 严格单向：每个窗口的 train_end < oos_start；
    - 窗口之间按 step 推进（首窗口 index 0）；
    - 当尾部不足以构成完整样本外窗口时停止；
    - 相同 index + 相同 cfg ⇒ 完全相同的窗口列表（确定性）。
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError(f"index 必须是 DatetimeIndex，收到 {type(index)!r}")
    if index.tz is not None:
        raise ValueError("index 必须是无时区（tz-naive）的 DatetimeIndex")
    if not index.is_monotonic_increasing:
        raise ValueError("index 必须按日期升序排列")
    if index.has_duplicates:
        raise ValueError("index 不允许重复日期")
    if len(index) == 0:
        return []

    # 可选的整体边界裁剪
    start_idx = 0
    end_idx = len(index)  #  exclusive
    if cfg.start_date is not None:
        mask = index >= cfg.start_date
        if mask.any():
            start_idx = int(mask.argmax())
        else:
            return []  # 开始日期晚于所有数据
    if cfg.end_date is not None:
        mask = index <= cfg.end_date
        if mask.any():
            # 找到最后一个满足条件的索引位置 + 1
            end_idx = int(mask.sum())
            # argmax 返回第一个 True 的位置，对最后一个 True 用类似方式：
            last_true_pos = len(index) - 1 - int((~mask)[::-1].argmax())
            end_idx = last_true_pos + 1
        else:
            return []

    usable_index = index[start_idx:end_idx]
    n = len(usable_index)
    if n == 0:
        return []

    train_len = cfg.train_window
    oos_len = cfg.oos_window
    step = cfg.step
    warmup_len = cfg.warmup or 0

    windows: list[WindowSpec] = []
    idx = 0  # 窗口计数
    train_start = 0  # 以 usable_index 的位置编号表示

    while True:
        # 训练区间
        train_end = train_start + train_len  # exclusive
        # 样本外区间紧接在训练区间之后
        oos_start = train_end
        oos_end = oos_start + oos_len

        if oos_end > n:
            break  # 尾部不足以构成完整样本外窗口，停止

        train_dates = usable_index[train_start:train_end]
        oos_dates = usable_index[oos_start:oos_end]

        n_train = len(train_dates)
        n_oos = len(oos_dates)

        skip_reason: str | None = None
        valid = True
        if n_train < cfg.min_train:
            valid = False
            skip_reason = f"训练样本不足：{n_train} < min_train={cfg.min_train}"
        elif n_oos < cfg.min_oos:
            valid = False
            skip_reason = f"样本外样本不足：{n_oos} < min_oos={cfg.min_oos}"

        spec = WindowSpec(
            window_index=idx,
            train_start=train_dates[0],
            train_end=train_dates[-1],
            oos_start=oos_dates[0],
            oos_end=oos_dates[-1],
            n_train=n_train,
            n_oos=n_oos,
            valid=valid,
            skip_reason=skip_reason,
        )
        windows.append(spec)

        # 推进到下一个窗口
        train_start += step
        idx += 1

        # 防死循环：如果 step=0 或 step<0（已由 frozen __post_init__ 保证正整数）
        if step <= 0:
            break

    return windows


# =========================================================================== #
# 单窗口执行
# =========================================================================== #


def _slice_with_warmup(
    full_df: pd.DataFrame,
    spec: WindowSpec,
    warmup_days: int,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """拼接「训练尾部（预热）+ 样本外区间」的行情数据。

    返回 (combined_df, oos_start, oos_end)，其中 combined_df 只包含
    样本外区间开始之前 warmup_days 起，到样本外区间结束的数据。

    保证：oos_start..oos_end 完整包含在 combined_df.index 中；
    warmup 部分仅用于指标预热，不会计入窗口绩效（引擎按 config.start_date
    裁剪后只跑 oos 区间）。
    """
    full_index = full_df.index
    # 找 warmup 起点位置：oos_start 之前 warmup_days 行
    oos_pos = full_index.get_loc(spec.oos_start)
    if not isinstance(oos_pos, int):
        # 理论上 get_loc 在 DatetimeIndex 上返回 int，这里用 iloc 兜底
        pos_list = list(full_index.get_indexer_for([spec.oos_start]))
        if pos_list[0] == -1:
            raise ValueError(
                f"oos_start({spec.oos_start}) 不在行情 index 中，"
                "窗口生成和行情 index 不一致"
            )
        oos_pos = int(pos_list[0])

    warmup_start = max(0, oos_pos - warmup_days)
    oos_end_pos = full_index.get_loc(spec.oos_end)
    if not isinstance(oos_end_pos, int):
        pos_list = list(full_index.get_indexer_for([spec.oos_end]))
        oos_end_pos = int(pos_list[0])
    combined = full_df.iloc[warmup_start : oos_end_pos + 1].copy()
    return combined, spec.oos_start, spec.oos_end


def run_window(
    full_df: pd.DataFrame,
    strategy: "Strategy",
    base_config: BacktestConfig,
    spec: WindowSpec,
    *,
    warmup_days: int = 0,
) -> WindowResult:
    """执行单个样本外窗口，返回 WindowResult。

    参数：
        full_df:     完整历史行情（索引必须覆盖 spec.oos_start..spec.oos_end）；
        strategy:    固定参数的策略实例（本阶段不做参数选择）；
        base_config: 回测配置（initial_cash / 费用 / 市场规则等；start/end 会被覆盖）；
        spec:        窗口定义；
        warmup_days: 预热交易日数（0 表示不加预热）。

    注意：
    - 本函数**不修改** base_config；内部构造新的 config 实例；
    - 异常被捕获，记录到 WindowResult.error，不会向上抛出；
    - 若窗口无效（spec.valid=False）仍可调用，warmup 长度不足时引擎会抛异常 →
      WindowResult.success=False。
    """
    from deepquant.backtest.engine import run_backtest  # 局部导入避免顶层循环

    combined, oos_start, oos_end = _slice_with_warmup(full_df, spec, warmup_days)

    # 构造只针对样本外区间的 config（账户独立）
    import dataclasses
    new_config = dataclasses.replace(
        base_config,
        start_date=oos_start,
        end_date=oos_end,
    )

    try:
        result: "BacktestResult" = run_backtest(combined, strategy, new_config)
    except Exception as exc:  # noqa: BLE001 — 我们要把异常变成 result
        return WindowResult(
            window_index=spec.window_index,
            oos_start=spec.oos_start,
            oos_end=spec.oos_end,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            initial_equity=new_config.initial_cash,
            final_equity=None,
            cumulative_return=None,
            max_drawdown_amount=None,
            max_drawdown_ratio=None,
            trade_count=0,
            order_count=0,
            total_fees=0.0,
            fee_source=new_config.fee_source,
            is_complete=False,
            incomplete_reasons=("回测引擎执行失败",),
        )

    perf = result.performance
    trade_stats = perf.trade_stats

    return WindowResult(
        window_index=spec.window_index,
        oos_start=spec.oos_start,
        oos_end=spec.oos_end,
        success=True,
        error=None,
        initial_equity=new_config.initial_cash,
        final_equity=perf.final_equity,
        cumulative_return=perf.cumulative_return,
        max_drawdown_amount=perf.max_drawdown_amount,
        max_drawdown_ratio=perf.max_drawdown_ratio,
        trade_count=trade_stats.n_closed_trades,
        order_count=trade_stats.n_orders,
        total_fees=trade_stats.total_fees,
        fee_source=new_config.fee_source,
        is_complete=perf.is_complete,
        incomplete_reasons=tuple(perf.incomplete_reasons),
    )


# =========================================================================== #
# 跨窗口汇总
# =========================================================================== #


def summarize_results(results: list[WindowResult]) -> WalkForwardSummary:
    """对一批 WindowResult 生成窗口级统计汇总。

    统计量说明（**全部是窗口级独立指标的统计，不是连续账户的组合收益**）：
    - median_return：各窗口 cumulative_return 的中位数（仅统计非 None）；
    - positive_ratio：正收益窗口 / 有效窗口（success=True 且 final_equity 非 None）；
    - median_max_drawdown_amount：各窗口 max_drawdown_amount 中位数；
    - total_trades / orders / fees：仅累加到 success=True 的窗口。
    """
    n_total = len(results)
    valid_results = [r for r in results if r.success]
    failed = n_total - len(valid_results)

    returns = [r.cumulative_return for r in valid_results]
    # 计算中位数
    valid_returns = [v for v in returns if v is not None]
    median_return = float(pd.Series(valid_returns).median()) if valid_returns else None

    positive_count = sum(1 for v in valid_returns if v is not None and v > 0)
    positive_ratio = (positive_count / len(valid_returns)) if valid_returns else None

    drawdowns = [r.max_drawdown_amount for r in valid_results if r.max_drawdown_amount is not None]
    median_mdd = float(pd.Series(drawdowns).median()) if drawdowns else None
    drawdowns_ratio = [
        r.max_drawdown_ratio for r in valid_results
        if r.max_drawdown_ratio is not None and math.isfinite(r.max_drawdown_ratio)
    ]
    median_mdd_ratio = float(pd.Series(drawdowns_ratio).median()) if drawdowns_ratio else None

    total_trades = sum(r.trade_count for r in valid_results)
    total_orders = sum(r.order_count for r in valid_results)
    total_fees = sum(r.total_fees for r in valid_results)

    window_indices = tuple(r.window_index for r in valid_results)
    window_returns = tuple(returns)

    # 汇总说明：硬性标记窗口级统计 + 费用来源
    fee_sources = sorted({r.fee_source for r in valid_results}) or sorted(
        {r.fee_source for r in results}
    )
    note = (
        "窗口级统计，不是把所有窗口串成连续账户的组合收益；"
        f"费用来源：{fee_sources[0] if len(fee_sources) == 1 else fee_sources}。"
        + " 若 fee_source 为 simulation_placeholder，费用为模拟占位参数，"
        "不代表真实券商费率。"
    )

    return WalkForwardSummary(
        n_total_windows=n_total,
        n_valid_windows=len(valid_results),
        n_skipped_windows=0,  # skipped 在 window 生成时处理；传入本函数的都是跑过的
        n_failed_windows=failed,
        window_indices=window_indices,
        window_returns=window_returns,
        median_return=median_return,
        positive_ratio=positive_ratio,
        median_max_drawdown_amount=median_mdd,
        median_max_drawdown_ratio=median_mdd_ratio,
        total_trades=total_trades,
        total_orders=total_orders,
        total_fees=total_fees,
        note=note,
    )


# =========================================================================== #
# 高层入口：一次完整的固定参数 Walk-Forward 运行
# =========================================================================== #


def run_fixed_parameter_walkforward(
    full_df: pd.DataFrame,
    strategy: "Strategy",
    base_config: BacktestConfig,
    cfg: WalkForwardConfig,
    *,
    warmup_days: int | None = None,
) -> tuple[list[WindowSpec], list[WindowResult], WalkForwardSummary]:
    """执行完整的固定参数滚动样本外验证。

    参数：
        full_df:    完整历史行情 DatetimeIndex DataFrame；
        strategy:   固定参数的策略（本阶段不做参数选择）；
        base_config:基础回测配置（initial_cash / 费用 / 市场规则等）；
        cfg:        滚动验证配置（窗口长度 / 步长 / 边界）；
        warmup_days:预热天数；None 时使用 cfg.warmup（为 None 时默认 0）。

    返回 (specs, results, summary)。

    说明：
    - specs 包括 valid=True 和 valid=False 的窗口（后者 skip_reason 填充）；
    - 只对 valid=True 的窗口跑回测；
    - 这是纯函数：不写数据库、不持久化、不修改传入对象。
    """
    import dataclasses

    # 规范化 index（引擎已有 normalize_daily_data；这里只做基本检查，避免后面 generate_windows 抛难以理解的错误）
    if not isinstance(full_df.index, pd.DatetimeIndex):
        raise TypeError("full_df.index 必须是 DatetimeIndex")
    if full_df.index.tz is not None:
        raise ValueError("full_df.index 必须是无时区（tz-naive）")
    if not full_df.index.is_monotonic_increasing:
        raise ValueError("full_df.index 必须按日期升序排列")

    specs = generate_windows(full_df.index, cfg)

    effective_warmup = (
        warmup_days if warmup_days is not None else (cfg.warmup or 0)
    )

    results: list[WindowResult] = []
    for spec in specs:
        if not spec.valid:
            # 无效窗口仍记录占位结果（success=False），便于汇总统计
            results.append(WindowResult(
                window_index=spec.window_index,
                oos_start=spec.oos_start,
                oos_end=spec.oos_end,
                success=False,
                error=spec.skip_reason,
                initial_equity=base_config.initial_cash,
                final_equity=None,
                cumulative_return=None,
                max_drawdown_amount=None,
                max_drawdown_ratio=None,
                trade_count=0,
                order_count=0,
                total_fees=0.0,
                fee_source=base_config.fee_source,
                is_complete=False,
                incomplete_reasons=(spec.skip_reason,),
            ))
            continue
        results.append(run_window(
            full_df, strategy, base_config, spec, warmup_days=effective_warmup
        ))

    summary = summarize_results(results)
    # 修正 skipped 统计（在 specs 层面）
    summary = dataclasses.replace(
        summary,
        n_skipped_windows=sum(1 for s in specs if not s.valid),
        n_total_windows=len(specs),
    )
    return specs, results, summary
