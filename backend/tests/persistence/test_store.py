"""持久化层单元测试。

覆盖：
- schema 初始化幂等（可重复执行）；
- 成功保存后可读取运行元数据与完整结果文档；
- 写入前后 result_to_dict 内容一致；
- result_digest 计算稳定；
- 同一配置两次运行分别保存（不自动去重）；
- 保存失败时事务回滚，不留下伪成功记录；
- 数据库路径不可写时错误可被正确处理；
- source_sha256 / dataset_digest / result_digest 三者语义互不混淆；
- 读取层错误分级：文档缺失返回 None，文档损坏抛 ResultDocumentCorruptedError
  （数据完整性问题），数据库故障抛 PersistenceError（二者明确区分）；
- 数据库文件不会被 Git 跟踪（由 .gitignore 保证，单独在 test_gitignore 验证）。
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
    PersistenceError,
    ResultDocumentCorruptedError,
    connect,
    get_db_path,
    get_run,
    get_run_document,
    get_run_with_document,
    init_schema,
    list_runs,
    result_digest,
    save_backtest_result,
)
from deepquant.strategies.base import Signal, Strategy

# --------------------------------------------------------------------------- #
# 最小假策略 + 确定性行情，构造真实 BacktestResult
# --------------------------------------------------------------------------- #


class _BuyThenSell(Strategy):
    """第 1 天买入、第 4 天卖出，其余持有。"""

    name = "persistence_test_strategy"
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


def _make_result() -> object:
    config = BacktestConfig(symbol="TESTPERSIST", initial_cash=100000.0)
    strategy = _BuyThenSell()
    return run_backtest(_make_frame(), strategy, config)


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    """初始化可重复执行，不报错也不重建表。"""
    db = tmp_path / "test.db"
    conn = connect(db)
    try:
        init_schema(conn)  # 第一次
        init_schema(conn)  # 第二次：幂等
        init_schema(conn)  # 第三次：幂等
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
    finally:
        conn.close()
    assert {"schema_version", "strategy_runs", "run_documents"} <= tables


def test_save_and_read_run_metadata(tmp_path: Path) -> None:
    """成功保存后，可按 run_id 读取运行元数据。"""
    db = tmp_path / "test.db"
    result = _make_result()
    run_id = save_backtest_result(
        db,
        result,
        strategy_id="ma_cross",
        dataset_symbol="TESTPERSIST",
        source_sha256="sha256:deadbeef",
        duration_ms=1234,
    )

    record = get_run(db, run_id)
    assert record is not None
    assert record.run_id == run_id
    assert record.status == "succeeded"
    assert record.symbol == "TESTPERSIST"
    assert record.dataset_symbol == "TESTPERSIST"
    assert record.source_sha256 == "sha256:deadbeef"
    assert record.strategy_id == "ma_cross"
    assert record.initial_cash == 100000.0
    assert record.duration_ms == 1234
    assert record.result_digest is not None
    assert record.result_digest.startswith("sha256:")
    # is_complete 沿用结果自身语义，不因为保存成功而强制为 True
    assert record.is_complete == result.is_complete


def test_save_and_read_full_document(tmp_path: Path) -> None:
    """完整结果 JSON 写入后可以读取，且与 result_to_dict 一致。"""
    db = tmp_path / "test.db"
    result = _make_result()
    expected = result_to_dict(result)

    run_id = save_backtest_result(
        db,
        result,
        strategy_id="ma_cross",
        dataset_symbol="TESTPERSIST",
        source_sha256=None,
        duration_ms=0,
    )

    document = get_run_document(db, run_id)
    assert document is not None
    assert document == expected


def test_result_digest_is_stable() -> None:
    """同一结果文档多次计算摘要必须一致；不同文档摘要不同。"""
    result = _make_result()
    document = result_to_dict(result)
    d1 = result_digest(document)
    d2 = result_digest(document)
    assert d1 == d2
    assert d1.startswith("sha256:")

    document_2 = dict(document)
    document_2["engine_version"] = "different-version"
    d3 = result_digest(document_2)
    assert d3 != d1


def test_result_digest_rejects_non_dict() -> None:
    """非 dict 输入必须抛出 PersistenceError，避免把不稳定表示当摘要。"""
    with pytest.raises(PersistenceError):
        result_digest([1, 2, 3])  # type: ignore[arg-type]


def test_two_runs_same_config_are_separate(tmp_path: Path) -> None:
    """同一配置的两次运行分别保存，run_id 不同，不自动去重。"""
    db = tmp_path / "test.db"
    result = _make_result()

    r1 = save_backtest_result(
        db, result, strategy_id="ma_cross", dataset_symbol="X", source_sha256=None, duration_ms=1
    )
    r2 = save_backtest_result(
        db, result, strategy_id="ma_cross", dataset_symbol="X", source_sha256=None, duration_ms=1
    )

    assert r1 != r2
    runs = list_runs(db)
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {r1, r2}


def test_save_failure_rolls_back(tmp_path: Path) -> None:
    """run_documents 写入失败时整体回滚，不留下标记为成功但无完整结果的记录。"""
    db = tmp_path / "test.db"
    result = _make_result()

    # 预先建表，然后用一个带 CHECK 约束的 run_documents 替换原版：
    # result_digest 永远不可能等于 'impossible'，因此 INSERT run_documents 必然失败。
    # save_backtest_result 内部的 init_schema 使用 IF NOT EXISTS，不会覆盖此表。
    conn = connect(db)
    init_schema(conn)
    conn.execute("DROP TABLE run_documents")
    conn.execute(
        "CREATE TABLE run_documents ("
        "run_id TEXT PRIMARY KEY REFERENCES strategy_runs(run_id) ON DELETE CASCADE, "
        "document_json TEXT NOT NULL, "
        "result_digest TEXT NOT NULL CHECK (result_digest = 'impossible')"
        ")"
    )
    conn.commit()
    conn.close()

    with pytest.raises(PersistenceError):
        save_backtest_result(
            db,
            result,
            strategy_id="ma_cross",
            dataset_symbol="X",
            source_sha256=None,
            duration_ms=1,
        )

    # 回滚后 strategy_runs 不应有残留
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_unwritable_db_path_raises(tmp_path: Path) -> None:
    """数据库路径无效（指向目录）时，错误可被正确处理（PersistenceError，不假装成功）。"""
    # 把一个已存在的目录当作 db 文件路径：sqlite3 无法打开，应包装为 PersistenceError。
    db_as_dir = tmp_path / "is_a_directory"
    db_as_dir.mkdir()
    result = _make_result()

    with pytest.raises(PersistenceError):
        save_backtest_result(
            db_as_dir,
            result,
            strategy_id="ma_cross",
            dataset_symbol="X",
            source_sha256=None,
            duration_ms=1,
        )


def test_three_digests_are_distinct(tmp_path: Path) -> None:
    """source_sha256 / dataset_digest / result_digest 语义互不混淆。"""
    db = tmp_path / "test.db"
    result = _make_result()
    source = "sha256:sourcecsvfilebytes"
    dataset_digest = (result.dataset or {}).get("digest")

    run_id = save_backtest_result(
        db,
        result,
        strategy_id="ma_cross",
        dataset_symbol="X",
        source_sha256=source,
        duration_ms=1,
    )

    record = get_run(db, run_id)
    assert record is not None
    # 三者互不相等
    assert record.source_sha256 != record.dataset_digest
    assert record.source_sha256 != record.result_digest
    assert record.dataset_digest != record.result_digest
    # 各自等于预期
    assert record.source_sha256 == source
    assert record.dataset_digest == dataset_digest


def test_get_db_path_creates_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库目录不存在时可安全创建。"""
    target = tmp_path / "nested" / "deep" / "backtests.db"
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(target))
    path = get_db_path()
    assert path == target.resolve()
    assert path.parent.is_dir()


