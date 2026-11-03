"""Phase 2.5：日终对账的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.5-a ~ 2.5-d。

这一节的组织方式
--------------
四类对象（持仓/资金/成交/委托）× 两个方向（本地有券商无、券商有本地无）×
每个字段——**逐条**写，不合并。合并成"改一处看有没有差异"的测试，只能证明
"对账跑得动"，证明不了"漏了哪条它会红"。

⭐ 三条被刻意钉死的性质
1. **两个方向**（2.5-b / P3）：只遍历本地去券商侧找的实现，在
   ``test_broker_only_*`` 系列上必红。
2. **差异必须带字段与差值**（2.5-c）：``Difference.field`` 为空直接抛。
   断言 ``delta`` 的**符号**也很重要——符号反了就说明"谁减谁"写错了，
   而"有差异"这个结论照样成立，光看有没有差异是看不出来的。
3. **容差方向相反**（2.5-d）：股数一股都不能差；金额一分钱以内算舍入。
   `test_money_rounding_tolerance_*` 三条把 0.01 的两侧都测了。
"""

from __future__ import annotations

import pytest

from deepquant.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    Cash,
    Fill,
    Position,
)
from deepquant.execution.reconciler import (
    MONEY_TOL_CENTS,
    DiffKind,
    Difference,
    ReconcileResult,
    apply_reconcile_to_gate_state,
    reconcile,
    reconcile_with_broker,
)
from deepquant.execution.risk_gate import GateState
from deepquant.market.rules import Side

DAY = 20261030


# --------------------------------------------------------------------------- 构造


def _pos(code="600990", qty=1000, avail=None, cost=10.0) -> Position:
    """``avail`` 默认跟随 ``qty``：Position 自己禁止 ``available > quantity``，
    写死不变量会让"数量不等"的用例在构造阶段就崩，测不到对账。"""
    return Position(code, quantity=qty,
                    available=qty if avail is None else avail, avg_cost=cost)


def _cash(avail=10_000.0, frozen=0.0, total=None) -> Cash:
    return Cash(available=avail, frozen=frozen,
                total=avail + frozen if total is None else total)


def _order(coid="c-1", status=BrokerOrderStatus.FILLED, *, filled=200,
           shares=200, code="600990") -> BrokerOrder:
    return BrokerOrder(client_order_id=coid, broker_order_id="B-" + coid,
                       code=code, side=Side.BUY, shares=shares,
                       limit_price=10.0, status=status, filled_shares=filled,
                       updated_ts=1)


def _fill(fid="f-1", coid="c-1", code="600990", shares=200, price=10.0) -> Fill:
    return Fill(fill_id=fid, client_order_id=coid, code=code, side=Side.BUY,
                shares=shares, price=price, ts=1)


def _run(**kw):
    """默认两侧完全一致，每次只破坏一处。"""
    base = dict(
        local_positions=[_pos()], broker_positions=[_pos()],
        local_cash=_cash(), broker_cash=_cash(),
        local_orders=[_order()], broker_orders=[_order()],
        local_fills=[_fill()], broker_fills=[_fill()],
        ts=DAY,
    )
    base.update(kw)
    return reconcile(**base)


# --------------------------------------------------------------------------- 基线


def test_identical_state_reconciles_clean():
    r = _run()
    assert r.ok is True
    assert r.diffs == ()
    assert r.duplicate_fill_ids == ()
    assert "通过" in r.summary()


# --------------------------------------------------------------------------- 2.5-b 两个方向：持仓


def test_broker_only_position_is_reported():
    """⭐ P3 方向：券商有、本地无（手工交易 / 漏录成交）。"""
    r = _run(local_positions=[], broker_positions=[_pos("600990", 1000)])
    assert r.ok is False
    d = r.diffs[0]
    assert d.kind is DiffKind.POSITION_MISSING_LOCAL
    assert d.code == "600990"
    assert d.local is None and d.broker == 1000
    assert d.delta == 1000, "差值符号必须是 券商 − 本地"


def test_local_only_position_is_reported():
    r = _run(local_positions=[_pos("600990", 1000)], broker_positions=[])
    d = r.diffs[0]
    assert d.kind is DiffKind.POSITION_MISSING_BROKER
    assert d.delta == -1000, "本地多出来的持仓，差值应为负"


