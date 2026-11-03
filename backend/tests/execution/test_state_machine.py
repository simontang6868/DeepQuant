"""Phase 2.4：委托状态机与崩溃恢复的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.4-a ~ 2.4-d。

这一节最容易"看着都对、实际全错"的地方
--------------------------------------
状态机写成一个 `if/elif` 大表、非法转移 `return False`、重建时顺手把本地缓存
当起点——三者都能跑通、单次运行完全正常，但**账实不符时查不出来**。
所以这里的断言全部落在"能不能抛/能不能拦"上，不落在"返回值看起来合理"上。

⭐ 三条独立的防线，各自都有测试
1. `TRANSITIONS` 转移表（2.4-b）
2. `ReadOnlyBroker` 白名单 + 显式拦截（2.4-d）
3. `apply_rebuild_to_gate_state` 只改 `has_unknown_orders`（P5 复发点）

⭐ 一个必须有的**集成**测试：`test_unknown_blocks_new_orders_end_to_end`。
状态机自己的测试绿、`RiskGate` 自己的测试绿，两者之间的接线断了照样能全绿——
所以这里从"重建发现 UNKNOWN"一路跑到"闸门拒单"，断言原因码。
"""

from __future__ import annotations

import datetime as dt

import pytest

from deepquant.broker.errors import RejectCode
from deepquant.broker.models import (
    TERMINAL_STATUSES,
    BrokerOrder,
    BrokerOrderStatus,
    Cash,
    Fill,
    OrderRequest,
    Position,
    Quote,
)
from deepquant.execution.risk_gate import (
    Authorization,
    GateState,
    OrderContext,
    RiskGate,
    RiskLimits,
)
from deepquant.execution.state_machine import (
    READ_ONLY_METHODS,
    TRANSITIONS,
    IllegalTransition,
    OrderRegistry,
    OrderStateMachine,
    ReadOnlyBroker,
    ReadOnlyViolation,
    apply_rebuild_to_gate_state,
    rebuild_from_broker,
)
from deepquant.market.rules import Side

DAY = 20261030
COID = "20261030-600990-B-200"


# --------------------------------------------------------------------------- 构造


def _order(status=BrokerOrderStatus.ACCEPTED, *, filled: int = 0, shares: int = 200,
           ts: int = 1, coid: str = COID, code: str = "600990",
           reject_code: str = "") -> BrokerOrder:
    return BrokerOrder(
        client_order_id=coid, broker_order_id="B-" + coid, code=code,
        side=Side.BUY, shares=shares, limit_price=10.0, status=status,
        filled_shares=filled, avg_fill_price=10.0 if filled else 0.0,
        reject_code=reject_code, updated_ts=ts,
    )


class _StubBroker:
    """只读桩：把"券商事实"直接给定，不做任何撮合。

    ⚠️ 重建测试**必须**用桩而不是 ``MockBroker``：MockBroker 是"故障注入券商"，
    会自己算成交。重建要验的是"券商说什么就是什么"，需要能精确指定它说什么。
    """

    def __init__(self, *, positions=(), cash=None, orders=(), fills=()):
        self._positions = tuple(positions)
        self._cash = cash or Cash(available=10_000.0, frozen=0.0, total=10_000.0)
        self._orders = tuple(orders)
        self._fills = tuple(fills)
        self.calls: list[str] = []

    def get_positions(self):
        self.calls.append("get_positions")
        return self._positions

    def get_cash(self):
        self.calls.append("get_cash")
        return self._cash

    def get_orders(self, **kw):
        self.calls.append("get_orders")
        return self._orders

    def get_fills(self, **kw):
        self.calls.append("get_fills")
        return self._fills

    def place_order(self, req):
        self.calls.append("place_order")
        raise AssertionError("重建路径不该下单")

    def cancel_order(self, coid):
        self.calls.append("cancel_order")
        raise AssertionError("重建路径不该撤单")


