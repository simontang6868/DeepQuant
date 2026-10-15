"""行情校验层测试。"""

import pandas as pd

from deepquant.data.validation.checks import ERROR, WARNING, validate_daily


def _frame(**overrides) -> pd.DataFrame:
    """构造一份可通过校验的两行日线数据，可用 overrides 注入问题。"""
    data = {
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [10.0, 10.2],
        "high": [10.5, 10.6],
        "low": [9.8, 10.0],
        "close": [10.2, 10.4],
        "volume": [1000.0, 1200.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_clean_data_passes():
    report = validate_daily(_frame())
    assert report.is_valid
    assert report.issues == []


def test_duplicate_date_is_warning():
    df = _frame(date=pd.to_datetime(["2024-01-02", "2024-01-02"]))
    report = validate_daily(df)
    assert report.is_valid  # 可通过清洗解决
    issue = report.get("duplicate_dates")
    assert issue is not None
    assert issue.level == WARNING
    assert issue.count == 1


def test_unsorted_dates_is_warning():
    df = _frame(date=pd.to_datetime(["2024-01-03", "2024-01-02"]))
    assert validate_daily(df).has_code("unsorted_dates")


def test_missing_values_is_warning():
    df = _frame(close=[10.2, None])
    report = validate_daily(df)
    assert report.is_valid
    issue = report.get("missing_values")
    assert issue is not None
    assert issue.count == 1


def test_ohlc_relation_violation_is_error():
    df = _frame(high=[9.0, 10.6])  # high < low，矛盾
    report = validate_daily(df)
    assert not report.is_valid
    issue = report.get("ohlc_relation")
    assert issue is not None and issue.level == ERROR


def test_negative_price_is_error():
    df = _frame(open=[-1.0, 10.2])
    report = validate_daily(df)
    assert not report.is_valid
    assert report.has_code("invalid_price")


def test_negative_volume_is_error():
    df = _frame(volume=[-5.0, 1200.0])
    assert validate_daily(df).has_code("negative_volume")


def test_unparsable_date_is_error():
    df = _frame(date=pd.to_datetime([None, "2024-01-03"]))
    report = validate_daily(df)
    assert not report.is_valid
    assert report.has_code("unparsable_date")


def test_missing_column_is_error():
    df = _frame().drop(columns=["volume"])
    report = validate_daily(df)
    assert not report.is_valid
    assert report.has_code("missing_columns")


def test_empty_data_is_error():
    report = validate_daily(_frame().iloc[0:0])
    assert not report.is_valid
    assert report.has_code("empty")
