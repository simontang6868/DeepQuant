# -*- coding: utf-8 -*-
"""中证红利 100 只成分股 —— 「现价 / MA12 − 1」状态扫描。

【数据源决策，有据】弃用本地 data/qfq_all 的自算 ADJ：
  在 MA12 窗口内与通达信后复权最大形状偏离 36.5%（002572 索菲亚 2026-05→06：
  TDX −11.9% vs qfq −21.3%），中位 6.87pp，属「送股 RATIO 翻倍」老问题。
  本脚本一律用 TDX：period=4 日线 700 根（2023-11-30 起）88 只 +
  period=6 月线 260 根（上市起）12 只，tqFlag=2（后复权）。

【为什么用后复权】红利股股息率 5~7%，不复权会在除权日凭空「跌」5%，把 state 打偏；
  后复权与前复权的 P/MA12 是同一个数（只差常数缩放），不复权不是。
  另并列 state_raw（不复权）作诊断，看口径是否会把票分到不同档。

【如何区分复权口径】接口响应体不返回 tqFlag。利用可证的性质：
  后复权以上市首根为锚、复权因子只增 → 同一代码后复权价恒 >= 不复权价。
  故同代码多条日线记录中，末根收盘价最大者 = 后复权（主口径），最小者 = 不复权。

【统一窗口】回撤/收益一律在 2023-11-30 ~ 2026-10-23 同一段上算。
"""
import os, sys, csv, json, glob
from collections import OrderedDict

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

WIN_START = "20231130"
ASOF = "20261023"
BUCKETS = [
    ("远低于均线", -9.99, -0.08),
    ("低于均线", -0.08, -0.02),
    ("均线附近", -0.02, 0.02),
    ("高于均线", 0.02, 0.08),
    ("远高于均线", 0.08, 9.99),
]


def bucket_of(v):
    for nm, lo, hi in BUCKETS:
        if lo <= v < hi:
            return nm
    return "?"


def series_from(d):
    out = []
    for r in d["Rows"]:
        try:
            out.append((str(r["Data"]), float(r["Close"])))
        except Exception:
            continue
    out.sort()
    return out


def build_index():
    """{code: {period: [ record... ]}}，record = dict(ser, cls)。

    复权口径判别：响应体不带 tqFlag，但 AttachInfo.Now/Close 是**真实不复权现价**
    （实测 002756：Now=35.89，后复权序列末根 111.00、不复权 35.89）。
    故序列末根收盘价与 AttachInfo.Now 相差 <2% 者判为不复权('raw')，否则判为后复权('adj')。
    仅在序列末根等于最新交易日时判别（翻页旧页不参与口径判别）。
    """
    idx = {}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        per = str(d.get("Period", "")).split(".")[0]
        if per not in ("4", "6"):
            continue
        code = str(d.get("Code", "")).strip()
        if not code:
            continue
        ser = series_from(d)
        if len(ser) < 13:
            continue
        ai = d.get("AttachInfo") or {}
        now = ai.get("Now") or ai.get("Close")
        cls = "adj"
        try:
            if ser[-1][0] == ASOF and now and float(now) > 0:
                if abs(ser[-1][1] / float(now) - 1.0) < 0.02:
                    cls = "raw"
        except Exception:
            pass
        try:
            nowv = float(now) if now else None
        except Exception:
            nowv = None
        idx.setdefault(code, {}).setdefault(per, []).append(
            {"ser": ser, "cls": cls, "now": nowv})
    for code, byp in idx.items():
        for per, lst in byp.items():
            uniq, seen = [], set()
            for r in lst:
                s = r["ser"]
                k = (s[0][0], s[-1][0], round(s[-1][1], 4), len(s))
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(r)
            byp[per] = uniq
    return idx


def pick_covered(byp, per, cls, lo="20250901", hi="20261001"):
    """只保留覆盖 [lo, hi] 且口径为 cls 的记录。

    必须先筛覆盖面再看价格：翻页旧页（startxh=700）在下跌股上末根收盘价可能
    高于最新页，若按「末根价最大」取，会把只到 2023-11 的旧页当成主口径。
    """
    out = [r for r in byp.get(per, [])
           if r["cls"] == cls and r["ser"][0][0] <= lo and r["ser"][-1][0] >= hi]
    out.sort(key=lambda r: -len(r["ser"]))
    return out


def month_end(ser):
    buck = OrderedDict()
    for d, c in ser:
        buck[d[:6]] = (d, c)
    return [(k, v[1], v[0]) for k, v in buck.items()]


def state_at(me, i, incl=True):
    lo = i - 11 if incl else i - 12
    if lo < 0:
        return None
    win = [x[1] for x in me[lo:i + 1]] if incl else [x[1] for x in me[lo:i]]
    if len(win) != 12 or any(w <= 0 for w in win):
        return None
    return me[i][1] / (sum(win) / 12.0) - 1.0


def mdd(closes):
    peak, worst = None, 0.0
    for c in closes:
        peak = c if peak is None or c > peak else peak
        if peak > 0:
            worst = min(worst, c / peak - 1.0)
    return worst