# ---------------------------------------------------------------------------
# 2.4-b 非法状态转移必须抛异常（不得静默改字段）
# ---------------------------------------------------------------------------


def test_terminal_state_cannot_go_back_to_partial_fill():
    """``FILLED → PARTIALLY_FILLED``：终态回退。真实世界里这只会出现在本地乱改。"""
    sm = OrderStateMachine(_order(BrokerOrderStatus.FILLED, filled=200))
    with pytest.raises(IllegalTransition) as ei:
        sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=2))
    assert "终态" in str(ei.value)
    # 抛了之后状态必须**保持不变**：抛异常却已经改了一半，比不抛更糟
    assert sm.status is BrokerOrderStatus.FILLED
    assert sm.order.filled_shares == 200


def test_terminal_state_rejects_different_fill_quantity():
    sm = OrderStateMachine(_order(BrokerOrderStatus.CANCELLED))
    with pytest.raises(IllegalTransition):
        sm.apply(_order(BrokerOrderStatus.CANCELLED, filled=100, ts=2))


def test_duplicate_terminal_report_is_idempotent_not_error():
    """同一终态的重复回报应当是"无操作"，而不是异常。

    ⚠️ 这条与上面两条不矛盾：重复**回报**必须幂等（否则一重发就崩），
    状态**变化**必须抛。区分二者靠 ``(status, filled_shares)`` 是否全同。
    """
    sm = OrderStateMachine(_order(BrokerOrderStatus.FILLED, filled=200))
    out = sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2))
    assert out is sm.order
    assert sm.status is BrokerOrderStatus.FILLED


def test_accepted_cannot_go_back_to_pending_new():
    """反向转移（券商把已接受的单退回"待确认"）必须是错误，不是状态刷新。"""
    sm = OrderStateMachine(_order(BrokerOrderStatus.ACCEPTED))
    with pytest.raises(IllegalTransition) as ei:
        sm.apply(_order(BrokerOrderStatus.PENDING_NEW, ts=2))
    assert "合法后继" in str(ei.value)


def test_cancelled_cannot_become_filled():
    """已撤又成交：说明撤单回报与成交回报顺序错乱，必须暴露而不是覆盖。"""
    sm = OrderStateMachine(_order(BrokerOrderStatus.CANCELLED))
    with pytest.raises(IllegalTransition):
        sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2))


def test_illegal_transition_raises_instead_of_returning_false():
    """必须是异常。``return False`` 的写法调用方不看返回值就白做了。

    本项目在闸门上吃过"写了警告却仍计入通过"的教训，所以这里断言的是
    ``pytest.raises``——如果实现改成返回 ``False``，这条会立刻红。
    """
    sm = OrderStateMachine(_order(BrokerOrderStatus.FILLED, filled=200))
    with pytest.raises(IllegalTransition):
        sm.apply(_order(BrokerOrderStatus.ACCEPTED, ts=2))


def test_filled_shares_cannot_decrease():
    sm = OrderStateMachine(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=150, ts=5))
    with pytest.raises(IllegalTransition) as ei:
        sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=6))
    assert "不可减少" in str(ei.value)


def test_identity_fields_cannot_be_mutated():
    """把 ``shares`` 改小就能伪装成"合法的部分成交"——必须拦住。"""
    sm = OrderStateMachine(_order(BrokerOrderStatus.ACCEPTED, shares=200))
    with pytest.raises(ValueError) as ei:
        sm.apply(_order(BrokerOrderStatus.ACCEPTED, shares=100, ts=2))
    assert "身份字段" in str(ei.value)


def test_logical_clock_cannot_go_backwards():
    sm = OrderStateMachine(_order(BrokerOrderStatus.ACCEPTED, ts=9))
    with pytest.raises(IllegalTransition) as ei:
        sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=8))
    assert "时间倒退" in str(ei.value)


