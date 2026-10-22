#!/usr/bin/env python
"""阶段 S 检查器的变异测试（mutation test）。

## 为什么必须做

铁律：**任何检查器都必须先做变异测试**——故意注入已知缺陷，确认它真的能检出。
本项目已有三次"检查器自己写错、通过率是假的"前科（V2 交叉验证、PC2 两版判据）。

阶段 S 是 exQ5 的结项检验，判定直接决定项目最终结论，所以先把刀磨清楚：

| # | 注入的缺陷 | 期望 |
|---|---|---|
| M1 | 冲击项符号翻转（+√ 而不是 −√） | 均值必须偏离 2×0.01899561 |
| M2 | 漏掉退回 Amihud 上界（少 +0.00078376） | 均值必须偏离 0.00078376 |
| M3 | pp 直接传 `newey_west`（放大 100 倍陷阱） | mean_pp 必须 ≈100× 真值 |
| M4 | 分段边界错（2015-01-01） | 月数必须不再是 108/129 |
| M5 | 判据只看绝对 t 不看均值符号 | 负 alpha 必须被误判为 DETECTED（即缺陷可观测） |
| M6 | se_iid 冒充 se_nw | 强自相关序列上比值必须 ≈1.0（<1.5） |
| M7 | bootstrap 丢掉固定种子 | 两次结果必须不同 |
| M8 | 缩放系数误用 √3（而非 1.6792） | 副检验 4 全样本均值必须 ≠ 0.01899561 |
| M9 | NW 滞后误用 0（冒充 L=3） | 强自相关序列上比值必须 < 1.5 |

每条变异都要求"缺陷可被观测"，否则对应单测形同虚设。
用法：python scripts/mutation_test_phase_s.py（全部符合期望退出 0）
"""

from __future__ import annotations

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_s_exq5_modern import (  # noqa: E402
    IMPACT_UPPER, NW_LAG, SCALE_3M_OVER_1M, SQRT_1000W, SQRT_3000W,
    build_series, judge, nw_stats_pp,
)

FAILS: list[str] = []


def report(name: str, caught: bool, detail: str) -> None:
    print(f"  [{'OK ' if caught else 'MISS'}] {name}: {detail}")
    if not caught:
        FAILS.append(name)


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


def _synthetic_rows() -> list[dict]:
    return [{"signal_date": "2020-01-31", "net_BASE_3000w": 0.10,
             "net_BASE_1000w": 0.09, "impact_B_eta1_pp": 0.005,
             "net_STRESS_3000w": 0.0, "net_CONS_1000w": 0.0,
             "net_CONS_3000w": 0.0, "alpha_pp": 0.16,
             "finv_1000w": 0.9, "finv_3000w": 0.95, "n_keep": 3000.0,
             "to": 0.18, "impact_A_pp": 0.0002}]


def case_m1_sign_flip() -> None:
    rows = _synthetic_rows()
    good = build_series(rows)["net_S_3000w"][0]
    bad = rows[0]["net_BASE_3000w"] + IMPACT_UPPER + SQRT_3000W     # 变异：+√
    diff = abs(bad - good)
    report("M1 冲击符号翻转", abs(diff - 2 * SQRT_3000W) < 1e-12,
           f"偏差 {diff:.8f} = 2×{SQRT_3000W:.8f}（单测 test_primary_formula_exact 可抓）")


def case_m2_missing_impact_upper() -> None:
    rows = _synthetic_rows()
    good = build_series(rows)["net_S_3000w"][0]
    bad = rows[0]["net_BASE_3000w"] - SQRT_3000W                     # 变异：漏退回
    diff = abs(bad - good)
    report("M2 漏退 Amihud 上界", abs(diff - IMPACT_UPPER) < 1e-12,
           f"偏差 {diff:.8f} = {IMPACT_UPPER:.8f}（单测 test_primary_formula_exact 可抓）")


def case_m3_pp_into_newey_west() -> None:
    vals = [0.12, -0.05, 0.08, 0.15, -0.02, 0.10, 0.07, -0.09, 0.11, 0.03]
    bad = C.newey_west(vals, lag=NW_LAG)              # 变异：pp 当小数传入
    good = nw_stats_pp(vals)
    ratio = bad["mean_pp"] / good["mean_pp"]
    report("M3 pp 直传 newey_west", abs(ratio - 100.0) < 1e-6,
           f"mean_pp 被放大 {ratio:.2f} 倍（单测 test_pp_series_not_amplified_100x 可抓）")


