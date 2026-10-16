"""只读完整性审计 API 测试（GET /backtests/{run_id}/integrity）。

覆盖：
- 已保存且未修改的记录返回 passed；
- 结果文档被修改返回 failed，并给出明确 check 状态；
- 结果文档缺失返回 failed（不是 404，也不是通过）；
- run 不存在返回 404 run_not_found；
- 审计严格只读：历史记录数量 / 内容 / result_digest 不变，不新增记录；
- 原有历史详情 API 与比较 API 不受影响；
- 响应不暴露服务器绝对路径。
"""

from __future__ import annotations

import json

from deepquant.persistence import (
    connect,
    get_db_path,
    get_run,
    get_run_with_document,
    list_runs,
)


def _post_backtest(client, payload) -> str:
    response = client.post("/backtests", json=dict(payload))
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _tamper_document(run_id: str) -> None:
    conn = connect(get_db_path())
    try:
        row = conn.execute(
            "SELECT document_json FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
        document = json.loads(row["document_json"])
        document["engine_version"] = "tampered"
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            (json.dumps(document, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #


def test_integrity_passed_for_fresh_record(client_with_dataset, valid_backtest_payload) -> None:
    """新保存且未修改的记录：status=passed，checks 全部 passed。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get(f"/backtests/{run_id}/integrity")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["status"] == "passed"
    assert {c["name"] for c in body["checks"]} >= {
        "document_present",
        "result_digest_consistent",
        "digests_match_across_tables",
        "required_fields_present",
    }
    assert all(c["status"] == "passed" for c in body["checks"])
    assert body["recomputed_digest"] == body["document_result_digest"]
    assert body["audited_at"]


def test_integrity_failed_when_document_modified(
    client_with_dataset, valid_backtest_payload
) -> None:
    """结果文档被修改：status=failed，摘要一致性检查为 failed。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    _tamper_document(run_id)

    body = client_with_dataset.get(f"/backtests/{run_id}/integrity").json()
    assert body["status"] == "failed"
    digest_check = next(c for c in body["checks"] if c["name"] == "result_digest_consistent")
    assert digest_check["status"] == "failed"


def test_integrity_failed_when_document_missing(
    client_with_dataset, valid_backtest_payload
) -> None:
    """结果文档缺失：status=failed（200 + 明确 failed，而非 404 或通过）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    conn = connect(get_db_path())
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    response = client_with_dataset.get(f"/backtests/{run_id}/integrity")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    present = next(c for c in body["checks"] if c["name"] == "document_present")
    assert present["status"] == "failed"


def test_integrity_unknown_run_returns_404(client) -> None:
    """未知 run_id 返回 404 run_not_found。"""
    response = client.get("/backtests/nonexistent/integrity")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_integrity_is_read_only(client_with_dataset, valid_backtest_payload) -> None:
    """审计不修改历史记录：数量、内容、result_digest 均不变。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    before_runs = list_runs(get_db_path())
    before_record = get_run(get_db_path(), run_id)
    _, before_doc = get_run_with_document(get_db_path(), run_id)

    response = client_with_dataset.get(f"/backtests/{run_id}/integrity")
    assert response.status_code == 200

    after_runs = list_runs(get_db_path())
    after_record = get_run(get_db_path(), run_id)
    _, after_doc = get_run_with_document(get_db_path(), run_id)
    assert len(after_runs) == len(before_runs)
    assert {r.run_id for r in after_runs} == {r.run_id for r in before_runs}
    assert after_record is not None and before_record is not None
    assert after_record.result_digest == before_record.result_digest
    assert after_doc == before_doc


def test_detail_and_list_unaffected_by_audit(
    client_with_dataset, valid_backtest_payload
) -> None:
    """审计调用不影响既有详情 API 与列表 API。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    detail_before = client_with_dataset.get(f"/backtests/{run_id}").json()
    list_before = client_with_dataset.get("/backtests").json()

    client_with_dataset.get(f"/backtests/{run_id}/integrity")

    detail_after = client_with_dataset.get(f"/backtests/{run_id}").json()
    list_after = client_with_dataset.get("/backtests").json()
    assert detail_after["result"] == detail_before["result"]
    assert detail_after["metadata"] == detail_before["metadata"]
    assert list_after["total"] == list_before["total"]
    assert [i["run_id"] for i in list_after["items"]] == [i["run_id"] for i in list_before["items"]]


def test_integrity_response_does_not_leak_paths(
    client_with_dataset, valid_backtest_payload
) -> None:
    """响应不包含服务器绝对路径或数据库文件名。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    text = client_with_dataset.get(f"/backtests/{run_id}/integrity").text
    assert "backtests.db" not in text
    assert "/tmp" not in text
    assert "/workspace" not in text


# --------------------------------------------------------------------------- #
# 完整性 ≠ 可复现性
# --------------------------------------------------------------------------- #


def test_saved_strategy_params_and_reproducibility_are_readable(
    client_with_dataset, valid_backtest_payload
) -> None:
    """已保存的策略参数与可复现性判定可正常读取。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    body = client_with_dataset.get(f"/backtests/{run_id}").json()
    assert body["metadata"]["strategy_params"] == {"fast_window": 5, "slow_window": 20}
    assert body["result"]["strategy"]["params"] == {"fast_window": 5, "slow_window": 20}
    repro = body["result"]["reproducibility"]
    assert isinstance(repro, dict)
    assert {"inputs_identifiable", "process_reproducible", "missing", "notes"} <= set(repro)


def test_integrity_does_not_upgrade_reproducibility(
    client_with_dataset, valid_backtest_payload
) -> None:
    """完整性审计不改变结果文档中的可复现性判定（二者不是同一回事）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    repro_before = client_with_dataset.get(f"/backtests/{run_id}").json()["result"]["reproducibility"]

    client_with_dataset.get(f"/backtests/{run_id}/integrity")

    repro_after = client_with_dataset.get(f"/backtests/{run_id}").json()["result"]["reproducibility"]
    assert repro_after == repro_before


def test_integrity_response_makes_no_reproducibility_claim(
    client_with_dataset, valid_backtest_payload
) -> None:
    """完整性审计响应不含任何可复现性判定（避免把「完整性通过」当成「可复现」）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    text = client_with_dataset.get(f"/backtests/{run_id}/integrity").text
    assert "reproducibility" not in text
    assert "process_reproducible" not in text
