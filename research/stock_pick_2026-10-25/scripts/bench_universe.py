# -*- coding: utf-8 -*-
"""基准测算：等权持有全 A 的历史年化（判断 10% 目标是否需要 alpha）

输出：
  1. 全样本 2005-2026 等权全 A 年化（含分红 / 不含分红）
  2. 分段：IS 2005-2017 / OOS1 2018-2021 / OOS2 2022-2026
  3. 分红贡献 = 含分红 − 不含分红

口径：
  含分红   : data/qfq_all/*.qfq.csv 的 close（ADJ，项目自算，有已知送股 bug）
  不含分红 : data/exec_all/*.exec.csv 的 close（真不复权）
  等权     : 每月最后一个交易日调仓至等权，月度收益 = 下月末/本月末 − 1，需两期都有数据
"""
import os, csv, json, glob
from collections import defaultdict
from statistics import mean

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
QFQ = os.path.join(ROOT, "data", "qfq_all")
EXEC = os.path.join(ROOT, "data", "exec_all")
OUT = os.path.join(ROOT, "research", "stock_pick_2026-10-25", "out")


def month_end_series(path, col="close"):
    """读 CSV，返回 {YYYY-MM: (date, close)}，每月份最后一个交易日。"""
    me = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                # date 可能是 YYYY-MM-DD 或 YYYYMMDD，统一成 8 位
                d = str(r.get("date", "")).replace("-", "").strip()
                if len(d) != 8 or not d.isdigit():
                    continue
                try:
                    v = float(r[col])
                except (KeyError, ValueError, TypeError):
                    continue
                if v <= 0:
                    continue
                k = d[:4] + "-" + d[4:6]
                if k not in me or d > me[k][0]:
                    me[k] = (d, v)
    except Exception:
        return None
    return me if len(me) >= 6 else None


def build_panel(base, suffix, col="close"):
    panel = {}
    for p in glob.glob(os.path.join(base, "*." + suffix)):
        code = os.path.basename(p).split(".")[0]
        s = month_end_series(p, col)
        if s:
            panel[code] = s
    return panel


def monthly_ew(panel):
    """等权月度收益：{YYYY-MM: ret}，ret 为该月所有有前后两期数据的个股等权收益。"""
    months = sorted({m for s in panel.values() for m in s})
    out = {}
    for i in range(1, len(months)):
        m0, m1 = months[i - 1], months[i]
        rs = []
        for s in panel.values():
            a = s.get(m0)
            b = s.get(m1)
            if a and b:
                rs.append((b[1] / a[1] - 1, a[0], b[0]))
        if len(rs) >= 100:
            out[m1] = (mean(x[0] for x in rs), len(rs))
    return out


def stats(ret_map, lo, hi, label):
    ms = [m for m in sorted(ret_map) if lo <= m <= hi]
    if not ms:
        return None
    eq = 1.0
    for m in ms:
        eq *= 1 + ret_map[m][0]
    yrs = len(ms) / 12
    ann = eq ** (1 / yrs) - 1
    # 最大回撤（月频）
    peak, mdd = 1.0, 0.0
    eq2 = 1.0
    for m in ms:
        eq2 *= 1 + ret_map[m][0]
        peak = max(peak, eq2)
        mdd = min(mdd, eq2 / peak - 1)
    n_avg = mean(ret_map[m][1] for m in ms)
    return {"label": label, "months": len(ms), "total": eq - 1, "annual": ann,
            "mdd": mdd, "avg_stocks": n_avg}


def main():
    print("读 qfq_all（含分红）…")
    p_adj = build_panel(QFQ, "qfq.csv")
    print(f"  {len(p_adj)} 只")
    print("读 exec_all（不含分红）…")
    p_raw = build_panel(EXEC, "exec.csv")
    print(f"  {len(p_raw)} 只")

    r_adj = monthly_ew(p_adj)
    r_raw = monthly_ew(p_raw)
    print(f"月度序列：含分红 {len(r_adj)} 期｜不含分红 {len(r_raw)} 期")
    print(f"区间：{min(r_adj)} ~ {max(r_adj)}")

    segs = [("2005-01", "2017-12", "IS 2005-2017"),
            ("2018-01", "2021-12", "OOS1 2018-2021"),
            ("2022-01", "2026-10", "OOS2 2022-2026"),
            ("2005-01", "2026-10", "全样本 2005-2026")]

    print("\n" + "=" * 88)
    print("等权持有全 A（月末再平衡）")
    print("=" * 88)
    print(f"{'区间':<22}{'月数':>6}{'累计':>12}{'年化':>9}{'月频MDD':>10}{'平均只数':>9}")
    rows = []
    for lo, hi, lab in segs:
        a = stats(r_adj, lo, hi, lab + " 含分红")
        b = stats(r_raw, lo, hi, lab + " 不含分红")
        if a:
            print(f"{lab+' 含分红':<22}{a['months']:>6}{a['total']*100:>11.1f}%"
                  f"{a['annual']*100:>8.2f}%{a['mdd']*100:>9.2f}%{a['avg_stocks']:>9.0f}")
        if b:
            print(f"{lab+' 不含分红':<22}{b['months']:>6}{b['total']*100:>11.1f}%"
                  f"{b['annual']*100:>8.2f}%{b['mdd']*100:>9.2f}%{b['avg_stocks']:>9.0f}")
        if a and b:
            print(f"{'  -> 分红贡献':<22}{'':>6}{'':>12}{a['annual']*100-b['annual']*100:>8.2f}pp")
        print()
        rows.append({"seg": lab, "adj": a, "raw": b})

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "bench_universe_ew.json"), "w", encoding="utf-8") as f:
        json.dump({"segments": rows,
                   "monthly_adj": {k: v[0] for k, v in r_adj.items()},
                   "monthly_raw": {k: v[0] for k, v in r_raw.items()}},
                  f, ensure_ascii=False, indent=1)
    print("saved out/bench_universe_ew.json")


if __name__ == "__main__":
    main()
