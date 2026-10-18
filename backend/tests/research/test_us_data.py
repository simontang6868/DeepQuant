"""美股股债利差包：数据层的单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread_us.data import (
    build_derived,
    load_raw_csv,
    month_end,
    sha256_file,
    slice_period,
    validate,
)


@pytest.fixture
def tiny_frame() -> pd.DataFrame:
    idx = pd.date_range("2000-01-31", periods=6, freq="ME")
    return pd.DataFrame(
        {
            "price": [100.0, 110.0, 121.0, 110.0, 121.0, 133.1],
            "cape": [20.0, 22.0, 24.4, 22.0, 24.2, 26.6],
            "div_yield": [2.0] * 6,
            "cpi": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "earnings": [5.0, 5.0, 5.0, 5.0, 5.0, 5.0],
            "y10": [4.0, 4.0, 4.0, 4.0, 4.0, 4.0],
        },
        index=idx,
    )


def test_month_end_normalizes_mid_month():
    idx = pd.to_datetime(["2020-01-01", "2020-02-14"])
    out = month_end(idx)
    assert [d.day for d in out] == [31, 29]
    assert [d.month for d in out] == [1, 2]


def test_yields_and_spread(tiny_frame):
    df = build_derived(tiny_frame)
    assert df["ey_cape"].iloc[0] == pytest.approx(5.0)
    assert df["spread_cape"].iloc[0] == pytest.approx(1.0)
    assert df["pe1"].iloc[0] == pytest.approx(20.0)
    assert df["ey_pe1"].iloc[0] == pytest.approx(5.0)


def test_total_return_uses_previous_month_dividend(tiny_frame):
    df = build_derived(tiny_frame)
    # 第 2 个月：上月价格 100、上月股息率 2% → 月度股息 = 100*0.02/12
    expected = (110.0 + 100.0 * 0.02 / 12.0) / 100.0 - 1.0
    assert df["ret"].iloc[1] == pytest.approx(expected)
    assert np.isnan(df["ret"].iloc[0])


def test_real_return_deflates_by_cpi(tiny_frame):
    df = build_derived(tiny_frame)
    nominal = df["ret"].iloc[1]
    inflation = df["infl"].iloc[1]
    assert df["real_ret"].iloc[1] == pytest.approx((1 + nominal) / (1 + inflation) - 1)


def test_real_index_starts_at_one(tiny_frame):
    df = build_derived(tiny_frame)
    assert df["real_tr_index"].iloc[0] == pytest.approx(1.0)
    assert df["tr_index"].iloc[0] == pytest.approx(1.0)


def test_validate_reports_gaps(tiny_frame):
    df = build_derived(tiny_frame).drop(tiny_frame.index[2])
    checks = validate(df)
    assert checks["n_months"] == 5
    assert checks["missing_months"] == ["2000-03-31"]
    assert checks["monotonic"] is True


def test_slice_period_inclusive():
    idx = pd.date_range("2000-01-31", periods=6, freq="ME")
    df = pd.DataFrame({"x": range(6)}, index=idx)
    out = slice_period(df, "2000-02-29", "2000-04-30")
    assert len(out) == 3
    assert out.index[0] == pd.Timestamp("2000-02-29")
    assert out.index[-1] == pd.Timestamp("2000-04-30")


def test_sha256_file_is_stable(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"deepquant")
    h1 = sha256_file(p)
    assert len(h1) == 64
    assert h1 == sha256_file(p)


def test_load_raw_csv_requires_month_column(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("date,price\n2020-01-01,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_raw_csv(p)
