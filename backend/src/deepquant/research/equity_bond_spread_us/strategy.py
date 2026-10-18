"""把信号变成可执行规则的近似评估。

为什么不能直接拿「只在高分位做多」的净值去和买入持有比
------------------------------------------------------
阈值 90% 意味着策略只有约 10% 的时间在场内。仓位暴露完全不同，
净值高低只反映「有没有承担股票风险」，不反映「择时有没有用」。

因此这里做三件事：
1. 报告策略的**在场月份占比**、年化收益、波动、最大回撤；
2. 用**匹配暴露的随机基准**做对比：随机抽取同样多的在场月份（保留块结构），
   看策略的收益在随机分布里处于什么位置——这才是「择时是否有效」的检验；
3. 给出换手次数与成本敏感性，避免把「频繁进出」的成本忽略掉。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import stats
from .config import USConfig
from .signals import threshold_flag


def build_positions(frame: pd.DataFrame, cfg: USConfig, predictor: str = "spread_cape",
                    threshold: float | None = None) -> pd.Series:
    """信号 → 持仓权重（0/1），并按 exec_lag 顺延。

    权重 w_t 表示「第 t 月是否持有股票」，由 t-exec_lag 月的信号决定。
    """
    thr = cfg.threshold_pct if threshold is None else threshold
    pct_col = f"{predictor}_pct"
    flag = threshold_flag(frame[pct_col], thr).astype(float)
    return flag.shift(cfg.exec_lag).fillna(0.0).rename("weight")


def run_strategy(
    frame: pd.DataFrame,
    cfg: USConfig,
    predictor: str = "spread_cape",
    threshold: float | None = None,
    cost_per_switch: float = 0.0,
    real: bool = True,
) -> dict:
    """按规则回测（月度、无杠杆、空仓期真实收益记为 0）。"""
    weight = build_positions(frame, cfg, predictor, threshold)
    ret_col = "real_ret" if real else "ret"
    ret = frame[ret_col].fillna(0.0)

    switches = weight.diff().abs().fillna(weight.abs())
    strat_ret = weight * ret - switches * cost_per_switch
    curve = (1.0 + strat_ret).cumprod()
    bh_curve = (1.0 + ret).cumprod()

    n_months = len(frame)
    years = n_months / 12.0
    invested = float(weight.mean())
    return {
        "predictor": predictor,
        "threshold": threshold if threshold is not None else cfg.threshold_pct,
        "cost_per_switch": cost_per_switch,
        "n_months": int(n_months),
        "n_switch": int((switches > 0).sum()),
        "invested_share_pct": round(invested * 100.0, 2),
        "strategy_total_return_pct": round(float(curve.iloc[-1] - 1.0) * 100.0, 2),
        "strategy_cagr_pct": round(float(stats.annualized_return(curve.iloc[-1] - 1.0, years)) * 100.0, 2),
        "strategy_vol_pct": round(stats.annualized_vol(strat_ret) * 100.0, 2),
        "strategy_mdd_pct": round(stats.max_drawdown(curve) * 100.0, 2),
        "bh_cagr_pct": round(float(stats.annualized_return(bh_curve.iloc[-1] - 1.0, years)) * 100.0, 2),
        "bh_vol_pct": round(stats.annualized_vol(ret) * 100.0, 2),
        "bh_mdd_pct": round(stats.max_drawdown(bh_curve) * 100.0, 2),
        "return_per_invested_month_pct": round(
            float(strat_ret[weight > 0].mean() * 100.0), 3
        ) if (weight > 0).any() else None,
        "all_month_mean_ret_pct": round(float(ret.mean() * 100.0), 3),
        "_curve": curve,
    }


def matched_exposure_test(
    frame: pd.DataFrame,
    cfg: USConfig,
    result: dict,
    n_boot: int = 2000,
    block: int = 6,
) -> dict:
    """匹配暴露的随机基准：随机投放同样数量的在场月份（块抽样，保留连续性）。"""
    ret = frame["real_ret"].fillna(0.0).to_numpy(dtype=float)
    n = len(ret)
    k = int(round(result["invested_share_pct"] / 100.0 * n))
    if k <= 0 or k >= n:
        return {"k": k, "p_value": None, "note": "暴露极端，基准不适用"}
    rng = np.random.default_rng(cfg.seed + 7)
    years = n / 12.0
    cagrs = np.empty(n_boot)
    for b in range(n_boot):
        mask = np.zeros(n, dtype=bool)
        while mask.sum() < k:
            s = int(rng.integers(0, n))
            length = min(block, k - mask.sum())
            idx = (np.arange(s, s + length) % n)
            mask[idx] = True
        curve = float(np.prod(1.0 + ret * mask))
        cagrs[b] = stats.annualized_return(curve - 1.0, years)
    obs = result["strategy_cagr_pct"] / 100.0
    p = (1.0 + float((cagrs >= obs).sum())) / (n_boot + 1.0)
    return {
        "k_months": int(k),
        "n_boot": n_boot,
        "block": block,
        "rand_cagr_mean_pct": round(float(cagrs.mean()) * 100.0, 2),
        "rand_cagr_p05_pct": round(float(np.quantile(cagrs, 0.05)) * 100.0, 2),
        "rand_cagr_p95_pct": round(float(np.quantile(cagrs, 0.95)) * 100.0, 2),
        "p_value": round(p, 4),
    }