def test_position_quantity_mismatch_reports_exact_delta():
    r = _run(local_positions=[_pos("600990", 1000)],
             broker_positions=[_pos("600990", 800)])
    d = r.diffs[0]
    assert d.kind is DiffKind.POSITION_QUANTITY
    assert d.field == "quantity"
    assert (d.local, d.broker, d.delta) == (1000, 800, -200)


def test_position_available_mismatch_is_separate_from_quantity():
    """数量对、可卖不对：T+1 解冻判断错。只比 quantity 的实现会漏掉。"""
    r = _run(local_positions=[_pos("600990", 1000, avail=1000)],
             broker_positions=[_pos("600990", 1000, avail=0)])
    assert len(r.diffs) == 1
    assert r.diffs[0].kind is DiffKind.POSITION_AVAILABLE
    assert r.diffs[0].field == "available"
    assert r.diffs[0].delta == -1000


def test_share_counts_have_no_tolerance():
    """⭐ 2.5-d：股数**不允许容差**，差 1 股也必须在账上体现。"""
    r = _run(local_positions=[_pos("600990", 1000)],
             broker_positions=[_pos("600990", 999)])
    assert r.ok is False
    assert r.diffs[0].kind is DiffKind.POSITION_QUANTITY
    assert r.diffs[0].delta == -1


# --------------------------------------------------------------------------- 2.5-a 资金三字段


def test_cash_swap_between_available_and_frozen_is_caught():
    """⚠️ 只比 total 会漏掉"冻结没释放"：总钱数对、可用不对，实盘就是买不了。

    注意 ``Cash`` 自己强制 ``total = available + frozen``，所以"只改 total"
    这种场景在模型层面就不存在。真正能骗过"只比 total"的实现的是**互换**：
    available 少 3000、frozen 多 3000，total 一分不差。
    """
    local = Cash(available=10_000.0, frozen=0.0, total=10_000.0)
    broker = Cash(available=7_000.0, frozen=3_000.0, total=10_000.0)
    r = _run(local_cash=local, broker_cash=broker)

    kinds = {d.kind for d in r.diffs}
    assert kinds == {DiffKind.CASH_AVAILABLE, DiffKind.CASH_FROZEN}, (
        f"实际报出 {sorted(k.value for k in kinds)}——只比 total 的实现在这里会判通过")
    avail = [d for d in r.diffs if d.kind is DiffKind.CASH_AVAILABLE][0]
    assert avail.field == "available"
    assert avail.delta == -3_000.0


def test_cash_frozen_mismatch_alone_is_caught():
    local = Cash(available=5_000.0, frozen=5_000.0, total=10_000.0)
    broker = Cash(available=5_000.0, frozen=4_000.0, total=9_000.0)
    r = _run(local_cash=local, broker_cash=broker)
    kinds = {d.kind for d in r.diffs}
    assert DiffKind.CASH_FROZEN in kinds
    assert DiffKind.CASH_TOTAL in kinds
    assert DiffKind.CASH_AVAILABLE not in kinds, "available 两侧相等，不该报"


# --------------------------------------------------------------------------- 2.5-d 金额容差两侧


def test_money_rounding_within_one_cent_is_ignored():
    """差 1 分 ⇒ 视为舍入，不报。"""
    r = _run(local_cash=_cash(avail=10_000.00), broker_cash=_cash(avail=10_000.01))
    assert r.ok is True, f"1 分差被当成差异：{[str(d) for d in r.diffs]}"


def test_money_rounding_over_one_cent_is_reported():
    """差 2 分 ⇒ 必须报。"""
    r = _run(local_cash=_cash(avail=10_000.00), broker_cash=_cash(avail=10_000.02))
    assert r.ok is False
    d = [x for x in r.diffs if x.kind is DiffKind.CASH_AVAILABLE][0]
    assert d.delta == pytest.approx(0.02)