def test_partial_fill_can_progress_and_marks_jump_only_on_skipped_edge():
    sm = OrderStateMachine(_order(BrokerOrderStatus.PENDING_NEW, ts=1))
    sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2))
    assert sm.jumped is True, "PENDING_NEW 直接 FILLED 属跳级，必须留痕"

    sm2 = OrderStateMachine(_order(BrokerOrderStatus.PENDING_NEW, ts=1))
    sm2.apply(_order(BrokerOrderStatus.ACCEPTED, ts=2))
    sm2.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=3))
    assert sm2.jumped is False, "正常推进不算跳级"


def test_transition_table_covers_every_status():
    """新增枚举值却忘了配转移表 ⇒ 该状态永远不可达且无人发现。

    ``TRANSITIONS[status]`` 在 ``apply`` 里是**直接下标**，漏配会 KeyError；
    这条测试把"漏配"提前到测试期。
    """
    missing = [s for s in BrokerOrderStatus if s not in TRANSITIONS]
    assert missing == [], f"这些状态没有配转移表：{[s.value for s in missing]}"


def test_terminal_states_have_no_outgoing_transitions():
    for s in TERMINAL_STATUSES:
        assert TRANSITIONS[s] == frozenset(), (
            f"{s.value} 是终态，不应有后继；实际有 "
            f"{sorted(x.value for x in TRANSITIONS[s])}")


# ---------------------------------------------------------------------------
# 2.4-a 存在 UNKNOWN 时禁止发新单
# ---------------------------------------------------------------------------


def test_unknown_is_blocking_and_others_are_not():
    for status in BrokerOrderStatus:
        # FILLED 必须全额成交，否则 BrokerOrder 自己就不让构造——
        # 这条保护是好的：它挡住了"状态与数量自相矛盾"的委托。
        filled = 200 if status is BrokerOrderStatus.FILLED else 0
        sm = OrderStateMachine(_order(status, filled=filled))
        assert sm.is_blocking() is (status is BrokerOrderStatus.UNKNOWN), (
            f"{status.value} 的阻断判定写错了")


def test_registry_reports_blocking_when_any_unknown():
    reg = OrderRegistry([_order(BrokerOrderStatus.FILLED, filled=200, coid="a-1"),
                         _order(BrokerOrderStatus.UNKNOWN, coid="a-2")])
    assert reg.blocking() is True
    assert reg.unknown_ids() == ("a-2",)

    reg2 = OrderRegistry([_order(BrokerOrderStatus.FILLED, filled=200, coid="a-1")])
    assert reg2.blocking() is False
    assert reg2.unknown_ids() == ()


def test_unknown_cannot_be_released_by_local_inference():
    """⭐ 2.4-c 的可执行形式：本地不得把 UNKNOWN 自行改成 FILLED。"""
    sm = OrderStateMachine(_order(BrokerOrderStatus.UNKNOWN))
    with pytest.raises(IllegalTransition) as ei:
        sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2), source="local")
    assert "券商" in str(ei.value)
    assert sm.status is BrokerOrderStatus.UNKNOWN
    assert sm.is_blocking() is True


def test_unknown_can_be_released_by_broker_data():
    sm = OrderStateMachine(_order(BrokerOrderStatus.UNKNOWN))
    sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2), source="broker")
    assert sm.status is BrokerOrderStatus.FILLED
    assert sm.is_blocking() is False


