"""封印条目解封复核：把「L3t 抄错了」与「判定量本身不可核对」分开。

L3u 的预登记写「核对未通过不下分级结论」，于是 6 条取错量的条目一律挂 ``NOT_GRADED``。
这条规则防的是「用错量算出好看的结果」，方向没错，但它把两种完全不同的情况压成同一格：

* **A 类（抄错）**：判定统计量与重建序列完全同源，只是 L3t 登记时取了别的列 / 别的窗口。
  这类从未不可核对，封印是过度保守。
* **B 类（不同源）**：判定量与序列均值**本来就不是同一个东西**（回归系数 vs 序列均值、
  3000 万档 vs 1000 万档）。这类拼在一起算 ratio 就是错的，必须继续封印。

本模块只做一件事：用三道同源闸门（均值 / t / n）把 A 类与 B 类分开，再用 NW 滞后网格
检验「解封后的分级是否稳定」。跨桶的不解封——那说明结论对标准误口径敏感，本来就不该被引用。

阈值（2×/1×/0.5×、功效 0.8/0.5）直接复用 ``citation.grade_quotability``，
本模块不定义任何新门槛，避免「解封」变成「放宽」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from deepquant.gates import citation as cit

#: 参与解封复核的核对分类（``t_only`` 的另有 6 条，属于口径展示问题，不在此列）
SEALED_CLASSES = frozenset({cit.MISMATCH_MEAN, cit.MISMATCH_N})

#: 主口径滞后（L3t/L3u 全用 3）
PRIMARY_LAG = 3

#: 稳定性网格。lag=0 时 Bartlett 核只剩 g0，与 iid 的差别仅在自由度因子
#: √((n−1)/n)（NW 用有偏方差 ÷n，iid 用样本方差 ÷(n−1)）；n=237 时差 0.2%。
DEFAULT_LAGS = (0, 1, 2, 3, 4, 6)

#: 同源判定容差：均值用相对差，t 用绝对差。
#: 均值容差首版取 1e-6 过严——summary 里的均值只保留有限位小数，
#: 「同一个均值」与「不同档的均值」之间被卡在 1e-6 附近，误判成不同源。
#: 1e-4（0.01%）仍远小于真实不同档的 20% 量级。
TOL_MEAN_REL = 1e-4
TOL_T_ABS = 1e-4


class UnsealError(ValueError):
    """解封复核的前置条件不满足。"""


def _rel_diff(a: float, b: float) -> float:
    denom = max(abs(float(a)), abs(float(b)), 1e-12)
    return abs(float(a) - float(b)) / denom


def select_sealed(
    rows: Sequence[Mapping[str, Any]], *, mode: str = "mismatch"
) -> list[Mapping[str, Any]]:
    """挑出待复核的封印条目。

    * ``mode="mismatch"``（L3w）—— 核对分类是 ``MISMATCH_*`` 且**不是** ``t_only``：
      判定量本身可能不同源，需要三关验。
    * ``mode="t_only"``（L3x）—— ``t_only=True``：均值与分母都已一致，
      只是判定用的 t 与登记表的 t 口径不同（iid vs NW3）。三关里只有 U2 有意义，
      但仍旧走同一套判定，不另开豁免。
    """
    if mode not in {"mismatch", "t_only"}:
        raise UnsealError(f"未知 mode：{mode!r}")
    out = []
    for row in rows:
        cmp = row.get("comparison") or {}
        if mode == "t_only":
            if cmp.get("t_only"):
                out.append(row)
        elif cmp.get("class") in SEALED_CLASSES and not cmp.get("t_only"):
            out.append(row)
    return out


def check_same_source(
    row: Mapping[str, Any],
    *,
    tol_mean_rel: float = TOL_MEAN_REL,
    tol_t_abs: float = TOL_T_ABS,
) -> dict[str, Any]:
    """三关：判定均值、判定 t、判定分母，是否与重建序列同源。"""
    rc = row.get("recomputed") or {}
    stat = row.get("stat") or {}
    reasons: list[str] = []

    stat_mean = stat.get("mean")
    mean_diff = None
    mean_match = False
    if stat_mean is None or rc.get("mean") is None:
        reasons.append("判定均值或序列均值缺失，无法核对")
    else:
        mean_diff = abs(float(stat_mean) - float(rc["mean"]))
        mean_match = _rel_diff(stat_mean, rc["mean"]) <= tol_mean_rel
        if not mean_match:
            reasons.append(
                f"判定均值 {float(stat_mean):+.4f} 与序列均值 {float(rc['mean']):+.4f} 相对差 "
                f"{_rel_diff(stat_mean, rc['mean']):.2e}（阈值 {tol_mean_rel:.0e}）："
                "判定档与序列档不同源，mean 与 MDE 不可拼"
            )

    stat_t = stat.get("t")
    t_match = None
    if stat_t is None:
        reasons.append("判定 t 缺失")
    else:
        for name in ("t_iid", "t_nw3"):
            value = rc.get(name)
            if value is not None and abs(float(stat_t) - float(value)) <= tol_t_abs:
                t_match = name
                break
        if t_match is None:
            cand = "／".join(
                f"{name}={float(rc[name]):+.4f}"
                for name in ("t_iid", "t_nw3")
                if rc.get(name) is not None
            )
            reasons.append(
                f"判定 t={float(stat_t):+.4f} 与序列的 {cand} 均不符："
                "该 t 不是这条序列均值的 t（回归系数 / 另一模型），不能与序列 MDE 配对"
            )

    stat_n = stat.get("n")
    n_match = stat_n is not None and rc.get("n") is not None and int(stat_n) == int(rc["n"])
    if not n_match:
        reasons.append(
            f"判定分母 {stat_n} ≠ 序列 n {rc.get('n')}：必须确认是回归分母还是子样本窗口"
        )

    return {
        "mean_diff": mean_diff,
        "mean_match": bool(mean_match),
        "t_match": t_match,
        "n_match": bool(n_match),
        "stat_n": stat_n,
        "ok": bool(mean_match and t_match and n_match),
        "reasons": reasons,
    }


def mde_grid(
    values: Sequence[float],
    *,
    lags: Sequence[int] = DEFAULT_LAGS,
) -> dict[int, dict[str, Any]]:
    """在 NW 滞后网格上重算 se/MDE/功效。同一序列、同一实现，只改滞后。"""
    grid: dict[int, dict[str, Any]] = {}
    for lag in lags:
        stats = cit.summarise_series(values, nw_lag=int(lag))
        grid[int(lag)] = stats
    return grid


def tier_grid(
    grid: Mapping[int, Mapping[str, Any]],
    *,
    effect: float,
    detected: bool | None,
    rules: Mapping[str, Any],
) -> dict[int, str]:
    """每档滞后下的分级。effect 固定用序列自身均值，只让 MDE/功效随滞后变。"""
    out: dict[int, str] = {}
    for lag, stats in grid.items():
        graded = cit.grade_quotability(
            effect=effect,
            mde=stats.get("mde"),
            detected=detected,
            power=stats.get("power"),
            rules=rules,
        )
        out[int(lag)] = graded["tier"]
    return out


def stability(tiers: Mapping[int, str]) -> dict[str, Any]:
    """是否在网格内保持不变。跨桶即不稳，结论对标准误口径敏感。"""
    distinct = sorted(set(tiers.values()))
    return {
        "stable": len(distinct) == 1,
        "tiers": dict(sorted(tiers.items())),
        "distinct": distinct,
        "primary_tier": tiers.get(PRIMARY_LAG),
    }


def decide(
    row: Mapping[str, Any],
    grid: Mapping[int, Mapping[str, Any]],
    *,
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    """单条：同源 → 网格稳定 → 解封；任一关不过就保持封印并写清原因。"""
    key = row["key"]
    same = check_same_source(row)
    detected = row.get("detected")
    effect = float((row.get("recomputed") or {})["mean"])
    tiers = tier_grid(grid, effect=effect, detected=detected, rules=rules)
    stab = stability(tiers)
    primary = grid.get(PRIMARY_LAG) or {}
    ratio = abs(effect) / float(primary["mde"]) if primary.get("mde") else None

    if not same["ok"]:
        if not same["mean_match"]:
            klass = "B_different_budget"
        elif same["t_match"] is None and same.get("stat_n") is None:
            # summary 连观测数都没给，t 也对不上任何口径：不是「换个滞后」，是没有可核对的分母
            klass = "B_missing_stat"
        elif same["t_match"] is None and not same["n_match"]:
            klass = "B_regression_coef"
        else:
            klass = "B_different_source"
        return {
            "key": key,
            "status": "STAY_SEALED",
            "class": klass,
            "tier": "NOT_GRADED",
            "same_source": same,
            "ratio_primary": ratio,
            "stability": stab,
            "reasons": same["reasons"],
        }
    if not stab["stable"]:
        return {
            "key": key,
            "status": "STAY_SEALED",
            "class": "A_but_unstable",
            "tier": "NOT_GRADED",
            "same_source": same,
            "ratio_primary": ratio,
            "stability": stab,
            "reasons": [
                f"同源检查通过，但分级随 NW 滞后跨桶：{stab['tiers']}；"
                "结论对标准误口径敏感，不解封"
            ],
        }
    return {
        "key": key,
        "status": "UNSEALED",
        "class": "A_same_source",
        "tier": stab["primary_tier"],
        "same_source": same,
        "ratio_primary": ratio,
        "stability": stab,
        "reasons": [
            f"判定量与序列同源（t 口径 {same['t_match']}），"
            f"分级在 L∈{sorted(tiers)} 全档恒为 {stab['primary_tier']}"
        ],
    }


def merge_project_tiers(
    rows: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把解封结果并回 L3v 的 27 条，给出「解封后全项目」分级与变化。"""
    by_key = {d["key"]: d for d in decisions}
    counts_before: dict[str, int] = {}
    counts_after: dict[str, int] = {}
    changed: list[dict[str, Any]] = []
    for row in rows:
        key = row["key"]
        before = (row.get("grade") or {}).get("tier")
        counts_before[before] = counts_before.get(before, 0) + 1
        decision = by_key.get(key)
        after = decision["tier"] if decision and decision["status"] == "UNSEALED" else before
        counts_after[after] = counts_after.get(after, 0) + 1
        if after != before:
            changed.append({"key": key, "before": before, "after": after})
    return {
        "counts_before": dict(sorted(counts_before.items())),
        "counts_after": dict(sorted(counts_after.items())),
        "changed": changed,
        "n_changed": len(changed),
        "quotable_before": counts_before.get("QUOTABLE", 0),
        "quotable_after": counts_after.get("QUOTABLE", 0),
    }
