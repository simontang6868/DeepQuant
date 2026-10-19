"""store.py 契约与不变量测试（A16#1）。

聚焦「回测结果持久化：错了会丢结果或错配」这一风险点，补充既有 ``test_store.py``
未覆盖的**契约级**不变量（不重复其已覆盖的分支）：

1. 保存→读取的完整往返一致性，含 ``get_run_with_document``；
2. 完整性审计 ``audit_run_integrity``：
   - 干净保存后给出 ``passed``，且三个摘要一致；
   - **文档被静默篡改后必须给出 ``failed``**（不能把损坏/错配当有效返回）——这是「错了必须被发现」的红线；
3. ``list_runs`` 的分页 / 过滤 / 排序契约与参数守卫（limit>=1 / offset>=0）；
4. ``result_digest`` 对键序无关、对内容敏感（防止错配）；
5. 未知 run_id 的三类读取均返回 ``None``（不抛、不伪装）。

只新增测试，不改 ``store.py`` 一行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.persistence import (
    PersistenceError,
    ResultDocumentCorruptedError,
    audit_run_integrity,
    connect,
    count_runs,
    get_run,
    get_run_document,
    get_run_with_document,
    list_runs,
    result_digest,
    save_backtest_result,
)
from deepquant.strategies.base import Signal, Strategy


class _BuyThenSell(Strategy):
    """第 1 天买入、第 4 天卖出，其余持有（确定性，便于可复现往返）。"""

    name = "contract_strategy"
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


def _make_result(symbol: str = "CONTRACT") -> object:
    config = BacktestConfig(symbol=symbol, initial_cash=100000.0)
    return run_backtest(_make_frame(), _BuyThenSell(), config)


def _save(db: Path, *, symbol: str = "CONTRACT", source_sha256=None) -> str:
    return save_backtest_result(
        db,
        _make_result(symbol),
        strategy_id="ma_cross",
        dataset_symbol=symbol,
        source_sha256=source_sha256,
        duration_ms=1,
    )


# --------------------------------------------------------------------------- #
# 1. 完整往返一致性
# --------------------------------------------------------------------------- #


def test_round_trip_metadata_and_document_consistent(tmp_path: Path) -> None:
    """保存后，元数据与完整文档都可读回，且摘要一致。"""
    db = tmp_path / "t.db"
    run_id = _save(db, source_sha256="sha256:srcbytes")

    record = get_run(db, run_id)
    assert record is not None
    assert record.symbol == "CONTRACT"
    assert record.strategy_id == "ma_cross"
    assert record.source_sha256 == "sha256:srcbytes"
    assert record.result_digest.startswith("sha256:")

    pair = get_run_with_document(db, run_id)
    assert pair is not None
    rec, doc = pair
    assert rec.run_id == run_id
    assert isinstance(doc, dict)
    assert doc["config"]["symbol"] == "CONTRACT"
    # 文档落盘摘要 == 元数据摘要（不得错配）
    assert rec.result_digest == record.result_digest


# --------------------------------------------------------------------------- #
# 2. 完整性审计：干净 → passed；篡改 → failed（红线）
# --------------------------------------------------------------------------- #


def test_audit_passes_on_clean_save(tmp_path: Path) -> None:
    """干净保存后审计必须为 passed，且三类摘要一致。"""
    db = tmp_path / "t.db"
    run_id = _save(db)

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == "passed"
    assert {c.name for c in report.checks} >= {
        "document_present",
        "document_json_parseable",
        "result_digest_consistent",
        "digests_match_across_tables",
        "required_fields_present",
    }
    assert all(c.status == "passed" for c in report.checks)
    # 三处摘要完全一致
    assert report.recomputed_digest == report.metadata_result_digest == report.document_result_digest


def test_audit_detects_tampered_document(tmp_path: Path) -> None:
    """文档被静默篡改（数值改变、摘要未同步）→ 审计必须给出 failed，不能当有效返回。

    这是「错了会丢结果或错配 → 必须被发现」的核心不变量。
    """
    db = tmp_path / "t.db"
    run_id = _save(db)

    # 读取已保存文档，篡改一个数值，写回，但**不动** result_digest 列
    doc = get_run_document(db, run_id)
    assert doc is not None
    original_cash = doc["config"]["initial_cash"]
    doc["config"]["initial_cash"] = original_cash + 1.0

    conn = connect(db)
    try:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            (json.dumps(doc, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    # 必须发现，不能假装通过
    assert report.status == "failed"
    failed = {c.name for c in report.checks if c.status == "failed"}
    assert "result_digest_consistent" in failed
    # 读取层本身不校验：仍返回被篡改内容，校验职责在 audit
    assert get_run_document(db, run_id)["config"]["initial_cash"] == original_cash + 1.0


def test_audit_missing_document_reports_failed(tmp_path: Path) -> None:
    """元数据存在但文档缺失 → 明确完整性问题（failed），不是「无法验证」。"""
    db = tmp_path / "t.db"
    run_id = _save(db)
    conn = connect(db)
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    report = audit_run_integrity(db, run_id)
    assert report is not None
    assert report.status == "failed"
    assert any(c.name == "document_present" and c.status == "failed" for c in report.checks)


# --------------------------------------------------------------------------- #
# 3. list_runs 契约与参数守卫
# --------------------------------------------------------------------------- #


def test_list_runs_pagination_and_union(tmp_path: Path) -> None:
    """分页不重不漏：两页并集 == 全部 run_id。"""
    db = tmp_path / "t.db"
    rids = {_save(db, symbol=f"S{i}") for i in range(3)}

    page1 = list_runs(db, limit=2, offset=0)
    page2 = list_runs(db, limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 1
    assert {r.run_id for r in page1} | {r.run_id for r in page2} == rids


def test_list_runs_filter_by_symbol(tmp_path: Path) -> None:
    """symbol 过滤只返回匹配项；无匹配返回空。"""
    db = tmp_path / "t.db"
    a = _save(db, symbol="AAA")
    _save(db, symbol="BBB")
    assert [r.run_id for r in list_runs(db, symbol="AAA")] == [a]
    assert list_runs(db, symbol="ZZZ") == []


def test_list_runs_count_matches_list(tmp_path: Path) -> None:
    """count_runs 与 list_runs 在同过滤条件下数量一致。"""
    db = tmp_path / "t.db"
    _save(db, symbol="AAA")
    _save(db, symbol="AAA")
    _save(db, symbol="BBB")
    assert count_runs(db, symbol="AAA") == 2
    assert len(list_runs(db, symbol="AAA")) == 2


def test_list_runs_rejects_bad_pagination(tmp_path: Path) -> None:
    """参数守卫：limit<1 / offset<0 必须显式报错，不接受非法分页。"""
    db = tmp_path / "t.db"
    _save(db)
    with pytest.raises(ValueError):
        list_runs(db, limit=0)
    with pytest.raises(ValueError):
        list_runs(db, offset=-1)


# --------------------------------------------------------------------------- #
# 4. result_digest 契约：键序无关、内容敏感（防错配）
# --------------------------------------------------------------------------- #


def test_result_digest_key_order_independent() -> None:
    """规范化用 sort_keys，故键顺序不同不应改变摘要（防止「同内容不同摘要」错配）。"""
    d1 = {"a": 1, "b": {"c": 2}}
    d2 = {"b": {"c": 2}, "a": 1}
    assert result_digest(d1) == result_digest(d2)


def test_result_digest_content_sensitive() -> None:
    """内容变化必须改变摘要。"""
    assert result_digest({"v": 1.0}) != result_digest({"v": 2.0})


def test_result_digest_utf8_stable() -> None:
    """含中文键值的文档也能稳定摘要且带 sha256: 前缀。"""
    d = {"标的": "平安银行", "收益": 0.01}
    dg = result_digest(d)
    assert dg.startswith("sha256:")
    assert result_digest(d) == dg  # 重复计算一致


def test_result_digest_rejects_non_dict() -> None:
    """非 dict 输入必须抛 PersistenceError，避免把不稳定表示当摘要。"""
    with pytest.raises(PersistenceError):
        result_digest([1, 2, 3])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 5. 未知 run_id 的三类读取语义
# --------------------------------------------------------------------------- #


def test_unknown_run_id_returns_none(tmp_path: Path) -> None:
    """未知 run_id：元数据 / 文档 / 详情 / 审计 均返回 None（不抛、不伪装）。"""
    db = tmp_path / "t.db"
    assert get_run(db, "nope") is None
    assert get_run_document(db, "nope") is None
    assert get_run_with_document(db, "nope") is None
    assert audit_run_integrity(db, "nope") is None


def test_unknown_run_document_not_corrupted(tmp_path: Path) -> None:
    """未知 run_id 读取缺失文档应返回 None，而非抛 ResultDocumentCorruptedError。"""
    db = tmp_path / "t.db"
    assert get_run_document(db, "missing") is None
