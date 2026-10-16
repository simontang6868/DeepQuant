"""只读完整性审计单元测试。

覆盖：
- 完整且未修改的结果通过校验（passed）；
- 结果文档内容变化 → 检测到摘要不一致（failed）；
- 结果文档缺失 → 明确报告（failed，非「无法验证」）；
- 元数据缺失 / run 不存在 → 返回 None（由 API 层映射为 404）；
- 可选字段缺失不会误报为严重损坏；
- 无法执行校验（摘要缺失）时不会错误返回 passed（incomplete）；
- 数据库访问错误与数据完整性错误区分（前者抛 PersistenceError）；
- 审计严格只读：前后历史记录数量、内容、摘要均不变。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.performance.serialize import result_to_dict
from deepquant.persistence import (
    INTEGRITY_FAILED,
    INTEGRITY_INCOMPLETE,
    INTEGRITY_PASSED,
    PersistenceError,
    audit_run_integrity,
    connect,
    get_run,
    list_runs,
    result_digest,
    save_backtest_result,
)
from deepquant.strategies.base import Signal, Strategy


class _BuyThenSell(Strategy):
    name = "integrity_test_strategy"
    version = "1.0.0"

    @property
    def params(self) -> dict:
        return {"window": 3}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        series = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        if len(series) >= 1:
            series.iloc[0] = int(Signal.BUY)
        if len(series) >= 4:
            series.iloc[3] = int(Signal.SELL)
        return series


def _make_frame() -> pd.DataFrame:
    dates = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    )
    closes = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0]
    opens = [10.0, 10.5, 11.5, 12.0, 11.5, 13.5]
    highs = [max(o, c) for o, c in zip(opens, closes)]
    lows = [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1000] * 6},
        index=dates,
    )


def _make_result():
    config = BacktestConfig(symbol="TESTINTEG", initial_cash=100000.0)
    return run_backtest(_make_frame(), _BuyThenSell(), config)


def _save(db: Path) -> str:
    return save_backtest_result(
        db,
        _make_result(),
        strategy_id="ma_cross",
        dataset_symbol="TESTINTEG",
        source_sha256=None,
        duration_ms=1,
    )


def _check(report, name: str):
    return next(c for c in report.checks if c.name == name)


# --------------------------------------------------------------------------- #
# 正常 / 一致
# --------------------------------------------------------------------------- #


def test_audit_passes_for_fresh_record(tmp_path: Path) -> None:
    """完整且未修改的记录通过校验。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_PASSED
    assert all(c.status == INTEGRITY_PASSED for c in report.checks)
    # 重新计算的摘要与保存的摘要一致
    assert report.recomputed_digest == report.document_result_digest
    assert report.metadata_result_digest == report.document_result_digest
    assert report.audited_at  # 含审计时间


def test_audit_detects_modified_document(tmp_path: Path) -> None:
    """结果文档内容被修改 → 检测到摘要不一致（failed）。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT document_json FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
        document = json.loads(row["document_json"])
        document["engine_version"] = "tampered-version"
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            (json.dumps(document, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_FAILED
    assert _check(report, "result_digest_consistent").status == INTEGRITY_FAILED


def test_audit_detects_unparseable_document(tmp_path: Path) -> None:
    """结果文档 JSON 损坏 → 明确报告 failed（不是「无法验证」，也不是通过）。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            ("{not valid json", run_id),
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_FAILED
    assert _check(report, "document_json_parseable").status == INTEGRITY_FAILED


def test_audit_reports_missing_document(tmp_path: Path) -> None:
    """元数据存在但结果文档缺失 → failed（document_present），非「无法验证」。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_FAILED
    assert _check(report, "document_present").status == INTEGRITY_FAILED


def test_audit_returns_none_for_unknown_run(tmp_path: Path) -> None:
    """run 不存在 → 返回 None（由 API 层映射为 404）。"""
    db = tmp_path / "test.db"
    assert audit_run_integrity(db, "does-not-exist") is None


# --------------------------------------------------------------------------- #
# 缺失信息与兼容性
# --------------------------------------------------------------------------- #


def test_audit_optional_field_missing_is_not_damage(tmp_path: Path) -> None:
    """缺少可选字段（如 performance / strategy）不应误报为严重损坏。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT document_json FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
        document = json.loads(row["document_json"])
        # 移除可选字段，并同步更新摘要（模拟缺少可选字段的历史记录）
        document.pop("performance", None)
        document.pop("strategy", None)
        new_digest = result_digest(document)
        conn.execute(
            "UPDATE run_documents SET document_json = ?, result_digest = ? WHERE run_id = ?",
            (json.dumps(document, ensure_ascii=False), new_digest, run_id),
        )
        conn.execute(
            "UPDATE strategy_runs SET result_digest = ? WHERE run_id = ?", (new_digest, run_id)
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    # 必需字段齐全 + 摘要一致 → 通过；缺少可选字段不构成 failed
    assert report.status == INTEGRITY_PASSED
    assert _check(report, "required_fields_present").status == INTEGRITY_PASSED


def test_audit_missing_digest_is_incomplete_not_passed(tmp_path: Path) -> None:
    """摘要缺失（无法比对）→ incomplete，绝不返回 passed。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        conn.execute(
            "UPDATE strategy_runs SET result_digest = NULL WHERE run_id = ?", (run_id,)
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_INCOMPLETE
    assert _check(report, "digests_match_across_tables").status == INTEGRITY_INCOMPLETE


def test_audit_required_field_missing_is_failed(tmp_path: Path) -> None:
    """缺少必需字段（schema_version/engine_version/config）→ failed。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT document_json FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
        document = json.loads(row["document_json"])
        document.pop("config", None)
        new_digest = result_digest(document)
        conn.execute(
            "UPDATE run_documents SET document_json = ?, result_digest = ? WHERE run_id = ?",
            (json.dumps(document, ensure_ascii=False), new_digest, run_id),
        )
        conn.execute(
            "UPDATE strategy_runs SET result_digest = ? WHERE run_id = ?", (new_digest, run_id)
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == INTEGRITY_FAILED
    assert _check(report, "required_fields_present").status == INTEGRITY_FAILED


# --------------------------------------------------------------------------- #
# 错误分类
# --------------------------------------------------------------------------- #


def test_audit_db_error_raises_persistence_error(tmp_path: Path) -> None:
    """数据库访问错误 → PersistenceError（与数据完整性问题区分）。"""
    db_as_dir = tmp_path / "is_a_directory"
    db_as_dir.mkdir()
    with pytest.raises(PersistenceError):
        audit_run_integrity(db_as_dir, "any-run-id")


# --------------------------------------------------------------------------- #
# 只读性
# --------------------------------------------------------------------------- #


def test_audit_is_read_only(tmp_path: Path) -> None:
    """审计前后历史记录数量、内容与 result_digest 均不变，且不新增记录。"""
    db = tmp_path / "test.db"
    run_id = _save(db)
    before_runs = list_runs(db)
    before_record = get_run(db, run_id)

    report = audit_run_integrity(db, run_id)
    assert report is not None

    after_runs = list_runs(db)
    after_record = get_run(db, run_id)
    assert len(after_runs) == len(before_runs)
    assert {r.run_id for r in after_runs} == {r.run_id for r in before_runs}
    assert after_record is not None and before_record is not None
    assert after_record.result_digest == before_record.result_digest
