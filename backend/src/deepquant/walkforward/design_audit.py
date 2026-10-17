"""研究设计审计：参与率口径、一手可负担性、窗口/网格算术约束。

本模块是 **B 线诊断产物**，只做三件事：
1. 把「零参与窗口」从静默的收益 0 里分离出来，变成可报告的口径；
2. 检查标的在给定本金下是否买得起一手（A 股整手约束）；
3. 给出窗口/网格/历史长度的**算术事实**（窗口数、IS:OOS 比、历史年数）。

**本模块不做的三件事**（有意为之，不是没做）：
- 不推荐参数值。任何"该选多少"的判断都会引入 OOS 收益信息，违反 R2 / R11。
- 不估计窗口内的交易事件数。事件数取决于实际价格路径，
  **无法从参数算术推导**；要得到它必须实测（见 docs/DECISION_RULING_2026-10-17.md）。
- 不修改任何既有行为。全部为纯函数，无副作用。

红线提醒（实现时请务必保留，不要当作过时注释删掉）：
- 参与率（participation_rate）是**数量**指标，**不是质量**指标。
  实测（2018-2024 真实行情，MA 交叉，30 窗口）：提高 min_trades 使参与率
  从 56.7% 升到 73.3%，同期「剔除零参与后的收益中位数」从 -0.002566 恶化到
  -0.022492 —— **两者反向运动**。
  → 因此参与率**禁止**作为任何择优流程的判据。
- 「仅参与窗口」（active-only）口径同样禁止用于择优：
  哪些窗口算"参与"是结果出来之后才知道的，用它等于自选样本。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ParticipationSummary",
    "summarize_participation",
    "AffordabilityResult",
    "check_affordability",
    "GridDesignFacts",
    "check_grid_design",
    "A_SHARE_LOT_SIZE",
    "EXTERNAL_MIN_HISTORY_YEARS",
]

# A 股主板/创业板买入最小申报单位（沪深交易所交易规则 3.3.8）。
# 科创板为 200 股，本模块默认按 100 股处理；需要时由调用方显式传入。
A_SHARE_LOT_SIZE = 100

# 外部惯例门槛：有意义的 walk-forward 至少需要 8 年日线
# （来源：TradingView《Walk-Forward Testing and Out-of-Sample Validation》）。
# 这里只用于**标注事实**，不用于推荐任何配置。
EXTERNAL_MIN_HISTORY_YEARS = 8.0

# 一年交易日近似值，仅用于把"交易日数"换算成"年数"以便对照外部惯例。
_TRADING_DAYS_PER_YEAR = 244


# =========================================================================== #
# 1. 参与率口径
# =========================================================================== #


@dataclass(frozen=True)
class ParticipationSummary:
    """walk-forward 结果的参与率口径汇总。

    字段分两组，**用途不同，不得混用**：

    主口径（对外报告唯一口径，按全部窗口计算）：
        n_windows / median_cumulative_return / mean_cumulative_return / positive_ratio

    参与口径（并列披露，用于解释主口径为什么长这样）：
        n_selected / n_no_selection / n_with_trades / n_zero_trade /
        participation_rate / sum_trades

    诊断列（**禁止**用于择优，见模块 docstring）：
        median_return_active / n_active
    """

    # —— 主口径 ——
    n_windows: int
    median_cumulative_return: float | None
    mean_cumulative_return: float | None
    positive_ratio: float | None

    # —— 参与口径 ——
    n_selected: int
    n_no_selection: int
    n_with_trades: int
    n_zero_trade: int
    participation_rate: float
    sum_trades: int

    # —— 诊断列（禁止用于择优）——
    median_return_active: float | None
    n_active: int

    @property
    def sample_too_small(self) -> bool:
        """参与率过低时，任何收益分位数都不具备评价意义。

        参与率低于 50% 意味着过半窗口根本没参与市场，此时收益中位数衡量的是
        **参与频率**而不是交易质量。
        """
        return self.participation_rate < 0.5


def _is_selected(r) -> bool:
    """窗口是否选出了候选参数（未被 min_trades 全部过滤）。"""
    params = getattr(r, "selected_params", None)
    if params is None:
        return False
    if isinstance(params, str):
        return params.strip() != ""
    if isinstance(params, dict):
        return len(params) > 0
    return True


def summarize_participation(results) -> ParticipationSummary:
    """汇总 walk-forward 结果的参与情况。

    参数：
        results: WindowWFResult 序列（本模块不做类型耦合，只依赖属性）。

    本函数刻意**不**提供任何"更好/更差"的判断——它只把事实摊开。
    """
    results = list(results)

    n_windows = len(results)
    n_selected = sum(1 for r in results if _is_selected(r))
    n_no_selection = n_windows - n_selected

    trade_counts = [(getattr(r, "trade_count", None) or 0) for r in results]
    n_with_trades = sum(1 for c in trade_counts if c > 0)
    n_zero_trade = n_windows - n_with_trades
    sum_trades = sum(trade_counts)

    rets = [r.cumulative_return for r in results if r.cumulative_return is not None]

    # 主口径：全部参与统计的窗口（ret 非 None），零收益窗口照常计入。
    #
    # 注意：n_no_selection 与 ret=None 是**重叠**关系（无选中 → ret 为 None），
    # 二者不可相加，否则会重复计数。这里只报「无选中」这一个口径。
    if rets:
        median = _median(rets)
        mean = sum(rets) / len(rets)
        positive_ratio = sum(1 for x in rets if x > 0) / len(rets)
    else:
        median = mean = positive_ratio = None

    # 诊断列：剔除收益恰为 0 的窗口。
    # ⚠️ 禁止用于择优 —— 哪些窗口收益为 0 是结果出来后才知道的。
    active = [x for x in rets if x != 0]

    return ParticipationSummary(
        n_windows=n_windows,
        median_cumulative_return=median,
        mean_cumulative_return=mean,
        positive_ratio=positive_ratio,
        n_selected=n_selected,
        n_no_selection=n_no_selection,
        n_with_trades=n_with_trades,
        n_zero_trade=n_zero_trade,
        participation_rate=(n_with_trades / n_windows) if n_windows else 0.0,
        sum_trades=sum_trades,
        median_return_active=_median(active) if active else None,
        n_active=len(active),
    )


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


# =========================================================================== #
# 2. 一手可负担性
# =========================================================================== #


@dataclass(frozen=True)
class AffordabilityResult:
    """单标的的整手可负担性检查结果。"""

    price: float
    initial_cash: float
    lot_size: int
    lot_cost: float
    tradable: bool

    @property
    def reason(self) -> str:
        if self.tradable:
            return (
                f"1 手 {self.lot_size} 股需 {self.lot_cost:,.2f} "
                f"≤ 本金 {self.initial_cash:,.2f}"
            )
        return (
            f"1 手 {self.lot_size} 股需 {self.lot_cost:,.2f} "
            f"> 本金 {self.initial_cash:,.2f} → 在 A 股无法成交"
        )


def check_affordability(
    price: float,
    initial_cash: float,
    *,
    lot_size: int = A_SHARE_LOT_SIZE,
) -> AffordabilityResult:
    """检查给定价格下，本金是否买得起一手。

    判据只有一个：**price × lot_size ≤ initial_cash**。
    这里刻意**没有**可调的百分比阈值——"买得起"是一件二值的事，
    不需要拍一个 X%，因此也不存在借它调参的空间。

    依据：沪深交易所交易规则 3.3.8，竞价交易买入须为 100 股（科创板 200 股）
    或其整数倍。因此"买 80 股茅台"在 A 股是不存在的操作，
    回测里允许它等于回测了无法执行的交易。
    """
    if price is None or price <= 0:
        raise ValueError(f"price 必须为正数，收到 {price!r}")
    if initial_cash is None or initial_cash <= 0:
        raise ValueError(f"initial_cash 必须为正数，收到 {initial_cash!r}")
    if lot_size <= 0:
        raise ValueError(f"lot_size 必须为正整数，收到 {lot_size!r}")

    lot_cost = price * lot_size
    return AffordabilityResult(
        price=float(price),
        initial_cash=float(initial_cash),
        lot_size=int(lot_size),
        lot_cost=lot_cost,
        tradable=lot_cost <= initial_cash,
    )


# =========================================================================== #
# 3. 窗口 / 网格 / 历史的算术约束
# =========================================================================== #


@dataclass(frozen=True)
class GridDesignFacts:
    """研究设计的算术事实。

    **全部字段都是算术结果，不含任何推荐**。
    本结构刻意不提供"建议的 OOS 长度"或"建议的最慢慢线"——
    那需要引入收益信息，违反 R2 / R11。
    """

    oos_periods: int
    is_periods: int
    max_slow: int
    history_periods: int
    n_windows: int
    is_oos_ratio: float
    history_years: float
    meets_external_history_threshold: bool


def check_grid_design(
    *,
    oos_periods: int,
    is_periods: int,
    max_slow: int,
    history_periods: int,
) -> GridDesignFacts:
    """计算研究设计的算术事实。

    给出的是**约束关系**，不是建议：

    - `n_windows`：给定历史长度下能切出多少个 walk-forward 窗口；
      窗口太少（外部惯例认为 < 5–10 个）时汇总统计意义有限。
    - `is_oos_ratio`：外部惯例落在 4–10:1；本函数只报数字。
    - `history_years` / `meets_external_history_threshold`：
      对照外部惯例的 8 年日线门槛。

    **本函数刻意不估计"窗口内会发生几次交易"**。
    交易事件数取决于实际价格路径，无法从 (oos_periods, max_slow) 算术推出。
    要得到它必须实测——这一点在模块 docstring 与决策文档中均已声明。
    """
    for name, v in (
        ("oos_periods", oos_periods),
        ("is_periods", is_periods),
        ("max_slow", max_slow),
        ("history_periods", history_periods),
    ):
        if not isinstance(v, int) or v <= 0:
            raise ValueError(f"{name} 必须为正整数，收到 {v!r}")

    if history_periods < is_periods + oos_periods:
        n_windows = 0
    else:
        n_windows = (history_periods - is_periods) // oos_periods

    history_years = history_periods / _TRADING_DAYS_PER_YEAR

    return GridDesignFacts(
        oos_periods=oos_periods,
        is_periods=is_periods,
        max_slow=max_slow,
        history_periods=history_periods,
        n_windows=n_windows,
        is_oos_ratio=is_periods / oos_periods,
        history_years=history_years,
        meets_external_history_threshold=(
            history_years >= EXTERNAL_MIN_HISTORY_YEARS
        ),
    )
