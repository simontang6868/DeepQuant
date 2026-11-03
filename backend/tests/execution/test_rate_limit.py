"""Phase 2.10-b：申报速率限速器的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.10-b
「草稿必须与实际实现一致（速率/笔数上限来自配置，不是编的）」。

**本文件的最终目的不是"限速器能用"，而是"申报表上那个数字是真的"**
----------------------------------------------------------------
`test_measured_rate_never_exceeds_configured` 用实测流量验证理论上界：
按最激进的方式连续申请，看任意一秒内实际放行了几笔。若实现里"稳态速率"
其实没被约束到，这条会红——那时申报表填的数字就是假的。

三条钉死的行为
----------
1. **重启不清零**（`test_tokens_survive_restart`）：不恢复令牌数 = 重启刷单。
2. **时钟倒退不清空也不补满**（`test_clock_regression_neither_resets_nor_refills`）。
3. **逻辑时钟，无 wall clock**（`test_module_does_not_read_wall_clock`）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from deepquant.execution.rate_limit import (
    PERMIT_EPS,
    RateLimitExceeded,
    RateLimiter,
    max_orders_in_window,
    orders_per_second_upper_bound,
)

SRC = (pathlib.Path(__file__).resolve().parents[2]
       / "src" / "deepquant" / "execution" / "rate_limit.py")

#: 申报材料里用的配置。**测试直接读这两个常量**，材料也从同一处引用，
#: 保证"纸面数字"与"受测数字"是同一个来源。
RATE = 2.0        # 笔/秒
BURST = 2         # 瞬时突发


def _limiter(rate=RATE, burst=BURST) -> RateLimiter:
    return RateLimiter(rate_per_second=rate, burst=burst)


# --------------------------------------------------------------------------- 基本


def test_bucket_starts_full_so_the_first_order_never_waits():
    """开盘第一笔不该莫名被拒——那时正是最需要发单的时刻。"""
    lim = _limiter()
    assert lim.tokens == pytest.approx(BURST)
    assert lim.try_acquire(0.0) is True
    assert lim.try_acquire(0.0) is True
    assert lim.try_acquire(0.0) is False, "桶只有 2 个令牌"


def test_refill_is_proportional_to_elapsed_logical_time():
    lim = _limiter(rate=2.0, burst=4)
    for _ in range(4):
        assert lim.try_acquire(0.0) is True
    assert lim.try_acquire(0.0) is False
    assert lim.try_acquire(0.25) is False, "0.25s × 2/s = 0.5 个令牌，不够"
    assert lim.try_acquire(0.5) is True, "0.5s × 2/s = 1 个令牌，刚好够"
    assert lim.tokens == pytest.approx(0.0, abs=1e-9)


def test_tokens_never_exceed_capacity():
    lim = _limiter(rate=2.0, burst=2)
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    lim.try_acquire(1000.0)          # 隔了很久
    assert lim.tokens <= BURST + 1e-9
    assert lim.try_acquire(1000.0) is True
    assert lim.try_acquire(1000.0) is False


def test_acquire_raises_while_try_acquire_returns_false():
    lim = _limiter()
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    assert lim.try_acquire(0.0) is False
    with pytest.raises(RateLimitExceeded, match="速率超限"):
        lim.acquire(0.0)


def test_retry_after_tells_how_long_to_wait():
    lim = _limiter(rate=2.0, burst=2)
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    assert lim.retry_after(0.0) == pytest.approx(0.5)
    assert lim.retry_after(0.5) == 0.0
    # 等 0.6 秒后确实可以取
    assert lim.try_acquire(0.6) is True


def test_counters_track_granted_and_denied():
    lim = _limiter()
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)      # 拒
    assert lim.granted == 2
    assert lim.denied == 1


# --------------------------------------------------------------------------- 上界


def test_upper_bound_formula_is_burst_plus_rate():
    """申报表要填的是**瞬时上界**，不是稳态速率。

    填稳态 2/s 会低估瞬时能力（实际一秒内可以先发 2 笔突发、再按 2/s 补 2 笔）。
    监管关心的是瞬时。
    """
    lim = _limiter(rate=2.0, burst=2)
    assert lim.orders_per_second_upper_bound() == pytest.approx(4.0)
    assert lim.steady_state_orders_per_second() == pytest.approx(2.0)
    assert orders_per_second_upper_bound(2.0, 2) == pytest.approx(4.0)


def test_measured_rate_never_exceeds_configured():
    """⭐⭐ 用**实测**证明申报数字是真的。

    模拟最激进的调用方：每 1 毫秒申请一次，持续 5 秒（5000 次请求）。
    统计任意 1 秒滑窗内的放行数，断言不超过 ``burst + rate×1``。
    若实现里稳态速率其实没约束住，这条必红。
    """
    lim = _limiter(rate=2.0, burst=2)
    grants: list[float] = []
    ts = 0.0
    for _ in range(5000):
        if lim.try_acquire(ts):
            grants.append(ts)
        ts += 0.001

    assert len(grants) > 0
    # 任意 1 秒滑窗
    worst = 0
    for i, t0 in enumerate(grants):
        n = sum(1 for t in grants[i:] if t <= t0 + 1.0)
        worst = max(worst, n)
    assert worst <= lim.orders_per_second_upper_bound() + 1e-9, (
        f"实测 1 秒内最多放行 {worst} 笔，超过申报上界 "
        f"{lim.orders_per_second_upper_bound()}")


def test_measured_long_run_average_matches_steady_state():
    """长跑平均必须落在稳态速率附近（±5%）。"""
    lim = _limiter(rate=2.0, burst=2)
    ts, granted_count = 0.0, 0
    for _ in range(20_000):        # 20 秒
        if lim.try_acquire(ts):
            granted_count += 1
        ts += 0.001
    avg = granted_count / 20.0
    assert avg == pytest.approx(2.0, rel=0.05), f"20 秒平均 {avg}/s"


def test_max_orders_in_window_is_consistent_with_simulation():
    """窗口上界公式与实测一致（防止公式与实现各说一套）。"""
    lim = _limiter(rate=2.0, burst=2)
    window = 1.0
    grants: list[float] = []
    ts = 0.0
    for _ in range(3000):
        if lim.try_acquire(ts):
            grants.append(ts)
        ts += 0.001
    # 从 0 开始的窗口
    observed = sum(1 for t in grants if t <= window)
    assert observed <= max_orders_in_window(2.0, 2, window)


# --------------------------------------------------------------------------- 持久化


def test_tokens_survive_restart(tmp_path):
    """⭐⭐ 重启不得满血复活。

    这条是"重启刷单"的唯一防线：内存桶一旦随进程消失，崩溃重启后
    立刻又有一整桶令牌，而这几秒恰恰是没人盯着的。
    """
    lim = _limiter(rate=2.0, burst=2)
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    assert lim.try_acquire(0.0) is False

    d = lim.to_dict()
    lim2 = RateLimiter.from_dict(d)

    assert lim2.tokens == pytest.approx(0.0, abs=1e-9), "重启后令牌被补满了"
    assert lim2.try_acquire(0.0) is False, "重启后立刻又能发单"


def test_restart_keeps_counters_and_clock():
    lim = _limiter()
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)
    lim.try_acquire(0.0)     # denied
    lim2 = RateLimiter.from_dict(lim.to_dict())
    assert lim2.granted == 2
    assert lim2.denied == 1
    assert lim2.clock_regressions == 0


def test_from_dict_clamps_inconsistent_tokens():
    """配置被改小后落盘值可能超容量 ⇒ 夹回，不报错（否则重启起不来）。"""
    d = {"rate_per_second": 2.0, "burst": 2, "tokens": 99.0, "last_ts": 5.0}
    lim = RateLimiter.from_dict(d)
    assert lim.tokens == pytest.approx(2.0)
    assert lim.try_acquire(5.0) is True


def test_dict_roundtrip_is_lossless():
    lim = _limiter()
    lim.try_acquire(1.5)
    d = lim.to_dict()
    lim2 = RateLimiter.from_dict(d)
    assert lim2.to_dict() == d


# --------------------------------------------------------------------------- 时钟


def test_clock_regression_neither_resets_nor_refills():
    """⭐⭐ 时钟倒退：不清空（否则可用来无限刷）、不补满（否则送一整桶）。"""
    lim = _limiter(rate=2.0, burst=2)
    lim.try_acquire(10.0)
    lim.try_acquire(10.0)
    assert lim.tokens == pytest.approx(0.0, abs=1e-9)

    assert lim.try_acquire(5.0) is False, "倒退时凭空有了令牌"
    assert lim.tokens == pytest.approx(0.0, abs=1e-9)
    assert lim.clock_regressions == 1


def test_clock_regression_counter_is_a_visible_signal():
    lim = _limiter()
    for ts in (10.0, 9.0, 8.0):
        lim.try_acquire(ts)
    assert lim.clock_regressions == 2


def test_forward_time_after_regression_works_normally():
    """倒退之后时间继续前进，应当正常补充（不能永久卡死）。"""
    lim = _limiter(rate=2.0, burst=2)
    lim.try_acquire(10.0)
    lim.try_acquire(10.0)
    lim.try_acquire(1.0)              # 倒退
    assert lim.try_acquire(11.0) is True, "倒退后前进 10 秒仍取不到令牌"


# --------------------------------------------------------------------------- 配置错误


def test_single_request_larger_than_bucket_is_a_config_error():
    """一次要的令牌超过桶容量 —— 这个请求**永远**不可能满足，是配置错。"""
    lim = _limiter(rate=2.0, burst=2)
    with pytest.raises(ValueError, match="超过桶容量"):
        lim.try_acquire(0.0, n=5)


def test_invalid_config_is_rejected():
    with pytest.raises(ValueError):
        RateLimiter(rate_per_second=0, burst=1)
    with pytest.raises(ValueError):
        RateLimiter(rate_per_second=-1.0, burst=1)
    with pytest.raises(ValueError):
        RateLimiter(rate_per_second=1.0, burst=0)


def test_module_does_not_read_wall_clock():
    """⭐ 逻辑时钟：不 import time/datetime，否则限速不可重放、单测要 sleep。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    banned = [m for m in mods if m.split(".")[0] in {"time", "datetime"}]
    assert banned == [], f"rate_limit.py 读了 wall clock：{banned}"


def test_scanner_can_catch_a_wall_clock_import():
    """检查器自检。"""
    bad = "import time\ndatetime = 1\n"
    tree = ast.parse(bad)
    mods = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names]
    assert "time" in mods


# --------------------------------------------------------------------------- 与笔数上限的关系


def test_daily_cap_is_the_binding_constraint_for_this_strategy():
    """⭐ 本策略的约束是**日笔数**而不是速率。

    月度调仓、单日最多 12 笔；即使按 2/s 发，12 笔只需约 5 秒。
    所以申报材料里"单日笔数"是主约束，速率是次要保证——这一点必须写对，
    否则会让审查方以为这是个高频策略。
    """
    daily_cap = 12
    lim = _limiter(rate=2.0, burst=2)
    # 从开盘起连续发，看 12 笔要多久
    ts, sent = 0.0, 0
    while sent < daily_cap:
        if lim.try_acquire(ts):
            sent += 1
        ts += 0.001
    assert ts <= 6.0, f"12 笔用了 {ts:.2f}s，与「不是高频」的表述不符"
    assert daily_cap < max_orders_in_window(2.0, 2, 60.0), (
        "日笔数上限应当小于一分钟的速率上限，否则主约束就不是笔数了")
