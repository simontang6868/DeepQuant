# -*- coding: utf-8 -*-
"""阶段 2 基础：构建全 A 月度因子面板（v2，修正版）

v1 的两个 bug：
  1. 每只股票用「自己的月末最后交易日」，停牌导致日期不统一（2983 个日期），无法做横截面
     → 修正：统一到「全市场月末交易日」ME，每只股票用 as-of 取该日或之前最近值，
       但若距 ME 超过 MAX_STALE 自然日则视为停牌不可用（避免用陈年价格）
  2. pandas 3.0 链式赋值 df["x"][a:] = ... 静默失效，ret20/ret5 全为 NaN
     → 修正：先算 numpy 数组再整体赋值

因子（全部只用 t 及之前）：
  f2      = mean(TrueRange, 20) / close(t)
  vol20   = std(日收益, 20)
  ret20   = close(t)/close(t-20) - 1
  ret5    = close(t)/close(t-5) - 1
  amihud  = mean(|ret| / 成交额, 20) × 1e9
标签：买入 = ME 之后第 1 个交易日 close；卖出 = 买入后第 20 个交易日 close；fwd20 = 卖/买 - 1
"""
import os, glob
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
QFQ = os.path.join(ROOT, "data", "qfq_all")
OUT = os.path.join(ROOT, "research", "stock_pick_2026-10-25", "data")
os.makedirs(OUT, exist_ok=True)

HOLD = 20
W = 20
MAX_STALE = 15     # 距 ME 超过 15 自然日视为停牌不可用


def roll_mean(x, n):
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def factor_series(df):
    """在原始交易日序列上算因子，返回与 df 同长的 dict of arrays。"""
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    n = len(c)

    ret = np.full(n, np.nan)
    ret[1:] = c[1:] / c[:-1] - 1
    r0 = np.where(np.isnan(ret), 0.0, ret)
    mr = roll_mean(r0, W)
    mr2 = roll_mean(r0 ** 2, W)
    vol = np.sqrt(np.maximum(mr2 - mr ** 2, 0.0))
    vol[: W - 1] = np.nan

    r20 = np.full(n, np.nan)
    r20[W:] = c[W:] / c[:-W] - 1
    r5 = np.full(n, np.nan)
    r5[5:] = c[5:] / c[:-5] - 1

    amt = v * 100.0 * c
    with np.errstate(divide="ignore", invalid="ignore"):
        illiq = np.where(amt > 0, np.abs(ret) / amt * 1e9, 0.0)
    illiq = np.where(np.isnan(illiq), 0.0, illiq)

    # ---- 其余候选因子（全部只用 t 及之前）----
    o = df["open"].to_numpy(float)
    # F1 日内收益均值（close/open - 1），反映散户日内追高
    intraday = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        intraday = np.where(o > 0, c / o - 1, np.nan)
    i0 = np.where(np.isnan(intraday), 0.0, intraday)
    f1 = roll_mean(i0, W)
    f1[: W - 1] = np.nan
    # F3 换手加速度：5日均量 / 60日均量
    v5 = roll_mean(v, 5)
    v60 = roll_mean(v, 60)
    with np.errstate(divide="ignore", invalid="ignore"):
        f3 = np.where(v60 > 0, v5 / v60, np.nan)
    # F4 收益偏度（20日）：m3 / m2^1.5
    m3 = roll_mean(r0 ** 3, W)
    sd3 = np.power(np.maximum(mr2 - mr ** 2, 1e-12), 1.5)
    f4 = m3 / sd3
    f4[: W - 1] = np.nan
    # F5 量价相关性 corr(ret, volume) 20日 —— 向量化（rolling corr）
    f5 = pd.Series(ret).rolling(W).corr(pd.Series(v)).to_numpy()
    # F6 距 60 日高点回撤 —— 向量化（rolling max）
    hi60 = pd.Series(c).rolling(60).max().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f6 = np.where(hi60 > 0, c / hi60 - 1, np.nan)

    return {
        "f1": f1, "f3": f3, "f4": f4, "f5": f5, "f6": f6,
        "f2": roll_mean(tr, W) / c,
        "vol20": vol,
        "ret20": r20,
        "ret5": r5,
        "amihud": roll_mean(illiq, W),
        "amt20": roll_mean(amt, W),
        "close": c,
        "volume": v,
    }


