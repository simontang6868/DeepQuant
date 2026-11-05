"""验收 2.2-c / 2.2-d 的调度闸：时段、重定价次数、顺延交易日数。

这批测试的重点不是"函数能跑"，而是**把三条硬约束钉死**：
1. 发单只在可连续竞价的窗口内（午休、收盘竞价、开盘前都不行）；
2. 重定价总次数 ≤ 2，超出**报错而不是跳过**（跳过会让账本显示没超）；
3. 顺延以**交易日**计，不接受自然日；10 天到顶转 ABANDONED。
"""

from __future__ import annotations

import datetime as dt

import pytest

from deepquant.execution.session import (
    MAX_CARRY_DAYS,
    MAX_REPRICE,
    CarryExhausted,
    CloseOutcome,
    OrderTracker,
    Phase,
    RepriceNotAllowed,
    SessionTimes,
    is_trading_time,
    next_trading_day,
    phase_of,
    reprice_slots_passed,
)

D = dt.date(2026, 12, 1)          # 一个普通交易日（周二）


def at(h: int, m: int, s: int = 0, day: dt.date = D) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, h, m, s)


def make_tracker(cid: str = "DQ-20261130-600000-B-1000") -> OrderTracker:
    return OrderTracker(cid, decision_day="20261130")


# ---------------------------------------------------------------------------
# 1. 时段判定
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("h,m,expect", [
    (9, 0, Phase.PRE_OPEN),            # 开盘前
    (9, 30, Phase.PRE_OPEN),           # 已开盘但策略约定 09:35 前不发
    (9, 34, Phase.PRE_OPEN),
    (9, 35, Phase.PLACE_AM),           # 恰好边界
    (10, 30, Phase.PLACE_AM),
    (11, 29, Phase.PLACE_AM),
    (11, 30, Phase.LUNCH),             # 午休起点
    (12, 0, Phase.LUNCH),
    (12, 59, Phase.LUNCH),
    (13, 0, Phase.PLACE_PM),
    (14, 30, Phase.PLACE_PM),
    (14, 56, Phase.PLACE_PM),
    (14, 57, Phase.CLOSING_AUCTION),   # 收盘集合竞价
    (14, 59, Phase.CLOSING_AUCTION),
    (15, 0, Phase.CLOSED),
    (20, 0, Phase.CLOSED),
])
def test_phase_boundaries(h, m, expect):
    assert phase_of(at(h, m)) is expect


def test_lunch_break_is_not_trading_time():
    """午休是最容易漏的窗口——11:30 发的单会被柜台直接拒。"""
    assert is_trading_time(at(11, 29)) is True
    assert is_trading_time(at(11, 30)) is False
    assert is_trading_time(at(12, 30)) is False
    assert is_trading_time(at(13, 0)) is True


def test_closing_auction_rejected():
    assert is_trading_time(at(14, 56)) is True
    assert is_trading_time(at(14, 57)) is False


def test_place_raises_outside_window():
    tr = make_tracker()
    with pytest.raises(RepriceNotAllowed, match="不能发单"):
        tr.place(at(12, 30))
    assert tr.placed_at is None       # 失败后不得留下"已发单"的假象


def test_place_twice_raises():
    tr = make_tracker()
    tr.place(at(9, 40))
    with pytest.raises(RuntimeError, match="已发过单"):
        tr.place(at(10, 0))


def test_place_sets_first_day():
    tr = make_tracker()
    tr.place(at(9, 40))
    assert tr.first_day == "20261201"


# ---------------------------------------------------------------------------
# 2. 重定价时点与次数（2.2-c / 2.2-d）
# ---------------------------------------------------------------------------
def test_reprice_slots_passed_counts():
    assert reprice_slots_passed(at(10, 0)) == 0
    assert reprice_slots_passed(at(10, 30)) == 1
    assert reprice_slots_passed(at(14, 0)) == 1
    assert reprice_slots_passed(at(14, 30)) == 2


