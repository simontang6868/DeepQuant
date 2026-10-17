"""``data/validation/checks_extended.py`` 覆盖率补强（A10：74% → ≥90%）。

A 线红线（本文件严格遵守）：
  1. **只新增测试，不修改 ``src/`` 下任何代码。**
  2. 测出的 bug **只许测出来不许修**，用 ``xfail(strict=True)`` 固化；
     一旦有人修好，该用例会 XPASS 并因 strict 而报错，强制回头摘掉标记。
  3. 断言值由「构造数据的设计意图」推导得出，**禁止先跑一遍再把结果抄成断言**。

已知缺陷（本轮通过测试确认，不修）：
  - ``_check_price_jumps`` 的 ``count`` 报的是**行数**而不是跳变个数。
    根因是 ``validate_daily_extended`` 里取 ``df_sorted[["close"]]`` —— 双层方括号
    返回的是 **DataFrame 而非 Series**，于是 ``len(jumps)`` 退化成行数；
    源码中 ``hasattr(max_jump_scalar, "iloc")`` 那段兼容补丁正是为此打的补丁（治标）。

  - 更严重的后果：**该检查恒定误报**。因为 ``jumps`` 是 DataFrame 布尔索引的结果
    （行数不变、不满足处置 NaN），``len(jumps) > 0`` 对任何 ≥2 行的数据都成立，
    于是 ``price_jump`` WARNING 在**每一份数据**上都会出现。实测：40 行完全平坦
    （最大变化 0.00%）的序列，仍报「存在 39 个相邻收盘价跳变 > 15%」。
    任何把 ``has_code("price_jump")`` 当信号用的下游都会被 100% 误导。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.data.validation.checks import WARNING, ValidationReport
from deepquant.data.validation.checks_extended import (
    _check_price_jumps,
    _check_volume_near_zero,
    validate_daily_extended,
)

# 需要覆盖但公共入口触达不到：``_check_volume_near_zero`` 的「无 volume 列」分支
# （volume 属于 REQUIRED_COLUMNS，缺列会在基础校验阶段直接返回），故对私有函数做单元测试。


def _make_df(
    dates: pd.DatetimeIndex,
    closes,
    volumes=None,
) -> pd.DataFrame:
    """构造通过基础校验的日线数据：价格为正、OHLC 自洽。"""
    close = np.asarray(closes, dtype="float64")
    n = len(close)
    if volumes is None:
        vol = np.full(n, 1_000_000.0)
    else:
        vol = np.asarray(volumes, dtype="float64")
    return pd.DataFrame(
        {
            "date": pd.DatetimeIndex(dates),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": vol,
        }
    )


def _staircase_close(n: int, steps: tuple[int, ...], factor: float = 1.25) -> np.ndarray:
    """在 steps 位置各抬升 factor 并**保持**新价位。

    这样 pct_change 只在这些位置出现一次跃升，跃升幅度恒为 (factor-1)，
    跳变个数 = len(steps)，便于推导断言。
    """
    close = np.full(n, 10.0)
    level = 10.0
    for i in steps:
        level *= factor
        close[i:] = level
    return close


# --------------------------------------------------------------------------- #
# 早退分支：有效日期不足 2 个
# --------------------------------------------------------------------------- #


class TestEarlyReturn:
    def test_single_row_skips_all_extended_checks(self) -> None:
        """有效日期 < 2 时直接返回基础报告，扩展检查一条都不跑。"""
        df = _make_df(pd.bdate_range("2024-01-01", periods=1), [10.0])
        report = validate_daily_extended(df, adjust_status="qfq")

        assert report.n_rows == 1
        for code in (
            "temporal_gap_too_long",
            "price_jump",
            "zero_or_near_zero_volume",
        ):
            assert not report.has_code(code), f"{code} 不应在早退后出现"

    def test_early_return_precedes_adjust_status_check(self) -> None:
        """早退发生在复权口径检查之前 —— 即使 adjust_status 缺失也不报 unclear。"""
        df = _make_df(pd.bdate_range("2024-01-01", periods=1), [10.0])
        report = validate_daily_extended(df, adjust_status=None)

        assert not report.has_code("adjust_status_unclear")


# --------------------------------------------------------------------------- #
# A. 时序完整性
# --------------------------------------------------------------------------- #


class TestTemporalGaps:
    def test_consecutive_calendar_days_produce_no_gap_issue(self) -> None:
        """相邻间隔恒为 1 天 ⇒ 无 gap > 2 ⇒ 走「无长间隔」早退分支。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        df = _make_df(dates, [10.0] * 30)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.has_code("temporal_gap_too_long")

    def test_weekly_spacing_triggers_gap_issue_with_run_length(self) -> None:
        """15 个周频点 ⇒ 14 个间隔，每个 7 天 (>2) 且全部连续 ⇒ 连续段长度 14。"""
        dates = pd.date_range("2024-01-01", periods=15, freq="7D")
        df = _make_df(dates, [10.0] * 15)

        report = validate_daily_extended(df, adjust_status="qfq")
        issue = report.get("temporal_gap_too_long")

        assert issue is not None
        assert issue.level == WARNING
        assert issue.count == 14

    def test_gap_threshold_is_configurable(self) -> None:
        """同样的周频数据，把阈值放宽到 20 ⇒ 连续段 14 不超阈值 ⇒ 不报。"""
        dates = pd.date_range("2024-01-01", periods=15, freq="7D")
        df = _make_df(dates, [10.0] * 15)

        report = validate_daily_extended(
            df, adjust_status="qfq", max_consecutive_missing_days=20
        )

        assert not report.has_code("temporal_gap_too_long")

    def test_isolated_weekend_gap_is_below_default_threshold(self) -> None:
        """纯工作日序列只有周末 3 天间隔，且被工作日打断 ⇒ 最长连续段 = 1 < 10。"""
        dates = pd.bdate_range("2024-01-01", periods=40)
        df = _make_df(dates, [10.0] * 40)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.has_code("temporal_gap_too_long")


