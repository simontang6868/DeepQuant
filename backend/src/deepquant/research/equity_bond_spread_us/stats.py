"""统计工具：前视收益、Newey-West 推断、块自举、有效样本与功效。

为什么不用普通的 t 检验
------------------------
重叠的前视窗口（例如每月都算未来 60 个月的收益）会让相邻观测高度相关，
朴素 t 检验会把「1860 个相关观测」当成「1860 个独立观测」，
p 值被系统性低估。本模块提供三种纠正：

1. **Newey-West 标准误**：滞后阶数按 1.5 倍窗口取，吸收重叠导致的自相关；
2. **循环块自举（circular block bootstrap）**：对预测变量与收益**分别**按块重采样，
   保留各自的自相关、打断两者的关联，得到「无预测能力」零假设下的分布；
3. **有效样本量**：``n_eff = n // h``，即非重叠观测数；用于功效计算，
   回答「这份数据最多能检测到多大的效应」。

三者结论不一致时，报告以更保守的为准。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 前视收益
def forward_return(index_series: pd.Series, horizon: int, exec_lag: int = 1) -> pd.Series:
    """信号月 t 对应的前视收益：t+exec_lag 买入，持有 horizon 个月。

    返回值的索引是**信号月 t**，值是**执行月之后再持有 horizon 个月**的累计收益。
    末尾不足的月份为 NaN（不截断、不填充）。
    """
    if horizon <= 0:
        raise ValueError("horizon 必须 > 0")
    if exec_lag < 1:
        raise ValueError("exec_lag 必须 >= 1（禁止同月成交）")
    base = index_series.shift(-exec_lag)
    end = index_series.shift(-(exec_lag + horizon))
    return (end / base - 1.0).rename(f"fwd{horizon}")


def path_drawdown(index_series: pd.Series, horizon: int, exec_lag: int = 1) -> pd.Series:
    """每个信号月之后、持有期内的最大回撤（按月末观测）。"""
    vals = index_series.to_numpy(dtype=float)
    n = len(vals)
    out = np.full(n, np.nan)
    for i in range(n):
        start = i + exec_lag
        end = start + horizon
        if end >= n:
            continue
        seg = vals[start : end + 1]
        peak = np.maximum.accumulate(seg)
        out[i] = float(np.min(seg / peak - 1.0))
    return pd.Series(out, index=index_series.index, name=f"dd{horizon}")


# ---------------------------------------------------------------- 描述统计
def summarize_returns(values: pd.Series | np.ndarray) -> dict:
    """前视收益的描述统计（收益为小数，输出百分数便于阅读）。"""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {"n": 0}
    return {
        "n": int(len(arr)),
        "mean_pct": float(arr.mean() * 100.0),
        "median_pct": float(np.median(arr) * 100.0),
        "std_pct": float(arr.std(ddof=1) * 100.0) if len(arr) > 1 else float("nan"),
        "min_pct": float(arr.min() * 100.0),
        "max_pct": float(arr.max() * 100.0),
        "positive_share_pct": float((arr > 0).mean() * 100.0),
    }


def max_drawdown(index_series: pd.Series) -> float:
    """指数序列的最大回撤（负数，小数）。"""
    vals = index_series.to_numpy(dtype=float)
    peak = np.maximum.accumulate(vals)
    return float(np.min(vals / peak - 1.0))


def annualized_return(total_return: float, years: float) -> float:
    """由区间累计收益换算年化（小数）。years 必须为正。"""
    if years <= 0:
        raise ValueError("years 必须 > 0")
    if total_return <= -1:
        return -1.0
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def annualized_vol(monthly_returns: pd.Series) -> float:
    """月度收益换算年化波动（简单年化，不做自相关调整）。"""
    arr = np.asarray(monthly_returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return float("nan")
    return float(arr.std(ddof=1) * np.sqrt(12))


# ---------------------------------------------------------------- 相关与回归
def spearman_ic(x, y) -> float:
    """Spearman 秩相关（对离群值稳健，用于「是否单调相关」）。

    接受 ``pd.Series`` 或 numpy 数组（块自举里以数组形式调用）。
    """
    if not isinstance(x, pd.Series):
        x = pd.Series(np.asarray(x, dtype=float))
    if not isinstance(y, pd.Series):
        y = pd.Series(np.asarray(y, dtype=float))
    j = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    if len(j) < 3:
        return float("nan")
    return float(j["x"].rank().corr(j["y"].rank()))


@dataclass
class OLSResult:
    """带 Newey-West 标准误的 OLS 结果。"""

    names: list[str]
    params: np.ndarray
    se: np.ndarray
    tstat: np.ndarray
    n: int
    lag: int
    r2: float

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "lag": self.lag,
            "r2": round(self.r2, 6),
            "coef": {
                name: {
                    "param": float(self.params[i]),
                    "se": float(self.se[i]),
                    "t": float(self.tstat[i]),
                }
                for i, name in enumerate(self.names)
            },
        }


def nw_ols(y: np.ndarray | pd.Series, X: np.ndarray | pd.DataFrame, lag: int,
           names: list[str] | None = None, add_const: bool = True) -> OLSResult:
    """OLS + Newey-West(Bartlett) 标准误。

    ``X`` 为 (n, k)；``add_const=True`` 时自动加截距（截距不进 ``names`` 之外的解读）。
    """
    yv = np.asarray(y, dtype=float).reshape(-1)
    Xv = np.asarray(X, dtype=float)
    if Xv.ndim == 1:
        Xv = Xv.reshape(-1, 1)
    if len(yv) != len(Xv):
        raise ValueError(f"y 与 X 长度不一致：{len(yv)} vs {len(Xv)}")
    mask = np.isfinite(yv) & np.isfinite(Xv).all(axis=1)
    yv, Xv = yv[mask], Xv[mask]
    n = len(yv)
    if n < 10:
        raise ValueError(f"有效样本过少：{n}")
    if add_const:
        Xv = np.column_stack([np.ones(n), Xv])
        names = (["const"] + (names or [])) if names else None

    xtx = Xv.T @ Xv / n
    xtx_inv = np.linalg.pinv(xtx)
    beta = xtx_inv @ (Xv.T @ yv / n)
    resid = yv - Xv @ beta

    # 得分向量 g_t = x_t * u_t
    g = Xv * resid.reshape(-1, 1)
    S = (g.T @ g) / n
    for j in range(1, lag + 1):
        w = 1.0 - j / (lag + 1.0)
        gamma = (g[j:].T @ g[:-j]) / n
        S += w * (gamma + gamma.T)

    cov = xtx_inv @ S @ xtx_inv / n
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    tstat = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)

    ss_res = float(resid @ resid)
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    if names is None:
        names = [f"x{i}" for i in range(Xv.shape[1])]
    return OLSResult(names=names, params=beta, se=se, tstat=tstat, n=n, lag=lag, r2=r2)


# ---------------------------------------------------------------- 块自举
def circular_block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """循环块自举的索引序列（长度 n）。"""
    if block <= 0:
        raise ValueError("block 必须 > 0")
    if block >= n:
        return rng.permutation(n)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, n_blocks)
    idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])
    return idx[:n]


def block_bootstrap_pvalue(
    x: np.ndarray,
    y: np.ndarray,
    statistic,
    block: int,
    n_boot: int = 5000,
    seed: int = 0,
) -> dict:
    """「两者无关」零假设下的块自举 p 值。

    ``x`` 与 ``y`` **各自独立**按块重采样：保留各自的序列相关结构，
    打断两者的关联。统计量取绝对值做双侧检验。
    """
    xv = np.asarray(x, dtype=float)
    yv = np.asarray(y, dtype=float)
    mask = np.isfinite(xv) & np.isfinite(yv)
    xv, yv = xv[mask], yv[mask]
    n = len(xv)
    obs = float(statistic(xv, yv))
    if not np.isfinite(obs) or n < 12:
        return {"obs": obs, "p_value": float("nan"), "n": n, "n_boot": 0, "block": block}

    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot)
    for b in range(n_boot):
        ix = circular_block_indices(n, block, rng)
        iy = circular_block_indices(n, block, rng)
        boot[b] = statistic(xv[ix], yv[iy])

    p = (1.0 + float((np.abs(boot) >= abs(obs)).sum())) / (n_boot + 1.0)
    return {
        "obs": obs,
        "p_value": float(p),
        "n": n,
        "n_boot": int(n_boot),
        "block": int(block),
        "boot_mean": float(np.mean(boot)),
        "boot_p05": float(np.quantile(boot, 0.05)),
        "boot_p95": float(np.quantile(boot, 0.95)),
    }


# ---------------------------------------------------------------- 功效
def effective_sample(n: int, horizon: int) -> int:
    """非重叠观测数（保守的有效样本量）。"""
    return max(1, int(n // max(1, horizon)))


def min_detectable_ic(n_eff: int, alpha: float = 0.05, power: float = 0.80) -> float:
    """在给定有效样本下，80% 功效能检测到的最小 |相关系数|（Fisher z 近似）。"""
    if n_eff <= 3:
        return float("nan")
    from statistics import NormalDist

    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    z = (z_alpha + z_power) / np.sqrt(n_eff - 3)
    return float(np.tanh(z))


def oos_r2(y_true: np.ndarray, y_pred: np.ndarray, y_bench: np.ndarray) -> float:
    """Campbell-Thompson 样本外 R²：1 - MSFE(model) / MSFE(历史均值基准)。"""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    yb = np.asarray(y_bench, dtype=float)
    mask = np.isfinite(yt) & np.isfinite(yp) & np.isfinite(yb)
    if mask.sum() < 10:
        return float("nan")
    msfe_m = float(np.mean((yt[mask] - yp[mask]) ** 2))
    msfe_b = float(np.mean((yt[mask] - yb[mask]) ** 2))
    if msfe_b <= 0:
        return float("nan")
    return 1.0 - msfe_m / msfe_b


def bond_real_return(frame: pd.DataFrame, horizon: int, exec_lag: int = 1) -> pd.Series:
    """债券侧的真实收益近似（用于「相对收益」附录检验）。

    做法：执行月 t+exec_lag 的 10 年期收益率 y 视为滚动持有 10 年国债的年化票息，
    持有 horizon 个月的名义收益 ≈ (1 + y/100)^(h/12) - 1，再用同期**已实现** CPI
    通胀折算成真实收益。

    这不是债券总收益指数：
    - 忽略了久期带来的资本利得/损失（收益率下行时低估、上行时高估）；
    - 忽略了票息再投资路径。
    在 5-10 年窗口上，这类误差通常在每年 1 个百分点量级，且无系统性方向，
    因此只用于判断「利差是不是一个相对价值信号」，不用于计算策略收益。
    """
    if horizon <= 0:
        raise ValueError("horizon 必须 > 0")
    y = frame["y10"].shift(-exec_lag) / 100.0
    nominal = (1.0 + y) ** (horizon / 12.0) - 1.0
    infl = frame["cpi"].shift(-(exec_lag + horizon)) / frame["cpi"].shift(-exec_lag) - 1.0
    return ((1.0 + nominal) / (1.0 + infl) - 1.0).rename(f"bond_real_{horizon}")


def excess_real_return(frame: pd.DataFrame, horizon: int, exec_lag: int = 1) -> pd.Series:
    """股票真实收益 − 债券真实收益（近似），索引仍是信号月 t。"""
    eq = forward_return(frame["real_tr_index"], horizon, exec_lag)
    bd = bond_real_return(frame, horizon, exec_lag)
    return ((1.0 + eq) / (1.0 + bd) - 1.0).rename(f"excess_real_{horizon}")


def expanded_window_slope_forecast(
    x: pd.Series, y: pd.Series, min_train: int
) -> pd.Series:
    """扩展窗口滚动回归的样本外预测（严格只用 t 之前的数据估计系数）。"""
    xv = x.to_numpy(dtype=float)
    yv = y.to_numpy(dtype=float)
    n = len(xv)
    pred = np.full(n, np.nan)
    for i in range(n):
        if i < min_train:
            continue
        xs, ys = xv[:i], yv[:i]
        mask = np.isfinite(xs) & np.isfinite(ys)
        if mask.sum() < min_train:
            continue
        xs, ys = xs[mask], ys[mask]
        xm, ym = xs.mean(), ys.mean()
        denom = float(((xs - xm) ** 2).sum())
        if denom <= 0 or not np.isfinite(xv[i]):
            continue
        beta = float(((xs - xm) * (ys - ym)).sum()) / denom
        alpha = ym - beta * xm
        pred[i] = alpha + beta * xv[i]
    return pd.Series(pred, index=x.index)
