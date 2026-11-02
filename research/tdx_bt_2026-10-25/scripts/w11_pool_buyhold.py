# -*- coding: utf-8 -*-
"""阶段 W11：「买整池」落地检验 —— 100 只成分股 vs 中证红利指数本身（日频口径）。

为什么需要这一步：
  W10 已证明「买远低于均线的票」被买入不动支配，因此路线回到「买整池」。
  但 W10 的基准只给了月频结果，月频会低估回撤；且「自建 100 只」不等于「买指数」。
  本脚本回答三个问题：
    1. 买整池（自建 100 只，等权 / 按指数权重）在日频下的年化、最大回撤、Calmar 是多少？
    2. 它跟中证红利全收益指数（ETF 的标的）比，是跑赢还是跑输？
    3. 整手约束下，不同资金量有多少钱买不进去（未投资比例）？

口径：
  · 起点 2023-02-01（与 W10 规则的建仓日对齐），终点 2026-10-23
  · 价格：成分股用通达信后复权（红利股不分红会被除权打成假跌）；指数用指数自身点位
  · 日频净值曲线，个股缺失日按上一可用价 carry（停牌不变价）
  · 成本：自建组合一次性买入单边 0.051%；指数/ETF 成本单独讨论，不在此扣
"""
import os, sys, csv, json, glob
from statistics import mean, median

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "tdx_bt_2026-10-25")
EXEC = os.path.join(ROOT, "data", "exec_all")
sys.path.insert(0, os.path.join(RD, "scripts"))
from tdx_io import load_tdx, TDIR  # noqa: E402

START, END = "20230201", "20261023"
COST = 0.00051
IDX_PX, IDX_TR = "000922", "H00922"


def load_series(code, period="4"):
    """合并同 code 多页，返回 {date: close}。"""
    out = {}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period", "")).split(".")[0] != period:
            continue
        if str(d.get("Code", "")).strip() != code:
            continue
        for r in d["Rows"]:
            try:
                out[str(r["Data"])] = float(r["Close"])
            except Exception:
                continue
    return out


