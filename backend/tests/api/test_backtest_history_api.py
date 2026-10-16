"""历史回测列表与详情 API 测试。

覆盖：
- 列表：空结果、时间倒序、次级排序稳定、limit/offset、过滤、total、不含 document_json；
- 详情：正确返回、404、元数据存在但文档缺失（完整性错误）、结果文档损坏
  （run_result_corrupted）、不修改历史记录；
- 兼容性：POST /backtests 保存的记录可被列表/详情读取。
"""

from __future__ import annotations

import dataclasses

from deepquant.persistence import (
    connect,
    get_db_path,
    get_run_with_document,
    list_runs,
)

# --------------------------------------------------------------------------- #
# 辅助：通过 POST 保存若干回测记录
# --------------------------------------------------------------------------- #


def _post_backtest(client, payload, *, symbol: str | None = None) -> str:
    """执行一次回测并返回 run_id（默认保存）。"""
    body = dict(payload)
    if symbol is not None:
        body["dataset_symbol"] = symbol
    response = client.post("/backtests", json=body)
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


# --------------------------------------------------------------------------- #
# 列表 API
# --------------------------------------------------------------------------- #


def test_list_empty_returns_zero_total(client) -> None:
    """数据库无历史记录时返回空列表且 total=0。"""
    response = client.get("/backtests")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_list_default_order_desc_by_created_at(
    client_with_dataset, valid_backtest_payload
) -> None:
    """多条记录默认按 created_at 降序，最新在前。"""
    ids = [_post_backtest(client_with_dataset, valid_backtest_payload) for _ in range(3)]
    response = client_with_dataset.get("/backtests")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["run_id"] for i in items] == list(reversed(ids))
    # 列表项不含完整结果文档字段
    for item in items:
        assert "result" not in item
        assert "document_json" not in item


def test_list_limit_and_offset(client_with_dataset, valid_backtest_payload) -> None:
    """limit 与 offset 正常生效。"""
    ids = [_post_backtest(client_with_dataset, valid_backtest_payload) for _ in range(5)]
    r1 = client_with_dataset.get("/backtests?limit=2").json()
    assert len(r1["items"]) == 2
    assert r1["total"] == 5
    r2 = client_with_dataset.get("/backtests?limit=2&offset=2").json()
    assert len(r2["items"]) == 2
    assert r2["items"][0]["run_id"] == ids[2]  # 倒序：第3条（索引2从末尾数）


def test_list_limit_over_max_returns_422(client) -> None:
    """limit 超过上限 100 返回 422。"""
    response = client.get("/backtests?limit=101")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_list_limit_zero_returns_422(client) -> None:
    """limit 为 0 返回 422。"""
    response = client.get("/backtests?limit=0")
    assert response.status_code == 422


def test_list_offset_negative_returns_422(client) -> None:
    """offset 为负数返回 422。"""
    response = client.get("/backtests?offset=-1")
    assert response.status_code == 422


def test_list_filter_by_symbol(
    client, data_root, tmp_path, valid_backtest_payload
) -> None:
    """按 symbol 过滤正确。"""
    from conftest import ingest_dataset

    ingest_dataset(data_root, tmp_path, "AAA")
    ingest_dataset(data_root, tmp_path, "BBB")
    _post_backtest(client, valid_backtest_payload, symbol="AAA")
    _post_backtest(client, valid_backtest_payload, symbol="BBB")
    _post_backtest(client, valid_backtest_payload, symbol="AAA")

    response = client.get("/backtests?symbol=AAA")
    items = response.json()["items"]
    assert len(items) == 2
    assert all(i["symbol"] == "AAA" for i in items)


def test_list_filter_by_strategy_id(
    client_with_dataset, valid_backtest_payload, monkeypatch
) -> None:
    """按 strategy_id 过滤正确。"""
    # 注册一个同 factory 但不同 id 的策略，用于产生不同 strategy_id 的记录
    from deepquant.api import registry as registry_mod

    ma_spec = registry_mod.STRATEGY_REGISTRY["ma_cross"]
    monkeypatch.setitem(
        registry_mod.STRATEGY_REGISTRY,
        "dummy_strategy",
        dataclasses.replace(ma_spec, strategy_id="dummy_strategy"),
    )

    _post_backtest(client_with_dataset, valid_backtest_payload)
    other = dict(valid_backtest_payload)
    other["strategy_id"] = "dummy_strategy"
    _post_backtest(client_with_dataset, other)

    response = client_with_dataset.get("/backtests?strategy_id=ma_cross")
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["strategy_id"] == "ma_cross"


def test_list_filter_by_status(client_with_dataset, valid_backtest_payload) -> None:
    """按 status 过滤正确。"""
    _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get("/backtests?status=succeeded")
    assert response.json()["total"] == 1
    response = client_with_dataset.get("/backtests?status=failed")
    assert response.json()["total"] == 0


def test_list_combined_filters_total(
    client, data_root, tmp_path, valid_backtest_payload
) -> None:
    """多过滤条件组合时，total 为过滤后总数（非当前页数量）。"""
    from conftest import ingest_dataset

    ingest_dataset(data_root, tmp_path, "AAA")
    ingest_dataset(data_root, tmp_path, "BBB")
    _post_backtest(client, valid_backtest_payload, symbol="AAA")
    _post_backtest(client, valid_backtest_payload, symbol="AAA")
    _post_backtest(client, valid_backtest_payload, symbol="BBB")

    response = client.get("/backtests?symbol=AAA&limit=1")
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 2  # 过滤后共 2 条，当前页只取 1 条


