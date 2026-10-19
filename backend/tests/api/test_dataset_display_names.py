"""数据集显示名称的读取端契约（``api/datasets._parse_metadata``）。

分两类，必须分开测：
1. **缺失合法**——历史数据集元数据里没有 ``instrument``，不能因此变成不可用；
2. **存在而非法必须报错**——名称会进 DOM，宁可列成不可用，也不要显示乱码或控制字符。
"""

from __future__ import annotations

import json

import pytest

from deepquant.api.datasets import discover_datasets
from deepquant.data.instrument import MAX_DISPLAY_NAME_LENGTH


def _register(root, symbol: str = "TEST01", instrument=None) -> None:
    """手工写一份最小元数据（数据文件为空壳，只验证元数据解析）。"""
    (root / "processed").mkdir(parents=True, exist_ok=True)
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    (root / "processed" / f"{symbol}.daily.parquet").write_bytes(b"stub")
    meta = {
        "symbol": symbol,
        "files": {"processed": f"{symbol}.daily.parquet"},
        "rows": {"processed": 10},
        "date_range": {"start": "2020-01-01", "end": "2020-02-01"},
        "source": {"name": "unit-test"},
    }
    if instrument is not None:
        meta["instrument"] = instrument
    (root / "metadata" / f"{symbol}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def test_missing_instrument_keeps_dataset_usable(data_root) -> None:
    _register(data_root)
    valid, invalid = discover_datasets(data_root)
    assert invalid == []
    assert valid[0].display_name is None


def test_instrument_without_name_is_usable_but_null(data_root) -> None:
    """只有 industry 之类附加信息时，名称仍然是 None，而不是空串。"""
    _register(data_root, instrument={"industry": "银行"})
    valid, invalid = discover_datasets(data_root)
    assert invalid == []
    assert valid[0].display_name is None


def test_name_is_read_back(data_root) -> None:
    _register(data_root, instrument={"display_name": "浦发银行"})
    valid, invalid = discover_datasets(data_root)
    assert invalid == []
    assert valid[0].display_name == "浦发银行"


def test_instrument_not_object_is_invalid(data_root) -> None:
    _register(data_root, instrument=["浦发银行"])
    valid, invalid = discover_datasets(data_root)
    assert valid == []
    assert invalid[0].reason == "元数据 instrument 字段不是对象"


@pytest.mark.parametrize(
    "bad_name, keyword",
    [
        ("", "不能为空"),
        ("   ", "不能为空"),
        ("\x00bad", "控制字符"),
        ("x" * (MAX_DISPLAY_NAME_LENGTH + 1), "超过"),
    ],
)
def test_invalid_name_marks_dataset_unavailable(data_root, bad_name: str, keyword: str) -> None:
    _register(data_root, instrument={"display_name": bad_name})
    valid, invalid = discover_datasets(data_root)
    assert valid == []
    assert "元数据标的名称不可用" in invalid[0].reason
    assert keyword in invalid[0].reason


def test_invalid_name_blocks_symbol_lookup(make_client, data_root) -> None:
    """按 symbol 取数据集时也必须是「已登记但不可用」，不能悄悄回落到无名称版本。"""
    _register(data_root, instrument={"display_name": "\x7f"})
    resp = make_client(data_root).get("/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["datasets"] == []
    assert body["invalid_datasets"][0]["symbol"] == "TEST01"


def test_route_returns_display_name(make_client, data_root) -> None:
    _register(data_root, instrument={"display_name": "浦发银行"})
    resp = make_client(data_root).get("/datasets")
    assert resp.status_code == 200
    entry = resp.json()["datasets"][0]
    assert entry["display_name"] == "浦发银行"


def test_route_keeps_display_name_null_when_absent(make_client, data_root) -> None:
    """字段缺失时返回 null（而不是省略），前端类型与响应保持一一对应。"""
    _register(data_root)
    resp = make_client(data_root).get("/datasets")
    entry = resp.json()["datasets"][0]
    assert "display_name" in entry
    assert entry["display_name"] is None
