"""store.py 边界 / 错误契约测试（补齐 A16#1 本地 89% 缺口，本地不提 PR）。

聚焦既有 ``test_store.py`` / ``test_store_contracts.py`` 未覆盖的**错误与边界分支**，
全部为语义断言（不依赖 pandas / run_backtest，避免触发 numpy 双重加载）：

1. 连接或初始化失败（``connect`` 抛 ``sqlite3.Error``）统一包装为 ``PersistenceError``，
   不向调用方泄漏底层 sqlite 细节（覆盖 ``_open_conn`` 的守卫）；
2. 各读 / 写 / 列举 / 审计函数在**已连接但执行失败**（``conn.execute`` 抛 ``sqlite3.Error``）
   时，同样映射为 ``PersistenceError``（覆盖各函数的 ``except sqlite3.Error`` 分支）；
3. ``_row_to_record`` 对损坏的 ``strategy_params_json`` 降级为 ``None``（历史数据容错，不抛）；
4. 完整性审计 ``audit_run_integrity`` 的三态语义：
   - 文档为合法 JSON 但非对象 → ``document_json_parseable`` 明确 ``failed``；
   - 文档摘要缺失 / 元数据或文档摘要缺失 → ``incomplete``（不是 ``passed``，也不是 ``failed``）；
   - 元数据摘要与文档摘要不一致 → ``failed``；
   - 结果文档缺少必需字段 → ``failed``。

只新增测试，不改 ``store.py`` 一行。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import deepquant.persistence.store as store_mod
from deepquant.persistence import (
    PersistenceError,
    audit_run_integrity,
    count_runs,
    get_run,
    get_run_document,
    get_run_with_document,
    list_runs,
    save_backtest_result,
)
from deepquant.persistence.connection import connect, init_schema


# --------------------------------------------------------------------------- #
# 失败连接 / 失败执行 的桩
# --------------------------------------------------------------------------- #


class _FailingConn:
    """连接成功但任何执行都抛 sqlite3.Error 的桩，用于覆盖 ``except sqlite3.Error``。"""

    def execute(self, *args, **kwargs):  # noqa: D401
        raise sqlite3.Error("injected sqlite failure")

    def commit(self):
        raise sqlite3.Error("injected sqlite failure")

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _failing_connect(*args, **kwargs) -> _FailingConn:
    return _FailingConn()


def _patch_open_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 ``_open_conn`` 连接成功但执行失败：connect 返回桩、init_schema 变空操作。"""
    monkeypatch.setattr(store_mod, "connect", _failing_connect)
    monkeypatch.setattr(store_mod, "init_schema", lambda conn: None)