def test_run_documents_result_digest_matches_strategy_runs(tmp_path: Path) -> None:
    """run_documents.result_digest 与 strategy_runs.result_digest 必须一致。"""
    db = tmp_path / "test.db"
    result = _make_result()
    run_id = save_backtest_result(
        db, result, strategy_id="s", dataset_symbol="X", source_sha256=None, duration_ms=1
    )

    conn = connect(db)
    try:
        sr = conn.execute(
            "SELECT result_digest FROM strategy_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        rd = conn.execute(
            "SELECT result_digest FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()

    assert sr is not None and rd is not None
    assert sr["result_digest"] == rd["result_digest"]
    expected = result_digest(result_to_dict(result))
    assert sr["result_digest"] == expected


# --------------------------------------------------------------------------- #
# 读取层错误分级：文档缺失 / 文档损坏 / 数据库故障
# --------------------------------------------------------------------------- #


def _save_one(db: Path) -> str:
    return save_backtest_result(
        db,
        _make_result(),
        strategy_id="ma_cross",
        dataset_symbol="TESTPERSIST",
        source_sha256="sha256:abc",
        duration_ms=1,
    )


def _set_document_json(db: Path, run_id: str, text: str) -> None:
    conn = connect(db)
    try:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?", (text, run_id)
        )
        conn.commit()
    finally:
        conn.close()