def test_list_secondary_sort_stable(client_with_dataset, valid_backtest_payload, monkeypatch) -> None:
    """相同 created_at 下按 run_id 升序次级排序，保证分页稳定。"""
    # 强制所有记录的 created_at 相同，验证次级排序
    from deepquant.persistence import store as store_mod

    original_now = store_mod._now_utc_iso
    fixed = "2026-01-01T00:00:00.000000Z"
    monkeypatch.setattr(store_mod, "_now_utc_iso", lambda: fixed)

    ids = sorted(_post_backtest(client_with_dataset, valid_backtest_payload) for _ in range(3))
    response = client_with_dataset.get("/backtests")
    items = response.json()["items"]
    assert [i["run_id"] for i in items] == ids  # run_id 升序

    monkeypatch.setattr(store_mod, "_now_utc_iso", original_now)


def test_list_malicious_filter_param_is_safe(client_with_dataset, valid_backtest_payload) -> None:
    """恶意过滤参数不能改变 SQL 语义（仍按字面字符串匹配）。"""
    _post_backtest(client_with_dataset, valid_backtest_payload)
    # 尝试注入：不会被解释为 SQL，只作为字面 symbol 值匹配，结果为 0 条
    response = client_with_dataset.get("/backtests?symbol=' OR 1=1 --")
    assert response.status_code == 200
    assert response.json()["total"] == 0


# --------------------------------------------------------------------------- #
# 详情 API
# --------------------------------------------------------------------------- #


def test_detail_existing_run_returns_metadata_and_result(
    client_with_dataset, valid_backtest_payload
) -> None:
    """已存在的 run_id 返回正确元数据与完整结果。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get(f"/backtests/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["metadata"]["run_id"] == run_id
    assert "result" in body
    # 结果文档包含回测核心字段
    assert "performance" in body["result"]
    assert "config" in body["result"]


def test_detail_result_matches_saved_document(
    client_with_dataset, valid_backtest_payload
) -> None:
    """详情返回的 result 与数据库保存的文档完全一致。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get(f"/backtests/{run_id}")
    body = response.json()

    _, document = get_run_with_document(get_db_path(), run_id)  # type: ignore[misc]
    assert document is not None
    assert body["result"] == document


def test_detail_result_digest_consistency(
    client_with_dataset, valid_backtest_payload
) -> None:
    """元数据中的 result_digest 与返回结果文档的摘要一致。"""
    from deepquant.persistence import result_digest

    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    response = client_with_dataset.get(f"/backtests/{run_id}")
    body = response.json()
    assert body["metadata"]["result_digest"] == result_digest(body["result"])


def test_detail_not_found_returns_404(client) -> None:
    """不存在的 run_id 返回 404。"""
    response = client.get("/backtests/nonexistent-run-id")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_detail_integrity_error_when_document_missing(
    client_with_dataset, valid_backtest_payload
) -> None:
    """元数据存在但结果文档缺失时返回明确的完整性错误（500，非 404）。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    # 手动删除 run_documents 行，制造完整性问题
    conn = connect(get_db_path())
    try:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()

    response = client_with_dataset.get(f"/backtests/{run_id}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "run_integrity_error"


def test_detail_result_corrupted_when_document_invalid_json(
    client_with_dataset, valid_backtest_payload
) -> None:
    """结果文档 JSON 损坏时返回明确的完整性错误码（500 run_result_corrupted）。

    必须与「文档缺失」（run_integrity_error）、「数据库故障」（persistence_failed）、
    「记录不存在」（404 run_not_found）区分，而非无差别的通用 500。
    """
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    conn = connect(get_db_path())
    try:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            ("{not a valid result document", run_id),
        )
        conn.commit()
    finally:
        conn.close()

    response = client_with_dataset.get(f"/backtests/{run_id}")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "run_result_corrupted"
    # 不泄露服务器路径或堆栈
    text = response.text
    assert "backtests.db" not in text
    assert "/tmp" not in text

    # 原有历史记录仍能正常读取（不因一处损坏影响其它记录）
    other = _post_backtest(client_with_dataset, valid_backtest_payload)
    ok = client_with_dataset.get(f"/backtests/{other}")
    assert ok.status_code == 200
    assert ok.json()["run_id"] == other


def test_detail_does_not_modify_history(
    client_with_dataset, valid_backtest_payload
) -> None:
    """查询详情不会修改或删除已有历史记录。"""
    run_id = _post_backtest(client_with_dataset, valid_backtest_payload)
    before = list_runs(get_db_path())

    response = client_with_dataset.get(f"/backtests/{run_id}")
    assert response.status_code == 200

    after = list_runs(get_db_path())
    assert len(after) == len(before)
    assert {r.run_id for r in after} == {r.run_id for r in before}


def test_detail_multiple_runs_return_respective_results(
    client_with_dataset, valid_backtest_payload
) -> None:
    """多个不同 run_id 各自返回对应结果。"""
    id_a = _post_backtest(client_with_dataset, valid_backtest_payload)
    id_b = _post_backtest(client_with_dataset, valid_backtest_payload)

    ra = client_with_dataset.get(f"/backtests/{id_a}").json()
    rb = client_with_dataset.get(f"/backtests/{id_b}").json()
    assert ra["run_id"] == id_a
    assert rb["run_id"] == id_b
    # 两条记录的 run_id 不同
    assert id_a != id_b
