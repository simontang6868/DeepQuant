"""股债利差研究：独立审计复算脚本。

目的：**不复用** research 模块的指标/信号函数，从清洗后的原始 CSV 重新实现
一遍关键中间量，再与研究模块的输出逐项比对，用于发现实现错误与口径误解。

运行：
    PYTHONPATH=backend/src python scripts/audit_equity_bond_spread.py

输出：
    data/equity_bond_spread/audit/audit.json        结构化复算结果
    data/equity_bond_spread/audit/event_audit.csv    逐事件明细
    stdout                                         人读的核对清单
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "equity_bond_spread"
EQ = DATA / "csi300_monthly.csv"
BOND = DATA / "cn10y.csv"
OUT = DATA / "audit"

OBS_PER_YEAR = 12
WINDOWS_TD = (20, 60, 120, 250)
COST = 0.0015
THRESHOLDS = (80.0, 85.0, 90.0, 95.0)
MIN_HIST = 36
MIN_GAP = 4
IS_END = pd.Timestamp("2018-12-31")


# --------------------------------------------------------------------------
# 独立实现（刻意不复用 research 模块）
# --------------------------------------------------------------------------
def load() -> pd.DataFrame:
    eq = pd.read_csv(EQ, parse_dates=["date"]).set_index("date").sort_index()
    bd = pd.read_csv(BOND, parse_dates=["date"]).set_index("date").sort_index()["yield_10y"]
    bd = bd.dropna().sort_index()
    # 向前取最近可得值，间隔 > 10 自然日视为缺失（与研究模块规则一致）
    aligned = bd.reindex(bd.index.union(eq.index)).sort_index().ffill()
    src = pd.Series(aligned.index, index=aligned.index)
    idx = eq.index
    src_for_obs = src.reindex(src.index.union(idx)).sort_index().ffill().reindex(idx)
    gap = pd.Series([(d - s).days if pd.notna(s) else np.nan
                     for d, s in zip(idx, src_for_obs)], index=idx)
    bond = aligned.reindex(idx)
    bond = bond.where(gap <= 10)
    eq["bond_10y"] = bond
    return eq


def ann_return(price: pd.Series, years: int, obs_per_year: int = OBS_PER_YEAR) -> pd.Series:
    """(P_t / P_{t-H}) ** (1/H) - 1，以百分数返回。H 用观测数折算的年数。"""
    n = int(round(years * obs_per_year))
    span_years = n / obs_per_year
    p = price.astype("float64")
    prev = p.shift(n)
    ok = p.notna() & prev.notna() & (p > 0) & (prev > 0)
    out = pd.Series(np.nan, index=p.index, dtype="float64")
    out[ok] = ((p[ok] / prev[ok]) ** (1.0 / span_years) - 1.0) * 100.0
    return out


def pct_rank(v: pd.Series, min_obs: int, window_obs: int | None = None,
             include_current: bool = True) -> pd.Series:
    """历史百分位。include_current=True 复现研究模块口径（含当前值）。"""
    hist: list[float] = []
    out: list[float] = []
    for x in v.to_numpy(dtype="float64"):
        if np.isnan(x):
            out.append(np.nan)
            continue
        if include_current:
            hist.append(float(x))
            h = hist[-window_obs:] if window_obs else hist
            arr = np.asarray(h, dtype="float64")
            out.append(np.nan if arr.size < min_obs else float((arr <= x).mean() * 100.0))
        else:
            arr = np.asarray(hist[-window_obs:] if window_obs else hist, dtype="float64")
            out.append(np.nan if arr.size < min_obs else float((arr <= x).mean() * 100.0))
            hist.append(float(x))
    return pd.Series(out, index=v.index)


def td_to_obs(td: int) -> int:
    return max(1, int(round(td / 252.0 * OBS_PER_YEAR)))


def merge_runs(flag: pd.Series, min_gap: int = MIN_GAP) -> list[tuple[int, int]]:
    arr = flag.fillna(0).astype(int).to_numpy()
    pos = np.flatnonzero(arr == 1)
    if pos.size == 0:
        return []
    runs, start, prev = [], int(pos[0]), int(pos[0])
    for p in pos[1:]:
        p = int(p)
        if p - prev <= min_gap:
            prev = p
        else:
            runs.append((start, prev))
            start = prev = p
    runs.append((start, prev))
    return runs


def events_from(flag: pd.Series) -> list[dict]:
    idx = flag.index
    evs = []
    for i, (s, e) in enumerate(merge_runs(flag), start=1):
        evs.append({
            "event_id": i,
            "signal_pos": s,
            "signal_date": idx[s],
            "last_signal_date": idx[e],
            "n_signal_obs": e - s + 1,
            "exec_pos": s + 1 if s + 1 < len(idx) else None,
            "exec_date": idx[s + 1] if s + 1 < len(idx) else None,
        })
    return evs


def fwd(price: pd.Series, pos: int, w: int) -> dict:
    p0 = price.iloc[pos]
    end = pos + w
    if end >= len(price) or pd.isna(p0) or p0 <= 0:
        return {"ret": np.nan, "dd": np.nan, "mfe": np.nan, "mae": np.nan,
                "end_date": None, "immature": True}
    seg = price.iloc[pos:end + 1].to_numpy(dtype="float64")
    p1 = float(price.iloc[end])
    gross = p1 / float(p0) - 1.0
    run_max = np.maximum.accumulate(seg)
    return {
        "ret": (1.0 + gross) * (1.0 - COST) - 1.0,
        "dd": float(np.min(seg / run_max - 1.0)),
        "mfe": float(seg.max() / float(p0) - 1.0),
        "mae": float(seg.min() / float(p0) - 1.0),
        "end_date": price.index[end],
        "immature": False,
    }


def describe(series: pd.Series) -> dict:
    v = series.dropna().astype("float64")
    return {
        "n": int(v.size),
        "mean": float(v.mean()) if v.size else None,
        "median": float(v.median()) if v.size else None,
        "worst": float(v.min()) if v.size else None,
        "pct_positive": float((v > 0).mean()) if v.size else None,
    }


# --------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    df = load()
    price = df["price"]
    bond = df["bond_10y"]
    out: dict = {"meta": {
        "panel_obs": int(len(df)),
        "panel_start": str(df.index[0].date()),
        "panel_end": str(df.index[-1].date()),
        "bond_missing": int(bond.isna().sum()),
        "pe_missing": int(df["pe"].isna().sum()),
        "obs_per_year": OBS_PER_YEAR,
        "cost_rate": COST,
    }}

    # ---------- 假设 A ----------
    a = {}
    for years in (5, 10):
        r = ann_return(price, years)
        spread = r - bond
        a[f"A_{years}y"] = {
            "first_valid_return": str(r.first_valid_index().date()) if r.first_valid_index() is not None else None,
            "first_valid_spread": str(spread.first_valid_index().date()) if spread.first_valid_index() is not None else None,
            "n_valid_spread": int(spread.notna().sum()),
            "lookback_obs": int(round(years * OBS_PER_YEAR)),
            "percentile": {},
        }
        # 5 年滚动收益的历史最高点（用于解释"为何 90% 无信号"）
        if r.notna().any():
            a[f"A_{years}y"]["return_max"] = float(r.max())
            a[f"A_{years}y"]["return_max_date"] = str(r.idxmax().date())
            a[f"A_{years}y"]["return_max_after_pct_available"] = None  # 下面填

        for label, kw in (("expand_36", dict(min_obs=MIN_HIST, window_obs=None)),
                          ("expand_60", dict(min_obs=60, window_obs=None)),
                          ("fixed_5y", dict(min_obs=36, window_obs=60)),
                          ("fixed_10y", dict(min_obs=36, window_obs=120)),
                          ("expand_36_excl_cur", dict(min_obs=MIN_HIST, window_obs=None,
                                                      include_current=False))):
            pct = pct_rank(spread, **kw)
            first = pct.first_valid_index()
            rec = {
                "first_percentile_date": str(first.date()) if first is not None else None,
                "pct_max": float(pct.max()) if pct.notna().any() else None,
                "pct_max_date": str(pct.idxmax().date()) if pct.notna().any() else None,
                "counts": {},
            }
            for th in THRESHOLDS:
                rec["counts"][f"thr{int(th)}"] = len(events_from(pct >= th))
            a[f"A_{years}y"]["percentile"][label] = rec
            if label == "expand_36":
                pct_main = pct
                if r.notna().any():
                    after = r[r.index >= first] if first is not None else r.iloc[0:0]
                    a[f"A_{years}y"]["return_max_after_pct_available"] = (
                        float(after.max()) if after.notna().any() else None)
                    a[f"A_{years}y"]["return_max_after_pct_date"] = (
                        str(after.idxmax().date()) if after.notna().any() else None)
                # 5 年滚动收益序列最高点前后分布
                if years == 5:
                    top5 = r.dropna().nlargest(5)
                    a[f"A_{years}y"]["top5_returns"] = [
                        {"date": str(d.date()), "value": float(v)} for d, v in top5.items()]
    out["hypothesis_A"] = a

    # ---------- 假设 B ----------
    ey = 100.0 / df["pe"].where(df["pe"] > 0)
    spread_b = ey - bond
    b = {
        "ey_first_valid": str(ey.first_valid_index().date()),
        "spread_first_valid": str(spread_b.first_valid_index().date()),
        "n_valid_spread": int(spread_b.notna().sum()),
        "spread_min": float(spread_b.min()),
        "spread_min_date": str(spread_b.idxmin().date()),
        "spread_max": float(spread_b.max()),
        "spread_max_date": str(spread_b.idxmax().date()),
        "percentile": {},
    }
    pct_b_main = None
    for label, kw in (("expand_36", dict(min_obs=MIN_HIST, window_obs=None)),
                      ("expand_36_excl_cur", dict(min_obs=MIN_HIST, window_obs=None,
                                                  include_current=False)),
                      ("expand_60", dict(min_obs=60, window_obs=None)),
                      ("fixed_5y", dict(min_obs=36, window_obs=60)),
                      ("fixed_10y", dict(min_obs=36, window_obs=120)),
                      ("fixed_15y", dict(min_obs=36, window_obs=180))):
        pct = pct_rank(spread_b, **kw)
        first = pct.first_valid_index()
        rec = {
            "first_percentile_date": str(first.date()) if first is not None else None,
            "pct_max": float(pct.max()) if pct.notna().any() else None,
            "pct_max_date": str(pct.idxmax().date()) if pct.notna().any() else None,
            "counts": {f"thr{int(th)}": len(events_from(pct >= th)) for th in THRESHOLDS},
        }
        b["percentile"][label] = rec
        if label == "expand_36":
            pct_b_main = pct
    out["hypothesis_B"] = b

    # ---------- 主实验：B + expand + 36 + thr90 ----------
    flag = (pct_b_main >= 90.0)
    evs = events_from(flag)

    rows = []
    for ev in evs:
        base = {
            "event_id": ev["event_id"],
            "signal_date": str(ev["signal_date"].date()),
            "last_signal_date": str(ev["last_signal_date"].date()),
            "n_signal_obs": ev["n_signal_obs"],
            "exec_date": str(ev["exec_date"].date()) if ev["exec_date"] is not None else None,
            "spread_at_signal": float(spread_b.iloc[ev["signal_pos"]]),
            "ey_at_signal": float(ey.iloc[ev["signal_pos"]]),
            "bond_at_signal": float(bond.iloc[ev["signal_pos"]]),
            "pe_at_signal": float(df["pe"].iloc[ev["signal_pos"]]),
            "percentile_at_signal": float(pct_b_main.iloc[ev["signal_pos"]]),
            "price_at_signal": float(price.iloc[ev["signal_pos"]]),
            "exec_price": (float(price.iloc[ev["exec_pos"]])
                           if ev["exec_pos"] is not None else None),
            "segment": ("IS" if ev["exec_date"] is not None and ev["exec_date"] <= IS_END
                        else "OOS"),
        }
        pos = ev["exec_pos"]
        for td in WINDOWS_TD:
            w = td_to_obs(td)
            f = fwd(price, pos, w) if pos is not None else {"immature": True}
            base[f"ret_{td}td"] = f.get("ret", np.nan)
            base[f"dd_{td}td"] = f.get("dd", np.nan)
            base[f"mfe_{td}td"] = f.get("mfe", np.nan)
            base[f"mae_{td}td"] = f.get("mae", np.nan)
            base[f"end_{td}td"] = str(f["end_date"].date()) if f.get("end_date") is not None else None
            base[f"immature_{td}td"] = bool(f.get("immature", True))
        rows.append(base)
    ev_df = pd.DataFrame(rows)
    ev_df.to_csv(OUT / "event_audit.csv", index=False, encoding="utf-8")

    # 事件窗口重叠检查（250td = 12 obs）
    overlap = []
    for i in range(len(ev_df)):
        for j in range(i + 1, len(ev_df)):
            a_end = ev_df.loc[i, "end_250td"]
            b_start = ev_df.loc[j, "exec_date"]
            if a_end and b_start and a_end >= b_start:
                overlap.append([int(ev_df.loc[i, "event_id"]), int(ev_df.loc[j, "event_id"])])
    out["event_overlap_250td"] = overlap

    # 基准：全部有效观测的无条件前瞻收益
    base_rows = []
    for td in WINDOWS_TD:
        w = td_to_obs(td)
        for pos in range(len(price)):
            f = fwd(price, pos, w)
            if f["immature"]:
                continue
            base_rows.append({"obs_date": price.index[pos], "window_td": td, "ret": f["ret"]})
    base_df = pd.DataFrame(base_rows)

    comp = {}
    for seg in ("full", "IS", "OOS"):
        comp[seg] = {}
        for td in WINDOWS_TD:
            col = f"ret_{td}td"
            e = ev_df[~ev_df[f"immature_{td}td"]]
            if seg == "IS":
                e = e[e["segment"] == "IS"]
            elif seg == "OOS":
                e = e[e["segment"] == "OOS"]
            bsel = base_df[base_df["window_td"] == td]
            if seg != "full":
                bsel = bsel[bsel["obs_date"] <= IS_END] if seg == "IS" else bsel[bsel["obs_date"] > IS_END]
            s = describe(e[col])
            bs = describe(bsel["ret"])
            comp[seg][f"{td}td"] = {
                "n_signals": s["n"], "signal_mean": s["mean"], "signal_median": s["median"],
                "signal_worst": s["worst"], "signal_pct_positive": s["pct_positive"],
                "n_baseline": bs["n"], "baseline_mean": bs["mean"],
                "baseline_median": bs["median"], "baseline_pct_positive": bs["pct_positive"],
                "diff": (s["mean"] - bs["mean"]) if (s["mean"] is not None and bs["mean"] is not None) else None,
            }
    out["comparison_recomputed"] = comp

    # 独立样本容量：给定 12 观测窗口，区间内最多能容纳多少个不重叠样本
    oos_months = int(((df.index > IS_END).sum()))
    out["sample_capacity"] = {
        "oos_obs": oos_months,
        "max_nonoverlapping_12obs_windows_oos": oos_months // 12,
        "n_events_oos": int((ev_df["segment"] == "OOS").sum()),
        "n_events_is": int((ev_df["segment"] == "IS").sum()),
    }

    # ---------- 与研究模块输出交叉核对 ----------
    cross = {}
    try:
        import sys
        sys.path.insert(0, str(ROOT / "backend" / "src"))
        from deepquant.research.equity_bond_spread.config import (  # noqa: E402
            IndicatorType, ResearchConfig)
        from deepquant.research.equity_bond_spread.data import build_panel  # noqa: E402
        from deepquant.research.equity_bond_spread.indicators import (  # noqa: E402
            build_indicator_frame)

        panel = build_panel(EQ, BOND)
        cfg = ResearchConfig(indicator=IndicatorType.B, threshold_pct=90.0)
        fr = build_indicator_frame(panel.frame, cfg)
        merged = pd.DataFrame({
            "spread_audit": spread_b, "spread_module": fr["spread_pct"],
            "pct_audit": pct_b_main, "pct_module": fr["percentile"],
        }).dropna()
        cross["B"] = {
            "n_compared": int(len(merged)),
            "max_abs_spread_diff": float((merged["spread_audit"] - merged["spread_module"]).abs().max()),
            "max_abs_percentile_diff": float((merged["pct_audit"] - merged["pct_module"]).abs().max()),
        }

        cfg_a = ResearchConfig(indicator=IndicatorType.A, lookback_years=5, threshold_pct=90.0)
        fr_a = build_indicator_frame(panel.frame, cfg_a)
        ra = ann_return(price, 5)
        sp_a = ra - bond
        pcta = pct_rank(sp_a, min_obs=MIN_HIST)
        m_a = pd.DataFrame({"s_audit": sp_a, "s_module": fr_a["spread_pct"],
                            "p_audit": pcta, "p_module": fr_a["percentile"]}).dropna()
        cross["A"] = {
            "n_compared": int(len(m_a)),
            "max_abs_spread_diff": float((m_a["s_audit"] - m_a["s_module"]).abs().max()),
            "max_abs_percentile_diff": float((m_a["p_audit"] - m_a["p_module"]).abs().max()),
        }
    except Exception as exc:  # pragma: no cover
        cross["error"] = f"{type(exc).__name__}: {exc}"
    out["cross_check_vs_module"] = cross

    # ---------- 2024 案例时间精度 ----------
    case = {}
    for probe in ("2024-08-30", "2024-09-30", "2024-10-31"):
        t = pd.Timestamp(probe)
        sub = df[df.index <= t]
        if len(sub):
            i = sub.index[-1]
            pct_i = float(pct_b_main.loc[i]) if not pd.isna(pct_b_main.loc[i]) else np.nan
            case[probe] = {
                "last_obs_used": str(i.date()),
                "price": float(df.loc[i, "price"]),
                "pe": None if pd.isna(df.loc[i, "pe"]) else float(df.loc[i, "pe"]),
                "bond_10y": None if pd.isna(sub["bond_10y"].iloc[-1]) else float(sub["bond_10y"].iloc[-1]),
                "spread": float(spread_b.loc[i]) if not pd.isna(spread_b.loc[i]) else None,
                "percentile": pct_i,
                "is_signal": bool(pct_i >= 90.0) if not np.isnan(pct_i) else False,
            }
    out["case_2024"] = case
    out["case_2024"]["exec_of_2024_08_30_event"] = next(
        (r["exec_date"] for r in rows if r["signal_date"] == "2024-08-30"), None)

    # ---------- 落地 ----------
    (OUT / "audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---------- 打印 ----------
    def p(*a):
        print(*a, flush=True)

    p("=" * 78)
    p("面板:", out["meta"])
    p("=" * 78)

    p("\n[假设 A]")
    for years in (5, 10):
        r = a[f"A_{years}y"]
        p(f"  {years}年回溯: 首个有效收益日={r['first_valid_return']} "
          f"首个有效利差日={r['first_valid_spread']} 有效观测={r['n_valid_spread']}")
        p(f"    滚动收益最高={r['return_max']:.2f}% @ {r['return_max_date']}")
        p(f"    百分位可用后最高滚动收益={r['return_max_after_pct_available']} "
          f"@ {r.get('return_max_after_pct_date')}")
        if years == 5:
            p(f"    滚动收益 Top5: {r['top5_returns']}")
        for label, rec in r["percentile"].items():
            p(f"    [{label}] 首个百分位日={rec['first_percentile_date']} "
              f"最高={rec['pct_max']:.2f}% @ {rec['pct_max_date']} 事件数={rec['counts']}")

    p("\n[假设 B]")
    p(f"  盈利收益率首个有效日={b['ey_first_valid']} 利差首个有效日={b['spread_first_valid']}")
    p(f"  利差范围 {b['spread_min']:.2f}% @ {b['spread_min_date']} ~ "
      f"{b['spread_max']:.2f}% @ {b['spread_max_date']}")
    for label, rec in b["percentile"].items():
        p(f"    [{label}] 首个百分位日={rec['first_percentile_date']} "
          f"最高={rec['pct_max']:.2f}% @ {rec['pct_max_date']} 事件数={rec['counts']}")

    p("\n[事件明细]")
    cols = ["event_id", "signal_date", "last_signal_date", "exec_date", "segment",
            "pe_at_signal", "bond_at_signal", "spread_at_signal", "percentile_at_signal",
            "ret_250td"]
    p(ev_df[cols].to_string(index=False))
    p(f"\n  250td 窗口重叠事件对: {overlap}")
    p(f"  样本容量: {out['sample_capacity']}")

    p("\n[聚合复核]")
    for seg in ("full", "IS", "OOS"):
        p(f"  {seg}:")
        for k, v in comp[seg].items():
            p(f"    {k:>6} n={v['n_signals']} signal_mean="
              f"{'—' if v['signal_mean'] is None else f'{v['signal_mean'] * 100:.2f}%'} "
              f"baseline_mean={'—' if v['baseline_mean'] is None else f'{v['baseline_mean'] * 100:.2f}%'} "
              f"diff={'—' if v['diff'] is None else f'{v['diff'] * 100:.2f}%'}")

    p("\n[与研究模块交叉核对]")
    p(" ", cross)

    p("\n[2024 案例]")
    for k, v in case.items():
        p(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
