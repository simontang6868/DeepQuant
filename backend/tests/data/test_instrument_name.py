"""标的显示名称：写入端 → 元数据 → 读取端的整条链路契约。

为什么单独测这条链路
--------------------
名称是**跨两个模块的同一个规则**：``data/pipeline`` 写、``api/datasets`` 读。
两边各写一套校验，就会出现「写得进去、读不出来」——数据集直接在界面上变成
不可用，而错误只说「元数据无效」。所以这里既测单侧规则，也测**写出去的东西
一定能读回来**（round-trip），这条比任何单侧断言都重要。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from deepquant.api.datasets import discover_datasets
from deepquant.data.instrument import MAX_DISPLAY_NAME_LENGTH, normalize_display_name
from deepquant.data.pipeline import ingest_daily_csv


# --------------------------------------------------------------------------- #
# 1. 规则本体
# --------------------------------------------------------------------------- #


def test_normalize_strips_surrounding_whitespace() -> None:
    """从 CSV/JSON 里抄名称常带空格，落盘前统一去掉。"""
    assert normalize_display_name("  平安银行\t") == "平安银行"


def test_normalize_passes_through_none() -> None:
    """None 表示「没有名称」，是合法输入（历史元数据都没有这个键）。"""
    assert normalize_display_name(None) is None


@pytest.mark.parametrize(
    "value, reason_keyword",
    [
        ("", "不能为空"),
        ("   ", "不能为空"),
        (123, "必须是字符串"),
        (b"\xe5\xb9\xb3", "必须是字符串"),
        ("x" * (MAX_DISPLAY_NAME_LENGTH + 1), "超过"),
        ("平安\x00银行", "控制字符"),
        ("平安\n银行", "控制字符"),
        ("平安\x7f银行", "控制字符"),
    ],
)
def test_normalize_rejects_invalid(value: object, reason_keyword: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        normalize_display_name(value)
    assert reason_keyword in str(excinfo.value)


def test_normalize_accepts_length_boundary() -> None:
    """上限边界要能进——否则没人知道上限是 63 还是 64。"""
    name = "龙" * MAX_DISPLAY_NAME_LENGTH
    assert normalize_display_name(name) == name


# --------------------------------------------------------------------------- #
# 2. 写入端：元数据里出现 / 不出现 instrument
# --------------------------------------------------------------------------- #


def _write_csv(path, rows: int = 30) -> None:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [10.0 + i * 0.1 for i in range(rows)],
            "high": [10.1 + i * 0.1 for i in range(rows)],
            "low": [9.9 + i * 0.1 for i in range(rows)],
            "close": [10.05 + i * 0.1 for i in range(rows)],
            "volume": [1000 + i for i in range(rows)],
        }
    )
    frame.to_csv(path, index=False, encoding="utf-8")


def test_metadata_has_no_instrument_key_when_name_absent(tmp_path) -> None:
    """不传名称时元数据必须与历史行为一字不差（否则既有数据集「看起来被改过」）。"""
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    result = ingest_daily_csv(src, symbol="600000.SH", data_root=tmp_path / "data")
    assert "instrument" not in result.metadata
    on_disk = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert "instrument" not in on_disk


def test_metadata_carries_normalized_name(tmp_path) -> None:
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    result = ingest_daily_csv(
        src, symbol="600000.SH", data_root=tmp_path / "data", instrument_name="  浦发银行 "
    )
    assert result.metadata["instrument"] == {"display_name": "浦发银行"}
    on_disk = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert on_disk["instrument"]["display_name"] == "浦发银行"


def test_invalid_name_fails_before_any_file_is_written(tmp_path) -> None:
    """名字非法时不能留下「raw 写了、metadata 没写」的半成品。"""
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    data_root = tmp_path / "data"
    with pytest.raises(ValueError):
        ingest_daily_csv(src, symbol="600000.SH", data_root=data_root, instrument_name="\x00bad")
    for sub in ("raw", "processed", "metadata"):
        directory = data_root / sub
        assert not directory.exists() or not any(directory.iterdir())


def test_name_does_not_reach_the_market_data(tmp_path) -> None:
    """名称只进元数据：行情表列集合不变（防止有人把名称并进 DataFrame）。"""
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    result = ingest_daily_csv(
        src, symbol="600000.SH", data_root=tmp_path / "data", instrument_name="浦发银行"
    )
    assert list(result.data.columns) == ["date", "open", "high", "low", "close", "volume"]


# --------------------------------------------------------------------------- #
# 3. 读取端：round-trip（写出去的一定读得回来）
# --------------------------------------------------------------------------- #


def test_written_name_is_read_back_by_api(tmp_path) -> None:
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    data_root = tmp_path / "data"
    ingest_daily_csv(src, symbol="600000.SH", data_root=data_root, instrument_name="浦发银行")

    valid, invalid = discover_datasets(data_root.resolve())
    assert invalid == []
    assert [d.symbol for d in valid] == ["600000.SH"]
    assert valid[0].display_name == "浦发银行"


def test_dataset_without_name_is_still_usable(tmp_path) -> None:
    """名称缺失不是错误：老数据集照常可用，只是界面上只显示代码。"""
    src = tmp_path / "600000.SH.qfq.csv"
    _write_csv(src)
    data_root = tmp_path / "data"
    ingest_daily_csv(src, symbol="600000.SH", data_root=data_root)

    valid, invalid = discover_datasets(data_root.resolve())
    assert invalid == []
    assert valid[0].display_name is None
