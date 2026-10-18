"""数据层测试：日期匹配、排序去重、缺失值、异常 PE、样本不足告警。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.config import DataConfig
from deepquant.research.equity_bond_spread.data import (
    PANEL_COLUMNS,
    build_panel,
    load_bond_csv,
    load_equity_csv,
    match_bond,
    sha256_file,
    validate_panel,
)


def _write_equity(tmp_path, rows, header="date,price,pe,pb,div_yield"):
    p = tmp_path / "eq.csv"
    p.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 5. 排序与重复日期


def test_load_equity_sorts_and_dedups(tmp_path):
    p = _write_equity(tmp_path, [
        "2010-03-31,103,11,1.1,2.0",
        "2010-01-31,101,10,1.0,2.0",
        "2010-02-28,102,10.5,1.05,2.0",
        "2010-02-28,999,99,9.9,9.9",  # 重复日期，保留最后一条
    ])
    df, rep = load_equity_csv(p)
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates
    assert rep.n_duplicate_date == 1
    assert len(df) == 3
    assert float(df["price"].iloc[1]) == pytest.approx(999.0)


def test_load_equity_drops_unparsable_dates(tmp_path):
    p = _write_equity(tmp_path, [
        "not-a-date,100,10,1,2",
        "2010-01-31,101,10,1,2",
    ])
    df, rep = load_equity_csv(p)
    assert rep.n_bad_date == 1
    assert len(df) == 1


def test_load_equity_requires_date_column(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("price,pe\n100,10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="date"):
        load_equity_csv(p)


# ---------------------------------------------------------------- 6. 缺失值


def test_missing_values_stay_missing_no_interpolation(tmp_path):
    p = _write_equity(tmp_path, [
        "2010-01-31,100,10,1.0,2.0",
        "2010-02-28,,10.5,1.05,2.0",
        "2010-03-31,103,,1.1,2.0",
        "2010-04-30,104,10.8,1.1,",
    ])
    df, rep = load_equity_csv(p)
    assert pd.isna(df["price"].iloc[1])
    assert pd.isna(df["pe"].iloc[2])
    assert pd.isna(df["div_yield"].iloc[3])
    # 覆盖率记录必须反映缺失
    assert rep.missing_rate["price"] == pytest.approx(0.25)
    assert rep.missing_rate["pe"] == pytest.approx(0.25)
    assert rep.missing_rate["div_yield"] == pytest.approx(0.25)


# ---------------------------------------------------------------- 7. 异常 PE


@pytest.mark.parametrize("bad_pe", ["0", "-5", "100000", "abc"])
def test_invalid_pe_becomes_nan(tmp_path, bad_pe):
    p = _write_equity(tmp_path, [
        f"2010-01-31,100,{bad_pe},1.0,2.0",
        "2010-02-28,101,12,1.0,2.0",
    ])
    df, rep = load_equity_csv(p)
    assert pd.isna(df["pe"].iloc[0]), f"PE={bad_pe} 必须置为 NaN"
    assert rep.n_out_of_range["pe_invalid"] >= 1
    assert df["pe"].iloc[1] == pytest.approx(12.0)


def test_pb_zero_treated_as_missing(tmp_path):
    """行情源用 0 表示缺失，必须转成 NaN 而不是当真值用。"""
    p = _write_equity(tmp_path, ["2010-01-31,100,10,0,2.0"])
    df, _ = load_equity_csv(p)
    assert pd.isna(df["pb"].iloc[0])


# ---------------------------------------------------------------- 4. 国债日期匹配


def test_match_bond_exact_rule():
    idx = pd.to_datetime(["2010-01-31", "2010-02-28"])
    bond = pd.Series([3.5, 3.6],
                     index=pd.to_datetime(["2010-01-29", "2010-02-26"]))
    out, stats = match_bond(idx, bond, DataConfig(bond_match_rule="exact"))
    assert stats["n_exact"] == 0
    assert out.isna().all()


def test_match_bond_forward_fill_within_limit():
    idx = pd.to_datetime(["2010-01-31", "2010-02-28"])
    bond = pd.Series([3.5, 3.6],
                     index=pd.to_datetime(["2010-01-29", "2010-02-26"]))
    out, stats = match_bond(idx, bond, DataConfig(bond_match_rule="forward_fill",
                                                  bond_max_fill_days=10))
    assert out.iloc[0] == pytest.approx(3.5)
    assert out.iloc[1] == pytest.approx(3.6)
    assert stats["n_filled"] == 2


def test_match_bond_never_uses_future_values():
    """观测日之前的缺失，不得用观测日之后的数据回填。"""
    idx = pd.to_datetime(["2010-01-31"])
    bond = pd.Series([3.9], index=pd.to_datetime(["2010-02-05"]))  # 未来
    out, stats = match_bond(idx, bond, DataConfig(bond_match_rule="forward_fill",
                                                  bond_max_fill_days=30))
    assert pd.isna(out.iloc[0])
    assert stats["n_missing"] == 1


def test_match_bond_respects_max_fill_days():
    idx = pd.to_datetime(["2010-03-31"])
    bond = pd.Series([3.0], index=pd.to_datetime(["2010-01-05"]))  # 相隔 85 天
    out, stats = match_bond(idx, bond, DataConfig(bond_match_rule="forward_fill",
                                                  bond_max_fill_days=10))
    assert pd.isna(out.iloc[0])
    assert stats["n_missing"] == 1


def test_load_bond_filters_out_of_range(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("date,yield_10y\n2010-01-04,3.5\n2010-01-05,999\n2010-01-06,2.9\n",
                 encoding="utf-8")
    s, rep = load_bond_csv(p)
    assert pd.isna(s.iloc[1])
    assert s.iloc[0] == pytest.approx(3.5)
    assert rep.n_out_of_range["bond_out_of_range"] == 1


# ---------------------------------------------------------------- 16. 样本不足告警


def test_validate_panel_insufficient_obs(tmp_path):
    p = _write_equity(tmp_path, ["2010-01-31,100,10,1.0,2.0"])
    b = tmp_path / "b.csv"
    b.write_text("date,yield_10y\n2010-01-04,3.5\n", encoding="utf-8")
    panel = build_panel(p, b, DataConfig(bond_max_fill_days=60))
    rep = validate_panel(panel, min_obs=24)
    assert "insufficient_obs" in rep.error_codes()
    assert not rep.is_valid


def test_validate_panel_missing_bond_is_error(tmp_path):
    p = _write_equity(tmp_path, ["2010-01-31,100,10,1.0,2.0"] * 5)
    b = tmp_path / "b.csv"
    b.write_text("date,yield_10y\n2010-01-04,3.5\n", encoding="utf-8")
    panel = build_panel(p, b, DataConfig(bond_match_rule="exact"))
    rep = validate_panel(panel, min_obs=1)
    assert "missing_bond" in rep.error_codes()


def test_validate_panel_ok(tmp_path):
    rows = [f"2010-{m:02d}-28,10{m},10,1.0,2.0" for m in range(1, 13)]
    p = _write_equity(tmp_path, rows)
    b = tmp_path / "b.csv"
    b.write_text(
        "date,yield_10y\n" + "\n".join(f"2010-{m:02d}-26,3.{m}" for m in range(1, 13)),
        encoding="utf-8",
    )
    panel = build_panel(p, b, DataConfig(bond_max_fill_days=10))
    rep = validate_panel(panel, min_obs=1)
    assert rep.is_valid, [i.code for i in rep.issues]


def test_panel_columns_and_coverage(tmp_path):
    rows = [f"2010-{m:02d}-28,10{m},10,1.0,2.0" for m in range(1, 7)]
    p = _write_equity(tmp_path, rows)
    b = tmp_path / "b.csv"
    b.write_text(
        "date,yield_10y\n" + "\n".join(f"2010-{m:02d}-26,3.{m}" for m in range(1, 7)),
        encoding="utf-8",
    )
    panel = build_panel(p, b, DataConfig(bond_max_fill_days=10))
    assert tuple(panel.frame.columns) == PANEL_COLUMNS
    cov = panel.coverage()
    assert cov["n_obs"] == 6
    assert cov["coverage_bond_10y"] == pytest.approx(1.0)


def test_sha256_is_stable_and_raises_when_missing(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello", encoding="utf-8")
    h1 = sha256_file(p)
    h2 = sha256_file(p)
    assert h1 == h2
    assert len(h1) == 64
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "nope.txt")


def test_start_before_bond_data_leaves_nan(tmp_path):
    """国债曲线起始之前，股债利差必须是 NaN，不能用后数据回填。"""
    p = _write_equity(tmp_path, [
        "2005-01-31,100,10,1.0,2.0",
        "2010-01-31,101,10.5,1.0,2.0",
    ])
    b = tmp_path / "b.csv"
    # 国债数据从 2010-01-29 才开始，2005 年的股票观测无债收益率可匹配
    b.write_text("date,yield_10y\n2010-01-29,3.5\n", encoding="utf-8")
    panel = build_panel(p, b, DataConfig(bond_max_fill_days=10))
    assert pd.isna(panel.frame["bond_10y"].iloc[0])
    assert panel.frame["bond_10y"].iloc[1] == pytest.approx(3.5)