def test_money_tolerance_uses_integer_cents_not_float_compare():
    """⭐ 这条钉住"用整数分"这个实现选择。

    ``100.01 - 100.00`` 在 float64 下是 ``0.010000000000000009``，**严格大于**
    0.01。若实现写成 ``abs(a-b) <= 0.01``，一分钱的舍入会被判成差异。
    """
    a, b = 100.00, 100.01
    assert b - a > 0.01, "前提不成立：本机 float 下 1 分差竟然 ≤ 0.01"
    r = _run(local_cash=_cash(avail=a), broker_cash=_cash(avail=b))
    assert r.ok is True, "float 直接比较的实现会在这里红"


# --------------------------------------------------------------------------- 2.5-b 两个方向：委托


def test_broker_only_order_is_reported():
    r = _run(local_orders=[], broker_orders=[_order("spy-1")])
    d = r.diffs[0]
    assert d.kind is DiffKind.ORDER_MISSING_LOCAL
    assert "spy-1" in d.detail


def test_local_only_order_is_reported():
    r = _run(local_orders=[_order("ghost-1")], broker_orders=[])
    d = r.diffs[0]
    assert d.kind is DiffKind.ORDER_MISSING_BROKER
    assert "ghost-1" in d.detail


def test_order_status_mismatch_is_reported():
    r = _run(local_orders=[_order("c-1", BrokerOrderStatus.FILLED, filled=200)],
             broker_orders=[_order("c-1", BrokerOrderStatus.PARTIALLY_FILLED, filled=200)])
    d = r.diffs[0]
    assert d.kind is DiffKind.ORDER_STATUS
    assert (d.local, d.broker) == ("filled", "partially_filled")


def test_order_filled_shares_mismatch_is_reported():
    r = _run(local_orders=[_order("c-1", BrokerOrderStatus.PARTIALLY_FILLED, filled=200)],
             broker_orders=[_order("c-1", BrokerOrderStatus.PARTIALLY_FILLED, filled=100)])
    d = r.diffs[0]
    assert d.kind is DiffKind.ORDER_FILLED_SHARES
    assert d.delta == -100


# --------------------------------------------------------------------------- 2.5-b 两个方向：成交 + 去重


def test_broker_only_fill_is_reported():
    r = _run(local_fills=[], broker_fills=[_fill("f-9")])
    d = r.diffs[0]
    assert d.kind is DiffKind.FILL_MISSING_LOCAL
    assert "f-9" in d.detail


def test_local_only_fill_is_reported():
    r = _run(local_fills=[_fill("f-9")], broker_fills=[])
    d = r.diffs[0]
    assert d.kind is DiffKind.FILL_MISSING_BROKER
    assert d.delta == -200


def test_fill_price_and_shares_mismatch_are_separate():
    r = _run(local_fills=[_fill("f-1", shares=200, price=10.00)],
             broker_fills=[_fill("f-1", shares=100, price=10.50)])
    kinds = {d.kind for d in r.diffs}
    assert kinds == {DiffKind.FILL_SHARES, DiffKind.FILL_PRICE}
    price = [d for d in r.diffs if d.kind is DiffKind.FILL_PRICE][0]
    assert price.delta == pytest.approx(0.5)


def test_duplicate_report_is_deduped_and_flagged_not_counted_as_extra_fill():
    """⭐⭐ 回报重复：必须去重后比对，但**不能静默**。

    不去重 ⇒ 同一笔成交被当成两笔 ⇒ 本地"少了一笔"的假差异（方向还反了）；
    去重但不上报 ⇒ "回报重复"这个故障永远没人知道。
    """
    r = _run(local_fills=[_fill("f-1")],
             broker_fills=[_fill("f-1"), _fill("f-1")])
    assert r.ok is True, f"重复回报被当成差异：{[str(d) for d in r.diffs]}"
    assert r.duplicate_fill_ids == ("f-1",)
    assert "重复回报" in r.summary()


def test_duplicate_on_local_side_is_also_flagged():
    r = _run(local_fills=[_fill("f-1"), _fill("f-1")], broker_fills=[_fill("f-1")])
    assert r.ok is True
    assert r.duplicate_fill_ids == ("f-1",)


# --------------------------------------------------------------------------- 2.5-c 差异必须可定位


def test_difference_without_field_is_rejected():
    """⭐ 2.5-c 的可执行形式：只报"不一致"等于没报。"""
    with pytest.raises(ValueError, match="field"):
        Difference("cash", DiffKind.CASH_TOTAL, "", "", 1.0, 2.0, 1.0)


