#!/usr/bin/env python
"""阶段 C-3：信号到成交的延迟成本。

预登记：`docs/PHASE_C3_EXECUTION_LAG_PREREG_2026-10-21.md`
冻结 sha256：`6a76a10687b468156e6a953c86f3974384399b45b0f3c882f913345492c75b37`

## 问题

Gate 2/3/6 与阶段 C 的收益全是 `ffill(nxt)/ffill(pos) − 1`：
信号在 pos 日收盘生成，成交价也用 pos 日收盘。这是**看到收盘价的同时按收盘价成交**，
不可能执行。真实最早 pos+1。

## 三个格（共用同一批持仓，只改收益起算点）

- **V0** = 阶段 C 的 D 格（`ffill(nxt)/ffill(pos)`），必须逐位复现
- **V1** = `ffill(nxt)/ffill(pos+1)`，纯延迟一天
- **V2** = `ffill(nxt)/ffill(e)`，e = 从 pos+1 起第一个 `tradable` 的交易日（最多 5 日）

## ⚠️ 预登记的一处内部不一致（按 §1 执行，记录在案）

§1 写"三格用**同一批持仓**，含阶段 C §5 的约束"；§3.2 却写"V1 无条件在 pos+1 成交，
可能买到当天涨停的票；V2 顺延后避开涨停"。**两者不能同时成立**——涨停票已被 §5 剔出腿了。

本脚本按 **§1** 执行（它更基础，且是"Δ 里不含选股差异"这一设计成立的前提）。
后果：V2 的顺延实际只处理**停牌**，不处理涨停。报告里如实登记。

用法
----
    python scripts/run_phase_c3_exec_lag.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    ADJ_DIR, END_HOLD, END_SIGNAL, K_FRAC, MIN_LISTED_DAYS, NW_LAG, Panels, START,
    apply_constraints, build_mask, cost_of, flags_fast, load_close, pair_diff,
    stream_bars, turnover_of,
)
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数 ====================

PREREG_PATH = "docs/PHASE_C3_EXECUTION_LAG_PREREG_2026-10-21.md"
PREREG_SHA256 = "6a76a10687b468156e6a953c86f3974384399b45b0f3c882f913345492c75b37"

PHASE_C_PREREG = "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md"
PHASE_C_PREREG_SHA256 = "3a01759a37f03c57ec19a6090f52bbda11ed5a284271edfc4328504de64d1ab3"

ACCEPT_PATH = "data/exec_all/_acceptance.json"
MIN_QFQ_FILES = 5400

MAX_DELAY_DAYS = 5          # 顺延上限（ADJ 交易日）
NW_LAG_C3 = NW_LAG
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30
SEG2_END = 5                # D1 分解：第 1 天 / 第 2~5 天 / 剩余

# 阶段 C D 格必须逐位复现（单位 pp，从产物抄，不手算——C-2 在这里踩过一次）
EXPECT_V0 = {"n": 237, "mean_d": 0.65818965, "mean_spread": 1.31637930,
             "t_stat": 4.1110485798}
REPRO_TOL = {"n": 0, "mean_d": 1e-6, "mean_spread": 1e-6, "t_stat": 1e-6}

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasec3_out", PREREG_SHA12)


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def verify() -> dict:
    info: dict = {}
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = _sha(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符，拒绝运行。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[C3] ① 本预登记校验 OK  {got[:16]}")

    c1 = _sha(PHASE_C_PREREG)
    if c1 != PHASE_C_PREREG_SHA256:
        raise SystemExit(f"阶段 C 预登记 sha256 不符：{c1}")
    info["phase_c_prereg_sha256"] = c1
    print(f"[C3] ② 阶段 C 预登记 OK  {c1[:16]}")

    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0 or acc.get("v2_bad"):
        raise SystemExit(f"exec_all 验收未通过：{acc.get('n_missing')} / {acc.get('v2_bad')}")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target")}
    print(f"[C3] ③ exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}")

    n = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n} < {MIN_QFQ_FILES}")
    info["n_qfq_files"] = n
    print(f"[C3] ④ qfq_all 覆盖 OK  {n}")

    info["params_hardcoded"] = True
    print("[C3] ⑤ 参数硬编码 OK")
    return info


def first_tradable(code: str, start: int, cal, bars: dict, ex_aligned: dict,
                   max_days: int = MAX_DELAY_DAYS) -> tuple[int | None, str | None, int]:
    """从 start 起第一个 `tradable` 的交易日。返回 (索引, 日期, 顺延天数)。

    顺延天数 0 = pos+1 就能成交；`max_days` = 超时（该标的当月剔除）。
    """
    for k in range(max_days):
        j = start + k
        if j >= len(cal):
            break
        day = str(cal[j].date())
        f = flags_fast(bars.get(code, {}).get(day), code, day, ex_aligned.get(code, set()))
        if f["tradable"]:
            return j, day, k
    return None, None, max_days


def reprice(rows: list[dict], ffill, cal, mode: str, bars: dict, ex_aligned: dict,
            pos_of: dict[str, int]) -> list[dict]:
    """在同一批持仓上，只改收益起算点。返回新的 d（以及顺延诊断）。"""
    out = []
    for r in rows:
        pos = pos_of[r["signal_date"]]
        nxt = pos_of[r["month"]]
        entry0 = pos + 1
        if entry0 >= len(cal):
            continue
        longs, shorts = r["longs_c"], r["shorts_c"]
        if not longs or not shorts:
            continue
        p_end = ffill.iloc[nxt]

        delay_hist: dict[int, int] = defaultdict(int)
        timed_out = 0
        fwd: dict[str, float] = {}
        for c in longs + shorts:
            if mode == "V1":
                j, d, k = entry0, str(cal[entry0].date()), 0
            else:                                     # V2：顺延到可成交
                j, d, k = first_tradable(c, entry0, cal, bars, ex_aligned)
                if j is None:
                    timed_out += 1
                    continue
            delay_hist[k] += 1
            p0 = ffill.iloc[j].get(c)
            p1 = p_end.get(c)
            if p0 is None or p1 is None or p0 != p0 or p1 != p1 or p0 <= 0:
                continue
            fwd[c] = float(p1 / p0 - 1.0)

        kl = [c for c in longs if c in fwd]
        ks = [c for c in shorts if c in fwd]
        if not kl or not ks:
            continue
        rl = statistics.mean(fwd[c] for c in kl)
        rs = statistics.mean(fwd[c] for c in ks)
        out.append({**r, "d_repriced": 0.5 * (rl - rs), "r_long_rep": rl, "r_short_rep": rs,
                    "n_kept_long": len(kl), "n_kept_short": len(ks),
                    "n_timeout": timed_out, "delay_hist": dict(delay_hist)})
    return out


def judge(nw: dict) -> dict:
    n, eff, t = nw["n"], nw["n_eff"], nw["t_nw"]
    mean_pp = abs(nw["mean_pp"]) if nw["mean_pp"] == nw["mean_pp"] else float("nan")
    if n < MIN_N_MAIN:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"n={n} < {MIN_N_MAIN}"]}
    if eff < MIN_NW_EFF:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"NW 有效样本 {eff} < {MIN_NW_EFF}"]}
    sig = abs(t) >= T_CRIT
    big = mean_pp >= ECON_MIN_PP
    if sig and big:
        return {"verdict": "LAG_COST_MATERIAL",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp >= {ECON_MIN_PP}pp"],
                "direction": "延迟使效应变小" if nw["mean"] < 0 else "延迟使效应变大"}
    if not sig:
        return {"verdict": "LAG_COST_NEGLIGIBLE",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp"]}
    return {"verdict": "LAG_COST_SMALL_BUT_SIGNIFICANT",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                          f"|mean|={mean_pp:.4f}pp < {ECON_MIN_PP}pp"]}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 C-3：信号到成交的延迟成本")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG_C3}")
    print("=" * 78)
    vi = verify()
    print()

    close = load_close()
    cal = close.index
    pos_of = {str(cal[i].date()): i for i in range(len(cal))}
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[C3] 调仓点 {len(positions)} 个")

    mask = build_mask(close, MIN_LISTED_DAYS)
    rows_d = panels.month_rows(mask, positions, "frac")
    print(f"[C3] D 格选股 {len(rows_d)} 月")

    # 顺延需要 pos+1..pos+5 的所有交易日，所以 needed 要覆盖这一段
    need: dict[str, set[str]] = defaultdict(set)
    for r in rows_d:
        pos = pos_of[r["signal_date"]]
        nxt = pos_of[r["month"]]
        for k in range(1, MAX_DELAY_DAYS + 1):
            j = pos + k
            if j >= len(cal):
                break
            day = str(cal[j].date())
            for c in r["longs"] + r["shorts"]:
                need[c].add(day)
        for c in r["longs"] + r["shorts"]:
            need[c].add(str(cal[nxt].date()))
    print(f"[C3] 需检查 {len(need)} 只 · {sum(len(v) for v in need.values())} 点")
    bars, ex_aligned, span = stream_bars(need)

    # ---- V0：阶段 C 的实现，逐位复现
    v0 = apply_constraints(rows_d, bars, ex_aligned, span)
    st0 = C.stats_of([r["d"] for r in v0])
    print()
    print("=== V0 复现校验（必须逐位等于阶段 C 的 D 格）===")
    repro = {}
    for k, exp in EXPECT_V0.items():
        got = st0[k] * (100 if k in ("mean_d", "mean_spread") else 1)
        tol = REPRO_TOL[k]
        ok = abs(got - exp) <= tol if tol else got == exp
        repro[k] = {"expected": exp, "actual": round(got, 8), "tol": tol, "ok": ok}
        print(f"  {k:<12} 实测 {got:+.6f}  期望 {exp:+.6f}  {'OK' if ok else 'FAIL'}")
    if not all(v["ok"] for v in repro.values()):
        raise SystemExit("V0 未能复现阶段 C 的 D 格，拒绝继续（先查实现）")
    print("  >>> V0 逐位复现通过")

    # ---- V1 / V2
    v1 = reprice(v0, panels.ffill, cal, "V1", bars, ex_aligned, pos_of)
    v2 = reprice(v0, panels.ffill, cal, "V2", bars, ex_aligned, pos_of)
    st1 = C.stats_of([r["d_repriced"] for r in v1])
    st2 = C.stats_of([r["d_repriced"] for r in v2])

    print()
    print("=" * 78)
    print("三格统计（同一批持仓，只改收益起算点）")
    print("=" * 78)
    print(f"{'格':<24}{'n':>4}{'mean(d)':>11}{'spread':>10}{'t':>8}")
    for tag, s in (("V0  pos 收盘（前视）", st0),
                   ("V1  pos+1 收盘", st1),
                   ("V2  首个可成交日", st2)):
        print(f"{tag:<24}{s['n']:>4}{s['mean_d']*100:>+11.4f}{s['mean_spread']*100:>10.4f}"
              f"{s['t_stat']:>+8.3f}")

    # ---- 主检验
    print()
    print("=" * 78)
    print("主检验：Δ_t = spread(V1) − spread(V0)   （纯「晚一天」的代价）")
    print("=" * 78)
    common, delta = pair_diff(v0, [{**r, "d": r["d_repriced"]} for r in v1], "d")
    nw = C.newey_west(delta, NW_LAG_C3)
    verdict = judge(nw)
    print(f"  配对月份 {len(common)}  NW 有效样本 {nw['n_eff']}")
    print(f"  mean(Δ) = {nw['mean_pp']:+.4f} pp/月   NW se = {nw['se_nw_pp']:.4f}")
    print(f"  NW t    = {nw['t_nw']:+.4f}   p = {nw['p']:.4g}")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    if verdict.get("direction"):
        print(f"      · 方向：{verdict['direction']}")

    # ---- 交叉验证
    print()
    print("交叉验证（必报）：Δ'_t = spread(V2) − spread(V1)   （「顺延到可成交」的额外代价）")
    # V1 与 V2 的月份集合可能不同（V2 有超时剔除），所以不用 pair_diff 的等长假设，
    # 而是按 signal_date 取交集后直接配对。
    m1 = {r["signal_date"]: r for r in v1}
    m2 = {r["signal_date"]: r for r in v2}
    common2 = sorted(set(m1) & set(m2))
    delta2 = [2 * (m2[k]["d_repriced"] - m1[k]["d_repriced"]) for k in common2]
    nw2 = C.newey_west(delta2, NW_LAG_C3)
    print(f"  配对月份 {len(common2)}  mean(Δ') = {nw2['mean_pp']:+.4f} pp  "
          f"NW t = {nw2['t_nw']:+.4f}  p = {nw2['p']:.4g}")
    same = (nw["mean"] * nw2["mean"] > 0) if (nw["mean"] == nw["mean"] and
                                              nw2["mean"] == nw2["mean"]) else None
    if same is True:
        cross = "两段一致（同号）"
    elif same is False:
        cross = "两段异号 → 主结论降级 EVIDENCE_CONFLICT"
        verdict = {"verdict": "EVIDENCE_CONFLICT",
                   "rule_hits": [f"主 {nw['mean_pp']:+.4f} vs 交叉 {nw2['mean_pp']:+.4f} 异号"],
                   "original": verdict["verdict"]}
    else:
        cross = "无法判断"
    print(f"  >>> {cross}")

    # ---- D1 时间分解
    print()
    print("=" * 78)
    print("D1 持有期收益的时间分解（描述性，本实验的地基）")
    print("=" * 78)
    seg_rows = []
    for r in v0:
        pos = pos_of[r["signal_date"]]
        nxt = pos_of[r["month"]]
        j1 = pos + 1
        j5 = min(pos + SEG2_END, nxt)
        if j1 >= len(cal) or j1 > nxt:
            continue
        f = panels.ffill
        seg = {}
        for name, a, b in (("d1_pos_to_pos1", pos, j1),
                           ("d2_pos1_to_pos5", j1, j5),
                           ("d3_pos5_to_nxt", j5, nxt)):
            ra = f.iloc[a]
            rb = f.iloc[b]
            rl_, rs_ = [], []
            for c in r["longs_c"]:
                v = rb.get(c, float("nan")) / ra.get(c, float("nan")) - 1.0
                if v == v:
                    rl_.append(v)
            for c in r["shorts_c"]:
                v = rb.get(c, float("nan")) / ra.get(c, float("nan")) - 1.0
                if v == v:
                    rs_.append(v)
            if rl_ and rs_:
                seg[name] = {"long": statistics.mean(rl_), "short": statistics.mean(rs_),
                             "d": 0.5 * (statistics.mean(rl_) - statistics.mean(rs_))}
        if len(seg) == 3:
            seg_rows.append({"signal_date": r["signal_date"], **{
                f"{k}_{m}": round(v[m], 10) for k, v in seg.items() for m in ("long", "short", "d")}})

    if seg_rows:
        print(f"{'区间':<22}{'多头':>10}{'空头':>10}{'spread':>10}{'占全期':>9}")
        tot = sum(statistics.mean([s[f"{k}_d"] for s in seg_rows]) for k in
                  ("d1_pos_to_pos1", "d2_pos1_to_pos5", "d3_pos5_to_nxt"))
        for k, lab in (("d1_pos_to_pos1", "pos → pos+1（第1天）"),
                       ("d2_pos1_to_pos5", "pos+1 → pos+5（第2~5天）"),
                       ("d3_pos5_to_nxt", "pos+5 → nxt（剩余）")):
            lg = statistics.mean([s[f"{k}_long"] for s in seg_rows])
            sh = statistics.mean([s[f"{k}_short"] for s in seg_rows])
            dd = statistics.mean([s[f"{k}_d"] for s in seg_rows])
            # ⚠️ 份额 = 该段 d / 三段 d 之和（等价于 该段 spread / 三段 spread 之和）。
            #    第一版写成 `2*dd/tot*100`，把份额整体放大 2 倍（第1天 −8.0% 实际是 −4.0%）。
            #    "看起来合理"（三段恰好凑到 200%）反而掩盖了它错。
            share = dd / tot * 100 if tot else float("nan")
            print(f"{lab:<22}{lg*100:>+10.4f}{sh*100:>+10.4f}{2*dd*100:>+10.4f}{share:>8.1f}%")
        print(f"  （占比以三段 spread 之和为分母；三段之和 = {tot*100:+.4f}pp，"
              f"与 V0 全期 spread {st0['mean_spread']*100:+.4f}pp 的差来自复利与缺失对齐）")

    # ---- D2 相关
    corr = float("nan")
    d1_series, dl_series = [], []
    smap = {s["signal_date"]: s["d1_pos_to_pos1_d"] for s in seg_rows}
    v0map = {r["signal_date"]: r["d"] for r in v0}
    v1map = {r["signal_date"]: r["d_repriced"] for r in v1}
    for k in sorted(set(smap) & set(v0map) & set(v1map)):
        d1_series.append(smap[k])
        dl_series.append(2 * (v1map[k] - v0map[k]))
    if len(d1_series) > 10 and statistics.stdev(d1_series) > 0 and statistics.stdev(dl_series) > 0:
        ma, mb = statistics.mean(d1_series), statistics.mean(dl_series)
        cov = sum((a - ma) * (b - mb) for a, b in zip(d1_series, dl_series)) / len(d1_series)
        corr = cov / (statistics.stdev(d1_series) * statistics.stdev(dl_series))
    print()
    print(f"D2 第1天 spread 与 Δ 的相关：r = {corr:+.4f}（n={len(d1_series)} 月）")
    print("    （Δ 若完全由第 1 天驱动，r 应接近 −1：第 1 天 spread 越大，放弃它损失越多）")

    # ---- D3 顺延分布
    hist: dict[int, int] = defaultdict(int)
    timeout_total = 0
    for r in v2:
        for k, v in r["delay_hist"].items():
            hist[int(k)] += v
        timeout_total += r["n_timeout"]
    total_checked = sum(hist.values()) + timeout_total
    print()
    print("D3 顺延天数分布（V2）：")
    for k in sorted(hist):
        print(f"    顺延 {k} 天: {hist[k]:>7} 只次（{hist[k]/max(total_checked,1)*100:5.2f}%）")
    print(f"    超时（>{MAX_DELAY_DAYS} 日）: {timeout_total:>7} 只次（{timeout_total/max(total_checked,1)*100:5.2f}%）")

    # ---- D4 腿规模
    nl_v0 = statistics.median(len(r["longs_c"]) for r in v0)
    nl_v2 = statistics.median(r["n_kept_long"] for r in v2)
    ns_v0 = statistics.median(len(r["shorts_c"]) for r in v0)
    ns_v2 = statistics.median(r["n_kept_short"] for r in v2)
    print(f"D4 腿规模中位：多头 {nl_v0:.0f} → {nl_v2:.0f}，空头 {ns_v0:.0f} → {ns_v2:.0f}")

    # ---- D5 分年度 Δ
    by_year: dict[str, list[float]] = defaultdict(list)
    for k in common:
        by_year[k[:4]].append(2 * (v1map[k] - v0map[k]))
    print()
    print("D5 分年度 Δ（pp/月）：")
    line = "  ".join(f"{y}:{statistics.mean(v)*100:+.3f}(n={len(v)})"
                     for y, v in sorted(by_year.items()))
    print("    " + line)

    # ---- 成本
    print()
    print("=" * 78)
    print("成本（月度成本 = 2 × 单边换手 × 费率；×12 是年化，不入月度扣减）")
    print("=" * 78)
    cost = {}
    for tag, s, rows, lk, sk in (("V0", st0, v0, "longs_c", "shorts_c"),
                                 ("V1", st1, v1, "longs_c", "shorts_c"),
                                 ("V2", st2, v2, "longs_c", "shorts_c")):
        to = turnover_of(rows, lk, sk)
        tiers = {}
        for c in (0.001, 0.003):
            r_ = cost_of(to, s["mean_d"], s["sd_d"], s["n"], c)
            tiers[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r_.items()}
        cost[tag] = {"one_side_per_month": round(to, 6), "cost": tiers}
        print(f"  {tag} 单边 {to*100:5.1f}%/月  净@0.1% {tiers['0.1pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.1pct']['t_net']:+.2f})  净@0.3% {tiers['0.3pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.3pct']['t_net']:+.2f})")

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    m1i = {r["signal_date"]: r for r in v1}
    m2i = {r["signal_date"]: r for r in v2}
    with open(os.path.join(OUT_DIR, "cells.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,k_used,n_long,n_short,"
                "d_v0,d_v1,d_v2,spread_v0,spread_v1,spread_v2,n_timeout_v2\n")
        for r in v0:
            k = r["signal_date"]
            a = m1i.get(k)
            b = m2i.get(k)
            f.write(f"{k},{r['month']},{r['n_pool']},{r['k_used']},"
                    f"{len(r['longs_c'])},{len(r['shorts_c'])},"
                    f"{r['d']:.10f},{a['d_repriced'] if a else float('nan'):.10f},"
                    f"{b['d_repriced'] if b else float('nan'):.10f},"
                    f"{2*r['d']:.10f},{2*a['d_repriced'] if a else float('nan'):.10f},"
                    f"{2*b['d_repriced'] if b else float('nan'):.10f},"
                    f"{b['n_timeout'] if b else ''}\n")

    with open(os.path.join(OUT_DIR, "decomposed.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date," + ",".join(
            f"{k}_{m}" for k in ("d1_pos_to_pos1", "d2_pos1_to_pos5", "d3_pos5_to_nxt")
            for m in ("long", "short", "d")) + "\n")
        for s in seg_rows:
            f.write(s["signal_date"] + "," + ",".join(
                f"{s[f'{k}_{m}']:.10f}" for k in ("d1_pos_to_pos1", "d2_pos1_to_pos5", "d3_pos5_to_nxt")
                for m in ("long", "short", "d")) + "\n")

    summary = {
        "experiment": "phasec3-execution-lag-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "phase_c_prereg_sha256": PHASE_C_PREREG_SHA256,
        "question": "把成交从信号日收盘推迟到真正可执行的那天，效应还剩多少？",
        "prereg_inconsistency": (
            "预登记 §1 写'三格共用同一批持仓（含 §5 约束）'，§3.2 却写'V1 可能买到当天涨停的票、"
            "V2 顺延后避开涨停'。两者不能同时成立——涨停票已被 §5 剔出腿。"
            "本脚本按 §1 执行（更基础，且是'Δ 不含选股差异'成立的前提）。"
            "后果：V2 的顺延实际只处理停牌，不处理涨停。"),
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "params": {"k_frac": K_FRAC, "min_listed_days": MIN_LISTED_DAYS,
                   "max_delay_days": MAX_DELAY_DAYS, "nw_lag": NW_LAG_C3,
                   "min_n": MIN_N_MAIN, "t_crit": T_CRIT, "econ_min_pp": ECON_MIN_PP},
        "verify": vi,
        "v0_reproduction": repro,
        "cells": {"V0": clean(st0), "V1": clean(st1), "V2": clean(st2)},
        "turnover_cost": cost,
        "main_test": {"comparison": "spread(V1) − spread(V0)", "n_pairs": len(common),
                      **clean(nw), "judgement": verdict},
        "cross_validation": {"comparison": "spread(V2) − spread(V1)", "n_pairs": len(common2),
                             **clean(nw2), "reading": cross},
        "diagnostics": {
            "D1_decomposition_monthly": seg_rows,
            "D2_corr_day1_vs_delta": round(corr, 6) if corr == corr else None,
            "D2_n_months": len(d1_series),
            "D3_delay_hist": {str(k): v for k, v in sorted(hist.items())},
            "D3_timeout": timeout_total,
            "D3_total_checked": total_checked,
            "D4_leg_median": {"long": [nl_v0, nl_v2], "short": [ns_v0, ns_v2]},
            "D5_delta_by_year": {y: {"n": len(v), "mean_pp": round(statistics.mean(v) * 100, 6)}
                                 for y, v in sorted(by_year.items())},
        },
        "limitations": [
            "V2 只处理停牌顺延（涨停已在 §5 被剔），预登记 §3.2 与 §1 不一致，已登记",
            "平仓日不顺延（买入是唯一延迟来源；且做空腿不可得，测平仓延迟无现实意义）",
            "开盘价未使用——本实验只改'哪一天的收盘价'，不模拟日内成交价",
            "做空腿不可得（M4 结构阻塞），d 不是可实现组合收益",
            "ST 无历史标记；after_last_data 中退市与抓取截断不可区分",
        ],
        "note": "本实验不改变 Gate 6 / 阶段 C 的既有数值，只量化'pos 收盘成交'这一前视口径的代价。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["cells.csv", "decomposed.csv", "summary.json"]
    manifest = {
        "experiment": "phasec3-execution-lag-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "script_sha256": _sha(os.path.abspath(__file__)),
        "files": {n: {"sha256": _sha(os.path.join(OUT_DIR, n)),
                      "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print()
    print("=" * 78)
    print(f"产物 -> {OUT_DIR}/")
    print(f"主判定：{verdict['verdict']}   交叉：{cross}")
    print(f"总耗时 {time.time()-t0:.0f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
