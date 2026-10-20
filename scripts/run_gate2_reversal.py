#!/usr/bin/env python
"""Gate 2 运行脚本：短期反转（月度），按预登记 v2.0 机械执行。

预登记：docs/GATE2_REVERSAL_PREREG_2026-10-20.md
冻结 sha256：c440c77d284ae9c12f7fc555dd56c6f44e021cf967b0bc3d8e6b2ee7b39cdf87

## 为什么参数硬编码、不给命令行覆盖

预登记 §8 明确要求：运行脚本只能读预登记里声明的参数，且**不提供命令行覆盖**。
一旦允许 `--k 50 --skip 0` 这种覆盖，就跑得出一堆结果、再挑一个报告，
预登记制度立刻失效。要改设计必须回到文档、重新冻结、重新编号。

## 脚本自带的契约检查

启动时校验预登记文件的 sha256 是否等于冻结值。不一致就**拒绝运行**——
防止"跑了以后回改预登记"这种更隐蔽的作弊。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import (  # noqa: E402
    MIN_VALID_RATIO,
    load_close_matrix,
    month_end_positions,
    period_return,
    scores_at,
)

# ============================ 冻结参数（逐字照抄预登记 §2/§3/§4） ============================

PREREG_PATH = "docs/GATE2_REVERSAL_PREREG_2026-10-20.md"
PREREG_SHA256 = "c440c77d284ae9c12f7fc555dd56c6f44e021cf967b0bc3d8e6b2ee7b39cdf87"

POOL_DIR = "data/qfq_rebuilt_broad"
START = "2016-05-01"
END = "2026-10-31"

# 预登记 §2：600515.SH 的 ADJ 重建未通过（2021-12-22 单日 +35.6%，重整转增疑似未在
# 分红表登记），已声明从研究池剔除。目录里仍有它的 csv，必须显式排除，
# 否则"池 549 只"与预登记的"548 只"不符（首次运行犯过这个执行偏差）。
EXCLUDED_SYMBOLS = ["600515.SH"]

LOOKBACK = 21          # §3 信号窗口
SKIP = 1               # §3 跳过最近 1 日
K_MAIN = 110           # §3 持仓数（quintile）
DIRECTION = "reversal"  # §3 做多低分
REBALANCE = "monthly"   # §3

# §4 判定阈值
SUCCESS_T = 1.645      # 单侧 5%
SUCCESS_MIN_N = 120
INCONCLUSIVE_MAX_SD = 0.040  # 4.0pp

# §4 补充分组（不参与判定）
SENS_K = [50, 110, 200]
SENS_SKIP = [0, 1, 5]

# 成本档（§4 描述性，不参与判定）：月度全换手，双边成本 = 2 × 单边费率
COST_TIERS = [0.001, 0.003]

OUT_DIR = os.path.join("gate2_out", PREREG_SHA256[:12])


def verify_prereg() -> None:
    """契约：预登记字节必须与冻结值一致，否则拒绝运行。"""
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    raw = open(PREREG_PATH, "rb").read()
    got = hashlib.sha256(raw).hexdigest()
    if got != PREREG_SHA256:
        raise SystemExit(
            "预登记 sha256 与冻结值不符，拒绝运行。\n"
            f"  期望 {PREREG_SHA256}\n  实际 {got}\n"
            "若确需改设计，请新开预登记并重新冻结（预登记 §7 第 4 条）。"
        )
    print(f"[gate2] 预登记校验 OK  {got[:16]}  ({len(raw)} B)")


def run_combo(
    close_df: pd.DataFrame,
    close_ffill: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    positions: list[int],
    lookback: int,
    skip: int,
    k: int,
    direction: str = DIRECTION,
) -> list[dict]:
    """逐月计算多空组合收益。返回每月记录。"""
    min_valid = math.ceil(lookback * MIN_VALID_RATIO)
    rows: list[dict] = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sc = scores_at(pos, close_df, lookback=lookback, skip=skip, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close_ffill)
        pool = sc.index.intersection(rets.dropna().index)
        kk = min(k, len(pool) // 2)
        if kk < 1:
            continue
        r_pool = rets.loc[pool]
        ranked = sc.loc[pool].sort_values(ascending=(direction == "reversal"))
        r_long = float(r_pool.loc[ranked.index[:kk]].mean())   # 反转：多头 = 分数最低
        r_short = float(r_pool.loc[ranked.index[-kk:]].mean())  # 空头 = 分数最高
        rows.append(
            {
                "month": str(calendar[nxt].date()),
                "signal_date": str(calendar[pos].date()),
                "n_pool": int(len(pool)),
                "k_used": int(kk),
                "r_long": r_long,
                "r_short": r_short,
                "d": 0.5 * (r_long - r_short),
            }
        )
    return rows


def stats_of(d: list[float]) -> dict:
    n = len(d)
    if n < 2:
        return {"n": n}
    mean = sum(d) / n
    sd = statistics.stdev(d)
    se = sd / math.sqrt(n)
    t = mean / se if se > 0 else float("nan")
    # 单侧 p 值：H1: mean > 0
    p_one_sided = 0.5 * (1 - math.erf(t / math.sqrt(2))) if t == t else float("nan")
    return {
        "n": n,
        "mean_d": mean,
        "sd_d": sd,
        "se": se,
        "t_stat": t,
        "p_one_sided": p_one_sided,
        "win_ratio": sum(1 for x in d if x > 0) / n,
        "min_d": min(d),
        "max_d": max(d),
    }


def judge(st: dict) -> dict:
    """预登记 §4 的机械判定。顺序不可调换。"""
    n, sd = st["n"], st["sd_d"]
    if n < SUCCESS_MIN_N or sd > INCONCLUSIVE_MAX_SD:
        reasons = []
        if n < SUCCESS_MIN_N:
            reasons.append(f"n={n} < {SUCCESS_MIN_N}")
        if sd > INCONCLUSIVE_MAX_SD:
            reasons.append(f"sd={sd*100:.3f}pp > {INCONCLUSIVE_MAX_SD*100:.1f}pp")
        return {"verdict": "INCONCLUSIVE", "label": "证据不足", "rule_hits": reasons}
    if st["mean_d"] > 0 and st["t_stat"] >= SUCCESS_T:
        return {
            "verdict": "SUCCESS",
            "label": "通过",
            "rule_hits": [
                f"mean(d)={st['mean_d']:.6f} > 0",
                f"t={st['t_stat']:.4f} >= {SUCCESS_T}",
                f"n={n} >= {SUCCESS_MIN_N}",
            ],
        }
    hits = []
    if st["mean_d"] <= 0:
        hits.append(f"mean(d)={st['mean_d']:.6f} <= 0")
    if st["t_stat"] < SUCCESS_T:
        hits.append(f"t={st['t_stat']:.4f} < {SUCCESS_T}")
    return {"verdict": "FAILURE", "label": "失败", "rule_hits": hits}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    verify_prereg()

    close_df, _ = load_close_matrix(POOL_DIR)
    dropped = [c for c in EXCLUDED_SYMBOLS if c in close_df.columns]
    if dropped:
        close_df = close_df.drop(columns=dropped)
        print(f"[gate2] 按预登记 §2 剔除 {dropped}")
    calendar = close_df.index
    close_ffill = close_df.ffill()
    positions = month_end_positions(calendar, START, END)
    print(f"[gate2] 池 {close_df.shape[1]} 只 | 日历 {calendar[0].date()} ~ {calendar[-1].date()} "
          f"| 月末位置 {len(positions)}")
    print(f"[gate2] 主检验 lookback={LOOKBACK} skip={SKIP} k={K_MAIN} direction={DIRECTION}")
    print()

    # ---------------- 主检验 ----------------
    rows = run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, SKIP, K_MAIN)
    d = [r["d"] for r in rows]
    st = stats_of(d)
    verdict = judge(st)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly_paired.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("month,signal_date,n_pool,k_used,r_long,r_short,d\n")
        for r in rows:
            f.write(f"{r['month']},{r['signal_date']},{r['n_pool']},{r['k_used']},"
                    f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f}\n")

    print("=== 主检验结果（预登记 §4 判据）===")
    print(f"  n = {st['n']} 个月")
    print(f"  mean(d) = {st['mean_d']*100:+.4f} pp/月")
    print(f"  sd(d)   = {st['sd_d']*100:.4f} pp/月")
    print(f"  se      = {st['se']*100:.4f} pp")
    print(f"  t       = {st['t_stat']:+.4f}   (单侧 5% 临界 1.645)")
    print(f"  p(单侧) = {st['p_one_sided']:.6f}")
    print(f"  胜率    = {st['win_ratio']*100:.2f}%")
    print(f"  区间    = [{st['min_d']*100:+.2f}, {st['max_d']*100:+.2f}] pp")
    print()
    print(f"  >>> 判定：{verdict['verdict']}（{verdict['label']}）")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    print()

    # ---------------- 分年（描述性） ----------------
    by_year: dict[str, list[float]] = {}
    for r in rows:
        by_year.setdefault(r["month"][:4], []).append(r["d"])
    yearly = {}
    print("=== 分年度（描述性，不参与判定）===")
    for y in sorted(by_year):
        v = by_year[y]
        yearly[y] = {
            "n": len(v),
            "mean_d_pp": round(sum(v) / len(v) * 100, 4),
            "win_ratio": round(sum(1 for x in v if x > 0) / len(v), 4),
        }
        print(f"  {y}: n={len(v):>2}  mean={sum(v)/len(v)*100:+7.3f}pp  胜率={yearly[y]['win_ratio']*100:5.1f}%")
    print()

    # ---------------- 成本档（描述性） ----------------
    print("=== 成本档（描述性，不参与判定；月度全换手，双边=2c）===")
    cost_desc = {}
    for c in COST_TIERS:
        net = [x - 2 * c for x in d]
        s = stats_of(net)
        cost_desc[f"one_way_{c*100:.1f}pct"] = {
            "mean_net_pp": round(s["mean_d"] * 100, 4),
            "t_net": round(s["t_stat"], 4),
        }
        print(f"  单边 {c*100:.1f}%: mean_net={s['mean_d']*100:+7.4f}pp  t_net={s['t_stat']:+.3f}")
    print()

    # ---------------- 敏感性（描述性） ----------------
    sens_rows = []
    print("=== 敏感性（描述性，不参与判定）===")
    for k in SENS_K:
        rr = run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, SKIP, k)
        s = stats_of([x["d"] for x in rr])
        sens_rows.append({"param": "k", "value": k, **{kk: s.get(kk) for kk in ("n", "mean_d", "t_stat", "sd_d")}})
        print(f"  k={k:>4}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f}")
    for sk in SENS_SKIP:
        rr = run_combo(close_df, close_ffill, calendar, positions, LOOKBACK, sk, K_MAIN)
        s = stats_of([x["d"] for x in rr])
        sens_rows.append({"param": "skip", "value": sk, **{kk: s.get(kk) for kk in ("n", "mean_d", "t_stat", "sd_d")}})
        print(f"  skip={sk}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f}")
    print()

    with open(os.path.join(OUT_DIR, "sensitivity.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("param,value,n,mean_d,t_stat,sd_d\n")
        for r in sens_rows:
            f.write(f"{r['param']},{r['value']},{r['n']},{r['mean_d']:.10f},{r['t_stat']:.6f},{r['sd_d']:.10f}\n")

    # ---------------- summary ----------------
    summary = {
        "prereg_version": "gate2-reversal-v2.0",
        "prereg_sha256": PREREG_SHA256,
        "pool": {"dir": POOL_DIR, "n_symbols": int(close_df.shape[1])},
        "window": [START, END],
        "params": {
            "lookback": LOOKBACK,
            "skip": SKIP,
            "k": K_MAIN,
            "direction": DIRECTION,
            "rebalance": REBALANCE,
            "min_valid_ratio": MIN_VALID_RATIO,
        },
        "main": {k: (round(v, 10) if isinstance(v, float) else v) for k, v in st.items()},
        "judgement": verdict,
        "yearly_descriptive": yearly,
        "cost_descriptive": cost_desc,
        "sensitivity_descriptive": sens_rows,
        "note": "描述性结果不参与判定；主判定以 §4 判据为准（预登记 §7 第 2 条）。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    # ---------------- manifest ----------------
    files = ["monthly_paired.csv", "sensitivity.csv", "summary.json"]
    manifest = {
        "prereg_version": "gate2-reversal-v2.0",
        "prereg_sha256": PREREG_SHA256,
        "files": {
            name: {
                "sha256": sha256_file(os.path.join(OUT_DIR, name)),
                "bytes": os.path.getsize(os.path.join(OUT_DIR, name)),
            }
            for name in files
        },
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print(f"[gate2] 产物已写 {OUT_DIR}/")
    for name in files + ["manifest.json"]:
        print(f"        {name:24} {os.path.getsize(os.path.join(OUT_DIR, name)):>8} B")
    print()
    print(f"[gate2] 最终判定：{verdict['verdict']}（{verdict['label']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
