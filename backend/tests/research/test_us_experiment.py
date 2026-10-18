"""美股股债利差包：实验流程测试（用构造数据，不依赖真实文件）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread_us.config import USConfig
from deepquant.research.equity_bond_spread_us.data import build_derived
from deepquant.research.equity_bond_spread_us.experiment import (
    prepare_frame,
    run_study,
    sample_slice,
)


def _synthetic(n: int = 300, seed: int = 0) -> pd.DataFrame:
    """构造一段「估值越高、未来收益越低」的可预测数据。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("1900-01-31", periods=n, freq="ME")
    cape = 15.0 + np.cumsum(rng.normal(scale=0.15, size=n))
    cape = np.clip(cape, 5.0, 45.0)
    drift = 0.02 - 0.001 * (cape - 15.0)
    price = 100.0 * np.cumprod(1.0 + drift / 12.0 + rng.normal(scale=0.03, size=n))
    df = pd.DataFrame(
        {
            "price": price,
            "cape": cape,
            "div_yield": 100.0 / cape * 0.5,
            "cpi": 100.0 * np.cumprod(1.0 + rng.normal(scale=0.002, size=n) + 0.002),
            "earnings": price / cape,
            "y10": 4.0 + rng.normal(scale=0.3, size=n),
        },
        index=idx,
    )
    return build_derived(df)


def test_prepare_frame_adds_percentiles_and_forwards():
    df = _synthetic()
    cfg = USConfig(horizons=(12, 60), n_boot=100)
    out = prepare_frame(df, cfg)
    assert "spread_cape_pct" in out.columns
    assert "fwd_real_60" in out.columns
    assert out["fwd_real_60"].isna().sum() >= 60


def test_sample_slice_keeps_window_inside_is():
    df = _synthetic(400)
    cfg = USConfig(horizons=(12, 60), is_end_date="1920-12-31", n_boot=100)
    prepared = prepare_frame(df, cfg)
    seg = sample_slice(prepared, cfg, "is", 60)
    assert seg.index.max() < pd.Timestamp("1920-12-31") - pd.DateOffset(months=60)


def test_sample_slice_oos_starts_after_is_end():
    df = _synthetic(400)
    cfg = USConfig(horizons=(12, 60), is_end_date="1920-12-31", n_boot=100)
    prepared = prepare_frame(df, cfg)
    seg = sample_slice(prepared, cfg, "oos", 60)
    assert seg.index.min() > pd.Timestamp("1920-12-31")


def test_run_study_shapes():
    df = _synthetic(360)
    cfg = USConfig(horizons=(12, 60), primary_horizon=60, n_boot=200,
                   is_end_date="1919-12-31", start_date="1900-01-31",
                   end_date="1929-12-31")
    res = run_study(df, cfg)
    assert len(res.ic) == 4 * 2 * 3          # 4 预测变量 × 2 窗口 × 3 样本段
    assert res.deciles and len(res.deciles) == 2
    assert res.tests_counted > 0
    assert all("ic" in r for r in res.ic)
    # 构造数据里估值与未来收益负相关，IC 应为负
    r60 = next(r for r in res.ic if r["predictor"] == "cape" and r["horizon"] == 60
               and r["sample"] == "full")
    assert r60["ic"] is not None and r60["ic"] < 0


def test_run_study_reports_insufficient_sample():
    df = _synthetic(140)
    cfg = USConfig(horizons=(120,), primary_horizon=120, n_boot=100,
                   start_date="1900-01-31", end_date="1911-12-31", is_end_date="1905-12-31")
    res = run_study(df, cfg)
    # 样本内段不足以容纳 120 个月持有期 → 明确标注，不抛异常
    assert any(r.get("note") == "样本不足" for r in res.ic)


def test_config_rejects_bad_parameters():
    with pytest.raises(ValueError):
        USConfig(exec_lag=0)
    with pytest.raises(ValueError):
        USConfig(primary_horizon=24)
    with pytest.raises(ValueError):
        USConfig(threshold_pct=0)
    with pytest.raises(ValueError):
        USConfig(is_end_date="1870-01-31")
