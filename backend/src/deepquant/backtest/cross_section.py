"""横截面候选集与动量信号（Gate 1 实现层）。

本模块只做一件事：把「某个信号日、哪些股票可以参与排序、各自的动量分数是多少」
按预登记 ``docs/GATE1_PREREGISTRATION.md`` 第 3~4 节写死的口径算出来。
它**不含**选股、权重、撮合、绩效——那些全部交给
``backtest/portfolio.py``（``equal_weight_targets`` / ``build_rebalance_schedule`` /
``run_portfolio_backtest``），避免出现第二套交易逻辑。

为什么口径必须写在这里而不是散在脚本里
--------------------------------------
预登记把 P1~P3 写成可核对的文字，代码是这些文字的唯一实现。若同一套规则在
「主实验」和「敏感性分析」里各写一遍，两边迟早会不一致，而敏感性分析的意义
恰恰是「只改一个变量」——底层口径漂移会让 12 组对照全部作废。

日历对齐的选择（会实质影响结果，必须写明）
------------------------------------------
每只股票的对齐基准是**全市场交易日历**（所有标的日期的并集），不是各股自身的
交易日序列。理由：
- 用自身序列，「过去 60 个交易日」对停牌股会跨越更长的自然时间，
  等于停牌越久看得越远——这不是我们想测的东西；
- 用并集日历，停牌就是那几天为 ``NaN``，由 P3 的覆盖率门槛统一处置，
  与预登记第 2 节「停牌只能用日期缺失近似」的声明一致。

窗口定义（与预登记第 3 节 P3 字面对齐）
--------------------------------------
``lookback=60, skip=5`` 时，窗口是日历上的 ``[t-64, t-5]`` 共 **60 个位置**，
动量 = ``close[t-5] / close[t-64] - 1``。两端点之间跨 59 个交易日间隔——
预登记 P3 写的是「60 个交易日历位置」，按其字面实现。
这一点在附录 B 有单独说明，避免将来把「60 位置」误读成「60 个间隔」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")


# --------------------------------------------------------------------------- #
# 剔除原因（固定字符串，便于汇总统计；不允许静默丢弃）
# --------------------------------------------------------------------------- #

REASON_OK = "ok"
REASON_NO_SIGNAL_ROW = "no_row_on_signal_date"
REASON_NOT_YET_LISTED = "not_yet_listed"
REASON_AFTER_LAST_ROW = "after_last_available_row"
REASON_NONPOSITIVE_PRICE = "nonpositive_price_on_signal_date"
REASON_ZERO_AMOUNT = "zero_amount_on_signal_date"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_INSUFFICIENT_COVERAGE = "insufficient_window_coverage"
REASON_ENDPOINT_MISSING = "endpoint_price_missing"
REASON_ENDPOINT_NONPOSITIVE = "endpoint_price_nonpositive"
REASON_SIGNAL_NOT_IN_CALENDAR = "signal_date_not_in_calendar"


@dataclass(frozen=True)
class CrossSectionConfig:
    """预登记第 3~4 节写死的口径参数。

    默认值就是主实验的值。敏感性分析只允许改这里列出的字段，
    改别的（比如对齐方式、剔除规则）不属于敏感性分析，属改变实验定义。
    """

    lookback: int = 60
    skip: int = 5
    min_valid_ratio: float = 0.90
    require_positive_amount: bool = True

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError(f"lookback 必须 >= 2，收到 {self.lookback}")
        if self.skip < 0:
            raise ValueError(f"skip 必须 >= 0，收到 {self.skip}")
        if self.skip >= self.lookback:
            raise ValueError(
                f"skip({self.skip}) 必须小于 lookback({self.lookback})，否则两端点位置重叠"
            )
        if not (0.0 < self.min_valid_ratio <= 1.0):
            raise ValueError(f"min_valid_ratio 必须在 (0, 1]，收到 {self.min_valid_ratio}")

    @property
    def window_length(self) -> int:
        """窗口覆盖的日历位置数（含两端）。60/5 → 60。"""
        return self.lookback

    @property
    def start_offset(self) -> int:
        """起点相对信号日的偏移（正数）。60/5 → 64。"""
        return self.skip + self.lookback - 1

    @property
    def min_valid_days(self) -> int:
        """窗口内至少需要多少个有效交易日。60 × 0.9 → 54。"""
        return math.ceil(self.window_length * self.min_valid_ratio)


# --------------------------------------------------------------------------- #
# 面板：一次对齐，多次取分
# --------------------------------------------------------------------------- #


def trading_calendar(prices: Mapping[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """所有标的日期的并集（升序去重）。

    用并集而不是交集：交集会把「某只股票延迟上市」的那些月份整段砍掉，
    等于用未来信息（它后来上市了）来决定样本期。
    """
    stamps: list[pd.Timestamp] = []
    for df in prices.values():
        if df is None or len(df) == 0:
            continue
        stamps.append(pd.DatetimeIndex(_dates_of(df)))
    if not stamps:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted(set().union(*[set(s) for s in stamps])))


def _dates_of(df: pd.DataFrame) -> pd.DatetimeIndex:
    """取日期索引：优先 ``date`` 列，其次 DataFrame 索引。"""
    if "date" in df.columns:
        return pd.DatetimeIndex(pd.to_datetime(df["date"]))
    return pd.DatetimeIndex(pd.to_datetime(df.index))


def _aligned_frame(df: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """把单只股票对齐到日历；非正价格置为 ``NaN``（等价于「当日不可用」）。

    非正价不是「极低价」，是复权缺陷的产物（实测 ``600809.SH`` 最低 −6.72）。
    把它当有效价格会算出符号翻转的收益率，所以这里必须置空，
    而不是保留原值等下游去过滤。
    """
    frame = df.copy()
    frame.index = _dates_of(frame)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.reindex(calendar)
    for col in PRICE_COLUMNS:
        if col in frame.columns:
            frame[col] = frame[col].where(frame[col] > 0)
    return frame


class CrossSectionPanel:
    """多标的行情面板：对齐一次，之后按任意信号日取分。

    构造时做全部对齐与合法性检查；之后每次 ``scores()`` 只做位置切片，
    这样 50 只 × 96 个月不会反复 reindex。
    """

    def __init__(
        self,
        prices: Mapping[str, pd.DataFrame],
        *,
        config: CrossSectionConfig | None = None,
        calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex | None = None,
    ):
        if not prices:
            raise ValueError("prices 为空：没有标的就没有横截面")
        self.config = config or CrossSectionConfig()
        if calendar is None:
            self.calendar = trading_calendar(prices)
        else:
            # 显式日历：回测区间固定、或只测单只股票时需要。
            # 若仍从数据推日历，删掉某一行会让整个日历平移，「信号日」随之漂移——
            # 那不是数据问题，是接口没给调用方控制权。
            self.calendar = pd.DatetimeIndex(pd.to_datetime(list(calendar))).sort_values()
            if self.calendar.has_duplicates:
                raise ValueError("显式 calendar 含重复日期")
        if len(self.calendar) == 0:
            raise ValueError("行情日历为空：所有标的都没有可用日期")

        missing_amount = [s for s, df in prices.items() if "amount" not in df.columns]
        if missing_amount and self.config.require_positive_amount:
            raise ValueError(
                f"缺少 amount 列，无法执行 P2 的成交额检查：{missing_amount[:5]}"
                "（若确需跳过该检查，显式设 require_positive_amount=False）"
            )

        self.symbols: tuple[str, ...] = tuple(prices.keys())
        self._frames = {s: _aligned_frame(df, self.calendar) for s, df in prices.items()}
        # 「这一天原本有没有这行」必须单独记：对齐后把非正价置成 NaN，
        # 「价格被复权缺陷污染」与「压根没有这一行（停牌/未上市）」会变成同一个值。
        # 不区分这两者，就没法回答「它到底为什么被剔除」。
        self._has_row: dict[str, pd.Series] = {}
        # 每只股票的首末可用日期：用来把「当时还没上市」与「停牌/数据缺口」分开。
        # 二者都是「当日无行」，但含义完全不同——一个是池子构建问题
        # （用 2026 年的成分回溯 2017 年，里面混着 2022 年才上市的公司），
        # 另一个是交易约束问题。混成一个原因就没法判断偏差来源。
        self._span: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
        for s, df in prices.items():
            dates = pd.DatetimeIndex(_dates_of(df))
            present = dates.intersection(self.calendar)
            mask = pd.Series(False, index=self.calendar)
            mask.loc[present] = True
            self._has_row[s] = mask
            self._span[s] = (dates.min(), dates.max())

    # --- 位置与窗口 ---

    def _position(self, signal_date: pd.Timestamp) -> int | None:
        if signal_date not in self.calendar:
            return None
        return int(self.calendar.get_loc(signal_date))

    def window_bounds(self, signal_date: pd.Timestamp) -> tuple[int, int] | None:
        """返回窗口的 ``[start, end]`` 位置（含两端）；历史不足返回 ``None``。"""
        pos = self._position(signal_date)
        if pos is None:
            return None
        end = pos - self.config.skip
        start = end - (self.config.window_length - 1)
        if start < 0:
            return None
        return start, end

    # --- 评估 ---

    def evaluate(
        self, signal_date: pd.Timestamp
    ) -> dict[str, tuple[float | None, str]]:
        """逐标的返回 ``(分数或 None, 原因)``。

        返回**全部**标的（不可用的也带原因），而不是只返回可用的那几个：
        只返回可用标的，等于让「谁被剔除、为什么」无从追查，而这正是
        B20 里「分母悄悄变小」的同款问题。
        """
        signal_date = pd.Timestamp(signal_date)
        out: dict[str, tuple[float | None, str]] = {}

        bounds = self.window_bounds(signal_date)
        if bounds is None:
            reason = (
                REASON_SIGNAL_NOT_IN_CALENDAR
                if self._position(signal_date) is None
                else REASON_INSUFFICIENT_HISTORY
            )
            return {s: (None, reason) for s in self.symbols}

        start, end = bounds
        win = self.calendar[start:end + 1]
        start_date, end_date = self.calendar[start], self.calendar[end]
        # 窗口内「有效交易日」= 四价齐全且为正
        win_frames = {
            s: self._frames[s].loc[win] for s in self.symbols
        }

        for s in self.symbols:
            out[s] = self._evaluate_one(s, signal_date, start_date, end_date, win_frames[s])
        return out

    def _evaluate_one(
        self,
        symbol: str,
        signal_date: pd.Timestamp,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        window_frame: pd.DataFrame,
    ) -> tuple[float | None, str]:
        cfg = self.config
        frame = self._frames[symbol]

        # --- P1 / P2：信号日必须可定价、可成交 ---
        first, last = self._span[symbol]
        if signal_date < first:
            return None, REASON_NOT_YET_LISTED
        if signal_date > last:
            return None, REASON_AFTER_LAST_ROW
        if not bool(self._has_row[symbol].get(signal_date, False)):
            return None, REASON_NO_SIGNAL_ROW
        row = frame.loc[signal_date]
        prices = [row.get(c) for c in PRICE_COLUMNS]
        # 走到这里说明「有这一行」；价格仍是 NaN 则要么原始缺失、要么非正
        # （_aligned_frame 已把 ≤0 置空），两者都归为「价格不可用」。
        if any(pd.isna(p) or float(p) <= 0 for p in prices):
            return None, REASON_NONPOSITIVE_PRICE
        if cfg.require_positive_amount:
            amount = row.get("amount")
            if pd.isna(amount) or float(amount) <= 0:
                return None, REASON_ZERO_AMOUNT

        # --- P3：窗口覆盖率 ---
        valid_mask = window_frame[list(PRICE_COLUMNS)].notna().all(axis=1)
        n_valid = int(valid_mask.sum())
        if n_valid < cfg.min_valid_days:
            return None, f"{REASON_INSUFFICIENT_COVERAGE}:{n_valid}/{cfg.window_length}"

        # --- 动量：两端点价格 ---
        p_end = window_frame.at[end_date, "close"] if end_date in window_frame.index else None
        p_start = window_frame.at[start_date, "close"] if start_date in window_frame.index else None
        if p_end is None or p_start is None or pd.isna(p_end) or pd.isna(p_start):
            return None, REASON_ENDPOINT_MISSING
        if float(p_start) <= 0 or float(p_end) <= 0:
            return None, REASON_ENDPOINT_NONPOSITIVE
        return float(p_end) / float(p_start) - 1.0, REASON_OK

    # --- 对外便捷方法 ---

    def scores(self, signal_date: pd.Timestamp) -> dict[str, float | None]:
        """``{symbol: 分数或 None}``，可直接喂给 ``equal_weight_targets``。"""
        return {s: v for s, (v, _) in self.evaluate(signal_date).items()}

    def reasons(self, signal_date: pd.Timestamp) -> dict[str, str]:
        """``{symbol: 剔除原因}``；可用标的的值为 ``"ok"``。"""
        return {s: r for s, (_, r) in self.evaluate(signal_date).items()}

    def scores_by_date(
        self, signal_dates: Sequence[pd.Timestamp]
    ) -> dict[pd.Timestamp, dict[str, float | None]]:
        return {pd.Timestamp(d): self.scores(pd.Timestamp(d)) for d in signal_dates}

    def exclusion_summary(
        self, signal_dates: Sequence[pd.Timestamp]
    ) -> dict[str, dict[str, int]]:
        """按信号日统计各剔除原因的标的数——用于确认「剔除是规则性的、不是随机的」。"""
        out: dict[str, dict[str, int]] = {}
        for d in signal_dates:
            key = str(pd.Timestamp(d).date())
            counts: dict[str, int] = {}
            for reason in self.reasons(pd.Timestamp(d)).values():
                bucket = reason.split(":", 1)[0]
                counts[bucket] = counts.get(bucket, 0) + 1
            out[key] = counts
        return out


# --------------------------------------------------------------------------- #
# 函数式封装
# --------------------------------------------------------------------------- #


def momentum_scores(
    prices: Mapping[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    *,
    config: CrossSectionConfig | None = None,
    panel: CrossSectionPanel | None = None,
) -> dict[str, float | None]:
    """单次取分。多次调用请自建 ``CrossSectionPanel`` 复用对齐结果。"""
    p = panel or CrossSectionPanel(prices, config=config)
    return p.scores(pd.Timestamp(signal_date))


def build_scores_by_date(
    prices: Mapping[str, pd.DataFrame],
    signal_dates: Sequence[pd.Timestamp],
    *,
    config: CrossSectionConfig | None = None,
) -> dict[pd.Timestamp, dict[str, float | None]]:
    """批量取分（复用同一次对齐）。"""
    panel = CrossSectionPanel(prices, config=config)
    return panel.scores_by_date(signal_dates)