def test_no_reprice_before_first_slot():
    tr = make_tracker()
    tr.place(at(9, 40))
    assert tr.due_slots(at(10, 0)) == ()
    with pytest.raises(RepriceNotAllowed, match="无可用重定价时点"):
        tr.reprice(at(10, 0))


def test_reprice_at_1030_then_1430_uses_two():
    tr = make_tracker()
    tr.place(at(9, 40))
    assert tr.reprice(at(10, 35)) == 0
    assert tr.reprice(at(14, 35)) == 1
    assert tr.reprice_count == 2
    assert tr.reprice_remaining == 0


def test_second_reprice_same_slot_raises():
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.reprice(at(10, 35))                     # 用掉 slot 0
    with pytest.raises(RepriceNotAllowed):
        tr.reprice(at(10, 50), slot=0)          # 同一 slot 不得重复
    assert tr.reprice_count == 1


def test_third_reprice_raises_hard():
    """2.2-d：上限 2，第 3 次**报错**。跳过会掩盖超限事实。"""
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.reprice(at(10, 35))
    tr.reprice(at(14, 35))
    with pytest.raises(RepriceNotAllowed, match=f"上限 {MAX_REPRICE}"):
        tr.reprice(at(14, 50))


def test_reprice_at_1430_can_skip_missed_1030():
    """错过 10:30 的，14:30 仍可改一次（due_slots 会返回两个待用 slot）。"""
    tr = make_tracker()
    tr.place(at(9, 40))
    assert tr.due_slots(at(14, 31)) == (0, 1)
    assert tr.reprice(at(14, 31)) == 0          # 取最早一个
    assert tr.reprice_count == 1


def test_reprice_before_place_raises():
    tr = make_tracker()
    with pytest.raises(RepriceNotAllowed, match="尚未发单"):
        tr.reprice(at(10, 35))


def test_reprice_next_day_raises():
    """跨日重定价属口径变更：本实现只允许发单当日改价。"""
    tr = make_tracker()
    tr.place(at(9, 40))
    with pytest.raises(RepriceNotAllowed, match="只在发单当日"):
        tr.reprice(at(10, 35, day=dt.date(2026, 12, 2)))


def test_reprice_during_lunch_raises():
    tr = make_tracker()
    tr.place(at(9, 40))
    with pytest.raises(RepriceNotAllowed, match="非交易时段"):
        tr.reprice(at(12, 0))


def test_total_cap_is_not_reset_by_carry():
    """⭐ 关键：2.2-d 的 2 次是**累计**上限，顺延不重置（本模块选择的最严解释）。

    若改成"每日 2 次"，此断言会失败——那是一次口径变更，须改预登记。
    """
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.reprice(at(10, 35))
    tr.reprice(at(14, 35))
    assert tr.reprice_count == 2
    days = ["20261201", "20261202", "20261203"]
    tr.on_close("20261201", filled=False)
    tr.carry_to("20261202", days)
    assert tr.reprice_count == 2               # 顺延不重置
    assert tr.reprice_remaining == 0


# ---------------------------------------------------------------------------
# 3. 收盘处置与顺延（2.2-c，10 个交易日）
# ---------------------------------------------------------------------------
def test_filled_is_terminal():
    tr = make_tracker()
    tr.place(at(9, 40))
    assert tr.on_close("20261201", filled=True) is CloseOutcome.FILLED
    assert tr.carry_days == 0


def test_unfilled_carries_then_abandons_at_10():
    days = [f"202612{d:02d}" for d in range(1, 32)]
    tr = make_tracker()
    tr.place(at(9, 40))
    outcomes = []
    for i in range(MAX_CARRY_DAYS):
        o = tr.on_close(days[i], filled=False)
        outcomes.append(o)
        if o is CloseOutcome.CARRY:
            tr.carry_to(days[i + 1], days)
    assert outcomes[:-1] == [CloseOutcome.CARRY] * (MAX_CARRY_DAYS - 1)
    assert outcomes[-1] is CloseOutcome.ABANDONED
    assert tr.carry_days == MAX_CARRY_DAYS
    assert tr.exhausted is True


