"""GET /datasets 接口测试。

覆盖：空目录、正常数据集、多数据集排序、损坏/不完整/不一致元数据、
数据文件缺失、非法文件名、数据根目录不存在、不泄露本机绝对路径。
"""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient  # noqa: F401  (类型标注用)

from conftest import ingest_dataset


def test_empty_data_root_returns_empty_lists(client) -> None:
    """数据根目录存在但没有任何数据集：返回两个空列表。"""
    response = client.get("/datasets")
    assert response.status_code == 200
    assert response.json() == {"datasets": [], "invalid_datasets": []}


def test_single_dataset_fields(client, data_root, tmp_path) -> None:
    """正常数据集：字段与导入管线落盘的元数据一致。"""
    ingest = ingest_dataset(data_root, tmp_path, "TEST01")

    response = client.get("/datasets")
    assert response.status_code == 200
    body = response.json()
    assert body["invalid_datasets"] == []
    assert len(body["datasets"]) == 1

    entry = body["datasets"][0]
    assert entry["symbol"] == "TEST01"
    assert entry["rows"] == 60  # 清洗后行数（构造数据无缺失）
    assert entry["start_date"] == "2020-01-01"
    assert entry["end_date"] == "2020-02-29"
    # source_sha256 来自元数据记录的原始 CSV 指纹，与 raw 文件内容一致
    expected_sha256 = hashlib.sha256(ingest.raw_path.read_bytes()).hexdigest()
    assert entry["source_sha256"] == ingest.metadata["source"]["sha256"] == expected_sha256
    assert entry["source_name"] == "TEST01.csv"
    assert entry["ingested_at"] is not None


def test_multiple_datasets_sorted_deterministically(client, data_root, tmp_path) -> None:
    """多数据集：按 symbol 字典序返回，与导入顺序无关。"""
    ingest_dataset(data_root, tmp_path, "BBB")
    ingest_dataset(data_root, tmp_path, "AAA")
    ingest_dataset(data_root, tmp_path, "CCC")

    body = client.get("/datasets").json()
    assert [d["symbol"] for d in body["datasets"]] == ["AAA", "BBB", "CCC"]
    # 再次请求结果一致（排序确定且可复现）
    body2 = client.get("/datasets").json()
    assert body2["datasets"] == body["datasets"]


def test_corrupt_metadata_json(client, data_root) -> None:
    """元数据不是合法 JSON：不伪装成正常数据集，列入 invalid_datasets。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "BAD01.meta.json").write_text("{not valid json", encoding="utf-8")

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert len(body["invalid_datasets"]) == 1
    invalid = body["invalid_datasets"][0]
    assert invalid["metadata_file"] == "BAD01.meta.json"
    assert invalid["symbol"] == "BAD01"
    assert invalid["reason"]


def test_metadata_missing_required_fields(client, data_root) -> None:
    """元数据缺少必需字段：列入 invalid_datasets。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "BAD02.meta.json").write_text(
        json.dumps({"symbol": "BAD02"}), encoding="utf-8"
    )

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert body["invalid_datasets"][0]["symbol"] == "BAD02"
    assert "缺少必需字段" in body["invalid_datasets"][0]["reason"]


def test_metadata_symbol_mismatch(client, data_root) -> None:
    """元数据记录的 symbol 与文件名不一致：列入 invalid_datasets。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir()
    payload = {
        "symbol": "OTHER",
        "files": {"processed": "BAD03.daily.parquet"},
        "rows": {"processed": 10},
        "date_range": {"start": "2020-01-01", "end": "2020-01-10"},
    }
    (metadata_dir / "BAD03.meta.json").write_text(json.dumps(payload), encoding="utf-8")

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert "不一致" in body["invalid_datasets"][0]["reason"]


def test_missing_processed_file(client, data_root, tmp_path) -> None:
    """元数据完好但数据文件缺失：列入 invalid_datasets，不能伪装成正常数据集。"""
    ingest_dataset(data_root, tmp_path, "GONE01")
    processed = data_root / "processed" / "GONE01.daily.parquet"
    assert processed.exists()
    processed.unlink()

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert len(body["invalid_datasets"]) == 1
    assert body["invalid_datasets"][0]["symbol"] == "GONE01"
    assert "数据文件缺失" in body["invalid_datasets"][0]["reason"]


def test_invalid_metadata_filename(client, data_root) -> None:
    """元数据文件名不是合法数据集标识符：列入 invalid_datasets（symbol 为 null）。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "bad name.meta.json").write_text("{}", encoding="utf-8")

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    invalid = body["invalid_datasets"][0]
    assert invalid["symbol"] is None
    assert "标识符" in invalid["reason"]


def test_data_root_missing_returns_503(make_client, tmp_path) -> None:
    """数据根目录不存在：返回 503 与稳定错误码。"""
    client = make_client((tmp_path / "not_exists").resolve())
    response = client.get("/datasets")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "data_root_unavailable"


def test_response_does_not_leak_absolute_path(client_with_dataset, data_root) -> None:
    """响应（含正常与无效条目）不得包含本机绝对路径。"""
    body = client_with_dataset.get("/datasets").json()
    text = json.dumps(body, ensure_ascii=False)
    assert str(data_root) not in text
    assert "D:\\" not in text and "/home/" not in text


def test_health_still_compatible(client) -> None:
    """GET /health 行为保持兼容。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
