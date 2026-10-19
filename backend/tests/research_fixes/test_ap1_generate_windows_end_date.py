"""A-P1：`generate_windows` 的 `end_date` 语义（P0-1 回归守卫）。

**背景**：2026-10-19 之前，`walkforward/__init__.py:222` 用 `(~mask)[::-1].argmax()`
求最后一个满足位置。`~mask` 在尾部（所有 `index > end_date` 的位置）恒为 True，
反转后第一个元素就是 True，`argmax()` 恒为 0 → `last_true_pos = len(index)-1`
→ `end_idx = len(index)`，**`end_date` 被完全忽略**。

**本次落地的姿态**：缺陷已由 B 线于 2026-10-19 修复，因此这里**不使用 xfail**，
全部为普通断言。理由：整目录 `xfail(strict=True)` 会对「本来就该通过的条目」立即 XPASS
并触发 strict 报错，反而把套件染红。这些用例的职责是：**一旦实现退化就报红**。

规格来源：NIGHT_PROGRESS「🎯 下一阶段任务分配（2026-10-19 01:10）」A-P1 测试规格第一条。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.walkforward import WalkForwardConfig, generate_windows

IDX = pd.bdate_range("2018-01-01", "2024-12-31")
CUTOFF = pd.Timestamp("2022-06-30")


def _cfg(**overrides) -> WalkForwardConfig:
    params = dict(train_window=250, oos_window=60, step=60)
    params.update(overrides)
    return WalkForwardConfig(**params)


# --------------------------------------------------------------------------- #
# 1. 任何窗口都不得越过 end_date
# --------------------------------------------------------------------------- #


def test_no_window_oos_end_exceeds_end_date():
    """`end_date` 是硬上界：**任何**窗口的 `oos_end` 都不得晚于它。

    这是 P0-1 最直接的可观察后果。修复前末窗落在 2024-12-06（越过截断日 2 年 5 个月）。
    """
    windows = generate_windows(IDX, _cfg(end_date=CUTOFF))

    assert windows, "2022-06-30 之前有足够数据，不应返回空窗口集"
    offenders = [w for w in windows if w.oos_end > CUTOFF]
    assert offenders == [], (
        "以下窗口的 oos_end 越过了 end_date="
        f"{CUTOFF.date()}：{[(w.oos_end.date()) for w in offenders]}"
    )


def test_end_date_actually_shrinks_the_window_set():
    """`end_date` 必须**真的起作用**：收紧后窗口集与「不设上界」不同。

    修复前的症状是两者完全一致（end_date 是摆设）。仅断言「≤ end_date」还不够 ——
    一个返回空列表的实现也能满足它，所以这里同时要求窗口集非空且确实变少。
    """
    unlimited = generate_windows(IDX, _cfg())
    bounded = generate_windows(IDX, _cfg(end_date=CUTOFF))

    assert len(bounded) < len(unlimited), (
        f"收紧到 {CUTOFF.date()} 后窗口数应减少，实际 {len(bounded)} vs {len(unlimited)}"
    )
    assert bounded[-1].oos_end < unlimited[-1].oos_end


# --------------------------------------------------------------------------- #
# 2. 闭区间语义
# --------------------------------------------------------------------------- #


def test_end_date_is_inclusive():
    """`end_date` 是**闭区间**：取「无上界时的末窗 oos_end」作为上界，窗口集应当不变。

    若实现把上界当成开区间（`<` 而非 `<=`），末窗会被无故砍掉一条 ——
    这是「差一天」类错误的典型来源。
    """
    unlimited = generate_windows(IDX, _cfg())
    last_oos_end = unlimited[-1].oos_end

    closed = generate_windows(IDX, _cfg(end_date=last_oos_end))

    assert len(closed) == len(unlimited), (
        f"end_date 恰为末窗 oos_end（{last_oos_end.date()}）时不应丢窗口："
        f"{len(closed)} vs {len(unlimited)}"
    )
    assert closed[-1].oos_end == last_oos_end


# --------------------------------------------------------------------------- #
# 3. 退化区间 → 空
# --------------------------------------------------------------------------- #


def test_start_after_end_returns_empty():
    """`start_date > end_date` → 空列表（不是异常、不是越界窗口）。"""
    windows = generate_windows(IDX, _cfg(start_date=IDX[100], end_date=IDX[50]))
    assert windows == []


def test_insufficient_room_for_full_oos_returns_empty():
    """区间内不足以构成一个完整 OOS 窗口 → 空列表。

    这里把起点推到末尾 40 个交易日（< oos_window=60），且上界即数据末尾。
    """
    windows = generate_windows(IDX, _cfg(start_date=IDX[-40], end_date=IDX[-1]))
    assert windows == []


def test_end_date_before_enough_training_data_returns_empty():
    """`end_date` 太早（不足以完成训练窗口）→ 空列表。"""
    windows = generate_windows(IDX, _cfg(end_date=IDX[60]))
    assert windows == []


# --------------------------------------------------------------------------- #
# 4. 单向性与确定性
# --------------------------------------------------------------------------- #


def test_every_window_train_end_precedes_oos_start():
    """严格单向：每个窗口的 `train_end < oos_start`（不得有重叠，否则是数据泄漏）。"""
    windows = generate_windows(IDX, _cfg(end_date=CUTOFF))

    assert windows
    for w in windows:
        assert w.train_end < w.oos_start, f"窗口 {w.window_index} 的训练期与样本外期重叠"


def test_same_input_gives_identical_windows():
    """确定性：同一 index + 同一 cfg ⇒ 完全相同的窗口列表（可复现的前提）。"""
    a = generate_windows(IDX, _cfg(end_date=CUTOFF))
    b = generate_windows(IDX, _cfg(end_date=CUTOFF))

    key = lambda w: (w.window_index, w.train_start, w.train_end, w.oos_start, w.oos_end)
    assert [key(w) for w in a] == [key(w) for w in b]


def test_end_date_change_is_monotonic_in_window_count():
    """越早的上界不可能产生越多的窗口（单调性；防止「裁剪后反而变多」这类实现错误）。"""
    counts = [
        len(generate_windows(IDX, _cfg(end_date=pd.Timestamp(d))))
        for d in ("2020-12-31", "2022-06-30", "2024-12-31")
    ]
    assert counts == sorted(counts), f"窗口数随 end_date 推后应单调不减，实际 {counts}"
