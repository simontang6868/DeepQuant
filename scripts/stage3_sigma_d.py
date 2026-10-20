#!/usr/bin/env python
"""阶段 3-B：用 548 只宽池实测配对月度超额的 σ_d，为功效计算提供真实参数。

## 这个脚本只回答一个问题

「在给定池规模 N 与持仓数 k 下，配对月度超额 d_m 的标准差是多少？」

它**不是**假设检验，**不产出**任何效应量结论。设计用途只有一个：
把 MDE（最小可检出效应）算准，从而判断某个实验该不该跑。

## 纪律（R2 / R11 / R12）

- 打分规则**完全照抄 Gate 1 预登记 v1.3**，不做任何调参：
  lookback=60, skip=5, min_valid_ratio=0.90, 端点 close 比值。
- 选股规则**预先固定**：动量分数 top-k，等权，月度再平衡。
  脚本内没有任何"试几个阈值挑一个"的逻辑。
- **mean(d) 不参与设计**。脚本把它写进 JSON 供审计，但**不打印**——
  设计者看到效应方向就可能无意识调参，这是 R2 要防的正是这件事。
- σ_d 属方差信息，无方向性，用于样本量计算是标准做法（不过界）。

## 与 Gate 1 的可比性

Gate 1 主实验：52 只池 / 10 持仓 / OOS 2021-01~2024-12 / σ_d = 4.8257pp。
本脚本在同口径下给出 52 只池的复算值作为对照，再给 548 只池的实测值。

## 池的动态性（避免幸存者偏差）

每月池 = **信号日当天能算出分数的标的集合**（有行、四价为正、窗口覆盖 ≥90%）。
上市不足的不在池里，退市后的不在池里——这是"当时可投资"的近似，
而不是"用 2026 年的名单回溯 2017 年"。

用法：
    python scripts/stage3_sigma_d.py                       # 默认 IS 段为主
    python scripts/stage3_sigma_d.py --pool-dir data/qfq_rebuilt --k 10
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

# --- 打分参数：照抄 Gate 1 预登记 v1.3，不得修改 ---
LOOKBACK = 60
SKIP = 5
MIN_VALID_RATIO = 0.90
MIN_VALID_DAYS = math.ceil(LOOKBACK * MIN_VALID_RATIO)  # 54

# --- 估计窗口 ---
# IS 段 = Gate 1 的样本内段，从未用于任何 OOS 判定，是最干净的估计源。
WINDOWS = {
    "IS": ("2016-05-01", "2020-12-31"),
    "OOS_GATE1": ("2021-01-01", "2024-12-31"),
    "OOS_NEW": ("2025-01-01", "2026-12-31"),
    "FULL": ("2016-05-01", "2026-12-31"),
}

Z_ALPHA_05_TWO_SIDED = 1.959963985
Z_POWER_80 = 0.841621234


def load_close_matrix(pool_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读全部 *.qfq.csv → (close 宽表, volume 宽表)，index=日期, columns=代码。"""
    files = sorted(glob.glob(os.path.join(pool_dir, "*.qfq.csv")))
    if not files:
        raise SystemExit(f"没有找到行情文件：{pool_dir}/*.qfq.csv")
    closes: dict[str, pd.Series] = {}
    vols: dict[str, pd.Series] = {}
    for path in files:
        code = os.path.basename(path).replace(".qfq.csv", "")
        df = pd.read_csv(path, usecols=["date", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset="date", keep="last").set_index("date").sort_index()
        closes[code] = df["close"].astype(float)
        vols[code] = df["volume"].astype(float)
    close_df = pd.DataFrame(closes).sort_index()
    vol_df = pd.DataFrame(vols).sort_index()
    # 前复权序列理论上无非正价（ADJ 重建已验 A1），保险起见置空以纳入"不可用"
    close_df = close_df.where(close_df > 0)
    return close_df, vol_df


def month_end_positions(calendar: pd.DatetimeIndex, start: str, end: str) -> list[int]:
    """返回落在 [start, end] 内每个自然月最后一个交易日的位置索引。"""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    months: dict[tuple[int, int], int] = {}
    for pos, ts in enumerate(calendar):
        if ts < s or ts > e:
            continue
        months[(ts.year, ts.month)] = pos  # 顺序遍历，自然保留每月最后一个
    return [months[key] for key in sorted(months)]


def scores_at(pos: int, close_df: pd.DataFrame) -> pd.Series:
    """信号日位置 pos 的动量分数（照抄 platform cross_section 的窗口定义）。

    窗口 = [pos - (skip+lookback-1), pos - skip]，两端含。
    端点 p_start = 窗口首日 close，p_end = 窗口末日 close，分数 = p_end/p_start - 1。
    """
    start = pos - (SKIP + LOOKBACK - 1)
    end = pos - SKIP
    if start < 0:
        return pd.Series(dtype=float)
    win = close_df.iloc[start : end + 1]
    # P3 覆盖率：窗口内四价齐全且为正的有效交易日数（close 已保证 >0 或 NaN）
    valid_days = win.notna().sum(axis=0)
    p_start = win.iloc[0]
    p_end = win.iloc[-1]
    ok = (valid_days >= MIN_VALID_DAYS) & p_start.notna() & p_end.notna()
    # P1/P2：信号日本身必须可定价（有行且为正）
    signal_row = close_df.iloc[pos]
    ok &= signal_row.notna()
    s = (p_end / p_start - 1.0).where(ok)
    return s.dropna()


def period_return(pos0: int, pos1: int, close_ffill: pd.DataFrame) -> pd.Series:
    """持有期收益。停牌时用持有期内最后一个可用价（等价于无法卖出）。"""
    p0 = close_ffill.iloc[pos0]
    p1 = close_ffill.iloc[pos1]
    return (p1 / p0 - 1.0).where(p0.notna() & p1.notna())


def measure(
    close_df: pd.DataFrame,
    close_ffill: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    ks: list[int],
    start: str,
    end: str,
    mode: str = "long_only",
) -> dict:
    """在一个估计窗口内计算各 k 的配对月度超额序列统计量。"""
    positions = month_end_positions(calendar, start, end)
    if len(positions) < 6:
        return {"n": 0, "note": "月份不足，跳过"}

    per_k: dict[int, dict] = {k: {"d": [], "wss": []} for k in ks}
    pool_sizes: list[int] = []

    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sc = scores_at(pos, close_df)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close_ffill)
        pool = sc.index.intersection(rets.dropna().index)
        if len(pool) < 3:
            continue
        n_pool = len(pool)
        pool_sizes.append(n_pool)
        r_pool = rets.loc[pool]
        r_bench = float(r_pool.mean())
        ranked = sc.loc[pool].sort_values(ascending=False)
        for k in ks:
            kk = min(k, n_pool)
            if mode == "long_short":
                # 多空：0.5×top-k − 0.5×bottom-k（自融资，零净暴露）。
                # 设计理由（先验，非从数据挑）：σ_d 主要由风格共同波动驱动，
                # 做空等量"输家"可对冲市值/波动/行业暴露，从而压低 σ_d。
                # 这是个股排序信号的【标准】检验方式（组合排序多空价差），
                # 与 Gate 1 的单边多头不可直接比较，属于不同的设计。
                kk_eff = min(kk, n_pool // 2)
                top_idx = ranked.index[:kk_eff]
                bot_idx = ranked.index[-kk_eff:]
                r_long = float(r_pool.loc[top_idx].mean())
                r_short = float(r_pool.loc[bot_idx].mean())
                per_k[k]["d"].append(0.5 * (r_long - r_short))
                # 多空组合的权重差平方和：多头每只 +0.5/k、空头每只 −0.5/k，其余 0。
                #   Σ(Δw)² = k(0.5/k)² + k(0.5/k)² = 0.5/k
                per_k[k]["wss"].append(0.5 / kk_eff)
                continue
            r_strat = float(r_pool.loc[ranked.index[:kk]].mean())
            per_k[k]["d"].append(r_strat - r_bench)
            # 权重差平方和：策略 = top-k 等权，基准 = 全池等权，策略是基准的【子集】。
            #   Δw(top-k) = 1/k − 1/N，Δw(其余) = −1/N
            #   Σ(Δw)² = k(1/k−1/N)² + (N−k)(1/N)² = 1/k − 1/N   （代数恒等）
            # 注意是【减】而非加：上一版误用 1/k + 1/N（那是两个互斥组合的方差和），
            # 导致反解出的 σ_idio 随 k 单调上升（13.2/10.4/7.3），自相矛盾。
            per_k[k]["wss"].append(1.0 / kk - 1.0 / n_pool)

    out = {
        "window": [start, end],
        "n_months": len(per_k[ks[0]]["d"]),
        "pool_size_median": int(statistics.median(pool_sizes)) if pool_sizes else 0,
        "pool_size_min": min(pool_sizes) if pool_sizes else 0,
        "pool_size_max": max(pool_sizes) if pool_sizes else 0,
        "per_k": {},
    }
    for k in ks:
        d = per_k[k]["d"]
        if len(d) < 6:
            out["per_k"][str(k)] = {"n": len(d), "note": "样本不足"}
            continue
        sd = statistics.stdev(d)  # ddof=1
        # Var(d) = σ_idio² · Σ(Δw)²（个股独立、等波动假设）→ 反解 σ_idio
        # 一致性检验：若模型正确，不同 k 反解出的 σ_idio 应当一致。
        wss = per_k[k]["wss"]
        mean_wss = sum(wss) / len(wss)
        sigma_idio = sd / math.sqrt(mean_wss) if mean_wss > 0 else float("nan")
        z = Z_ALPHA_05_TWO_SIDED + Z_POWER_80
        out["per_k"][str(k)] = {
            "n": len(d),
            "sigma_d": round(sd, 6),
            "sigma_d_pp": round(sd * 100, 4),
            "mean_weight_diff_sq": round(mean_wss, 8),
            "sigma_idio_implied_pp": round(sigma_idio * 100, 4),
            # 该 k 在 n 个月下能检出的最小效应
            "mde_at_n_pp": round(z * sd / math.sqrt(len(d)), 4),
            "n_required_for_030pp": int(math.ceil(z * z * sd * sd / (0.0030**2))),
            "n_required_for_050pp": int(math.ceil(z * z * sd * sd / (0.0050**2))),
            # 以下供审计，明确禁止用于设计
            "mean_d_audit_only_forbidden_for_design": round(sum(d) / len(d), 8),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段 3-B：实测配对月度超额 σ_d")
    ap.add_argument("--pool-dir", default="data/qfq_rebuilt_broad")
    ap.add_argument("--ks", default="10,20,50,100,200", help="持仓数列表（逗号分隔）")
    ap.add_argument("--windows", default="IS,FULL", help="估计窗口：IS/OOS_GATE1/OOS_NEW/FULL")
    ap.add_argument("--out", default="docs/STAGE3_SIGMA_D_2026-10-20.json")
    ap.add_argument(
        "--mode",
        choices=["long_only", "long_short"],
        default="long_only",
        help="long_only=top-k vs 全池等权（Gate 1 口径）；long_short=0.5(top-k)−0.5(bottom-k) 自融资",
    )
    args = ap.parse_args(argv)

    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    close_df, vol_df = load_close_matrix(args.pool_dir)
    calendar = close_df.index
    close_ffill = close_df.ffill()

    print(f"[sigma_d] 池目录 {args.pool_dir}")
    print(f"[sigma_d] 标的 {close_df.shape[1]} 只 | 日历 {len(calendar)} 个交易日 "
          f"({calendar[0].date()} ~ {calendar[-1].date()})")
    print(f"[sigma_d] 参数 lookback={LOOKBACK} skip={SKIP} min_valid={MIN_VALID_DAYS}/{LOOKBACK}")
    print(f"[sigma_d] 持仓数 k = {ks}")
    print(f"[sigma_d] 模式 mode = {args.mode}"
          + ("（多空 0.5×top-k − 0.5×bottom-k，自融资）" if args.mode == "long_short"
             else "（top-k vs 全池等权，Gate 1 口径）"))
    print()

    report = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "pool_dir": args.pool_dir,
        "n_symbols": int(close_df.shape[1]),
        "calendar": [str(calendar[0].date()), str(calendar[-1].date())],
        "scoring_params": {
            "lookback": LOOKBACK,
            "skip": SKIP,
            "min_valid_ratio": MIN_VALID_RATIO,
            "min_valid_days": MIN_VALID_DAYS,
            "source": "照抄 Gate 1 预登记 v1.3（scripts 内不含任何可调项）",
        },
        "discipline": {
            "purpose": "仅用于功效/MDE 计算（方差信息，无方向性）",
            "mean_d_usage": "禁止用于设计决策；JSON 中仅供审计，stdout 不打印",
        },
        "mode": args.mode,
        "windows": {},
    }

    for wname in [w.strip() for w in args.windows.split(",") if w.strip()]:
        if wname not in WINDOWS:
            print(f"!! 未知窗口 {wname}，跳过", file=sys.stderr)
            continue
        start, end = WINDOWS[wname]
        res = measure(close_df, close_ffill, calendar, ks, start, end, mode=args.mode)
        report["windows"][wname] = res
        print(f"=== {wname}  ({start} ~ {end}) ===")
        print(f"  月份 n={res.get('n_months')} | 当月池规模 中位 {res.get('pool_size_median')} "
              f"(min {res.get('pool_size_min')} / max {res.get('pool_size_max')})")
        print(f"  {'k':>4} {'σ_d(pp)':>9} {'反解σ_idio(pp)':>15} {'MDE@n(pp)':>11} {'n需0.30pp':>10} {'n需0.50pp':>10}")
        for k in ks:
            r = res["per_k"].get(str(k), {})
            if "sigma_d_pp" not in r:
                print(f"  {k:>4}  {r.get('note', 'n/a')}")
                continue
            print(f"  {k:>4} {r['sigma_d_pp']:>9.4f} {r['sigma_idio_implied_pp']:>15.3f} "
                  f"{r['mde_at_n_pp']:>11.4f} {r['n_required_for_030pp']:>10} {r['n_required_for_050pp']:>10}")
        print()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"[sigma_d] 报告已写 {args.out}")
    print("[sigma_d] 注意：stdout 刻意不打印 mean(d)；如需审计见 JSON 的 mean_d_audit_only_forbidden_for_design 字段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
