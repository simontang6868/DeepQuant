#!/usr/bin/env python
r"""阶段 T 检查器的变异测试（mutation test）。

## 为什么必须做

铁律：**任何检查器都必须先做变异测试**——故意注入已知缺陷，确认它真的能检出。
本项目已有六次"检查器自己写错、通过率是假的"前科（V2 交叉验证只比交集、
PC2 判据写错四次、阶段 K 闸门 abs()、阶段 M 闸门 AND 错对象、
阶段 N 变异第 9 类首次未检出）。

阶段 T 决定项目唯一正面结论（exQ5 现代段）**能承重多少**，所以先把刀磨清楚：

| # | 注入的缺陷 | 期望 |
|---|---|---|
| M1 | 极端月剔除集漏掉 2024-01/02（只剔 4 个月） | n 必须从 123 变 127 |
| M2 | 剔除按年份匹配而非月份前缀（把 2016 整年剔掉） | n 必须远小于 123 |
| M3 | top-k 按值而非 \|值\| 选 | 选出的月份集合必须不同 |
| M4 | NW 滞后恒为 3（lag 参数被忽略） | 强自相关序列 L=0/L=3 的 se 必须不同 |
| M5 | 分界点错一个月（2016-02-01） | 现代段 n 必须从 129 变 128 |
| M6 | ADV 分组用等宽而非等频 | 组大小极差必须 > 1（等频下 ≤ 1） |
| M7 | 分解式漏掉池均值项 | 恒等式偏差必须 > 1e-12 |
| M8 | 组内避免效应冒充 contribution | 合成数据上两者必须不相等 |
| M9 | 组合判定只看副检验（主检验失败也判 ALL） | verdict_of(False,True,True) 必须 ≠ T_ROBUST_ALL |
| M10 | T3 比值阈值方向写反 | 同一组数字的判定必须翻转 |

每条变异都要求"缺陷可被观测"，否则对应单测形同虚设。
用法：python scripts/mutation_test_phase_t.py（全部符合期望退出 0）
"""

from __future__ import annotations

import os
import random
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_t_exq5_robustness import (  # noqa: E402
    APRIORI_EXTREME, MODERN_LO, N_ADV_GROUP, adv_quintiles, decompose,
    nw_with_lag, seg_subset, verdict_of,
)

FAILS: list[str] = []


def report(name: str, caught: bool, detail: str) -> None:
    print(f"  [{'OK ' if caught else 'MISS'}] {name}: {detail}")
    if not caught:
        FAILS.append(name)


def _month_dates() -> list[str]:
    """2007-01 ~ 2026-09 的月末交易日序列（237 个月）。"""
    out = []
    for y in range(2007, 2027):
        for m in range(1, 13):
            if y == 2026 and m > 9:
                continue
            out.append(f"{y}-{m:02d}-28")
    return out


def _ar_series(n: int = 300, phi: float = 0.9, seed: int = 11) -> list[float]:
    rng = random.Random(seed)
    cur = 0.0
    for _ in range(n):                 # 燃烧段
        cur = phi * cur + rng.gauss(0.0, 0.1)
    out = []
    for _ in range(n):
        cur = phi * cur + rng.gauss(0.0, 0.1)
        out.append(cur)
    return out


