"""Phase 2.4 的变异测试：状态机与重建的四条硬规矩必须"删了就红"。

为什么 2.4 也要变异
------------------
2.4 的四条要求（``UNKNOWN`` 阻断、非法转移抛异常、以券商为准、重建只读）都属于
**「失效时完全看不出来」**的类型：状态机少一条校验，正常路径一切照旧；重建多看
一眼缓存，结果通常也一样。只有把规则删掉、看测试红不红，才知道测试到底有没有
守住它。

⚠️ 每个变异都先断言 ``mutated != src``。本项目出现过两次「假变异」——替换串没
匹配上，测试照样绿，于是白拿一次通过。

⭐ 一个在写变异时才发现的事实
--------------------------
「终态不可离开」是**两处共同**保证的：``apply`` 里的显式终态检查，和
``TRANSITIONS`` 里终态映射为空集。只删显式检查（M1a）**行为不变**——表还在拦。
所以 M1 必须**同时**给表加后继才构成真变异。这不是冗余代码，是分层：
一处说"为什么不行"（给人和日志），一处说"行不行"（给机器）。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

from deepquant.broker.models import BrokerOrder, BrokerOrderStatus
from deepquant.market.rules import Side

SRC = (pathlib.Path(__file__).resolve().parents[2]
       / "src" / "deepquant" / "execution" / "state_machine.py")
COID = "20261030-600990-B-200"


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def _load(tmp_path: pathlib.Path, src: str, name: str):
    p = tmp_path / f"{name}.py"
    p.write_text(src, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mutate(tmp_path, pairs, name: str):
    """``pairs`` 是 ``[(old, new), ...]``，逐个替换。**每个锚点都必须存在**。"""
    src = _source()
    mutated = src
    for old, new in pairs:
        assert old in mutated, f"变异锚点不存在：{name}｜{old[:50]!r}"
        mutated = mutated.replace(old, new, 1)
    assert mutated != src, f"变异 {name} 没有真正改动源码（假变异）"
    return _load(tmp_path, mutated, name)


def _order(status=BrokerOrderStatus.ACCEPTED, *, filled: int = 0, shares: int = 200,
           ts: int = 1) -> BrokerOrder:
    return BrokerOrder(client_order_id=COID, broker_order_id="B-" + COID,
                       code="600990", side=Side.BUY, shares=shares,
                       limit_price=10.0, status=status, filled_shares=filled,
                       updated_ts=ts)


# --------------------------------------------------------------------------- M1


def test_M1_terminal_state_can_be_left_if_both_guards_removed(tmp_path):
    """终态必须**同时**删显式检查与表后继才放行——证明两处都在守。

    ⚠️ 场景用 ``FILLED(200) → CANCELLED(200)`` 而不是 ``→ PARTIALLY_FILLED(100)``：
    后者会先被 **``filled_shares`` 单调性**拦住（200 → 100），于是这条变异体
    测到的是第三道守卫，而不是"终态不可离开"。写变异时才看清原来这里是三层：
    显式终态检查（给人读的原因）／转移表（机器判）／数量单调（数据一致性）。
    """
    mod = _mutate(tmp_path, [
        ("        if old.status in TERMINAL_STATUSES:",
         "        if False:"),
        ("    BrokerOrderStatus.FILLED: frozenset(),",
         "    BrokerOrderStatus.FILLED: frozenset({BrokerOrderStatus.CANCELLED}),"),
    ], "sm_m1")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.FILLED, filled=200))
    out = sm.apply(_order(BrokerOrderStatus.CANCELLED, filled=200, ts=2))
    assert out.status is BrokerOrderStatus.CANCELLED, (
        "两处守卫都删了，终态仍不可离开 —— 变异没生效")


def test_M1b_removing_only_the_explicit_check_keeps_behaviour(tmp_path):
    """⭐ 只删显式终态检查，行为**不变**：转移表仍在拦。

    这条不是可有可无的补充，它记录了"这里有两道守卫"这个事实本身。
    如果哪天有人"清理冗余代码"删掉显式检查又顺手改了表，M1 会提醒他。
    """
    mod = _mutate(tmp_path, [
        ("        if old.status in TERMINAL_STATUSES:",
         "        if False:"),
    ], "sm_m1b")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.FILLED, filled=200))
    try:
        sm.apply(_order(BrokerOrderStatus.CANCELLED, filled=200, ts=2))
    except mod.IllegalTransition:
        pass
    else:
        raise AssertionError("只删显式检查就不拦了 —— 说明转移表没有终态保护")
    assert sm.status is BrokerOrderStatus.FILLED


# --------------------------------------------------------------------------- M2


def test_M2_local_can_release_unknown(tmp_path):
    """去掉 ``source != "broker"`` 校验 ⇒ 本地可以自行解封 UNKNOWN。

    这正是 2.4-c 要禁止的：本地推断不算数。
    """
    mod = _mutate(tmp_path, [
        ('and source != "broker":', "and False:"),
    ], "sm_m2")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.UNKNOWN))
    sm.apply(_order(BrokerOrderStatus.FILLED, filled=200, ts=2), source="local")
    assert sm.status is BrokerOrderStatus.FILLED, "变异没生效"


# --------------------------------------------------------------------------- M3


def test_M3_filled_shares_may_decrease(tmp_path):
    mod = _mutate(tmp_path, [
        ("        if new.filled_shares < old.filled_shares:", "        if False:"),
    ], "sm_m3")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=150, ts=5))
    sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=6))
    assert sm.order.filled_shares == 100, "变异没生效"


# --------------------------------------------------------------------------- M4


def test_M4_rebuild_resets_peak_equity(tmp_path):
    """⭐⭐ P5 的变异：重建顺手把峰值改小 ⇒ 熔断永不触发。

    这条变异体存在的全部意义，就是保证 ``test_rebuild_does_not_reset_peak_equity``
    真的能红。
    """
    mod = _mutate(tmp_path, [
        ("    state.has_unknown_orders = rebuilt.blocked",
         "    state.has_unknown_orders = rebuilt.blocked\n"
         "    state.peak_equity = rebuilt.cash.total"),
    ], "sm_m4")
    # GateState 属于 risk_gate，不在被变异的模块里——用真的那个。
    from deepquant.broker.models import Cash
    from deepquant.execution.risk_gate import GateState

    state = GateState(peak_equity=100_000.0, trading_day=20261030,
                      last_reconciliation_ok=True)
    rebuilt = mod.RebuiltState(
        positions=(), cash=Cash(available=80_000.0, frozen=0.0, total=80_000.0),
        orders=(), fills=(), reads=())
    mod.apply_rebuild_to_gate_state(rebuilt, state)
    assert state.peak_equity == 80_000.0, "变异没生效"


# --------------------------------------------------------------------------- M5


def test_M5_readonly_guard_lets_place_order_through(tmp_path):
    """去掉 ``place_order`` 的显式拦截 ⇒ 重建期间能下单（2.4-d 失效）。"""
    mod = _mutate(tmp_path, [
        ("    def place_order(self, *args, **kwargs):\n"
         "        raise ReadOnlyViolation(",
         "    def place_order(self, *args, **kwargs):\n"
         "        return self._inner.place_order(*args, **kwargs)\n"
         "        raise ReadOnlyViolation("),
    ], "sm_m5")

    class _Inner:
        def __init__(self):
            self.hit = False

        def place_order(self, *a, **k):
            self.hit = True
            return "SENT"

    inner = _Inner()
    ro = mod.ReadOnlyBroker(inner)
    assert ro.place_order(None) == "SENT"
    assert inner.hit is True, "变异没生效"


# --------------------------------------------------------------------------- M6


def test_M6_identity_fields_can_be_mutated(tmp_path):
    """去掉身份字段校验 ⇒ 把 ``shares`` 改小就能伪装成合法部分成交。"""
    mod = _mutate(tmp_path, [
        ('        for f in ("code", "side", "shares"):', "        for f in ():"),
    ], "sm_m6")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100))
    sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, shares=150, ts=2))
    assert sm.order.shares == 150, "变异没生效"


# --------------------------------------------------------------------------- M7


def test_M7_logical_clock_may_go_backwards(tmp_path):
    mod = _mutate(tmp_path, [
        ("        if new.updated_ts < old.updated_ts:", "        if False:"),
    ], "sm_m7")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.ACCEPTED, ts=9))
    sm.apply(_order(BrokerOrderStatus.PARTIALLY_FILLED, filled=100, ts=8))
    assert sm.order.updated_ts == 8, "变异没生效"


# --------------------------------------------------------------------------- M8


def test_M8_nothing_blocks_new_orders(tmp_path):
    """``is_blocking`` 恒 False ⇒ UNKNOWN 不再阻断新单（2.4-a 失效）。"""
    mod = _mutate(tmp_path, [
        ("        return self._order.status is BrokerOrderStatus.UNKNOWN",
         "        return False"),
    ], "sm_m8")
    sm = mod.OrderStateMachine(_order(BrokerOrderStatus.UNKNOWN))
    assert sm.is_blocking() is False, "变异没生效"
    reg = mod.OrderRegistry([_order(BrokerOrderStatus.UNKNOWN)])
    assert reg.blocking() is False


# --------------------------------------------------------------------------- M9


def test_M9_broker_only_orders_are_dropped(tmp_path):
    """⭐ P3 的变异：重建时忽略「券商有、本地无」的委托。

    只遍历本地去券商侧查的实现就是长这样——``if m is None: continue``。
    """
    mod = _mutate(tmp_path, [
        ("            if m is None:\n"
         "                self._machines[bo.client_order_id] = OrderStateMachine(bo)",
         "            if m is None:\n"
         "                continue"),
    ], "sm_m9")
    reg = mod.OrderRegistry()          # 本地空
    overrides = reg.reconcile([_order(BrokerOrderStatus.FILLED, filled=200)])
    assert overrides == (), "变异没生效"
    assert len(reg) == 0, "券商有的委托被丢了"


# --------------------------------------------------------------------------- 锚点自检


def test_mutation_anchors_all_exist():
    """所有变异锚点必须在源码里**恰好出现一次**。

    没有这条，源码一改（比如自己把 bug 修了），变异会从"抓到"静默退化成
    "没抓到"而测试仍显示 passed——这就是假变异。
    """
    src = _source()
    anchors = [
        "        if old.status in TERMINAL_STATUSES:",
        "    BrokerOrderStatus.FILLED: frozenset(),",
        'and source != "broker":',
        "        if new.filled_shares < old.filled_shares:",
        "    state.has_unknown_orders = rebuilt.blocked",
        '        for f in ("code", "side", "shares"):',
        "        if new.updated_ts < old.updated_ts:",
        "        return self._order.status is BrokerOrderStatus.UNKNOWN",
        "                self._machines[bo.client_order_id] = OrderStateMachine(bo)",
    ]
    for a in anchors:
        assert src.count(a) == 1, (
            f"锚点出现 {src.count(a)} 次（应为 1）：{a!r}——"
            "源码改动后必须同步更新变异测试，否则会静默变成假变异")
