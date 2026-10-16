"""只读复现信息清单 API 测试（GET /backtests/{run_id}/reproducibility）。

覆盖：
- 正常记录：返回 items / levels，结论不宣称完全可复现；
- 原始数据指纹与处理后数据摘要被区分；
- 原始行情文件未归档、策略代码版本未记录、运行环境未记录如实标注；
- 旧记录缺少可选字段 → 如实标记 missing，不伪造默认值；
- 结果文档损坏 / 缺失 → 200 + result_document_state（不抛异常）；
- run 不存在 → 404 run_not_found；
- 数据库访问异常 → 500 persistence_failed（与字段缺失区分）；
- 清单查询严格只读（不新增/更新/删除历史记录）；
- 不影响既有详情 / 完整性 / 比较 API；
- 响应不泄露服务器绝对路径。
"""

from __future__ import annotations

from deepquant.persistence import (
    connect,
    get_db_path,
    get_run,
    list_runs,
)


def _post_backtest(client, payload) -> str:
    response = client.post("/backtests", json=dict(payload))
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _item(body: dict, key: str) -> dict:
    return next(i for i in body["items"] if i["key"] == key)


def _level(body: dict, name: str) -> dict:
    return next(lv for lv in body["levels"] if lv["level"] == name)


def _set_document_json(run_id: str, text: str) -> None:
    conn = connect(get_db_path())
    try:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?", (text, run_id)
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 正常记录
# --------------------------------------------------------------------------- #


def test_repro_registered_run_returns_checklist(
    client_with_dataset, valid_backtest_payload
) -> None:
    """正常记录返回完整清单结构，文档状态 ok。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get(f"/backtests/{run_id}/reproducibility")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["result_document_state"] == "ok"
    assert body["items"] and body["levels"]
    assert body["conclusion"]
    assert body["generated_at"]


def test_repro_distinguishes_source_sha256_and_dataset_digest(
    client_with_dataset, valid_backtest_payload
) -> None:
    """原始数据指纹与处理后数据摘要区分为两项，且都不是 verifiable。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    source = _item(body, "source_sha256")
    dataset = _item(body, "dataset_digest")
    assert source["value"] != dataset["value"]
    # 指纹存在但原始文件未归档 → 无法验证
    assert source["status"] == "not_verifiable"
    assert dataset["status"] == "not_verifiable"


def test_repro_reports_not_archived_and_not_recorded(
    client_with_dataset, valid_backtest_payload
) -> None:
    """原始文件未归档 / 策略代码未记录 / 运行环境未记录。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    assert _item(body, "raw_market_file")["status"] == "not_archived"
    assert _item(body, "strategy_code_version")["status"] == "not_recorded"
    assert _item(body, "runtime_environment")["status"] == "not_recorded"


def test_repro_levels_never_claim_full(
    client_with_dataset, valid_backtest_payload
) -> None:
    """三级复现能力均不得为 full；结论不得宣称完全可复现。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    for name in ("data", "code", "environment"):
        assert _level(body, name)["status"] in ("partial", "none", "undetermined")
    assert "受限" in body["conclusion"]
    assert "不代表可复现" in body["conclusion"] or "完整性审计通过仅表示" in body["conclusion"]


def test_repro_available_fields_reflect_metadata(
    client_with_dataset, valid_backtest_payload
) -> None:
    """身份 / 资金 / 引擎等已保存字段标注为 available。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    for key in ("run_id", "symbol", "period", "initial_cash", "strategy_id", "engine_version"):
        assert _item(body, key)["status"] == "available"


# --------------------------------------------------------------------------- #
# 旧记录 / 字段缺失
# --------------------------------------------------------------------------- #


def test_repro_old_record_missing_fields_are_marked(
    client_with_dataset, valid_backtest_payload
) -> None:
    """旧记录缺少指纹 / 摘要 / 版本 → missing，且 value 为 null（不伪造）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    conn = connect(get_db_path())
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

    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    for key in ("source_sha256", "dataset_digest", "strategy_version", "engine_version"):
        assert _item(body, key)["status"] == "missing"
        assert _item(body, key)["value"] is None
    assert _level(body, "data")["status"] == "none"


# --------------------------------------------------------------------------- #
# 文档损坏 / 缺失
# --------------------------------------------------------------------------- #