def test_get_run_document_returns_none_when_document_missing(tmp_path: Path) -> None:
    """文档行不存在 → 返回 None（表示缺失，不抛异常）。"""
    db = tmp_path / "test.db"
    run_id = _save_one(db)
    conn = connect(db)
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    assert get_run_document(db, run_id) is None
    # 未知 run_id 同样返回 None
    assert get_run_document(db, "does-not-exist") is None


def test_get_run_document_raises_corrupted_for_invalid_json(tmp_path: Path) -> None:
    """文档存在但 JSON 非法 → ResultDocumentCorruptedError（不是通用 PersistenceError）。"""
    db = tmp_path / "test.db"
    run_id = _save_one(db)
    _set_document_json(db, run_id, "{this is not valid json")

    with pytest.raises(ResultDocumentCorruptedError):
        get_run_document(db, run_id)


def test_get_run_document_raises_corrupted_for_non_object(tmp_path: Path) -> None:
    """文档 JSON 合法但顶层不是对象（如数组 / 标量）→ 视为结构损坏。"""
    db = tmp_path / "test.db"
    run_id = _save_one(db)
    _set_document_json(db, run_id, json.dumps([1, 2, 3]))

    with pytest.raises(ResultDocumentCorruptedError):
        get_run_document(db, run_id)


def test_corrupted_error_is_persistence_error_subclass(tmp_path: Path) -> None:
    """损坏异常是 PersistenceError 的子类，但类型可区分；消息不含绝对路径。"""
    db = tmp_path / "test.db"
    run_id = _save_one(db)
    _set_document_json(db, run_id, "{bad json")

    try:
        get_run_document(db, run_id)
        raise AssertionError("应当抛出 ResultDocumentCorruptedError")
    except ResultDocumentCorruptedError as exc:
        assert isinstance(exc, PersistenceError)
        assert str(db) not in str(exc)
        assert str(tmp_path) not in str(exc)


def test_get_run_with_document_raises_corrupted(tmp_path: Path) -> None:
    """get_run_with_document 遇到损坏文档同样抛 ResultDocumentCorruptedError。"""
    db = tmp_path / "test.db"
    run_id = _save_one(db)
    _set_document_json(db, run_id, "{broken")

    with pytest.raises(ResultDocumentCorruptedError):
        get_run_with_document(db, run_id)


def test_db_error_raises_persistence_error_not_corrupted(tmp_path: Path) -> None:
    """数据库路径无效（指向目录）→ PersistenceError，不是文档损坏异常。"""
    db_as_dir = tmp_path / "is_a_directory"
    db_as_dir.mkdir()

    with pytest.raises(PersistenceError) as info:
        get_run_document(db_as_dir, "any-run-id")
    assert not isinstance(info.value, ResultDocumentCorruptedError)

