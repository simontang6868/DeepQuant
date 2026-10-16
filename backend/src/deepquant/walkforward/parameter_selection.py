"""训练期参数选择器 + 真正的 Walk-Forward 验证（v1）。

核心原则（与 Step 7.1 对比）：
- Step 7.1：**固定参数**滚动样本外评估（每个窗口用同一套策略参数）；
- Step 7.2：**训练期参数选择 + 独立样本外评估**（每个窗口用自己的训练期数据
  比较候选参数、选出最优、再用该参数在该窗口独立 OOS 区间评估）。

本模块严格遵守：
1. **训练期和 OOS 期时间隔离**：train_start <= train_end < oos_start <= oos_end，
   两者不重叠；参数选择只能用训练期数据，**OOS 结果不参与参数选择**；
2. **每个窗口独立**：候选回测和 OOS 回测都使用独立策略实例、独立 BacktestConfig
   （含独立 initial_cash），窗口之间不传递现金 / 持仓；
3. **参数搜索边界固定**：候选集合在窗口开始前就完全确定，OOS 结果不会反过来
   扩展候选范围或调整评价指标权重；
4. **失败显式表达**：候选失败、数据不足、无可用参数等情况均返回明确状态，
   不会被静默当作最差收益后继续排序；
5. **纯函数**：不写数据库、不修改输入 DataFrame、不依赖隐式全局状态。

首版限制（明确记录）：
- 只支持 MaCrossStrategy；候选参数为 fast_window / slow_window 的有限网格；
- 不支持随机搜索、贝叶斯优化、遗传算法、机器学习调参；
- 不在本阶段修改 DB schema、不新增 HTTP 路由、不新增前端页面；
- 费用沿用 D6 模拟占位（fee_source="simulation_placeholder"）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.strategies.base import Strategy

if TYPE_CHECKING:
    from deepquant.strategies.ma_cross import MaCrossStrategy  # 仅类型注解


# =========================================================================== #
# 选择器配置与候选生成
# =========================================================================== #


@dataclass(frozen=True)
class GridSearchConfig:
    """网格搜索配置。

    fast_candidates / slow_candidates 必须均为正整数；最终候选集合会过滤掉
    slow <= fast 的非法组合。

    边界：max_combinations 防止意外消耗过多资源（例如 fast/slow 各 100 个 →
    10000 组合 → 窗口数 × 10000 回测）。超过时抛 ValueError。
    """

    fast_candidates: tuple[int, ...] = (3, 5, 10)
    slow_candidates: tuple[int, ...] = (20, 30, 50)
    min_train_periods: int = 60          # 至少的训练期交易日数
    min_oos_periods: int = 20            # 至少的 OOS 交易日数
    min_trades: int | None = None        # 最低已完成交易数（None 表示不限制）
    max_combinations: int = 50           # 候选组合硬上限


def generate_ma_cross_candidates(
    cfg: GridSearchConfig,
) -> list[dict]:
    """生成 MaCrossStrategy 的候选参数字典列表（过滤 slow <= fast）。

    结果是确定性的：相同 cfg → 相同候选列表（按 fast_window, slow_window 升序）。
    """
    fasts = sorted({int(x) for x in cfg.fast_candidates if isinstance(x, int) and x > 0})
    slows = sorted({int(x) for x in cfg.slow_candidates if isinstance(x, int) and x > 0})
    if len(fasts) == 0 or len(slows) == 0:
        raise ValueError("fast_candidates 和 slow_candidates 必须至少各有一个正整数")

    combos: list[dict] = []
    for f in fasts:
        for s in slows:
            if s > f:
                combos.append({"fast_window": f, "slow_window": s})

    if len(combos) == 0:
        raise ValueError(
            f"GridSearchConfig 未产生任何合法候选（所有 slow_candidates 均 <= fast_candidates）"
        )
    if len(combos) > cfg.max_combinations:
        raise ValueError(
            f"候选组合数 {len(combos)} 超过 max_combinations={cfg.max_combinations}，"
            "请缩小候选范围或提高 max_combinations"
        )
    return combos


def _max_window(candidates: list[dict], key: str) -> int:
    """从候选集合中找出某个参数的最大值，用于确定预热长度。"""
    return max(int(c[key]) for c in candidates)


# =========================================================================== #
# 训练期候选回测 + 评价
# =========================================================================== #


@dataclass
class CandidateTrainResult:
    """单个候选参数在训练期的回测结果。"""

    params: dict                       # {"fast_window": 5, "slow_window": 20}
    success: bool
    error: str | None                  # 异常或不可计算原因
    is_complete: bool | None           # None 表示未执行 / 执行异常
    cumulative_return: float | None    # None 表示不可计算
    max_drawdown_ratio: float | None
    n_closed_trades: int | None
    total_fees: float | None


def evaluate_candidates_on_train(
    train_df: pd.DataFrame,
    base_config: BacktestConfig,
    candidates: list[dict],
    *,
    warmup_days: int,
    strategy_factory,
    min_trades: int | None = None,
) -> list[CandidateTrainResult]:
    """对候选集合中的每一组参数，在训练期独立回测并计算评价指标。

    参数：
        train_df:       训练期行情（**已含预热尾部**，即调用方已做好「训练尾部预热
                        + 训练期」的拼接；本函数内部不再裁剪，直接交给引擎）；
        base_config:    基础回测配置（initial_cash / 费用 / 市场规则等；内部会用
                        dataclasses.replace 生成独立实例，不修改传入对象）；
        candidates:     候选参数字典列表；
        warmup_days:    预热交易日数（用于内部构造 config.start_date 之前的尾部；
                        若传入的 train_df 已经拼好预热段，warmup_days 只用于
                        计算 config.start_date = train_df.index[warmup_days]）；
        strategy_factory: callable(params_dict) -> Strategy
        min_trades:     最低已完成交易数（候选交易不足时标记为过滤失败）。

    重要：
    - 所有候选回测使用**相同**的训练期行情（含相同预热），确保公平比较；
    - 每个候选使用独立策略实例 + 独立 BacktestConfig（含相同 initial_cash）；
    - 候选之间的唯一差异是策略参数；
    - 异常捕获后返回 success=False，error 写明原因，不会中断整个候选列表。
    """
    import dataclasses as _dc

    results: list[CandidateTrainResult] = []
    train_start_pos = warmup_days  # 训练期起始位置（跳过预热段）
    train_index = train_df.index[train_start_pos:]
    if len(train_index) < 1:
        raise ValueError("训练期在跳过预热段后没有可用交易日")

    for params in candidates:
        try:
            strategy = strategy_factory(params)
        except Exception as exc:  # noqa: BLE001
            results.append(CandidateTrainResult(
                params=params, success=False,
                error=f"策略构造失败: {type(exc).__name__}: {exc}",
                is_complete=None, cumulative_return=None,
                max_drawdown_ratio=None, n_closed_trades=None, total_fees=None,
            ))
            continue

        config = _dc.replace(
            base_config,
            start_date=train_index[0],
            end_date=train_index[-1],
        )

        try:
            result = run_backtest(train_df, strategy, config)
        except Exception as exc:  # noqa: BLE001
            results.append(CandidateTrainResult(
                params=params, success=False,
                error=f"训练期回测失败: {type(exc).__name__}: {exc}",
                is_complete=None, cumulative_return=None,
                max_drawdown_ratio=None, n_closed_trades=None, total_fees=None,
            ))
            continue

        perf = result.performance
        n_trades = perf.trade_stats.n_closed_trades

        # 过滤条件：可选最低交易次数
        if min_trades is not None and n_trades < min_trades:
            results.append(CandidateTrainResult(
                params=params, success=False,
                error=f"已完成交易数 {n_trades} < min_trades={min_trades}",
                is_complete=perf.is_complete,
                cumulative_return=perf.cumulative_return,
                max_drawdown_ratio=perf.max_drawdown_ratio,
                n_closed_trades=n_trades,
                total_fees=perf.trade_stats.total_fees,
            ))
            continue

        # 有效性条件：权益必须可用（final_equity 非 None），否则该候选不可评价
        if perf.final_equity is None or not perf.is_complete:
            results.append(CandidateTrainResult(
                params=params, success=False,
                error=(
                    "训练期权益不完整或不可计算"
                    f"（is_complete={perf.is_complete}"
                    f", final_equity={perf.final_equity!r}"
                    + (f", reasons={perf.incomplete_reasons}" if perf.incomplete_reasons else "")
                    + "）"
                ),
                is_complete=perf.is_complete,
                cumulative_return=perf.cumulative_return,
                max_drawdown_ratio=perf.max_drawdown_ratio,
                n_closed_trades=n_trades,
                total_fees=perf.trade_stats.total_fees,
            ))
            continue

        results.append(CandidateTrainResult(
            params=params, success=True,
            error=None,
            is_complete=True,
            cumulative_return=perf.cumulative_return,
            max_drawdown_ratio=perf.max_drawdown_ratio,
            n_closed_trades=n_trades,
            total_fees=perf.trade_stats.total_fees,
        ))

    return results


# =========================================================================== #
# 排序与选中
# =========================================================================== #


def _sort_key(c: CandidateTrainResult, metric: str) -> tuple:
    """排序键。主排序按 metric 降序（收益高 / 回撤低），次排序按回撤升序 + 参数字典序。"""
    # 主排序：cumulative_return 越大越好；max_drawdown_ratio 越小越好
    if metric == "cumulative_return":
        primary = -(c.cumulative_return if c.cumulative_return is not None else -math.inf)
    elif metric == "max_drawdown_ratio":
        primary = (c.max_drawdown_ratio if c.max_drawdown_ratio is not None else math.inf)
    else:
        raise ValueError(f"未知选择指标 {metric!r}")
    # 次排序：回撤（越小越好）+ 参数字典序（deterministic tie-break）
    secondary = (
        c.max_drawdown_ratio if c.max_drawdown_ratio is not None else math.inf,
        tuple(sorted((k, v) for k, v in c.params.items())),
    )
    return (primary, secondary)


def select_best(
    candidates: list[CandidateTrainResult],
    *,
    metric: str = "cumulative_return",
) -> tuple[dict | None, list[CandidateTrainResult]]:
    """在训练期评价结果中选择最优参数。

    返回 (best_params, all_results)。若没有任何候选满足 success=True，best_params=None
    并返回完整候选列表（用于审计）。
    """
    valid = [c for c in candidates if c.success]
    if not valid:
        return None, candidates

    # 按 metric 主排序 + 回撤 + params 字典序打破平局
    sorted_valid = sorted(valid, key=lambda c: _sort_key(c, metric))
    best = sorted_valid[0]
    return best.params, candidates


# =========================================================================== #
# 单窗口完整流程
# =========================================================================== #


def _warmup_tail(df: pd.DataFrame, pos_start: int, warmup_days: int) -> pd.DataFrame:
    """取 df 中 [pos_start - warmup_days, pos_start) 段作为预热尾部。

    返回 DataFrame，索引从 pos_start - warmup_days 起，包含完整 warmup_days 行。
    若 pos_start - warmup_days < 0，则用 df.iloc[0:pos_start]（较短的一段，
    至少 1 行；调用方应在窗口生成时保证 warmup_days 合理）。
    """
    start = max(0, pos_start - warmup_days)
    return df.iloc[start:pos_start]


@dataclass
class WindowWFResult:
    """单个 Walk-Forward 窗口的完整结果（含训练期选择 + OOS 评估）。"""

    # --- 窗口基本信息 ---
    window_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    train_periods: int
    oos_periods: int
    warmup_days: int

    # --- 参数选择 ---
    candidates: tuple[dict, ...]
    selection_metric: str
    selected_params: dict | None
    selection_success: bool
    selection_error: str | None
    train_candidate_results: tuple[CandidateTrainResult, ...]

    # --- OOS 评估（仅当 selected_params 非 None 时才有，否则均为 None）---
    oos_success: bool | None
    oos_error: str | None
    final_equity: float | None
    cumulative_return: float | None
    max_drawdown_ratio: float | None
    trade_count: int | None
    order_count: int | None
    total_fees: float | None
    oos_is_complete: bool | None
    oos_incomplete_reasons: tuple[str, ...]

    # --- 元数据 ---
    fee_source: str
    strategy_name: str
    strategy_version: str


def run_walkforward_window(
    full_df: pd.DataFrame,
    base_config: BacktestConfig,
    *,
    window_index: int,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    oos_start: pd.Timestamp,
    oos_end: pd.Timestamp,
    strategy_factory,
    candidates: list[dict],
    selection_metric: str = "cumulative_return",
    min_trades: int | None = None,
    warmup_days: int = 0,
) -> WindowWFResult:
    """执行单个 Walk-Forward 窗口的完整流程：候选生成（已在调用方）→ 训练期评价
    → 选中参数 → OOS 独立回测。"""
    import dataclasses as _dc

    # --- 1. 定位行情位置 ---
    try:
        idx = full_df.index
        train_start_pos = int(idx.get_loc(train_start))
        train_end_pos = int(idx.get_loc(train_end))
        oos_start_pos = int(idx.get_loc(oos_start))
        oos_end_pos = int(idx.get_loc(oos_end))
    except KeyError as exc:
        return WindowWFResult(
            window_index=window_index,
            train_start=train_start, train_end=train_end,
            oos_start=oos_start, oos_end=oos_end,
            train_periods=0, oos_periods=0,
            warmup_days=warmup_days,
            candidates=tuple(candidates),
            selection_metric=selection_metric,
            selected_params=None, selection_success=False,
            selection_error=f"日期不在行情 index 中: {exc}",
            train_candidate_results=(),
            oos_success=None, oos_error=None,
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source=base_config.fee_source,
            strategy_name="", strategy_version="",
        )

    # --- 2. 训练期行情（含尾部预热） ---
    # warmup 段 = [train_start_pos - warmup_days, train_start_pos)
    # 训练期段 = [train_start_pos, train_end_pos + 1)
    warmup_tail = _warmup_tail(full_df, train_start_pos, warmup_days)
    train_segment = full_df.iloc[train_start_pos : train_end_pos + 1]
    train_with_warmup = pd.concat([warmup_tail, train_segment])

    # --- 3. 训练期评价 ---
    train_results = evaluate_candidates_on_train(
        train_with_warmup,
        base_config,
        candidates,
        warmup_days=len(warmup_tail),  # 实际预热行数 = train_start_pos - (train_start_pos - warmup_days) = warmup_days
        strategy_factory=strategy_factory,
        min_trades=min_trades,
    )

    selected, _ = select_best(train_results, metric=selection_metric)

    # --- 4. OOS 回测 ---
    if selected is None:
        return WindowWFResult(
            window_index=window_index,
            train_start=train_start, train_end=train_end,
            oos_start=oos_start, oos_end=oos_end,
            train_periods=int(train_end_pos - train_start_pos + 1),
            oos_periods=int(oos_end_pos - oos_start_pos + 1),
            warmup_days=warmup_days,
            candidates=tuple(candidates),
            selection_metric=selection_metric,
            selected_params=None, selection_success=False,
            selection_error="训练期所有候选均不满足选择条件（详见 train_candidate_results）",
            train_candidate_results=tuple(train_results),
            oos_success=None, oos_error=None,
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source=base_config.fee_source,
            strategy_name="", strategy_version="",
        )

    # OOS 行情：oos_start 之前的 warmup 段 + oos 段
    oos_warmup_tail = _warmup_tail(full_df, oos_start_pos, warmup_days)
    oos_segment = full_df.iloc[oos_start_pos : oos_end_pos + 1]
    oos_with_warmup = pd.concat([oos_warmup_tail, oos_segment])

    try:
        strategy = strategy_factory(selected)
    except Exception as exc:  # noqa: BLE001
        return WindowWFResult(
            window_index=window_index,
            train_start=train_start, train_end=train_end,
            oos_start=oos_start, oos_end=oos_end,
            train_periods=int(train_end_pos - train_start_pos + 1),
            oos_periods=int(oos_end_pos - oos_start_pos + 1),
            warmup_days=warmup_days,
            candidates=tuple(candidates),
            selection_metric=selection_metric,
            selected_params=selected, selection_success=True,
            selection_error=None,
            train_candidate_results=tuple(train_results),
            oos_success=False, oos_error=f"OOS 策略构造失败: {type(exc).__name__}: {exc}",
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source=base_config.fee_source,
            strategy_name=strategy.name if hasattr(strategy, "name") else "",
            strategy_version=strategy.version if hasattr(strategy, "version") else "",
        )

    oos_config = _dc.replace(
        base_config,
        start_date=oos_start,
        end_date=oos_end,
    )

    try:
        oos_result = run_backtest(oos_with_warmup, strategy, oos_config)
    except Exception as exc:  # noqa: BLE001
        return WindowWFResult(
            window_index=window_index,
            train_start=train_start, train_end=train_end,
            oos_start=oos_start, oos_end=oos_end,
            train_periods=int(train_end_pos - train_start_pos + 1),
            oos_periods=int(oos_end_pos - oos_start_pos + 1),
            warmup_days=warmup_days,
            candidates=tuple(candidates),
            selection_metric=selection_metric,
            selected_params=selected, selection_success=True,
            selection_error=None,
            train_candidate_results=tuple(train_results),
            oos_success=False,
            oos_error=f"OOS 回测失败: {type(exc).__name__}: {exc}",
            final_equity=None, cumulative_return=None,
            max_drawdown_ratio=None, trade_count=None, order_count=None,
            total_fees=None, oos_is_complete=None,
            oos_incomplete_reasons=(),
            fee_source=base_config.fee_source,
            strategy_name=strategy.name, strategy_version=strategy.version,
        )

    perf = oos_result.performance
    stats = perf.trade_stats
    return WindowWFResult(
        window_index=window_index,
        train_start=train_start, train_end=train_end,
        oos_start=oos_start, oos_end=oos_end,
        train_periods=int(train_end_pos - train_start_pos + 1),
        oos_periods=int(oos_end_pos - oos_start_pos + 1),
        warmup_days=warmup_days,
        candidates=tuple(candidates),
        selection_metric=selection_metric,
        selected_params=selected, selection_success=True,
        selection_error=None,
        train_candidate_results=tuple(train_results),
        oos_success=True, oos_error=None,
        final_equity=perf.final_equity,
        cumulative_return=perf.cumulative_return,
        max_drawdown_ratio=perf.max_drawdown_ratio,
        trade_count=stats.n_closed_trades,
        order_count=stats.n_orders,
        total_fees=stats.total_fees,
        oos_is_complete=perf.is_complete,
        oos_incomplete_reasons=tuple(perf.incomplete_reasons),
        fee_source=base_config.fee_source,
        strategy_name=strategy.name, strategy_version=strategy.version,
    )


# =========================================================================== #
# 跨窗口汇总
# =========================================================================== #


@dataclass
class WalkForwardSummaryV2:
    """训练期选择 + 独立 OOS 评估的跨窗口汇总。

    与 Step 7.1 不同：这里明确区分参数选择成功 / OOS 回测成功 / OOS 指标可用。
    语义：窗口级统计，不是把所有窗口串成连续账户的组合收益。
    """

    n_total_windows: int
    n_selection_success: int
    n_selection_failed: int
    n_oos_success: int           # 只在 selection_success 上统计
    n_oos_failed: int
    n_oos_with_valid_metrics: int  # final_equity 非 None 且 is_complete=True
    positive_ratio: float | None
    median_cumulative_return: float | None
    median_max_drawdown_ratio: float | None
    total_oos_trades: int
    total_oos_orders: int
    total_oos_fees: float
    param_distribution: dict       # {参数组合字符串: 选中次数}
    fee_source: str
    note: str


def summarize_v2(results: list[WindowWFResult]) -> WalkForwardSummaryV2:
    """跨窗口汇总（窗口级统计，不冒充组合收益）。"""
    n_total = len(results)
    sel_ok = [r for r in results if r.selection_success]
    sel_fail = [r for r in results if not r.selection_success]
    oos_ok = [r for r in sel_ok if r.oos_success]
    oos_fail = [r for r in sel_ok if not r.oos_success]
    valid_metric = [r for r in oos_ok if r.final_equity is not None and r.oos_is_complete]

    returns = [r.cumulative_return for r in valid_metric if r.cumulative_return is not None]
    median_ret = float(pd.Series(returns).median()) if returns else None
    pos_count = sum(1 for v in returns if v > 0)
    pos_ratio = (pos_count / len(returns)) if returns else None

    mdds = [r.max_drawdown_ratio for r in valid_metric
            if r.max_drawdown_ratio is not None and math.isfinite(r.max_drawdown_ratio)]
    median_mdd = float(pd.Series(mdds).median()) if mdds else None

    total_trades = sum(r.trade_count or 0 for r in oos_ok)
    total_orders = sum(r.order_count or 0 for r in oos_ok)
    total_fees = sum(r.total_fees or 0.0 for r in oos_ok)

    param_dist: dict[str, int] = {}
    for r in oos_ok:
        key = str(sorted((k, v) for k, v in (r.selected_params or {}).items()))
        param_dist[key] = param_dist.get(key, 0) + 1

    fee_sources = {r.fee_source for r in results}
    note = (
        "窗口级统计，不是把所有窗口串成连续账户的组合收益；"
        f"费用来源：{list(fee_sources)}。"
        + " 若 fee_source 为 simulation_placeholder，费用为模拟占位参数，"
        "不代表真实券商费率；训练期累计收益最高不等于风险调整后最优；"
        "候选网格搜索存在训练期过拟合风险。"
    )

    return WalkForwardSummaryV2(
        n_total_windows=n_total,
        n_selection_success=len(sel_ok),
        n_selection_failed=len(sel_fail),
        n_oos_success=len(oos_ok),
        n_oos_failed=len(oos_fail),
        n_oos_with_valid_metrics=len(valid_metric),
        positive_ratio=pos_ratio,
        median_cumulative_return=median_ret,
        median_max_drawdown_ratio=median_mdd,
        total_oos_trades=total_trades,
        total_oos_orders=total_orders,
        total_oos_fees=total_fees,
        param_distribution=param_dist,
        fee_source=list(fee_sources)[0] if len(fee_sources) == 1 else str(sorted(fee_sources)),
        note=note,
    )


# =========================================================================== #
# 高层入口：完整 Walk-Forward 运行
# =========================================================================== #


def run_walkforward_v2(
    full_df: pd.DataFrame,
    base_config: BacktestConfig,
    *,
    strategy_factory,
    grid_config: GridSearchConfig,
    selection_metric: str = "cumulative_return",
    window_config=None,  # Step 7.1 WalkForwardConfig 或等价对象
) -> tuple[list, list[WindowWFResult], WalkForwardSummaryV2]:
    """完整 Walk-Forward：窗口生成 → 参数选择 + OOS → 跨窗口汇总。

    本函数**不依赖 Step 7.1 的 run_fixed_parameter_walkforward**（逻辑独立），
    但窗口生成复用 WalkForwardConfig 的语义：train_window + oos_window + step。

    返回 (window_specs, window_results, summary)。
    """
    from deepquant.walkforward import WalkForwardConfig, generate_windows

    # --- 1. 候选生成 ---
    candidates = generate_ma_cross_candidates(grid_config)
    warmup_days = grid_config.min_train_periods  # 候选 slow_window 最大值（最朴素的统一预热长度）

    # --- 2. 窗口生成 ---
    if window_config is None:
        window_config = WalkForwardConfig(
            train_window=grid_config.min_train_periods,
            oos_window=grid_config.min_oos_periods,
            step=grid_config.min_oos_periods,
            min_train=grid_config.min_train_periods,
            min_oos=grid_config.min_oos_periods,
        )
    specs = generate_windows(full_df.index, window_config)

    results: list[WindowWFResult] = []
    for spec in specs:
        if not spec.valid:
            results.append(WindowWFResult(
                window_index=spec.window_index,
                train_start=spec.train_start, train_end=spec.train_end,
                oos_start=spec.oos_start, oos_end=spec.oos_end,
                train_periods=spec.n_train, oos_periods=spec.n_oos,
                warmup_days=warmup_days,
                candidates=tuple(candidates),
                selection_metric=selection_metric,
                selected_params=None, selection_success=False,
                selection_error=spec.skip_reason or "窗口无效",
                train_candidate_results=(),
                oos_success=None, oos_error=None,
                final_equity=None, cumulative_return=None,
                max_drawdown_ratio=None, trade_count=None, order_count=None,
                total_fees=None, oos_is_complete=None,
                oos_incomplete_reasons=(),
                fee_source=base_config.fee_source,
                strategy_name="", strategy_version="",
            ))
            continue

        results.append(run_walkforward_window(
            full_df, base_config,
            window_index=spec.window_index,
            train_start=spec.train_start, train_end=spec.train_end,
            oos_start=spec.oos_start, oos_end=spec.oos_end,
            strategy_factory=strategy_factory,
            candidates=candidates,
            selection_metric=selection_metric,
            min_trades=grid_config.min_trades,
            warmup_days=warmup_days,
        ))

    summary = summarize_v2(results)
    return specs, results, summary
