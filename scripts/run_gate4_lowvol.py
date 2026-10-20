#!/usr/bin/env python
"""Gate 4 运行脚本：低波动异象（月度）+ 2016 分段检验。

预登记：docs/GATE4_LOWVOL_PREREG_2026-10-20.md
冻结 sha256：94988f7c57d1e5f41c78d16330b704204d7453b39cdee4b34d0b40da6826687d

## 纪律

- 参数**硬编码**，不提供命令行覆盖（预登记 §7 第 1 条）。要改设计 → 新预登记、重新冻结。
- 启动时校验预登记 sha256；不一致**拒绝运行**。
- **主判定只有全段一个**（§4.1）。分段（§4.2）**不参与判定**，只按跑前写死的四选一措辞定性。

## 与前三份的不同

本脚本额外输出 2016 前后两段的 Welch 检验——因为本轮的研究问题本身就是"有没有断点"。
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
from stage3_sigma_d import load_close_matrix, month_end_positions  # noqa: E402
from run_gate3_longsample import EXCLUDED_SYMBOLS, K_MAIN, POOL_DIR  # noqa: E402

# ==================== 冻结参数（逐字照抄预登记 §2/§3/§4） ====================

PREREG_PATH = "docs/GATE4_LOWVOL_PREREG_2026-10-20.md"
PREREG_SHA256 = "94988f7c57d1e5f41c78d16330b704204d7453b39cdee4b34d0b40da6826687d"

VOL_WIN = 60              # §3 波动估计窗口
VOL_MIN_VALID = 54        # §3 90%
DIRECTION = "low_vol"     # §3 做多低波动

START, END_SIGNAL, END_HOLD = "2007-01-01", "2026-09-30", "2026-10-31"
SEG_A = ("2007-01-01", "2015-12-31")    # §4.2 段 A
SEG_B = ("2016-01-01", "2026-09-30")    # §4.2 段 B

SUCCESS_T = 1.645          # §4.1 单侧 5%
SUCCESS_MIN_N = 200
MDE_MAX_PP = 0.0142        # §4.1（文献效应量 12.9%/年 ≈ 1.08pp/月；取 1.42pp 与 Gate3 一致）
Z_MDE = 2.8016

SENS_K = [50, 110, 200]
SENS_VOL_WIN = [21, 60, 120]
COST_TIERS = [0.001, 0.003]

OUT_DIR = os.path.join("gate4_out", PREREG_SHA256[:12])


def verify_prereg() -> None:
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = hashlib.sha256(open(PREREG_PATH, "rb").read()).hexdigest()
    if got != PREREG_SHA256:
        raise SystemExit(
            "预登记 sha256 与冻结值不符，拒绝运行。\n"
            f"  期望 {PREREG_SHA256}\n  实际 {got}\n"
            "若确需改设计，请新开预登记并重新冻结（预登记 §7 第 1 条）。"
        )
    print(f"[gate4] 预登记校验 OK  {got[:16]}")


def welch_t(a, b) -> tuple[float, float]:
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return float("nan"), float("nan")
    ma, mb = sum(a) / na, sum(b) / nb
    va = statistics.variance(a) / na
    vb = statistics.variance(b) / nb
    se = math.sqrt(va + vb)
    if se == 0:
        return float("nan"), float("nan")
    t = (ma - mb) / se
    p = 2 * 0.5 * (1 - math.erf(abs(t) / math.sqrt(2)))
    return t, p


def stats_of(d: list[float]) -> dict:
    n = len(d)
    if n < 2:
        return {"n": n}
    mean = sum(d) / n
    sd = statistics.stdev(d)
    se = sd / math.sqrt(n)
    t = mean / se if se > 0 else float("nan")
    p_one = 0.5 * (1 - math.erf(t / math.sqrt(2))) if t == t else float("nan")
    return {"n": n, "mean_d": mean, "sd_d": sd, "se": se, "t_stat": t,
            "p_one_sided": p_one, "mde_pp": Z_MDE * se,
            "win_ratio": sum(1 for x in d if x > 0) / n, "min_d": min(d), "max_d": max(d)}


def judge(st: dict) -> dict:
    n, mde = st["n"], st["mde_pp"]
    if n < SUCCESS_MIN_N or mde > MDE_MAX_PP:
        reasons = []
        if n < SUCCESS_MIN_N:
            reasons.append(f"n={n} < {SUCCESS_MIN_N}")
        if mde > MDE_MAX_PP:
            reasons.append(f"MDE={mde*100:.3f}pp > {MDE_MAX_PP*100:.2f}pp")
        return {"verdict": "INCONCLUSIVE", "label": "证据不足", "rule_hits": reasons}
    if st["mean_d"] > 0 and st["t_stat"] >= SUCCESS_T:
        return {"verdict": "SUCCESS", "label": "通过", "rule_hits": [
            f"n={n} >= {SUCCESS_MIN_N}", f"MDE={mde*100:.3f}pp <= {MDE_MAX_PP*100:.2f}pp",
            f"mean(d)={st['mean_d']:.6f} > 0", f"t={st['t_stat']:.4f} >= {SUCCESS_T}"]}
    hits = []
    if st["mean_d"] <= 0:
        hits.append(f"mean(d)={st['mean_d']:.6f} <= 0")
    if st["t_stat"] < SUCCESS_T:
        hits.append(f"t={st['t_stat']:.4f} < {SUCCESS_T}")
    hits.append(f"n={n}, MDE={mde*100:.3f}pp（均满足前提）")
    return {"verdict": "FAILURE", "label": "失败", "rule_hits": hits}


def build_rows(close_df: pd.DataFrame, calendar, positions, vol_win: int, k: int) -> list[dict]:
    """逐月：按 vol_win 日波动排序，做多最低 k、做空最高 k。返回月度记录。"""
    ret = close_df.pct_change(fill_method=None).replace([float("inf"), float("-inf")], float("nan"))
    vol = ret.rolling(vol_win, min_periods=max(3, int(vol_win * 0.9))).std() * math.sqrt(250)
    ffill = close_df.ffill()
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        v = vol.iloc[pos].dropna()
        if len(v) < 3:
            continue
        fwd = (ffill.iloc[nxt] / ffill.iloc[pos] - 1.0).dropna()
        pool = v.index.intersection(fwd.index)
        # 信号日自身必须可定价
        pool = pool.intersection(close_df.iloc[pos].dropna().index)
        kk = min(k, len(pool) // 2)
        if kk < 1:
            continue
        ranked = v.loc[pool].sort_values(ascending=True)   # 低波动在前
        longs, shorts = ranked.index[:kk], ranked.index[-kk:]
        rl = float(fwd.loc[longs].mean())
        rs = float(fwd.loc[shorts].mean())
        out.append({
            "signal_date": str(calendar[pos].date()),
            "month": str(calendar[nxt].date()),
            "n_pool": int(len(pool)), "k_used": int(kk),
            "k_frac": round(kk / len(pool), 4),
            "vol_long_mean": float(v.loc[longs].mean()),
            "vol_short_mean": float(v.loc[shorts].mean()),
            "longs": list(longs), "shorts": list(shorts),
            "fwd": fwd,
            "r_long": rl, "r_short": rs, "d": 0.5 * (rl - rs),
        })
    return out


def turnover(rows: list[dict]) -> float:
    """实测单边换手/月（占组合总值），口径同 `STRATEGY_TURNOVER_2026-10-20.md`。"""
    tos = []
    prev_l: list[str] = []
    prev_s: list[str] = []
    for r in rows:
        fwd = r["fwd"]
        def leg_turn(curr, prev):
            if not curr:
                return float("nan")
            w_prev = {c: 1.0 / len(prev) for c in prev} if prev else {}
            drift, tot = {}, 0.0
            for c, w in w_prev.items():
                v = fwd.get(c, float("nan"))
                if v != v:
                    continue
                drift[c] = w * (1 + v)
                tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(curr)
            return 0.5 * sum(abs((wn if c in curr else 0.0) - drift.get(c, 0.0))
                             for c in set(drift) | set(curr))
        tl, ts = leg_turn(r["longs"], prev_l), leg_turn(r["shorts"], prev_s)
        if tl == tl and ts == ts:
            tos.append(0.5 * tl + 0.5 * ts)
        prev_l, prev_s = r["longs"], r["shorts"]
    tos = [x for i, x in enumerate(tos) if i > 0]
    return statistics.mean(tos) if tos else float("nan")


def main() -> int:
    verify_prereg()

    close_all, _ = load_close_matrix(POOL_DIR)
    close_df = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    cal = close_df.index
    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[gate4] 池 {close_df.shape[1]} 只 | 日历 {cal[0].date()} ~ {cal[-1].date()} | 调仓点 {len(positions)}")
    print(f"[gate4] 主检验 vol_win={VOL_WIN} k={K_MAIN} direction={DIRECTION}")

    rows = [r for r in build_rows(close_df, cal, positions, VOL_WIN, K_MAIN) if r["signal_date"] <= END_SIGNAL]
    d = [r["d"] for r in rows]
    st = stats_of(d)
    verdict = judge(st)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly_paired.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,k_used,k_frac,vol_long_mean,vol_short_mean,r_long,r_short,d\n")
        for r in rows:
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['k_used']},{r['k_frac']},"
                    f"{r['vol_long_mean']:.8f},{r['vol_short_mean']:.8f},"
                    f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f}\n")

    print()
    print("=== 主检验（全段 2007-01 ~ 2026-09，预登记 §4.1）===")
    print(f"  n = {st['n']}")
    print(f"  mean(d) = {st['mean_d']*100:+.4f} pp/月")
    print(f"  sd(d)   = {st['sd_d']*100:.4f} pp/月")
    print(f"  se      = {st['se']*100:.4f} pp")
    print(f"  t       = {st['t_stat']:+.4f}   (单侧 5% 临界 {SUCCESS_T})")
    print(f"  p(单侧) = {st['p_one_sided']:.6f}")
    print(f"  MDE     = {st['mde_pp']*100:.4f} pp  (上限 {MDE_MAX_PP*100:.2f}pp)")
    print(f"  胜率    = {st['win_ratio']*100:.2f}%")
    kf = [r["k_frac"] for r in rows]
    print(f"  k/n_pool = 中位 {statistics.median(kf)*100:.1f}%")
    vl = statistics.mean(r["vol_long_mean"] for r in rows)
    vs = statistics.mean(r["vol_short_mean"] for r in rows)
    print(f"  构造自检：低波动腿均波动 {vl*100:.1f}% vs 高波动腿 {vs*100:.1f}%  "
          f"({'✓ 两腿分离' if vl < vs else '✗ 异常'})")
    print()
    print(f"  >>> 主判定：{verdict['verdict']}（{verdict['label']}）")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    print()

    # ---------------- §4.2 必报：2016 分段 ----------------
    da = [r["d"] for r in rows if SEG_A[0] <= r["signal_date"] <= SEG_A[1]]
    db = [r["d"] for r in rows if SEG_B[0] <= r["signal_date"] <= SEG_B[1]]
    sa, sb = stats_of(da), stats_of(db)
    tw, pw = welch_t(da, db)
    print("=== §4.2 必报项：2016 分段（不参与判定）===")
    for tag, s, win in (("段A 2007-2015", sa, SEG_A), ("段B 2016-2026", sb, SEG_B)):
        print(f"  {tag}: n={s['n']:>3}  mean={s['mean_d']*100:+7.4f}pp  t={s['t_stat']:+6.3f}  "
              f"sd={s['sd_d']*100:5.2f}pp  MDE={s['mde_pp']*100:5.3f}pp  胜率={s['win_ratio']*100:5.1f}%")
    print(f"  两段差 Welch t = {tw:+.3f}  p = {pw:.4f}（双侧）")
    if pw == pw:
        if pw >= 0.05 and sa["t_stat"] >= SUCCESS_T and sb["t_stat"] >= SUCCESS_T:
            phrase = "效应跨段持续"
        elif pw < 0.05 and sa["t_stat"] >= SUCCESS_T and sb["t_stat"] < SUCCESS_T:
            phrase = "效应在 2016 后断裂"
        elif pw < 0.05 and sa["t_stat"] < SUCCESS_T and sb["t_stat"] >= SUCCESS_T:
            phrase = "效应在 2016 后才出现"
        else:
            phrase = "不足以判断是否存在断点"
        print(f"  >>> 分段判读（§4.2 四选一）：**{phrase}**")
    print()

    # ---------------- 分年（描述性） ----------------
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["signal_date"][:4], []).append(r["d"])
    yearly = {}
    print("=== 分年度（描述性）===")
    for y in sorted(by):
        v = by[y]
        yearly[y] = {"n": len(v), "mean_d_pp": round(sum(v) / len(v) * 100, 4),
                     "win_ratio": round(sum(1 for x in v if x > 0) / len(v), 4)}
        print(f"  {y}: n={len(v):>2}  mean={yearly[y]['mean_d_pp']:+7.3f}pp  胜率={yearly[y]['win_ratio']*100:5.1f}%")
    print()

    # ---------------- 敏感性（描述性） ----------------
    sens = []
    print("=== 敏感性（描述性，不参与判定）===")
    for k in SENS_K:
        rr = [r for r in build_rows(close_df, cal, positions, VOL_WIN, k) if r["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "k", "value": k, **{x: s.get(x) for x in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  k={k:>4}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    for vw in SENS_VOL_WIN:
        rr = [r for r in build_rows(close_df, cal, positions, vw, K_MAIN) if r["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "vol_win", "value": vw, **{x: s.get(x) for x in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  vol={vw:>3}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    print()
    with open(os.path.join(OUT_DIR, "sensitivity.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("param,value,n,mean_d,t_stat,sd_d,mde_pp\n")
        for r in sens:
            f.write(f"{r['param']},{r['value']},{r['n']},{r['mean_d']:.10f},{r['t_stat']:.6f},"
                    f"{r['sd_d']:.10f},{r['mde_pp']:.10f}\n")

    # ---------------- 实测换手与成本 ----------------
    to = turnover(rows)
    print("=== 实测换手与成本（描述性）===")
    print(f"  单边换手/月 = {to*100:.1f}%（年化单边 {to*12*100:.0f}%，双边成交额 {2*to*12*100:.0f}%）")
    cost = {}
    for c in COST_TIERS:
        cm = 2 * to * c * 100
        net = st["mean_d"] * 100 - cm
        cost[f"one_way_{c*100:.1f}pct"] = {"cost_pp_per_month": round(cm, 4), "mean_net_pp": round(net, 4),
                                           "t_net": round(net / (st["sd_d"] * 100 / math.sqrt(st["n"])), 3)}
        print(f"  单边 {c*100:.1f}%: 成本 {cm:.3f}pp/月（{cm*12:.2f}pp/年）→ 净 {net:+.4f}pp/月  t_net={cost[f'one_way_{c*100:.1f}pct']['t_net']:+.3f}")
    print()

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    summary = {
        "prereg_version": "gate4-lowvol-segmented-v4.0",
        "prereg_sha256": PREREG_SHA256,
        "pool": {"dir": POOL_DIR, "n_used": int(close_df.shape[1]), "excluded": [c for c in EXCLUDED_SYMBOLS if c in close_all.columns]},
        "window": [START, END_SIGNAL],
        "params": {"vol_win": VOL_WIN, "vol_min_valid": VOL_MIN_VALID, "k": K_MAIN,
                   "direction": DIRECTION, "rebalance": "monthly"},
        "main": clean(st),
        "judgement": verdict,
        "construct_check": {"vol_long_mean": vl, "vol_short_mean": vs},
        "segment_2016": {"A": clean(sa), "B": clean(sb), "welch_t": tw, "welch_p": pw,
                         "reading": phrase if pw == pw else None},
        "yearly_descriptive": yearly,
        "sensitivity_descriptive": [{k: (round(v, 10) if isinstance(v, float) else v) for k, v in r.items()} for r in sens],
        "turnover": {"one_side_per_month": to, "cost": cost},
        "note": "主判定只有全段一个；分段按 §4.2 四选一措辞定性，不参与判定。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["monthly_paired.csv", "sensitivity.csv", "summary.json"]
    manifest = {"prereg_version": "gate4-lowvol-segmented-v4.0", "prereg_sha256": PREREG_SHA256,
                "files": {n: {"sha256": hashlib.sha256(open(os.path.join(OUT_DIR, n), "rb").read()).hexdigest(),
                              "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files}}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"[gate4] 产物 -> {OUT_DIR}/")
    print(f"[gate4] 主判定：{verdict['verdict']}（{verdict['label']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
