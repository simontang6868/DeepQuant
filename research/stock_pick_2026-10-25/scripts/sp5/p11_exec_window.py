# -*- coding: utf-8 -*-
"""SP-5 / P1-1：可执行区间审计（主口径 2023-01 起）

窗口口径
--------
按预登记 v1.2（§11），主口径窗口为 **2023-01 ~ 2026-08（44 月）**，
从 2023-01 月末**新开仓**，不继承 2023 年以前的持仓。
261 月全样本结果保留作附录（由 p04_position_dd.py 产出），不与本窗口比较 t 值。

三口径（预登记 §3 E1–E4）
------------------------
* 口径 A 全样本研究：44 月全部计入，逐月标注可执行性；
* 口径 B 可执行月份：只保留 E1∧E2∧E3 满足的月份（**时间轴不连续**，已标注）；
* 口径 C 连续可执行区间：E1∧E2 连续满足的最长月份段，长度 ≥36 才成立（E4）。

E1 = 决策日 pool_n ≥ 5；E2 = 次一交易日可买 ≥ 5 只；E3 = 换出标的次一交易日可卖。

为什么必须拆口径
----------------
早期"没股可买"造成的空仓会被当成"策略回撤"，把不可执行期的损失算进策略绩效。
但**拆口径也是危险动作**：删月份可以提高收益。因此规则冻结在预登记里，
且本脚本**同时报三个口径**，禁止只挑最好的那个引用。

统计口径
--------
`t` 同时报 iid（stdev/√n）与 NW(3)；`MDE = 2.8016×se`；
事后功效 `Φ(|t|−1.96)+Φ(−|t|−1.96)`；日频 MDD 用真实组合账本净值。
⚠️ 44 月窗口的 MDE 约 3.4pp/月，**统计上无法证实策略有效**（见预登记 §11）。

输出：sp5_out/sp5_exec_window.json、sp5_out/sp5_exec_monthly.csv
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402
import p03_tradability as p03  # noqa: E402
import p04_position_dd as p04  # noqa: E402

RD, OUT = pathlib.Path(p03.RD), pathlib.Path(p03.OUT)
K = 5
START = "20230131"
MIN_POOL = 5          # E1/E2 阈值
E4_MIN_LEN = 36       # 连续可执行区间最短长度
NW_LAG = 3
N_PATHS = int(os.environ.get("SP5_PATHS", "200"))
W_LIST = (0.25, 0.20)
# 窗口内压力段：44 月里并非没有急跌，2024-01 微盘股就是一次真实的流动性冲击。
# 如果 −8.6% 的 MDD 已经包含了这一段，说明它不是"窗口太幸运"；如果没包含，结论要打折。
SEGS = [("20240101", "20240208", "2024-01 微盘急跌"),
        ("20240601", "20240930", "2024 三季度阴跌"),
        ("20250101", "20251231", "2025 全年")]


# ------------------------------------------------------------------ 统计工具
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def nw_se(x: np.ndarray, lag: int = NW_LAG) -> float:
    """Newey-West 标准误（Bartlett 核）。入参必须是小数收益，传 pp 会放大 100 倍。"""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for l in range(1, min(lag, n - 1) + 1):
        g = float(e[l:] @ e[:-l]) / n
        s += 2.0 * (1.0 - l / (lag + 1.0)) * g
    return math.sqrt(max(s, 0.0) / n)


def stats(x: np.ndarray) -> dict:
    x = np.asarray([v for v in np.asarray(x, float) if np.isfinite(v)], float)
    n = len(x)
    if n < 3:
        return {"n": n}
    mu, sd = float(x.mean()), float(x.std(ddof=1))
    se_i = sd / math.sqrt(n)
    se_nw = nw_se(x)
    t_i = mu / se_i if se_i > 0 else float("nan")
    t_nw = mu / se_nw if se_nw and se_nw > 0 else float("nan")
    mde = 2.8016 * se_i
    power = norm_cdf(abs(t_i) - 1.96) + norm_cdf(-abs(t_i) - 1.96)
    return {
        "n": n, "mean": mu, "sd": sd, "se_iid": se_i, "se_nw3": se_nw,
        "t_iid": t_i, "t_nw3": t_nw, "mde": mde, "power": power,
        "pos_share": float((x > 0).mean()),
    }


def mdd(nav: np.ndarray) -> float:
    nav = np.asarray(nav, float)
    if len(nav) == 0:
        return float("nan")
    return float((nav / np.maximum.accumulate(nav) - 1.0).min())


def month_end_nav(df: pd.DataFrame, months: list[str], cal: list[str]) -> np.ndarray:
    """从日频账本抽每月最后一个交易日的净值。

    ⚠️ 必须**按同月匹配**（`date[:6] == month[:6]`），不能用 `date <= month`：
    后者在该月无记录时会返回上月的净值，等于前向填充造假——会把"整月缺数据"
    伪装成"当月零波动"，正是这类审计最该防的错误。
    """
    if len(df) == 0:
        return np.full(len(months), np.nan)
    keys = np.asarray([x[:6] for x in df["date"].to_numpy()])
    nav = df["nav"].to_numpy(float)
    out = []
    for m in months:
        sel = keys == m[:6]
        out.append(float(nav[sel][-1]) if sel.any() else np.nan)
    return np.asarray(out, float)


def month_series(df: pd.DataFrame, months: list[str], cal: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """月末净值 → (有记录掩码, 月收益)。rets[i] = months[i-1] → months[i]，无记录为 NaN。

    ⚠️ 不能用 `nv/nv[0]` 后直接切片：建仓月（months[0]）账本里没有月末记录，
    nv[0] 是 NaN，会把整条序列污染成 NaN（曾导致 B 口径全空、被静默当成"无样本"）。
    """
    nv = month_end_nav(df, months, cal)
    ok = np.isfinite(nv) & (nv > 0)
    rets = np.full(len(months), np.nan)
    for i in range(1, len(months)):
        if ok[i] and ok[i - 1]:
            rets[i] = nv[i] / nv[i - 1] - 1.0
    return ok, rets


def accumulate(rets: np.ndarray) -> np.ndarray:
    r = rets[np.isfinite(rets)]
    return np.concatenate([[1.0], np.cumprod(1.0 + r)])


MIN_WINDOW_FOR_VERDICT = 60   # §5.4 门槛（预登记 v1.2 §11：44 月窗口下不可达）


def decide(n_exe: int, n_months: int, run_len: int, a20: dict | None) -> str:
    """可执行区间判定。纯函数，便于测试每个分支。

    ⚠️ 最后一条 `EXPLORATORY_ONLY_WINDOW_LT_60M` 是**无条件附加**的：
    n < 60 时无论收益/回撤多好，都不能判 `EXECUTABLE_WINDOW_FOUND`（§5.4）。
    这是窗口选择的代价，不是可以"结果好就豁免"的条款。
    """
    v = []
    v.append("ALL_MONTHS_EXECUTABLE" if n_exe == n_months else "SOME_MONTHS_NOT_EXECUTABLE")
    if run_len < E4_MIN_LEN:
        v.append("NO_CONTIGUOUS_36M_WINDOW")
    if a20:
        if a20.get("mdd_med") is not None and a20["mdd_med"] < -0.20:
            v.append("DD_BREACH_20PCT")
        if a20.get("ann_med") is not None and a20["ann_med"] < 0.08:
            v.append("RETURN_BELOW_8PCT")
    if n_months < MIN_WINDOW_FOR_VERDICT:
        v.append("EXPLORATORY_ONLY_WINDOW_LT_60M")
    elif n_exe == n_months and run_len >= MIN_WINDOW_FOR_VERDICT:
        v.append("EXECUTABLE_WINDOW_FOUND")
    return "|".join(v)


def longest_run(flags: list[bool]) -> tuple[int, int]:
    """返回 (最长连续 True 的长度, 起始下标)。"""
    best, bi, cur, ci = 0, -1, 0, 0
    for i, f in enumerate(flags):
        if f:
            if cur == 0:
                ci = i
            cur += 1
            if cur > best:
                best, bi = cur, ci
        else:
            cur = 0
    return best, bi


def main() -> int:
    panel = pd.read_parquet(p03.PANEL)
    panel = panel[panel["date"] >= START].copy()
    print(f"[window] {panel['date'].min()} ~ {panel['date'].max()}"
          f"｜{panel['date'].nunique()} 月｜{len(panel):,} 行")

    by, months, paths, ns_list, _ = p03.build_decisions(panel, N_PATHS)
    print(f"[paths] {len(paths)} 条｜{len(months)} 月｜{months[0]} ~ {months[-1]}")

    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    carr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]

    # ---------------- 股票价格与成交位图 ----------------
    codes = sorted({c for h in paths for hs in h for c in hs})
    print(f"[need] {len(codes)} 只")
    px, tabs, miss = {}, {}, 0
    for i, c in enumerate(codes):
        b = fe.load_bars(c)
        if b is None:
            miss += 1
            continue
        a = np.full(len(cal), np.nan, dtype=np.float32)
        pos = np.searchsorted(carr, b.d)
        ok = (pos < len(cal)) & (carr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        a[pos[ok]] = b.qc[ok].astype(np.float32)
        px[c] = pd.Series(a).ffill().to_numpy(dtype=np.float32)
        tabs[c] = fe.status_tables(b)
    print(f"[bars] {len(px)} 只，缺 {miss} 只")

    # ---------------- 逐月可执行性（E1 / E2） ----------------
    rows = []
    for t, m in enumerate(months):
        b = by[m]
        pool = b["pool"]
        n1 = int(len(pool))
        d_buy = buy[t]
        if n1:
            ok_buy = 0
            for i in pool:
                c = b["codes"][i]
                tb = tabs.get(c)
                if tb is None:
                    continue
                dd, bo, _ = tb
                j = np.searchsorted(dd, d_buy)
                if j < len(dd) and dd[j] == d_buy and bo[j]:
                    ok_buy += 1
        else:
            ok_buy = 0
        rows.append({
            "month": m, "buy_date": d_buy, "pool_n": n1, "pool_buyable": ok_buy,
            "E1": n1 >= MIN_POOL, "E2": ok_buy >= MIN_POOL,
            "executable": (n1 >= MIN_POOL) and (ok_buy >= MIN_POOL),
        })
    mm = pd.DataFrame(rows)
    mm.to_csv(OUT / "sp5_exec_monthly.csv", index=False, encoding="utf-8")

    e1_only = int(mm["E1"].sum())
    exe = mm["executable"].to_numpy()
    n_exe = int(exe.sum())
    run_len, run_at = longest_run(list(exe))
    print(f"[E1] 满足 {e1_only}/{len(mm)} 月｜[E1∧E2] 可执行 {n_exe}/{len(mm)} 月"
          f"｜最长连续 {run_len} 月（{months[run_at] if run_at >= 0 else '—'} 起）")

    # ---------------- 真实账本：口径 A ----------------
    def run_ledger(path_idx: int, t0: int, w: float, span: list[int] | None = None):
        """span=[i0,i1] 时只在该下标段内记账（口径 C 用）。"""
        df = p04.simulate(paths[path_idx], t0, w, px, tabs, months, buy, nxt, cal_pos, cal, "rebal")
        if span is not None:
            d0, d1 = cal[cal_pos[buy[span[0]]]], cal[cal_pos[nxt[span[1]]]]
            df = df[(df["date"] >= d0) & (df["date"] <= d1)].reset_index(drop=True)
        return df

    res: dict = {"window": {"start": months[0], "end": months[-1], "n_months": len(months)},
                 "n_paths": len(paths), "missing_bars": miss}

    # 月收益（各路径）
    mon_ret: dict[str, np.ndarray] = {}
    daily_nav: dict[str, list[np.ndarray]] = {}
    for w in W_LIST:
        key = f"A_w{int(w*100)}"
        mrs, navs_d = [], []
        for pi in range(len(paths)):
            df = run_ledger(pi, 0, w)
            _, r = month_series(df, months, cal)
            if np.isfinite(r).sum() < 3:
                continue
            mrs.append(r[np.isfinite(r)])
            navs_d.append(df["nav"].to_numpy(float) / df["nav"].to_numpy(float)[0])
        # 逐路径统计 → 取中位，另报"合并所有路径月度观测"的统计
        per_path = [q for q in (stats(r) for r in mrs) if "mean" in q]
        pool_all = np.concatenate(mrs) if mrs else np.array([])
        mon_ret[key] = mrs[0] if mrs else np.array([])
        daily_nav[key] = navs_d
        res[key] = {
            "scope": "全样本（44 月，含不可执行月）",
            "months": int(np.median([p["n"] for p in per_path])),
            "ann_med": float(np.median([(1 + r).prod() ** (12 / len(r)) - 1 for r in mrs])),
            "mean_med": float(np.median([p["mean"] for p in per_path])),
            "t_iid_med": float(np.median([p["t_iid"] for p in per_path])),
            "t_nw3_med": float(np.median([p["t_nw3"] for p in per_path])),
            "mde_med": float(np.median([p["mde"] for p in per_path])),
            "power_med": float(np.median([p["power"] for p in per_path])),
            "mdd_med": float(np.median([mdd(v) for v in navs_d])),
            "mdd_worst": float(np.min([mdd(v) for v in navs_d])),
            "breach20_share": float(np.mean([mdd(v) < -0.20 for v in navs_d])),
            "pooled": stats(pool_all),
        }
        print(f"[{key}] ann {res[key]['ann_med']*100:+.2f}%  "
              f"t_iid {res[key]['t_iid_med']:+.2f}  t_nw3 {res[key]['t_nw3_med']:+.2f}  "
              f"MDE {res[key]['mde_med']*100:.2f}pp/月  MDD {res[key]['mdd_med']*100:+.2f}%")

    # ---------------- 口径 B：只算可执行月 ----------------
    idx_exe = [i for i, f in enumerate(exe) if f]
    for w in W_LIST:
        key = f"B_w{int(w*100)}"
        mrs = []
        for pi in range(len(paths)):
            df = run_ledger(pi, 0, w)
            _, r = month_series(df, months, cal)
            # 区间两端（决策月末 i−1 与持有月末 i）都必须可执行，取保守判据
            keep = [i for i in range(1, len(months))
                    if exe[i] and exe[i - 1] and np.isfinite(r[i])]
            if keep:
                mrs.append(r[keep])
        per_path = [q for q in (stats(r) for r in mrs if len(r) >= 3) if "mean" in q]
        pool_all = np.concatenate([r for r in mrs if len(r)]) if mrs else np.array([])
        res[key] = {
            "scope": f"可执行月份（{n_exe}/{len(months)} 月，时间轴不连续）",
            "months": int(np.median([p["n"] for p in per_path])) if per_path else 0,
            "ann_med": float(np.median([(1 + r).prod() ** (12 / len(r)) - 1
                                        for r in mrs if len(r)])) if mrs else None,
            "mean_med": float(np.median([p["mean"] for p in per_path])) if per_path else None,
            "t_iid_med": float(np.median([p["t_iid"] for p in per_path])) if per_path else None,
            "t_nw3_med": float(np.median([p["t_nw3"] for p in per_path])) if per_path else None,
            "mde_med": float(np.median([p["mde"] for p in per_path])) if per_path else None,
            "power_med": float(np.median([p["power"] for p in per_path])) if per_path else None,
            "pooled": stats(pool_all),
            "note": "⚠️ 年化基于不连续月序列复利，仅用于对比，不是可实现净值",
        }
        if res[key]["ann_med"] is not None:
            print(f"[{key}] ann {res[key]['ann_med']*100:+.2f}%  "
                  f"mean {res[key]['mean_med']*100:+.3f}pp  t_iid {res[key]['t_iid_med']:+.2f}  "
                  f"MDE {res[key]['mde_med']*100:.2f}pp")

    # ---------------- 口径 C：最长连续可执行区间 ----------------
    res["C"] = {"run_len": run_len, "start": months[run_at] if run_at >= 0 else None,
                "E4_pass": run_len >= E4_MIN_LEN}
    if run_len >= 5 and run_at >= 0:
        i1 = run_at + run_len - 1
        for w in W_LIST:
            key = f"C_w{int(w*100)}"
            anns, mdd_d, mrs = [], [], []
            for pi in range(len(paths)):
                df = run_ledger(pi, run_at, w, span=[run_at, min(i1, len(months) - 1)])
                _, rr = month_series(df, months[run_at:i1 + 1], cal)
                rr = rr[np.isfinite(rr)]
                if len(rr) < 3:
                    continue
                mrs.append(rr)
                acc = np.concatenate([[1.0], np.cumprod(1.0 + rr)])
                anns.append(acc[-1] ** (12 / len(rr)) - 1)
                mdd_d.append(mdd(df["nav"].to_numpy(float) / df["nav"].to_numpy(float)[0]))
            per_path = [stats(r) for r in mrs if len(r) >= 3]
            if not per_path:
                continue
            res[key] = {
                "scope": f"连续可执行区间 {months[run_at]} ~ {months[min(i1, len(months)-1)]}"
                         f"（{run_len} 月，E4 {'满足' if run_len >= E4_MIN_LEN else '不满足'}）",
                "months": run_len,
                "ann_med": float(np.median(anns)),
                "mean_med": float(np.median([p["mean"] for p in per_path])),
                "t_iid_med": float(np.median([p["t_iid"] for p in per_path])),
                "t_nw3_med": float(np.median([p["t_nw3"] for p in per_path])),
                "mde_med": float(np.median([p["mde"] for p in per_path])),
                "power_med": float(np.median([p["power"] for p in per_path])),
                "mdd_med": float(np.median(mdd_d)),
                "mdd_worst": float(np.min(mdd_d)),
            }
            print(f"[{key}] {run_len} 月  ann {res[key]['ann_med']*100:+.2f}%  "
                  f"t_iid {res[key]['t_iid_med']:+.2f}  MDE {res[key]['mde_med']*100:.2f}pp  "
                  f"MDD {res[key]['mdd_med']*100:+.2f}%")

    # ---------------- 窗口内压力段（检验"回撤小是否只是运气"） ----------------
    seg_res = {}
    for a, b, name in SEGS:
        anns, mdds, rets = [], [], []
        for pi in range(min(len(paths), 60)):
            df = run_ledger(pi, 0, 0.25)
            sub = df[(df["date"] >= a) & (df["date"] <= b)]
            if len(sub) < 10:
                continue
            nv = sub["nav"].to_numpy(float)
            nv = nv / nv[0]
            mdds.append(mdd(nv))
            rets.append(nv[-1] - 1.0)
        if mdds:
            seg_res[name] = {
                "range": [a, b],
                "ret_med": float(np.median(rets)),
                "mdd_med": float(np.median(mdds)),
                "mdd_worst": float(np.min(mdds)),
            }
            print(f"[seg] {name}  区间收益 {np.median(rets)*100:+.2f}%  "
                  f"段内MDD {np.median(mdds)*100:+.2f}%（最差 {np.min(mdds)*100:+.2f}%）")
    res["segments_w25"] = seg_res

    # ---------------- 实际股票权重（专家 §二.3「有效持仓数」询问项） ----------------
    # ⚠️ 曾用 `df[stock_value>0].groupby(月).size()` 当"持仓只数"——那数的是**账本行数**
    # （每月约 21.5 个交易日），不是股票只数。真实持仓数无法从日频账本直接读出，
    # 能可靠读出的是**股票市值占净值比例**，它同时反映"买不进"与"漂移"。
    sw = {}
    for w in W_LIST:
        vals = []
        for pi in range(min(len(paths), 60)):
            df = run_ledger(pi, 0, w)
            ratio = (df["stock_value"] / df["nav"]).to_numpy(float)
            vals.append(float(np.median(ratio)))
        sw[f"w{int(w*100)}"] = {
            "target": w,
            "actual_median": float(np.median(vals)) if vals else None,
            "gap_pp": float(w - np.median(vals)) * 100 if vals else None,
        }
    res["stock_weight"] = sw
    for k, v in sw.items():
        print(f"[weight] {k} 目标 {v['target']*100:.0f}% → 实测中位 "
              f"{v['actual_median']*100:.2f}%（缺口 {v['gap_pp']:+.2f}pp）")

    # ---------------- 基准分解：这 7% 是不是小盘 beta ----------------
    # 用 panel 的 fwd20（与 p03 决策口径一致），毛收益、不加成本，只做归因。
    dec = by
    bm = {"BM1_market": [], "BM2_lowliq": [], "BM3_pool": []}
    for m in months:
        b = dec[m]
        n = len(b["fwd"])
        if n < 100:
            continue
        lq = np.asarray([0.0])  # 占位，实际用 mask
    # 重新按 panel 直接算（by 里没有保留 liqpct，改用 pool 索引反推不可靠）
    pp = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    pp["f3pct"] = pp.groupby("date")["f3"].rank(pct=True, method="average")
    pp["liqpct"] = pp.groupby("date")["amt20"].rank(pct=True, method="average")
    for m in months:
        g = pp[pp["date"] == m]
        if len(g) < 100:
            continue
        f = g["fwd20"].to_numpy(float)
        bm["BM1_market"].append(float(np.mean(f)))
        lo = g["liqpct"].to_numpy() < p03.LIQ_MAX
        bm["BM2_lowliq"].append(float(np.mean(f[lo])))
        pool_mask = lo & (g["f3pct"].to_numpy() <= p03.THRESH)
        bm["BM3_pool"].append(float(np.mean(f[pool_mask])) if pool_mask.any() else np.nan)

    res["baseline"] = {}
    for k, v in bm.items():
        st = stats(np.asarray(v, float))
        res["baseline"][k] = st
        if "mean" in st:
            print(f"[{k}] mean {st['mean']*100:+.3f}pp/月  t_iid {st['t_iid']:+.2f}  "
                  f"ann {(1+st['mean'])**12-1:+.2%}")

    # 归因：BM1 → BM2 = 流动性；BM2 → BM3 = F3；BM3 → 策略毛
    if "mean" in res["baseline"]["BM1_market"]:
        m1 = res["baseline"]["BM1_market"]["mean"]
        m2 = res["baseline"]["BM2_lowliq"]["mean"]
        m3 = res["baseline"]["BM3_pool"]["mean"]
        res["attribution"] = {
            "liquidity_pp": (m2 - m1) * 100,
            "f3_pp": (m3 - m2) * 100,
            "note": "毛口径、未扣成本；pp/月",
        }
        print(f"[attr] 流动性 {(m2-m1)*100:+.3f}pp   F3 {(m3-m2)*100:+.3f}pp")

    # ---------------- 判定 ----------------
    res["verdict"] = decide(n_exe, len(months), run_len, res.get("A_w20"))
    res["prereg_note"] = ("预登记 v1.2：44 月 < 60 月门槛，EXECUTABLE_WINDOW_FOUND 不可达；"
                          "结论上限 EXPLORATORY_ONLY")

    # 逐路径明细（对账用）
    res["paths_monthly_returns_w20"] = [
        [float(v) for v in r] for r in
        [month_end_nav(run_ledger(pi, 0, 0.20), months, cal) for pi in range(min(20, len(paths)))]
    ]

    (OUT / "sp5_exec_window.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[verdict] {res['verdict']}")
    print(f"[save] {OUT / 'sp5_exec_window.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
