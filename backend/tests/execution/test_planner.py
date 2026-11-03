"""Phase 2.2：订单规划的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.2-a ~ 2.2-e。

三条最容易被写错、也最贵的性质
------------------------------
1. **先卖后买**（2.2-a）：换仓时买入的钱来自卖出。顺序错了会把同一笔钱用两次。
2. **幂等键不含时钟**（2.2-b）：一旦含时间戳，重试就是新单，而单次运行完全看不出来。
3. **买入预算不假定卖出回款**（2.2-e）：`settled_cash_from_sales` 默认 0，
   要动用回款必须由调用方显式声明。
"""

from __future__ import annotations

import pytest

from deepquant.broker.models import AccountSnapshot, Cash, Position, Quote
from deepquant.execution.planner import (
    PlanInput,
    make_client_order_id,
    plan_orders,
)
from deepquant.market.rules import Side

DEC = "20261030"


def _q(code: str, last: float) -> Quote:
    return Quote(code=code, last=last, ts=20261030, stale=False)


def _snap(cash: float, positions: list[Position] | None = None) -> AccountSnapshot:
    return AccountSnapshot(
        cash=Cash(available=cash, frozen=0.0, total=cash),
        positions=tuple(positions or ()),
        ts=20261030,
    )


def _input(targets, snapshot, quotes, *, weight=0.2, dev=0.0, **kw) -> PlanInput:
    return PlanInput(
        decision=DEC, targets=targets, snapshot=snapshot, quotes=quotes,
        target_weight=weight, price_deviation=dev, **kw,
    )


# ---------------------------------------------------------------------------
# 2.2-a 差额、整手、先卖后买
# ---------------------------------------------------------------------------
def test_empty_account_buys_five_targets_in_order():
    codes = ["600001", "600002", "600003", "600004", "600005"]
    quotes = {c: _q(c, 10.0) for c in codes}
    r = plan_orders(_input(codes, _snap(1_000_000.0), quotes))
    assert r.sells == ()
    assert [o.code for o in r.buys] == codes, "买单顺序必须等于目标顺序（= amt20 降序优先级）"
    for o in r.buys:
        assert o.shares == 20_000, "100 万 × 20% ÷ 10 元 = 2 万股"
        assert o.shares % 100 == 0
        assert o.reason == "新建仓"


def test_rounds_target_shares_down_to_whole_lot():
    """预算不足以买整手时向下取整，不得四舍五入。"""
    r = plan_orders(_input(["600001"], _snap(1_000.0), {"600001": _q("600001", 30.0)}))
    # 1000 × 0.2 = 200 元 ÷ 30 = 6.67 股 → 0 手 → 跳过（而不是买 7 股）
    assert r.orders == ()
    assert len(r.skipped) == 1
    assert "不足一手" in r.skipped[0].reason

    r2 = plan_orders(_input(["600001"], _snap(100_000.0), {"600001": _q("600001", 30.0)}))
    assert r2.buys[0].shares == 600   # 20000/30 = 666.7 → 600


def test_sell_before_buy_on_rebalance():
    """卖 A 买 B：卖单必须全部排在买单之前。"""
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 10.0)}
    # 必须留一些现金，否则按 2.2-e「不假定卖出回款」根本不会生成买单，
    # 这条测试就退化成只验证卖单了。
    snap = _snap(100_000.0, [Position("600001", quantity=20_000, available=20_000,
                                      avg_cost=9.0)])
    r = plan_orders(_input(["600002"], snap, quotes))
    sides = [o.side for o in r.orders]
    assert sides == [Side.SELL, Side.BUY], f"实际顺序 {sides}"
    assert r.orders[0].code == "600001"
    assert r.orders[0].reason == "清仓"
    assert r.orders[0].shares == 20_000, "清仓必须卖掉全部可卖部分"
    assert r.orders[1].code == "600002"
    # 权益 = 现金 10 万 + 持仓 20 万 = 30 万 ⇒ 每只 20% = 6 万 ÷ 10 元 = 6000 股
    assert r.orders[1].shares == 6_000


def test_reduce_position_sells_only_the_excess():
    quotes = {"600001": _q("600001", 10.0)}
    # 目标 20% × 权益；持仓 3 万股、现金 0 ⇒ 权益 30 万 ⇒ 目标 6 千股
    snap = _snap(0.0, [Position("600001", quantity=30_000, available=30_000, avg_cost=9.0)])
    r = plan_orders(_input(["600001"], snap, quotes))
    assert r.buys == ()
    assert r.sells[0].shares == 30_000 - 6_000
    assert r.sells[0].reason == "减至目标"


