"""发单循环（``order_loop.tick``）的行为测试。

覆盖三件事，每件都对应可追溯的条款：
1. **到点才发**（2.2-c）：09:34 不发、09:35 发、午休不发、14:58 不发；
2. **改价上限 2 次且只朝有利方向**（2.2-c/2.2-d）：第 3 次硬失败；
   买单不改低、卖单不改高、陈旧报价不改；
3. **收盘顺延以交易日计**（2.2-c）：未成交转 CARRY、第 10 天 ABANDON、
   日内从未发出**不**计顺延。
"""

from __future__ import annotations

import datetime as dt

import pytest

from deepquant.broker.models import Quote
from deepquant.execution.order_loop import (
    Action,
    LoopState,
    reprice_price,
    tick,
)
from deepquant.execution.planner import PlannedOrder, make_client_order_id
from deepquant.execution.rate_limit import RateLimiter
from deepquant.market.rules import Side

D = dt.date(2026, 12, 1)


def at(h: int, m: int, day: dt.date = D) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, h, m)


DECISION = "20261130"


def o(side: Side = Side.BUY, code: str = "600000", shares: int = 1000,
      price: float = 10.00, cid: str | None = None) -> PlannedOrder:
    # ⚠️ 用 planner 的真实派生函数，不手写假格式。
    # 第一版这里写的是 f"DQ-{...}"，与真实 id（决策日在第 0 段）不符，
    # 结果 _decision_of 在生产格式上会崩——测试却因为用了假格式而全绿。
    return PlannedOrder(
        client_order_id=cid or make_client_order_id(DECISION, code, side, shares),
        code=code, side=side, shares=shares, limit_price=price, reason="新建仓")


def q(last: float, code: str = "600000", stale: bool = False) -> Quote:
    return Quote(code=code, last=last, ts=20261201, stale=stale)


def state(rate: float = 100.0, burst: int = 100) -> LoopState:
    """默认给足令牌，避免限速干扰非限速用例。"""
    return LoopState(limiter=RateLimiter(rate_per_second=rate, burst=burst))


# ---------------------------------------------------------------------------
# 1. 到点才发（2.2-c）
# ---------------------------------------------------------------------------
def test_no_place_before_0935():
    st = state()
    acts = tick(now=at(9, 34), plan=[o()], state=st)
    assert acts[0].kind is Action.HOLD
    assert "非交易时段" in acts[0].reason
    # 非交易时段**不建立 tracker**（没发单就不该在账上留痕）——查不到是预期
    assert st.trackers.get(o().client_order_id) is None


def test_place_at_0935_exact():
    st = state()
    acts = tick(now=at(9, 35), plan=[o()], state=st)
    assert acts[0].kind is Action.PLACE
    assert acts[0].limit_price == 10.00
    assert st.trackers[o().client_order_id].placed_at == at(9, 35)


def test_no_place_during_lunch():
    st = state()
    acts = tick(now=at(12, 0), plan=[o()], state=st)
    assert acts[0].kind is Action.HOLD
    assert "非交易时段" in acts[0].reason


def test_no_place_during_closing_auction():
    st = state()
    acts = tick(now=at(14, 58), plan=[o()], state=st)
    assert acts[0].kind is Action.HOLD


def test_second_round_does_not_repost():
    """⭐ 已发过的不得再 PLACE——重复发单会变成两笔真实委托。"""
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    acts = tick(now=at(9, 50), plan=[o()], state=st)
    assert acts[0].kind is Action.HOLD
    assert "未到重定价时点" in acts[0].reason or "重定价次数" in acts[0].reason


def test_sell_before_buy_order_preserved():
    """先卖后买：动作顺序必须与 plan 顺序一致（planner 已排好）。"""
    st = state()
    plan = [o(Side.SELL, "600000", 1000, 10.0), o(Side.BUY, "000001", 500, 20.0)]
    acts = tick(now=at(9, 40), plan=plan, state=st)
    assert [a.kind for a in acts] == [Action.PLACE, Action.PLACE]
    assert acts[0].side is Side.SELL and acts[1].side is Side.BUY


# ---------------------------------------------------------------------------
# 2. 重定价（2.2-c / 2.2-d）
# ---------------------------------------------------------------------------
def test_reprice_after_1030_moves_buy_up():
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    acts = tick(now=at(10, 35), plan=[o()], state=st, quotes={"600000": q(10.05)})
    assert acts[0].kind is Action.REPRICE
    # 10.05 × 1.003 = 10.08015 → 10.08，且高于原价 10.00
    assert acts[0].limit_price == 10.08
    assert st.trackers[o().client_order_id].reprice_count == 1