def build_calendar():
    """全市场交易日并集 → 每月最后一个交易日。只读 date 列。"""
    ds = set()
    for p in glob.glob(os.path.join(QFQ, "*.qfq.csv")):
        try:
            d = pd.read_csv(p, usecols=["date"])["date"].astype(str)
        except Exception:
            continue
        ds.update(d.str.replace("-", "", regex=False).dropna().unique().tolist())
    cal = sorted(x for x in ds if len(str(x)) == 8 and str(x).isdigit())
    me = {}
    for d in cal:
        me[d[:6]] = d            # 每月最后出现的交易日覆盖前者
    return [me[k] for k in sorted(me)]


def main():
    ME = build_calendar()
    print(f"市场日历：每月末交易日 {len(ME)} 个｜{ME[0]} ~ {ME[-1]}", flush=True)
    ME_arr = np.array(ME)

    files = sorted(glob.glob(os.path.join(QFQ, "*.qfq.csv")))
    print(f"全 A {len(files)} 只", flush=True)
    rows = []
    bad = 0
    for i, p in enumerate(files):
        code = os.path.basename(p).split(".")[0]
        try:
            df = pd.read_csv(p, usecols=["date", "open", "close", "high", "low", "volume"])
        except Exception:
            bad += 1
            continue
        df = df.dropna()
        df["date"] = df["date"].astype(str).str.replace("-", "", regex=False)
        df = df[df["close"] > 0].sort_values("date").reset_index(drop=True)
        if len(df) < W + HOLD + 5:
            bad += 1
            continue
        f = factor_series(df)
        dates = df["date"].to_numpy()
        dt = pd.to_datetime(dates, format="%Y%m%d")

        # 每个 ME 对应 pos = 该日或之前最近一个交易日
        pos = np.searchsorted(dates, ME_arr, side="right") - 1
        ok = pos >= W - 1
        if not ok.any():
            continue
        pos_c = np.clip(pos, 0, len(dates) - 1)
        stale = (pd.to_datetime(ME_arr, format="%Y%m%d") - dt[pos_c]).days
        ok &= (stale <= MAX_STALE)
        buy = pos + 1
        sell = pos + 1 + HOLD
        ok &= (sell < len(dates))
        if not ok.any():
            continue
        idx = np.where(ok)[0]

        rows.append(pd.DataFrame({
            "date": ME_arr[idx],
            "code": code,
            "f2": f["f2"][pos_c[idx]],
            "vol20": f["vol20"][pos_c[idx]],
            "ret20": f["ret20"][pos_c[idx]],
            "ret5": f["ret5"][pos_c[idx]],
            "amihud": f["amihud"][pos_c[idx]],
            "f1": f["f1"][pos_c[idx]],
            "f3": f["f3"][pos_c[idx]],
            "f4": f["f4"][pos_c[idx]],
            "f5": f["f5"][pos_c[idx]],
            "f6": f["f6"][pos_c[idx]],
            "amt20": f["amt20"][pos_c[idx]],
            "close": f["close"][pos_c[idx]],
            "buy_close": f["close"][buy[idx]],
            "fwd20": f["close"][sell[idx]] / f["close"][buy[idx]] - 1,
            "stale_days": stale[idx],
        }))
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{len(files)}  行 {sum(len(x) for x in rows):,}", flush=True)

    panel = pd.concat(rows, ignore_index=True)
    panel["ok_buy"] = (
        panel["close"].between(1.0, 200.0)
        & panel["f2"].notna() & panel["vol20"].notna()
        & (panel["amt20"] > 5e6)
    )
    path = os.path.join(OUT, "panel_monthly.parquet")
    panel.to_parquet(path, index=False)
    print(f"\n输出 {path}")
    print(f"  行数 {len(panel):,}｜股票 {panel['code'].nunique():,}｜月份 {panel['date'].nunique()}"
          f"｜{panel['date'].min()} ~ {panel['date'].max()}")
    print(f"  解析失败 {bad} 只｜ok_buy {panel['ok_buy'].mean()*100:.1f}%")
    for c in ("f2", "vol20", "ret20", "ret5", "amihud", "f1", "f3", "f4", "f5", "f6", "fwd20"):
        print(f"  {c:<8} 缺失 {panel[c].isna().mean()*100:>5.1f}%")
    g = panel.groupby("date").size()
    print(f"  每月股票数：中位 {int(g.median())}｜最小 {int(g.min())}｜最大 {int(g.max())}")


if __name__ == "__main__":
    main()
