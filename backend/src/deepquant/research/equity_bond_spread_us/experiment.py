"""主实验：IC、增量信息回归、分位分组、阈值事件、子区间稳定性、统计功效。

主检验的设计（预登记，看结果前固定）
------------------------------------
- **目标变量**：真实总收益（扣除 CPI），因为 155 年样本跨越多轮通胀 regime，
  名义收益的长期均值本身随通胀漂移，会把「通胀补偿」误读成「预测能力」；
  名义收益作为次级结果同时报告。
- **主窗口**：60 个月（5 年）。理由：股债利差是估值类慢变量，1 年期预测主要被
  短期动量与贴现率波动主导；10 年期虽然信号更强但独立样本更少。
- **主指标**：Spearman 秩相关 + Newey-West t 统计 + 块自举 p 值三者并列，
  结论以最保守者为准；阈值事件作为「可读性层」，不作为唯一证据。
- **增量信息检验**：把估值（EY）与利率（Y10）同时放进回归，看利率系数是否显著。
  这是 A 股研究没做、也是本研究的核心问题——减掉国债收益率到底有没有用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import stats
from .config import USConfig
from .indicators import add_percentiles
from .signals import decile_bucket, merge_events, threshold_flag

#: 子区间划分（预登记，按美国宏观 regime 划分，而不是按结果切）
SUBPERIODS = (
    ("1881-1913 金本位", "1881-01-31", "1913-12-31"),
    ("1914-1945 两次世界大战", "1914-01-31", "1945-12-31"),
    ("1946-1990 战后高通胀", "1946-01-31", "1990-12-31"),
    ("1991-2026 低利率与正常化", "1991-01-31", "2026-12-31"),
)


@dataclass
class StudyResult:
    """一次完整研究的全部中间结果（可直接序列化落盘）。"""

    frame: pd.DataFrame
    ic: list[dict] = field(default_factory=list)
    regression: dict = field(default_factory=dict)
    deciles: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    event_stats: list[dict] = field(default_factory=list)
    subperiods: list[dict] = field(default_factory=list)
    power: dict = field(default_factory=dict)
    oos: dict = field(default_factory=dict)
    excess: list[dict] = field(default_factory=list)
    tests_counted: int = 0


def prepare_frame(frame: pd.DataFrame, cfg: USConfig) -> pd.DataFrame:
    """追加百分位与前视收益列。"""
    out = add_percentiles(
        frame,
        columns=("spread_cape", "ey_cape", "y10"),
        window=cfg.pct_window,
        min_obs=cfg.min_history,
        expanding=cfg.expanding,
    )
    for h in cfg.horizons:
        out[f"fwd_real_{h}"] = stats.forward_return(out["real_tr_index"], h, cfg.exec_lag)
        out[f"fwd_nom_{h}"] = stats.forward_return(out["tr_index"], h, cfg.exec_lag)
        out[f"dd_{h}"] = stats.path_drawdown(out["real_tr_index"], h, cfg.exec_lag)
    return out


def _positions(frame: pd.DataFrame, cfg: USConfig) -> tuple[int, int]:
    """返回 (样本内最后位置, 样本外起点位置)。"""
    idx = frame.index
    is_last = int(np.searchsorted(idx.values, np.datetime64(pd.Timestamp(cfg.is_end_date)), side="right") - 1)
    is_last = min(max(is_last, 0), len(idx) - 1)
    return is_last, is_last + 1


def sample_slice(frame: pd.DataFrame, cfg: USConfig, sample: str, horizon: int) -> pd.DataFrame:
    """按样本段截取，并**要求整个持有期落在样本段内**（避免评估期的跨段泄漏）。"""
    is_last, oos_start = _positions(frame, cfg)
    n = len(frame)
    if sample == "is":
        end_pos = is_last - cfg.exec_lag - horizon
        if end_pos <= 0:
            return frame.iloc[:0]
        return frame.iloc[: end_pos + 1]
    if sample == "oos":
        return frame.iloc[oos_start:]
    if sample == "full":
        return frame
    raise ValueError(f"未知 sample：{sample}")


def run_ic(frame: pd.DataFrame, cfg: USConfig, predictors: tuple[str, ...]) -> list[dict]:
    """对每个预测变量 × 窗口 × 样本段计算 IC 及三种显著性。"""
    rows: list[dict] = []
    for pred in predictors:
        if pred not in frame.columns:
            raise KeyError(f"缺少预测变量列 {pred}")
        for h in cfg.horizons:
            y_col = f"fwd_real_{h}"
            for sample in ("full", "is", "oos"):
                seg = sample_slice(frame, cfg, sample, h)
                x = seg[pred]
                y = seg[y_col]
                j = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
                if len(j) < 24:
                    rows.append(
                        {
                            "predictor": pred, "horizon": h, "sample": sample,
                            "n": int(len(j)), "ic": None, "nw_t": None,
                            "boot_p": None, "n_eff": None, "mdes": None,
                            "note": "样本不足",
                        }
                    )
                    continue
                ic = stats.spearman_ic(j["x"], j["y"])
                z = (j["x"] - j["x"].mean()) / j["x"].std(ddof=1)
                reg = stats.nw_ols(j["y"], z.to_numpy(), lag=cfg.nw_lag(h), names=["z"])
                boot = stats.block_bootstrap_pvalue(
                    j["x"].to_numpy(), j["y"].to_numpy(),
                    statistic=stats.spearman_ic,
                    block=max(12, h), n_boot=cfg.n_boot, seed=cfg.seed,
                )
                n_eff = stats.effective_sample(len(j), h)
                rows.append(
                    {
                        "predictor": pred,
                        "horizon": h,
                        "sample": sample,
                        "n": int(len(j)),
                        "ic": round(float(ic), 4),
                        "nw_t": round(float(reg.tstat[1]), 3),
                        "nw_lag": reg.lag,
                        "boot_p": round(float(boot["p_value"]), 4),
                        "n_eff": n_eff,
                        "mdes": round(float(stats.min_detectable_ic(n_eff)), 4),
                        "note": "",
                    }
                )
    return rows


def run_incremental_regression(frame: pd.DataFrame, cfg: USConfig) -> dict:
    """核心问题：控制估值后，国债收益率是否还有增量信息？

    R_{t+h} = a + b·z(EY) + c·z(Y10) + e   （Newey-West）
    b < 0：估值越贵（EY 越低）→ 未来收益越低，符合估值逻辑；
    c ≠ 0：利率在估值之外还有独立信息，此时「利差」才有存在意义。
    """
    out: dict = {}
    for h in cfg.horizons:
        for sample in ("full", "is", "oos"):
            seg = sample_slice(frame, cfg, sample, h)
            j = seg[["ey_cape", "y10", f"fwd_real_{h}"]].dropna()
            if len(j) < 30:
                out[f"{h}m_{sample}"] = {"n": int(len(j)), "note": "样本不足"}
                continue
            zx = (j["ey_cape"] - j["ey_cape"].mean()) / j["ey_cape"].std(ddof=1)
            zy = (j["y10"] - j["y10"].mean()) / j["y10"].std(ddof=1)
            X = np.column_stack([zx.to_numpy(), zy.to_numpy()])
            res = stats.nw_ols(j[f"fwd_real_{h}"].to_numpy(), X, lag=cfg.nw_lag(h),
                               names=["z_ey", "z_y10"])
            d = res.to_dict()
            d["sample"] = sample
            d["horizon"] = h
            out[f"{h}m_{sample}"] = d
    return out


def run_deciles(frame: pd.DataFrame, cfg: USConfig, predictor: str = "spread_cape") -> list[dict]:
    """按历史百分位分十组，看前视收益是否单调。"""
    if f"{predictor}_pct" not in frame.columns:
        raise KeyError(f"缺少百分位列 {predictor}_pct")
    bucket = decile_bucket(frame[f"{predictor}_pct"])
    rows: list[dict] = []
    for h in cfg.horizons:
        y = frame[f"fwd_real_{h}"]
        tmp = pd.DataFrame({"bucket": bucket, "y": y}).dropna()
        if tmp.empty:
            continue
        grp = tmp.groupby("bucket")["y"]
        means = grp.mean()
        counts = grp.count()
        ic_across = stats.spearman_ic(means.index.to_series(), means)
        rows.append(
            {
                "predictor": predictor,
                "horizon": h,
                "buckets": [
                    {
                        "bucket": int(b),
                        "n": int(counts.loc[b]),
                        "mean_pct": round(float(means.loc[b] * 100.0), 2),
                    }
                    for b in means.index
                ],
                "monotonic_ic": round(float(ic_across), 4) if np.isfinite(ic_across) else None,
                "high_minus_low_pct": round(
                    float((means.iloc[-1] - means.iloc[0]) * 100.0), 2
                ),
            }
        )
    return rows


def run_events(
    frame: pd.DataFrame, cfg: USConfig, predictor: str = "spread_cape"
) -> tuple[list[dict], list[dict]]:
    """阈值事件 + 事件后的前视收益（事件层面，避免重复计数）。"""
    pct_col = f"{predictor}_pct"
    if pct_col not in frame.columns:
        raise KeyError(f"缺少百分位列 {pct_col}")
    flag = threshold_flag(frame[pct_col], cfg.threshold_pct)
    events = merge_events(
        flag, values=frame[predictor], percentiles=frame[pct_col],
        gap=cfg.event_gap, exec_lag=cfg.exec_lag,
    )
    event_rows = [e.to_dict() for e in events]

    stats_rows: list[dict] = []
    pos = {ts: i for i, ts in enumerate(frame.index)}
    for h in cfg.horizons:
        y_col = f"fwd_real_{h}"
        base = frame[y_col].dropna()
        if base.empty:
            continue
        sig_vals, dd_vals = [], []
        for e in events:
            i = pos[pd.Timestamp(e.signal_month)]
            v = frame[y_col].iloc[i]
            if np.isfinite(v):
                sig_vals.append(float(v))
                dd_vals.append(float(frame[f"dd_{h}"].iloc[i]))
        if not sig_vals:
            stats_rows.append({"horizon": h, "n_events_usable": 0})
            continue
        sig = np.array(sig_vals)
        stats_rows.append(
            {
                "horizon": h,
                "n_events_usable": int(len(sig)),
                "n_baseline": int(len(base)),
                "signal_mean_pct": round(float(sig.mean() * 100), 2),
                "signal_median_pct": round(float(np.median(sig) * 100), 2),
                "baseline_mean_pct": round(float(base.mean() * 100), 2),
                "baseline_median_pct": round(float(np.median(base) * 100), 2),
                "diff_mean_pct": round(float((sig.mean() - base.mean()) * 100), 2),
                "signal_positive_share_pct": round(float((sig > 0).mean() * 100), 1),
                "baseline_positive_share_pct": round(float((base > 0).mean() * 100), 1),
                "signal_mean_dd_pct": round(float(np.mean(dd_vals) * 100), 2)
                if np.isfinite(dd_vals).any() else None,
            }
        )
    return event_rows, stats_rows


def run_subperiods(frame: pd.DataFrame, cfg: USConfig, predictor: str = "spread_cape") -> list[dict]:
    """子区间 IC 稳定性（预登记的四段划分）。"""
    rows: list[dict] = []
    h = cfg.primary_horizon
    y_col = f"fwd_real_{h}"
    for name, start, end in SUBPERIODS:
        seg = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
        j = pd.concat([seg[predictor].rename("x"), seg[y_col].rename("y")], axis=1).dropna()
        if len(j) < 36:
            rows.append({"period": name, "n": int(len(j)), "ic": None})
            continue
        ic = stats.spearman_ic(j["x"], j["y"])
        boot = stats.block_bootstrap_pvalue(
            j["x"].to_numpy(), j["y"].to_numpy(), statistic=stats.spearman_ic,
            block=max(12, h), n_boot=min(cfg.n_boot, 3000), seed=cfg.seed + 1,
        )
        rows.append(
            {
                "period": name,
                "n": int(len(j)),
                "ic": round(float(ic), 4),
                "boot_p": round(float(boot["p_value"]), 4),
                "mean_fwd_pct": round(float(j["y"].mean() * 100), 2),
            }
        )
    return rows


def run_power(frame: pd.DataFrame, cfg: USConfig, ic_rows: list[dict]) -> dict:
    """功效分析：这份数据到底能检测到多大的效应。"""
    out: dict = {"by_horizon": []}
    for h in cfg.horizons:
        y = frame[f"fwd_real_{h}"].dropna()
        n = len(y)
        n_eff = stats.effective_sample(n, h)
        mdes = stats.min_detectable_ic(n_eff)
        obs = next(
            (r["ic"] for r in ic_rows
             if r["horizon"] == h and r["sample"] == "full" and r["predictor"] == "spread_cape"),
            None,
        )
        req_n = None
        if obs not in (None, 0) and np.isfinite(obs):
            from statistics import NormalDist

            z_a = NormalDist().inv_cdf(0.975)
            z_b = NormalDist().inv_cdf(0.80)
            at = np.arctanh(min(abs(obs), 0.999))
            if at > 0:
                req_n = int(np.ceil(((z_a + z_b) / at) ** 2 + 3))
        # 注意：`required_independent_obs` 已经是「独立观测个数」，
        # 不能再除以窗口长度当成「事件数」——那会把单位搞混（早期版本就犯过这个错，
        # 得出「只需 2 个事件」的荒谬结论）。这里改为报告缺口倍数。
        shortfall = round(req_n / n_eff, 2) if (req_n and n_eff) else None
        out["by_horizon"].append(
            {
                "horizon": h,
                "n_obs": int(n),
                "n_eff": n_eff,
                "mdes_ic": round(float(mdes), 3) if np.isfinite(mdes) else None,
                "observed_ic": obs,
                "required_independent_obs": req_n,
                "power_shortfall_x": shortfall,
            }
        )
    return out


def run_oos(frame: pd.DataFrame, cfg: USConfig) -> dict:
    """样本外：扩展窗口滚动回归，比较「估值」与「利差」两个模型的样本外 R²。"""
    out: dict = {}
    min_train = 120
    for h in cfg.horizons:
        y = frame[f"fwd_real_{h}"]
        res: dict = {"horizon": h}
        for pred in ("ey_cape", "spread_cape"):
            x = frame[pred]
            pred_series = stats.expanded_window_slope_forecast(x, y, min_train=min_train)
            # 只用样本外段评估
            _, oos_start = _positions(frame, cfg)
            seg = pd.DataFrame(
                {"y": y, "pred": pred_series, "x": x}
            ).iloc[oos_start:].dropna()
            if len(seg) < 24:
                res[pred] = {"n": int(len(seg)), "oos_r2": None}
                continue
            bench = np.full(len(seg), float(pd.Series(frame[f"fwd_real_{h}"]).iloc[:oos_start].mean()))
            r2 = stats.oos_r2(seg["y"].to_numpy(), seg["pred"].to_numpy(), bench)
            res[pred] = {
                "n": int(len(seg)),
                "oos_r2": round(float(r2), 5),
                "ic_oos": round(float(stats.spearman_ic(seg["x"], seg["y"])), 4),
            }
        out[f"{h}m"] = res
    return out


def run_excess_appendix(frame: pd.DataFrame, cfg: USConfig) -> list[dict]:
    """附录：换目标变量——相对收益、债券收益，看利差的预测力从哪来。

    动机：如果利差本质上是**相对价值**信号，它应该更擅长预测相对收益。
    但这里有一个必须说清的问题：利差 S 里含 y10，而债券在持有期内的收益
    **本身就近似等于当期 y10**（到期收益率定义）。所以「预测相对收益」这件事
    有一块是机械的，不是信息。为此同时报告：
    - ``target=excess``：股票真实收益 − 债券真实收益
    - ``target=bond``：债券真实收益（用来确认机械成分有多大）
    """
    rows: list[dict] = []
    targets = {
        "excess": {h: stats.excess_real_return(frame, h, cfg.exec_lag) for h in cfg.horizons},
        "bond": {h: stats.bond_real_return(frame, h, cfg.exec_lag) for h in cfg.horizons},
    }
    for h in cfg.horizons:
        for target, series in targets.items():
            for pred in ("spread_cape", "ey_cape", "y10"):
                for sample in ("full", "oos"):
                    seg = sample_slice(frame, cfg, sample, h)
                    idx = seg.index
                    j = pd.concat(
                        [frame.loc[idx, pred].rename("x"), series[h].loc[idx].rename("y")],
                        axis=1,
                    ).dropna()
                    if len(j) < 24:
                        rows.append({"predictor": pred, "horizon": h, "sample": sample,
                                     "target": target, "n": int(len(j)),
                                     "ic": None, "boot_p": None})
                        continue
                    ic = stats.spearman_ic(j["x"], j["y"])
                    boot = stats.block_bootstrap_pvalue(
                        j["x"].to_numpy(), j["y"].to_numpy(), statistic=stats.spearman_ic,
                        block=max(12, h), n_boot=min(cfg.n_boot, 2000), seed=cfg.seed + 3,
                    )
                    rows.append(
                        {
                            "predictor": pred,
                            "horizon": h,
                            "sample": sample,
                            "target": target,
                            "n": int(len(j)),
                            "ic": round(float(ic), 4),
                            "boot_p": round(float(boot["p_value"]), 4),
                        }
                    )
    return rows


def run_study(frame: pd.DataFrame, cfg: USConfig) -> StudyResult:
    """一次性跑完主实验。"""
    prepared = prepare_frame(frame, cfg)
    predictors = ("spread_cape", "ey_cape", "y10", "cape")
    ic_rows = run_ic(prepared, cfg, predictors)
    events, event_stats = run_events(prepared, cfg, "spread_cape")
    deciles = run_deciles(prepared, cfg, "spread_cape")
    sub = run_subperiods(prepared, cfg, "spread_cape")
    power = run_power(prepared, cfg, ic_rows)
    reg = run_incremental_regression(prepared, cfg)
    oos = run_oos(prepared, cfg)
    excess = run_excess_appendix(prepared, cfg)

    n_tests = (
        len(ic_rows)
        + sum(len(v.get("coef", {})) for v in reg.values() if isinstance(v, dict))
        + len(deciles)
        + len(sub)
        + len(excess)
    )
    return StudyResult(
        frame=prepared,
        ic=ic_rows,
        regression=reg,
        deciles=deciles,
        events=events,
        event_stats=event_stats,
        subperiods=sub,
        power=power,
        oos=oos,
        excess=excess,
        tests_counted=n_tests,
    )