def test_reprice_hits_cap_after_two():
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    tick(now=at(10, 35), plan=[o()], state=st, quotes={"600000": q(10.05)})
    tick(now=at(14, 35), plan=[o()], state=st, quotes={"600000": q(10.20)})
    tr = st.trackers[o().client_order_id]
    assert tr.reprice_count == 2
    # 第三次：不再产出 REPRICE，且原因明说是次数用尽
    acts = tick(now=at(14, 40), plan=[o()], state=st, quotes={"600000": q(10.30)})
    assert acts[0].kind is Action.HOLD
    assert "重定价次数已用尽" in acts[0].reason


def test_no_reprice_before_first_slot():
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    acts = tick(now=at(10, 0), plan=[o()], state=st, quotes={"600000": q(11.0)})
    assert acts[0].kind is Action.HOLD


def test_buy_never_reprices_down():
    """⭐ 买单只上移。最新价低于原限价时改价没有意义，必须 HOLD。"""
    st = state()
    tick(now=at(9, 40), plan=[o(price=10.00)], state=st)
    acts = tick(now=at(10, 35), plan=[o(price=10.00)], state=st,
                quotes={"600000": q(9.50)})
    assert acts[0].kind is Action.HOLD
    assert "不朝不利方向改" in acts[0].reason


def test_sell_never_reprices_up():
    st = state()
    ord_ = o(Side.SELL, price=10.00)
    tick(now=at(9, 40), plan=[ord_], state=st)
    acts = tick(now=at(10, 35), plan=[ord_], state=st, quotes={"600000": q(10.50)})
    assert acts[0].kind is Action.HOLD


def test_sell_reprices_down_toward_market():
    st = state()
    ord_ = o(Side.SELL, price=10.00)
    tick(now=at(9, 40), plan=[ord_], state=st)
    acts = tick(now=at(10, 35), plan=[ord_], state=st, quotes={"600000": q(9.80)})
    assert acts[0].kind is Action.REPRICE
    # 9.80 × 0.997 = 9.7706 → 9.77，且低于原价
    assert acts[0].limit_price == 9.77


def test_stale_quote_blocks_reprice():
    """⭐ 停牌/滞后报价不得用于改价。"""
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    acts = tick(now=at(10, 35), plan=[o()], state=st,
                quotes={"600000": q(10.5, stale=True)})
    assert acts[0].kind is Action.HOLD
    assert "无报价或改价无改善" in acts[0].reason
    assert st.trackers[o().client_order_id].reprice_count == 0


def test_missing_quote_blocks_reprice():
    st = state()
    tick(now=at(9, 40), plan=[o()], state=st)
    acts = tick(now=at(10, 35), plan=[o()], state=st, quotes={})
    assert acts[0].kind is Action.HOLD


def test_price_limit_caps_buy_reprice():
    """涨停价夹逼：不能因为算出来的价超过涨停而报出无效价。"""
    st = state()
    tick(now=at(9, 40), plan=[o(price=10.00)], state=st)
    acts = tick(now=at(10, 35), plan=[o(price=10.00)], state=st,
                quotes={"600000": q(10.95)},
                price_limits={"600000": (9.00, 11.00)})
    # 10.95×1.003 = 10.98 < 11.00 → 10.98
    assert acts[0].limit_price == 10.98


def test_reprice_price_returns_none_without_improvement():
    ord_ = o(price=10.00)
    # 最新价 = 原限价时，×1.003 会算出更高的价 ⇒ **确实**应改价，不是 None
    assert reprice_price(ord_, q(10.00)) == 10.03
    assert reprice_price(ord_, None) is None
    assert reprice_price(ord_, q(0.0)) is None
    # 卖单：市场价高于原限价时，算出来的价被 min(原价, …) 拉回原价 ⇒ 不该改
    assert reprice_price(o(Side.SELL, price=10.00), q(10.50)) is None
    # 卖单：市场价等于原限价时，×0.997 确实更低 ⇒ 应改
    assert reprice_price(o(Side.SELL, price=10.00), q(10.00)) == 9.97


