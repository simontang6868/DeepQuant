"""只读复现信息清单单元测试。

覆盖：
- 正常记录：清单包含身份 / 数据 / 策略 / 执行 / 引擎等项；
- 原始数据指纹（source_sha256）与处理后数据摘要（dataset_digest）被正确区分；
- 指纹存在但原始文件未归档 → 明确标注（not_archived / not_verifiable），不推断为可复现；
- 策略代码版本、历史运行环境 → 明确标注 not_recorded；
- 旧记录缺失可选字段 → 如实标记 missing，不伪造默认值；
- run 不存在 → 返回 None；
- 数据库访问错误 → 抛 PersistenceError（与「字段缺失」区分）；
- 结果文档损坏 / 缺失 → 转为状态报告（不抛异常）；
- 严格只读：前后历史记录数量、内容、摘要均不变。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.persistence import (
    DOC_CORRUPTED,
    DOC_MISSING,
    DOC_OK,
    ITEM_MISSING,
    ITEM_NOT_ARCHIVED,
    ITEM_NOT_RECORDED,
    ITEM_NOT_VERIFIABLE,
    LEVEL_NONE,
    LEVEL_PARTIAL,
    PersistenceError,
    build_reproducibility_checklist,
    connect,
    get_run,
    list_runs,
    save_backtest_result,
)
from deepquant.strategies.base import Signal, Strategy


class _BuyThenSell(Strategy):
    name = "repro_test_strategy"
    version = "2.1.0"

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
    config = BacktestConfig(symbol="REPROTEST", initial_cash=100000.0)
    return run_backtest(_make_frame(), _BuyThenSell(), config)


def _save(db: Path, *, source_sha256: str | None = "sha256:sourcefiledigest") -> str:
    return save_backtest_result(
        db,
        _make_result(),
        strategy_id="ma_cross",
        dataset_symbol="REPROTEST",
        source_sha256=source_sha256,
        duration_ms=1,
    )


def _item(checklist, key: str):
    return next(i for i in checklist.items if i.key == key)


def _level(checklist, name: str):
    return next(lv for lv in checklist.levels if lv.level == name)


# --------------------------------------------------------------------------- #
# 正常记录
# --------------------------------------------------------------------------- #


def test_checklist_for_fresh_record(tmp_path: Path) -> None:
    """正常记录：文档可读，关键信息项状态正确。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert checklist.run_id == run_id
    assert checklist.result_document_state == DOC_OK
    assert checklist.generated_at

    # 已保存的身份 / 资金 / 引擎信息
    assert _item(checklist, "run_id").status == "available"
    assert _item(checklist, "symbol").status == "available"
    assert _item(checklist, "period").status == "available"
    assert _item(checklist, "initial_cash").status == "available"
    assert _item(checklist, "strategy_id").status == "available"
    assert _item(checklist, "strategy_version").status == "available"
    assert _item(checklist, "engine_version").status == "available"
    assert _item(checklist, "schema_version").status == "available"


def test_source_sha256_and_dataset_digest_are_distinguished(tmp_path: Path) -> None:
    """原始数据指纹与处理后数据摘要被区分为两个独立项，且不属于可验证。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    source = _item(checklist, "source_sha256")
    dataset = _item(checklist, "dataset_digest")
    # 两个不同项，不能混为一谈
    assert source.key != dataset.key
    assert source.value is not None and dataset.value is not None
    assert source.value != dataset.value
    # 指纹存在但原始文件未归档 → 无法验证（不因存在就判为 verified）
    assert source.status == ITEM_NOT_VERIFIABLE
    assert dataset.status == ITEM_NOT_VERIFIABLE


def test_raw_file_not_archived_is_explicit(tmp_path: Path) -> None:
    """原始行情文件未归档：即使指纹存在，也明确标注 not_archived。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert _item(checklist, "raw_market_file").status == ITEM_NOT_ARCHIVED


def test_strategy_code_and_runtime_environment_not_recorded(tmp_path: Path) -> None:
    """策略代码版本与历史运行环境未记录 → 明确标注 not_recorded。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert _item(checklist, "strategy_code_version").status == ITEM_NOT_RECORDED
    assert _item(checklist, "runtime_environment").status == ITEM_NOT_RECORDED


def test_levels_are_partial_not_full(tmp_path: Path) -> None:
    """三级复现能力均不得判为 full（缺关键证据）。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    for lv in checklist.levels:
        assert lv.status in (LEVEL_PARTIAL, LEVEL_NONE)
    # 数据 / 代码 / 环境均有缺失材料
    for name in ("data", "code", "environment"):
        assert _level(checklist, name).missing_materials