def test_unknown_blocks_new_orders_end_to_end():
    """⭐ 集成：重建发现 UNKNOWN ⇒ 闸门必须拒新单，原因码可检索。

    状态机自己的测试绿、RiskGate 自己的测试绿，中间接线断了照样全绿。
    所以这里从 rebuild 一路跑到 evaluate。
    """
    broker = _StubBroker(
        positions=[Position("600990", quantity=200, available=200, avg_cost=10.0)],
        orders=[_order(BrokerOrderStatus.UNKNOWN)],
    )
    rebuilt = rebuild_from_broker(broker, ts=DAY)
    assert rebuilt.blocked is True

    state = GateState(peak_equity=20_000.0, trading_day=DAY,
                      last_reconciliation_ok=True)
    apply_rebuild_to_gate_state(rebuilt, state)

    gate = RiskGate(
        limits=RiskLimits.for_strategy_capital(
            stock_capital=20_000.0, n_hold=5, max_price_deviation=0.01,
            max_quote_age_days=5, max_drawdown=0.025, max_daily_orders=12),
        state=state,
        authorization=Authorization(max_notional=20_000.0,
                                    valid_until=dt.date(2027, 12, 31),
                                    boards=frozenset({"SH_MAIN"})),
    )
    d = gate.evaluate(OrderContext(
        order=OrderRequest(client_order_id="x", code="600990", side=Side.BUY,
                           shares=100, limit_price=10.0),
        quote=Quote(code="600990", last=10.0, ts=DAY, stale=False),
        equity=20_000.0, position_value=2_000.0, board="SH_MAIN",
        is_trading_hours=True, trading_day=DAY, halt_file_present=False,
    ))
    assert d.allowed is False
    assert d.code is RejectCode.UNKNOWN_ORDER_PENDING
    # ⚠️ 断言 `failure.check` 而不是 `"unknown_orders" in d.checked`：
    #    `checked` 是**已通过**的检查列表，触发拒单的那条不在里面。
    #    写成 `in d.checked` 会永远红，然后很容易被"改成断言别的"糊弄过去。
    assert d.failure.check == "unknown_orders"


def test_gate_reopens_after_unknown_resolved_by_broker():
    """UNKNOWN 解封后闸门必须恢复——否则一次回报丢失就永久停摆。"""
    broker = _StubBroker(orders=[_order(BrokerOrderStatus.FILLED, filled=200)])
    rebuilt = rebuild_from_broker(broker, ts=DAY)
    assert rebuilt.blocked is False

    state = GateState(peak_equity=20_000.0, trading_day=DAY,
                      last_reconciliation_ok=True, has_unknown_orders=True)
    apply_rebuild_to_gate_state(rebuilt, state)
    assert state.has_unknown_orders is False


# ---------------------------------------------------------------------------
# 2.4-c 重启后以券商为准重建
# ---------------------------------------------------------------------------


def test_rebuild_takes_broker_positions_not_local_cache():
    """本地记 1000 股、券商说 800 股 ⇒ 重建结果必须是 800。"""
    local = OrderRegistry()          # 本地没有任何"持仓缓存"可用——这正是设计意图
    broker = _StubBroker(
        positions=[Position("600990", quantity=800, available=800, avg_cost=10.0)],
        cash=Cash(available=1_000.0, frozen=0.0, total=1_000.0),
    )
    rebuilt = rebuild_from_broker(broker, ts=DAY, local=local)
    assert rebuilt.position_map() == {"600990": 800}
    assert rebuilt.cash.available == pytest.approx(1_000.0)


def test_rebuild_adopts_order_that_local_never_saw():
    """⭐ 缺陷预登记 P3 的方向：「券商有、本地无」。

    只遍历本地去券商侧查的实现，永远看不到这类委托（手工交易、漏录回报）。
    """
    local = OrderRegistry()                        # 本地空
    broker = _StubBroker(orders=[_order(BrokerOrderStatus.FILLED, filled=200, coid="m-1")])
    rebuilt = rebuild_from_broker(broker, ts=DAY, local=local)

    assert [o.client_order_id for o in rebuilt.orders] == ["m-1"]
    assert len(rebuilt.overrides) == 1
    ov = rebuilt.overrides[0]
    assert ov.field == "adopted"
    assert ov.local == "(缺失)"
    assert "券商有、本地无" in ov.detail


def test_rebuild_reports_local_only_order_without_deleting_it():
    """「本地有、券商无」只报告，不自动删。

    自动删的后果：这笔委托从此在两边的账上都消失，没人知道它去哪了。
    """
    local = OrderRegistry([_order(BrokerOrderStatus.ACCEPTED, coid="ghost-1")])
    broker = _StubBroker(orders=[])
    rebuilt = rebuild_from_broker(broker, ts=DAY, local=local)

    assert rebuilt.orders == ()
    assert any(o.field == "missing_at_broker" and o.client_order_id == "ghost-1"
               for o in rebuilt.overrides)
    # 本地登记表里那笔仍在（未被删）
    assert "ghost-1" in local