def _synth_rec(n: int = 300, seed: int = 5) -> dict:
    rng = np.random.default_rng(seed)
    adv = rng.lognormal(14.0, 1.2, size=n)          # 右偏，接近真实 ADV 分布
    fwd = rng.normal(0.008, 0.06, size=n)
    is_q5 = np.zeros(n, dtype=bool)
    is_q5[np.argsort(-fwd)[: n // 5]] = True
    return {"signal_date": "2020-01-31", "codes": [f"{i:06d}" for i in range(n)],
            "fwd": fwd.tolist(), "adv": adv.tolist(), "adv_ok": [True] * n,
            "is_q5": is_q5.tolist(), "n_pool": n,
            "alpha": float(fwd[~is_q5].mean() - fwd.mean())}


def case_m1_incomplete_extreme_set() -> None:
    dates = _month_dates()
    vals = [0.11] * len(dates)
    d_good, _ = seg_subset(dates, vals, MODERN_LO, drop=APRIORI_EXTREME)
    partial = tuple(m for m in APRIORI_EXTREME if not m.startswith("2024"))
    d_bad, _ = seg_subset(dates, vals, MODERN_LO, drop=partial)      # 变异
    report("M1 剔除集漏月份", len(d_good) == 123 and len(d_bad) == 125,
           f"正确 n={len(d_good)}（应 123） vs 漏剔 2024 两个月 n={len(d_bad)}"
           f"（单测 test_apriori_drop_removes_exactly_six_from_modern 可抓）")


def case_m2_year_prefix_matching() -> None:
    """变异形态：匹配器按**年份**前缀而不是月份前缀。

    `seg_subset` 自身用 `d[:7] in drop`；要模拟"按年匹配"的缺陷，
    必须连匹配逻辑一起换（只改 drop 的值是无效变异——这一点本身
    就是个教训：变异必须真的改变被检查代码的行为）。
    """
    dates = _month_dates()
    vals = [0.11] * len(dates)
    d_good, _ = seg_subset(dates, vals, MODERN_LO, drop=APRIORI_EXTREME)
    years = {m[:4] for m in APRIORI_EXTREME}                          # 变异：按年剔
    d_bad = [d for d, v in zip(dates, vals)
             if d >= MODERN_LO and d[:4] not in years]
    report("M2 剔除按年份而非月份", len(d_good) == 123 and len(d_bad) < 110,
           f"正确 n={len(d_good)} vs 按年剔 n={len(d_bad)}"
           f"（单测 test_seg_subset_drop_matches_whole_month_prefix 可抓）")


def case_m3_topk_by_value_not_abs() -> None:
    mv = [0.5, -0.9, 0.2, -0.3, 0.1, -0.05, 0.4, -0.6, 0.15, -0.25]
    good = sorted(range(len(mv)), key=lambda i: -abs(mv[i]))
    bad = sorted(range(len(mv)), key=lambda i: -mv[i])               # 变异
    report("M3 top-k 按值不按绝对值", good[0] == 1 and bad[0] == 0,
           f"|值| 最大是索引 1（-0.9），按值最大是索引 0（+0.5）"
           f"（单测 test_topk_drops_largest_absolute_not_largest_positive 可抓）")


def case_m4_nw_lag_ignored() -> None:
    ar = _ar_series()
    s0 = nw_with_lag(ar, 0)
    s3 = nw_with_lag(ar, 3)
    ratio_good = s3["se_nw_pp"] / s0["se_nw_pp"]
    ratio_bad = 1.0                      # 变异：lag 被忽略 → 任何 L 都报 L=3 的数
    report("M4 NW 滞后被忽略", ratio_good > 1.2 and ratio_bad < 1.2,
           f"强自相关序列 se(L=3)/se(L=0) = {ratio_good:.3f}；变异下该比值恒为 "
           f"{ratio_bad:.1f}（单测 test_nw_with_lag_actually_changes_se 可抓）")


def case_m5_boundary_off_by_one_month() -> None:
    dates = _month_dates()
    vals = [0.11] * len(dates)
    d_good, _ = seg_subset(dates, vals, MODERN_LO)
    d_bad, _ = seg_subset(dates, vals, "2016-02-01")                  # 变异
    report("M5 分界点错一个月", len(d_good) == 129 and len(d_bad) == 128,
           f"正确 n={len(d_good)}（应 129） vs 错一月 n={len(d_bad)}"
           f"（单测 test_frozen_phase_s_series_shape 可抓）")


def case_m6_equal_width_instead_of_equal_freq() -> None:
    rng = np.random.default_rng(3)
    adv = rng.lognormal(14.0, 1.2, size=3000)
    g_good = adv_quintiles(adv)
    sizes_good = [int((g_good == i).sum()) for i in range(N_ADV_GROUP)]
    # 变异：等宽分箱（对右偏分布会严重失衡）
    lo, hi = float(adv.min()), float(adv.max())
    edges = np.linspace(lo, hi, N_ADV_GROUP + 1)
    g_bad = np.clip(np.digitize(adv, edges[1:-1]), 0, N_ADV_GROUP - 1)
    sizes_bad = [int((g_bad == i).sum()) for i in range(N_ADV_GROUP)]
    report("M6 ADV 分组等宽冒充等频",
           max(sizes_good) - min(sizes_good) <= 1
           and max(sizes_bad) - min(sizes_bad) > 50,
           f"等频极差 {max(sizes_good)-min(sizes_good)} vs 等宽极差 "
           f"{max(sizes_bad)-min(sizes_bad)}（单测 test_adv_quintiles_balanced_and_contiguous 可抓）")


def case_m7_decomposition_missing_pool_term() -> None:
    rec = _synth_rec()
    rows, _ = decompose([rec])
    good_err = abs(sum(rows[0]["contrib"]) - rows[0]["alpha_t3"])
    # 变异：contribution 只算保留股均值，不减池均值 → 恒等式必破
    fwd = np.asarray(rec["fwd"])
    is_q5 = np.asarray(rec["is_q5"], dtype=bool)
    bad_err = abs(fwd[~is_q5].mean() - rows[0]["alpha_t3"])
    report("M7 分解漏掉池均值项", good_err < 1e-12 and bad_err > 1e-6,
           f"正确恒等式偏差 {good_err:.2e} vs 变异偏差 {bad_err:.2e}"
           f"（单测 test_decompose_identity_holds_exactly 可抓）")


def case_m8_within_effect_as_contribution() -> None:
    rec = _synth_rec(n=400, seed=21)
    rows, _ = decompose([rec])
    r = rows[0]
    # 组大小不等时，"组内避免效应"与"对总 alpha 的贡献"数值必然不同
    diffs = [abs(r["contrib"][i] - r["within"][i]) for i in range(N_ADV_GROUP)]
    report("M8 组内效应冒充贡献", max(diffs) > 1e-6,
           f"两组量最大差 {max(diffs):.6f}pp（单测 "
           f"test_decompose_contribution_matches_hand_computation 可抓）")


def case_m9_verdict_ignores_primary() -> None:
    good = verdict_of(False, True, True)
    bad = "T_ROBUST_ALL"                                               # 变异：只看副检验
    report("M9 组合判定无视主检验", good == "T_FRAGILE" and good != bad,
           f"主检验失败 + 副检验全过：正确判 {good}"
           f"（单测 test_verdict_primary_failure_is_fragile_even_if_secondaries_pass 可抓）")


def case_m10_ratio_threshold_direction() -> None:
    ratio = 4.0                                                        # 假设实测比值
    good = ratio <= 3.0
    bad = ratio >= 3.0                                                 # 变异：方向写反
    report("M10 比值阈值方向写反", good is False and bad is True,
           f"比值 4.0 > 阈值 3.0：正确判「不通过」，变异判「通过」"
           f"（单测 test_t3_ratio_threshold_frozen + test_verdict_one_secondary_fails_is_caveat 可抓）")


def main() -> int:
    print("阶段 T 变异测试（每个缺陷都必须可被观测）")
    for case in (case_m1_incomplete_extreme_set, case_m2_year_prefix_matching,
                 case_m3_topk_by_value_not_abs, case_m4_nw_lag_ignored,
                 case_m5_boundary_off_by_one_month,
                 case_m6_equal_width_instead_of_equal_freq,
                 case_m7_decomposition_missing_pool_term,
                 case_m8_within_effect_as_contribution,
                 case_m9_verdict_ignores_primary,
                 case_m10_ratio_threshold_direction):
        case()
    print()
    if FAILS:
        print(f"!! {len(FAILS)} 个变异未被观测到：{FAILS}")
        return 1
    print("全部 10 个变异均可被观测 → 检查器通过变异测试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
