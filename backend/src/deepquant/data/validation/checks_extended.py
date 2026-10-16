"""行情数据校验 Step 7.4 扩展版。

在 Step 7.0 的基础检查（missing/empty/unparsable_date/unsorted/duplicate/missing_values/invalid_price/ohlc_relation/negative_volume）之上，新增：

A. 时序完整性（WARNING）
- consecutive_non_trading_gaps：交易日连续多日缺失（日历无法完全判断，
  但长于常规节假日周末的连续 gap 值得提示）。
- date_range_extent：请求范围 vs 实际覆盖（仅作参考，无交易日历）。

B. 市场数据异常（WARNING — 不自动拦截）
- price_jump：相邻收盘价显著跳变（阈值可配置，默认 15%）。涨跌停 / 除权除息
  会触发此告警，不自动拦截，留给人工判断。
- zero_or_near_zero_volume：成交量长期接近零（可能停牌 / 流动性枯竭）。

C. 复权与口径（WARNING + 元数据提示）
- adjust_status_unclear：数据源未明确声明复权口径。

严重级别保持两级：
- ERROR：已存在（会中止导入）。
- WARNING：可自动清洗 / 值得关注但不中止。

不新增"elevated"等第三级 —— 保持项目既有错误 / 警告二级语义。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deepquant.data.validation.checks import (
    ERROR,
    WARNING,
    ValidationIssue,
    ValidationReport,
    validate_daily as _validate_base,
)

# ---- 新检查的默认阈值（初始保守值，可配置） ----
DEFAULT_MAX_CONSECUTIVE_MISSING_TRADING_DAYS = 10
DEFAULT_PRICE_JUMP_THRESHOLD = 0.15   # 15%
DEFAULT_ZERO_VOLUME_RATIO_THRESHOLD = 0.5   # 成交量接近零的天数占比


def validate_daily_extended(
    df: pd.DataFrame,
    *,
    expected_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    max_consecutive_missing_days: int = DEFAULT_MAX_CONSECUTIVE_MISSING_TRADING_DAYS,
    price_jump_threshold: float = DEFAULT_PRICE_JUMP_THRESHOLD,
    zero_volume_ratio_threshold: float = DEFAULT_ZERO_VOLUME_RATIO_THRESHOLD,
    adjust_status: str | None = None,
) -> ValidationReport:
    """在基础校验之上叠加 Step 7.4 扩展检查。

    返回与基础校验合并的 ValidationReport。
    """
    report = _validate_base(df)

    # 基础检查已报告 ERROR 时，扩展检查仍然执行（但跳过依赖完整数据的部分）
    valid_dates = df["date"].dropna()
    if len(valid_dates) < 2:
        return report

    df_sorted = df.sort_values("date").reset_index(drop=True)
    dates = df_sorted["date"].dropna().reset_index(drop=True)

    # ---- A. 时序完整性 ----
    _check_temporal_gaps(report, dates, max_consecutive_missing_days)
    if expected_range is not None:
        _check_date_range_extent(report, dates, expected_range)

    # ---- B. 价格跳变 ----
    price_df = df_sorted[["close"]].astype("float64").dropna()
    if len(price_df) >= 2:
        _check_price_jumps(report, price_df, price_jump_threshold)

    # ---- 成交量异常 ----
    _check_volume_near_zero(report, df_sorted, zero_volume_ratio_threshold)

    # ---- C. 复权口径 ----
    if adjust_status is None or adjust_status == "unknown":
        report.issues.append(
            ValidationIssue(
                WARNING, "adjust_status_unclear",
                "数据源未明确声明复权口径（未复权 / 前复权 / 后复权）",
            )
        )

    return report


# --------------------------------------------------------------------------- #
# 分函数
# --------------------------------------------------------------------------- #


def _check_temporal_gaps(
    report: ValidationReport,
    dates: pd.Series,
    max_consecutive_missing_days: int,
) -> None:
    """检测相邻交易日之间的间隔。

    正常 A 股间隔 = 1 个交易日（跳过周末），典型 gap:
    - 周末：间隔 2（周五 → 周一）
    - 法定节假日：间隔 3–7+ 不等

    我们只能检测"连续出现的 gap 是否持续过长"—— 无法用交易日历精确判断。
    """
    deltas = dates.diff().dropna()  # 已是 Timedelta
    deltas_days = deltas.dt.days
    # 只看 gap > 2 的（跳过周末是正常的，超过则可能是节假日 / 数据源遗漏）
    long_gaps = deltas_days[deltas_days > 2]

    if len(long_gaps) == 0:
        return

    # 连续长 gap 的最大长度
    max_consec = 0
    cur = 0
    for g in deltas_days:
        if g > 2:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    if max_consec > max_consecutive_missing_days:
        report.issues.append(
            ValidationIssue(
                WARNING, "temporal_gap_too_long",
                f"存在 {max_consec} 个连续交易日间隔 > 2 天（可能是数据源遗漏 / 长时间停牌 / 长假）",
                count=max_consec,
            )
        )


def _check_date_range_extent(
    report: ValidationReport,
    dates: pd.Series,
    expected_range: tuple[pd.Timestamp, pd.Timestamp],
) -> None:
    """请求的日期范围 vs 实际覆盖。"""
    exp_start, exp_end = expected_range
    actual_start = dates.min()
    actual_end = dates.max()

    missing_head = (actual_start - exp_start).days
    missing_tail = (exp_end - actual_end).days

    if missing_head > 1:
        report.issues.append(
            ValidationIssue(
                WARNING, "date_range_missing_head",
                f"请求起始 {exp_start.date()} 前 {missing_head} 天无数据（实际起始 {actual_start.date()}）",
                count=missing_head,
            )
        )
    if missing_tail > 1:
        report.issues.append(
            ValidationIssue(
                WARNING, "date_range_missing_tail",
                f"请求结束 {exp_end.date()} 后 {missing_tail} 天无数据（实际结束 {actual_end.date()}）",
                count=missing_tail,
            )
        )


def _check_price_jumps(
    report: ValidationReport,
    close_series: pd.Series,
    threshold: float,
) -> None:
    """检测相邻收盘价相对变化超过阈值的情况。

    注意：A股涨跌停 10%（主板）/ 20%（创业板/科创板）是合法的。阈值 15%
    刚好在两者之上，大部分合法涨跌停不会触发，但连涨多日也会触发。
    所有跳变都只是 WARNING，交给人工判断。
    """
    pct_change = close_series.pct_change().dropna().abs()
    jumps = pct_change[pct_change > threshold]
    if len(jumps) > 0:
        max_jump_scalar = pct_change.max()
        # pandas 3.0: Series.max() 可能返回 scalar 或 Series，兼容两种情况
        if hasattr(max_jump_scalar, "iloc"):
            max_jump_val = float(max_jump_scalar.iloc[0])
        else:
            max_jump_val = float(max_jump_scalar)
        report.issues.append(
            ValidationIssue(
                WARNING, "price_jump",
                f"存在 {len(jumps)} 个相邻收盘价跳变 > {threshold:.0%}（最大 {max_jump_val:.2%}，可能是涨跌停 / 除权除息 / 数据源异常）",
                count=int(len(jumps)),
            )
        )


def _check_volume_near_zero(
    report: ValidationReport,
    df: pd.DataFrame,
    threshold_ratio: float,
) -> None:
    """检测成交量接近零的天数占比。"""
    if "volume" not in df.columns:
        return
    vol = df["volume"].astype("float64").dropna()
    if len(vol) == 0:
        return
    # 接近零：< 当日均值的 1% 且绝对值 < 100（避免除零）
    mean_vol = float(vol.mean())
    near_zero = vol[(vol < max(mean_vol * 0.01, 100)) & (vol > 0)]
    ratio = len(near_zero) / len(vol)
    if ratio > threshold_ratio:
        report.issues.append(
            ValidationIssue(
                WARNING, "zero_or_near_zero_volume",
                f"有 {ratio:.1%} 的交易日成交量接近零（可能停牌 / 流动性枯竭）",
                count=int(len(near_zero)),
            )
        )
