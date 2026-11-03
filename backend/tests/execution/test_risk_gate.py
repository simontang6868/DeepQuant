"""Phase 2.3：``RiskGate`` 硬限的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.3-a ~ 2.3-c。
变异测试单独放在 `test_risk_gate_mutation.py`（要求 2.3-d）。

测试组织方式：**先构造一个"全绿"的上下文**，然后每次只破坏一个字段。
这样每条用例只验证一件事，失败时的归因是唯一的。

⭐ 两条预登记缺陷的正身
----------------------
* **P4**（缺失输入被静默跳过）：所有可为 ``None`` 的输入都必须导致**拒单**。
  `test_every_missing_input_is_rejected` 用参数化把每一个 ``None`` 都跑一遍。
* **P5**（峰值回撤重启后丢失）：`test_peak_survives_restart` 与
  `test_breaker_still_trips_after_restart` 联合钉住——只存内存的实现在这里必红。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from deepquant.broker.errors import RejectCode
from deepquant.broker.models import OrderRequest, Quote
from deepquant.execution.risk_gate import (
    Authorization,
    GateState,
    NoAuthorization,
    OrderContext,
    RiskGate,
    RiskLimits,
)
from deepquant.market.rules import Side

DAY = 20261030


# ---------------------------------------------------------------------------
# fixtures：一个"全绿"的基线
# ---------------------------------------------------------------------------
@pytest.fixture()
def limits() -> RiskLimits:
    # 策略仓 2 万、5 只 ⇒ 单只 4000；D2 熔断 2.5%
    return RiskLimits.for_strategy_capital(
        stock_capital=20_000.0, n_hold=5,
        max_price_deviation=0.01, max_quote_age_days=5,
        max_drawdown=0.025, max_daily_orders=12,
    )


@pytest.fixture()
def auth() -> Authorization:
    return Authorization(max_notional=20_000.0,
                         valid_until=dt.date(2027, 12, 31),
                         boards=frozenset({"SH_MAIN", "SZ_MAIN"}))


@pytest.fixture()
def state() -> GateState:
    return GateState(peak_equity=20_000.0, trading_day=DAY, daily_order_count=0,
                     last_reconciliation_ok=True)


@pytest.fixture()
def gate(limits, state, auth) -> RiskGate:
    return RiskGate(limits=limits, state=state, authorization=auth)


def _order(shares: int = 100, price: float = 10.0, code: str = "600990") -> OrderRequest:
    return OrderRequest(client_order_id=f"{DAY}-{code}-B-{shares}",
                        code=code, side=Side.BUY, shares=shares, limit_price=price)


def _ctx(**kw) -> OrderContext:
    """全绿上下文。任何一项破坏都只会触发对应的那一条检查。"""
    base = dict(
        order=_order(),
        quote=Quote(code="600990", last=10.0, ts=DAY, stale=False),
        equity=20_000.0,
        position_value=3_000.0,          # < 4000 单票上限
        board="SH_MAIN",
        is_trading_hours=True,
        trading_day=DAY,
        halt_file_present=False,
    )
    base.update(kw)
    return OrderContext(**base)


# ---------------------------------------------------------------------------
# 基线：全绿必须真的全绿
# ---------------------------------------------------------------------------
def test_baseline_passes_every_check(gate):
    d = gate.evaluate(_ctx())
    assert d.allowed is True
    assert d.failure is None
    assert len(d.checked) == 12, f"应有 12 条检查，实际 {d.checked}"


def test_check_order_is_fixed_and_documented(gate):
    """顺序本身就是规则：先问该不该停机，再问这笔单合不合规。"""
    names = [n for n, _ in gate.checks]
    assert names == [
        "halt_switch", "circuit_breaker", "authorization", "reconciliation",
        "unknown_orders", "trading_hours", "blacklist", "quote",
        "price_deviation", "position_limit", "order_notional", "daily_count",
    ]


# ---------------------------------------------------------------------------
# 2.3-a 逐条硬限：通过边界 + 拒绝边界
# ---------------------------------------------------------------------------
# ---- 1 停机开关 ----
def test_halt_state_rejects(gate):
    gate.state.halted = True
    gate.state.halt_reason = "人工停机"
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.HALT_SWITCH
    assert "人工停机" in d.failure.detail


def test_halt_file_rejects(gate):
    d = gate.evaluate(_ctx(halt_file_present=True))
    assert d.code is RejectCode.HALT_SWITCH


def test_halt_file_false_passes(gate):
    """边界：文件不存在时必须放行，不能"保守地"一律拒。"""
    assert gate.evaluate(_ctx(halt_file_present=False)).allowed is True


# ---- 2 熔断 ----
def test_breaker_rejects(gate):
    gate.state.breaker_tripped = True
    gate.state.breaker_reason = "回撤超阈值"
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.CIRCUIT_BREAKER


def test_breaker_not_tripped_passes(gate):
    assert gate.evaluate(_ctx()).allowed is True


# ---- 3 授权 ----
def test_missing_authorization_rejects(limits, state):
    g = RiskGate(limits=limits, state=state, authorization=None)
    d = g.evaluate(_ctx())
    assert d.code is RejectCode.NO_AUTHORIZATION


def test_expired_authorization_rejects(gate):
    gate.auth = Authorization(max_notional=20_000.0,
                              valid_until=dt.date(2026, 1, 1),
                              boards=frozenset({"SH_MAIN"}))
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.NOT_AUTHORIZED
    assert "到期" in d.failure.detail


def test_authorization_last_valid_day_passes(gate):
    """边界：授权到期日**当天**仍可用（valid_until 是闭区间）。"""
    gate.auth = Authorization(max_notional=20_000.0,
                              valid_until=dt.date(2026, 10, 30),
                              boards=frozenset({"SH_MAIN"}))
    assert gate.evaluate(_ctx()).allowed is True


def test_board_not_authorized_rejects(gate):
    d = gate.evaluate(_ctx(board="STAR"))
    assert d.code is RejectCode.NOT_AUTHORIZED
    assert "STAR" in d.failure.detail


def test_notional_over_authorization_rejects(gate):
    """授权上限 2 万，本笔 3000 股 × 10 元 = 3 万 ⇒ 超。"""
    d = gate.evaluate(_ctx(order=_order(shares=3000)))
    assert d.code is RejectCode.NOT_AUTHORIZED


def test_notional_exactly_at_authorization_passes(limits, state):
    """边界：恰好等于上限应放行（`>` 而非 `>=`）。

    ⚠️ 必须把**授权上限与单笔上限设成同一个值**，否则会先被单笔检查拒掉，
    这条测试就退化成「验证单笔上限」了——两条规则的边界要分开验。
    """
    lim = RiskLimits(max_single_position_ratio=0.2, max_order_notional=5_000.0,
                     max_daily_orders=12, max_price_deviation=0.01,
                     max_quote_age_days=5, max_drawdown=0.025)
    a = Authorization(max_notional=5_000.0, valid_until=dt.date(2027, 12, 31),
                      boards=frozenset({"SH_MAIN"}))
    g = RiskGate(limits=lim, state=state, authorization=a)
    assert g.evaluate(_ctx(order=_order(shares=500), position_value=0.0)).allowed is True
    # 多一手就越界
    assert g.evaluate(_ctx(order=_order(shares=600), position_value=0.0)).code \
        is RejectCode.NOT_AUTHORIZED


# ---- 4 对账 ----
def test_never_reconciled_rejects(gate):
    """``None`` = 从未对账 ⇒ 视同不一致。保守方向。"""
    gate.state.last_reconciliation_ok = None
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.RECONCILIATION_MISMATCH
    assert "从未对账" in d.failure.detail


def test_failed_reconciliation_rejects(gate):
    gate.state.last_reconciliation_ok = False
    assert gate.evaluate(_ctx()).code is RejectCode.RECONCILIATION_MISMATCH


def test_passed_reconciliation_passes(gate):
    gate.state.last_reconciliation_ok = True
    assert gate.evaluate(_ctx()).allowed is True


# ---- 5 UNKNOWN 委托 ----
def test_unknown_orders_block_new_orders(gate):
    gate.state.has_unknown_orders = True
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.UNKNOWN_ORDER_PENDING


# ---- 6 交易时段 ----
def test_outside_trading_hours_rejects(gate):
    assert gate.evaluate(_ctx(is_trading_hours=False)).code is RejectCode.NOT_TRADING_HOURS


def test_inside_trading_hours_passes(gate):
    assert gate.evaluate(_ctx(is_trading_hours=True)).allowed is True


# ---- 7 黑名单 ----
def test_blacklist_rejects(gate):
    gate.limits = RiskLimits(
        max_single_position_ratio=0.2, max_order_notional=4000.0,
        max_daily_orders=12, max_price_deviation=0.01, max_quote_age_days=5,
        max_drawdown=0.025, blacklist=frozenset({"600990"}))
    assert gate.evaluate(_ctx()).code is RejectCode.BLACKLIST


def test_not_in_blacklist_passes(gate):
    assert gate.evaluate(_ctx()).allowed is True


# ---- 8 行情 ----
def test_missing_quote_rejects(gate):
    """⭐ P4 正身：拿不到行情必须拒单，不能"跳过价格类检查"。"""
    d = gate.evaluate(_ctx(quote=None))
    assert d.allowed is False
    assert d.code is RejectCode.STALE_QUOTE


def test_stale_quote_rejects(gate):
    d = gate.evaluate(_ctx(quote=Quote(code="600990", last=10.0, ts=DAY, stale=True)))
    assert d.code is RejectCode.STALE_QUOTE


def test_zero_price_quote_rejects(gate):
    d = gate.evaluate(_ctx(quote=Quote(code="600990", last=0.0, ts=DAY)))
    assert d.code is RejectCode.STALE_QUOTE


def test_old_quote_rejects(gate):
    d = gate.evaluate(_ctx(quote=Quote(code="600990", last=10.0, ts=20261020)))
    assert d.code is RejectCode.STALE_QUOTE
    assert "过期" in d.failure.detail


def test_quote_at_age_limit_passes(gate):
    """边界：恰好 5 天应放行（上限是 5）。"""
    d = gate.evaluate(_ctx(quote=Quote(code="600990", last=10.0, ts=20261025)))
    assert d.allowed is True


# ---- 9 价格偏离 ----
def test_price_deviation_rejects(gate):
    d = gate.evaluate(_ctx(order=_order(price=10.2)))    # 偏离 2%
    assert d.code is RejectCode.PRICE_DEVIATION


def test_price_deviation_just_below_limit_passes(gate):
    """边界：10.0 → 10.1 应放行。

    ⚠️ 这**不是**"恰好 1%"。float64 下 abs(10.1-10.0)/10.0 = 0.009999999999999964，
    比 1% 略小。任何 x*1.01 都得不到精确的 1%，所以「恰好等于阈值」这个用例
    在浮点下不存在——这里测的是"最接近阈值的可表示价格"。
    """
    import math
    assert abs(10.1 - 10.0) / 10.0 < 0.01
    assert math.isclose(abs(10.1 - 10.0) / 10.0, 0.01, rel_tol=1e-12)
    d = gate.evaluate(_ctx(order=_order(price=10.1)))
    assert d.allowed is True


def test_price_deviation_just_above_limit_rejects(gate):
    """边界另一侧：10.11（1.1%）必须拒。"""
    d = gate.evaluate(_ctx(order=_order(price=10.11)))
    assert d.code is RejectCode.PRICE_DEVIATION


def test_price_deviation_downward_also_checked(gate):
    """偏离要取绝对值——卖出让价同样是偏离。"""
    d = gate.evaluate(_ctx(order=_order(price=9.8)))
    assert d.code is RejectCode.PRICE_DEVIATION


# ---- 10 单票上限 ----
def test_position_over_cap_rejects(gate):
    d = gate.evaluate(_ctx(position_value=4_001.0))      # 上限 20000×0.2 = 4000
    assert d.code is RejectCode.POSITION_LIMIT


def test_position_at_cap_passes(gate):
    assert gate.evaluate(_ctx(position_value=4_000.0)).allowed is True


def test_missing_equity_rejects(gate):
    d = gate.evaluate(_ctx(equity=None))
    assert d.code is RejectCode.MISSING_INPUT
    assert d.failure.check == "position_limit"


def test_missing_position_value_rejects(gate):
    d = gate.evaluate(_ctx(position_value=None))
    assert d.code is RejectCode.MISSING_INPUT


# ---- 11 单笔金额 ----
def test_order_notional_rejects(gate):
    d = gate.evaluate(_ctx(order=_order(shares=500)))    # 5000 > 4000
    assert d.code is RejectCode.ORDER_NOTIONAL_LIMIT


def test_order_notional_at_limit_passes(gate):
    assert gate.evaluate(_ctx(order=_order(shares=400))).allowed is True


# ---- 12 单日笔数 ----
def test_daily_count_limit_rejects(gate):
    gate.state.daily_order_count = 12
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.DAILY_ORDER_LIMIT


def test_daily_count_one_below_limit_passes(gate):
    gate.state.daily_order_count = 11
    assert gate.evaluate(_ctx()).allowed is True


def test_new_day_resets_counter(gate):
    """跨日后当日笔数归零，不该被昨天的笔数卡住。"""
    gate.state.daily_order_count = 12
    gate.state.trading_day = 20261029
    assert gate.evaluate(_ctx()).allowed is True


def test_missing_trading_day_rejects(gate):
    d = gate.evaluate(_ctx(trading_day=None))
    assert d.code is RejectCode.MISSING_INPUT


# ---------------------------------------------------------------------------
# 2.3-b 停在第一个失败 + 原因码可检索
# ---------------------------------------------------------------------------
def test_stops_at_first_failure(gate):
    """同时违反多条时，返回**顺序最前**的那条，且 checked 不含失败项。"""
    gate.state.halted = True
    gate.state.breaker_tripped = True
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.HALT_SWITCH
    assert d.checked == (), "第一条就失败，不应有任何已通过项"


def test_checked_records_passed_checks_before_failure(gate):
    gate.state.daily_order_count = 12      # 最后一条失败
    d = gate.evaluate(_ctx())
    assert d.code is RejectCode.DAILY_ORDER_LIMIT
    assert len(d.checked) == 11
    assert "order_notional" in d.checked and "daily_count" not in d.checked


def test_every_rejection_has_a_machine_readable_code(gate):
    """拒单原因必须是 RejectCode 枚举，不能是自由文本。"""
    gate.state.halted = True
    d = gate.evaluate(_ctx())
    assert isinstance(d.code, RejectCode)
    assert isinstance(d.failure.check, str) and d.failure.check


# ---------------------------------------------------------------------------
# 2.3-c ⭐ 缺失输入一律拒单（预登记 P4）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("quote", None),
    ("equity", None),
    ("position_value", None),
    ("board", None),
    ("is_trading_hours", None),
    ("trading_day", None),
    ("halt_file_present", None),
])
def test_every_missing_input_is_rejected(gate, field, value):
    """把每一个可空字段逐个设为 None，都必须被拒。

    这条测试是本阶段的核心防线：本项目反复出现「拿不到 ⇒ 跳过 ⇒ 通过」。
    参数化的价值是——将来给 ``OrderContext`` 加一个可空字段却忘了处理，
    只要把它加进这个列表，就会立刻红。
    """
    d = gate.evaluate(_ctx(**{field: value}))
    assert d.allowed is False, f"{field}=None 竟然通过了闸门"
    assert isinstance(d.code, RejectCode)


# ---------------------------------------------------------------------------
# 峰值与熔断（预登记 P5）
# ---------------------------------------------------------------------------
def test_peak_only_rises(limits, state, auth):
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.observe_equity(21_000.0)
    assert g.state.peak_equity == 21_000.0
    g.observe_equity(20_500.0)
    assert g.state.peak_equity == 21_000.0, "峰值不能下降"


def test_breaker_trips_at_threshold(limits, state, auth):
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.observe_equity(20_000.0)
    g.observe_equity(19_600.0)          # 回撤 2.0% < 2.5%
    assert g.state.breaker_tripped is False
    g.observe_equity(19_400.0)          # 回撤 3.0% > 2.5%
    assert g.state.breaker_tripped is True
    assert "回撤" in g.state.breaker_reason


def test_breaker_at_exact_threshold_does_not_trip(limits, state, auth):
    """边界：恰好 2.5% 不触发（阈值判定是 `>`，不是 `>=`）。

    取"恰好"时浮点会有误差，所以用一个能精确表示的数：
    20000 × 0.975 = 19500。
    """
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.observe_equity(20_000.0)
    g.observe_equity(19_500.0)
    assert g.state.breaker_tripped is False


def test_peak_survives_restart(tmp_path, limits, auth):
    """⭐ P5 正身：峰值必须落盘，重启后仍在。"""
    sp = tmp_path / "gate_state.json"
    g1 = RiskGate(limits=limits, state=GateState(last_reconciliation_ok=True),
                  authorization=auth, state_path=sp)
    g1.observe_equity(25_000.0)
    assert sp.exists()
    assert json.loads(sp.read_text(encoding="utf-8"))["peak_equity"] == 25_000.0

    g2 = RiskGate(limits=limits, state=GateState.load(sp), authorization=auth, state_path=sp)
    assert g2.state.peak_equity == 25_000.0, "重启后峰值丢失 → 回撤会被重算成 0"


def test_breaker_still_trips_after_restart(tmp_path, limits, auth):
    """⭐ 只存内存的实现在这里必红：重启后从 25,000 跌到 24,000 仍然触发。"""
    sp = tmp_path / "gate_state.json"
    g1 = RiskGate(limits=limits, state=GateState(last_reconciliation_ok=True),
                  authorization=auth, state_path=sp)
    g1.observe_equity(25_000.0)

    g2 = RiskGate(limits=limits, state=GateState.load(sp), authorization=auth, state_path=sp)
    g2.observe_equity(24_000.0)        # 相对峰值回撤 4% > 2.5%
    assert g2.state.breaker_tripped is True


def test_tripped_breaker_persists(tmp_path, limits, auth):
    sp = tmp_path / "gate_state.json"
    g1 = RiskGate(limits=limits, state=GateState(last_reconciliation_ok=True),
                  authorization=auth, state_path=sp)
    g1.observe_equity(20_000.0)
    g1.observe_equity(19_000.0)
    assert g1.state.breaker_tripped is True

    g2 = RiskGate(limits=limits, state=GateState.load(sp), authorization=auth, state_path=sp)
    assert g2.state.breaker_tripped is True, "熔断状态必须跨重启保留，否则重启即复活"


def test_state_roundtrip_is_lossless(tmp_path, limits):
    st = GateState(peak_equity=1.0, trading_day=DAY, daily_order_count=3,
                   halted=True, halt_reason="r", breaker_tripped=True, breaker_reason="b",
                   last_reconciliation_ok=False, has_unknown_orders=True)
    p = tmp_path / "s.json"
    st.save(p)
    assert GateState.load(p) == st


def test_missing_state_file_yields_blank_state(tmp_path):
    assert GateState.load(tmp_path / "nope.json") == GateState()


# ---------------------------------------------------------------------------
# 授权文件：只读、缺字段即拒
# ---------------------------------------------------------------------------
def _write_auth(tmp_path, **over) -> "object":
    d = {"max_notional": 20_000.0, "valid_until": "2027-12-31",
         "boards": ["SH_MAIN", "SZ_MAIN"]}
    d.update(over)
    p = tmp_path / "live_authorization.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def test_authorization_load_ok(tmp_path):
    a = Authorization.load(_write_auth(tmp_path))
    assert a.max_notional == 20_000.0
    assert a.boards == frozenset({"SH_MAIN", "SZ_MAIN"})


def test_authorization_missing_file(tmp_path):
    with pytest.raises(NoAuthorization, match="不存在"):
        Authorization.load(tmp_path / "absent.json")


def test_authorization_missing_field(tmp_path):
    """缺字段必须报错，不能补默认值——「补一个看起来合理的默认值」正是
    本项目在成本口径上错过四次的同一个动作。"""
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"max_notional": 1.0}), encoding="utf-8")
    with pytest.raises(NoAuthorization, match="缺字段"):
        Authorization.load(p)


def test_authorization_empty_boards_rejected(tmp_path):
    with pytest.raises(NoAuthorization, match="boards"):
        Authorization.load(_write_auth(tmp_path, boards=[]))


def test_authorization_has_no_write_method():
    """agent 不得创建/修改/延期授权文件 —— 不给写方法比"约定不调用"更难绕过。"""
    public = {n for n in dir(Authorization) if not n.startswith("_")}
    for forbidden in ("save", "write", "create", "extend", "update"):
        assert forbidden not in public, f"Authorization 不应暴露 {forbidden}"


# ---------------------------------------------------------------------------
# 记账与人工控制
# ---------------------------------------------------------------------------
def test_record_order_only_counts_allowed(limits, state, auth):
    g = RiskGate(limits=limits, state=state, authorization=auth)
    assert g.state.daily_order_count == 0
    g.record_order(_ctx())
    assert g.state.daily_order_count == 1


def test_halt_and_resume(limits, state, auth):
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.halt("人工")
    assert g.state.halted is True
    g.resume()
    assert g.state.halted is False


def test_resume_does_not_reset_breaker(limits, state, auth):
    """解除停机 ≠ 解除熔断。熔断必须人工复核后单独复位。"""
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.state.breaker_tripped = True
    g.halt("x")
    g.resume()
    assert g.state.breaker_tripped is True


def test_reset_breaker(limits, state, auth):
    g = RiskGate(limits=limits, state=state, authorization=auth)
    g.state.breaker_tripped = True
    g.reset_breaker()
    assert g.state.breaker_tripped is False