def load_stock_panels():
    """100 只后复权日线（口径判别同 scan_pool100：AttachInfo.Now 是不复权现价）。"""
    best = {}
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        d = load_tdx(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period", "")).split(".")[0] != "4":
            continue
        code = str(d.get("Code", "")).strip()
        if not code or code in (IDX_PX, IDX_TR):
            continue
        ser = {}
        for r in d["Rows"]:
            try:
                ser[str(r["Data"])] = float(r["Close"])
            except Exception:
                continue
        if len(ser) < 13:
            continue
        ai = d.get("AttachInfo") or {}
        now = ai.get("Now") or ai.get("Close")
        cls = "adj"
        try:
            last = ser[max(ser)]
            if max(ser) == END and now and float(now) > 0 and abs(last / float(now) - 1) < 0.02:
                cls = "raw"
        except Exception:
            pass
        slot = best.setdefault(code, {})
        for k, v in ser.items():
            cur = slot.get(k)
            if cur is None or (cls == "adj" and cur[1] == "raw") or (cls == cur[1] and v > cur[0]):
                slot[k] = (v, cls)
    return {c: {k: v[0] for k, v in s.items()} for c, s in best.items()}


def metrics(curve, label):
    """curve: [(date, value)]，value 已归一为 1。"""
    vals = [v for _, v in curve]
    n = len(vals)
    days = (int(curve[-1][0][:4]) - int(curve[0][0][:4])) * 365.25
    # 用实际日历天数更准
    from datetime import date
    d0 = date(int(curve[0][0][:4]), int(curve[0][0][4:6]), int(curve[0][0][6:]))
    d1 = date(int(curve[-1][0][:4]), int(curve[-1][0][4:6]), int(curve[-1][0][6:]))
    yrs = (d1 - d0).days / 365.25
    tot = vals[-1] / vals[0] - 1
    ann = (vals[-1] / vals[0]) ** (1 / yrs) - 1 if yrs > 0 else None
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, n) if vals[i - 1] > 0]
    vol = (sum((r - mean(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * (252 ** 0.5)
    return {"label": label, "days": n, "years": yrs, "total": tot, "annual": ann,
            "mdd": mdd, "vol": vol, "calmar": (ann / abs(mdd)) if mdd < 0 else None,
            "sharpe": (ann / vol) if vol > 0 else None,
            "first": curve[0][0], "last": curve[-1][0]}


def main():
    comps = list(csv.DictReader(open(os.path.join(RD, "pool", "000922_components.csv"),
                                     encoding="utf-8-sig")))
    codes = [c["code"] for c in comps]
    wt = {c["code"]: float(c["weight"]) for c in comps}
    print(f"成分权重合计 {sum(wt.values()):.2f}%")

    idx_tr = load_series(IDX_TR)
    idx_px = load_series(IDX_PX)
    print(f"全收益指数 H00922: {min(idx_tr)}~{max(idx_tr)} n={len(idx_tr)}")
    print(f"价格指数 000922 : {min(idx_px)}~{max(idx_px)} n={len(idx_px)}")

    panels = load_stock_panels()
    have = [c for c in codes if c in panels]
    print(f"成分股后复权日线: {len(have)}/100")

    # 主日历 = 全收益指数交易日
    cal = sorted(d for d in idx_tr if START <= d <= END)
    print(f"评估窗口 {cal[0]} ~ {cal[-1]}，交易日 {len(cal)}")

    # ---- 自建组合：起点等权/按权重买入，之后固定股数 ----
    def build(mode):
        p0, sh = {}, {}
        for c in have:
            px = panels[c]
            base = None
            for d in cal:
                if d in px:
                    base = px[d]
                    break
            if not base or base <= 0:
                continue
            p0[c] = base
            sh[c] = (wt[c] if mode == "w" else 1.0) / base
        tw = sum((wt[c] if mode == "w" else 1.0) for c in sh)
        curve, last = [], {}
        for d in cal:
            v = 0.0
            for c in sh:
                if d in panels[c]:
                    last[c] = panels[c][d]
                v += sh[c] * last.get(c, p0[c])
            curve.append((d, v / tw))
        # 一次性买入成本
        f = 1 - COST
        return [(d, x * f) for d, x in curve]

    c_ew = build("ew")
    c_w = build("w")

    def idx_curve(src):
        base = None
        out = []
        for d in cal:
            if d in src:
                if base is None:
                    base = src[d]
                out.append((d, src[d] / base))
        return out

    c_tr = idx_curve(idx_tr)
    c_px = idx_curve(idx_px)

    print()
    hdr = f"{'口径':<26}{'年数':>6}{'累计':>11}{'年化':>9}{'最大回撤':>11}{'年化波动':>10}{'Calmar':>9}{'收益/波动':>10}"
    print(hdr)
    ms = [metrics(c_tr, "中证红利全收益 H00922"),
          metrics(c_px, "中证红利价格 000922"),
          metrics(c_ew, "自建100只 等权"),
          metrics(c_w, "自建100只 按指数权重")]
    for m in ms:
        print(f"{m['label']:<26}{m['years']:>6.2f}{m['total']*100:>10.2f}%{m['annual']*100:>8.2f}%"
              f"{m['mdd']*100:>10.2f}%{m['vol']*100:>9.2f}%{m['calmar']:>9.2f}{m['sharpe']:>10.2f}")

    # ---- 与全收益指数对比（超额） ----
    d_tr = dict(c_tr)
    print("\n相对中证红利全收益指数的累计超额：")
    for m, cur in (("自建100只 等权", c_ew), ("自建100只 按指数权重", c_w)):
        d_c = dict(cur)
        rel = d_c[cal[-1]] / d_tr[cal[-1]] - 1
        print(f"  {m:<22} {rel*100:+.2f}pp")

    # ---- 分年度 ----
    def yearly(cur):
        out = {}
        prev = None
        for d, v in cur:
            y = d[:4]
            if y not in out:
                out[y] = [v, v]
            out[y][1] = v
            if prev is not None and y != prev[0]:
                out[y][0] = prev[1]
            prev = (y, v)
        return {y: b / a - 1 for y, (a, b) in out.items()}

    print(f"\n{'年份':<8}{'全收益指数':>12}{'价格指数':>11}{'自建等权':>11}{'自建按权重':>12}")
    yws = [yearly(c_tr), yearly(c_px), yearly(c_ew), yearly(c_w)]
    for y in sorted(yws[0]):
        print(f"{y:<8}" + "".join(f"{w.get(y, float('nan'))*100:>11.2f}%" for w in yws))

    # ---- 个股跑赢指数比例 ----
    beat = 0
    for c in have:
        px = panels[c]
        b = None
        for d in cal:
            if d in px:
                b = px[d]
                break
        e = px.get(cal[-1])
        if b and e:
            if e / b - 1 > d_tr[cal[-1]] - 1:
                beat += 1
    print(f"\n个股跑赢全收益指数的只数：{beat}/{len(have)}（等权买全池，赢面靠的是分散不是选股）")

    # ---- 整手门槛 ----
    def raw_close(c):
        sc = next(x["setcode"] for x in comps if x["code"] == c)
        sfx = {"1": "SH", "0": "SZ"}.get(str(sc), "BJ")
        p = os.path.join(EXEC, f"{c}.{sfx}.exec.csv")
        if not os.path.exists(p):
            return None
        last = None
        for r in csv.DictReader(open(p, encoding="utf-8")):
            last = r
        try:
            return float(last["close"])
        except Exception:
            return None

    min_ew = 0.0
    n_ok = 0
    for c in codes:
        p = raw_close(c)
        if p is None:
            continue
        n_ok += 1
        min_ew += p * 100          # 1 手 = 100 股
    print(f"\n整手门槛（等权 100 只各买 1 手，真不复权价 {n_ok}/100 只可查）：")
    print(f"  最低资金 ≈ {min_ew/1e4:.1f} 万元（不含费用）")
    print(f"  等权 1 手时单只占比最高/最低差异极大，故下表给出不同资金量下的未投资比例：")

    prices = {}
    for c in codes:
        p = raw_close(c)
        if p:
            prices[c] = p * 100        # 每手金额
    for W in (10e4, 30e4, 50e4, 100e4, 300e4, 1000e4):
        invest = 0.0
        for c, lot in prices.items():
            tgt = W / len(codes)
            k = int(tgt // lot)
            invest += k * lot
        print(f"    W={W/1e4:>7.0f}万 -> 可投 {invest/W*100:>6.2f}%，未投资 {(1-invest/W)*100:>9.2f}%")

    out = {"window": [cal[0], cal[-1]], "trading_days": len(cal),
           "metrics": {m["label"]: m for m in ms},
           "yearly": {y: [w.get(y) for w in yws] for y in sorted(yws[0])},
           "beat_index": [beat, len(have)], "min_capital_1lot": min_ew}
    with open(os.path.join(RD, "pool", "w11_pool.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved pool/w11_pool.json")


if __name__ == "__main__":
    main()