# --------------------------------------------------------------------------- #
# A. 请求区间 vs 实际覆盖
# --------------------------------------------------------------------------- #


class TestDateRangeExtent:
    def test_both_ends_missing_reports_head_and_tail(self) -> None:
        """实际 2024-01-10 ~ 2024-01-23，请求 2024-01-01 ~ 2024-02-01。

        2024-01-10 是周三，往后 10 个工作日 ⇒ 末日 2024-01-23（周二）。
        head 缺失 = (01-10 − 01-01).days = 9；tail 缺失 = (02-01 − 01-23).days = 9。
        """
        dates = pd.bdate_range("2024-01-10", periods=10)
        df = _make_df(dates, [10.0] * 10)
        expected = (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"))

        report = validate_daily_extended(
            df, expected_range=expected, adjust_status="qfq"
        )

        head = report.get("date_range_missing_head")
        tail = report.get("date_range_missing_tail")
        assert head is not None and tail is not None
        assert head.level == WARNING and tail.level == WARNING
        assert head.count == 9
        assert tail.count == 9

    def test_expected_range_equal_to_actual_reports_nothing(self) -> None:
        """请求区间与实际完全一致 ⇒ 两端均无缺失。"""
        dates = pd.bdate_range("2024-01-10", periods=10)
        df = _make_df(dates, [10.0] * 10)

        report = validate_daily_extended(
            df,
            expected_range=(dates.min(), dates.max()),
            adjust_status="qfq",
        )

        assert not report.has_code("date_range_missing_head")
        assert not report.has_code("date_range_missing_tail")

    @pytest.mark.parametrize("offset_days,code", [(1, "date_range_missing_head"),
                                                  (2, "date_range_missing_head")])
    def test_head_gap_boundary_is_strictly_greater_than_one(
        self, offset_days: int, code: str
    ) -> None:
        """判据是 ``missing_head > 1``：缺 1 天不报，缺 2 天报。"""
        dates = pd.bdate_range("2024-01-10", periods=10)
        df = _make_df(dates, [10.0] * 10)
        exp_start = dates.min() - pd.Timedelta(days=offset_days)

        report = validate_daily_extended(
            df, expected_range=(exp_start, dates.max()), adjust_status="qfq"
        )

        if offset_days == 1:
            assert not report.has_code(code)
        else:
            issue = report.get(code)
            assert issue is not None
            assert issue.count == offset_days

    def test_tail_gap_boundary_is_strictly_greater_than_one(self) -> None:
        """同理：末尾缺 2 天才报。"""
        dates = pd.bdate_range("2024-01-10", periods=10)
        df = _make_df(dates, [10.0] * 10)
        exp_end = dates.max() + pd.Timedelta(days=2)

        report = validate_daily_extended(
            df, expected_range=(dates.min(), exp_end), adjust_status="qfq"
        )

        issue = report.get("date_range_missing_tail")
        assert issue is not None
        assert issue.count == 2


# --------------------------------------------------------------------------- #
# B. 价格跳变
# --------------------------------------------------------------------------- #


class TestPriceJumps:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "已知缺陷：validate_daily_extended 取 df_sorted[[\"close\"]]（双层方括号）得到 "
            "DataFrame 而非 Series，导致 len(jumps) 退化成行数。"
            "本例 60 行、真实跳变 3 个，实际报 59。修复后请删除本 xfail 标记。"
        ),
    )
    def test_public_path_reports_actual_jump_count(self) -> None:
        """期望：count 应等于真实跳变个数 3。当前实现会报错 —— 故 xfail。"""
        close = _staircase_close(60, steps=(10, 25, 40), factor=1.25)
        df = _make_df(pd.bdate_range("2024-01-01", periods=60), close)

        report = validate_daily_extended(df, adjust_status="qfq")
        issue = report.get("price_jump")

        assert issue is not None
        assert issue.count == 3

    def test_series_input_hits_scalar_branch_and_counts_correctly(self) -> None:
        """直接给 ``_check_price_jumps`` 传 Series ⇒ 走标量分支，计数正确。

        这条用例同时锁定了「意图语义」：跳变个数 = 3，最大跳变 = 25.00%。
        """
        close = pd.Series(_staircase_close(60, steps=(10, 25, 40), factor=1.25))
        report = ValidationReport(n_rows=len(close))

        _check_price_jumps(report, close, 0.15)

        issue = report.get("price_jump")
        assert issue is not None
        assert issue.level == WARNING
        assert issue.count == 3
        assert "最大 25.00%" in issue.message

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "同一缺陷的另一面：jumps 是 DataFrame 布尔索引结果（行数不变），"
            "len(jumps) > 0 对任何 ≥2 行数据都成立 ⇒ 恒定误报。"
            "期望：全部变化都低于阈值时不报。修复后请删除本 xfail 标记。"
        ),
    )
    def test_below_threshold_reports_nothing(self) -> None:
        """每日仅 +1% ⇒ 全部低于 15% 阈值 ⇒ 不应报 price_jump。"""
        n = 60
        close = 10.0 * (1.01 ** np.arange(n))
        df = _make_df(pd.bdate_range("2024-01-01", periods=n), close)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.has_code("price_jump")

    def test_threshold_lowering_makes_check_fire(self) -> None:
        """阈值从 15% 降到 5%：原本 +10% 的一步跨越被检出（这一半当前即可通过）。"""
        n = 40
        close = np.full(n, 10.0)
        close[20:] = 11.0  # 一次性 +10%

        strict = validate_daily_extended(
            _make_df(pd.bdate_range("2024-01-01", periods=n), close),
            adjust_status="qfq",
            price_jump_threshold=0.05,
        )

        assert strict.has_code("price_jump")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "恒定误报：阈值 15% 高于实际变化 10%，理应静默，但因 len(jumps)"
            " 恒为正数而仍报警。修复后请删除本 xfail 标记。"
        ),
    )
    def test_threshold_raising_keeps_check_silent(self) -> None:
        """阈值 15% > 实际变化 10% ⇒ 应当静默。"""
        n = 40
        close = np.full(n, 10.0)
        close[20:] = 11.0

        loose = validate_daily_extended(
            _make_df(pd.bdate_range("2024-01-01", periods=n), close),
            adjust_status="qfq",
            price_jump_threshold=0.15,
        )

        assert not loose.has_code("price_jump")

    def test_nan_close_rows_are_dropped_before_jump_detection(self) -> None:
        """close 含 NaN 的行会被 dropna 剔除，不参与跳变判定。"""
        close = np.full(30, 10.0)
        close[5] = np.nan
        close[6] = 13.0  # 相对 10.0 是 +30%，应在剔除后仍被检出
        df = _make_df(pd.bdate_range("2024-01-01", periods=30), close)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert report.has_code("price_jump")


