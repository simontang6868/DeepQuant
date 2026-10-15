"""导入流水线测试：原始/处理分离、清洗规则、硬错误中止、元数据可复现。"""

import pytest

from deepquant.data.pipeline import DataValidationError, ingest_daily_csv
from deepquant.data.storage import io as storage_io

# 含 1 行重复日期 + 1 行数值缺失，共 5 行原始数据
MESSY_CSV = (
    "date,open,high,low,close,volume\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
    "2024-01-03,10.2,10.6,10.0,10.5,1300\n"
    "2024-01-04,10.4,,10.1,10.6,1400\n"
    "2024-01-05,10.6,10.9,10.3,10.8,1500\n"
)


def _write(tmp_path, text, name="in.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_ingest_separates_raw_and_processed(tmp_path):
    src = _write(tmp_path, MESSY_CSV)
    result = ingest_daily_csv(src, symbol="TEST", data_root=tmp_path / "data")

    # 原始文件原样保存（逐字节一致），位于 raw/
    assert result.raw_path.name == "TEST.raw.csv"
    assert result.raw_path.read_bytes() == src.read_bytes()

    # 处理后文件位于 processed/，格式为 Parquet
    assert result.processed_path.name == "TEST.daily.parquet"
    assert result.processed_path.parent.name == "processed"

    # 不存在被填充的假数据
    assert not bool(result.data.isna().to_numpy().any())


def test_ingest_cleaning_counts_and_metadata(tmp_path):
    src = _write(tmp_path, MESSY_CSV, name="sample.csv")
    result = ingest_daily_csv(
        src, symbol="TEST", data_root=tmp_path / "data", source_name="demo-vendor"
    )

    assert result.metadata["rows"] == {"raw": 5, "processed": 3}
    assert result.metadata["cleaning"]["duplicate_dates_dropped"] == 1
    assert result.metadata["cleaning"]["missing_value_rows_dropped"] == 1
    assert result.metadata["date_range"] == {"start": "2024-01-02", "end": "2024-01-05"}
    assert result.metadata["storage"] == {"raw_format": "csv", "processed_format": "parquet"}
    assert result.metadata["source"]["name"] == "demo-vendor"
    assert result.metadata["source"]["sha256"] == storage_io.file_sha256(src)

    # 原始数据含可清洗告警；处理后数据完全通过
    assert result.report_before.has_code("duplicate_dates")
    assert result.report_before.has_code("missing_values")
    assert result.report_after.is_valid
    assert result.report_after.issues == []

    # 元数据落盘且与返回值一致（可用于复现）
    assert storage_io.read_json(result.metadata_path) == result.metadata


def test_processed_parquet_round_trips(tmp_path):
    src = _write(tmp_path, MESSY_CSV)
    result = ingest_daily_csv(src, symbol="TEST", data_root=tmp_path / "data")

    reloaded = storage_io.read_daily_parquet(result.processed_path)
    assert len(reloaded) == 3
    assert str(reloaded["date"].dtype).startswith("datetime64")  # Parquet 保留类型
    assert reloaded["date"].is_monotonic_increasing
    assert not bool(reloaded.isna().to_numpy().any())
    assert reloaded["close"].tolist() == [10.2, 10.4, 10.8]


def test_ingest_hard_error_aborts_without_processed_output(tmp_path):
    # high(9.5) < low(10.2)：硬错误，不可自动修复
    bad_csv = (
        "date,open,high,low,close,volume\n"
        "2024-01-02,9.0,9.5,10.2,10.0,1000\n"
    )
    src = _write(tmp_path, bad_csv, name="bad.csv")
    data_root = tmp_path / "data"

    with pytest.raises(DataValidationError):
        ingest_daily_csv(src, symbol="BAD", data_root=data_root)

    assert not (data_root / "processed" / "BAD.daily.parquet").exists()
