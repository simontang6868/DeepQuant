"""POST /backtests/compare 接口测试。"""

from __future__ import annotations

import json

import pytest

from deepquant.persistence import get_db_path, get_run, get_run_document, list_runs
from deepquant.persistence.store import _open_conn


# --------------------------------------------------------------------------- #
# 造两条回测
# --------------------------------------------------------------------------- #


def _create_run(client, payload: dict) -> str:
    resp = client.post("/backtests", json=payload)
    assert resp.status_code == 200, resp.json()
    run_id = resp.json().get("run_id")
    assert run_id, "回测响应应包含 run_id（默认保存）"
    return run_id


@pytest.fixture
def two_run_ids(client_with_dataset, valid_backtest_payload) -> tuple[str, str]:
    a = _create_run(client_with_dataset, valid_backtest_payload)
    b = _create_run(client_with_dataset, valid_backtest_payload)
    return a, b


@pytest.fixture
def three_run_ids(client_with_dataset, valid_backtest_payload) -> tuple[str, str, str]:
    a = _create_run(client_with_dataset, valid_backtest_payload)
    b = _create_run(client_with_dataset, valid_backtest_payload)
    c = _create_run(client_with_dataset, valid_backtest_payload)
    return a, b, c


# --------------------------------------------------------------------------- #
# 1. 基本比较
# --------------------------------------------------------------------------- #


def test_compare_two_runs_success(client_with_dataset, two_run_ids):
    a, b = two_run_ids
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, b]})
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["run_id"] == a
    assert body["items"][1]["run_id"] == b
    assert len(body["pairwise"]) == 1
    key = next(iter(body["pairwise"]))
    assert "fairly_comparable" in body["pairwise"][key]
    assert body["pairwise"][key]["fairly_comparable"] is True


def test_compare_three_runs_success(client_with_dataset, three_run_ids):
    a, b, c = three_run_ids
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, b, c]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    assert len(body["pairwise"]) == 3


# --------------------------------------------------------------------------- #
# 2. 参数边界
# --------------------------------------------------------------------------- #


def test_compare_less_than_two(client_with_dataset):
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": ["only-one"]})
    assert resp.status_code == 422


def test_compare_duplicate_ids_rejected(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, a]})
    assert resp.status_code == 422


def test_compare_empty_string_rejected(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, "  "]})
    assert resp.status_code == 422


def test_compare_more_than_five_rejected(client_with_dataset):
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": list("abcdef")})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 3. 不存在 / 完整性错误
# --------------------------------------------------------------------------- #


def test_compare_missing_run_returns_404(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    resp = client_with_dataset.post(
        "/backtests/compare", json={"run_ids": [a, "0" * 32]}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "run_not_found"


def test_compare_document_missing_returns_integrity_error(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    with _open_conn(get_db_path()) as conn, conn:
        conn.execute("DELETE FROM run_documents WHERE run_id = ?", (a,))
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, _b]})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "run_integrity_error"


# --------------------------------------------------------------------------- #
# 4. 返回值正确性
# --------------------------------------------------------------------------- #


def test_compare_metrics_match_original(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    doc_a = get_run_document(get_db_path(), a)
    assert doc_a is not None
    perf = doc_a.get("performance") or {}
    expected_cumulative = perf.get("cumulative_return")
    expected_final = perf.get("final_equity")

    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, _b]})
    assert resp.status_code == 200
    item_a = resp.json()["items"][0]
    assert item_a["metrics"]["cumulative_return"] == expected_cumulative
    assert item_a["metrics"]["final_equity"] == expected_final


def test_compare_metadata_matches_run_record(client_with_dataset, two_run_ids):
    a, _b = two_run_ids
    record = get_run(get_db_path(), a)
    assert record is not None
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, _b]})
    meta_a = resp.json()["items"][0]["metadata"]
    assert meta_a["run_id"] == record.run_id
    assert meta_a["symbol"] == record.symbol
    assert meta_a["initial_cash"] == record.initial_cash


def test_compare_missing_metrics_not_zero(client_with_dataset, two_run_ids):
    """把一条 run 的 performance 设为 null，返回的指标应显式为 null。"""
    a, _b = two_run_ids
    # 手动 UPDATE 数据库里的 JSON（Python json.dumps + UPDATE）
    import json as _json

    doc = get_run_document(get_db_path(), a)
    assert doc is not None
    doc["performance"] = None
    with _open_conn(get_db_path()) as conn, conn:
        conn.execute(
            "UPDATE run_documents SET document_json = ? WHERE run_id = ?",
            (_json.dumps(doc), a),
        )

    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, _b]})
    assert resp.status_code == 200
    # a 的 performance 为 null → 全部指标为 null；b 的正常
    items = {it["run_id"]: it for it in resp.json()["items"]}
    assert items[a]["metrics"]["cumulative_return"] is None
    assert items[a]["metrics"]["final_equity"] is None
    assert items[a]["metrics"]["total_pnl"] is None
    # b 的应正常有值
    assert items[_b]["metrics"]["final_equity"] is not None


# --------------------------------------------------------------------------- #
# 5. 可比性规则
# --------------------------------------------------------------------------- #


def test_compare_same_params_is_comparable(client_with_dataset, two_run_ids):
    a, b = two_run_ids
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, b]})
    pairwise = resp.json()["pairwise"]
    key = next(iter(pairwise))
    assert pairwise[key]["fairly_comparable"] is True
    assert pairwise[key]["strategy_differs"] is False
    assert resp.json()["warnings"] == []


def test_compare_different_strategy_params_labeled(client_with_dataset, valid_backtest_payload):
    """修改 slow_window → 策略参数不同，但应仍属于合法比较维度。"""
    payload_a = dict(valid_backtest_payload)
    payload_b = dict(valid_backtest_payload)
    payload_b["strategy_params"] = {"fast_window": 5, "slow_window": 30}
    a = _create_run(client_with_dataset, payload_a)
    b = _create_run(client_with_dataset, payload_b)
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, b]})
    assert resp.status_code == 200
    pairwise = resp.json()["pairwise"]
    key = next(iter(pairwise))
    assert pairwise[key]["fairly_comparable"] is True
    assert pairwise[key]["strategy_differs"] is True
    reasons = pairwise[key].get("reasons", [])
    assert any("策略" in r for r in reasons)


# --------------------------------------------------------------------------- #
# 6. 比较不修改数据库
# --------------------------------------------------------------------------- #


def test_compare_does_not_modify_history(client_with_dataset, two_run_ids):
    a, b = two_run_ids
    before_runs = list_runs(get_db_path(), limit=100, offset=0)
    before_ids = {r.run_id for r in before_runs}
    before_doc_a = get_run_document(get_db_path(), a)
    assert before_doc_a is not None

    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": [a, b]})
    assert resp.status_code == 200

    after_runs = list_runs(get_db_path(), limit=100, offset=0)
    after_ids = {r.run_id for r in after_runs}
    assert before_ids == after_ids
    assert len(before_runs) == len(after_runs)

    after_doc_a = get_run_document(get_db_path(), a)
    assert after_doc_a is not None
    assert json.dumps(before_doc_a, sort_keys=True, separators=(",", ":")) == json.dumps(
        after_doc_a, sort_keys=True, separators=(",", ":")
    )
