"""统计效力分析：现有事件数能否支撑「信号有效」的结论。

回答三个问题：

1. **置换检验**：把「信号组均值 − 基准组均值」放到随机分组的分布里，
   看现有差异有多罕见。（不依赖正态假设，适合小样本。）
2. **有效独立样本量**：月频/日频观测高度重叠，名义 n 不等于独立 n。
3. **所需样本量**：要在一个给定效应量上达到 80% 检验力，需要多少个**独立**事件。

结论只用于说明「证据强度」，不作为投资建议。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/equity_bond_spread")
OUT = DATA / "stat_power"


def permutation_test(
    signal_vals: np.ndarray,
    baseline_vals: np.ndarray,
    *,
    n_perm: int = 20000,
    seed: int = 20261018,
) -> dict:
    """置换检验：随机抽取同样数量的观测作为「伪信号组」。

    Returns:
        含观测均值差、单/双侧 p 值、随机分布分位数的字典。
    """
    sig = np.asarray(signal_vals, dtype="float64")
    sig = sig[~np.isnan(sig)]
    pool = np.concatenate([sig, np.asarray(baseline_vals, dtype="float64")])
    pool = pool[~np.isnan(pool)]
    n = len(sig)
    if n == 0 or len(pool) <= n:
        return {"valid": False, "note": "观测不足，无法做置换检验"}

    # 统计量统一为「信号组均值 − 池整体均值」，置换时用同一口径，保证可比
    observed = float(sig.mean() - pool.mean())

    rng = np.random.default_rng(seed)
    stats = np.empty(n_perm, dtype="float64")
    for i in range(n_perm):
        pick = rng.choice(len(pool), size=n, replace=False)
        stats[i] = pool[pick].mean() - pool.mean()

    p_two = float((np.abs(stats) >= abs(observed)).mean())
    p_one = float((stats >= observed).mean())
    return {
        "valid": True,
        "n_signal": int(n),
        "n_pool": int(len(pool)),
        "signal_mean": float(sig.mean()),
        "pool_mean": float(pool.mean()),
        "observed_diff": observed,
        "n_perm": int(n_perm),
        "p_two_sided": p_two,
        "p_one_sided": p_one,
        "perm_std": float(stats.std()),
        "perm_q95": float(np.quantile(stats, 0.95)),
        "perm_q99": float(np.quantile(stats, 0.99)),
        "note": (
            "置换检验只衡量「随机分组的偶然性」，不校正观测重叠、"
            "不校正多重比较、不构成因果推断"
        ),
    }


def required_sample_size(effect: float, sd: float, *, alpha: float = 0.05, power: float = 0.80) -> dict:
    """两独立样本均值比较所需的每组样本量（正态近似）。"""
    if sd <= 0 or effect == 0:
        return {"valid": False}
    # 标准正态分位数：双侧 α=0.05 → 1.95996；检验力 0.80 → 0.84162
    Z_ALPHA = {0.10: 1.6448536, 0.05: 1.9599640, 0.01: 2.5758293}
    Z_POWER = {0.80: 0.8416212, 0.90: 1.2815516, 0.95: 1.6448536}
    z_a = Z_ALPHA.get(round(alpha, 2))
    z_b = Z_POWER.get(round(power, 2))
    if z_a is None or z_b is None:
        return {"valid": False, "note": f"未内置 alpha={alpha} / power={power} 的分位数"}
    n = 2.0 * ((z_a + z_b) ** 2) * (sd / effect) ** 2
    return {
        "valid": True,
        "effect": effect,
        "sd": sd,
        "alpha": alpha,
        "power": power,
        "n_per_group": float(n),
        "n_per_group_ceil": int(np.ceil(n)),
        "note": "正态近似；对厚尾与自相关数据是乐观下界，实际需求更多",
    }


def effective_n(values: pd.Series, max_lag: int = 60) -> dict:
    """用滞后相关系数估算有效独立样本量 n_eff = n·(1−ρ)/(1+ρ)。"""
    v = values.dropna()
    if len(v) < 10:
        return {"valid": False}
    rho = float(v.autocorr(1))
    if np.isnan(rho):
        return {"valid": False}
    n = len(v)
    n_eff = n * (1 - rho) / (1 + rho) if abs(rho) < 1 else 1.0
    # 综合前若干阶自相关（更保守）
    lags = [float(v.autocorr(k)) for k in range(1, min(max_lag, len(v) // 4) + 1)]
    lags = [x for x in lags if not np.isnan(x)]
    rho_sum = 1.0 + 2.0 * sum(lags)
    n_eff_sum = n / rho_sum if rho_sum > 0 else float(n)
    return {
        "valid": True,
        "n": int(n),
        "lag1_autocorr": rho,
        "n_eff_lag1": float(n_eff),
        "sum_autocorr_factor": float(rho_sum),
        "n_eff_multi_lag": float(n_eff_sum),
        "note": "n_eff_multi_lag 用前若干阶自相关之和，比仅用 lag-1 更保守",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--window", type=int, default=250)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    OUT.mkdir(parents=True, exist_ok=True)
    ev = pd.read_csv(data_dir / "daily_check" / "event_daily_B.csv")
    col = f"ret_{args.window}td"
    imm = f"immature_{args.window}td"
    ev = ev[ev["daily_available"].astype(bool)]
    if imm in ev.columns:
        ev = ev[~ev[imm].astype(bool)]
    ev = ev[ev[col].notna()]

    # 基准池：从日频基准重算结果中取（与信号组同一收益口径）
    baseline_pool_json = data_dir / "daily_check" / f"daily_check_B.json"
    payload = json.loads(baseline_pool_json.read_text(encoding="utf-8"))
    comp = payload["comparison_daily"][f"{args.window}td"]

    # 基准观测值需重新构造：读回基准明细
    from deepquant.research.equity_bond_spread.daily_exec import (
        build_baseline_daily,
        load_daily_close,
        load_trading_calendar,
        split_segments,
    )
    from deepquant.research.equity_bond_spread.data import build_panel

    panel = build_panel(data_dir / "csi300_monthly.csv", data_dir / "cn10y.csv").frame
    daily = load_daily_close(data_dir / "csi300_daily.csv")
    cal = load_trading_calendar(data_dir / "trade_cal.csv")
    base = build_baseline_daily(panel, daily, cal, args.window, cost_rate=0.0015)

    sig_vals = ev[col].to_numpy(dtype="float64")
    base_vals = base["net_return"].to_numpy(dtype="float64")

    perm = permutation_test(sig_vals, base_vals)

    # 样本外单独检验：OOS 是唯一有实质样本的部分，需单独给出证据强度
    is_end = pd.Timestamp("2018-12-31")
    ev_exec = pd.to_datetime(ev["exec_date_daily"], errors="coerce")
    oos_sig = ev[ev_exec > is_end][col].to_numpy(dtype="float64")
    oos_base = base[base["exec_date"] > is_end]["net_return"].to_numpy(dtype="float64")
    perm_oos = permutation_test(oos_sig, oos_base) if len(oos_sig) else {"valid": False}

    sd = float(np.concatenate([sig_vals, base_vals]).std(ddof=1))
    effect = abs(float(sig_vals.mean() - base_vals.mean())) if len(sig_vals) else np.nan
    need = required_sample_size(effect, sd) if not np.isnan(effect) else {"valid": False}

    # 自相关诊断：只在**最长连续段**上做，跨数据空洞的收益会污染波动率与自相关
    segs = split_segments(daily)
    longest = max(segs, key=len) if segs else daily
    rets = longest.pct_change().dropna()
    overlap_note = effective_n(rets, max_lag=20)

    out = {
        "window_td": args.window,
        "n_signal_events": int(len(sig_vals)),
        "n_baseline_obs": int(len(base_vals)),
        "signal_mean": float(sig_vals.mean()) if len(sig_vals) else None,
        "baseline_mean": float(base_vals.mean()),
        "observed_difference": float(sig_vals.mean() - base_vals.mean()) if len(sig_vals) else None,
        "pooled_sd": sd,
        "permutation": perm,
        "permutation_oos": perm_oos,
        "n_oos_signals": int(len(oos_sig)),
        "n_oos_baseline": int(len(oos_base)),
        "oos_difference": (
            float(oos_sig.mean() - oos_base.mean()) if len(oos_sig) and len(oos_base) else None
        ),
        "required_sample": need,
        "overlap_diagnostics": comp,
        "daily_return_autocorr": overlap_note,
        "caveats": [
            "事件数 4 个，远低于正态近似所需样本量，任何 p 值都不可作为接受/拒绝的依据",
            "信号事件的持有期互相重叠（事件3/4/5），不构成独立观测",
            "置换检验未校正多重比较、未校正数据窥探",
        ],
    }
    path = OUT / f"stat_power_{args.window}td.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== 统计效力分析（{args.window} 交易日窗口）===")
    print(f"信号事件 {out['n_signal_events']} 个，基准观测 {out['n_baseline_obs']} 个")
    print(f"信号均值 {out['signal_mean']*100:+.2f}% | 基准均值 {out['baseline_mean']*100:+.2f}% "
          f"| 差 {out['observed_difference']*100:+.2f}%")
    print(f"合并标准差 {sd*100:.2f}%")
    print()
    print("置换检验：")
    print(f"  观测差 {perm['observed_diff']*100:+.2f}%（相对池整体均值）")
    print(f"  双侧 p = {perm['p_two_sided']:.4f}   单侧 p = {perm['p_one_sided']:.4f}")
    print(f"  随机分布 95% 分位 {perm['perm_q95']*100:+.2f}%  99% 分位 {perm['perm_q99']*100:+.2f}%")
    print(f"  （{perm['n_perm']} 次重排，种子固定，可复现）")
    print()
    if perm_oos.get("valid"):
        print("样本外置换检验（执行日 > 2018-12-31）：")
        print(f"  信号 {perm_oos['n_signal']} 个 / 基准 {perm_oos['n_pool'] - perm_oos['n_signal']} 个")
        print(f"  信号均值 {oos_sig.mean()*100:+.2f}%  基准均值 {oos_base.mean()*100:+.2f}% "
              f"差 {out['oos_difference']*100:+.2f}%")
        print(f"  双侧 p = {perm_oos['p_two_sided']:.4f}   单侧 p = {perm_oos['p_one_sided']:.4f}")
    print()
    if need.get("valid"):
        print("所需样本量（α=0.05，检验力 80%，每组）：")
        print(f"  效应 {effect*100:.2f}% / 标准差 {sd*100:.2f}% → 需 {need['n_per_group_ceil']} 个**独立**事件")
        print(f"  现有 {len(sig_vals)} 个，缺口 {max(0, need['n_per_group_ceil'] - len(sig_vals))} 个")
    print()
    print(f"日频收益 lag-1 自相关 {overlap_note.get('lag1_autocorr', float('nan')):.4f}"
          f" → 名义 {overlap_note.get('n')} 个日收益的有效独立样本约 "
          f"{overlap_note.get('n_eff_multi_lag', float('nan')):.0f} 个")
    print(f"\n已写出：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