# ---------------------------------------------------------------------------
# 3. 限速（不是失败，是 HOLD）
# ---------------------------------------------------------------------------
def test_rate_limited_returns_hold_not_failure():
    """⭐ 被限速必须是 HOLD，不能记 FAILED——节流不是事故。"""
    st = LoopState(limiter=RateLimiter(rate_per_second=1.0, burst=1))
    plan = [o(Side.BUY, "600000", 1000, 10.0),
            o(Side.BUY, "000001", 500, 20.0)]
    acts = tick(now=at(9, 40), plan=plan, state=st)
    assert acts[0].kind is Action.PLACE
    assert acts[1].kind is Action.HOLD
    assert "限速" in acts[1].reason
    # 未获令牌的那笔没有 placed_at
    assert st.trackers[plan[1].client_order_id].placed_at is None


def test_rate_limited_order_retries_next_round():
    st = LoopState(limiter=RateLimiter(rate_per_second=100.0, burst=1))
    plan = [o(Side.BUY, "600000"), o(Side.BUY, "000001", 500, 20.0)]
    tick(now=at(9, 40), plan=plan, state=st)
    acts = tick(now=at(9, 41), plan=plan, state=st)     # 令牌已充回
    assert acts[1].kind is Action.PLACE


# ---------------------------------------------------------------------------
# 4. 收盘顺延（2.2-c，交易日计）
# ---------------------------------------------------------------------------
def test_close_marks_filled_and_no_carry():
    st = state()
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    acts = tick(now=at(15, 5), plan=[ord_], state=st, at_close=True,
                day="20261201", filled=[ord_.client_order_id])
    assert acts[0].kind is Action.HOLD and "已成交" in acts[0].reason
    assert st.trackers[ord_.client_order_id].carry_days == 0


def test_close_unfilled_carries_to_next_trading_day():
    st = state()
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    days = ["20261201", "20261202", "20261203"]
    acts = tick(now=at(15, 5), plan=[ord_], state=st, at_close=True,
                day="20261201", trading_days=days)
    assert acts[0].kind is Action.CANCEL
    assert "顺延至 20261202" in acts[0].reason
    tr = st.trackers[ord_.client_order_id]
    assert tr.carry_days == 1 and tr.first_day == "20261202"


def test_close_abandons_on_day_10():
    st = state()
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    days = [f"202612{d:02d}" for d in range(1, 32)]
    last = None
    for i in range(10):
        acts = tick(now=dt.datetime(2026, 12, i + 1, 15, 5), plan=[ord_], state=st,
                    at_close=True, day=days[i], trading_days=days)
        last = acts[0]
        if last.kind is Action.ABANDON:
            break
    assert last is not None and last.kind is Action.ABANDON
    assert "放弃" in last.reason
    assert st.trackers[ord_.client_order_id].carry_days == 10


def test_close_never_placed_does_not_count_carry():
    """⭐ 全天没发出（限速/无报价）不算顺延——顺延是"发过但没成"的账。"""
    lim = RateLimiter(rate_per_second=1e-9, burst=1)
    lim.try_acquire(at(9, 40).timestamp())      # 先把唯一令牌用掉（refill 极慢，等于不会再充）
    st = LoopState(limiter=lim)
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    acts = tick(now=at(15, 5), plan=[ord_], state=st, at_close=True,
                day="20261201", trading_days=["20261201", "20261202"])
    assert acts[0].kind is Action.HOLD
    assert "未发出" in acts[0].reason
    assert st.trackers[ord_.client_order_id].carry_days == 0


def test_close_without_calendar_does_not_advance_date():
    """失败关闭：没有交易日历就只报天数，不猜日期。"""
    st = state()
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    acts = tick(now=at(15, 5), plan=[ord_], state=st, at_close=True, day="20261201")
    assert acts[0].kind is Action.HOLD
    assert "无交易日历" in acts[0].reason
    tr = st.trackers[ord_.client_order_id]
    assert tr.carry_days == 1 and tr.first_day == "20261201"   # 日期没推进


def test_close_requires_day():
    st = state()
    with pytest.raises(ValueError, match="必须给 day"):
        tick(now=at(15, 5), plan=[o()], state=st, at_close=True)


# ---------------------------------------------------------------------------
# 5. 状态持久化与不变量
# ---------------------------------------------------------------------------
def test_state_roundtrip_keeps_carry_count():
    """⭐ 重启后顺延计数必须还在，否则订单会被无限顺延。"""
    st = state()
    ord_ = o()
    tick(now=at(9, 40), plan=[ord_], state=st)
    tick(now=at(15, 5), plan=[ord_], state=st, at_close=True,
         day="20261201", trading_days=["20261201", "20261202"])
    d = st.to_dict()
    st2 = LoopState.from_dict(d)
    assert st2.trackers[ord_.client_order_id].carry_days == 1
    assert st2.trackers[ord_.client_order_id].first_day == "20261202"
    assert st2.to_dict()["trackers"] == d["trackers"]