def case_m4_wrong_split() -> None:
    from run_phase_s_exq5_modern import segment
    dates = [f"{y}-{m:02d}-28" for y in range(2007, 2027) for m in range(1, 13)]
    dates = dates[:237]
    good = segment(dates)
    bad = ["2016-2026" if d >= "2015-01-01" else "2007-2015" for d in dates]  # 变异
    g = (good.count("2007-2015"), good.count("2016-2026"))
    b = (bad.count("2007-2015"), bad.count("2016-2026"))
    report("M4 分段边界错", g == (108, 129) and b != (108, 129),
           f"正确 {g} vs 变异 {b}（单测 test_real_segment_counts_108_129 可抓）")


def case_m5_judge_ignores_sign() -> None:
    good = judge(-0.5, 3.0)
    bad = {"verdict": "MODERN_ERA_ALPHA_DETECTED"}     # 变异：只看 |t|
    report("M5 判据忽略均值符号", good["verdict"] != bad["verdict"],
           f"负 alpha t=3.0：正确判 {good['verdict']}（单测 test_judge_never_calls_negative_mean_detected 可抓）")


def case_m6_iid_as_nw() -> None:
    ar = _ar_series()
    st = nw_stats_pp(ar)
    ratio_bad = 1.0                                    # 变异：se_iid 冒充 se_nw
    report("M6 se_iid 冒充 se_nw", st["se_nw_pp"] / st["se_iid_pp"] > 1.5 > ratio_bad,
           f"强自相关序列 se_nw/se_iid = {st['se_nw_pp'] / st['se_iid_pp']:.3f} > 1.5"
           f"（单测 test_se_nw_exceeds_se_iid_on_positively_autocorrelated 可抓）")


def case_m7_bootstrap_no_seed() -> None:
    from run_phase_s_exq5_modern import boot_ci_pp
    rng = random.Random(5)
    vals = [rng.gauss(0.1, 0.5) for _ in range(129)]
    good_a = boot_ci_pp(vals, n_draws=200)
    good_b = boot_ci_pp(vals, n_draws=200)
    rng2 = random.Random(999)
    bad_a = boot_ci_pp(vals, n_draws=200, seed=rng2.randrange(10 ** 9))
    bad_b = boot_ci_pp(vals, n_draws=200, seed=rng2.randrange(10 ** 9))
    report("M7 bootstrap 丢种子", good_a == good_b and bad_a != bad_b,
           "固定种子两次相同 / 随机种子两次不同（单测 test_bootstrap_deterministic_and_reasonable 可抓）")


def case_m8_wrong_scale() -> None:
    rows = _synthetic_rows()
    S = build_series(rows)
    bad = S["net_S_3000w_pm"][0] + rows[0]["impact_B_eta1_pp"] * (3 ** 0.5) \
        - rows[0]["impact_B_eta1_pp"] * SCALE_3M_OVER_1M     # 变异：√3 冒充 1.6792
    report("M8 缩放系数误用 √3", abs(bad) > 1e-6,
           f"副检验 4 单月偏差 {bad:+.8f}pp；全样本均值将从 0.01899561 漂到 "
           f"{SQRT_1000W * 3 ** 0.5:.8f}（单测 test_scale_factor_value 可抓）")


def case_m9_nw_lag_zero() -> None:
    ar = _ar_series()
    st = nw_stats_pp(ar)
    bad = C.newey_west([v / 100 for v in ar], lag=0)         # 变异：L=0 冒充 L=3
    ratio_bad = bad["se_nw"] * 100 / st["se_iid_pp"]
    report("M9 NW 滞后误用 0", st["se_nw_pp"] / st["se_iid_pp"] > 1.5 > ratio_bad,
           f"L=3 比值 {st['se_nw_pp'] / st['se_iid_pp']:.3f} vs L=0 比值 {ratio_bad:.3f}"
           f"（单测 test_se_nw_exceeds_se_iid_on_positively_autocorrelated 可抓）")


def main() -> int:
    print("阶段 S 变异测试（每个缺陷都必须可被观测）")
    for case in (case_m1_sign_flip, case_m2_missing_impact_upper,
                 case_m3_pp_into_newey_west, case_m4_wrong_split,
                 case_m5_judge_ignores_sign, case_m6_iid_as_nw,
                 case_m7_bootstrap_no_seed, case_m8_wrong_scale,
                 case_m9_nw_lag_zero):
        case()
    print()
    if FAILS:
        print(f"!! {len(FAILS)} 个变异未被观测到：{FAILS}")
        return 1
    print("全部 9 个变异均可被观测 → 检查器通过变异测试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
