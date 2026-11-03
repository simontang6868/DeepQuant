"""Phase 2.8：11 项故障注入（端到端）。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.8-a ~ 2.8-c。

验收标准列的 11 项 → 本文件的测试映射
------------------------------------
| # | 故障 | 测试 |
| --- | --- | --- |
| 1 | 断网 | `test_01_network_down` |
| 2 | 拒单（价格笼子/涨跌停/资金不足/最小单位） | `test_02a/02b/02c/02d` |
| 3 | 部分成交 | `test_03_partial_fill` |
| 4 | 回报丢失或重复 | `test_04a_dropped` / `test_04b_duplicated` |
| 5 | 行情陈旧 | `test_05_stale_quote` |
| 6 | 停牌 | `test_06_suspended` |
| 7 | 涨停买不进 | `test_07_limit_up` |
| 8 | 跌停卖不出 | `test_08_limit_down` |
| 9 | 日内重启 | `test_09_intraday_restart` |
| 10 | 时钟漂移 | `test_10_clock_drift` |
| 11 | 配置被篡改 | `test_11_config_tampered` |

02 拆四条（四种拒因**后果不同**：一处是规则拒、一处是资金拒、一处是报单格式拒），
04 拆两条（丢失的后果是"状态未知 + 账已变"，重复的后果是"账不变但回报异常"）。
把后果不同的情形合并成一条测试，就退化成"都跑通了"（2.8-a 明确禁止）。

⭐ 2.8-b 的落法：每条测试都断言**具体后果**——原因码、委托状态、持仓/资金数值、
告警类型、台账字段。没有一条断言停在"没崩"。

⭐⭐ 2.8-c 的落法：``test_11_config_tampered`` 验的是**运行时**校验
（``FrozenConfig.get`` 内部重算哈希），不是"加载时恰好是对的"。
另有一条 `test_config_guard_catches_tamper_between_freeze_and_use` 专门把这
两个时刻分开构造。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.broker.errors import NotConnected, OrderRejected, RejectCode
from deepquant.broker.mock import FaultScript, MockBroker
from deepquant.broker.models import (
    BrokerOrderStatus,
    Cash,
    OrderRequest,
    Position,
    Quote,
)
from deepquant.execution.alerts import (
    AlertKind,
    HeartbeatState,
    MemorySink,
    Severity,
    check_order_anomalies,
    check_reconcile,
    run_all_checks,
)
from deepquant.execution.config_guard import ConfigTampered, FrozenConfig
from deepquant.execution.ledger import DecisionSnapshot, Ledger
from deepquant.execution.planner import PlanInput, plan_orders
from deepquant.execution.reconciler import reconcile, reconcile_with_broker
from deepquant.execution.risk_gate import (
    Authorization,
    ClockWentBackwards,
    GateState,
    OrderContext,
    RiskGate,
    RiskLimits,
)
from deepquant.execution.state_machine import (
    OrderRegistry,
    apply_rebuild_to_gate_state,
    rebuild_from_broker,
)
from deepquant.market.rules import Side

DAY = 20261030
NEXT = 20261102
CODE = "600990"
H1 = "a" * 64
CAPITAL = 100_000.0


# --------------------------------------------------------------------------- 装配


def _cfg() -> BacktestConfig:
    return BacktestConfig(symbol=CODE, initial_cash=CAPITAL)


def _broker(*, positions=(), cash=None, quotes=None, script=None,
            connected=True) -> MockBroker:
    return MockBroker(
        cost_config=_cfg(),
        cash=cash or Cash(available=CAPITAL, frozen=0.0, total=CAPITAL),
        positions=positions,
        quotes=quotes if quotes is not None else {
            CODE: Quote(code=CODE, last=10.0, ts=DAY)},
        script=script, connected=connected,
    )


def _gate(*, state=None, max_notional=CAPITAL, boards=("SH_MAIN",)) -> RiskGate:
    return RiskGate(
        limits=RiskLimits.for_strategy_capital(
            stock_capital=CAPITAL, n_hold=5, max_price_deviation=0.01,
            max_quote_age_days=5, max_drawdown=0.025, max_daily_orders=12),
        state=state or GateState(peak_equity=CAPITAL, trading_day=DAY,
                                 last_reconciliation_ok=True),
        authorization=Authorization(max_notional=max_notional,
                                    valid_until=__import__("datetime").date(2027, 12, 31),
                                    boards=frozenset(boards)),
    )


def _ctx(*, price=10.0, shares=100, code=CODE, quote=None, equity=CAPITAL,
         position_value=0.0, board="SH_MAIN", day=DAY) -> OrderContext:
    return OrderContext(
        order=OrderRequest(client_order_id=f"{day}-{code}-B-{shares}", code=code,
                           side=Side.BUY, shares=shares, limit_price=price),
        quote=quote if quote is not None else Quote(code=code, last=10.0, ts=day),
        equity=equity, position_value=position_value, board=board,
        is_trading_hours=True, trading_day=day, halt_file_present=False,
    )


def _plan(*, targets=(CODE,), snap=None, quotes=None) -> PlanInput:
    return PlanInput(
        decision=str(DAY), targets=targets,
        snapshot=snap or __import__("deepquant.broker.models", fromlist=["AccountSnapshot"])
        .AccountSnapshot(cash=Cash(available=CAPITAL, frozen=0.0, total=CAPITAL),
                         positions=()),
        quotes=quotes or {CODE: Quote(code=CODE, last=10.0, ts=DAY)},
        target_weight=0.2,
    )


# --------------------------------------------------------------------------- 1. 断网


def test_01_network_down():
    """断网：下单与查询**都**失败，且失败是显式的。

    后果断言：``NotConnected``（不是返回空持仓、不是返回 0 资金）。
    返回空数据比抛异常危险得多——空持仓会让对账判"本地多出 1000 股"，
    然后人工去查一个根本不存在的差异。
    """
    b = _broker(connected=False)
    with pytest.raises(NotConnected):
        b.place_order(OrderRequest(client_order_id="x-1", code=CODE, side=Side.BUY,
                                   shares=100, limit_price=10.0))
    with pytest.raises(NotConnected):
        b.get_positions()
    with pytest.raises(NotConnected):
        b.get_cash()


def test_01b_network_down_does_not_look_like_empty_account():
    """⭐ 反向断言：断网**不能**返回空持仓/零资金。

    如果实现成 ``return ()``，对账会得出"券商说没有持仓、本地有 1000 股"
    ⇒ 一条完全虚构的差异，而且方向是"本地多记"。
    """
    b = _broker(connected=False)
    for fn in (b.get_positions, b.get_cash, b.get_orders, b.get_fills):
        with pytest.raises(NotConnected):
            fn()


# --------------------------------------------------------------------------- 2. 拒单（四种原因）


@pytest.mark.parametrize("reject_code", [
    RejectCode.PRICE_CAGE,
    RejectCode.INSUFFICIENT_CASH,
    RejectCode.INVALID_LOT,
])
def test_02a_reject_reason_codes_surface_unchanged(reject_code):
    """拒单：原因码必须**原样**透出，不能被归并成 ``OTHER``。

    后果断言：``OrderRejected.code`` 与原注入码相同，且委托被登记为
    ``REJECTED``（而不是"没发生过"）。
    """
    b = _broker(script=FaultScript(reject={CODE: reject_code}))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(OrderRequest(client_order_id="r-1", code=CODE, side=Side.BUY,
                                   shares=100, limit_price=10.0))
    assert ei.value.code is reject_code
    orders = b.get_orders()
    assert len(orders) == 1, "被拒的委托也要留下记录"
    assert orders[0].status is BrokerOrderStatus.REJECTED
    assert orders[0].reject_code == reject_code.value


def test_02b_insufficient_cash_is_computed_by_broker_not_guessed():
    """资金不足：由券商的资金账算出，不是 planner 猜的。

    现金 500 元、买 100 股 × 10 元 = 1000 元 ⇒ 券商拒 ``INSUFFICIENT_CASH``。
    """
    b = _broker(cash=Cash(available=500.0, frozen=0.0, total=500.0))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(OrderRequest(client_order_id="r-2", code=CODE, side=Side.BUY,
                                   shares=100, limit_price=10.0))
    assert ei.value.code is RejectCode.INSUFFICIENT_CASH


def test_02c_reject_leaves_cash_and_positions_untouched():
    """被拒之后账必须一分不动。"""
    b = _broker(script=FaultScript(reject={CODE: RejectCode.PRICE_CAGE}))
    before_cash = b.get_cash()
    before_pos = b.get_positions()
    with pytest.raises(OrderRejected):
        b.place_order(OrderRequest(client_order_id="r-3", code=CODE, side=Side.BUY,
                                   shares=100, limit_price=10.0))
    assert b.get_cash() == before_cash
    assert b.get_positions() == before_pos


def test_02d_reject_produces_a_WARN_alert_with_the_code():
    b = _broker(script=FaultScript(reject={CODE: RejectCode.PRICE_CAGE}))
    with pytest.raises(OrderRejected):
        b.place_order(OrderRequest(client_order_id="r-4", code=CODE, side=Side.BUY,
                                   shares=100, limit_price=10.0))
    alerts = check_order_anomalies(b.get_orders(), ts=DAY)
    assert len(alerts) == 1
    assert alerts[0].severity is Severity.WARN
    assert alerts[0].evidence["reject_code"] == "price_cage"


# --------------------------------------------------------------------------- 3. 部分成交


def test_03_partial_fill():
    """部分成交：委托状态 ``PARTIALLY_FILLED``、已成股数 < 委托、记账按实际成交。

    后果断言三件事：状态、已成股数、持仓增量。只看状态会漏掉"记了满仓"。
    """
    b = _broker(script=FaultScript(fill_ratio={CODE: 0.4}))
    o = b.place_order(OrderRequest(client_order_id="p-1", code=CODE, side=Side.BUY,
                                   shares=1000, limit_price=10.0))
    assert o.status is BrokerOrderStatus.PARTIALLY_FILLED
    assert o.filled_shares == 400, "1000 × 0.4 = 400"
    pos = {p.code: p for p in b.get_positions()}
    assert pos[CODE].quantity == 400
    fills = b.get_fills()
    assert sum(f.shares for f in fills) == 400


def test_03b_partial_fill_then_reconcile_is_clean():
    """部分成交后，本地记录与券商必须一致（不是"差 600 股"）。"""
    b = _broker(script=FaultScript(fill_ratio={CODE: 0.4}))
    b.place_order(OrderRequest(client_order_id="p-2", code=CODE, side=Side.BUY,
                               shares=1000, limit_price=10.0))
    r = reconcile_with_broker(b, local_positions=b.get_positions(),
                              local_cash=b.get_cash(),
                              local_orders=b.get_orders(),
                              local_fills=b.get_fills(), ts=DAY)
    assert r.ok is True, f"部分成交后对不上：{[str(d) for d in r.diffs]}"


# --------------------------------------------------------------------------- 4a. 回报丢失


def test_04a_dropped_fill_report():
    """回报丢失：委托停在 ``UNKNOWN``，**持仓已变但本地看不见成交**。

    后果断言：状态 UNKNOWN + 持仓已增 + ``get_fills`` 里没有这笔。
    三件事必须同时成立，只断言状态等于在测"字段被设成了 unknown"。
    """
    b = _broker(script=FaultScript(drop_fills=frozenset({CODE})))
    o = b.place_order(OrderRequest(client_order_id="d-1", code=CODE, side=Side.BUY,
                                   shares=200, limit_price=10.0))
    assert o.status is BrokerOrderStatus.UNKNOWN
    assert {p.code: p for p in b.get_positions()}[CODE].quantity == 200, "成交是真的发生了"
    assert b.get_fills() == (), "回报丢了 ⇒ 成交查询里看不到"

    # 局部的严重后果：本地若以为"没成交"，就会重复买
    alerts = check_order_anomalies([o], ts=DAY)
    assert alerts[0].severity is Severity.CRITICAL
    assert "阻断" in alerts[0].message


def test_04a_dropped_fill_blocks_new_orders_after_rebuild():
    """重建后仍是 UNKNOWN ⇒ 闸门拒绝所有新单，直到以券商为准解封。"""
    b = _broker(script=FaultScript(drop_fills=frozenset({CODE})))
    b.place_order(OrderRequest(client_order_id="d-2", code=CODE, side=Side.BUY,
                               shares=200, limit_price=10.0))
    rebuilt = rebuild_from_broker(b, ts=DAY)
    assert rebuilt.blocked is True
    assert rebuilt.unknown_client_order_ids == ("d-2",)

    state = GateState(peak_equity=CAPITAL, trading_day=DAY,
                      last_reconciliation_ok=True)
    apply_rebuild_to_gate_state(rebuilt, state)
    d = _gate(state=state).evaluate(_ctx())
    assert d.allowed is False
    assert d.code is RejectCode.UNKNOWN_ORDER_PENDING


# --------------------------------------------------------------------------- 4b. 回报重复


def test_04b_duplicated_fill_report():
    """回报重复：同一 ``fill_id`` 出现两次，**持仓只增一次**。

    后果断言：``get_fills`` 里同 id 两条、持仓 200（不是 400）、
    对账去重后无差异但报出重复。
    """
    b = _broker(script=FaultScript(duplicate_fills=frozenset({CODE})))
    b.place_order(OrderRequest(client_order_id="u-1", code=CODE, side=Side.BUY,
                               shares=200, limit_price=10.0))
    fills = b.get_fills()
    ids = [f.fill_id for f in fills]
    assert len(ids) == 2 and ids[0] == ids[1], "必须真的重复同一 fill_id"

    pos_qty = {p.code: p for p in b.get_positions()}[CODE].quantity
    assert pos_qty == 200, f"持仓被重复回报加成了：{pos_qty}（应为 200）"

    # ⚠️ 必须传 local_orders：不传的话对账会报"券商有、本地无"——那是**真实**
    #    的差异报告，不是去重失败。这里要验的是"重复回报去重后账是平的"。
    r = reconcile_with_broker(b, local_positions=b.get_positions(),
                              local_cash=b.get_cash(),
                              local_orders=b.get_orders(),
                              local_fills=fills, ts=DAY)
    assert r.ok is True, f"去重后不应有差异：{[str(d) for d in r.diffs]}"
    assert r.duplicate_fill_ids == (ids[0],)
    alerts = check_reconcile(r, ts=DAY)
    assert [a.severity for a in alerts] == [Severity.WARN]


# --------------------------------------------------------------------------- 5. 行情陈旧


def test_05_stale_quote():
    """行情陈旧：闸门拒单，原因码 ``STALE_QUOTE``。

    ⚠️ 注意这里**不是**"券商报价字段为 stale 就跳过价格偏离检查"——
    跳过检查等于把它当成通过（缺陷预登记 P4 那一类错误）。
    """
    b = _broker(script=FaultScript(stale_quotes=frozenset({CODE})))
    q = b.get_quote(CODE)
    assert q.stale is True

    d = _gate().evaluate(_ctx(quote=q))
    assert d.allowed is False
    assert d.code is RejectCode.STALE_QUOTE
    assert d.failure.check == "quote"


# --------------------------------------------------------------------------- 6. 停牌


def test_06_suspended():
    """停牌：券商拒单 ``SUSPENDED``；持仓已在本地记录但不可卖。"""
    # ⚠️ available 必须给足：MockBroker 先查持仓/资金，再报市场状态。
    #    给 available=0 会先被 INSUFFICIENT_POSITION 拦住，测到的是另一条规则。
    #    真实场景里停牌股的可卖数量通常仍显示为全部持仓，卖不出是券商侧拒单。
    b = _broker(positions=[Position(CODE, quantity=1000, available=1000, avg_cost=10.0)],
                script=FaultScript(reject={CODE: RejectCode.SUSPENDED}))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(OrderRequest(client_order_id="s-1", code=CODE, side=Side.SELL,
                                   shares=1000, limit_price=10.0))
    assert ei.value.code is RejectCode.SUSPENDED
    # 停牌期间持仓还在，只是卖不出
    assert {p.code: p for p in b.get_positions()}[CODE].quantity == 1000


# --------------------------------------------------------------------------- 7/8. 涨跌停


def test_07_limit_up_blocks_buy():
    """涨停买不进：拒单码 ``LIMIT_UP``，且**没有**任何持仓变化。"""
    b = _broker(script=FaultScript(reject={CODE: RejectCode.LIMIT_UP}))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(OrderRequest(client_order_id="lu-1", code=CODE, side=Side.BUY,
                                   shares=200, limit_price=11.0))
    assert ei.value.code is RejectCode.LIMIT_UP
    assert b.get_positions() == ()


def test_08_limit_down_blocks_sell():
    """跌停卖不出：拒单码 ``LIMIT_DOWN``，持仓**不减**（不是"卖掉了但没记"）。"""
    b = _broker(positions=[Position(CODE, quantity=1000, available=1000, avg_cost=10.0)],
                script=FaultScript(reject={CODE: RejectCode.LIMIT_DOWN}))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(OrderRequest(client_order_id="ld-1", code=CODE, side=Side.SELL,
                                   shares=1000, limit_price=9.0))
    assert ei.value.code is RejectCode.LIMIT_DOWN
    assert {p.code: p for p in b.get_positions()}[CODE].quantity == 1000


def test_07b_limit_rejections_are_distinguishable_from_each_other():
    """⭐ 涨停与跌停的原因码必须能区分。

    合并成 ``ORDER_REJECTED`` 的话，事后无法判断"买不进"还是"卖不出"，
    也就无法决定要不要重试——这两种的处理完全不同。
    """
    up = _broker(script=FaultScript(reject={CODE: RejectCode.LIMIT_UP}))
    # 卖单必须有持仓，否则会先被 INSUFFICIENT_POSITION 拦住
    dn = _broker(positions=[Position(CODE, quantity=1000, available=1000, avg_cost=10.0)],
                 script=FaultScript(reject={CODE: RejectCode.LIMIT_DOWN}))
    codes = []
    for b, side in ((up, Side.BUY), (dn, Side.SELL)):
        try:
            b.place_order(OrderRequest(client_order_id="x", code=CODE, side=side,
                                       shares=100, limit_price=10.0))
        except OrderRejected as e:
            codes.append(e.code)
    assert codes == [RejectCode.LIMIT_UP, RejectCode.LIMIT_DOWN]


# --------------------------------------------------------------------------- 9. 日内重启


def test_09_intraday_restart(tmp_path):
    """日内重启：以券商为准重建、**峰值不丢**、未终态委托继续跟踪、台账续链。

    后果断言四件事——每件单独都可能写错：
    1. 持仓来自券商（本地缓存 0 ⇒ 券商 400）
    2. ``peak_equity`` 保持不变（P5）
    3. 已成交的委托在重建后仍是 FILLED
    4. 台账能从文件接上链（seq 与 prev_sha256 都续上）
    """
    b = _broker(script=FaultScript(fill_ratio={CODE: 0.4}))
    b.place_order(OrderRequest(client_order_id="i-1", code=CODE, side=Side.BUY,
                               shares=1000, limit_price=10.0))

    # 重启前：本地状态落盘
    state = GateState(peak_equity=CAPITAL * 1.2, trading_day=DAY,
                      daily_order_count=3, last_reconciliation_ok=True)
    p = tmp_path / "gate.json"
    state.save(p)

    led = Ledger(tmp_path / "ledger.jsonl")
    led.record_decision(DecisionSnapshot(decision_date=DAY, mode="PAPER",
                                         data_sha256=H1, config_sha256=H1,
                                         targets=(CODE,)), ts=DAY)
    last_seq = led.entries()[-1]["seq"]

    # ---- 重启 ----
    state2 = GateState.load(p)
    assert state2.peak_equity == pytest.approx(CAPITAL * 1.2), "峰值没从盘上读回来"

    rebuilt = rebuild_from_broker(b, ts=DAY)
    apply_rebuild_to_gate_state(rebuilt, state2)

    assert rebuilt.position_map()[CODE] == 400, "重建没以券商为准"
    assert state2.peak_equity == pytest.approx(CAPITAL * 1.2), "重建把峰值改小了（P5）"
    assert state2.daily_order_count == 3, "重建把当日笔数清零了"
    assert rebuilt.orders[0].status is BrokerOrderStatus.PARTIALLY_FILLED
    assert rebuilt.blocked is False, "已成交的委托不该被当成未知"

    # 台账续链
    led2 = Ledger(tmp_path / "ledger.jsonl")
    e = led2.append("order", {"note": "restart"}, ts=DAY)
    assert e.seq == last_seq + 1
    led2.verify_chain()


def test_09b_restart_does_not_reset_daily_order_count_within_same_day():
    """同日重启不得把当日委托笔数清零——否则重启就能绕过笔数上限。"""
    state = GateState(peak_equity=CAPITAL, trading_day=DAY, daily_order_count=11,
                      last_reconciliation_ok=True)
    gate = _gate(state=state)
    gate.record_order(_ctx())          # 第 12 笔，恰好到上限
    assert state.daily_order_count == 12
    d = gate.evaluate(_ctx(shares=100))
    assert d.allowed is False
    assert d.code is RejectCode.DAILY_ORDER_LIMIT


# --------------------------------------------------------------------------- 10. 时钟漂移


def test_10_clock_drift():
    """时钟漂移：交易日倒退必须抛，**不得**被当成跨日重置笔数。

    ⚠️⚠️ 这条抓的是一个真实的实现缺陷。本轮之前 ``roll_day`` 写的是
    ``if day != self.trading_day``：日期倒退也满足"不等"，于是当日委托笔数
    被清零、``has_unknown_orders`` 被清掉——11 条硬限里第 3 条（单日笔数上限）
    和 UNKNOWN 阻断同时失效，而且**看起来一切正常**。
    """
    state = GateState(peak_equity=CAPITAL, trading_day=DAY, daily_order_count=9,
                      last_reconciliation_ok=True, has_unknown_orders=True)
    with pytest.raises(ClockWentBackwards):
        state.roll_day(20261029)
    assert state.daily_order_count == 9, "倒退时改了笔数"
    assert state.trading_day == DAY, "倒退时改了交易日"
    assert state.has_unknown_orders is True, "倒退时清掉了 UNKNOWN 阻断"


def test_10b_rolling_to_next_day_keeps_unknown_orders():
    """⭐ 即使**正常**跨日，也不能清 ``has_unknown_orders``。

    UNKNOWN 是券商侧的事实，不因日历翻页而消失。早期实现跨日就清它，
    效果是"昨天丢了回报，今天系统自动痊愈继续下单"。
    """
    state = GateState(peak_equity=CAPITAL, trading_day=DAY, daily_order_count=5,
                      last_reconciliation_ok=True, has_unknown_orders=True)
    state.roll_day(NEXT)
    assert state.trading_day == NEXT
    assert state.daily_order_count == 0, "跨日应重置当日笔数"
    assert state.has_unknown_orders is True, "跨日把 UNKNOWN 清掉了"
    assert state.peak_equity == CAPITAL


def test_10c_same_day_roll_is_noop():
    state = GateState(peak_equity=CAPITAL, trading_day=DAY, daily_order_count=4)
    state.roll_day(DAY)
    assert state.daily_order_count == 4


# --------------------------------------------------------------------------- 11. 配置被篡改


def _write_cfg(p: pathlib.Path, *, notional: float = 4000.0) -> pathlib.Path:
    p.write_text(json.dumps({"max_order_notional": notional,
                             "max_daily_orders": 12}, ensure_ascii=False),
                 encoding="utf-8", newline="\n")
    return p


def test_11_config_tampered(tmp_path):
    """配置被篡改：**运行时**校验拦住，不是"加载时恰好是对的"。

    步骤刻意设计成"冻结 → 改文件 → 用"，把两个时刻分开：
    只在加载时校验的实现在这里会放行。
    """
    p = _write_cfg(tmp_path / "cfg.json")
    frozen = FrozenConfig.freeze(p)
    assert frozen.get("max_order_notional") == 4000.0

    _write_cfg(p, notional=400_000.0)        # 会话中途被改（放大 100 倍）

    with pytest.raises(ConfigTampered, match="冻结后被改动"):
        frozen.get("max_order_notional")


def test_11b_config_tamper_is_detected_even_without_change_in_size(tmp_path):
    """改一个字符也要抓到——哈希不是长度校验。"""
    p = _write_cfg(tmp_path / "cfg.json", notional=4000.0)
    frozen = FrozenConfig.freeze(p)
    raw = p.read_text(encoding="utf-8").replace("4000.0", "9000.0")
    p.write_text(raw, encoding="utf-8", newline="\n")
    assert len(p.read_bytes()) == len(raw.encode("utf-8"))
    with pytest.raises(ConfigTampered):
        frozen.verify()


def test_11c_deleted_config_is_a_tamper_not_a_crash(tmp_path):
    """文件被删：必须报 ``ConfigTampered``，不能漏成 ``FileNotFoundError``。

    漏成 OSError 的话，上层写 `except OSError: use_default()` 就把
    "配置消失"变成了"用默认值继续跑"。
    """
    p = _write_cfg(tmp_path / "cfg.json")
    frozen = FrozenConfig.freeze(p)
    p.unlink()
    with pytest.raises(ConfigTampered, match="不见了"):
        frozen.verify()


def test_11d_config_guard_catches_tamper_between_freeze_and_use(tmp_path):
    """⭐⭐ 2.8-c 的正身：冻结与使用是**两个时刻**，中间被改必须能发现。"""
    p = _write_cfg(tmp_path / "cfg.json", notional=4000.0)
    frozen = FrozenConfig.freeze(p)

    frozen.verify()                       # 这一时刻是对的
    _write_cfg(p, notional=4000.0)        # 内容一样，仅重新格式化
    raw = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps(raw, indent=2), encoding="utf-8", newline="\n")

    with pytest.raises(ConfigTampered):
        frozen.verify()


def test_11e_untampered_config_records_every_check(tmp_path):
    p = _write_cfg(tmp_path / "cfg.json")
    frozen = FrozenConfig.freeze(p)
    frozen.get("max_order_notional")
    frozen.get("max_daily_orders")
    assert [t for t in frozen.checks] == ["get:max_order_notional", "get:max_daily_orders"]


# --------------------------------------------------------------------------- 跨项：全链路跑一遍


def test_full_stack_runs_with_all_faults_and_ledger_verifies(tmp_path):
    """把多项故障同时打开跑一遍完整链路，最后校验台账链。

    这不是替代上面 11 条，而是补一个"同时坏三处还活着"的冒烟——
    单点故障都处理好、组合起来却炸的情况很常见。
    """
    b = _broker(
        positions=[Position("600001", quantity=1000, available=1000, avg_cost=10.0)],
        quotes={"600001": Quote(code="600001", last=10.0, ts=DAY),
                "600002": Quote(code="600002", last=20.0, ts=DAY)},
        script=FaultScript(reject={"600002": RejectCode.LIMIT_UP},
                           duplicate_fills=frozenset({"600001"})),
    )
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record_decision(DecisionSnapshot(decision_date=DAY, mode="PAPER",
                                         data_sha256=H1, config_sha256=H1,
                                         targets=("600001", "600002")), ts=DAY)

    snap = __import__("deepquant.broker.models", fromlist=["AccountSnapshot"]) \
        .AccountSnapshot(cash=b.get_cash(), positions=b.get_positions())
    plan = plan_orders(PlanInput(
        decision=str(DAY), targets=("600001", "600002"), snapshot=snap,
        quotes={"600001": Quote(code="600001", last=10.0, ts=DAY),
                "600002": Quote(code="600002", last=20.0, ts=DAY)},
        target_weight=0.2))

    gate = _gate()
    for o in plan.orders:
        d = gate.evaluate(_ctx(price=o.limit_price, shares=o.shares, code=o.code))
        if not d.allowed:
            continue
        gate.record_order(_ctx(price=o.limit_price, shares=o.shares, code=o.code))
        try:
            bo = b.place_order(OrderRequest(client_order_id=o.client_order_id,
                                            code=o.code, side=o.side,
                                            shares=o.shares,
                                            limit_price=o.limit_price))
        except OrderRejected as e:
            led.append("order", {"reject": e.code.value, "code": o.code}, ts=DAY)
            continue
        led.record_order(bo, ts=DAY)

    for f in b.get_fills():
        led.record_fill(f, reference_price=10.0, ts=DAY)

    # 本地视图直接取自券商返回，故这是一次"同源冒烟"：它验证链路与台账链，
    # 不当对账能力的证据（双向对账的证据在 test_reconciler 与 04a/04b）。
    r = reconcile_with_broker(b, local_positions=b.get_positions(),
                              local_cash=b.get_cash(),
                              local_orders=b.get_orders(),
                              local_fills=b.get_fills(), ts=DAY)
    led.record_reconcile(r, ts=DAY)

    sink = MemorySink()
    run_all_checks(ts=DAY, heartbeat=HeartbeatState(DAY, 0),
                   orders=b.get_orders(), reconcile_result=r,
                   drawdown=dict(equity=CAPITAL, peak_equity=CAPITAL,
                                 breaker_dd=0.025), sink=sink)

    led.verify_chain()
    assert r.ok is True, f"组合故障下对账不平：{[str(d) for d in r.diffs]}"
    # 重复回报仍应被报出（虽然账是平的）
    assert any(a.kind is AlertKind.ORDER_ANOMALY for a in sink.alerts)