# --------------------------------------------------------------------------- #
# B. 成交量接近零
# ---------------------------------------------------------------------------


class TestVolumeNearZero:
    def test_missing_volume_column_is_skipped(self) -> None:
        """没有 volume 列 ⇒ 直接返回，不报错也不报 issue。

        volume 属于 REQUIRED_COLUMNS，缺列时基础校验会先返回，
        因此这条分支只能通过对私有函数直接测试来触达。
        """
        df = pd.DataFrame(
            {
                "date": pd.bdate_range("2024-01-01", periods=5),
                "open": [10.0] * 5,
                "high": [10.1] * 5,
                "low": [9.9] * 5,
                "close": [10.0] * 5,
            }
        )
        report = ValidationReport(n_rows=5)

        _check_volume_near_zero(report, df, 0.5)

        assert not report.has_code("zero_or_near_zero_volume")

    def test_all_nan_volume_is_skipped(self) -> None:
        """volume 全为 NaN ⇒ dropna 后为空 ⇒ 直接返回。"""
        dates = pd.bdate_range("2024-01-01", periods=5)
        df = _make_df(dates, [10.0] * 5, volumes=[np.nan] * 5)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.has_code("zero_or_near_zero_volume")

    def test_near_zero_ratio_reported_when_above_threshold(self) -> None:
        """10 天里 8 天成交量为 50（远低于阈值）⇒ 占比 80.0% > 50% ⇒ 报警，count=8。"""
        vols = [50.0] * 8 + [1_000_000.0, 2_000_000.0]
        dates = pd.bdate_range("2024-01-01", periods=10)
        df = _make_df(dates, [10.0] * 10, volumes=vols)

        report = validate_daily_extended(df, adjust_status="qfq")
        issue = report.get("zero_or_near_zero_volume")

        assert issue is not None
        assert issue.level == WARNING
        assert issue.count == 8
        assert "80.0%" in issue.message

    def test_normal_volume_reports_nothing(self) -> None:
        """成交量平稳 ⇒ 无 near-zero 告警。"""
        vols = [1_000_000.0 + i * 1000 for i in range(20)]
        dates = pd.bdate_range("2024-01-01", periods=20)
        df = _make_df(dates, [10.0] * 20, volumes=vols)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.has_code("zero_or_near_zero_volume")

    def test_exactly_zero_volume_days_are_excluded_by_current_filter(self) -> None:
        """现状刻画：``vol > 0`` 把成交量**恰好为 0** 的停牌日排除在外。

        本用例断言的是**当前实现的行为**，不是期望行为。

        注意语义不一致：检查名与文案都叫
        ``zero_or_near_zero_volume``（"零或接近零"），但过滤器用 ``vol > 0``
        把 0 排除，导致**整段停牌（成交量恒为 0）反而不报警**。
        这看起来是缺陷，但修复会改变共享校验模块的行为，
        按纪律需**先提案**，不在本轮改动。
        """
        vols = [0.0] * 8 + [1_000_000.0, 2_000_000.0]
        dates = pd.bdate_range("2024-01-01", periods=10)
        df = _make_df(dates, [10.0] * 10, volumes=vols)

        report = validate_daily_extended(df, adjust_status="qfq")

        # 当前行为：8 天零成交量被过滤掉，占比 0% ⇒ 不报警
        assert not report.has_code("zero_or_near_zero_volume")