def test_difference_kind_must_be_enum():
    with pytest.raises(TypeError, match="DiffKind"):
        Difference("cash", "cash_total", "", "total", 1.0, 2.0, 1.0)  # type: ignore[arg-type]


def test_every_difference_is_human_readable_and_names_the_field():
    r = _run(local_positions=[_pos("600990", 1000)],
             broker_positions=[_pos("600990", 800)],
             local_cash=_cash(avail=1.0), broker_cash=_cash(avail=99.0))
    for d in r.diffs:
        s = str(d)
        assert d.field in s
        assert d.object in s
        assert "本地=" in s and "券商=" in s


def test_summary_counts_per_object():
    r = _run(local_positions=[], broker_positions=[_pos()],
             local_cash=_cash(avail=1.0), broker_cash=_cash(avail=99.0))
    s = r.summary()
    assert "positions=" in s and "cash=" in s
    # cash 会报 available 与 total 两条（total = available + frozen 恒成立），
    # 所以总数是 1 + 2 = 3。写 2 就是没算清 total 那条。
    assert "3 条差异" in s


# --------------------------------------------------------------------------- 集成与闸门


def test_reconcile_with_broker_reads_only():
    """对账也走 ``ReadOnlyBroker``：对账不该改变被对账的对象。"""
    class _Stub:
        def __init__(self):
            self.calls = []

        def get_positions(self):
            self.calls.append("get_positions")
            return [_pos()]

        def get_cash(self):
            self.calls.append("get_cash")
            return _cash()

        def get_orders(self, **k):
            self.calls.append("get_orders")
            return [_order()]

        def get_fills(self, **k):
            self.calls.append("get_fills")
            return [_fill()]

        def place_order(self, r):
            raise AssertionError("对账不该下单")

    stub = _Stub()
    r = reconcile_with_broker(stub, local_positions=[_pos()], local_cash=_cash(),
                              local_orders=[_order()], local_fills=[_fill()], ts=DAY)
    assert r.ok is True
    assert "place_order" not in stub.calls


def test_reconcile_failure_shuts_the_gate():
    state = GateState(peak_equity=100_000.0, trading_day=DAY,
                      last_reconciliation_ok=True)
    r = _run(local_positions=[], broker_positions=[_pos()])
    apply_reconcile_to_gate_state(r, state)
    assert state.last_reconciliation_ok is False


def test_reconcile_success_does_not_auto_clear_a_previous_failure():
    """⭐⭐ 对账通过**不会**自动把闸门打开。

    一条差异被"下次对账恰好没复现"而自动清除，等于系统自己宣布自己没问题。
    必须人工核对后显式 ``clear=True``。
    """
    state = GateState(peak_equity=100_000.0, trading_day=DAY,
                      last_reconciliation_ok=False)
    apply_reconcile_to_gate_state(_run(), state)
    assert state.last_reconciliation_ok is False, "对账通过就自动放行了"

    apply_reconcile_to_gate_state(_run(), state, clear=True)
    assert state.last_reconciliation_ok is True


def test_apply_reconcile_only_touches_reconciliation_flag():
    state = GateState(peak_equity=88_888.0, trading_day=DAY, daily_order_count=5,
                      breaker_tripped=True, has_unknown_orders=True,
                      last_reconciliation_ok=True)
    snap = (state.peak_equity, state.trading_day, state.daily_order_count,
            state.breaker_tripped, state.has_unknown_orders)
    apply_reconcile_to_gate_state(_run(local_cash=_cash(avail=1.0),
                                       broker_cash=_cash(avail=99.0)), state)
    assert state.last_reconciliation_ok is False
    assert (state.peak_equity, state.trading_day, state.daily_order_count,
            state.breaker_tripped, state.has_unknown_orders) == snap


def test_money_tolerance_constant_is_one_cent():
    """容差是"一分钱"，不是 0.01 元这个浮点数——用整数分表达。"""
    assert MONEY_TOL_CENTS == 1


def test_empty_result_is_ok():
    r = ReconcileResult()
    assert r.ok is True
    assert r.by_object() == {}
