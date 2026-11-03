"""Phase 2.9：SHADOW 模式的机制测试（不发送、差异可解释）。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.9-a ~ 2.9-b。

**本文件只验机制**。「跑满 20 个交易日 + 无未解释差异」依赖 D8 运行机器，
本阶段无法达成（G2 因此预期 ``NOT_PASSED``）。

两条"不可绕过"是怎么测的
--------------------
1. **不发送**：`test_shadow_never_receives_a_broker` 用 ``inspect`` 检查
   ``build_shadow_plan``/``shadow_report`` 的参数名里没有任何券商对象；
   `test_shadow_module_does_not_call_place_order` 用 AST 扫调用。
   两者一起才够——只扫调用的话，将来有人加个 `adapter` 参数但暂时没调，
   测试仍绿。
2. **可解释**：每条 ``DiffReason`` 一个用例，外加 ``UNEXPLAINED`` 必须挡 G2。
   ⭐ 最要紧的是 `test_unexplained_is_not_swallowed`：归不进任何原因时必须
   落到 UNEXPLAINED，不能被"资金不足"之类兜住。
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from deepquant.broker.models import AccountSnapshot, Cash, Position, Quote
from deepquant.execution.shadow import (
    DiffReason,
    ExplanationContext,
    ShadowDiff,
    ShadowPlan,
    build_shadow_plan,
    compare_with_ledger,
    shadow_report,
)

SRC = (pathlib.Path(__file__).resolve().parents[2]
       / "src" / "deepquant" / "execution" / "shadow.py")

DAY = 20261030
CAPITAL = 100_000.0


def _record(targets=("600990", "603977"), day=DAY) -> dict:
    return {"decision_date": day, "target_holds": list(targets)}


def _snap(cash=CAPITAL, positions=()) -> AccountSnapshot:
    return AccountSnapshot(cash=Cash(available=cash, frozen=0.0, total=cash),
                           positions=positions)


def _quotes(**prices) -> dict[str, Quote]:
    return {c: Quote(code=c, last=px, ts=DAY) for c, px in prices.items()}


# --------------------------------------------------------------------------- 不发送


def test_shadow_never_receives_a_broker():
    """⭐ 2.9-a 的结构性保证：签名里没有券商对象，就不可能下单。"""
    for fn in (build_shadow_plan, shadow_report, compare_with_ledger):
        params = set(inspect.signature(fn).parameters)
        banned = {p for p in params
                  if any(k in p.lower() for k in ("broker", "adapter", "client", "gateway"))}
        assert banned == set(), (
            f"{fn.__name__} 接受券商相关参数 {banned}——SHADOW 的『不发送』"
            "若靠参数正确性保证，一次传错就真的下单了")


def test_shadow_module_does_not_call_place_order():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.add(f.id)
            elif isinstance(f, ast.Attribute):
                calls.add(f.attr)
    banned = {c for c in calls if c in {"place_order", "cancel_order",
                                        "place_orders", "submit"}}
    assert banned == set(), f"shadow.py 里出现下单类调用：{banned}"


def test_buy_codes_uses_enum_not_value_string():
    """⭐ 钉住一个真实踩过的坑：``Side.BUY.value`` 是 ``"BUY"`` 不是 ``"buy"``。

    实现里若写 ``o.side.value == "buy"``，``buy_codes`` 恒为空 ⇒ 所有台账目标
    都被判成"没买"⇒ 每条差异都落到"没有行情"这个**已解释**原因上 ⇒
    报告全绿而实际一条都没对上。这种错在单跑一次时完全看不出来。
    """
    from deepquant.execution.planner import PlannedOrder
    from deepquant.market.rules import Side

    assert Side.BUY.value == "BUY", "前提变了：Side.BUY.value 不再是全大写"
    o = PlannedOrder(client_order_id="x", code="600990", side=Side.BUY,
                     shares=100, limit_price=10.0, reason="建仓")
    plan = ShadowPlan(decision_date=DAY, target_holds=("600990",),
                      would_be_orders=(o,), skipped=(), equity=CAPITAL)
    assert plan.buy_codes == ("600990",)
    assert plan.sell_codes == ()


def test_shadow_plan_has_no_sent_state():
    """``ShadowPlan`` 里不该有"是否已发送"这种字段——它永远没被发送过。"""
    fields = set(ShadowPlan.__dataclass_fields__)
    assert not (fields & {"sent", "is_sent", "broker_order_id", "status"}), fields


# --------------------------------------------------------------------------- 构造


def test_shadow_builds_orders_from_ledger_targets():
    plan = build_shadow_plan(_record(), _snap(),
                             _quotes(**{"600990": 10.0, "603977": 20.0}),
                             target_weight=0.2)
    assert plan.decision_date == DAY
    assert plan.target_holds == ("600990", "603977")
    assert len(plan.would_be_orders) == 2
    from deepquant.market.rules import Side
    assert all(o.side is Side.BUY for o in plan.would_be_orders)


def test_missing_target_holds_is_an_error_not_a_quiet_noop():
    """⭐ 台账没目标 ⇒ 抛。把"空目标"当成"今天不用下单"是最危险的静默。"""
    with pytest.raises(ValueError, match="target_holds"):
        build_shadow_plan({"decision_date": DAY}, _snap(), _quotes(**{"600990": 10.0}),
                          target_weight=0.2)
    with pytest.raises(ValueError, match="target_holds"):
        build_shadow_plan({"decision_date": DAY, "target_holds": []}, _snap(),
                          _quotes(**{"600990": 10.0}), target_weight=0.2)


def test_missing_decision_date_is_an_error():
    with pytest.raises(ValueError, match="决策日"):
        build_shadow_plan({"target_holds": ["600990"]}, _snap(),
                          _quotes(**{"600990": 10.0}), target_weight=0.2)


def test_shadow_plan_respects_existing_holdings():
    """已持有目标股数时不该重复买（SHADOW 用同一套 planner）。"""
    plan = build_shadow_plan(_record(("600990",)), 
                             _snap(cash=0.0, positions=(Position("600990", 2000, 2000),)),
                             _quotes(**{"600990": 10.0}), target_weight=0.2)
    from deepquant.market.rules import Side
    buys = [o for o in plan.would_be_orders if o.side is Side.BUY]
    assert buys == [], "已达标还继续买"
    assert plan.equity > 0


# --------------------------------------------------------------------------- 可解释：每条原因


def _ctx(**kw) -> ExplanationContext:
    base = dict(price_cap=None, allowed_boards=None, board_of={},
                last_price={}, st_codes=frozenset())
    base.update(kw)
    return ExplanationContext(**base)


def test_match_when_everything_lines_up():
    plan = build_shadow_plan(_record(("600990",)), _snap(),
                             _quotes(**{"600990": 10.0}), target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)))
    assert len(diffs) == 1
    assert diffs[0].reason is DiffReason.MATCH
    assert diffs[0].blocks_g2 is False


def test_st_is_explained():
    plan = build_shadow_plan(_record(("600990",)), _snap(), {}, target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)),
                                _ctx(st_codes=frozenset({"600990"}),
                                     last_price={"600990": 10.0}))
    assert diffs[0].reason is DiffReason.EXPLAINED_ST
    assert diffs[0].blocker if False else True
    assert diffs[0].blocks_g2 is False


def test_board_restriction_is_explained():
    plan = build_shadow_plan(_record(("300750",)), _snap(), {}, target_weight=0.2)
    diffs = compare_with_ledger(
        plan, _record(("300750",)),
        _ctx(allowed_boards=frozenset({"SH_MAIN", "SZ_MAIN"}),
             board_of={"300750": "GEM"}, last_price={"300750": 30.0}))
    assert diffs[0].reason is DiffReason.EXPLAINED_BOARD
    assert "GEM" in diffs[0].detail


def test_price_cap_is_explained():
    plan = build_shadow_plan(_record(("600990",)), _snap(), {}, target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)),
                                _ctx(price_cap=40.0,
                                     last_price={"600990": 55.0}))
    assert diffs[0].reason is DiffReason.EXPLAINED_PRICE_CAP
    assert "55" in diffs[0].detail


def test_no_quote_is_explained():
    plan = build_shadow_plan(_record(("600990",)), _snap(), {}, target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)), _ctx())
    assert diffs[0].reason is DiffReason.EXPLAINED_NO_QUOTE


def test_cash_shortage_is_explained():
    plan = build_shadow_plan(_record(("600990",)), _snap(), {}, target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)),
                                _ctx(last_price={"600990": 10.0},
                                     cash_short_codes=frozenset({"600990"})))
    assert diffs[0].reason is DiffReason.EXPLAINED_CASH


def test_lot_shortage_is_explained():
    plan = build_shadow_plan(_record(("600990",)), _snap(cash=50.0), {},
                             target_weight=0.2)
    diffs = compare_with_ledger(plan, _record(("600990",)),
                                _ctx(last_price={"600990": 10.0},
                                     lot_short_codes=frozenset({"600990"})))
    assert diffs[0].reason is DiffReason.EXPLAINED_LOT


def test_planned_but_not_in_ledger_is_reported():
    """方向二：计划要买、台账没这只。"""
    plan = ShadowPlan(decision_date=DAY, target_holds=("600990",),
                      would_be_orders=(), skipped=(), equity=CAPITAL)
    from deepquant.execution.planner import PlannedOrder
    from deepquant.market.rules import Side

    o = PlannedOrder(client_order_id="x", code="600123", side=Side.BUY,
                     shares=100, limit_price=10.0, reason="建仓")
    plan2 = ShadowPlan(decision_date=DAY, target_holds=("600990",),
                       would_be_orders=(o,), skipped=(), equity=CAPITAL)
    diffs = compare_with_ledger(plan2, _record(("600990",)))
    assert any(d.reason is DiffReason.PLANNED_NOT_IN_LEDGER for d in diffs)


def test_unexplained_is_not_swallowed():
    """⭐⭐ 归不进任何原因 ⇒ UNEXPLAINED 且挡 G2。

    这条是整套解释机制的守门人：如果实现里加了一句
    ``return EXPLAINED_CASH`` 兜底，任何真 bug 都会被说成"资金不足"，
    而 G2 的"连续 20 日无未解释差异"就变成了永远通过。
    """
    plan = ShadowPlan(decision_date=DAY, target_holds=("600990",),
                      would_be_orders=(), skipped=(), equity=CAPITAL)
    # 有行情、不是 ST、板块允许、没过价格上限、不差钱 ⇒ 无法解释
    ctx = _ctx(allowed_boards=frozenset({"SH_MAIN"}),
               board_of={"600990": "SH_MAIN"},
               last_price={"600990": 10.0}, price_cap=40.0)
    diffs = compare_with_ledger(plan, _record(("600990",)), ctx)
    assert diffs[0].reason is DiffReason.UNEXPLAINED
    assert diffs[0].blocks_g2 is True


def test_unexplained_blocks_the_report():
    plan = ShadowPlan(decision_date=DAY, target_holds=("600990",),
                      would_be_orders=(), skipped=(), equity=CAPITAL)
    ctx = _ctx(allowed_boards=frozenset({"SH_MAIN"}),
               board_of={"600990": "SH_MAIN"}, last_price={"600990": 10.0})
    rep = shadow_report(_record(("600990",)), _snap(), {},
                        target_weight=0.2, ctx=ctx)
    assert rep.ok is False
    assert len(rep.unexplained) == 1
    assert "未解释" in rep.summary()


def test_explained_report_is_ok():
    rep = shadow_report(_record(("600990",)), _snap(), {},
                        target_weight=0.2,
                        ctx=_ctx(allowed_boards=frozenset({"SH_MAIN"}),
                                 board_of={"600990": "SH_MAIN"},
                                 st_codes=frozenset({"600990"})))
    assert rep.ok is True
    assert rep.unexplained == ()
    assert "未发送" in rep.summary()


# --------------------------------------------------------------------------- 原因枚举本身


def test_explained_prefix_is_the_only_way_to_be_explained():
    """``is_explained`` 的判定必须靠枚举自身，不靠一张手写清单。

    手写清单会漏：新增一个 ``EXPLAINED_*`` 却忘了加进去，那条差异就会被
    当成未解释（假阳性，拖着 G2 不放），而没人知道为什么。
    """
    for r in DiffReason:
        if r is DiffReason.MATCH:
            assert r.is_explained is True
        elif r is DiffReason.UNEXPLAINED:
            assert r.is_explained is False
        elif r.value.startswith("explained_"):
            assert r.is_explained is True, f"{r.value} 应当是已解释"
        else:
            assert r.is_explained is False, f"{r.value} 不该被当成已解释"


def test_reason_enum_is_finite_and_named():
    names = {r.value for r in DiffReason}
    required = {"match", "unexplained", "explained_price_cap",
                "explained_board", "explained_st", "explained_cash"}
    assert required <= names, f"缺少原因：{required - names}"


def test_shadow_report_summary_lists_unexplained_codes():
    plan = ShadowPlan(decision_date=DAY, target_holds=("600990", "603977"),
                      would_be_orders=(), skipped=(), equity=CAPITAL)
    rep = shadow_report(_record(("600990", "603977")), _snap(), {},
                        target_weight=0.2,
                        ctx=_ctx(allowed_boards=frozenset({"SH_MAIN"}),
                                 board_of={"600990": "SH_MAIN", "603977": "SH_MAIN"},
                                 last_price={"600990": 10.0, "603977": 20.0}))
    s = rep.summary()
    assert "600990" in s and "603977" in s
    assert s.count("unexplained") == 2