def test_repro_document_corrupted_reported_as_state(
    client_with_dataset, valid_backtest_payload
) -> None:
    """结果文档损坏 → 200 + corrupted（不抛异常，不伪装为可复现）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    _set_document_json(run_id, "{not valid json")

    response = client_with_dataset.get(f"/backtests/{run_id}/reproducibility")
    assert response.status_code == 200
    body = response.json()
    assert body["result_document_state"] == "corrupted"
    assert _item(body, "data_processing_rules")["status"] == "missing"


def test_repro_document_missing_reported_as_state(
    client_with_dataset, valid_backtest_payload
) -> None:
    """结果文档缺失 → 200 + missing。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    conn = connect(get_db_path())
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    assert body["result_document_state"] == "missing"
    # 依赖元数据的项仍可用
    assert _item(body, "run_id")["status"] == "available"


# --------------------------------------------------------------------------- #
# 错误分级
# --------------------------------------------------------------------------- #


def test_repro_unknown_run_returns_404(client) -> None:
    """未知 run_id → 404 run_not_found。"""
    response = client.get("/backtests/nonexistent/reproducibility")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_repro_db_error_returns_500_persistence_failed(
    client, tmp_path, monkeypatch
) -> None:
    """数据库访问异常 → 500 persistence_failed（与「字段缺失」区分）。"""
    db_as_dir = tmp_path / "db_is_a_directory"
    db_as_dir.mkdir()
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(db_as_dir))

    response = client.get("/backtests/any-run/reproducibility")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "persistence_failed"


# --------------------------------------------------------------------------- #
# 只读性 / 不影响既有功能 / 泄露检查
# --------------------------------------------------------------------------- #


def test_repro_is_read_only(client_with_dataset, valid_backtest_payload) -> None:
    """生成清单前后历史记录数量、内容、摘要均不变。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    before_runs = list_runs(get_db_path())
    before_record = get_run(get_db_path(), run_id)

    for _ in range(3):
        assert client_with_dataset.get(f"/backtests/{run_id}/reproducibility").status_code == 200

    after_runs = list_runs(get_db_path())
    after_record = get_run(get_db_path(), run_id)
    assert len(after_runs) == len(before_runs)
    assert {r.run_id for r in after_runs} == {r.run_id for r in before_runs}
    assert after_record is not None and before_record is not None
    assert after_record.result_digest == before_record.result_digest


def test_repro_does_not_affect_detail_and_integrity(
    client_with_dataset, valid_backtest_payload
) -> None:
    """清单调用不影响既有详情 / 完整性 API。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    detail_before = client_with_dataset.get(f"/backtests/{run_id}").json()
    integrity_before = client_with_dataset.get(f"/backtests/{run_id}/integrity").json()

    client_with_dataset.get(f"/backtests/{run_id}/reproducibility")

    detail_after = client_with_dataset.get(f"/backtests/{run_id}").json()
    integrity_after = client_with_dataset.get(f"/backtests/{run_id}/integrity").json()
    assert detail_after["result"] == detail_before["result"]
    assert integrity_after["status"] == integrity_before["status"]
    assert integrity_after["recomputed_digest"] == integrity_before["recomputed_digest"]


def test_repro_response_does_not_leak_paths(
    client_with_dataset, valid_backtest_payload
) -> None:
    """响应不包含服务器绝对路径或数据库文件名。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    text = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").text
    assert "backtests.db" not in text
    assert "/tmp" not in text
    assert "/workspace" not in text


# --------------------------------------------------------------------------- #
# 完整性 ≠ 可复现性（响应中不重复完整性职责）
# --------------------------------------------------------------------------- #


def test_repro_full_set_after_integrity_pass_is_still_limited(
    client_with_dataset, valid_backtest_payload
) -> None:
    """即使完整性审计通过，复现清单仍明确标注能力受限，不升级为完全可复现。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    integrity = client_with_dataset.get(f"/backtests/{run_id}/integrity").json()
    assert integrity["status"] == "passed"

    body = client_with_dataset.get(f"/backtests/{run_id}/reproducibility").json()
    assert _level(body, "code")["status"] != "full"
    assert _level(body, "environment")["status"] != "full"
