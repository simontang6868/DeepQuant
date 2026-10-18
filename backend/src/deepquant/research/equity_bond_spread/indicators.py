"""股债利差研究：指标计算。

所有函数都是纯函数（输入 Series/DataFrame，输出 Series），不就地修改入参，
且不读取任何"未来"数据——窗口一律只取 ``t`` 及其之前的值。

口径说明（重要）：
- 假设 A 把「过去 H 年的**已实现**股票年化收益」与「当期 10 年期国债
  **到期收益率**」相减。前者是历史实现值，后者是当期预期值，两者口径
  并不等价，该指标只是一个待检验的启发式指标，不是无偏的未来收益预测。
- 假设 B 的盈利收益率 = 1 / PE_TTM，是**估值倒数**，不等于股票投资回报率。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import IndicatorType, PercentileWindow, ResearchConfig, ReturnBasis


def rolling_annualized_return(
    prices: pd.Series,
    lookback_obs: int,
    *,
    obs_per_year: int = 252,
    basis: ReturnBasis = ReturnBasis.PRICE,
    div_yield: pd.Series | None = None,
) -> pd.Series:
    """过去 ``lookback_obs`` 个观测的股票年化收益率（百分数）。

    年化公式：``(P_t / P_{t-H}) ** (1 / years) - 1``，其中
    ``years = lookback_obs / obs_per_year``。

    Args:
        prices: 价格序列（价格指数口径）。
        lookback_obs: 回溯观测数（= 年数 × 每年观测数）。
        obs_per_year: 每年观测数（日 252 / 周 52 / 月 12）。
        basis: PRICE 仅价格收益；TOTAL_APPROX 为「价格收益 + 窗口内平均股息率」
            的**近似**总收益，必须在报告中标注为近似口径。
        div_yield: 股息率（百分数），basis=TOTAL_APPROX 时必需。

    Returns:
        年化收益率（百分数）；样本不足或非正价格处为 NaN。
    """
    if not isinstance(prices, pd.Series):
        raise TypeError(f"prices 必须是 pd.Series，收到 {type(prices)!r}")
    if lookback_obs <= 0:
        raise ValueError(f"lookback_obs 必须 > 0，收到 {lookback_obs}")
    if basis is ReturnBasis.TOTAL_APPROX and div_yield is None:
        raise ValueError("basis=TOTAL_APPROX 时必须提供 div_yield")

    years = annualized_return_years(lookback_obs, obs_per_year)
    p = prices.astype("float64").where(prices > 0)
    prev = p.shift(lookback_obs)
    valid = p.notna() & prev.notna() & (prev > 0)

    ann = pd.Series(np.nan, index=p.index, dtype="float64")
    ann[valid] = ((p[valid] / prev[valid]) ** (1.0 / years) - 1.0) * 100.0

    if basis is ReturnBasis.TOTAL_APPROX:
        d = div_yield.astype("float64").reindex(p.index)
        mean_div = d.rolling(lookback_obs, min_periods=max(1, lookback_obs // 2)).mean()
        ann = ann + mean_div.fillna(0.0)
        ann = ann.where(valid)
    ann.name = "stock_annual_return_pct"
    return ann


def annualized_return_years(lookback_obs: int, obs_per_year: int) -> float:
    """把回溯观测数折算为年数（年化公式的分母）。"""
    if obs_per_year <= 0:
        raise ValueError(f"obs_per_year 必须 > 0，收到 {obs_per_year}")
    return float(lookback_obs) / float(obs_per_year)


def earnings_yield(pe: pd.Series) -> pd.Series:
    """盈利收益率 = 1 / PE_TTM，返回百分数。

    PE 为 0、负值、缺失或非法时返回 NaN（已在数据层置 NaN，这里再兜一层）。
    """
    if not isinstance(pe, pd.Series):
        raise TypeError(f"pe 必须是 pd.Series，收到 {type(pe)!r}")
    p = pe.astype("float64")
    ok = p.notna() & (p > 0) & np.isfinite(p)
    ey = pd.Series(np.nan, index=p.index, dtype="float64")
    ey[ok] = 100.0 / p[ok]
    ey.name = "earnings_yield_pct"
    return ey


def compute_spread(
    panel: pd.DataFrame,
    cfg: ResearchConfig,
) -> pd.Series:
    """按配置计算股债利差（百分数）。

    假设 A：``R_stock(t,H) - R_bond(t)``
    假设 B：``EY(t) - R_bond(t)``
    """
    bond = panel["bond_10y"].astype("float64")
    if cfg.indicator is IndicatorType.A:
        obs_per_year = cfg.data.obs_per_year()
        lookback_obs = int(round(cfg.lookback_years * obs_per_year))
        years = annualized_return_years(lookback_obs, obs_per_year)
        p = panel["price"].astype("float64")
        prev = p.where(p > 0).shift(lookback_obs)
        valid = p.notna() & prev.notna() & (prev > 0)
        growth = pd.Series(np.nan, index=p.index, dtype="float64")
        growth[valid] = p[valid] / prev[valid]
        stock = pd.Series(np.nan, index=p.index, dtype="float64")
        stock[valid] = (growth[valid] ** (1.0 / years) - 1.0) * 100.0
        if cfg.return_basis is ReturnBasis.TOTAL_APPROX:
            d = panel["div_yield"].astype("float64")
            mean_div = d.rolling(lookback_obs, min_periods=max(1, lookback_obs // 2)).mean()
            stock = stock + mean_div.fillna(0.0)
            stock = stock.where(valid)
        spread = stock - bond
        spread.name = "spread_pct"
        return spread

    ey = earnings_yield(panel["pe"])
    spread = ey - bond
    spread.name = "spread_pct"
    return spread


def historical_percentile(
    values: pd.Series,
    *,
    window: PercentileWindow = PercentileWindow.EXPANDING,
    window_obs: int | None = None,
    min_history_obs: int = 1,
) -> pd.Series:
    """截至每个时点的历史百分位（0-100），**不使用未来数据**。

    Args:
        values: 指标序列，按时间升序。
        window: EXPANDING 用截至当前时点的全部历史；FIXED 只用最近
            ``window_obs`` 个观测。
        window_obs: FIXED 窗口的观测数。
        min_history_obs: 历史样本少于该值时输出 NaN，避免样本不足时给出
            看似精确的百分位。

    Returns:
        百分位序列；历史样本不足处为 NaN。
    """
    if not isinstance(values, pd.Series):
        raise TypeError(f"values 必须是 pd.Series，收到 {type(values)!r}")
    if window is PercentileWindow.FIXED:
        if window_obs is None or window_obs <= 0:
            raise ValueError(f"FIXED 窗口必须给出正的 window_obs，收到 {window_obs!r}")
    if min_history_obs < 1:
        raise ValueError(f"min_history_obs 必须 >= 1，收到 {min_history_obs}")

    v = values.astype("float64")
    out: list[float] = []
    history: list[float] = []

    for x in v.to_numpy(dtype="float64"):
        if np.isnan(x):
            # 当前值缺失 → 不产出百分位；但这不影响历史窗口的推进
            out.append(np.nan)
            history.append(np.nan)
            continue
        history.append(float(x))
        if window is PercentileWindow.FIXED:
            hist = history[-int(window_obs):]
        else:
            hist = history
        arr = np.array([h for h in hist if not np.isnan(h)], dtype="float64")
        if arr.size < min_history_obs:
            out.append(np.nan)
            continue
        # 百分位 = 历史中小于等于当前值的比例（含当前值本身）
        pct = float((arr <= x).sum()) / float(arr.size) * 100.0
        out.append(pct)

    res = pd.Series(out, index=v.index, name="percentile")
    return res


def build_indicator_frame(panel: pd.DataFrame, cfg: ResearchConfig) -> pd.DataFrame:
    """产出指标面板：利差、历史百分位、各中间量与有效标记。"""
    obs_per_year = cfg.data.obs_per_year()
    spread = compute_spread(panel, cfg)

    if cfg.percentile_window is PercentileWindow.FIXED:
        window_obs = int(round(cfg.percentile_fixed_years * obs_per_year))
    else:
        window_obs = None

    pct = historical_percentile(
        spread,
        window=cfg.percentile_window,
        window_obs=window_obs,
        min_history_obs=cfg.min_history_obs,
    )

    frame = pd.DataFrame(
        {
            "price": panel["price"],
            "pe": panel["pe"],
            "div_yield": panel["div_yield"],
            "bond_10y": panel["bond_10y"],
            "spread_pct": spread,
            "percentile": pct,
        }
    )
    if cfg.indicator is IndicatorType.A:
        lookback_obs = int(round(cfg.lookback_years * obs_per_year))
        frame["stock_annual_return_pct"] = rolling_annualized_return(
            panel["price"],
            lookback_obs,
            obs_per_year=obs_per_year,
            basis=cfg.return_basis,
            div_yield=panel["div_yield"],
        )
    else:
        frame["earnings_yield_pct"] = earnings_yield(panel["pe"])

    frame["history_obs"] = (
        spread.notna().cumsum().astype("float64")
        if cfg.percentile_window is PercentileWindow.EXPANDING
        else spread.notna().rolling(int(round(cfg.percentile_fixed_years * obs_per_year)),
                                    min_periods=1).sum().astype("float64")
    )
    frame["signal"] = (frame["percentile"] >= cfg.threshold_pct).astype("int64").where(
        frame["percentile"].notna(), other=pd.NA
    )
    return frame
