#!/usr/bin/env python
"""分散池诊断：行业分层后，独立样本数 N_eff 到底能不能从 1.88 抬起来？

【池子构造规则——在看到任何回测结果之前冻结】
  1. 宇宙 = 中证红利 000922 当前 100 只成分（历史调仓记录不可得，见 TDX_STUDY_PLAN §五）
  2. 按行业分组，**每行业最多 2 只**
  3. 组内按中证红利权重降序取前 2（权重是构造时点可得的客观指标，不含未来收益信息）
  4. 全部标的须 2011 年前上市（否则覆盖不到 2015 股灾，且月线根数不足会挤进对话）
  5. 行业数 ≥ 12

【为什么这样选而不是"按表现挑"】按权重挑是唯一不含未来信息的排序；
  若按"哪只过去涨得好"挑，就是前视偏差，N_eff 再高也没意义。

数据：通达信 tdx_kline period=6 (月线) tqFlag=2 (后复权)，落盘 txt。
  ⚠️ 长窗口必须后复权：前复权在高股息标的上会穿零（工行 2006-10 = −1.47 元）
"""
from __future__ import annotations
import json
import os
import glob
import math
import statistics
from collections import defaultdict

TDIR = (r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main"
        r"\33860a17-098b-4d49-954f-75fde862c20d\tool-results")

# ── 冻结的行业映射（12 行业 / 20 只）─────────────────────────────
SECTORS: dict[str, list[str]] = {
    "煤炭":     ["600546", "601699"],   # 山煤国际、潞安环能
    "银行":     ["601398", "601166"],   # 工商银行、兴业银行
    "交运":     ["601919", "601006"],   # 中远海控、大秦铁路
    "能源石化": ["600256", "601857"],   # 广汇能源、中国石油
    "有色钢铁": ["601168", "000408"],   # 西部矿业、藏格矿业
    "地产建筑": ["600153", "600039"],   # 建发股份、四川路桥
    "服装家纺": ["600177", "600398"],   # 雅戈尔、海澜之家
    "家电汽车": ["000651", "600741"],   # 格力电器、华域汽车
    "食品农业": ["000895"],             # 双汇发展
    "传媒出版": ["601098"],             # 中南传媒
    "保险":     ["601318"],             # 中国平安
    "化工":     ["600096"],             # 云天化
}
NAMES = {
    "600546": "山煤国际", "601699": "潞安环能", "601398": "工商银行", "601166": "兴业银行",
    "601919": "中远海控", "601006": "大秦铁路", "600256": "广汇能源", "601857": "中国石油",
    "601168": "西部矿业", "000408": "藏格矿业", "600153": "建发股份", "600039": "四川路桥",
    "600177": "雅戈尔", "600398": "海澜之家", "000651": "格力电器", "600741": "华域汽车",
    "000895": "双汇发展", "601098": "中南传媒", "601318": "中国平安", "600096": "云天化",
}
# 对照：原 11 只"资源周期"池（W6 用的那批）
OLD11 = ["601398", "601668", "601699", "601808", "600583", "600188", "600256",
         "601919", "601225", "600985", "601857"]


def parse(p: str) -> dict:
    t = open(p, encoding="utf-8").read()
    return json.JSONDecoder().raw_decode(t[t.find("{"):])[0]


def load_monthly(want: set[str]) -> dict[str, dict[str, tuple[float, float]]]:
    """同一 code 可能有前复权/后复权多个版本 → 取「末值最大」的那个。

    后复权末值 = 实际价 × 累计分红因子 > 实际价 = 前复权末值，故取最大值即可。
    """
    cand: dict[str, list[tuple[float, str, dict]]] = defaultdict(list)
    for f in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        try:
            o = parse(f)
        except Exception:
            continue
        if o.get("Period") != 6:
            continue
        code = o.get("Code")
        rows = o.get("Rows") or []
        if code not in want or not rows:
            continue
        s = {r["Data"]: (float(r["Open"]), float(r["Close"])) for r in rows}
        last_d = max(s)
        cand[code].append((s[last_d][1], f, s))
    out = {}
    for code, lst in cand.items():
        lst.sort(reverse=True)          # 末值最大的（后复权）排前
        out[code] = lst[0][2]
    return out


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else float("nan")


def pc1(mat: list[list[float]], iters: int = 400) -> tuple[float, list[float]]:
    k = len(mat)
    v = [1.0 / math.sqrt(k)] * k
    for _ in range(iters):
        nv = [sum(mat[i][j] * v[j] for j in range(k)) for i in range(k)]
        nrm = math.sqrt(sum(x * x for x in nv))
        v = [x / nrm for x in nv]
    lam = sum(v[i] * sum(mat[i][j] * v[j] for j in range(k)) for i in range(k))
    return lam, v


