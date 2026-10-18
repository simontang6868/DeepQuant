"""GET /datasets 的 unavailable_datasets / coverage 契约测试。

要解决的问题：导入时被闸门拒绝的标的**从未入库**，所以它既不在
``datasets`` 里、也不在 ``invalid_datasets`` 里——界面上就是「凭空少了一只」。
这让人以为软件漏导入，而实际上拒绝是对的（修一修会得到一份与文档不对应的行情）。

本文件锁四条边界，缺一条都会让这个字段变成骗人的：
1. 只有 ``status=rejected`` 且**当前仓库确实没有**的标的才列出；
2. 仓库里已经有该标的后，不再说它不可用（以仓库为准）；
3. 清单缺失/损坏不影响接口可用（返回空列表，不是 500）；
4. 列出的标的仍然不能用来回测（解释性信息，不是可用性开关）。
"""

from __future__ import annotations

import json
from pathlib import Path

from deepquant.api.datasets import POOL_MANIFEST_NAME
from tests.api.conftest import ingest_dataset


def _write_manifest(data_root: Path, symbols: list[dict], **extra) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-10-18T21:03:59+08:00",
        "pool": {"label": "b50_v3", "n_listed": len(symbols)},
        "symbols": symbols,
        **extra,
    }
    path = data_root / POOL_MANIFEST_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_rejected_symbol_is_explained_as_unavailable(client, data_root) -> None:
    _write_manifest(
        data_root,
        [
            {"code": "600809.SH", "name": "山西汾酒", "status": "rejected", "reason": "invalid_price(131)"},
            {"code": "000568.SZ", "name": "泸州老窖", "status": "rejected", "reason": "invalid_price(1)"},
        ],
    )
    body = client.get("/datasets").json()

    symbols = [d["symbol"] for d in body["unavailable_datasets"]]
    assert symbols == ["000568.SZ", "600809.SH"]  # 按代码升序，排序确定
    entry = next(d for d in body["unavailable_datasets"] if d["symbol"] == "600809.SH")
    assert entry["display_name"] == "山西汾酒"
    assert entry["reason"] == "invalid_price(131)"
    assert entry["checked_at"] == "2026-10-18T21:03:59+08:00"
    # 它们没有进入可选列表
    assert all(d["symbol"] not in symbols for d in body["datasets"])


def test_symbol_present_in_repo_is_not_listed_as_unavailable(
    client, data_root, tmp_path
) -> None:
    """清单说被拒、但仓库里其实有（后来补上了）→ 以仓库为准，不再说它不可用。"""
    ingest_dataset(data_root, tmp_path, "TEST01")
    _write_manifest(
        data_root,
        [{"code": "TEST01", "name": "测试标的", "status": "rejected", "reason": "invalid_price(9)"}],
    )
    body = client.get("/datasets").json()

    assert [d["symbol"] for d in body["datasets"]] == ["TEST01"]
    assert body["unavailable_datasets"] == []


def test_non_rejected_statuses_are_ignored(client, data_root) -> None:
    """unchanged / enriched / conflict 不是「被拒绝」，不能混进来。"""
    _write_manifest(
        data_root,
        [
            {"code": "AAA01", "status": "unchanged", "reason": None},
            {"code": "AAA02", "status": "conflict", "reason": "sha256 mismatch"},
            {"code": "AAA03", "status": "enriched", "reason": None},
            {"code": "AAA04", "status": "rejected", "reason": "invalid_price(2)"},
        ],
    )
    body = client.get("/datasets").json()
    assert [d["symbol"] for d in body["unavailable_datasets"]] == ["AAA04"]


def test_missing_manifest_is_not_an_error(client) -> None:
    """没有清单是正常状态（用户自己导入数据时不会有它）。"""
    response = client.get("/datasets")
    assert response.status_code == 200
    body = response.json()
    assert body["unavailable_datasets"] == []


def test_corrupted_manifest_is_not_an_error(client, data_root) -> None:
    (data_root / POOL_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    response = client.get("/datasets")
    assert response.status_code == 200
    assert response.json()["unavailable_datasets"] == []


def test_manifest_entries_without_reason_get_default_text(client, data_root) -> None:
    _write_manifest(
        data_root, [{"code": "AAA01", "name": "某标的", "status": "rejected", "reason": "  "}]
    )
    entry = client.get("/datasets").json()["unavailable_datasets"][0]
    assert entry["reason"] == "导入时被拒绝"


def test_manifest_with_invalid_name_still_reports_symbol(client, data_root) -> None:
    """名称非法不影响「它不可用」这个事实：不显示名称，而不是丢掉整条记录。"""
    _write_manifest(
        data_root,
        [{"code": "AAA01", "name": "x" * 200, "status": "rejected", "reason": "invalid_price(3)"}],
    )
    entry = client.get("/datasets").json()["unavailable_datasets"][0]
    assert entry["symbol"] == "AAA01"
    assert entry["display_name"] is None


def test_manifest_with_illegal_symbol_is_ignored(client, data_root) -> None:
    """清单里的代码也要过白名单：不能让 ../ 之类的东西进响应。"""
    _write_manifest(
        data_root,
        [
            {"code": "../../etc/passwd", "status": "rejected", "reason": "x"},
            {"code": "AAA01", "status": "rejected", "reason": "invalid_price(1)"},
        ],
    )
    symbols = [d["symbol"] for d in client.get("/datasets").json()["unavailable_datasets"]]
    assert symbols == ["AAA01"]


def test_unavailable_symbol_cannot_be_backtested(client, data_root) -> None:
    """解释性信息不是可用性开关：它依然不能作为回测标的。"""
    _write_manifest(
        data_root,
        [{"code": "AAA01", "name": "某标的", "status": "rejected", "reason": "invalid_price(1)"}],
    )
    response = client.post(
        "/portfolio-backtests",
        json={
            "dataset_symbols": ["AAA01", "AAA02"],
            "initial_cash": 1_000_000,
            "min_assets": 2,
        },
    )
    assert response.status_code == 404
    assert "AAA01" in response.text


def test_coverage_reports_range_and_counts(client, data_root, tmp_path) -> None:
    ingest_dataset(data_root, tmp_path, "AAA01")
    ingest_dataset(data_root, tmp_path, "AAA02")
    _write_manifest(
        data_root,
        [{"code": "600809.SH", "name": "山西汾酒", "status": "rejected", "reason": "invalid_price(131)"}],
    )
    coverage = client.get("/datasets").json()["coverage"]

    assert coverage["n_datasets"] == 2
    assert coverage["n_unavailable"] == 1
    assert coverage["start_date"] and coverage["end_date"]
    note = coverage["note"]
    # 三条必须说清楚：区间终点、清单来源、幸存者偏差
    assert "回测区间无法超过数据覆盖终点" in note
    assert "b50_v3" in note
    assert "幸存者偏差" in note


def test_coverage_is_null_without_datasets(client) -> None:
    """没有数据集时返回 null，不编一个空区间（空日期在界面上会显示成「—」）。"""
    assert client.get("/datasets").json()["coverage"] is None
