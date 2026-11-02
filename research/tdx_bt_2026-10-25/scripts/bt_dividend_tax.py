#!/usr/bin/env python
"""红利税接入回测。

税率（A 股差别化股息红利税）：
  持股 ≤1 个月 20% / 1 个月~1 年 10% / >1 年 0%

实现：按月对组合扣减「当月应计股息 × 税率」。
  - 当月应计股息 = 年化股息率 / 12 × 组合净值
  - 年化股息率取自实际分红记录：
      601398 工行  2026 两次除权 0.1689+0.1511 = 0.320 / 8.32 ≈ 3.9%
      601919 中远海控 0.44+0.43 = 0.87 / 15.85 ≈ 5.5%
      600188 兖矿   0.32+0.20 = 0.52 / 13.0  ≈ 4.0%
    → 池均值取 5.0%（保守取中位数偏下），另做 4%/6% 敏感性。
  ⚠️ 这是池级近似，不是逐笔分红；未建模「分红在除权日一次性到账」的时点效应。

注意：后复权序列已把股息再投资计入收益，所以这里扣税是**新增**的拖累，不重复计。
"""
from __future__ import annotations
import statistics
import bt_pool17 as B
import bt_s3_robust as R

SERIES, CODES, DATES, CLOSES = R.build()
N = len(DATES)
I_SPLIT = next(i for i, d in enumerate(DATES) if d >= "20250101")

YIELD = 0.050          # 池级年化股息率
TAX = {"0%（持股>1年）": 0.0, "10%（1月~1年）": 0.10, "20%（≤1月）": 0.20}


def drag(rate, yld=YIELD):
    """月度税率拖累（复利形式）：(1 - yld/12*rate)"""
    return 1.0 - yld / 12.0 * rate


def apply_tax(eq, rate, yld=YIELD):
    k = drag(rate, yld)
    return [v * (k ** i) for i, v in enumerate(eq)]


def main():
    print(f"池 {len(CODES)} 只  月末 {N} 个  {DATES[0]} → {DATES[-1]}")
    print(f"池级年化股息率假设 {YIELD*100:.1f}%  月度拖累 = 股息/12 × 税率\n")

    segs = {"全窗(18→末)": (18, N - 1), "样本内": (18, I_SPLIT), "样本外": (I_SPLIT, N - 1)}
    cands = {
        "买入持有": lambda a, b: R.buy_hold(SERIES, CODES, DATES, a, b),
        "MA(6,18)择时": lambda a, b: R.run_lowvol(SERIES, CODES, DATES, CLOSES, 6, 1.0, a, b)[0],
        "低波动50%": lambda a, b: R.run_lowvol(SERIES, CODES, DATES, CLOSES, 6, 0.5, a, b)[0],
    }

    for seg, (a, b) in segs.items():
        print(f"【{seg}】")
        print(f"  {'策略':14s} " + "".join(f"{k:>16s}" for k in TAX) + "   税率影响")
        for name, fn in cands.items():
            eq = fn(a, b)
            cells, base = [], None
            for lab, rate in TAX.items():
                m = B.metrics(apply_tax(eq, rate))
                cells.append(f"{m['cagr']*100:>15.2f}%")
                if base is None:
                    base = m["cagr"]
            m20 = B.metrics(apply_tax(eq, 0.20))
            print(f"  {name:14s} " + "".join(cells)
                  + f"   {(m20['cagr']-base)*100:+.2f}pp")
        print()

    # 超额是否还成立（样本外，20% 档）
    print("=" * 70)
    print("【关键检验：扣 20% 红利税后，样本外超额是否翻转】")
    for name, fn in cands.items():
        eq_l = fn(I_SPLIT, N - 1)
        m20 = B.metrics(apply_tax(eq_l, 0.20))
        m0 = B.metrics(eq_l)
        print(f"  {name:14s} 税后年化 {m20['cagr']*100:+6.2f}%（税前 {m0['cagr']*100:+6.2f}%）"
              f"  回撤 {m20['mdd']*100:+7.2f}%  Calmar {m20['calmar']:5.2f}")

    print("\n【股息率敏感性（样本外，20% 税率）】")
    for yld in (0.04, 0.05, 0.06, 0.08):
        row = f"  股息率 {yld*100:.0f}%: "
        for name, fn in cands.items():
            m = B.metrics(apply_tax(fn(I_SPLIT, N - 1), 0.20, yld))
            row += f"{name} {m['cagr']*100:+6.2f}%  "
        print(row)


if __name__ == "__main__":
    main()
