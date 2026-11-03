"""退出清理：四步串行、证据链、崩溃检测、拒绝启动。

这里的核心不是"清理能不能跑通"，而是**失败时会怎样**：
失败必须留下标记并阻止下次启动，否则"清理失败"就退化成一句日志。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from deepquant.safety.lifecycle import (
    CLEANUP_LOG,
    INFLIGHT,
    MARKER_FILE,
    NOT_APPLICABLE_STEPS,
    SESSION_FILE,
    CleanupStep,
    DirtyShutdown,
    assert_clean_start,
    begin_session,
    cleanup_log_entries,
    default_steps,
    end_session,
    pending_cleanup,
    run_cleanup,
    snapshot,
    stale_session,
    verify_log_chain,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "safety"
    monkeypatch.setenv("DEEPQUANT_SAFETY_DIR", str(root))
    monkeypatch.delenv("DEEPQUANT_ALLOW_DIRTY_START", raising=False)
    monkeypatch.delenv("DEEPQUANT_DRAIN_TIMEOUT_S", raising=False)
    INFLIGHT.reset()
    yield root
    INFLIGHT.reset()


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #


def test_snapshot_declares_not_applicable_steps() -> None:
    """无委托域的步骤必须始终可见，否则"缺的那块"会被当成已完成。"""
    snap = snapshot()
    assert set(snap["not_applicable"]) == set(NOT_APPLICABLE_STEPS)
    assert "cancel_open_orders" in snap["not_applicable"]
    assert snap["in_flight"] == 0


# --------------------------------------------------------------------------- #
# 成功清理
# --------------------------------------------------------------------------- #


def test_clean_run_writes_evidence_and_clears_marker(_env: Path) -> None:
    report = run_cleanup("test", root=_env)
    assert report.ok is True
    assert report.marker_cleared is True
    assert not (_env / MARKER_FILE).exists()
    assert report.before["in_flight"] == 0 and report.after["in_flight"] == 0

    entries = cleanup_log_entries(_env)
    assert entries and entries[-1]["event"] == "cleanup"
    assert entries[-1]["ok"] is True
    assert {s["name"] for s in entries[-1]["steps"]} >= {
        "stop_new_tasks",
        "drain_in_flight",
        "release_caches",
    }
    assert verify_log_chain(_env)["ok"] is True


def test_not_applicable_steps_are_recorded(_env: Path) -> None:
    report = run_cleanup("test", root=_env)
    statuses = {s.name: s.status for s in report.steps}
    for name in NOT_APPLICABLE_STEPS:
        assert statuses[name] == "skipped_not_applicable"
    # 不计入关键步骤判定
    assert all(s.ok for s in report.steps if s.status == "skipped_not_applicable")


# --------------------------------------------------------------------------- #
# 失败清理：必须留标记
# --------------------------------------------------------------------------- #


def test_failed_step_keeps_marker(_env: Path) -> None:
    def bad_verify() -> tuple[bool, str]:
        return False, "故意失败"

    steps = [CleanupStep("boom", lambda: None, bad_verify)]
    report = run_cleanup("test", steps=steps, root=_env)

    assert report.ok is False
    assert (_env / MARKER_FILE).exists(), "清理失败必须留下标记，否则下次启动无从判断"
    entries = cleanup_log_entries(_env)
    assert entries[-1]["ok"] is False
    assert entries[-1]["steps"][0]["detail"] == "故意失败"


def test_action_exception_is_recorded_not_swallowed(_env: Path) -> None:
    def boom() -> None:
        raise RuntimeError("动作炸了")

    steps = [CleanupStep("boom", boom, lambda: (True, ""))]
    report = run_cleanup("test", steps=steps, root=_env)
    assert report.ok is False
    assert "RuntimeError" in report.steps[0].detail


def test_non_critical_step_failure_does_not_block(_env: Path) -> None:
    steps = [
        CleanupStep("soft", lambda: None, lambda: (False, "可选步骤失败"), critical=False),
    ]
    report = run_cleanup("test", steps=steps, root=_env)
    assert report.ok is True


def test_drain_timeout_fails(_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超时窗内没排空 = 失败。计划的"超时窗内完成"是硬要求。"""
    holder = threading.Event()
    started = threading.Event()

    def hold() -> None:
        with INFLIGHT.track():
            started.set()
            holder.wait(2.0)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    started.wait(1.0)

    monkeypatch.setenv("DEEPQUANT_DRAIN_TIMEOUT_S", "0.05")
    report = run_cleanup("test", root=_env)
    holder.set()
    thread.join(timeout=2.0)

    assert report.ok is False
    drain = next(s for s in report.steps if s.name == "drain_in_flight")
    assert drain.ok is False and "未结束" in drain.detail


# --------------------------------------------------------------------------- #
# 启动门
# --------------------------------------------------------------------------- #


def test_clean_start_passes(_env: Path) -> None:
    assert assert_clean_start(root=_env)["clean"] is True


