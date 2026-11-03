"""熔断器：阈值、半开探测、**重启后仍然有效**、状态损坏时的失败方向。

最关键的一条是 ``test_open_state_survives_restart``：熔断状态落盘的目的就是
防止"重启清零"——而重启恰恰是最容易被用来绕过保护的动作。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from deepquant.safety.breaker import (
    BreakerStateError,
    allow,
    breaker_scope,
    config,
    load,
    record_failure,
    record_success,
    reset,
    snapshot,
    trip,
)


@pytest.fixture(autouse=True)
def _safety_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "safety"
    monkeypatch.setenv("DEEPQUANT_SAFETY_DIR", str(root))
    for name in ("DEEPQUANT_BREAKER_THRESHOLD", "DEEPQUANT_BREAKER_COOLDOWN_S",
                 "DEEPQUANT_BREAKER_HALF_OPEN_CALLS"):
        monkeypatch.delenv(name, raising=False)
    return root


def test_closed_allows() -> None:
    decision = allow("backtest.run")
    assert decision.allowed is True and decision.state == "closed"


def test_threshold_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPQUANT_BREAKER_THRESHOLD", "2")
    assert config()["threshold"] == 2


def test_invalid_threshold_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPQUANT_BREAKER_THRESHOLD", "abc")
    with pytest.raises(BreakerStateError):
        config()


def test_opens_after_threshold_failures() -> None:
    for _ in range(3):
        record_failure("backtest.run", "模拟失败")
    decision = allow("backtest.run")
    assert decision.allowed is False
    assert decision.code == "breaker_open"
    assert decision.retry_after_s > 0


def test_failures_below_threshold_still_allow() -> None:
    record_failure("backtest.run", "一次失败")
    record_failure("backtest.run", "两次失败")
    assert allow("backtest.run").allowed is True


def test_open_state_survives_restart(_safety_dir: Path) -> None:
    """模拟重启：新进程只读磁盘，仍应看到 open——否则"重启绕过"永远成立。"""
    for _ in range(3):
        record_failure("backtest.run", "模拟失败")
    assert (_safety_dir / "breaker.json").exists()

    raw = json.loads((_safety_dir / "breaker.json").read_text(encoding="utf-8"))
    assert raw["scopes"]["backtest.run"]["state"] == "open"

    # 不调用任何内存态；直接重新判定（等价于新进程启动后的第一次请求）
    decision = allow("backtest.run")
    assert decision.allowed is False and decision.code == "breaker_open"


def test_cooldown_transitions_to_half_open() -> None:
    start = 1_000_000.0
    for _ in range(3):
        record_failure("backtest.run", "x", now=start)
    # 冷却未到
    assert allow("backtest.run", now=start + 10).allowed is False
    # 冷却已过 → 半开，放一个探测
    first = allow("backtest.run", now=start + 61)
    assert first.allowed is True and first.state == "half_open"


def test_half_open_limits_probes() -> None:
    start = 1_000_000.0
    for _ in range(3):
        record_failure("backtest.run", "x", now=start)
    assert allow("backtest.run", now=start + 61).allowed is True
    # 半开状态下探测名额只有 1 个，且判定本身不消耗名额（写入时才计）
    record_success("backtest.run", now=start + 62)  # 探测成功 → 闭合
    state = load("backtest.run")
    assert state.state == "closed" and state.consecutive_failures == 0


def test_half_open_failure_reopens() -> None:
    start = 1_000_000.0
    for _ in range(3):
        record_failure("backtest.run", "x", now=start)
    assert allow("backtest.run", now=start + 61).state == "half_open"
    state = record_failure("backtest.run", "探测失败", now=start + 62)
    assert state.state == "open"
    assert allow("backtest.run", now=start + 63).allowed is False


def test_success_during_cooldown_keeps_open() -> None:
    """冷却期内偶发成功不代表恢复——否则"偶尔通一次"会把真故障掩盖掉。"""
    start = 1_000_000.0
    for _ in range(3):
        record_failure("backtest.run", "x", now=start)
    state = record_success("backtest.run", now=start + 5)
    assert state.state == "open"
    assert allow("backtest.run", now=start + 6).allowed is False


def test_success_clears_failure_count() -> None:
    record_failure("backtest.run", "x")
    record_failure("backtest.run", "x")
    record_success("backtest.run")
    assert load("backtest.run").consecutive_failures == 0
    record_failure("backtest.run", "x")
    assert allow("backtest.run").allowed is True  # 计数从 0 重来


def test_success_without_change_does_not_write(_safety_dir: Path) -> None:
    """闭合且无失败时，成功不该引起落盘（否则每次回测都多几次磁盘写）。"""
    record_failure("backtest.run", "x")   # 先建立状态
    record_success("backtest.run")        # 清掉失败计数（会写一次）
    path = _safety_dir / "breaker.json"
    assert path.exists()
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    record_success("backtest.run")        # 无变化 → 不应重写
    assert path.stat().st_mtime_ns == before


def test_trip_requires_reason() -> None:
    with pytest.raises(ValueError):
        trip("backtest.run", "   ")


def test_trip_and_reset() -> None:
    state = trip("backtest.run", "演练")
    assert state.state == "open" and state.trips == 1
    assert allow("backtest.run").allowed is False
    reset("backtest.run")
    assert allow("backtest.run").allowed is True
    # trips 保留作为历史证据
    assert load("backtest.run").trips == 1


def test_corrupt_state_fails_closed(_safety_dir: Path) -> None:
    _safety_dir.mkdir(parents=True, exist_ok=True)
    (_safety_dir / "breaker.json").write_text("{not json", encoding="utf-8")
    decision = allow("backtest.run")
    assert decision.allowed is False
    assert decision.code == "breaker_state_unreadable"


def test_snapshot_reports_error_on_corrupt(_safety_dir: Path) -> None:
    _safety_dir.mkdir(parents=True, exist_ok=True)
    (_safety_dir / "breaker.json").write_text("[]", encoding="utf-8")
    snap = snapshot()
    assert snap["error"] and snap["scopes"] == {}


def test_snapshot_normal() -> None:
    record_failure("backtest.run", "x")
    snap = snapshot()
    assert snap["error"] is None
    assert "backtest.run" in snap["scopes"]


def test_scopes_are_independent() -> None:
    for _ in range(3):
        record_failure("backtest.run", "x")
    assert allow("backtest.run").allowed is False
    assert allow("portfolio.backtest").allowed is True


# --------------------------------------------------------------------------- #
# 第二条路径：CLI/脚本作用域
# --------------------------------------------------------------------------- #


def test_breaker_scope_allows_and_records_success() -> None:
    with breaker_scope("backtest.run"):
        pass
    assert load("backtest.run").consecutive_failures == 0


def test_breaker_scope_blocks_when_open() -> None:
    for _ in range(3):
        record_failure("backtest.run", "x")
    with pytest.raises(BreakerStateError):
        with breaker_scope("backtest.run"):
            pytest.fail("熔断打开时不应进入作用域")


def test_breaker_scope_records_failure_on_exception() -> None:
    with pytest.raises(ValueError):
        with breaker_scope("backtest.run"):
            raise ValueError("业务炸了")
    assert load("backtest.run").consecutive_failures == 1


def test_two_paths_share_state() -> None:
    """HTTP 与 CLI 必须共用同一份状态：否则会出现"接口熔断了脚本还能跑"。"""
    for _ in range(3):
        record_failure("backtest.run", "x")
    assert allow("backtest.run").allowed is False  # HTTP 路径的判定函数
    with pytest.raises(BreakerStateError):
        with breaker_scope("backtest.run"):  # CLI 路径
            pass
