# -*- coding: utf-8 -*-
"""阶段 A 执行前的三个量化问题：
  1. 长期（2018-01 起，TDX 可得最长历史）价格 vs 全收益 → 分红到底贡献多少
  2. 现在这个位置贵不贵 → 当前 state 与点位在 8.8 年历史中的分位
  3. 一次性 vs 分批 → 不同起点下的最终收益分布

数据：TDX 月线 period=6，000922（价格）与 H00922（全收益），106 根（2018-01 ~ 2026-10）
注意：最后一根 20261023 是未完结月，按最新价处理。
"""
import os, sys, json, glob
from statistics import mean, median

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

PX, TR = "000922", "H00922"


def load_monthly(code):
    """日线按月取最后一个交易日收盘 -> {YYYYMM: close}。"""
    out = {}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period", "")).split(".")[0] != "4":
            continue
        if str(d.get("Code", "")).strip() != code:
            continue
        for r in d["Rows"]:
            try:
                out[str(r["Data"])] = float(r["Close"])
            except Exception:
                continue
    # 日线 -> 月末收盘
    me = {}
    for dd in sorted(out):
        me[dd[:4] + dd[4:6]] = (dd, out[dd])
    return {k: v[1] for k, v in me.items()}


def ann(c0, c1, years):
    return (c1 / c0) ** (1 / years) - 1


def main():
    px = load_monthly(PX)
    tr = load_monthly(TR)
    ds = sorted(px)
    print(f"月末点 {len(ds)} 个：{ds[0]} ~ {ds[-1]}")

    # 用自然日算年数
    from datetime import date
    def to_date(s):
        if len(s) == 8:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        import calendar
        y, m = int(s[:4]), int(s[4:6])
        return date(y, m, calendar.monthrange(y, m)[1])
    yrs = (to_date(ds[-1]) - to_date(ds[0])).days / 365.25
    p0, p1 = px[ds[0]], px[ds[-1]]
    t0, t1 = tr[ds[0]], tr[ds[-1]]

    print("\n=== 1. 长期收益与分红贡献 ===")
    print(f"区间 {ds[0]} ~ {ds[-1]}，{yrs:.2f} 年")
    print(f"  价格指数  {p0:.0f} -> {p1:.0f}   累计 {(p1/p0-1)*100:+.2f}%   年化 {ann(p0,p1,yrs)*100:+.2f}%")
    print(f"  全收益    {t0:.0f} -> {t1:.0f}   累计 {(t1/t0-1)*100:+.2f}%   年化 {ann(t0,t1,yrs)*100:+.2f}%")
    print(f"  → 分红贡献 ≈ {(ann(t0,t1,yrs)-ann(p0,p1,yrs))*100:.2f}pp/年")
    print(f"  → 全收益里分红占比 {max(0,(ann(t0,t1,yrs)-ann(p0,p1,yrs))/ann(t0,t1,yrs))*100:.0f}%")

    # 逐年
    print("\n=== 2. 逐年（价格 / 全收益 / 差额=分红） ===")
    print(f"{'年份':<8}{'价格':>10}{'全收益':>10}{'分红贡献':>10}")
    yrs_list = sorted(set(d[:4] for d in ds))
    for y in yrs_list:
        m = [d for d in ds if d.startswith(y)]
        if len(m) < 1:
            continue
        base_px = [d for d in ds if d < m[0]]
        base_tr = base_px
        if not base_px:
            continue
        bp, bt = px[base_px[-1]], tr[base_tr[-1]]
        print(f"{y:<8}{(px[m[-1]]/bp-1)*100:>9.2f}%{(tr[m[-1]]/bt-1)*100:>9.2f}%"
              f"{((tr[m[-1]]/bt)-(px[m[-1]]/bp))*100:>9.2f}pp")

    # 当前分位
    print("\n=== 3. 现在贵不贵 ===")
    cur_px = px[ds[-1]]
    hist_px = [px[d] for d in ds]
    rk = sum(1 for v in hist_px if v < cur_px) / len(hist_px)
    print(f"  价格指数当前 {cur_px:.0f}，在 {len(ds)} 个月末中的分位 {rk*100:.1f}%")
    print(f"  历史最高 {max(hist_px):.0f}（距高点 {(cur_px/max(hist_px)-1)*100:+.2f}%）"
          f"  历史最低 {min(hist_px):.0f}（距低点 {(cur_px/min(hist_px)-1)*100:+.2f}%）")

    # state（价格口径）历史分位
    ms = ds
    st = {}
    for i, k in enumerate(ms):
        if i < 12:
            continue
        win = [px[x] for x in ms[i - 12:i]]
        st[k] = px[k] / (sum(win) / 12) - 1
    vals = list(st.values())
    cs = st[ms[-1]]
    pct = sum(1 for v in vals if v < cs) / len(vals)
    print(f"  价格口径 state 当前 {cs*100:+.2f}%，历史 {len(vals)} 个月分位 {pct*100:.1f}%"
          f"（中位 {median(vals)*100:+.2f}%，区间 [{min(vals)*100:+.1f}%, {max(vals)*100:+.1f}%]）")

    # 一次性 vs 分批
    print("\n=== 4. 一次性 vs 分 12 批（每月一批，全收益口径） ===")
    print("   同一终点 2026-10，从不同起点开始，比较两种建仓方式到终点的累计收益")
    ends = tr[ds[-1]]
    rows = []
    for i, s in enumerate(ds[:-12]):
        if i + 12 >= len(ds) - 1:
            break
        # 一次性：s 月末全仓
        lump = ends / tr[s] - 1
        # 分 12 批：s 起的 12 个月每月末买 1/12
        legs = []
        for j in range(12):
            k = ds[i + j]
            legs.append(ends / tr[k] - 1)
        stag = mean(legs)
        rows.append((s, lump, stag))
    w_l = sum(1 for _, l, s2 in rows if l > s2)
    d = [l - s2 for _, l, s2 in rows]
    print(f"  可比起点 {len(rows)} 个（{rows[0][0]} ~ {rows[-1][0]}）")
    print(f"  一次性胜出 {w_l}/{len(rows)} = {w_l/len(rows)*100:.1f}%")
    print(f"  一次性 − 分批：均值 {mean(d)*100:+.2f}pp，中位 {median(d)*100:+.2f}pp，"
          f"最差 {min(d)*100:+.2f}pp，最好 {max(d)*100:+.2f}pp")
    print(f"  一次性收益 中位 {median([l for _, l, _ in rows])*100:+.2f}% / "
          f"分批收益 中位 {median([s2 for _, _, s2 in rows])*100:+.2f}%")

    out = {"window": [ds[0], ds[-1]], "years": yrs,
           "px": {"total": p1 / p0 - 1, "annual": ann(p0, p1, yrs)},
           "tr": {"total": t1 / t0 - 1, "annual": ann(t0, t1, yrs)},
           "div_pp": (ann(t0, t1, yrs) - ann(p0, p1, yrs)) * 100,
           "cur_px_pct": rk, "cur_state": cs, "cur_state_pct": pct,
           "state_median": median(vals),
           "lump_vs_stag": {"n": len(rows), "lump_win": w_l / len(rows),
                            "mean_diff": mean(d), "min_diff": min(d), "max_diff": max(d)}}
    with open(os.path.join(RD, "pool", "a_exec.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/a_exec.json")


if __name__ == "__main__":
    main()