# --------------------------------------------------------------------------- #
# C. 复权口径
# --------------------------------------------------------------------------- #


class TestAdjustStatus:
    @pytest.mark.parametrize("status", [None, "unknown"])
    def test_missing_or_unknown_status_reports_unclear(self, status: str | None) -> None:
        report = validate_daily_extended(
            _make_df(pd.bdate_range("2024-01-01", periods=10), [10.0] * 10),
            adjust_status=status,
        )
        issue = report.get("adjust_status_unclear")

        assert issue is not None
        assert issue.level == WARNING

    @pytest.mark.parametrize("status", ["qfq", "hfq", "none", "raw"])
    def test_declared_status_does_not_report_unclear(self, status: str) -> None:
        """已声明口径 ⇒ 不报 unclear（防止检查过度触发）。"""
        report = validate_daily_extended(
            _make_df(pd.bdate_range("2024-01-01", periods=10), [10.0] * 10),
            adjust_status=status,
        )

        assert not report.has_code("adjust_status_unclear")


# --------------------------------------------------------------------------- #
# 契约：扩展检查不得改变基础校验的判定
# --------------------------------------------------------------------------- #


class TestContract:
    def test_extended_only_adds_warnings_never_turns_valid_into_invalid(self) -> None:
        """干净数据上，扩展版不应引入任何 ERROR（is_valid 保持 True）。"""
        dates = pd.date_range("2024-01-01", periods=60, freq="D")
        close = 10.0 * (1.002 ** np.arange(60))
        df = _make_df(dates, close)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert report.is_valid, f"不应出现 ERROR：{report.error_codes()}"

    def test_extended_preserves_base_error_detection(self) -> None:
        """基础校验能抓到的负价，扩展版也必须抓到（不能因为早退而漏检）。"""
        dates = pd.bdate_range("2024-01-01", periods=10)
        close = np.full(10, 10.0)
        close[3] = -1.0
        df = _make_df(dates, close)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert not report.is_valid
        assert "invalid_price" in report.error_codes()

    def test_report_is_never_none_and_n_rows_matches_input(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=25)
        df = _make_df(dates, [10.0] * 25)

        report = validate_daily_extended(df, adjust_status="qfq")

        assert report is not None
        assert report.n_rows == 25