def neff(codes: list[str], rets: dict[str, list[float]]) -> tuple[float, float, list[float]]:
    pairs, mat = [], []
    for i, a in enumerate(codes):
        row = []
        for j, b in enumerate(codes):
            r = 1.0 if i == j else corr(rets[a], rets[b])
            row.append(r)
            if i < j:
                pairs.append(r)
        mat.append(row)
    rho = statistics.fmean(pairs)
    N = len(codes)
    return N / (1 + (N - 1) * rho), rho, mat


def main() -> None:
    pool = [c for cs in SECTORS.values() for c in cs]
    series = load_monthly(set(pool) | set(OLD11))
    missing = [c for c in pool if c not in series]
    print(f"池子 {len(pool)} 只（12 行业），载入 {len(series)} 只，缺 {missing}")

    print("\n【逐只覆盖】")
    for sec, cs in SECTORS.items():
        for c in cs:
            if c not in series:
                print(f"  {sec:6s} {c} {NAMES[c]:6s} ✗ 无数据")
                continue
            s = series[c]
            print(f"  {sec:6s} {c} {NAMES[c]:6s} {len(s):4d} 月  "
                  f"{min(s)} → {max(s)}  close {min(v[1] for v in s.values()):.2f}~"
                  f"{max(v[1] for v in s.values()):.2f}")

    # 负值自检（后复权不该有负值；有则说明选错版本）
    neg = {c: sum(1 for v in s.values() if v[1] <= 0) for c, s in series.items()}
    bad = {c: n for c, n in neg.items() if n}
    print(f"\n负值/零值价格自检：{'全部干净' if not bad else bad}")

    avail = [c for c in pool if c in series]
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    print(f"\n共同月末 {len(dates)} 个  {dates[0]} → {dates[-1]}")
    if dates[0] > "20120201":
        print("  ⚠️ 起点晚于 2012-02，可能漏掉 2015 股灾前的完整周期")

    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in avail}

    # ── 全池 N_eff ───────────────────────────────────────────────
    n_all, rho_all, mat = neff(avail, rets)
    lam, vec = pc1(mat)
    print(f"\n【分散池 N_eff】N={len(avail)}  ρ̄={rho_all:.4f}  "
          f"N_eff={n_all:.2f}  PC1={lam / len(avail) * 100:.1f}%")

    # ── 行业内部 vs 跨行业相关（这是"行业分散有没有用"的直接检验）──
    intra, inter = [], []
    for i, a in enumerate(avail):
        for j, b in enumerate(avail):
            if i >= j:
                continue
            r = corr(rets[a], rets[b])
            sa = next(s for s, cs in SECTORS.items() if a in cs)
            sb = next(s for s, cs in SECTORS.items() if b in cs)
            (intra if sa == sb else inter).append(r)
    print(f"  行业内部 ρ = {statistics.fmean(intra):.4f} (n={len(intra)} 对)")
    print(f"  跨行业   ρ = {statistics.fmean(inter):.4f} (n={len(inter)} 对)")
    diff = statistics.fmean(inter) - statistics.fmean(intra)
    print(f"  → 跨行业 − 行业内部 = {diff:+.4f}"
          f"（{'行业分散有效' if diff < -0.05 else '行业标签没用，相关性来自共同风格'}）")

    # ── 与 W6 的 11 只资源池对比 ──────────────────────────────────
    old = [c for c in OLD11 if c in series]
    if len(old) >= 3:
        d_old = [d for d in dates if all(d in series[c] for c in old)]
        r_old = {c: [series[c][d_old[i]][1] / series[c][d_old[i - 1]][1] - 1
                     for i in range(1, len(d_old))] for c in old}
        n_o, rho_o, _ = neff(old, r_old)
        print(f"\n【对照 W6 的 11 只资源池】ρ̄={rho_o:.4f}  N_eff={n_o:.2f}  (N={len(old)})")
        print(f"  分散池 N_eff / 旧池 N_eff = {n_all / n_o:.2f}×")

    # ── 行业贡献（等权 vs 权重）──────────────────────────────────
    print("\n【行业等权贡献（全窗口累计）】")
    for sec, cs in SECTORS.items():
        cs2 = [c for c in cs if c in series]
        if not cs2:
            continue
        cum = [series[c][dates[-1]][1] / series[c][dates[0]][1] - 1 for c in cs2]
        print(f"  {sec:6s} {len(cs2)} 只  累计 {statistics.fmean(cum) * 100:+8.2f}%")

    # 落盘供回测脚本读
    outp = os.path.join("research", "tdx_bt_2026-10-25", "pool", "diverse_diag.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"sectors": SECTORS, "names": NAMES,
                   "n": len(avail), "rho": rho_all, "n_eff": n_all,
                   "pc1": lam / len(avail),
                   "intra": statistics.fmean(intra), "inter": statistics.fmean(inter),
                   "window": [dates[0], dates[-1]], "months": len(dates),
                   "codes": avail}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