def test_rebuild_can_override_local_over_optimism():
    """本地虚报成交（FILLED）、券商说只成了 100 ⇒ 以券商为准，且记覆盖。

    ⚠️ 这条故意违反 ``apply`` 的"终态不可离开"。理由：重建的前提就是本地可能错，
    虚报成交比漏报更危险（仓位看起来更满 ⇒ 可能超买）。所以重建走
    ``reconcile_with_broker``，允许倒退但**必须留记录**。
    """
    local = OrderRegistry([_order(BrokerOrderStatus.FILLED, filled=200)])
    broker = _StubBroker(orders=[_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100,
                                        ts=9)])
    rebuilt = rebuild_from_broker(broker, ts=DAY, local=local)

    assert rebuilt.orders[0].status is BrokerOrderStatus.PARTIALLY_FILLED
    assert rebuilt.orders[0].filled_shares == 100
    ov = [o for o in rebuilt.overrides if o.field.startswith("status")]
    assert len(ov) == 1
    assert ov[0].local == "filled/200"
    assert ov[0].broker == "partially_filled/100"
    # 本地侧也已被改成券商的事实（否则下一轮又拿错的当基线）
    assert local.get(COID).order.filled_shares == 100


def test_rebuild_returns_no_override_when_consistent():
    local = OrderRegistry([_order(BrokerOrderStatus.FILLED, filled=200)])
    broker = _StubBroker(orders=[_order(BrokerOrderStatus.FILLED, filled=200, ts=5)])
    rebuilt = rebuild_from_broker(broker, ts=DAY, local=local)
    assert rebuilt.overrides == ()


def test_rebuild_output_is_sorted_for_reproducibility():
    """重建结果必须定序，否则「重放逐位相同」无从谈起。"""
    broker = _StubBroker(
        positions=[Position("600002", 100, 100), Position("600001", 200, 200)],
        orders=[_order(BrokerOrderStatus.ACCEPTED, coid="z-9"),
                _order(BrokerOrderStatus.ACCEPTED, coid="a-1")],
        fills=[Fill(fill_id="f2", client_order_id="a-1", code="600001", side=Side.BUY,
                    shares=100, price=10.0, ts=2),
               Fill(fill_id="f1", client_order_id="a-1", code="600001", side=Side.BUY,
                    shares=100, price=10.0, ts=1)],
    )
    r1 = rebuild_from_broker(broker, ts=DAY)
    r2 = rebuild_from_broker(broker, ts=DAY)
    assert [p.code for p in r1.positions] == ["600001", "600002"]
    assert [o.client_order_id for o in r1.orders] == ["a-1", "z-9"]
    assert [f.fill_id for f in r1.fills] == ["f1", "f2"]
    assert r1 == r2


# ---------------------------------------------------------------------------
# 2.4-d 重建只读
# ---------------------------------------------------------------------------


def test_readonly_broker_blocks_place_and_cancel():
    stub = _StubBroker()
    ro = ReadOnlyBroker(stub)
    with pytest.raises(ReadOnlyViolation, match="禁止下单"):
        ro.place_order(None)
    with pytest.raises(ReadOnlyViolation, match="禁止撤单"):
        ro.cancel_order("x")
    assert stub.calls == [], "被拦的调用不该真的到达券商"


def test_readonly_broker_rejects_methods_outside_whitelist():
    """白名单之外一律拒。将来 adapter 新增 ``place_orders`` 批量下单时，
    黑名单会漏掉它，白名单不会。"""
    ro = ReadOnlyBroker(_StubBroker())
    with pytest.raises(ReadOnlyViolation, match="白名单"):
        ro.place_orders([])
    assert "place_order" not in READ_ONLY_METHODS
    assert "place_orders" not in READ_ONLY_METHODS


