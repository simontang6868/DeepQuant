"""契约对账：声明↔实现双向核对、lock 版本约束、豁免清单时效。

这里测的是「漂移能不能被抓到」，而不是「正常时能不能通过」。因为正常时通过
几乎不需要测试，而漂移一旦漏检，声明就退化成一份会过时的文档——它继续声称
旧契约，直到有人按它对接才发现对不上。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from deepquant.api.schemas import (
    BacktestCompareRequest,
    BacktestRequest,
    BacktestResponse,
    DatasetListResponse,
)
from deepquant.capabilities.contract import (
    EXEMPT_ROUTES,
    compute_lock,
    default_lock_path,
    format_report,
    load_lock,
    verify_all,
    verify_app,
    verify_lock,
    write_lock,
)
from deepquant.capabilities.spec import CapabilityContractError, CapabilitySpec


def _app():
    from deepquant.api.app import create_app

    return create_app()


def _spec(**overrides) -> CapabilitySpec:
    base = dict(
        cap_id="backtest.run",
        title="单标的回测执行",
        version="1.0.0",
        summary="演示",
        transport="http",
        operation_id="backtest.run",
        method="POST",
        path="/backtests",
        input_model=BacktestRequest,
        output_model=BacktestResponse,
        required_fields=("dataset_symbol", "strategy_id", "strategy_params", "initial_cash"),
        preconditions=("identity", "limit", "state", "field", "operator_grant"),
        risk_tags=("mutating",),
    )
    base.update(overrides)
    return CapabilitySpec(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 真装配：当前仓库必须 PASS
# --------------------------------------------------------------------------- #


def test_real_app_passes_contract(tmp_path: Path) -> None:
    report = verify_all(_app())
    assert report.ok, format_report(report)
    assert report.checked_capabilities == 6


def test_report_is_json_serialisable() -> None:
    payload = verify_all(_app()).to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["ok"] is True
    assert payload["problem_counts"] == {}


def test_exempt_routes_all_exist() -> None:
    """豁免清单必须指向真实存在的路由，否则是"以为豁免了某接口"。"""
    kinds = {p.kind for p in verify_app(_app()).problems}
    assert "stale_exemption" not in kinds


# --------------------------------------------------------------------------- #
# 方向 1：声明 → 实现
# --------------------------------------------------------------------------- #


def test_missing_route_detected() -> None:
    report = verify_app(_app(), capabilities=[_spec(path="/no-such-route")])
    kinds = {p.kind for p in report.problems}
    assert "route_missing" in kinds


def test_input_model_drift_detected() -> None:
    report = verify_app(_app(), capabilities=[_spec(input_model=BacktestCompareRequest)])
    kinds = {p.kind for p in report.problems}
    assert "input_model_drift" in kinds


def test_output_model_drift_detected() -> None:
    report = verify_app(_app(), capabilities=[_spec(output_model=DatasetListResponse)])
    kinds = {p.kind for p in report.problems}
    assert "output_model_drift" in kinds


def test_operation_mapping_drift_detected() -> None:
    """声明与 T1 操作映射不一致会让拒绝事件记到别的能力名下。"""
    report = verify_app(
        _app(),
        capabilities=[_spec(operation_id="backtest.compare", required_fields=("run_ids",))],
    )
    kinds = {p.kind for p in report.problems}
    assert "operation_mapping_drift" in kinds


def test_required_fields_drift_detected() -> None:
    report = verify_app(_app(), capabilities=[_spec(required_fields=("dataset_symbol",))])
    kinds = {p.kind for p in report.problems}
    assert "required_fields_drift" in kinds


def test_service_capability_requires_registered_operation() -> None:
    spec = _spec(
        cap_id="ghost.service",
        transport="service",
        method=None,
        path=None,
        operation_id="ghost.operation",
        input_model=None,
        required_fields=(),
        risk_tags=("read_only",),
    )
    report = verify_app(_app(), capabilities=[spec])
    kinds = {p.kind for p in report.problems}
    assert "operation_unregistered" in kinds


# --------------------------------------------------------------------------- #
# 方向 2：实现 → 声明
# --------------------------------------------------------------------------- #


def test_unregistered_mutating_route_detected() -> None:
    """新增会改变状态的路由却没登记能力——T4 要拦的正是这件事。"""
    app = _app()

    @app.post("/sneaky")
    def sneaky() -> dict[str, str]:  # pragma: no cover - 只为让路由真实存在
        return {"ok": "no"}

    report = verify_app(app)
    kinds = {p.kind for p in report.problems}
    assert "uncovered_route" in kinds
    assert any("sneaky" in p.capability for p in report.problems if p.kind == "uncovered_route")


def test_unregistered_read_route_also_detected() -> None:
    """只读路由也要么登记、要么写进豁免清单——不允许"什么都没说"。"""
    app = _app()

    @app.get("/sneaky-read")
    def sneaky_read() -> dict[str, str]:  # pragma: no cover
        return {"ok": "no"}

    kinds = {p.kind for p in verify_app(app).problems}
    assert "uncovered_route" in kinds


def test_uncovered_core_operation_detected() -> None:
    report = verify_app(_app(), capabilities=[_spec()])
    kinds = {p.kind for p in report.problems}
    assert "uncovered_operation" in kinds


def test_assert_app_contract_raises_with_details() -> None:
    from deepquant.capabilities.contract import assert_app_contract

    with pytest.raises(CapabilityContractError, match="能力契约对账失败"):
        assert_app_contract(_app(), capabilities=[_spec(path="/nope")])


def test_framework_routes_are_ignored() -> None:
    kinds = {p.kind for p in verify_app(_app()).problems}
    assert "uncovered_route" not in kinds


# --------------------------------------------------------------------------- #
# lock：版本约束的牙齿
# --------------------------------------------------------------------------- #


def test_default_lock_path_points_to_backend_contracts() -> None:
    assert default_lock_path().name == "capabilities.lock.json"
    assert default_lock_path().parent.name == "contracts"


def test_initial_lock_records_all_capabilities() -> None:
    lock = load_lock()
    assert len(lock["capabilities"]) == 6
    for entry in lock["capabilities"].values():
        assert entry["current"] == "1.0.0"
        assert entry["history"][0]["digest"]


def test_lock_refuses_digest_change_without_version_bump(tmp_path: Path) -> None:
    """核心约束：同版本必须同摘要。想改契约就得升版本，这一步不能自动化。"""
    path = tmp_path / "lock.json"
    write_lock(path, recorded_at="2026-01-01")

    changed = _spec(required_fields=("dataset_symbol", "strategy_id"))
    with pytest.raises(CapabilityContractError, match="版本号没变"):
        write_lock(path, capabilities=[changed], recorded_at="2026-01-02")


def test_lock_accepts_change_with_version_bump(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, recorded_at="2026-01-01")

    bumped = _spec(version="1.1.0", required_fields=("dataset_symbol", "strategy_id"))
    write_lock(path, capabilities=[bumped], recorded_at="2026-01-02")

    entry = load_lock(path)["capabilities"]["backtest.run"]
    assert entry["current"] == "1.1.0"
    assert [h["version"] for h in entry["history"]] == ["1.0.0", "1.1.0"]


def test_lock_history_keeps_old_digests(tmp_path: Path) -> None:
    """历史不能覆盖：覆盖了就看不出"这份契约什么时候变过"。"""
    path = tmp_path / "lock.json"
    write_lock(path, recorded_at="2026-01-01")
    first = load_lock(path)["capabilities"]["backtest.run"]["history"][0]["digest"]

    write_lock(
        path,
        capabilities=[_spec(version="1.1.0", path="/backtests2")],
        recorded_at="2026-01-02",
    )
    history = load_lock(path)["capabilities"]["backtest.run"]["history"]
    assert history[0]["digest"] == first


def test_lock_rejects_non_increasing_history() -> None:
    previous = {
        "lock_version": 1,
        "capabilities": {
            "backtest.run": {
                "current": "1.0.0",
                "history": [
                    {"version": "1.0.0", "digest": "x"},
                    {"version": "1.0.0", "digest": "x"},
                ],
            }
        },
    }
    with pytest.raises(CapabilityContractError, match="未严格递增"):
        compute_lock(previous=previous)


def test_verify_lock_reports_missing_entry() -> None:
    problems = verify_lock({"lock_version": 1, "capabilities": {}})
    assert {p.kind for p in problems} == {"lock_missing"}


def test_verify_lock_reports_version_mismatch() -> None:
    lock = load_lock()
    lock = json.loads(json.dumps(lock))
    lock["capabilities"]["backtest.run"]["current"] = "9.9.9"
    kinds = {p.kind for p in verify_lock(lock)}
    assert "lock_version_mismatch" in kinds


def test_verify_lock_reports_changed_digest() -> None:
    lock = load_lock()
    lock = json.loads(json.dumps(lock))
    lock["capabilities"]["dataset.list"]["history"][0]["digest"] = "deadbeef"
    kinds = {p.kind for p in verify_lock(lock)}
    assert "contract_changed_without_version_bump" in kinds


def test_verify_lock_reports_stale_entry() -> None:
    lock = load_lock()
    lock = json.loads(json.dumps(lock))
    lock["capabilities"]["ghost.cap"] = {"current": "1.0.0", "history": []}
    kinds = {p.kind for p in verify_lock(lock)}
    assert "lock_stale" in kinds


def test_removed_capability_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, recorded_at="2026-01-01")
    # 升版本才能改契约（同版本换摘要会被工具拒绝，这正是它的职责）
    write_lock(path, capabilities=[_spec(version="1.1.0")], recorded_at="2026-01-02")
    removed = load_lock(path)["removed_capabilities"]
    assert "dataset.list" in removed


def test_lock_file_is_lf(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_lock(path, recorded_at="2026-01-01")
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_load_lock_rejects_wrong_version(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text('{"lock_version": 99, "capabilities": {}}', encoding="utf-8")
    with pytest.raises(CapabilityContractError, match="lock_version"):
        load_lock(path)


def test_verify_all_reads_custom_lock_path(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"lock_version": 1, "capabilities": {}}', encoding="utf-8")
    report = verify_all(_app(), lock_path=empty)
    assert report.ok is False
    assert "lock_missing" in report.by_kind()