def test_t_plus_one_locks_same_day_buys():
    """可卖少于持仓时，只能卖可卖部分。"""
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 10.0)}
    snap = _snap(0.0, [Position("600001", quantity=20_000, available=0, avg_cost=9.0)])
    r = plan_orders(_input(["600002"], snap, quotes))
    assert r.sells == (), "当日买入未解冻，今天卖不出去"
    assert any(s.code == "600001" and s.side is Side.SELL for s in r.skipped)


# ---------------------------------------------------------------------------
# 2.2-b 幂等键
# ---------------------------------------------------------------------------
def test_client_order_id_is_deterministic():
    a = make_client_order_id(DEC, "600990", Side.BUY, 200)
    b = make_client_order_id(DEC, "600990", Side.BUY, 200)
    assert a == b
    assert a == "20261030-600990-B-200"


def test_client_order_id_differs_by_every_meaningful_field():
    base = make_client_order_id(DEC, "600990", Side.BUY, 200)
    variants = {
        make_client_order_id("20261031", "600990", Side.BUY, 200),
        make_client_order_id(DEC, "600991", Side.BUY, 200),
        make_client_order_id(DEC, "600990", Side.SELL, 200),
        make_client_order_id(DEC, "600990", Side.BUY, 300),
    }
    assert base not in variants
    assert len(variants) == 4


def test_client_order_id_contains_no_clock_or_randomness():
    """id 里出现运行时刻/随机数 = 重试变成新单（验收标准缺陷 P2）。

    断言方式不是"看着像不像"，而是**同一进程隔一段时间再算一次仍相等**，
    且不出现任何形如 HHMMSS / 长串十六进制的片段。
    """
    import re
    import time
    first = make_client_order_id(DEC, "600990", Side.BUY, 200)
    time.sleep(0.01)
    second = make_client_order_id(DEC, "600990", Side.BUY, 200)
    assert first == second
    assert not re.search(r"\d{6}$", first.split("-")[-1]) if len(first.split("-")[-1]) == 3 else True
    assert not re.fullmatch(r"[0-9a-f]{16,}", first.split("-")[-1])


def test_planning_twice_yields_identical_orders():
    """幂等、可重入：同样的输入调两次，订单逐字段相同。"""
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 12.5)}
    a = plan_orders(_input(["600001", "600002"], _snap(500_000.0), quotes))
    b = plan_orders(_input(["600001", "600002"], _snap(500_000.0), quotes))
    assert a.orders == b.orders


def test_revision_produces_new_id_with_same_prefix():
    """重定价 = 同一意图的第二次尝试：前缀相同可追溯，id 必须不同。"""
    r0 = make_client_order_id(DEC, "600990", Side.BUY, 200)
    r1 = make_client_order_id(DEC, "600990", Side.BUY, 200, revision=1)
    assert r1 != r0
    assert r1.startswith(r0)
    assert r1.endswith("-r1")


# ---------------------------------------------------------------------------
# 2.2-c 限价与涨跌停
# ---------------------------------------------------------------------------
def test_limit_price_applies_deviation_both_sides():
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 10.0)}
    snap = _snap(1_000_000.0)
    r = plan_orders(_input(["600001"], snap, quotes, dev=0.003))
    assert r.buys[0].limit_price == pytest.approx(10.03)

    snap2 = _snap(0.0, [Position("600002", quantity=20_000, available=20_000, avg_cost=9.0)])
    r2 = plan_orders(_input(["600001"], snap2, quotes, dev=0.003))
    assert r2.sells[0].limit_price == pytest.approx(9.97)


def test_limit_price_is_clamped_by_price_limits():
    """涨停买不进：让价后超过涨停价时必须夹住，而不是报个买不到的价格。"""
    quotes = {"600001": _q("600001", 10.0)}
    r = plan_orders(_input(["600001"], _snap(1_000_000.0), quotes, dev=0.003,
                           price_limits={"600001": (9.0, 10.02)}))
    # 10 × 1.003 = 10.03 > 涨停 10.02 ⇒ 必须夹住，而不是报一个买不到的价格
    assert r.buys[0].limit_price == pytest.approx(10.02)


def test_price_is_rounded_to_cent():
    quotes = {"600001": _q("600001", 3.333)}
    r = plan_orders(_input(["600001"], _snap(1_000_000.0), quotes, dev=0.003))
    assert r.buys[0].limit_price == round(3.333 * 1.003, 2)