def test_ninth_day_still_carries():
    """第 9 天（carry_days=9）仍应为 CARRY，第 10 天才 ABANDONED。"""
    days = [f"202612{d:02d}" for d in range(1, 32)]
    tr = make_tracker()
    tr.place(at(9, 40))
    for i in range(9):
        assert tr.on_close(days[i], filled=False) is CloseOutcome.CARRY
        tr.carry_to(days[i + 1], days)
    assert tr.on_close(days[9], filled=False) is CloseOutcome.ABANDONED


def test_close_without_place_raises():
    tr = make_tracker()
    with pytest.raises(RuntimeError, match="未发单"):
        tr.on_close("20261201", filled=False)


# ---------------------------------------------------------------------------
# 4. 顺延必须走交易日，不许用自然日
# ---------------------------------------------------------------------------
def test_next_trading_day_skips_non_trading():
    """跨周末：周五顺延到周一，不是周六。"""
    days = ["20261204", "20261207"]     # 周五 → 下周一
    assert next_trading_day("20261204", days) == "20261207"


def test_next_trading_day_unknown_day_raises():
    with pytest.raises(KeyError, match="不在交易日序列"):
        next_trading_day("20260101", ["20261201", "20261202"])


def test_next_trading_day_last_day_raises():
    with pytest.raises(KeyError, match="最后一个交易日"):
        next_trading_day("20261202", ["20261201", "20261202"])


def test_carry_to_rejects_non_trading_day():
    """⭐ 把自然日写进顺延会被拒——否则 10 天上限被国庆稀释。"""
    days = ["20261201", "20261202"]
    tr = make_tracker()
    tr.place(at(9, 40))
    with pytest.raises(ValueError, match="不是次一交易日"):
        tr.carry_to("20261203", days)     # 03 不在序列里


def test_carry_to_advances_first_day():
    days = ["20261201", "20261202", "20261203"]
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.on_close("20261201", filled=False)
    tr.carry_to("20261202", days)
    assert tr.first_day == "20261202"
    assert tr.carried_to == "20261202"
    tr.on_close("20261202", filled=False)
    tr.carry_to("20261203", days)
    assert tr.first_day == "20261203"


# ---------------------------------------------------------------------------
# 5. 落盘往返（重启后必须能恢复，否则顺延计数会归零）
# ---------------------------------------------------------------------------
def test_to_dict_from_dict_roundtrip():
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.reprice(at(10, 35))
    d = tr.to_dict()
    tr2 = OrderTracker.from_dict(d)
    assert tr2.to_dict() == d
    assert tr2.reprice_count == 1
    assert tr2.placed_at == tr.placed_at


def test_from_dict_survives_missing_optional_fields():
    tr = OrderTracker.from_dict({"client_order_id": "X", "decision_day": "20261130"})
    assert tr.reprice_count == 0 and tr.carry_days == 0 and tr.placed_at is None


# ---------------------------------------------------------------------------
# 6. 时段配置校验（防止把参数配成自相矛盾却跑起来）
# ---------------------------------------------------------------------------
def test_bad_times_rejected():
    with pytest.raises(ValueError, match="时段非法"):
        SessionTimes(first_place=dt.time(11, 40))          # 发单晚于上午收盘
    with pytest.raises(ValueError, match="时段非法"):
        SessionTimes(closing_auction=dt.time(15, 30))      # 竞价晚于收盘
    with pytest.raises(ValueError, match="必须按时间升序"):
        SessionTimes(reprice_slots=(dt.time(14, 30), dt.time(10, 30)))
    with pytest.raises(ValueError, match="不在可发单窗口"):
        SessionTimes(reprice_slots=(dt.time(16, 0),))


def test_exhausted_property():
    tr = make_tracker()
    tr.place(at(9, 40))
    tr.carry_days = MAX_CARRY_DAYS
    assert tr.exhausted is True