def _patch_connect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 ``connect`` 直接抛 sqlite3.Error：覆盖 ``_open_conn`` 自身的守卫（147）。"""
    monkeypatch.setattr(store_mod, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.Error("connect failed")))


# --------------------------------------------------------------------------- #
# 直接构造 SQLite 行（不依赖 pandas / run_backtest）
# --------------------------------------------------------------------------- #


def _seed(
    db: Path,
    *,
    doc_json: str | None = "{}",
    doc_digest: str | None = "sha256:doc",
    meta_digest: str = "sha256:meta",
    params_json: str = '"{}"',
) -> str:
    """用真实 sqlite 建表并插入一条最小可解析的运行记录 + 结果文档。"""
    conn = connect(db)
    try:
        init_schema(conn)
        run = {
            "run_id": "R1",
            "created_at": "2024-01-01T00:00:00.000000Z",
            "status": "succeeded",
            "symbol": "X",
            "strategy_id": "s",
            "strategy_version": "1",
            "strategy_params_json": params_json,
            "dataset_symbol": "X",
            "dataset_digest": "sha256:ds",
            "source_sha256": None,
            "start_date": None,
            "end_date": None,
            "initial_cash": 100000.0,
            "config_json": "{}",
            "engine_version": "1",
            "schema_version": "1",
            "is_complete": 1,
            "incomplete_reasons_json": "[]",
            "duration_ms": 1,
            "result_digest": meta_digest,
            "error_code": None,
            "error_message": None,
        }
        cols = list(run.keys())
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO strategy_runs ({', '.join(cols)}) VALUES ({placeholders})",
            [run[c] for c in cols],
        )
        if doc_json is not None:
            conn.execute(
                "INSERT INTO run_documents (run_id, document_json, result_digest) "
                "VALUES (?, ?, ?)",
                ("R1", doc_json, doc_digest),
            )
        conn.commit()
    finally:
        conn.close()
    return "R1"


# --------------------------------------------------------------------------- #
# 1. 连接失败 → PersistenceError（守卫 + 各函数 except PersistenceError: raise）
# --------------------------------------------------------------------------- #


def test_get_run_connect_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connect 抛 sqlite3.Error → 必须包装为 PersistenceError（不泄漏底层细节）。"""
    _patch_connect_raises(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        get_run(db, "R1")


# --------------------------------------------------------------------------- #
# 2. 已连接但执行失败 → PersistenceError（各函数的 except sqlite3.Error 分支）
# --------------------------------------------------------------------------- #


def test_get_run_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        get_run(db, "R1")


def test_get_run_document_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        get_run_document(db, "R1")


def test_list_runs_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        list_runs(db)


def test_count_runs_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        count_runs(db)


def test_get_run_with_document_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        get_run_with_document(db, "R1")


def test_audit_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_open_conn(monkeypatch)
    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        audit_run_integrity(db, "R1")


def test_save_execute_failure_maps_to_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存时底层 INSERT 抛 sqlite3.Error → 必须包装为 PersistenceError（不伪装成功）。

    通过 monkeypatch 序列化入口与一个最小结果的桩，避免引入 pandas / BacktestResult。
    """

    class _FakeResult:
        config = SimpleNamespace(symbol="X", initial_cash=1.0)
        strategy: dict = {}
        dataset: dict = {}
        period_start = None
        period_end = None
        engine_version = "1"
        schema_version = "1"
        is_complete = True
        incomplete_reasons: list = []

    monkeypatch.setattr(store_mod, "result_to_dict", lambda r: {})
    monkeypatch.setattr(store_mod, "result_digest", lambda d: "sha256:x")
    monkeypatch.setattr(store_mod, "config_summary", lambda c: {})
    _patch_open_conn(monkeypatch)

    db = tmp_path / "x.db"
    with pytest.raises(PersistenceError):
        save_backtest_result(
            db,
            _FakeResult(),  # type: ignore[arg-type]
            strategy_id="s",
            dataset_symbol="X",
            source_sha256=None,
            duration_ms=1,
        )


# --------------------------------------------------------------------------- #
# 3. _row_to_record 对损坏 params 降级（历史数据容错）
# --------------------------------------------------------------------------- #


def test_row_to_record_corrupt_params_downgrades_to_none(tmp_path: Path) -> None:
    """strategy_params_json 非法 JSON 时，策略参数降级为 None，记录仍可读回。"""
    db = tmp_path / "x.db"
    _seed(db, params_json="this-is-not-json")

    record = get_run(db, "R1")
    assert record is not None
    assert record.strategy_params is None


# --------------------------------------------------------------------------- #
# 4. 审计三态语义（failed / incomplete 必须明确，不得把「无法验证」当通过）
# --------------------------------------------------------------------------- #


def test_audit_document_valid_json_non_object_is_failed(tmp_path: Path) -> None:
    """文档是合法 JSON 但不是对象（如整数 ``123``）→ document_json_parseable 明确 failed。"""
    db = tmp_path / "x.db"
    _seed(db, doc_json="123")

    report = audit_run_integrity(db, "R1")
    assert report is not None
    assert report.status == "failed"
    failed = {c.name: c.status for c in report.checks}
    assert failed["document_json_parseable"] == "failed"


def test_audit_meta_digest_missing_is_incomplete(tmp_path: Path) -> None:
    """元数据摘要缺失 → digests_match_across_tables 为 incomplete。"""
    db = tmp_path / "x.db"
    _seed(db, meta_digest=None)

    report = audit_run_integrity(db, "R1")
    assert report is not None
    status_by_name = {c.name: c.status for c in report.checks}
    assert status_by_name["digests_match_across_tables"] == "incomplete"


def test_audit_digests_mismatch_is_failed(tmp_path: Path) -> None:
    """元数据摘要与文档摘要不一致（二者均存在）→ 明确 failed。"""
    db = tmp_path / "x.db"
    # 文档为 "{}"，重算摘要与 "sha256:doc" 不符；元数据 "sha256:meta" 与文档也不符
    _seed(db, doc_json="{}", doc_digest="sha256:doc", meta_digest="sha256:meta")

    report = audit_run_integrity(db, "R1")
    assert report is not None
    status_by_name = {c.name: c.status for c in report.checks}
    assert status_by_name["result_digest_consistent"] == "failed"
    assert status_by_name["digests_match_across_tables"] == "failed"
    assert report.status == "failed"


def test_audit_required_fields_missing_is_failed(tmp_path: Path) -> None:
    """结果文档缺少必需字段（schema_version/engine_version/config）→ 明确 failed。"""
    db = tmp_path / "x.db"
    _seed(db, doc_json=json.dumps({"foo": "bar"}))

    report = audit_run_integrity(db, "R1")
    assert report is not None
    status_by_name = {c.name: c.status for c in report.checks}
    assert status_by_name["required_fields_present"] == "failed"
    assert report.status == "failed"