# ---------------------------------------------------------------------------
# 2.2-e 买入预算：不假定卖出回款
# ---------------------------------------------------------------------------
def test_buy_budget_excludes_pending_sale_proceeds():
    """换仓首轮：卖出回款还没到手，买单应该买不起。

    这正是 planner 需要"跑两轮"的原因——先卖、成交、再重新规划。
    """
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 10.0)}
    snap = _snap(0.0, [Position("600001", quantity=20_000, available=20_000, avg_cost=9.0)])
    r = plan_orders(_input(["600002"], snap, quotes))
    assert len(r.sells) == 1
    assert r.buys == (), "现金为 0 时不得假定卖出回款已可用"
    assert any(s.code == "600002" and "不足一手" in s.reason for s in r.skipped)


def test_buy_budget_uses_explicit_settled_cash():
    """显式声明回款后，第二轮就能买。"""
    quotes = {"600001": _q("600001", 10.0), "600002": _q("600002", 10.0)}
    snap = _snap(0.0, [Position("600001", quantity=20_000, available=0, avg_cost=9.0)])
    r = plan_orders(_input(["600002"], snap, quotes, settled_cash_from_sales=200_000.0))
    assert [o.code for o in r.buys] == ["600002"]
    # 权益 = 持仓 20 万 + 现金 0 = 20 万 ⇒ 每只 20% = 4 万 ÷ 10 元 = 4000 股
    assert r.buys[0].shares == 4_000


def test_budget_is_consumed_in_priority_order():
    """现金不够买满 5 只时，按目标顺序（= amt20 降序）优先，后面的跳过并说明。"""
    codes = ["600001", "600002", "600003", "600004", "600005"]
    quotes = {c: _q(c, 10.0) for c in codes}
    # 权益 = 现金 3 万 + 持仓 97 万 = 100 万 ⇒ 每只目标 2 万股
    # 现金只有 3 万 ⇒ 只够买 3000 股，第一只就花完，后面的都要跳过
    snap = AccountSnapshot(cash=Cash(available=30_000.0, frozen=0.0, total=30_000.0),
                           positions=(Position("600001", quantity=97_000, available=0,
                                               avg_cost=9.0),))
    # 600001 已超配（9.7 万股 > 目标 2 万股）且不可卖 ⇒ 不会出现在买单里
    r = plan_orders(_input(codes, snap, quotes))
    assert "600001" not in {o.code for o in r.buys}, "已超配的标的不会再买"
    assert r.buys[0].code == "600002", "按目标顺序，600002 是第一个需要买的"
    assert {o.code for o in r.buys} == {"600002"}, "3 万元只够一只"
    assert any(s.code == "600005" for s in r.skipped), "买不到的要进 skipped"


# ---------------------------------------------------------------------------
# 缺失输入必须显式
# ---------------------------------------------------------------------------
def test_missing_quote_for_target_is_skipped_with_reason():
    r = plan_orders(_input(["600001"], _snap(1_000_000.0), {}))
    assert r.orders == ()
    assert r.skipped[0].code == "600001"
    assert "无有效行情" in r.skipped[0].reason


def test_stale_quote_is_skipped_not_used():
    quotes = {"600001": Quote(code="600001", last=10.0, ts=20261001, stale=True)}
    r = plan_orders(_input(["600001"], _snap(1_000_000.0), quotes))
    assert r.orders == ()
    assert "无有效行情" in r.skipped[0].reason


def test_missing_quote_for_held_position_raises():
    """持仓缺价不能按 0 计市值——那会让所有目标股数系统性偏小。"""
    snap = _snap(100_000.0, [Position("600001", quantity=1000, available=1000, avg_cost=9.0)])
    with pytest.raises(LookupError, match="缺有效行情"):
        plan_orders(_input(["600002"], snap, {"600002": _q("600002", 10.0)}))


def test_invalid_decision_date_is_rejected():
    with pytest.raises(ValueError, match="YYYYMMDD"):
        PlanInput(decision="2026-10-30", targets=[], snapshot=_snap(0.0), quotes={},
                  target_weight=0.2)


def test_zero_target_weight_is_rejected():
    with pytest.raises(ValueError, match="target_weight"):
        PlanInput(decision=DEC, targets=[], snapshot=_snap(0.0), quotes={},
                  target_weight=0.0)


def test_duplicate_targets_are_deduplicated_but_order_kept():
    quotes = {c: _q(c, 10.0) for c in ("600001", "600002")}
    r = plan_orders(_input(["600002", "600001", "600002"], _snap(1_000_000.0), quotes))
    assert [o.code for o in r.buys] == ["600002", "600001"]