def test_marker_blocks_start(_env: Path) -> None:
    _env.mkdir(parents=True, exist_ok=True)
    (_env / MARKER_FILE).write_text(
        json.dumps({"started_at": "2026-10-27T10:00:00", "reason": "lifespan_shutdown"}),
        encoding="utf-8",
    )
    with pytest.raises(DirtyShutdown) as excinfo:
        assert_clean_start(root=_env)
    assert "清理" in str(excinfo.value)


def test_corrupt_marker_still_blocks_start(_env: Path) -> None:
    """标记文件损坏不能当成"不存在"——那正好是"没检出 ≠ 没问题"的翻版。"""
    _env.mkdir(parents=True, exist_ok=True)
    (_env / MARKER_FILE).write_text("{broken", encoding="utf-8")
    with pytest.raises(DirtyShutdown):
        assert_clean_start(root=_env)


def test_stale_session_blocks_start(_env: Path) -> None:
    """崩溃（没走退出钩子）也要被拦住：session 文件就是崩溃的证据。"""
    begin_session(root=_env)
    assert stale_session(root=_env) is not None
    with pytest.raises(DirtyShutdown) as excinfo:
        assert_clean_start(root=_env)
    assert "退出钩子" in str(excinfo.value)


def test_clean_shutdown_removes_session(_env: Path) -> None:
    begin_session(root=_env)
    report = run_cleanup("test", root=_env)
    assert report.ok is True
    end_session(root=_env)
    assert stale_session(root=_env) is None
    assert assert_clean_start(root=_env)["clean"] is True


def test_dirty_override_is_recorded(_env: Path) -> None:
    """知情放行必须留痕，否则这个开关就是下一次事故的免罪符。"""
    _env.mkdir(parents=True, exist_ok=True)
    (_env / MARKER_FILE).write_text('{"started_at": "x", "reason": "y"}', encoding="utf-8")

    result = assert_clean_start(root=_env, allow_dirty=True)
    assert result["clean"] is False and result["overridden"] is True

    entries = cleanup_log_entries(_env)
    assert entries[-1]["event"] == "dirty_start_override"
    assert verify_log_chain(_env)["ok"] is True


def test_override_via_env(monkeypatch: pytest.MonkeyPatch, _env: Path) -> None:
    monkeypatch.setenv("DEEPQUANT_ALLOW_DIRTY_START", "1")
    _env.mkdir(parents=True, exist_ok=True)
    (_env / MARKER_FILE).write_text('{"started_at": "x"}', encoding="utf-8")
    assert assert_clean_start(root=_env)["overridden"] is True


def test_pending_cleanup_reads_marker(_env: Path) -> None:
    assert pending_cleanup(_env) is None
    _env.mkdir(parents=True, exist_ok=True)
    (_env / MARKER_FILE).write_text('{"reason": "r"}', encoding="utf-8")
    assert pending_cleanup(_env)["reason"] == "r"


# --------------------------------------------------------------------------- #
# 证据链
# --------------------------------------------------------------------------- #


def test_log_chain_detects_tampering(_env: Path) -> None:
    run_cleanup("one", root=_env)
    run_cleanup("two", root=_env)
    path = _env / CLEANUP_LOG
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["ok"] = not payload["ok"]           # 篡改内容但不改摘要
    lines[0] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = verify_log_chain(_env)
    assert report["ok"] is False and report["broken_at"] == 1


def test_log_chain_detects_truncation(_env: Path) -> None:
    run_cleanup("one", root=_env)
    run_cleanup("two", root=_env)
    path = _env / CLEANUP_LOG
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[-1:]) + "\n", encoding="utf-8")  # 删掉第一条
    # 只剩一条时链无法发现"被删了前面"，但 prev 会对不上
    report = verify_log_chain(_env)
    assert report["ok"] is False


def test_empty_log_chain_ok(_env: Path) -> None:
    assert verify_log_chain(_env) == {"ok": True, "entries": 0, "broken_at": None}


# --------------------------------------------------------------------------- #
# 在飞计数
# --------------------------------------------------------------------------- #


def test_inflight_tracks_nested() -> None:
    with INFLIGHT.track():
        with INFLIGHT.track():
            assert INFLIGHT.count == 2
        assert INFLIGHT.count == 1
    assert INFLIGHT.count == 0


def test_inflight_released_on_exception() -> None:
    with pytest.raises(ValueError):
        with INFLIGHT.track():
            raise ValueError("x")
    assert INFLIGHT.count == 0


def test_wait_idle_timeout() -> None:
    holder = threading.Event()
    started = threading.Event()

    def hold() -> None:
        with INFLIGHT.track():
            started.set()
            holder.wait(2.0)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    started.wait(1.0)
    ok, remaining = INFLIGHT.wait_idle(0.05)
    holder.set()
    thread.join(timeout=2.0)
    assert ok is False and remaining == 1


def test_default_steps_names() -> None:
    names = [s.name for s in default_steps()]
    assert names == ["stop_new_tasks", "drain_in_flight", "release_caches"]
