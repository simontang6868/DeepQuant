"""股债利差研究：前瞻收益检验、基准比较、样本内外划分与敏感性。

核心可证伪设计：
- 信号事件的前瞻收益，与「同一时期、同样窗口、随机时点」的无条件前瞻收益
  分布直接比较。若两者没有系统性差异，则指标没有提供增量信息。
- 未来收益只使用执行时点之后的数据；观察窗口超出数据终点的样本标记为
  **未成熟**，不计入完整样本结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ResearchConfig
from .indicators import build_indicator_frame
from .signals import SpreadEvent, extract_events, trading_days_to_obs


def _path_stats(prices: np.ndarray) -> dict:
    """给定一段价格路径（含起点），计算 MDD / 最大有利 / 最大不利变动。"""
    p0 = float(prices[0])
    if p0 <= 0 or np.isnan(p0):
        return {"max_drawdown": np.nan, "max_favorable": np.nan, "max_adverse": np.nan}
    running_max = np.maximum.accumulate(prices)
    dd = prices / running_max - 1.0
    return {
        "max_drawdown": float(np.min(dd)),
        "max_favorable": float(np.max(prices) / p0 - 1.0),
        "max_adverse": float(np.min(prices) / p0 - 1.0),
    }


def summarize_returns(values: pd.Series, *, label: str = "") -> dict:
    """一组收益率的描述统计（用于信号组与基准组的直接对比）。"""
    v = pd.Series(values).dropna().astype("float64")
    n = int(v.size)
    out = {
        "label": label,
        "n": n,
        "mean": float(v.mean()) if n else None,
        "median": float(v.median()) if n else None,
        "std": float(v.std(ddof=1)) if n > 1 else None,
        "min": float(v.min()) if n else None,
        "max": float(v.max()) if n else None,
        "pct_positive": float((v > 0).mean()) if n else None,
    }
    if n == 0:
        out.update({"mean": None, "median": None, "min": None, "max": None,
                    "pct_positive": None, "std": None})
    return out


def forward_outcome(
    prices: pd.Series,
    exec_pos: int,
    window_obs: int,
    *,
    cost_rate: float = 0.0,
) -> dict:
    """从执行时点起，计算 ``window_obs`` 个观测后的收益与路径风险。

    Returns:
        dict：含 ``gross_return`` / ``net_return`` / ``max_drawdown`` /
        ``max_favorable`` / ``max_adverse`` / ``end_date`` / ``is_immature``。
    """
    n = len(prices)
    if exec_pos < 0 or exec_pos >= n:
        raise IndexError(f"exec_pos 越界：{exec_pos}（长度 {n}）")
    end_pos = exec_pos + window_obs
    if end_pos >= n:
        return {
            "gross_return": np.nan,
            "net_return": np.nan,
            "max_drawdown": np.nan,
            "max_favorable": np.nan,
            "max_adverse": np.nan,
            "end_date": None,
            "is_immature": True,
        }
    p0 = float(prices.iloc[exec_pos])
    p1 = float(prices.iloc[end_pos])
    seg = prices.iloc[exec_pos : end_pos + 1].to_numpy(dtype="float64")
    stats = _path_stats(seg)
    gross = p1 / p0 - 1.0 if p0 > 0 else np.nan
    net = (1.0 + gross) * (1.0 - cost_rate) - 1.0 if not np.isnan(gross) else np.nan
    return {
        "gross_return": float(gross) if not np.isnan(gross) else np.nan,
        "net_return": float(net) if not np.isnan(net) else np.nan,
        "max_drawdown": stats["max_drawdown"],
        "max_favorable": stats["max_favorable"],
        "max_adverse": stats["max_adverse"],
        "end_date": prices.index[end_pos],
        "is_immature": False,
    }


def build_baseline(frame: pd.DataFrame, cfg: ResearchConfig) -> pd.DataFrame:
    """无条件基准：从**每一个**有效观测出发的前瞻收益。

    这就是"随机时点买入"的分布，用来检验信号是否带来增量信息。
    """
    obs_per_year = cfg.data.obs_per_year()
    prices = frame["price"].astype("float64")
    rows = []
    for td in cfg.forward_windows_td:
        w = trading_days_to_obs(td, obs_per_year)
        for pos in range(len(prices)):
            if pd.isna(prices.iloc[pos]) or prices.iloc[pos] <= 0:
                continue
            out = forward_outcome(prices, pos, w, cost_rate=cfg.cost_rate)
            if out["is_immature"]:
                continue
            rows.append(
                {
                    "obs_date": prices.index[pos],
                    "window_td": td,
                    "window_obs": w,
                    "gross_return": out["gross_return"],
                    "net_return": out["net_return"],
                    "max_drawdown": out["max_drawdown"],
                    "max_favorable": out["max_favorable"],
                    "max_adverse": out["max_adverse"],
                }
            )
    return pd.DataFrame(rows)


def compute_event_outcomes(
    frame: pd.DataFrame,
    events: list[SpreadEvent],
    cfg: ResearchConfig,
) -> pd.DataFrame:
    """为每个信号事件计算各前瞻窗口的收益与风险。"""
    obs_per_year = cfg.data.obs_per_year()
    prices = frame["price"].astype("float64")
    index = {d: i for i, d in enumerate(prices.index)}

    rows = []
    for ev in events:
        base = ev.to_dict()
        if ev.exec_date is None or ev.exec_date not in index:
            row = dict(base)
            for td in cfg.forward_windows_td:
                row[f"ret_{td}td"] = np.nan
                row[f"dd_{td}td"] = np.nan
                row[f"mfe_{td}td"] = np.nan
                row[f"mae_{td}td"] = np.nan
                row[f"immature_{td}td"] = True
            row["immature_any"] = True
            rows.append(row)
            continue
        pos = index[ev.exec_date]
        row = dict(base)
        immature_any = False
        for td in cfg.forward_windows_td:
            w = trading_days_to_obs(td, obs_per_year)
            out = forward_outcome(prices, pos, w, cost_rate=cfg.cost_rate)
            row[f"ret_{td}td"] = out["net_return"]
            row[f"dd_{td}td"] = out["max_drawdown"]
            row[f"mfe_{td}td"] = out["max_favorable"]
            row[f"mae_{td}td"] = out["max_adverse"]
            row[f"immature_{td}td"] = out["is_immature"]
            immature_any = immature_any or out["is_immature"]
        row["immature_any"] = immature_any
        rows.append(row)

    df = pd.DataFrame(rows)
    # ``SpreadEvent.to_dict`` 为可序列化把日期转成了字符串；
    # 这里统一转回日期类型，便于后续比较、切分与导出。
    for col in ("signal_date", "last_signal_date", "exec_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def compare_to_baseline(
    event_df: pd.DataFrame,
    base_df: pd.DataFrame,
    cfg: ResearchConfig,
    *,
    segment: str = "full",
    is_end: pd.Timestamp | None = None,
) -> list[dict]:
    """信号组 vs 无条件基准组：逐窗口对比。"""
    out = []
    for td in cfg.forward_windows_td:
        col = f"ret_{td}td"
        immature_col = f"immature_{td}td"
        e = event_df.copy()
        if immature_col in e.columns:
            e = e[~e[immature_col].fillna(True)]
        if is_end is not None and "exec_date" in e.columns:
            exec_dates = pd.to_datetime(e["exec_date"], errors="coerce")
            if segment == "is":
                e = e[exec_dates <= is_end]
            elif segment == "oos":
                e = e[exec_dates > is_end]
        # 事件数为 0 时 event_df 可能是空表（无列），这里兜底为空序列
        sig_values = e[col] if col in e.columns else pd.Series(dtype="float64")
        sig = summarize_returns(sig_values, label=f"signal_{td}td")

        b = base_df[base_df["window_td"] == td]
        if is_end is not None:
            if segment == "is":
                b = b[b["obs_date"] <= is_end]
            elif segment == "oos":
                b = b[b["obs_date"] > is_end]
        bas = summarize_returns(b["net_return"], label=f"baseline_{td}td")

        diff = None
        if sig["mean"] is not None and bas["mean"] is not None:
            diff = sig["mean"] - bas["mean"]
        out.append(
            {
                "segment": segment,
                "window_td": td,
                "n_signals": sig["n"],
                "n_baseline": bas["n"],
                "signal_mean": sig["mean"],
                "signal_median": sig["median"],
                "signal_pct_positive": sig["pct_positive"],
                "signal_worst": sig["min"],
                "signal_best": sig["max"],
                "signal_mean_maxdd": (
                    float(e[f"dd_{td}td"].dropna().mean())
                    if f"dd_{td}td" in e.columns and e[f"dd_{td}td"].notna().any()
                    else None
                ),
                "baseline_mean": bas["mean"],
                "baseline_median": bas["median"],
                "baseline_pct_positive": bas["pct_positive"],
                "baseline_worst": bas["min"],
                "mean_difference": diff,
                "signal_better": (diff is not None and diff > 0),
            }
        )
    return out


def buy_and_hold(frame: pd.DataFrame, cfg: ResearchConfig) -> dict:
    """基准：区间内简单持有沪深300（价格指数口径）。"""
    p = frame["price"].astype("float64").dropna()
    p = p[(p.index >= pd.Timestamp(cfg.start_date)) & (p.index <= pd.Timestamp(cfg.end_date))]
    if len(p) < 2 or p.iloc[0] <= 0:
        return {"n_obs": int(len(p)), "total_return": None, "annualized_return": None,
                "max_drawdown": None}
    years = (p.index[-1] - p.index[0]).days / 365.25
    total = float(p.iloc[-1] / p.iloc[0] - 1.0)
    ann = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else None
    dd = float(np.min(p.to_numpy() / np.maximum.accumulate(p.to_numpy()) - 1.0))
    return {
        "n_obs": int(len(p)),
        "start_date": str(p.index[0].date()),
        "end_date": str(p.index[-1].date()),
        "years": round(years, 3),
        "total_return": total,
        "annualized_return": ann,
        "max_drawdown": dd,
    }


@dataclass
class ExperimentResult:
    """一次完整实验的产物。"""

    config: ResearchConfig
    indicator_frame: pd.DataFrame
    events: list[SpreadEvent]
    event_df: pd.DataFrame
    baseline_df: pd.DataFrame
    comparison: list[dict]
    comparison_is: list[dict]
    comparison_oos: list[dict]
    buy_hold: dict
    panel_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "n_events": len(self.events),
            "comparison_full": self.comparison,
            "comparison_is": self.comparison_is,
            "comparison_oos": self.comparison_oos,
            "buy_and_hold": self.buy_hold,
            "panel_info": self.panel_info,
        }


def run_experiment(panel_frame: pd.DataFrame, cfg: ResearchConfig,
                   panel_info: dict | None = None) -> ExperimentResult:
    """跑一次完整实验：指标 → 信号 → 前瞻收益 → 基准比较。"""
    frame = build_indicator_frame(panel_frame, cfg)
    frame = frame[
        (frame.index >= pd.Timestamp(cfg.start_date))
        & (frame.index <= pd.Timestamp(cfg.end_date))
    ]
    events = extract_events(frame, cfg)
    event_df = compute_event_outcomes(frame, events, cfg)
    base_df = build_baseline(frame, cfg)
    is_end = pd.Timestamp(cfg.is_end_date)

    comp = compare_to_baseline(event_df, base_df, cfg, segment="full")
    comp_is = compare_to_baseline(event_df, base_df, cfg, segment="is", is_end=is_end)
    comp_oos = compare_to_baseline(event_df, base_df, cfg, segment="oos", is_end=is_end)

    return ExperimentResult(
        config=cfg,
        indicator_frame=frame,
        events=events,
        event_df=event_df,
        baseline_df=base_df,
        comparison=comp,
        comparison_is=comp_is,
        comparison_oos=comp_oos,
        buy_hold=buy_and_hold(frame, cfg),
        panel_info=dict(panel_info or {}),
    )


def run_sensitivity(panel_frame: pd.DataFrame,
                    configs: list[ResearchConfig],
                    panel_info: dict | None = None) -> pd.DataFrame:
    """批量跑敏感性网格，返回长表（每行 = 一个配置 × 一个窗口）。"""
    rows = []
    for cfg in configs:
        res = run_experiment(panel_frame, cfg, panel_info)
        for seg, comps in (("full", res.comparison),
                           ("is", res.comparison_is),
                           ("oos", res.comparison_oos)):
            for c in comps:
                rows.append(
                    {
                        "experiment": cfg.experiment_name,
                        "indicator": cfg.indicator.value,
                        "return_basis": cfg.return_basis.value,
                        "lookback_years": cfg.lookback_years,
                        "percentile_window": cfg.percentile_window.value,
                        "threshold_pct": cfg.threshold_pct,
                        "segment": seg,
                        **{k: v for k, v in c.items() if k != "segment"},
                    }
                )
    return pd.DataFrame(rows)
