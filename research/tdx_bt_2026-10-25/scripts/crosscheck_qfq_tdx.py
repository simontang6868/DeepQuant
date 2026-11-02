#!/usr/bin/env python
"""两路数据一致性校验：本地 ADJ 湖（data/qfq_all）vs 通达信后复权（tool-results）。

问题：本项目 tdx 研究线一直用通达信 tqFlag=2（后复权）；而项目主线用自算 ADJ。
      把 100 只成分扫描切到本地湖，必须先证明两路**月收益**一致，否则口径混用。

判据（预登记，先写死后跑）：
  A. 同一标的、同一月，两路月收益差 |Δ| 的中位数 < 0.3pp（分红调整方式差异带来的噪声）
  B. 任一月 |Δ| > 3pp 的月份占比 < 1%（除权处理不同会在这里暴露）
  C. 2023-01~2026-10 累计收益差 < 2pp
只要 A/B 任一不过，就不许混用，改回逐只拉通达信。
"""
from __future__ import annotations
import glob
import json
import os
import statistics
from collections import defaultdict

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
QFQ = os.path.join(ROOT, "data", "qfq_all")
TDIR = (r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main"
        r"\33860a17-098b-4d49-954f-75fde862c20d\tool-results")
OUT = os.path.join(os.path.dirname(__file__), "..", "pool")

TESTS = {"601398": "工商银行", "600985": "淮北矿业", "601088": "中国神华",
         "000651": "格力电器", "601857": "中国石油", "600028": "中国石化"}


def tdx_monthly(code: str) -> dict[str, float]:
    """从 tool-results 找该 code 的月线（period=6）落盘，返回 {YYYYMM: close}。"""
    best = None
    for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        t = open(p, encoding="utf-8", errors="replace").read()
        i = t.find("{")
        if i < 0:
            continue
        try:
            d = json.JSONDecoder().raw_decode(t[i:])[0]
        except Exception:
            continue
        if str(d.get("Code")) != code or d.get("Period") != 6:
            continue
        rows = d.get("Rows") or []
        if rows and (best is None or len(rows) > len(best)):
            best = rows
    if not best:
        return {}
    return {r["Data"][:6]: float(r["Close"]) for r in best}


def qfq_monthly(code: str) -> dict[str, float]:
    for suf in (".SZ", ".SH", ".BJ"):
        p = os.path.join(QFQ, code + suf + ".qfq.csv")
        if os.path.exists(p):
            break
    else:
        return {}
    out: dict[str, float] = {}
    for r in _rows(p):
        out[r[0][:7].replace("-", "")] = float(r[2])
    return out


def _rows(p):
    with open(p, encoding="utf-8") as f:
        next(f)
        for ln in f:
            a = ln.rstrip("\n").split(",")
            if len(a) >= 3:
                yield a


def mrets(series: dict[str, float], lo="202301", hi="202610") -> dict[str, float]:
    ks = sorted(k for k in series if lo <= k <= hi)
    return {ks[i]: series[ks[i]] / series[ks[i - 1]] - 1 for i in range(1, len(ks))}


def main() -> None:
    rows = []
    allpass = True
    for code, name in TESTS.items():
        a = mrets(tdx_monthly(code))
        b = mrets(qfq_monthly(code))
        common = sorted(set(a) & set(b))
        if len(common) < 24:
            print(f"{code} {name}: 可比月数不足 {len(common)}")
            allpass = False
            continue
        diffs = [(a[m] - b[m]) * 100 for m in common]      # pp
        med = statistics.median([abs(x) for x in diffs])
        big = sum(1 for x in diffs if abs(x) > 3.0) / len(diffs)
        cum_a = cum(a, common)
        cum_b = cum(b, common)
        okA, okB, okC = med < 0.30, big < 0.01, abs(cum_a - cum_b) < 2.0
        allpass &= (okA and okB)
        rows.append({"code": code, "name": name, "months": len(common),
                     "median_abs_pp": round(med, 4), "frac_gt3pp": round(big, 4),
                     "cum_tdx_pp": round(cum_a * 100, 2), "cum_qfq_pp": round(cum_b * 100, 2),
                     "A_median<0.3": okA, "B_big<1%": okB, "C_cum<2pp": okC})
        print(f"{code} {name} n={len(common):3d} 中位|Δ|={med:.4f}pp "
              f"|Δ|>3pp占比={big:.2%} 累计 tdx={cum_a*100:+.2f}pp qfq={cum_b*100:+.2f}pp "
              f"A={okA} B={okB} C={okC}")
    print(f"\n总判定: {'PASS 两路可混用' if allpass else 'FAIL 不许混用'}")
    json.dump({"tests": rows, "pass": bool(allpass)}, 
              open(os.path.join(OUT, "crosscheck_qfq_tdx.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def cum(s, ks):
    v = 1.0
    for k in ks:
        v *= (1 + s[k])
    return v - 1


if __name__ == "__main__":
    main()
