#!/usr/bin/env python
"""Gate 5 运行脚本：交易活跃度（自身标准化成交额）异象 + 2016 分段。

预登记：docs/GATE5_TURNOVER_PREREG_2026-10-20.md
冻结 sha256：7c0c89916b0c56905c421db1d5860dd3fdadf5c2302928d1ad68290d44957775

## 与 Gate 2~4 的两个关键差别

1. **主检验是双侧的**（预登记 §0.3）：文献对"活跃度 → 未来收益"给出相反先验
   （A 股换手率水平异象 → 负；Gervais et al. 2001 异常成交量溢价 → 正），
   单侧检验等于把一半先验错误当成零假设。判据用 |t| ≥ 1.96，结论必须写明方向。
2. **信号不是真实换手率**（预登记 §0.2）：无历史股本数据，改用
   `amt_ratio = mean(amt, 21日) / mean(amt, 250日)`，以自身历史标准化剔除规模混淆。
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

PREREG_PATH = "docs/GATE5_TURNOVER_PREREG_2026-10-20.md"
PREREG_SHA256 = "7c0c89916b0c56905c421db1d5860dd3fdadf5c2302928d1ad68290d44957775"

EXEC_DIR = "data/exec_long"
SHORT_WIN, BASE_WIN = 21, 250        # §3
MIN_VALID_RATIO = 0.90

START, END_SIGNAL, END_HOLD = "2006-01-01", "2026-09-30", "2026-10-31"
SEG_A = ("2006-01-01", "2015-12-31")
SEG_B = ("2016-01-01", "2026-09-30")

SUCCESS_T_ABS = 1.96      # §4.1 双侧 5%
SUCCESS_MIN_N = 200
MDE_MAX_PP = 0.0142
Z_MDE = 2.8016

SENS_K = [50, 110, 200]
SENS_BASE = [60, 250]
COST_TIERS = [0.001, 0.003]

OUT_DIR = os.path.join("gate5_out", PREREG_SHA256[:12])


def load_volume_matrix(exec_dir: str, codes) -> pd.DataFrame:
    """读 EXEC 的 volume（单位：手）宽表。注意 exec 文件后缀是 .exec.csv，不是 .qfq.csv。"""
    import glob as _g
    import os as _o
    cols = {}
    for path in sorted(_g.glob(_o.path.join(exec_dir, "*.exec.csv"))):
        code = _o.path.basename(path).replace(".exec.csv", "")
        if code not in set(codes):
            continue
        df = pd.read_csv(path, usecols=["date", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        cols[code] = df.drop_duplicates(subset="date", keep="last").set_index("date")["volume"].astype(float)
    return pd.DataFrame(cols).sort_index()


def verify_prereg() -> None:
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = hashlib.sha256(open(PREREG_PATH, "rb").read()).hexdigest()
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符，拒绝运行。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    print(f"[gate5] 预登记校验 OK  {got[:16]}")


def welch_t(a, b):
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return float("nan"), float("nan")
    ma, mb = sum(a) / na, sum(b) / nb
    se = math.sqrt(statistics.variance(a) / na + statistics.variance(b) / nb)
    if se == 0:
        return float("nan"), float("nan")
    t = (ma - mb) / se
    return t, 2 * 0.5 * (1 - math.erf(abs(t) / math.sqrt(2)))


def stats_of(d):
    n = len(d)
    if n < 2:
        return {"n": n}
    mean = sum(d) / n
    sd = statistics.stdev(d)
    se = sd / math.sqrt(n)
    t = mean / se if se > 0 else float("nan")
    return {"n": n, "mean_d": mean, "sd_d": sd, "se": se, "t_stat": t,
            "p_two_sided": 2 * 0.5 * (1 - math.erf(abs(t) / math.sqrt(2))) if t == t else float("nan"),
            "mde_pp": Z_MDE * se, "win_ratio": sum(1 for x in d if x > 0) / n,
            "min_d": min(d), "max_d": max(d)}


def judge(st):
    """§4.1 双侧判定。"""
    n, mde = st["n"], st["mde_pp"]
    if n < SUCCESS_MIN_N or mde > MDE_MAX_PP:
        reasons = []
        if n < SUCCESS_MIN_N:
            reasons.append(f"n={n} < {SUCCESS_MIN_N}")
        if mde > MDE_MAX_PP:
            reasons.append(f"MDE={mde*100:.3f}pp > {MDE_MAX_PP*100:.2f}pp")
        return {"verdict": "INCONCLUSIVE", "label": "证据不足", "rule_hits": reasons}
    if abs(st["t_stat"]) >= SUCCESS_T_ABS:
        direction = "高活跃组占优（d>0）" if st["t_stat"] > 0 else "低活跃组占优（d<0）"
        return {"verdict": "SUCCESS", "label": "通过", "direction": direction, "rule_hits": [
            f"n={n} >= {SUCCESS_MIN_N}", f"MDE={mde*100:.3f}pp <= {MDE_MAX_PP*100:.2f}pp",
            f"|t|={abs(st['t_stat']):.4f} >= {SUCCESS_T_ABS}", f"方向：{direction}"]}
    return {"verdict": "FAILURE", "label": "失败（未检出）", "rule_hits": [
        f"|t|={abs(st['t_stat']):.4f} < {SUCCESS_T_ABS}",
        f"n={n}, MDE={mde*100:.3f}pp（均满足前提）"]}


def build_rows(close_df, vol_df, calendar, positions, short_win, base_win, k):
    """成交额 = ADJ close × volume（volume 单位：手，仅用于排序）。"""
    amt = close_df * vol_df.reindex_like(close_df)
    amt = amt.where(amt > 0)
    ffill = close_df.ffill()
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        a_short = amt.iloc[max(0, pos - short_win + 1):pos + 1]
        a_base = amt.iloc[max(0, pos - base_win + 1):pos + 1]
        s_ok = a_short.notna().sum() >= math.ceil(short_win * MIN_VALID_RATIO)
        b_ok = a_base.notna().sum() >= math.ceil(base_win * MIN_VALID_RATIO)
        s21 = a_short.mean().where(s_ok)
        b250 = a_base.mean().where(b_ok)
        ratio = (s21 / b250).where(b250 > 0)
        price_ok = close_df.iloc[pos].notna()
        ratio = ratio.where(price_ok).dropna()
        fwd = (ffill.iloc[nxt] / ffill.iloc[pos] - 1.0).dropna()
        pool = ratio.index.intersection(fwd.index)
        kk = min(k, len(pool) // 2)
        if kk < 1:
            continue
        ranked = ratio.loc[pool].sort_values(ascending=False)   # 高活跃在前
        highs, lows = ranked.index[:kk], ranked.index[-kk:]
        rh = float(fwd.loc[highs].mean())
        rl = float(fwd.loc[lows].mean())
        out.append({"signal_date": str(calendar[pos].date()), "month": str(calendar[nxt].date()),
                    "n_pool": int(len(pool)), "k_used": int(kk), "k_frac": round(kk / len(pool), 4),
                    "ratio_high_mean": float(ratio.loc[highs].mean()),
                    "ratio_low_mean": float(ratio.loc[lows].mean()),
                    "highs": list(highs), "lows": list(lows), "fwd": fwd,
                    "r_high": rh, "r_low": rl, "d": 0.5 * (rh - rl)})
    return out


def turnover(rows):
    tos, pl, plw = [], [], []
    for r in rows:
        fwd = r["fwd"]

        def leg(curr, prev):
            if not curr:
                return float("nan")
            wp = {c: 1.0 / len(prev) for c in prev} if prev else {}
            drift, tot = {}, 0.0
            for c, w in wp.items():
                v = fwd.get(c, float("nan"))
                if v != v:
                    continue
                drift[c] = w * (1 + v)
                tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(curr)
            return 0.5 * sum(abs((wn if c in curr else 0.0) - drift.get(c, 0.0)) for c in set(drift) | set(curr))
        th, tl = leg(r["highs"], pl), leg(r["lows"], plw)
        if th == th and tl == tl:
            tos.append(0.5 * th + 0.5 * tl)
        pl, plw = r["highs"], r["lows"]
    tos = [x for i, x in enumerate(tos) if i > 0]
    return statistics.mean(tos) if tos else float("nan")


def main() -> int:
    verify_prereg()
    close_all, _ = load_close_matrix(POOL_DIR)
    close_df = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    vol_df = load_volume_matrix(EXEC_DIR, close_df.columns).reindex(close_df.index)
    cal = close_df.index
    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[gate5] 池 {close_df.shape[1]} 只 | 调仓点 {len(positions)} | short={SHORT_WIN} base={BASE_WIN} k={K_MAIN}")

    rows = [r for r in build_rows(close_df, vol_df, cal, positions, SHORT_WIN, BASE_WIN, K_MAIN)
            if r["signal_date"] <= END_SIGNAL]
    d = [r["d"] for r in rows]
    st = stats_of(d)
    verdict = judge(st)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly_paired.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,k_used,k_frac,ratio_high_mean,ratio_low_mean,r_high,r_low,d\n")
        for r in rows:
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['k_used']},{r['k_frac']},"
                    f"{r['ratio_high_mean']:.8f},{r['ratio_low_mean']:.8f},"
                    f"{r['r_high']:.10f},{r['r_low']:.10f},{r['d']:.10f}\n")

    print()
    print("=== 主检验（双侧，2006-01 ~ 2026-09，§4.1）===")
    print(f"  n = {st['n']}")
    print(f"  mean(d) = {st['mean_d']*100:+.4f} pp/月    [d = 0.5×(高活跃组 − 低活跃组)]")
    print(f"  sd(d)   = {st['sd_d']*100:.4f} pp/月")
    print(f"  t       = {st['t_stat']:+.4f}   (双侧 5% 临界 |t| ≥ {SUCCESS_T_ABS})")
    print(f"  p(双侧) = {st['p_two_sided']:.4f}")
    print(f"  MDE     = {st['mde_pp']*100:.4f} pp  (上限 {MDE_MAX_PP*100:.2f}pp)")
    print(f"  胜率    = {st['win_ratio']*100:.2f}%")
    print(f"  构造自检：高活跃腿 amt_ratio {statistics.mean(r['ratio_high_mean'] for r in rows):.3f} "
          f"vs 低活跃腿 {statistics.mean(r['ratio_low_mean'] for r in rows):.3f}")
    print()
    print(f"  >>> 主判定：{verdict['verdict']}（{verdict['label']}）"
          + (f" 方向：{verdict['direction']}" if verdict.get("direction") else ""))
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    print()

    da = [r["d"] for r in rows if SEG_A[0] <= r["signal_date"] <= SEG_A[1]]
    db = [r["d"] for r in rows if SEG_B[0] <= r["signal_date"] <= SEG_B[1]]
    sa, sb = stats_of(da), stats_of(db)
    tw, pw = welch_t(da, db)
    print("=== §4.2 必报项：2016 分段（不参与判定）===")
    for tag, s in (("段A 2006-2015", sa), ("段B 2016-2026", sb)):
        print(f"  {tag}: n={s['n']:>3}  mean={s['mean_d']*100:+7.4f}pp  t={s['t_stat']:+6.3f}  "
              f"sd={s['sd_d']*100:5.2f}pp  MDE={s['mde_pp']*100:5.3f}pp  胜率={s['win_ratio']*100:5.1f}%")
    print(f"  两段差 Welch t = {tw:+.3f}  p = {pw:.4f}")
    if pw == pw:
        if pw >= 0.05 and abs(sa["t_stat"]) >= SUCCESS_T_ABS and abs(sb["t_stat"]) >= SUCCESS_T_ABS:
            phrase = "效应跨段持续"
        elif pw < 0.05 and abs(sa["t_stat"]) >= SUCCESS_T_ABS and abs(sb["t_stat"]) < SUCCESS_T_ABS:
            phrase = "效应在 2016 后断裂"
        elif pw < 0.05 and abs(sa["t_stat"]) < SUCCESS_T_ABS and abs(sb["t_stat"]) >= SUCCESS_T_ABS:
            phrase = "效应在 2016 后才出现"
        else:
            phrase = "不足以判断是否存在断点"
        print(f"  >>> 分段判读（§4.2 四选一）：**{phrase}**")
    print()

    by = {}
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

    sens = []
    print("=== 敏感性（描述性）===")
    for k in SENS_K:
        rr = [r for r in build_rows(close_df, vol_df, cal, positions, SHORT_WIN, BASE_WIN, k) if r["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "k", "value": k, **{x: s.get(x) for x in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  k={k:>4}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    for bw in SENS_BASE:
        rr = [r for r in build_rows(close_df, vol_df, cal, positions, SHORT_WIN, bw, K_MAIN) if r["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "base_win", "value": bw, **{x: s.get(x) for x in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  base={bw:>3}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")
    print()
    with open(os.path.join(OUT_DIR, "sensitivity.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("param,value,n,mean_d,t_stat,sd_d,mde_pp\n")
        for r in sens:
            f.write(f"{r['param']},{r['value']},{r['n']},{r['mean_d']:.10f},{r['t_stat']:.6f},{r['sd_d']:.10f},{r['mde_pp']:.10f}\n")

    to = turnover(rows)
    print("=== 实测换手与成本（描述性）===")
    print(f"  单边换手/月 = {to*100:.1f}%（年化双边成交额 {2*to*12*100:.0f}%）")
    cost = {}
    for c in COST_TIERS:
        cm = 2 * to * c * 100
        net = st["mean_d"] * 100 - cm
        cost[f"one_way_{c*100:.1f}pct"] = {"cost_pp_per_month": round(cm, 4), "mean_net_pp": round(net, 4),
                                           "t_net": round(net / (st["sd_d"] * 100 / math.sqrt(st["n"])), 3)}
        print(f"  单边 {c*100:.1f}%: 成本 {cm:.3f}pp/月 → 净 {net:+.4f}pp/月  t_net={cost[f'one_way_{c*100:.1f}pct']['t_net']:+.3f}")
    print()

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    summary = {
        "prereg_version": "gate5-turnover-segmented-v5.0",
        "prereg_sha256": PREREG_SHA256,
        "pool": {"dir": POOL_DIR, "n_used": int(close_df.shape[1])},
        "window": [START, END_SIGNAL],
        "params": {"short_win": SHORT_WIN, "base_win": BASE_WIN, "k": K_MAIN, "two_sided": True},
        "main": clean(st),
        "judgement": verdict,
        "segment_2016": {"A": clean(sa), "B": clean(sb), "welch_t": tw, "welch_p": pw,
                         "reading": phrase if pw == pw else None},
        "yearly_descriptive": yearly,
        "sensitivity_descriptive": [{k: (round(v, 10) if isinstance(v, float) else v) for k, v in r.items()} for r in sens],
        "turnover": {"one_side_per_month": to, "cost": cost},
        "note": "信号为自身标准化成交额代理，非真实换手率（无历史股本）；主判定为双侧。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    files = ["monthly_paired.csv", "sensitivity.csv", "summary.json"]
    manifest = {"prereg_version": "gate5-turnover-segmented-v5.0", "prereg_sha256": PREREG_SHA256,
                "files": {n: {"sha256": hashlib.sha256(open(os.path.join(OUT_DIR, n), "rb").read()).hexdigest(),
                              "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files}}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"[gate5] 产物 -> {OUT_DIR}/")
    print(f"[gate5] 主判定：{verdict['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