def test_action_requires_price_for_place():
    with pytest.raises(ValueError, match="limit_price"):
        from deepquant.execution.order_loop import LoopAction
        LoopAction(Action.PLACE, "X", "600000", Side.BUY, 100, None)


def test_decision_day_parsed_from_real_cid():
    """⭐ 用 planner 真实派生的 id 测，不用手写格式（否则测的是想象）。"""
    from deepquant.execution.order_loop import _decision_of
    real = make_client_order_id("20261130", "600990", Side.BUY, 200)
    assert real == "20261130-600990-B-200"
    assert _decision_of(o(cid=real)) == "20261130"
    # 重定价后的 id 带 -r1 后缀，决策日仍应解析正确
    rev = make_client_order_id("20261130", "600990", Side.BUY, 200, revision=1)
    assert rev == "20261130-600990-B-200-r1"
    assert _decision_of(o(cid=rev)) == "20261130"


def test_bad_cid_raises():
    st = state()
    bad = PlannedOrder(client_order_id="oops", code="600000", side=Side.BUY,
                       shares=100, limit_price=10.0)
    with pytest.raises(ValueError, match="不是 YYYYMMDD 决策日"):
        tick(now=at(9, 40), plan=[bad], state=st)


def test_filled_order_is_held_not_reposted():
    st = state()
    ord_ = o()
    acts = tick(now=at(9, 40), plan=[ord_], state=st,
                filled=[ord_.client_order_id])
    assert acts[0].kind is Action.HOLD
    assert "已成交" in acts[0].reason
    assert st.trackers[ord_.client_order_id].placed_at is None


# ---------------------------------------------------------------------------
# 6. 时点内多轮（限速器 burst 只约束单轮，不约束整个时点）
# ---------------------------------------------------------------------------
def test_burst_limits_single_round_not_the_slot():
    """⭐ 一轮只发 burst 笔；同一时点内多轮可把剩下的发完。

    这是 2026-11-05 纸面演练暴露的真实缺口：调用方若只在 09:35 调一次 tick，
    burst=2 时 4 笔只会发出 2 笔，另外 2 笔要等到下一个时点（可能是 14:40）。
    账面上看不出异常，但"早盘建仓"变成了"尾盘建仓"。
    """
    st = LoopState(limiter=RateLimiter(rate_per_second=2.0, burst=2))
    plan = [o(Side.BUY, f"60000{i}", 100, 10.0) for i in range(4)]

    r1 = tick(now=at(9, 35), plan=plan, state=st)
    placed1 = [a for a in r1 if a.kind is Action.PLACE]
    assert len(placed1) == 2, "单轮不得超过 burst"

    # 同一时点内推后 1 秒再来一轮：令牌已按 2/s 充回
    r2 = tick(now=dt.datetime.combine(D, dt.time(9, 35, 1)), plan=plan, state=st)
    placed2 = [a for a in r2 if a.kind is Action.PLACE]
    assert len(placed2) == 2
    assert {a.client_order_id for a in placed1} & {a.client_order_id for a in placed2} == set()
    # 四笔全部发出，且各只发一次
    all_placed = {a.client_order_id for a in placed1 + placed2}
    assert len(all_placed) == 4


def test_drain_reaches_steady_state():
    """时点内反复 tick 直到没有 PLACE —— 这就是真实执行器应有的形态。"""
    st = LoopState(limiter=RateLimiter(rate_per_second=2.0, burst=2))
    plan = [o(Side.BUY, f"60001{i}", 100, 10.0) for i in range(5)]
    t0 = dt.datetime.combine(D, dt.time(9, 35))
    rounds = 0
    for i in range(10):
        acts = tick(now=t0 + dt.timedelta(seconds=i), plan=plan, state=st)
        rounds += 1
        if not any(a.kind in (Action.PLACE, Action.REPRICE) for a in acts):
            break
    assert rounds <= 4, f"5 笔在 2/s 下应 3 轮内发完，实际 {rounds} 轮"
    assert all(st.trackers[o_.client_order_id].placed_at is not None for o_ in plan)