def test_conclusion_does_not_claim_full_reproducibility(tmp_path: Path) -> None:
    """结论不得宣称完全可复现，且关键缺失信息非空。"""
    db = tmp_path / "test.db"
    run_id = _save(db)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert "受限" in checklist.conclusion
    assert checklist.missing_info  # 至少列出原始行情文件 / 策略代码 / 运行环境


# --------------------------------------------------------------------------- #
# 旧记录 / 字段缺失
# --------------------------------------------------------------------------- #


def test_old_record_missing_optional_fields_marked_missing(tmp_path: Path) -> None:
    """旧记录缺少指纹 / 摘要 / 版本 → 标记为 missing，不伪造默认值。"""
    db = tmp_path / "test.db"
    run_id = _save(db, source_sha256="sha256:abc")

    conn = connect(db)
    try:
        conn.execute(
            "UPDATE strategy_runs SET source_sha256 = NULL, dataset_digest = NULL, "
            "strategy_version = NULL, engine_version = NULL, schema_version = NULL "
            "WHERE run_id = ?",
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert _item(checklist, "source_sha256").status == ITEM_MISSING
    assert _item(checklist, "dataset_digest").status == ITEM_MISSING
    assert _item(checklist, "strategy_version").status == ITEM_MISSING
    assert _item(checklist, "engine_version").status == ITEM_MISSING
    assert _item(checklist, "schema_version").status == ITEM_MISSING
    # 缺失字段值必须为 None，绝不用默认值冒充
    assert _item(checklist, "source_sha256").value is None
    # 数据级没有任何摘要 → 不具备
    assert _level(checklist, "data").status == LEVEL_NONE


def test_record_without_source_sha256_is_not_verifiable(tmp_path: Path) -> None:
    """无原始指纹时该项为 missing，而不是「无法验证」。"""
    db = tmp_path / "test.db"
    run_id = _save(db, source_sha256=None)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert _item(checklist, "source_sha256").status == ITEM_MISSING


# --------------------------------------------------------------------------- #
# 文档缺失 / 损坏
# --------------------------------------------------------------------------- #


def test_document_missing_reported_as_state(tmp_path: Path) -> None:
    """结果文档缺失 → 状态 missing，不抛异常，仅依赖元数据。"""
    db = tmp_path / "test.db"
    run_id = _save(db)
    conn = connect(db)
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert checklist.result_document_state == DOC_MISSING
    # 依赖文档的项（如执行规则）应为 missing
    assert _item(checklist, "execution_rules").status == ITEM_MISSING
    # 依赖元数据的项仍可用
    assert _item(checklist, "run_id").status == "available"


def test_document_corrupted_reported_as_state(tmp_path: Path) -> None:
    """结果文档损坏 → 状态 corrupted，不抛异常。"""
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

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None
    assert checklist.result_document_state == DOC_CORRUPTED
    assert _item(checklist, "data_processing_rules").status == ITEM_MISSING


# --------------------------------------------------------------------------- #
# 错误分类 / 只读性
# --------------------------------------------------------------------------- #


def test_unknown_run_returns_none(tmp_path: Path) -> None:
    """run 不存在 → 返回 None（由 API 层映射为 404）。"""
    db = tmp_path / "test.db"
    assert build_reproducibility_checklist(db, "does-not-exist") is None


def test_db_error_raises_persistence_error(tmp_path: Path) -> None:
    """数据库访问错误 → PersistenceError（与「字段缺失」区分）。"""
    db_as_dir = tmp_path / "is_a_directory"
    db_as_dir.mkdir()
    with pytest.raises(PersistenceError):
        build_reproducibility_checklist(db_as_dir, "any-run-id")


def test_checklist_is_read_only(tmp_path: Path) -> None:
    """生成清单前后历史记录数量、内容、摘要均不变，且不新增记录。"""
    db = tmp_path / "test.db"
    run_id = _save(db)
    before_runs = list_runs(db)
    before_record = get_run(db, run_id)

    checklist = build_reproducibility_checklist(db, run_id)
    assert checklist is not None

    after_runs = list_runs(db)
    after_record = get_run(db, run_id)
    assert len(after_runs) == len(before_runs)
    assert {r.run_id for r in after_runs} == {r.run_id for r in before_runs}
    assert after_record is not None and before_record is not None
    assert after_record.result_digest == before_record.result_digest
