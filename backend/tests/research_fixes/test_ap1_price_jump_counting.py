"""A-P1：`price_jump` 计数正确性（P0-2 回归守卫）。

**背景**：2026-10-19 之前，调用方写 `df_sorted[["close"]]`（双方括号）拿到的是
**单列 DataFrame** 而不是 Series。`_check_price_jumps` 内部
`pct = close_series.pct_change(); jumps = pct[pct > threshold]` 在 DataFrame 上走
布尔掩码语义（返回**同形、NaN 填充**的 DataFrame），`len(jumps)` 因此恒等于行数
（`pct_change` 首行为 NaN 时是行数−1）。后果：

- 零跳变的平盘序列也会**误报** `price_jump`；
- 告警里的计数恒为「行数−1」，与真实跳变数无关。历史 JSON 里
  「1594 行 1593 个跳变」就是这么来的。

**本次落地的姿态**：缺陷已由 B 线于 2026-10-19 修复（调用方改为 `df_sorted["close"]`，
且 `_check_price_jumps` 入口加了「非 Series 直接 TypeError」的显式防御），
故这里全部为普通断言，不用 xfail —— 职责是**一旦退化就报红**。

规格来源：NIGHT_PROGRESS「🎯 下一阶段任务分配（2026-10-19 01:10）」A-P1 测试规格第二条。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.data.validation.checks_extended import validate_daily_extended

THRESHOLD = 0.15


def _frame(closes: list[float]) -> pd.DataFrame:
    """构造最小合法日线表（date / 四价 / volume 齐全，避免命中其他检查）。"""
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=n),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000.0] * n,
        }
    )


def _expected_count(closes: pd.Series) -> int:
    """独立算一遍期望值：**相邻有效**收盘价对的相对变化绝对值严格大于阈值的位置数。

    口径与被测实现一致（先剔除缺失值，再对剩余序列求 pct_change）——
    这是「相邻有效对」的定义，而不是「按行号相邻」。缺失值本身不是跳变，
    但它两侧的有效价会在剔除后成为一对。

    刻意用 pandas 原生表达式重算，而不是复用被测实现的中间变量 ——
    否则测试只是把实现的算法抄一遍，实现错了它也错。
    """
    pct = closes.dropna().pct_change()
    return int((pct.abs() > THRESHOLD).sum())


def _jump_issue(closes: list[float]):
    report = validate_daily_extended(_frame(closes), price_jump_threshold=THRESHOLD)
    return report.get("price_jump")


# --------------------------------------------------------------------------- #
# 1. 零跳变
# --------------------------------------------------------------------------- #


def test_flat_prices_report_no_issue():
    """全程走平（0 个跳变）→ **不得**产生 price_jump。

    这是修复前最刺眼的症状：平盘 100 行报「存在 99 个跳变（最大 0.00%）」——
    计数与「最大值」自相矛盾。
    """
    closes = [10.0] * 100
    assert _expected_count(pd.Series(closes)) == 0

    assert _jump_issue(closes) is None, "零跳变不得报警"


def test_single_day_has_no_adjacent_pair():
    """只有 1 个有效收盘价 → 没有相邻对，不检查、不崩。"""
    assert _jump_issue([10.0]) is None


def test_all_nan_close_does_not_crash():
    """收盘价全为 NaN → 不崩、不报 price_jump（有效点不足 2 个）。"""
    assert _jump_issue([np.nan] * 30) is None


# --------------------------------------------------------------------------- #
# 2. 精确计数
# --------------------------------------------------------------------------- #


def test_single_jump_counts_exactly_one():
    """恰好 1 处超阈值 → 计数必须是 1（修复前恒报 99）。"""
    closes = [10.0] * 50 + [12.0] * 50  # 第 51 行 +20%
    issue = _jump_issue(closes)

    assert issue is not None
    assert issue.count == 1, f"应报 1 个跳变，实际 {issue.count}"


def test_multiple_jumps_counted_exactly():
    """多处跳变 → 计数与独立重算的结果逐项一致。"""
    closes = [10.0] * 25 + [12.0] * 25 + [10.0] * 25 + [12.0] * 25  # 3 处 ±20%
    issue = _jump_issue(closes)

    assert issue is not None
    assert issue.count == _expected_count(pd.Series(closes)) == 3


def test_sub_threshold_moves_are_silent():
    """全部变化都低于阈值（10% < 15%）→ 静默。

    修复前该用例必红：`len(DataFrame)` 恒 > 0，任何 ≥2 行数据都会报警。
    """
    closes = [10.0]
    for _ in range(40):
        closes.append(round(closes[-1] * 1.10, 4))  # 每日 +10%
    assert _expected_count(pd.Series(closes)) == 0

    assert _jump_issue(closes) is None


def test_threshold_raise_keeps_check_silent():
    """阈值抬到 15% 而实际最大变化 10% → 仍须静默（阈值不得被忽略）。"""
    closes = [10.0]
    for _ in range(20):
        closes.append(round(closes[-1] * 1.10, 4))
    for _ in range(20):
        closes.append(round(closes[-1] * 0.90, 4))

    assert _expected_count(pd.Series(closes)) == 0
    assert _jump_issue(closes) is None


def test_count_matches_manual_sample_with_gaps():
    """含缺失值的人工样本：只在**相邻有效对**上计数，与独立重算逐项一致。

    缺失值不得被当成「跳变」，也不得让计数退化成行数。
    """
    closes = [10.0] * 20 + [12.0] * 10 + [np.nan] * 5 + [9.0] * 20 + [11.0] * 15
    series = pd.Series(closes)
    expected = _expected_count(series)

    issue = _jump_issue(closes)

    assert issue is not None
    assert issue.count == expected, f"期望 {expected}，实际 {issue.count}"
    assert issue.count < len(closes), "计数不得退化成行数（P0-2 的原始症状）"


# --------------------------------------------------------------------------- #
# 3. 入口防御：非 Series 必须显式拒绝
# --------------------------------------------------------------------------- #


def test_dataframe_input_is_rejected_loudly():
    """`_check_price_jumps` 收到 DataFrame 必须 `TypeError`，不得静默算错。

    「静默算错」正是 P0-2 能活这么久的原因：调用方传错类型没有任何提示。
    显式拒绝把这类错误从「研究结论被污染」降级成「当场崩」。
    """
    from deepquant.data.validation.checks import ValidationReport
    from deepquant.data.validation.checks_extended import _check_price_jumps

    report = ValidationReport()
    with pytest.raises(TypeError, match="pd.Series"):
        _check_price_jumps(report, _frame([10.0, 12.0])[["close"]], THRESHOLD)
