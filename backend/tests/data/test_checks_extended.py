"""A10：给 `data/validation/checks_extended.py` 补覆盖（74% → ≥90%），并固化 `price_jump` 计数 bug。

**只新增测试，不改任何源码**（R7；修 bug 属判断类，交回 B 线提案）。

## 关于 price_jump 计数缺陷（本文件的核心价值）

`validate_daily_extended` 里取收盘价用的是**双方括号**：

    price_df = df_sorted[["close"]]      # -> DataFrame，不是 Series

于是 `_check_price_jumps` 里这一行走的是 **DataFrame 布尔掩码语义**：

    jumps = pct_change[pct_change > threshold]

对 Series，`s[s > t]` 返回**过滤后的子集**，`len()` = 违规个数；
对 DataFrame，`df[df > t]` 返回**同形、False 处置 NaN 的 DataFrame**，`len()` = **行数**。

后果（已实测，见下方常量）：
1. `count` 报的是「行数 - 1」而不是真实违规次数；
2. 更严重：掩码 DataFrame 的 `len()` 恒 > 0，**即使一行跳变都没有也会误报** price_jump。

下面用两个 `xfail(strict=True)` 把这两条固化成可执行证据：
现在它们是「预期失败」（套件保持绿），一旦有人修好，XPASS + strict 会直接报错，
**强制删除 xfail 标记** —— 缺陷不会悄悄溜走，也不会被伪装修好。

最小复现自包含（不依赖任何外部 CSV）：100 行、3 处跳变，真实超阈值 5 次，count 报 99。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.data.validation.checks import ERROR, ValidationReport
from deepquant.data.validation.checks_extended import (
    _check_date_range_extent,
    _check_price_jumps,
    _check_temporal_gaps,
    _check_volume_near_zero,
    validate_daily_extended,
)

# ---- 最小复现的常量（实测值，勿改） ----
REPRO_ROWS = 100
TRUE_JUMP_COUNT = 5      # 真实超阈值次数
BUGGY_COUNT = 99         # 当前实现报告的 count（= 行数 - 1）
JUMP_THRESHOLD = 0.15


# --------------------------------------------------------------------------- #
# 构造工具
# --------------------------------------------------------------------------- #

def _repro_closes() -> list[float]:
    """100 行、3 处跳变（2 处双向 + 1 处单向）→ 真实超阈值 5 次。

    事件 A（idx 20/21）：100 → 150（+50%）→ 100（−33.3%）   2 次
    事件 B（idx 40/41）：100 → 160（+60%）→ 100（−37.5%）   2 次
    事件 C（idx 70）  ：100 → 170（+70%）后走平             1 次
    """
    closes: list[float] = []
    closes += [100.0] * 20        # idx 0..19
    closes += [150.0, 100.0]      # idx 20, 21
    closes += [100.0] * 18        # idx 22..39
    closes += [160.0, 100.0]      # idx 40, 41
    closes += [100.0] * 28        # idx 42..69
    closes += [170.0]             # idx 70
    closes += [170.0] * 29        # idx 71..99
    assert len(closes) == REPRO_ROWS
    return closes


def _frame(closes: list[float], *, volume: float = 1_000_000.0,
           start: str = "2024-01-01") -> pd.DataFrame:
    """用给定收盘价构造合法日线表（OHLC 取等值以满足关系校验，成交量为正）。"""
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.bdate_range(start, periods=n),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [float(volume)] * n,
        }
    )


def _flat_frame(n: int = 60, price: float = 100.0) -> pd.DataFrame:
    return _frame([price] * n)


# --------------------------------------------------------------------------- #
# validate_daily_extended：入口与早退
# --------------------------------------------------------------------------- #

def test_clean_data_is_valid_without_errors():
    """干净数据（走平价、正常量、已声明复权口径）不应产生任何 ERROR。

    注意：受下方 xfail 记录的已知缺陷影响，此处**仍会**冒出一条 price_jump WARNING
    （实测：60 行走平价报「59 个跳变 > 15%，最大 0.00%」——59 = 行数-1，最大幅度 0%，
    自相矛盾，正是缺陷的直观证据）。因此这里只断言无任何 ERROR、整体 is_valid，
    「不应产生 price_jump」这一正确行为由下面那条 xfail 单独固化。
    """
    rep = validate_daily_extended(_flat_frame(), adjust_status="qfq")
    assert rep.is_valid
    assert rep.error_codes() == []


def test_single_row_returns_early_without_extended_checks():
    """不足 2 个有效日期时提前返回，不做任何扩展检查。"""
    rep = validate_daily_extended(_flat_frame(n=1))
    assert rep.n_rows == 1
    for code in ("price_jump", "temporal_gap_too_long", "zero_or_near_zero_volume"):
        assert not rep.has_code(code)


def test_empty_frame_reports_base_empty_error():
    """空表由基础校验报 empty（ERROR），并提前返回。"""
    rep = validate_daily_extended(_flat_frame(n=0))
    assert rep.has_code("empty")
    assert rep.error_codes() == ["empty"]
    assert not rep.is_valid


def test_adjust_status_missing_or_unknown_emits_warning():
    """未声明或声明为 unknown 的复权口径 → adjust_status_unclear（WARNING）。"""
    for status in (None, "unknown"):
        rep = validate_daily_extended(_flat_frame(n=10), adjust_status=status)
        issue = rep.get("adjust_status_unclear")
        assert issue is not None
        assert issue.level != ERROR


def test_adjust_status_known_emits_no_warning():
    """已明确声明复权口径（如 qfq）时不报 adjust_status_unclear。"""
    rep = validate_daily_extended(_flat_frame(n=10), adjust_status="qfq")
    assert not rep.has_code("adjust_status_unclear")


# --------------------------------------------------------------------------- #
# _check_temporal_gaps
# --------------------------------------------------------------------------- #

def test_temporal_gaps_none_when_business_days():
    """连续交易日（仅周末间隔）不触发 temporal_gap_too_long。"""
    rep = ValidationReport()
    _check_temporal_gaps(rep, pd.Series(pd.bdate_range("2024-01-01", periods=40)), 10)
    assert not rep.has_code("temporal_gap_too_long")


def test_temporal_gaps_all_consecutive_returns_early():
    """全部相邻间隔 <= 2 天（无任何长 gap）→ 函数提前 return，不产生问题。

    覆盖 `long_gaps` 为空的早退分支（bdate_range 含周末 3 天间隔，走不到这里）。
    """
    dates = pd.Series(pd.date_range("2024-01-01", periods=12, freq="D"))  # 间隔全为 1 天
    rep = ValidationReport()
    _check_temporal_gaps(rep, dates, 10)
    assert rep.issues == []


def test_temporal_gaps_below_threshold_no_issue():
    """连续长间隔数未超过阈值（3 <= 10）→ 不报。"""
    dates = pd.Series(pd.date_range("2024-01-01", periods=5, freq="5D"))
    rep = ValidationReport()
    _check_temporal_gaps(rep, dates, 10)
    assert not rep.has_code("temporal_gap_too_long")


def test_temporal_gaps_above_threshold_reports_max_consecutive():
    """连续长间隔数超过阈值 → 报 WARNING，count 为最大连续长度。"""
    dates = pd.Series(pd.date_range("2024-01-01", periods=20, freq="5D"))  # 19 个连续长 gap
    rep = ValidationReport()
    _check_temporal_gaps(rep, dates, 10)
    issue = rep.get("temporal_gap_too_long")
    assert issue is not None
    assert issue.count == 19
    assert "19" in issue.message


# --------------------------------------------------------------------------- #
# _check_date_range_extent
# --------------------------------------------------------------------------- #

def test_range_missing_head():
    """实际起始晚于请求起始 > 1 天 → date_range_missing_head。"""
    dates = pd.Series(pd.bdate_range("2024-02-01", periods=10))
    rep = ValidationReport()
    _check_date_range_extent(
        rep, dates, (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-14"))
    )
    assert rep.get("date_range_missing_head") is not None


def test_range_missing_tail():
    """实际结束早于请求结束 > 1 天 → date_range_missing_tail。"""
    dates = pd.Series(pd.bdate_range("2024-01-01", periods=10))
    rep = ValidationReport()
    _check_date_range_extent(
        rep, dates, (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-03-01"))
    )
    assert rep.get("date_range_missing_tail") is not None


def test_range_within_bounds_no_issue():
    """头尾缺口均 <= 1 天 → 不报任何 range 问题。"""
    dates = pd.Series(pd.bdate_range("2024-01-01", periods=10))
    rep = ValidationReport()
    _check_date_range_extent(
        rep, dates, (pd.Timestamp("2023-12-31"), pd.Timestamp("2024-01-13"))
    )
    assert rep.issues == []


def test_expected_range_is_honored_end_to_end():
    """经公开入口传入 expected_range 时，range 检查确实生效。"""
    df = _flat_frame(n=10)
    rep = validate_daily_extended(
        df,
        expected_range=(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-06-01")),
        adjust_status="qfq",
    )
    assert rep.has_code("date_range_missing_head") or rep.has_code("date_range_missing_tail")


# --------------------------------------------------------------------------- #
# _check_price_jumps
# --------------------------------------------------------------------------- #

def test_price_jump_issue_is_warning_not_error():
    """price_jump 只是 WARNING（涨跌停/除权除息合法，交人工判断），不中止导入。"""
    rep = validate_daily_extended(_frame(_repro_closes()))
    issue = rep.get("price_jump")
    assert issue is not None
    assert issue.level != ERROR
    assert rep.is_valid


def test_price_jumps_message_reports_max_jump():
    """消息里带最大跳变幅度（复现数据里是 70%）。"""
    rep = validate_daily_extended(_frame(_repro_closes()))
    assert "70.00%" in rep.get("price_jump").message


def test_check_price_jumps_with_series_gives_correct_count():
    """**根因隔离证据**：直接传 Series（正确语义）时，函数本身算出的 count 是对的。

    这条**通过**，说明缺陷不在 `_check_price_jumps` 内部，而在调用方
    `validate_daily_extended` 传了 DataFrame（双方括号）而非 Series。
    同时覆盖了 `max()` 返回标量的那条分支。
    """
    rep = ValidationReport()
    _check_price_jumps(rep, pd.Series(_repro_closes(), dtype="float64"), JUMP_THRESHOLD)
    issue = rep.get("price_jump")
    assert issue is not None
    assert issue.count == TRUE_JUMP_COUNT


def test_check_price_jumps_with_series_no_jump_no_issue():
    """传 Series 且无跳变时不产生 issue（对照：走 DataFrame 时会误报，见下方 xfail）。"""
    rep = ValidationReport()
    _check_price_jumps(rep, pd.Series([100.0] * 50, dtype="float64"), JUMP_THRESHOLD)
    assert not rep.has_code("price_jump")


# ---- 缺陷固化：两条 xfail(strict=True) ----

@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知缺陷（只测不修，修 bug 属判断类，交回 B 线）："
        "validate_daily_extended 里 df_sorted[[\"close\"]] 取到的是 DataFrame，"
        "使 _check_price_jumps 内 `pct_change[pct_change > threshold]` 走 DataFrame 布尔掩码语义"
        "（返回同形 NaN 填充的 DataFrame，而非过滤后的子集），len(jumps) 因此等于行数-1。"
        f"最小复现：{REPRO_ROWS} 行、3 处跳变，真实超阈值 {TRUE_JUMP_COUNT} 次，却报告 count={BUGGY_COUNT}。"
        "修复后本测试 XPASS，strict=True 会令其报错，强制移除本标记。"
    ),
)
def test_price_jump_count_should_equal_true_violations():
    """count 应等于真实超阈值次数（5），而非行数-1（99）。"""
    rep = validate_daily_extended(_frame(_repro_closes()))
    issue = rep.get("price_jump")
    assert issue is not None
    assert issue.count == TRUE_JUMP_COUNT


@pytest.mark.xfail(
    strict=True,
    reason=(
        "同一缺陷的更严重后果：掩码 DataFrame 的 len() 恒 > 0，"
        "因此**即使零跳变也会误报 price_jump**（count = 行数-1）。"
        "这正是历史研究 JSON 里「1594 行 1593 个跳变」= 行数-1 的由来——那不是真实跳变数。"
        "修复后本测试 XPASS，strict=True 会令其报错，强制移除本标记。"
    ),
)
def test_flat_prices_should_not_report_price_jump():
    """全程走平（零跳变）不应产生 price_jump。"""
    rep = validate_daily_extended(_flat_frame(n=100), adjust_status="qfq")
    assert not rep.has_code("price_jump")


# --------------------------------------------------------------------------- #
# _check_volume_near_zero
# --------------------------------------------------------------------------- #

def test_volume_near_zero_detected():
    """多数交易日成交量接近零（占比 > 阈值）→ zero_or_near_zero_volume。"""
    df = _flat_frame(n=20)
    df["volume"] = [10.0] * 18 + [1_000_000.0, 1_000_000.0]  # 18/20 = 90% 接近零
    rep = ValidationReport()
    _check_volume_near_zero(rep, df, 0.5)
    issue = rep.get("zero_or_near_zero_volume")
    assert issue is not None
    assert issue.count == 18


def test_volume_normal_no_issue():
    """成交量正常（无接近零的交易日）→ 不报。"""
    rep = ValidationReport()
    _check_volume_near_zero(rep, _flat_frame(n=20), 0.5)
    assert not rep.has_code("zero_or_near_zero_volume")


def test_volume_zero_not_counted_as_near_zero():
    """成交量为 0 不算「接近零」（该分支用 `vol > 0` 过滤，0 是停牌而非枯竭）。"""
    df = _flat_frame(n=20)
    df["volume"] = [0.0] * 20
    rep = ValidationReport()
    _check_volume_near_zero(rep, df, 0.5)
    assert not rep.has_code("zero_or_near_zero_volume")


def test_volume_no_column_returns_silently():
    """缺 volume 列时静默返回（保护性分支）。"""
    rep = ValidationReport()
    _check_volume_near_zero(rep, pd.DataFrame({"close": [1.0, 2.0]}), 0.5)
    assert not rep.has_code("zero_or_near_zero_volume")


def test_volume_all_nan_returns_silently():
    """成交量全为 NaN 时静默返回（dropna 后为空）。"""
    df = _flat_frame(n=5)
    df["volume"] = [float("nan")] * 5
    rep = ValidationReport()
    _check_volume_near_zero(rep, df, 0.5)
    assert not rep.has_code("zero_or_near_zero_volume")