def test_readonly_broker_records_only_reads():
    broker = _StubBroker(positions=[Position("600990", 100, 100)])
    rebuilt = rebuild_from_broker(broker, ts=DAY)
    assert set(rebuilt.reads) <= READ_ONLY_METHODS
    assert broker.calls == ["get_positions", "get_cash", "get_orders", "get_fills"]


def test_rebuild_has_no_side_effect_on_real_mock_broker():
    """用真 ``MockBroker`` 验一遍：重建前后委托数量不变。

    桩 Broker 的 ``place_order`` 会抛，所以"没下单"是被构造出来的；这里用
    会真的记账的 MockBroker，验证"重建确实没动过它"。
    """
    from deepquant.backtest.config import BacktestConfig
    from deepquant.broker.mock import MockBroker

    cfg = BacktestConfig(symbol="600990", initial_cash=100_000.0)
    b = MockBroker(cost_config=cfg,
                   cash=Cash(available=100_000.0, frozen=0.0, total=100_000.0),
                   positions=[Position("600990", quantity=0, available=0)],
                   quotes={"600990": Quote(code="600990", last=10.0, ts=DAY)})
    b.place_order(OrderRequest(client_order_id=COID, code="600990", side=Side.BUY,
                               shares=200, limit_price=10.0))
    before = len(b.get_orders())
    cash_before = b.get_cash()

    rebuilt = rebuild_from_broker(b, ts=DAY)

    assert len(b.get_orders()) == before, "重建期间产生了新委托"
    assert b.get_cash() == cash_before
    assert len(rebuilt.orders) == before


# ---------------------------------------------------------------------------
# P5 复发点：重建不得覆盖峰值与熔断状态
# ---------------------------------------------------------------------------


def test_rebuild_does_not_reset_peak_equity():
    """⭐⭐ 缺陷预登记 P5 的复发点。

    重建时顺手 ``GateState(peak_equity=当前权益)`` 会把峰值降到现在，
    「相对峰值的回撤」被重算成 0 ⇒ 熔断永远不会触发。
    """
    state = GateState(peak_equity=100_000.0, trading_day=DAY,
                      last_reconciliation_ok=True)
    rebuilt = rebuild_from_broker(
        _StubBroker(cash=Cash(available=80_000.0, frozen=0.0, total=80_000.0)),
        ts=DAY)
    apply_rebuild_to_gate_state(rebuilt, state)
    assert state.peak_equity == pytest.approx(100_000.0), (
        "重建把峰值改小了 ⇒ 回撤被重算 ⇒ 熔断失效（P5）")
    # 此时回撤 20%，远超 2.5%，熔断应该仍然有效
    state2 = GateState(peak_equity=100_000.0, trading_day=DAY,
                       last_reconciliation_ok=True, breaker_tripped=True)
    apply_rebuild_to_gate_state(rebuilt, state2)
    assert state2.breaker_tripped is True, "重建不该解除熔断"


def test_apply_rebuild_only_touches_unknown_flag():
    """字段级白名单：重建只允许改 ``has_unknown_orders``。"""
    state = GateState(peak_equity=50_000.0, trading_day=DAY, daily_order_count=7,
                      last_reconciliation_ok=True, breaker_tripped=True,
                      has_unknown_orders=False)
    snapshot = (state.peak_equity, state.trading_day, state.daily_order_count,
                state.last_reconciliation_ok, state.breaker_tripped)

    rebuilt = rebuild_from_broker(_StubBroker(orders=[_order(BrokerOrderStatus.UNKNOWN)]),
                                  ts=DAY)
    apply_rebuild_to_gate_state(rebuilt, state)

    assert state.has_unknown_orders is True
    assert (state.peak_equity, state.trading_day, state.daily_order_count,
            state.last_reconciliation_ok, state.breaker_tripped) == snapshot, (
        "重建改了不该改的字段——日笔数与对账结论都属于当日事实，不该被重建重置")
