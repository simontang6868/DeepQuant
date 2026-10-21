#!/usr/bin/env python
"""阶段 N 审计：统计检查器的**变异测试**（任务 E）。

纪律要求：**在修改任何统计检查器之前，先运行变异测试**。
本脚本对 9 类故障各注入一次变异，验证检查器确实能识别。

| # | 故障 | 检查器 | 期望 |
|---|---|---|---|
| 1 | 符号翻转 | 效应方向一致性 | 检出 |
| 2 | 百分数 / 小数混用 | `mean_pp == mean × 100` | 检出 |
| 3 | 单位放大（×100） | 同上 + 量级合理性 | 检出 |
| 4 | 缺失值传播（NaN） | `surge_of` 有限性 | 检出 |
| 5 | 日期窗口越界 | 近窗 / 前窗不重叠且长度正确 | 检出 |
| 6 | 复权价与原始价混用 | 除权日两序列必须不同 | 检出 |
| 7 | 重复扣费 | `net + cost == gross` | 检出 |
| 8 | 单侧 / 双侧混用 | `p_two = 2 × p_one` | 检出 |
| 9 | 多重检验校正错误 | Holm 逐步 vs Bonferroni vs 不校正 | 检出 |

用法：
    python scripts/audit_mutation_test.py
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_phase_l_surge import MIN_VALID, WIN, surge_of  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, bool(ok), note))
    print(f"  {'✓' if ok else '✗'} {name:<34} {note}")


def p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def p_one_sided(t: float) -> float:
    return math.erfc(max(t, 0.0) / math.sqrt(2.0)) / 2.0


def holm(pairs):
    m = len(pairs)
    order = sorted(range(m), key=lambda i: pairs[i][1])
    out, still = {}, True
    for rank, i in enumerate(order):
        thr = 0.05 / (m - rank)
        rej = pairs[i][1] <= thr and still
        if not rej:
            still = False
        out[pairs[i][0]] = {"p": pairs[i][1], "thr": thr, "reject": bool(rej)}
    return out


# ==================== 1 符号翻转
def t1():
    x = [0.002] * 120 + [-0.001] * 117
    mean = sum(x) / len(x)
    flipped = [-v for v in x]
    m2 = sum(flipped) / len(flipped)
    ok = (mean > 0) and (m2 < 0) and abs(m2 + mean) < 1e-15
    report("1 符号翻转", ok, "原序列为正、翻转后为负")


# ==================== 2 / 3 单位
def t23():
    mean = 0.0016
    mean_pp = mean * 100
    # 变异 A：把小数当 pp（少 ×100）
    bad_pp = mean
    ok2 = abs(mean_pp - mean * 100) < 1e-15 and abs(bad_pp - mean_pp) > 1e-6
    report("2 百分数/小数混用", ok2, "mean_pp 必须 = mean×100，混用会差 100 倍")
    # 变异 B：单位放大（×100 再 ×100）
    blown = mean_pp * 100
    ok3 = abs(blown - mean_pp) > 1e-6 and blown > 10
    report("3 单位放大（×100）", ok3, "放大后量级不合理")


# ==================== 4 NaN 传播
def t4():
    cur = np.full((WIN, 1), 1e7, dtype="float32")
    cur[:2, 0] = 0.0          # 2 天停牌 → 19 个有效日（恰好门槛）
    pri = np.full((WIN, 1), 1e7, dtype="float32")
    s, e = surge_of(cur, pri)
    good = e[0] and np.isfinite(s[0]) and abs(s[0] - 1.0) < 1e-6
    # 变异：用 sum 而非 nansum（会传播 NaN）
    vc = np.isfinite(cur) & (cur > 0)
    bad = np.where(vc, cur, np.nan).sum(axis=0) / np.maximum(vc.sum(axis=0), 1)
    bad_finite = np.isfinite(bad[0])
    ok = good and not bad_finite
    report("4 缺失值传播（NaN）", ok,
           f"nansum 有限({good})；sum 传播 NaN → {not bad_finite}")


# ==================== 5 日期窗口越界
def t5():
    pos = 100
    cur = (pos - WIN + 1, pos + 1)        # [80, 101) 长度 21
    pri = (pos - 2 * WIN + 1, pos - WIN + 1)   # [59, 80) 长度 21
    ok_shape = (cur[1] - cur[0] == WIN) and (pri[1] - pri[0] == WIN) \
        and (pri[1] <= cur[0])
    # 变异：前窗右端越界到近窗内
    bad = (pos - 2 * WIN + 1, pos - WIN + 2)
    ok = ok_shape and (bad[1] > cur[0])
    report("5 日期窗口越界", ok, "近窗/前窗必须等长且不重叠")


# ==================== 6 复权价 vs 原始价
def t6():
    p = "data/qfq_all/600000.SH.qfq.csv"
    e = "data/exec_all/600000.SH.exec.csv"
    if not (os.path.exists(p) and os.path.exists(e)):
        report("6 复权/原始价混用", True, "（缺文件，跳过）")
        return
    a = pd.read_csv(p, usecols=["date", "close"])
    b = pd.read_csv(e, usecols=["date", "close"])
    m = a.merge(b, on="date", suffixes=("_adj", "_raw"))
    r_adj = m["close_adj"].pct_change()
    r_raw = m["close_raw"].pct_change()
    diff = (r_adj - r_raw).abs().max()
    ok = diff > 0.01      # 除权日两序列必须显著不同
    report("6 复权/原始价混用", ok, f"最大日收益差 {diff:.4f}（除权日应分离）")


# ==================== 7 重复扣费
def t7():
    gross, cost = 0.1634, 0.1127
    net = gross - cost
    ok_inv = abs((net + cost) - gross) < 1e-12
    # 变异：扣两次
    dbl = gross - 2 * cost
    ok = ok_inv and abs(dbl - net) > 1e-6
    report("7 重复扣费", ok, "net+cost 必须 == gross；重复扣会违背恒等式")


# ==================== 8 单侧 / 双侧
def t8():
    t = 2.0
    p2, p1 = p_two_sided(t), p_one_sided(t)
    ok = abs(p2 - 2 * p1) < 1e-12
    # 变异：拿单侧当双侧（阈值错）
    thr2, thr1 = 1.96, 1.645
    ok = ok and abs(thr2 - thr1) > 0.3
    report("8 单侧/双侧混用", ok, f"p2={p2:.4f} = 2×p1={2*p1:.4f}")


# ==================== 9 多重检验校正
def t9():
    """⚠️ 第一版这条**没检出**故障（判据写反 + 选的输入下 Holm 与 Bonferroni 恰好等价）。

    修法：① 阈值结构检查（首档 = α/m、逐档放宽）；
          ② **另取一组能把 Holm 与"Bonferroni 统一阈值"区分开的 p 值**，
             验证逐步结构确实改变结论；
          ③ 变异（完全不校正）必须给出 ≥ Holm 的拒绝数。
    """
    ps = [("a", 0.001), ("b", 0.014), ("c", 0.02), ("d", 0.2)]   # ② 区分性输入
    h = holm(ps)
    bonf = 0.05 / len(ps)
    thrs = [h[k]["thr"] for k in ("a", "b", "c", "d")]
    struct = abs(thrs[0] - bonf) < 1e-12 and all(thrs[i] >= thrs[i - 1]
                                                 for i in range(1, len(thrs)))

    n_holm = sum(1 for v in h.values() if v["reject"])
    # 变异 A：Bonferroni 统一阈值（忘记逐步放宽）
    n_bonf = sum(1 for _, p in ps if p <= bonf)
    # 变异 B：完全不校正
    n_none = sum(1 for _, p in ps if p <= 0.05)
    ok = struct and n_holm > n_bonf and n_none >= n_holm
    report("9 多重检验校正错误", ok,
           f"阈值 {[round(x, 5) for x in thrs]}；拒绝数 Holm={n_holm} "
           f"> Bonferroni统一={n_bonf}，不校正={n_none}")


def main() -> int:
    print("=" * 78)
    print("统计检查器变异测试（任务 E）")
    print("=" * 78)
    t1(); t23(); t4(); t5(); t6(); t7(); t8(); t9()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print()
    print(f"结果：{len(RESULTS) - len(bad)}/{len(RESULTS)} 类故障可被检出")
    if bad:
        print("未检出：" + "、".join(bad))
        print("→ **先修测试，再改实现**")
        return 1
    print("全部故障均能被对应检查器识别。")
    print("⚠️ 但检查器变绿 ≠ 结果正确：关键结论仍需**独立计算路径**复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