def main():
    comps = list(csv.DictReader(open(os.path.join(RD, "pool", "000922_components.csv"),
                                     encoding="utf-8-sig")))
    idx = build_index()
    rows, missing = [], []

    for c in comps:
        code, name, wt, sc = c["code"], c["name"], float(c["weight"]), c["setcode"]
        byp = idx.get(code, {})
        adj_r, rc = None, None
        for per in ("4", "6"):
            g = pick_covered(byp, per, "adj")
            if g:
                adj_r, src = g[0], ("D" if per == "4" else "M")
                break
        if adj_r is None:
            missing.append({"code": code, "name": name, "why": "无覆盖窗口的后复权 K线"})
            continue
        adj = adj_r["ser"]
        for per in ("4", "6"):
            g = pick_covered(byp, per, "raw")
            if g:
                rc = g[0]["ser"]
                break
        # 不复权对照只取日线（月线条目里混有前复权，末根价碰巧等于现价会被误判）
        g4 = pick_covered(byp, "4", "raw")
        rc = g4[0]["ser"] if g4 else None
        me = month_end(adj)
        i = len(me) - 1
        s_incl, s_excl = state_at(me, i, True), state_at(me, i, False)
        if s_excl is None:
            missing.append({"code": code, "name": name, "why": "月末序列不足 13 月"})
            continue

        # 历史分位：用该股的长月线（260 根，后复权）
        m6 = [r for r in byp.get("6", []) if r["cls"] == "adj"]
        m6.sort(key=lambda r: -len(r["ser"]))
        hist, hsrc = [], None
        if m6 and m6[0]["ser"][0][0] <= "20090101":
            mme = month_end(m6[0]["ser"])
            for k in range(12, len(mme)):
                v = state_at(mme, k, False)
                if v is not None and mme[k][0] >= "200601":
                    hist.append(v)
            hsrc = "M"
        pct = (sum(1 for h in hist if h < s_excl) / len(hist)) if len(hist) >= 60 else None

        w = [x for x in adj if x[0] >= WIN_START]
        closes = [x[1] for x in w]
        y1 = [x for x in adj if x[0] >= "20251023"]

        state_raw, px, raw_bad = None, None, False
        if rc is not None:
            rme = month_end(rc)
            v = state_at(rme, len(rme) - 1, False) if len(rme) >= 13 else None
            # 可证恒等式：后复权因子只增 => 1+state_不复权 < 1+state_后复权。
            # 违反者说明该记录不是不复权（实测为前复权/异常序列），剔除而不是报出去。
            if v is not None and (1.0 + v) < (1.0 + s_excl):
                state_raw = v
            elif v is not None:
                raw_bad = True
        if px is None:
            px = adj_r.get("now")

        rows.append({
            "code": code, "name": name, "weight": wt, "src": src,
            "state_incl": s_incl, "state_excl": s_excl, "state_raw": state_raw,
            "bucket": bucket_of(s_excl),
            "pct_self": pct, "n_hist": len(hist), "hist_src": hsrc,
            "mdd_win": mdd(closes) if len(closes) > 2 else None,
            "ret_win": (closes[-1] / closes[0] - 1.0) if len(closes) > 2 else None,
            "ret_1y": (y1[-1][1] / y1[0][1] - 1.0) if len(y1) > 2 else None,
            "raw_close": px, "raw_avail": rc is not None, "raw_suspect": raw_bad,
        })

    rows.sort(key=lambda r: r["state_excl"])
    out = {"asof": ASOF, "win_start": WIN_START, "n": len(rows),
           "missing": missing, "rows": rows}
    with open(os.path.join(RD, "pool", "scan100.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    nd = sum(1 for r in rows if r["src"] == "D")
    print(f"asof={ASOF}  有效={len(rows)}  缺失={len(missing)}  日线源 {nd} / 月线源 {len(rows)-nd}")
    print(f"有长月线算历史分位: {sum(1 for r in rows if r['pct_self'] is not None)} 只 | "
          f"有不复权对照: {sum(1 for r in rows if r['state_raw'] is not None)} 只")
    if missing:
        print("缺失:", missing)
    print()
    print(f"{'代码':<7}{'名称':<9}{'权重':>6}{'现价':>8}{'state含':>9}{'state稳':>9}{'不复权':>9}  "
          f"{'分档':<11}{'分位':>6}{'窗口回撤':>9}{'窗口收益':>9}{'近1年':>8}")
    for r in rows:
        px = f"{r['raw_close']:.2f}" if r["raw_close"] else "-"
        pct = f"{r['pct_self']*100:.0f}%" if r["pct_self"] is not None else "n/a"
        sw = f"{r['state_raw']*100:.1f}%" if r["state_raw"] is not None else "-"
        print(f"{r['code']:<7}{r['name']:<9}{r['weight']:>6.2f}{px:>8}"
              f"{r['state_incl']*100:>8.2f}%{r['state_excl']*100:>8.2f}%{sw:>9}  {r['bucket']:<11}"
              f"{pct:>6}{r['mdd_win']*100:>8.1f}%{r['ret_win']*100:>8.1f}%{r['ret_1y']*100:>7.1f}%")
    print()
    print("分档人数:", {b[0]: sum(1 for r in rows if r["bucket"] == b[0]) for b in BUCKETS})
    flip = [r for r in rows if bucket_of(r["state_incl"]) != r["bucket"]]
    print(f"口径翻转(含当月 vs 已完结): {len(flip)} 只 -> " +
          ", ".join(f"{r['code']}{r['name']}" for r in flip))
    fr = [r for r in rows if r["state_raw"] is not None and bucket_of(r["state_raw"]) != r["bucket"]]
    print(f"不复权 vs 后复权 分档翻转(仅 17 只有对照): {len(fr)} 只")
    for r in fr:
        print(f"   {r['code']} {r['name']:<8} 后复权{r['state_excl']*100:+.2f}%({r['bucket']})"
              f" | 不复权{r['state_raw']*100:+.2f}%({bucket_of(r['state_raw'])})")


if __name__ == "__main__":
    main()
