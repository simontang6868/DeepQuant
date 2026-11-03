"""Phase 2.3-d：``RiskGate`` 的变异测试。

为什么闸门必须做变异测试
------------------------
单元测试回答的是"我写的那几个场景过不过"，变异测试回答的是"**删掉一条规则，
测试会不会红**"。前者在闸门上不够用——闸门的失效方式恰恰是「某条规则静默消失」
或「边界差一格」，而这两种都不会让正常路径的用例失败。

做法
----
读源码 → 字符串替换制造变异 → 从临时文件加载成新模块 → 用**同一组应当被拒/放行的
场景**跑变异模块 → 断言行为确实变了。

⚠️ 每个变异都先断言 `mutated != src`：**变异必须真改代码**。
本项目出现过两次"假变异"——替换串没匹配上，测试照样绿，于是白拿了一次"通过"。
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "deepquant" / "execution" / "risk_gate.py"
DAY = 20261030


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def _load_mutated(tmp_path: pathlib.Path, src: str, name: str):
    """把变异后的源码加载成一个独立模块。"""
    p = tmp_path / f"{name}.py"
    p.write_text(src, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _build(mod, *, auth_max=20_000.0, order_max=4_000.0, blacklist=frozenset()):
    """用**目标模块自己的类**构造闸门与上下文。"""
    limits = mod.RiskLimits(
        max_single_position_ratio=0.2, max_order_notional=order_max,
        max_daily_orders=12, max_price_deviation=0.01, max_quote_age_days=5,
        max_drawdown=0.025, blacklist=blacklist)
    auth = mod.Authorization(max_notional=auth_max, valid_until=dt.date(2027, 12, 31),
                             boards=frozenset({"SH_MAIN", "SZ_MAIN"}))
    state = mod.GateState(peak_equity=20_000.0, trading_day=DAY,
                          last_reconciliation_ok=True)
    gate = mod.RiskGate(limits=limits, state=state, authorization=auth)
    return gate, auth


def _ctx(mod, gate, **kw):
    from deepquant.broker.models import OrderRequest, Quote
    from deepquant.market.rules import Side
    base = dict(
        order=OrderRequest(client_order_id=f"{DAY}-600990-B-100", code="600990",
                           side=Side.BUY, shares=100, limit_price=10.0),
        quote=Quote(code="600990", last=10.0, ts=DAY, stale=False),
        equity=20_000.0, position_value=3_000.0, board="SH_MAIN",
        is_trading_hours=True, trading_day=DAY, halt_file_present=False)
    base.update(kw)
    return mod.OrderContext(**base)


def _mutate(tmp_path, old: str, new: str, name: str):
    src = _source()
    assert old in src, f"变异锚点不存在：{name}——源码改过就要同步更新变异"
    mutated = src.replace(old, new, 1)
    assert mutated != src, f"变异 {name} 没有真正改动源码（假变异）"
    return _load_mutated(tmp_path, mutated, name)


# ---------------------------------------------------------------------------
# M1 删掉熔断检查
# ---------------------------------------------------------------------------
def test_M1_removing_breaker_check_is_caught(tmp_path):
    """把 `_c_breaker` 整个改成永远放行。原实现应当拒，变异后应当通过。"""
    mod = _mutate(
        tmp_path,
        'def _c_breaker(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:\n'
        '    if gate.state.breaker_tripped:',
        'def _c_breaker(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:\n'
        '    if False:',
        "rg_m1")
    gate, _ = _build(mod)
    gate.state.breaker_tripped = True
    d = gate.evaluate(_ctx(mod, gate))
    assert d.allowed is True, "删掉熔断检查后竟然仍然拒单 —— 变异没生效"


# ---------------------------------------------------------------------------
# M2 从检查链里摘掉黑名单
# ---------------------------------------------------------------------------
def test_M2_removing_blacklist_from_chain_is_caught(tmp_path):
    mod = _mutate(tmp_path,
                  '    ("blacklist", _c_blacklist),\n',
                  '',
                  "rg_m2")
    gate, _ = _build(mod, blacklist=frozenset({"600990"}))
    d = gate.evaluate(_ctx(mod, gate))
    assert d.allowed is True, "摘掉黑名单检查后仍拒单 —— 变异没生效"
    assert "blacklist" not in [n for n, _ in gate.checks]


# ---------------------------------------------------------------------------
# M3 单票上限 `>` 写成 `>=`（过度拦截）
# ---------------------------------------------------------------------------
def test_M3_position_limit_ge_instead_of_gt_is_caught(tmp_path):
    """边界差一格：恰好等于上限应当**放行**，写成 `>=` 会误拒。"""
    mod = _mutate(tmp_path,
                  '    if ctx.position_value > cap + 1e-9:',
                  '    if ctx.position_value >= cap:',
                  "rg_m3")
    gate, _ = _build(mod)
    d = gate.evaluate(_ctx(mod, gate, position_value=4_000.0))   # 恰好等于上限
    assert d.allowed is False, "`>=` 应当把「恰好等于」判成超限 —— 变异没生效"
    assert d.code is mod.RejectCode.POSITION_LIMIT


# ---------------------------------------------------------------------------
# M4 价格偏离 `>` 写成 `>=`
# ---------------------------------------------------------------------------
def test_M4_loosening_price_deviation_threshold_is_caught(tmp_path):
    """把 1% 悄悄放宽到 10%（"反正从来没触发过"式的规则退化）。

    不用「恰好等于阈值」做锚点：float64 下 abs(10.1-10)/10 = 0.009999999999999964，
    任何 x*1.01 都得不到精确的 1%，那个边界在浮点里不存在。
    改为测「规则被放宽」这一更现实、也更危险的失效。
    """
    mod = _mutate(tmp_path,
                  '    if dev > gate.limits.max_price_deviation + 1e-12:',
                  '    if dev > gate.limits.max_price_deviation * 10:',
                  "rg_m4")
    gate, _ = _build(mod)
    d = gate.evaluate(_ctx(mod, gate, order=_order(mod, price=10.2)))   # 2%
    assert d.allowed is True, "阈值放宽到 10% 后 2% 偏离仍被拒 —— 变异没生效"


def _order(mod, shares: int = 100, price: float = 10.0):
    from deepquant.broker.models import OrderRequest
    from deepquant.market.rules import Side
    return OrderRequest(client_order_id=f"{DAY}-600990-B-{shares}", code="600990",
                        side=Side.BUY, shares=shares, limit_price=price)


# ---------------------------------------------------------------------------
# M5 ⭐ 行情缺失视为通过（预登记 P4 的变异）
# ---------------------------------------------------------------------------
def test_M5_treating_missing_quote_as_pass_is_caught(tmp_path):
    """把「没有行情 ⇒ 拒」改成「没有行情 ⇒ 跳过」。

    这是本项目历史上真实发生过的失效方式（拿不到就跳过），
    必须有一个变异体能把它复现出来。
    """
    mod = _mutate(
        tmp_path,
        '    if ctx.quote is None:\n'
        '        return GateFailure("quote", RejectCode.STALE_QUOTE,',
        '    if ctx.quote is None:\n'
        '        return None\n'
        '    if False:\n'
        '        return GateFailure("quote", RejectCode.STALE_QUOTE,',
        "rg_m5")
    gate, _ = _build(mod)
    d = gate.evaluate(_ctx(mod, gate, quote=None))
    # ⚠️ 整体仍是拒的：后面 `price_deviation` 这一道也会拦住（它单独实现了
    #    「没有最新价就拒」）。这正是**防御纵深**——一处失效不至于全盘放行。
    #    所以本变异体的判据是「quote 这条检查被跳过了」，不是「整体放行」。
    assert "quote" in d.checked, "缺行情时 quote 检查未被跳过 —— 变异没生效"
    assert d.code is mod.RejectCode.MISSING_INPUT, \
        "第二道闸（price_deviation）应当接过拦截责任"


# ---------------------------------------------------------------------------
# M6 对账 `is not True` 写成 `is False`（把「从未对账」放行）
# ---------------------------------------------------------------------------
def test_M6_relaxing_reconciliation_none_is_caught(tmp_path):
    """`None`（从未对账）在 `is False` 下会变成通过——方向完全反了。"""
    mod = _mutate(tmp_path,
                  '    if gate.state.last_reconciliation_ok is not True:',
                  '    if gate.state.last_reconciliation_ok is False:',
                  "rg_m6")
    gate, _ = _build(mod)
    gate.state.last_reconciliation_ok = None            # 从未对账
    d = gate.evaluate(_ctx(mod, gate))
    assert d.allowed is True, "「从未对账」仍被拒 —— 变异没生效"


# ---------------------------------------------------------------------------
# M7 单日笔数 `>=` 写成 `>`
# ---------------------------------------------------------------------------
def test_M7_daily_count_gt_instead_of_ge_is_caught(tmp_path):
    """恰好达上限应当**拒**，写成 `>` 会多放一笔。"""
    mod = _mutate(tmp_path,
                  '    if used >= gate.limits.max_daily_orders:',
                  '    if used > gate.limits.max_daily_orders:',
                  "rg_m7")
    gate, _ = _build(mod)
    gate.state.daily_order_count = 12                   # 恰好等于上限
    d = gate.evaluate(_ctx(mod, gate))
    assert d.allowed is True, "恰好达上限仍被拒 —— 变异没生效"


# ---------------------------------------------------------------------------
# M8 熔断 `>` 写成 `>=`（恰好触及阈值就熔断）
# ---------------------------------------------------------------------------
def test_M8_breaker_ge_instead_of_gt_is_caught(tmp_path):
    mod = _mutate(tmp_path,
                  '        if dd > self.limits.max_drawdown + 1e-12 and not self.state.breaker_tripped:',
                  '        if dd >= self.limits.max_drawdown and not self.state.breaker_tripped:',
                  "rg_m8")
    gate, _ = _build(mod)
    gate.observe_equity(20_000.0)
    gate.observe_equity(19_500.0)                       # 恰好 2.5%
    assert gate.state.breaker_tripped is True, "`>=` 应当把「恰好触及」判成熔断 —— 变异没生效"


# ---------------------------------------------------------------------------
# M9 ⭐ 峰值不落盘（预登记 P5 的变异）
# ---------------------------------------------------------------------------
def test_M9_not_persisting_peak_is_caught(tmp_path):
    """只存内存的实现：重启后峰值为 None，回撤被重算成 0，熔断永不触发。"""
    mod = _mutate(tmp_path,
                  '            "peak_equity": self.peak_equity,',
                  '            "peak_equity": None,',
                  "rg_m9")
    sp = tmp_path / "state.json"
    gate, _ = _build(mod)
    gate.state_path = sp
    gate.observe_equity(25_000.0)

    reloaded = mod.GateState.load(sp)
    assert reloaded.peak_equity is None, "峰值竟然落盘了 —— 变异没生效"

    gate2, _ = _build(mod)
    gate2.state = reloaded
    gate2.state_path = sp
    gate2.observe_equity(24_000.0)                      # 相对真实峰值的 4% 回撤
    assert gate2.state.breaker_tripped is False, (
        "峰值丢失后仍触发熔断 —— 变异没生效")


# ---------------------------------------------------------------------------
# 变异清单自检
# ---------------------------------------------------------------------------
def test_mutation_anchors_all_exist():
    """所有变异锚点必须在源码里存在一次且仅一次。

    没有这条，源码一改（比如把 `>` 改成 `>=` 自己修了 bug），
    变异测试会从「抓到」静默变成「没抓到」，却仍然显示 passed。
    """
    src = _source()
    anchors = [
        '    if ctx.position_value > cap + 1e-9:',
        '    if dev > gate.limits.max_price_deviation + 1e-12:',
        '    if gate.state.last_reconciliation_ok is not True:',
        '    if used >= gate.limits.max_daily_orders:',
        '        if dd > self.limits.max_drawdown + 1e-12 and not self.state.breaker_tripped:',
        '            "peak_equity": self.peak_equity,',
        '    ("blacklist", _c_blacklist),\n',
    ]
    for a in anchors:
        assert src.count(a) == 1, f"锚点出现 {src.count(a)} 次（应为 1）：{a!r}"
