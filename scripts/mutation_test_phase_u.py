#!/usr/bin/env python
r"""阶段 U 检查器的变异测试（mutation test）。

## 为什么必须做

铁律：**任何检查器都必须先做变异测试**——故意注入已知缺陷，确认它真的能检出。
本项目已有九次"没检出≠没问题"的前科，全部是断言/闸门写错，不是数据错。

阶段 U 是**只读审计**，它的"检查器"是三样东西：G1 复现闸门（含单位铁律）、
G2 独立实现交叉验证、翻转检测与三档判定。刀不磨清楚，审计结论不可信。

| # | 注入的缺陷 | 期望可观测 |
|---|---|---|
| M1 | `to_decimal` 对 pp 不除 100 | U2 均值差 100 倍（t 却不变）→ G1 均值校核炸 |
| M2 | `segment_drop` 忽略剔除集 | U12 的 n 从 123 变 129 → 行数闸门炸 |
| M3 | 翻转检测跳过 L=0 | U11 翻转集从 [0,1] 变 [1] |
| M4 | 判定逻辑忽略 A 类 | A 类翻转时判 CONFIRMED 而非 LOAD_BEARING |
| M5 | 独立 NW 漏掉 Bartlett 权重 | AR(0.9) 上与 canonical 差 ≫ 1e-9 → G2 炸 |
| M6 | U1 的 iid 口径被换成 NW(L=0) | 4.3954 vs 记录 4.3861，差 9.3e-3 > G1_TOL |
| M7 | U14 缩放系数被篡改（1.1637803→1.28） | 预测与实测差 10% → 恒等式单测炸 |
| M8 | L 网格被截断成 0..3 | U12 翻转集从 [4,5,6] 变 [] |
| M9 | G1 容差被放大 10000 倍 | 1e-3 的篡改不再被拦 |
| M10 | T_CRIT 被换成单侧 1.645（历史混用） | U12 翻转集从 [4,5,6] 变 [0] |

> ⚠️ 首版有两条**不可观测变异**，已替换（这本身就是教训）：
> ①「精确预测换成模板」——对 U14 数值恒等（U14 与 U11 是纯缩放关系，
> 模板缩放与恒等式给出同一个数）；②「signif 方向写反」——等价变异，
> 只在 t 恰好等于 1.96 时有别。**变异必须真的改变程序行为。**

每条变异都要求"缺陷可被观测"，否则对应单测形同虚设。
用法：python scripts/mutation_test_phase_u.py（全部符合期望退出 0）
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
import run_phase_u_nw_lag_audit as U  # noqa: E402

FAILS: list[str] = []


def report(name: str, caught: bool, detail: str) -> None:
    print(f"  [{'OK ' if caught else 'MISS'}] {name}: {detail}")
    if not caught:
        FAILS.append(name)


def _spec(sid: str) -> dict:
    return next(s for s in U.SERIES if s["id"] == sid)


def _load(sid: str):
    spec = _spec(sid)
    rows_all = U.load_rows(spec["csv"])
    rows = U.apply_filter(rows_all, spec["filt"])
    x = U.to_decimal(rows, spec["col"], spec["unit"])
    return spec, rows, x


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


# ============================================================ M1：单位铁律
def case_m1_pp_not_divided() -> None:
    """pp 列不除 100：t 尺度无关完全不变，只有均值差 100 倍。"""
    spec, rows, x = _load("U2")
    good_pp = U.nw_canonical(x, lag=3)["mean_pp"]
    x_bad = [v * 100.0 for v in x]                      # 变异：pp 当小数直传
    bad_pp = U.nw_canonical(x_bad, lag=3)["mean_pp"]
    t_good = U.nw_canonical(x, lag=3)["t_nw"]
    t_bad = U.nw_canonical(x_bad, lag=3)["t_nw"]
    caught = (abs(good_pp - spec["mean_pp"]) <= U.G1_TOL
              and abs(bad_pp - spec["mean_pp"]) > U.G1_TOL
              and abs(t_good - t_bad) < 1e-9)           # t 居然一样！
    report("M1 pp 未除 100", caught,
           f"正确 mean_pp={good_pp:.6f} vs 变异 {bad_pp:.4f}；"
           f"t 两者相同（{t_good:.6f}）——只有同时校量级才抓得到"
           "（单测 test_g1_gate_catches_wrong_recorded_mean_even_if_t_right 可抓）")


# ============================================================ M2：剔除集失效
def case_m2_drop_ignored() -> None:
    spec = _spec("U12")
    rows_all = U.load_rows(spec["csv"])
    good = U.apply_filter(rows_all, spec["filt"])
    bad = U.apply_filter(rows_all, ("segment", "2016-2026"))   # 变异：不剔
    caught = len(good) == 123 and len(bad) == 129
    report("M2 segment_drop 忽略剔除集", caught,
           f"正确 n={len(good)} vs 变异 n={len(bad)}"
           "（单测 test_u12_drops_exactly_the_six_apriori_months / "
           "test_profile_series_row_count_mismatch_raises 可抓）")


# ============================================================ M3：翻转检测跳过 L=0
def case_m3_flip_skips_lag0() -> None:
    _, _, x = _load("U11")
    prof = {L: U.nw_canonical(x, lag=L)["t_nw"] for L in U.LAG_GRID}
    base = prof[3] >= U.T_CRIT
    good = [L for L in U.LAG_GRID if (prof[L] >= U.T_CRIT) != base]
    bad = [L for L in U.LAG_GRID if L != 0 and (prof[L] >= U.T_CRIT) != base]
    caught = good == [0, 1] and bad == [1]
    report("M3 翻转检测跳过 L=0", caught,
           f"正确翻转 {good} vs 变异 {bad}"
           "（单测 test_measured_flips_match_frozen_expectations 可抓）")


# ============================================================ M4：判定忽略 A 类
def case_m4_verdict_ignores_class_a() -> None:
    flips = {"U6": [0], "U11": [0]}
    cls = {"U6": "A", "U11": "B"}
    good = U.verdict_of(flips, cls)
    bad = "NW_LAG_SENSITIVITY_CONFIRMED"        # 变异：不查 A 类
    caught = good == "NW_LAG_SENSITIVITY_LOAD_BEARING" and good != bad
    report("M4 判定忽略 A 类", caught,
           f"正确 {good} vs 变异 {bad}"
           "（单测 test_verdict_of_three_tiers 可抓）")


# ============================================================ M5：独立 NW 漏权重
def case_m5_independent_nw_wrong_weight() -> None:
    import math

    ar = _ar_series()
    worst_good = max(abs(U.nw_independent(ar, L)[2]
                         - U.nw_canonical(ar, L)["t_nw"])
                     for L in U.LAG_GRID)

    def bad_nw(x, lag):                          # 变异： Bartlett 权重恒为 1
        import numpy as np
        arr = np.asarray(x, dtype=float)
        n = arr.size
        m = float(arr.mean())
        e = arr - m
        ac = np.correlate(e, e, mode="full")[n - 1:] / n
        s = float(ac[0]) + 2.0 * sum(float(ac[j]) for j in range(1, lag + 1))
        se = math.sqrt(max(s, float(ac[0])) / n)
        return m / se

    worst_bad = max(abs(bad_nw(ar, L) - U.nw_canonical(ar, L)["t_nw"])
                    for L in U.LAG_GRID)
    caught = worst_good <= U.G2_TOL and worst_bad > 1e-3
    report("M5 独立 NW 漏 Bartlett 权重", caught,
           f"正确最大差 {worst_good:.2e} vs 变异 {worst_bad:.2e}"
           "（G2 闸门 + 单测 test_independent_nw_matches_canonical_* 可抓）")


# ============================================================ M6：iid 口径被换
def case_m6_iid_replaced_by_nw0() -> None:
    spec, _, x = _load("U1")
    good = U.iid_t(x)
    bad = U.nw_canonical(x, lag=0)["t_nw"]        # 变异：NW(L=0) 冒充 iid
    d_good = abs(good - spec["t3"])
    d_bad = abs(bad - spec["t3"])
    caught = d_good <= U.G1_TOL and d_bad > U.G1_TOL
    report("M6 iid 口径被换成 NW(L=0)", caught,
           f"iid={good:.6f}（与记录差 {d_good:.2e}） vs NW(L=0)={bad:.6f}"
           f"（差 {d_bad:.2e} > G1_TOL {U.G1_TOL:.0e}）"
           "（单测 test_g1_gate_u1_passes_under_iid_and_would_fail_under_nw3 可抓）")


# ============================================================ M7：缩放系数被篡改
def case_m7_ratio_tampered() -> None:
    u11 = U.profile_series(_spec("U11"))["profile"]
    actual = U.profile_series(_spec("U14"))["profile"]
    spec14 = _spec("U14")
    good = {L: u11[L]["t_nw"] * U.U14_RATIO for L in U.LAG_GRID}
    bad_ratio = U.U14_RATIO * 1.1               # 变异：系数被篡改 10%
    bad = {L: u11[L]["t_nw"] * bad_ratio for L in U.LAG_GRID}
    err_good = max(abs(good[L] - actual[L]["t_nw"]) for L in U.LAG_GRID)
    err_bad = max(abs(bad[L] - actual[L]["t_nw"]) for L in U.LAG_GRID)
    caught = err_good <= 1e-12 and err_bad > 0.1
    report("M7 U14 缩放系数被篡改", caught,
           f"正确系数最大差 {err_good:.2e} vs 篡改 10% 后 {err_bad:.3f}"
           "（单测 test_predicted_profile_u14_is_exact_identity_on_real_data 可抓）")
    # 附注：模板缩放对 U14 与恒等式数值恒等（纯缩放关系）——
    # 首版「精确→模板」变异因此不可观测，已替换为本条。


# ============================================================ M8：L 网格被截断
def case_m8_grid_truncated() -> None:
    _, _, x = _load("U12")
    prof = {L: U.nw_canonical(x, lag=L)["t_nw"] for L in (0, 1, 2, 3, 4, 5, 6)}
    base = prof[3] >= U.T_CRIT
    good = [L for L in (0, 1, 2, 3, 4, 5, 6) if (prof[L] >= U.T_CRIT) != base]
    truncated = (0, 1, 2, 3)                       # 变异：只审计到主口径
    bad = [L for L in truncated if (prof[L] >= U.T_CRIT) != base]
    caught = good == [4, 5, 6] and bad == []
    report("M8 L 网格截断成 0..3", caught,
           f"正确翻转 {good} vs 变异 {bad}——截断后'后段更显著'的事实被藏住"
           "（单测 test_measured_flips_match_frozen_expectations 可抓）")


# ============================================================ M9：容差被放大
def case_m9_tolerance_inflated() -> None:
    spec = _spec("U6")
    tamper = 1e-3                                # 篡改量
    d = abs(spec["t3"] + tamper - spec["t3"])
    good_caught = d > U.G1_TOL
    tol_bad = U.G1_TOL * 1e4                     # 变异：容差放大 10000 倍
    bad_caught = d > tol_bad
    caught = good_caught and not bad_caught
    report("M9 G1 容差放大 10000 倍", caught,
           f"篡改 {d:.0e}：容差 {U.G1_TOL:.0e} 能拦，容差 {tol_bad:.0e} 拦不住"
           "（单测 test_g1_gate_catches_wrong_recorded_t 的 +1e-3 篡改可抓）")


# ============================================================ M10：单双侧混用
def case_m10_one_sided_crit() -> None:
    """MEMORY 里记着的历史坑：单侧 1.645 与双侧 1.96 混用过。"""
    _, _, x = _load("U12")
    prof = {L: U.nw_canonical(x, lag=L)["t_nw"] for L in U.LAG_GRID}
    base = prof[3] >= U.T_CRIT
    good = [L for L in U.LAG_GRID if (prof[L] >= U.T_CRIT) != base]
    crit_bad = 1.645                            # 变异：单侧临界
    base_b = prof[3] >= crit_bad
    bad = [L for L in U.LAG_GRID if (prof[L] >= crit_bad) != base_b]
    caught = good == [4, 5, 6] and bad == [0]
    report("M10 T_CRIT 换成单侧 1.645", caught,
           f"正确翻转 {good} vs 变异 {bad}——同一个序列的'稳健区'被整体搬走"
           "（单测 test_measured_flips_match_frozen_expectations 可抓）")


def main() -> None:
    print("阶段 U 变异测试（检查器必须先证明能抓错）")
    case_m1_pp_not_divided()
    case_m2_drop_ignored()
    case_m3_flip_skips_lag0()
    case_m4_verdict_ignores_class_a()
    case_m5_independent_nw_wrong_weight()
    case_m6_iid_replaced_by_nw0()
    case_m7_ratio_tampered()
    case_m8_grid_truncated()
    case_m9_tolerance_inflated()
    case_m10_one_sided_crit()
    print(f"\n合计：{10 - len(FAILS)}/10 可观测")
    if FAILS:
        raise SystemExit("未被观测到的变异（对应单测形同虚设）：" + "；".join(FAILS))


if __name__ == "__main__":
    main()
