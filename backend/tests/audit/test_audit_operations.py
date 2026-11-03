"""核心操作注册表、覆盖率与业务侧记录器测试。

覆盖《推进计划》T1 的「核心操作工件覆盖率 100%」：
覆盖率必须建立在**显式枚举**的操作清单上，且声明在范围内的操作都要有可达的实现位置。

同时验证「未启用审计时零写入」——这是既有 1997 项测试不受影响的前提。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepquant.audit.middleware import registered_operations
from deepquant.audit.operations import (
    AUDIT_DIR_ENV,
    CORE_OPERATIONS,
    AuditConfigurationError,
    assert_coverage_complete,
    audit_enabled,
    coverage,
    record_operation,
    replay,
)
from deepquant.audit.store import AuditStore


def test_coverage_is_complete() -> None:
    """声明在范围内的核心操作必须 100% 有实现位置。"""
    report = coverage()
    assert report["ratio"] == 1.0, f"覆盖率不足：{report}"
    assert report["missing"] == []
    assert report["unreachable_implementation"] == []
    assert_coverage_complete()


def test_out_of_scope_operations_have_reason() -> None:
    """不在范围内的操作必须写明原因，避免「没做到」冒充「不需要」。"""
    report = coverage()
    assert report["unexplained_out_of_scope"] == []
    assert set(report["not_applicable"]) == {
        "order.entry",
        "order.review",
        "order.amend",
        "order.cancel",
    }


def test_middleware_routes_cover_declared_operations() -> None:
    """注册表登记的中间件实现，必须真的在路由映射里存在。"""
    routed = set(registered_operations())
    declared = {
        op.op_id
        for op in CORE_OPERATIONS
        if op.in_scope and (op.instrumented_at or "").startswith("deepquant.audit.middleware")
    }
    assert declared <= routed, f"注册表声明了中间件实现但路由表缺失：{declared - routed}"


def test_every_operation_id_is_unique() -> None:
    ids = [op.op_id for op in CORE_OPERATIONS]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# record_operation
# --------------------------------------------------------------------------- #
def test_record_operation_writes_complete_card(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit")
    with record_operation(
        "backtest.run",
        operator="ops",
        basis={"rule": "冻结配置"},
        subject={"symbol": "TEST01"},
        session="sess-1",
        caveats=["仅日频"],
        store=store,
    ) as draft:
        assert draft is not None
        draft.outcome["status_code"] = 200

    cards = store.query(action="backtest.run")
    assert len(cards) == 1
    card = cards[0]
    assert card["operator"] == "ops"
    assert card["session"] == "sess-1"
    assert card["outcome"] == {"status": "ok", "status_code": 200}
    assert card["caveats"] == ["仅日频"]
    assert card["basis"]["rule"] == "冻结配置"
    assert store.verify().ok is True


def test_record_operation_logs_failure_and_reraises(tmp_path: Path) -> None:
    """失败操作也要落卡：只记成功会让工件变成报喜不报忧的宣传册。"""
    store = AuditStore(tmp_path / "audit")
    with pytest.raises(RuntimeError, match="boom"):
        with record_operation(
            "backtest.run", operator="ops", basis={"rule": "x"}, store=store
        ):
            raise RuntimeError("boom")

    card = store.query(action="backtest.run")[0]
    assert card["outcome"]["status"] == "failed"
    assert card["outcome"]["error"]["type"] == "RuntimeError"
    assert "boom" in card["outcome"]["error"]["message"]


def test_record_operation_disabled_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置 DEEPQUANT_AUDIT_DIR 时不写任何文件（既有行为零变化）。"""
    monkeypatch.delenv(AUDIT_DIR_ENV, raising=False)
    root = tmp_path / "audit"
    with record_operation("backtest.run", operator="ops", basis={"rule": "x"}) as draft:
        assert draft is None
    assert not root.exists()
    assert audit_enabled() is False


def test_record_operation_uses_env_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "audit"
    monkeypatch.setenv(AUDIT_DIR_ENV, str(root))
    with record_operation("dataset.list", operator="ops", basis={"rule": "x"}) as draft:
        assert draft is not None
    assert (root / "index.jsonl").exists()


# --------------------------------------------------------------------------- #
# replay：查审计本身也要留痕
# --------------------------------------------------------------------------- #
def test_replay_records_query_and_returns_cards(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit")
    for i in range(3):
        with record_operation("backtest.run", operator="ops", basis={"i": i}, store=store):
            pass

    cards = replay(store=store, operator="auditor", action="backtest.run", limit=10)
    assert len(cards) == 3

    audit_cards = store.query(action="audit.replay")
    assert len(audit_cards) == 1
    assert audit_cards[0]["operator"] == "auditor"
    assert audit_cards[0]["outcome"]["n_returned"] == 3
    assert audit_cards[0]["basis"]["filters"]["action"] == "backtest.run"


def test_replay_notes_truncation(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit")
    for i in range(4):
        with record_operation("backtest.run", operator="ops", basis={"i": i}, store=store):
            pass

    replay(store=store, operator="auditor", action="backtest.run", limit=2)
    card = store.query(action="audit.replay")[0]
    assert card["outcome"]["n_returned"] == 2
    assert any("上限" in c for c in card["caveats"])


def test_streamlit_free_import_surface() -> None:
    """operations 模块不得依赖 API 层（否则脚本/CLI 无法单独使用审计层）。"""
    import deepquant.audit.operations as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from deepquant.api" not in source
