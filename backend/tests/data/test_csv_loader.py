"""CSV 导入层测试。"""

import pytest

from deepquant.data.loaders.csv_loader import DataLoadError, load_daily_csv

CLEAN_CSV = (
    "date,open,high,low,close,volume\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
)


def _write(tmp_path, text, name="sample.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_normalizes_columns_and_sorts(tmp_path):
    # 列名大小写混乱、行顺序颠倒，应被规范化为小写并按日期升序
    text = (
        "Date,Open,HIGH,low,Close,Volume\n"
        "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
        "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    )
    df = load_daily_csv(_write(tmp_path, text))
    assert list(df.columns)[:6] == ["date", "open", "high", "low", "close", "volume"]
    assert df["date"].is_monotonic_increasing
    assert df["close"].tolist() == [10.2, 10.4]


def test_load_parses_dates_and_numbers(tmp_path):
    df = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    assert str(df["date"].dtype).startswith("datetime64")
    assert df["volume"].tolist() == [1000, 1200]


def test_load_unparsable_values_become_missing(tmp_path):
    # 非法日期/价格应转为 NaT/NaN 交给校验层，而不是直接抛异常
    text = (
        "date,open,high,low,close,volume\n"
        "not-a-date,10.0,10.5,9.8,10.2,1000\n"
        "2024-01-03,abc,10.6,10.0,10.4,1200\n"
    )
    df = load_daily_csv(_write(tmp_path, text))
    assert int(df["date"].isna().sum()) == 1
    assert int(df["open"].isna().sum()) == 1


def test_load_missing_column_raises(tmp_path):
    text = "date,open,high,low,close\n2024-01-02,10.0,10.5,9.8,10.2\n"
    with pytest.raises(DataLoadError):
        load_daily_csv(_write(tmp_path, text))


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(DataLoadError):
        load_daily_csv(tmp_path / "nope.csv")
