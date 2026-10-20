#!/usr/bin/env python
"""Gate 6：在**无选择偏差的 as-of 全 A 宇宙**上重跑短期反转。

预登记：docs/GATE6_ALLASOF_PREREG_2026-10-21.md
冻结 sha256：b8c4f1ecba44cb44e384761949072661f1d001e8cd8962b4b5e73abd3c9ec673

## 与 Gate 2/3 的三点不同

1. **宇宙**：不是 532 只池，而是每个调仓日按 as-of 规则筛出的全 A 可投资集合
   （上市满 252 个交易日 + 当日仍在交易 + 当日可定价）。
2. **主持仓数**：比例 k = round(0.20 × n_pool)（文献 quintile）；固定 k=110 只作对照。
3. **判据双侧**：在一个新宇宙上，文献（正）与 Gate 2（近十年负）的方向先验冲突。

## 前置（必须先满足，否则拒绝运行）

    python scripts/rebuild_adj_from_exec.py --pool-manifest data/_all_a_manifest.json \
        --exec-dir data/exec_all --div-source merge --out data/qfq_all
    python scripts/verify_exec_all.py      # V2 交叉验证必须 0 不一致
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数（逐字照抄预登记 §3/§4） ====================

PREREG_PATH = "docs/GATE6_ALLASOF_PREREG_2026-10-21.md"
PREREG_SHA256 = "b8c4f1ecba44cb44e384761949072661f1d001e8cd8962b4b5e73abd3c9ec673"

ADJ_DIR = "data/qfq_all"
ACCEPT_PATH = "data/exec_all/_acceptance.json"

LOOKBACK, SKIP = 21, 1
MIN_VALID_RATIO = 0.90
K_FRAC = 0.20            # 主：比例 quintile
K_FIXED = 110            # 对照：与 Gate 2/3 同参数
MIN_LISTED_DAYS = 252    # §1 上市满 12 个月

START, END_SIGNAL, END_HOLD = "2007-01-01", "2026-09-30", "2026-10-31"
SEG_A = ("2007-01-01", "2015-12-31")
SEG_B = ("2016-01-01", "2026-09-30")

SUCCESS_T_ABS = 1.96
SUCCESS_MIN_N = 200
MDE_MAX_PP = 0.0142
Z_MDE = 2.8016

SENS_FRAC = [0.10, 0.20, 0.30]
COST_TIERS = [0.001, 0.003]

OUT_DIR = os.path.join("gate6_out", PREREG_SHA256[:12])


def verify() -> None:
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = hashlib.sha256(open(PREREG_PATH, "rb").read()).hexdigest()
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符，拒绝运行。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    print(f"[gate6] 预登记校验 OK  {got[:16]}")
    if not os.path.exists(ACCEPT_PATH):
        raise SystemExit(f"缺少验收文件 {ACCEPT_PATH}；请先跑 scripts/verify_exec_all.py")
    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("v2_bad"):
        raise SystemExit(f"验收 V2 未通过（{len(acc['v2_bad'])} 只不一致），拒绝运行")
    print(f"[gate6] 验收 V2 通过：{acc['v2_checked']} 只 0 不一致；落盘 {acc['n_have']}/{acc['n_target']}")


def load_adj(adj_dir: str) -> pd.DataFrame:
    import glob as _g
    cols = {}
    for path in sorted(_g.glob(os.path.join(adj_dir, "*.qfq.csv"))):
        code = os.path.basename(path).replace(".qfq.csv", "")
        df = pd.read_csv(path, usecols=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        cols[code] = df.drop_duplicates(subset="date", keep="last").set_index("date")["close"].astype(float)
    return pd.DataFrame(cols).sort_index()


def build_universe_mask(close: pd.DataFrame) -> pd.DataFrame:
    """as-of 可投资掩码：累计有效交易日 ≥ 252 且当日有价。"""
    valid = close.notna() & (close > 0)
    cum = valid.cumsum(axis=0)
    return valid & (cum >= MIN_LISTED_DAYS)


def build_rows(close: pd.DataFrame, mask: pd.DataFrame, calendar, positions, k_frac: float | None,
               k_fixed: int | None) -> list[dict]:
    logp = np.log(close.where(close > 0))
    ffill = close.ffill()
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        ok = mask.iloc[pos]
        pool0 = ok[ok].index
        if len(pool0) < 10:
            continue
        # 信号：21 日收益，跳 1 日
        s0, s1 = pos - (SKIP + LOOKBACK - 1), pos - SKIP
        win = close.iloc[s0:s1 + 1][pool0]
        nd = win.notna().sum(axis=0)
        p_start, p_end = win.iloc[0], win.iloc[-1]
        good = (nd >= math.ceil(LOOKBACK * MIN_VALID_RATIO)) & p_start.notna() & p_end.notna()
        sc = (p_end / p_start - 1.0).where(good).dropna()
        fwd = (ffill.iloc[nxt][pool0] / ffill.iloc[pos][pool0] - 1.0).dropna()
        pool = sc.index.intersection(fwd.index)
        n_pool = len(pool)
        if n_pool < 10:
            continue
        k = int(round(k_frac * n_pool)) if k_frac else int(k_fixed)
        k = max(1, min(k, n_pool // 2))
        ranked = sc.loc[pool].sort_values(ascending=True)     # 反转：低分在前
        longs, shorts = ranked.index[:k], ranked.index[-k:]
        rl = float(fwd.loc[longs].mean())
        rs = float(fwd.loc[shorts].mean())
        out.append({
            "signal_date": str(calendar[pos].date()), "month": str(calendar[nxt].date()),
            "n_pool": int(n_pool), "k_used": int(k), "k_frac": round(k / n_pool, 4),
            "longs": list(longs), "shorts": list(shorts), "fwd": fwd,
            "r_long": rl, "r_short": rs, "d": 0.5 * (rl - rs),
        })
    return out


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
    n, mde = st["n"], st["mde_pp"]
    if n < SUCCESS_MIN_N or mde > MDE_MAX_PP:
        return {"verdict": "INCONCLUSIVE", "label": "证据不足",
                "rule_hits": [f"n={n} < {SUCCESS_MIN_N}" if n < SUCCESS_MIN_N else "",
                              f"MDE={mde*100:.3f}pp > {MDE_MAX_PP*100:.2f}pp" if mde > MDE_MAX_PP else ""]}
    if abs(st["t_stat"]) >= SUCCESS_T_ABS:
        direction = "反转成立（低分组占优，d>0）" if st["t_stat"] > 0 else "反转反向成立（高分组占优，d<0）"
        return {"verdict": "SUCCESS", "label": "通过", "direction": direction, "rule_hits": [
            f"n={n} >= {SUCCESS_MIN_N}", f"MDE={mde*100:.3f}pp <= {MDE_MAX_PP*100:.2f}pp",
            f"|t|={abs(st['t_stat']):.4f} >= {SUCCESS_T_ABS}", f"方向：{direction}"]}
    return {"verdict": "FAILURE", "label": "失败（未检出）",
            "rule_hits": [f"|t|={abs(st['t_stat']):.4f} < {SUCCESS_T_ABS}",
                          f"n={n}, MDE={mde*100:.3f}pp（均满足前提）"]}


def welch_t(a, b):
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return float("nan"), float("nan")
    se = math.sqrt(statistics.variance(a) / na + statistics.variance(b) / nb)
    t = (sum(a) / na - sum(b) / nb) / se
    return t, 2 * 0.5 * (1 - math.erf(abs(t) / math.sqrt(2)))


def turnover(rows):
    tos, pl, ps_ = [], [], []
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
        tl, ts = leg(r["longs"], pl), leg(r["shorts"], ps_)
        if tl == tl and ts == ts:
            tos.append(0.5 * tl + 0.5 * ts)
        pl, ps_ = r["longs"], r["shorts"]
    tos = [x for i, x in enumerate(tos) if i > 0]
    return statistics.mean(tos) if tos else float("nan")


def main() -> int:
    verify()
    print("[gate6] 读取全 A ADJ（约需数分钟）…")
    close = load_adj(ADJ_DIR)
    cal = close.index
    print(f"[gate6] ADJ {close.shape[1]} 只 × {len(cal)} 天 ({cal[0].date()} ~ {cal[-1].date()})")

    mask = build_universe_mask(close)
    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[gate6] 调仓点 {len(positions)}")

    rows = [r for r in build_rows(close, mask, cal, positions, K_FRAC, None) if r["signal_date"] <= END_SIGNAL]
    d = [r["d"] for r in rows]
    st = stats_of(d)
    verdict = judge(st)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly_paired.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,k_used,k_frac,r_long,r_short,d\n")
        for r in rows:
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['k_used']},{r['k_frac']},"
                    f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f}\n")

    npool_by_year = {}
    for r in rows:
        npool_by_year.setdefault(r["signal_date"][:4], []).append(r["n_pool"])

    print()
    print("=== 主检验（as-of 全 A，比例 k=20%，双侧）===")
    print(f"  n = {st['n']}")
    print(f"  mean(d) = {st['mean_d']*100:+.4f} pp/月")
    print(f"  sd(d)   = {st['sd_d']*100:.4f} pp/月")
    print(f"  t       = {st['t_stat']:+.4f}   (双侧临界 |t| ≥ {SUCCESS_T_ABS})")
    print(f"  p(双侧) = {st['p_two_sided']:.4f}")
    print(f"  MDE     = {st['mde_pp']*100:.4f} pp")
    print(f"  胜率    = {st['win_ratio']*100:.2f}%")
    print(f"  池规模中位 {statistics.median(r['n_pool'] for r in rows):.0f} 只（k_used 中位 {statistics.median(r['k_used'] for r in rows):.0f}）")
    print()
    print(f"  >>> 主判定：{verdict['verdict']}（{verdict['label']}）"
          + (f" 方向：{verdict['direction']}" if verdict.get("direction") else ""))
    for h in verdict["rule_hits"]:
        if h:
            print(f"      · {h}")
    print()

    print("=== as-of 宇宙规模（按年，中位）===")
    for y in sorted(npool_by_year):
        print(f"  {y}: n_pool 中位 {statistics.median(npool_by_year[y]):.0f}")

    da = [r["d"] for r in rows if SEG_A[0] <= r["signal_date"] <= SEG_A[1]]
    db = [r["d"] for r in rows if SEG_B[0] <= r["signal_date"] <= SEG_B[1]]
    sa, sb = stats_of(da), stats_of(db)
    tw, pw = welch_t(da, db)
    print()
    print("=== §4.2 必报：2016 分段（不参与判定）===")
    for tag, s in (("段A 2007-2015", sa), ("段B 2016-2026", sb)):
        print(f"  {tag}: n={s['n']:>3} mean={s['mean_d']*100:+7.4f}pp t={s['t_stat']:+6.3f} "
              f"sd={s['sd_d']*100:5.2f}pp MDE={s['mde_pp']*100:5.3f}pp 胜率={s['win_ratio']*100:5.1f}%")
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
        print(f"  >>> 分段判读：**{phrase}**")

    # 对照：固定 k=110（描述性）
    rows_fixed = [r for r in build_rows(close, mask, cal, positions, None, K_FIXED) if r["signal_date"] <= END_SIGNAL]
    sf = stats_of([r["d"] for r in rows_fixed])
    print()
    print(f"=== §4.3 对照：固定 k=110（与 Gate 2/3 同参数，描述性）===")
    print(f"  n={sf['n']}  mean={sf['mean_d']*100:+.4f}pp  t={sf['t_stat']:+.3f}  MDE={sf['mde_pp']*100:.3f}pp")
    print("  （不得与 Gate 2/3 的 532 只池结果相减；两者宇宙不同）")

    by = {}
    for r in rows:
        by.setdefault(r["signal_date"][:4], []).append(r["d"])
    yearly = {y: {"n": len(v), "mean_d_pp": round(sum(v) / len(v) * 100, 4),
                  "win_ratio": round(sum(1 for x in v if x > 0) / len(v), 4)} for y, v in sorted(by.items())}
    print()
    print("=== 分年度（描述性）===")
    for y, v in yearly.items():
        print(f"  {y}: n={v['n']:>2} mean={v['mean_d_pp']:+7.3f}pp 胜率={v['win_ratio']*100:5.1f}%")

    sens = []
    print()
    print("=== 敏感性（描述性）===")
    for fr in SENS_FRAC:
        rr = [r for r in build_rows(close, mask, cal, positions, fr, None) if r["signal_date"] <= END_SIGNAL]
        s = stats_of([x["d"] for x in rr])
        sens.append({"param": "k_frac", "value": fr, **{x: s.get(x) for x in ("n", "mean_d", "t_stat", "sd_d", "mde_pp")}})
        print(f"  k_frac={fr:.2f}: n={s['n']:>3} mean={s['mean_d']*100:+7.3f}pp t={s['t_stat']:+.3f} MDE={s['mde_pp']*100:.3f}pp")

    to = turnover(rows)
    print()
    print(f"=== 实测换手（描述性）=== 单边 {to*100:.1f}%/月（年化双边 {2*to*12*100:.0f}%）")
    cost = {}
    for c in COST_TIERS:
        cm = 2 * to * c * 100
        net = st["mean_d"] * 100 - cm
        cost[f"one_way_{c*100:.1f}pct"] = {"cost_pp_per_month": round(cm, 4), "mean_net_pp": round(net, 4),
                                          "t_net": round(net / (st["sd_d"] * 100 / math.sqrt(st["n"])), 3)}
        print(f"  单边 {c*100:.1f}%: 成本 {cm:.3f}pp/月 → 净 {net:+.4f}pp/月  t_net={cost[f'one_way_{c*100:.1f}pct']['t_net']:+.3f}")

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    summary = {
        "prereg_version": "gate6-reversal-allasof-v6.0",
        "prereg_sha256": PREREG_SHA256,
        "universe": "as-of 全 A（上市≥252 交易日 + 当日可交易）",
        "adj_dir": ADJ_DIR, "n_symbols": int(close.shape[1]),
        "window": [START, END_SIGNAL],
        "params": {"lookback": LOOKBACK, "skip": SKIP, "k_frac": K_FRAC, "k_fixed_control": K_FIXED,
                   "min_listed_days": MIN_LISTED_DAYS, "two_sided": True},
        "main": clean(st), "judgement": verdict,
        "n_pool_median_by_year": {y: statistics.median(v) for y, v in sorted(npool_by_year.items())},
        "segment_2016": {"A": clean(sa), "B": clean(sb), "welch_t": tw, "welch_p": pw,
                         "reading": phrase if pw == pw else None},
        "control_k110": clean(sf),
        "yearly_descriptive": yearly,
        "sensitivity_descriptive": [{k: (round(v, 10) if isinstance(v, float) else v) for k, v in r.items()} for r in sens],
        "turnover": {"one_side_per_month": to, "cost": cost},
        "note": "主判定为双侧；分段与 k=110 对照不参与判定；不得与 532 只池结果相减。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    files = ["monthly_paired.csv", "summary.json"]
    manifest = {"prereg_version": "gate6-reversal-allasof-v6.0", "prereg_sha256": PREREG_SHA256,
                "files": {n: {"sha256": hashlib.sha256(open(os.path.join(OUT_DIR, n), "rb").read()).hexdigest(),
                              "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files}}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[gate6] 产物 -> {OUT_DIR}/")
    print(f"[gate6] 主判定：{verdict['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
