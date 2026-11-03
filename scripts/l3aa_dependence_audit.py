#!/usr/bin/env python
"""L3aa：多重检验的依赖结构 —— 任意依赖下的 BY 校正与家族重叠诊断。

承接 L3z 审计报告第 8 节留下的缺口：「BH 的依赖结构：未验证」。
本阶段不去证明 PRDS，而是**放弃它**：换成 Benjamini–Yekutieli，
它在任意依赖结构下都控制 FDR，代价是门槛收紧 H_m 倍（m=24 时 3.776 倍）。

同时把「同期、同池、同族」从说法变成可量的数：重新加载 20 条参与评分的
逐月序列（带月份索引），两两算窗口重叠率与 Pearson ρ，并给出 Li-Ji 有效检验数。

刻意不做的事：
- 不新增因子、不新增回测、不改 α / m / 滞后 / 分族；
- 不用两两 ρ 宣称 PRDS 已验证（PRDS 是高维联合性质，两两相关证明不了）；
- 不改 ``multiplicity.apply_corrections`` 的返回结构（L3z 产物已进 sha 链）。

用法：
    PYTHONPATH=backend/src python scripts/l3aa_dependence_audit.py
    PYTHONPATH=backend/src python scripts/l3aa_dependence_audit.py --quiet
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

from deepquant.gates import citation as ct
from deepquant.gates import dependence as dp
from deepquant.gates import multiplicity as mp

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE"
REPORT = ROOT / "reports" / "L3AA_ARBITRARY_DEPENDENCE_AUDIT.md"

PREREG = ROOT / "docs" / "gates" / "l3aa_prereg.json"
PREREG_SHA = "d189847274a4e795bd46c885fe2519be64c93ae1ea0ca2413f0a389303d143ea"

L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"
L3U_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
L3Z_RESULT = ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"
LAG_SENS = ROOT / "reports" / "L3Z_LAG_SENSITIVITY.json"

FROZEN = {
    "l3u_prereg": (
        L3U_PREREG,
        "0bbdc5ff4ad80f1de528f48544ee49094bc43a3a5fed19759e6fd878915ef493",
    ),
    "l3u_result": (
        L3U_RESULT,
        "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b",
    ),
    "claims_cards": (
        CARDS,
        "5b4173c7265e5dd5be0cbf37b9af8620ed3488ea3480224d249a91740b1317b0",
    ),
    "l3z_result": (
        L3Z_RESULT,
        "a138f8803ea45ca0717bc6bd6f53a654096f5dfde84200f42f2db17d1c3b3edc",
    ),
    "lag_sensitivity": (
        LAG_SENS,
        "b60428758948e261bdd4ee9b26165376ac9a4810e5c888780c1fba56a7f1a4f6",
    ),
}

#: 滞后网格，与 L3u / L3z 完全一致。主口径仍是 NW(3)。
LAGS = (0, 1, 2, 3, 4, 6)
MAIN_LAG = 3

#: 依赖诊断至少需要多少个月的重叠才报 ρ。
MIN_OVERLAP = 24

#: 有效检验数的子集门槛：太长/太短的窗口混进来会让相关矩阵残缺。
M_EFF_MIN_N = 200
M_EFF_MIN_OVERLAP = 150


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fill_lag_sha() -> str:
    """滞后敏感性产物的 sha 在预登记之后才算出，这里固化一次。"""
    return _sha(LAG_SENS) if LAG_SENS.exists() else ""


def _check_frozen() -> dict[str, str]:
    if not PREREG.exists():
        raise SystemExit(f"预登记不存在：{PREREG}")
    got = _sha(PREREG)
    if got != PREREG_SHA:
        raise SystemExit(
            f"预登记已被改动：期望 {PREREG_SHA[:12]}…，实际 {got[:12]}…；"
            "拒绝运行。若确需修订，先写 amendments 并更新脚本里的 sha。"
        )
    problems = []
    shas = {"prereg": got}
    for key, (path, expected) in FROZEN.items():
        if not path.exists():
            problems.append(f"{key}: 不存在 {path.relative_to(ROOT)}")
            continue
        digest = _sha(path)
        shas[key] = digest
        if expected and digest != expected:
            problems.append(f"{key}: 期望 {expected[:12]}…，实际 {digest[:12]}…")
    if problems:
        raise SystemExit("冻结输入已被改动，拒绝运行：\n  " + "\n  ".join(problems))
    return shas


# --------------------------------------------------------------------------
# 计算原语（拆成小函数，供变异测试替换）
# --------------------------------------------------------------------------


def by_reject(pvals: list[float], m: int, q: float = mp.Q) -> list[bool]:
    """BY 的拒绝判定。变异测试会替换它来看检查器是否真的会翻。"""
    return mp.benjamini_yekutieli(pvals, q=q, m=m)


def overlap_ratio(a: dp.MonthSeries, b: dp.MonthSeries) -> float:
    """窗口重叠率：交集月份 / **较短**窗口。"""
    return dp.overlap(a, b)["ratio"]


def pair_rho(a: dp.MonthSeries, b: dp.MonthSeries) -> dict:
    """一对序列的相关性，取三个偏移下 |ρ| 的最大值。"""
    return dp.pairwise_dependence(a, b, shifts=dp.DEFAULT_SHIFTS, min_overlap=MIN_OVERLAP)


def m_eff_calc(corr: list[list[float]]) -> dict:
    """有效检验数。变异测试会替换它，看闸门是否真的会响。"""
    return dp.li_ji_m_eff(corr)


def _effective_m(m: int, n_scored: int) -> int:
    """名义 m 与实际参与评分的条数取大：只计名额的条目照样占预算。"""
    return max(int(m), n_scored)


def _run_by(rows: list[dict], *, m: int, q: float = mp.Q) -> dict:
    """按给定 m 跑一次 BY，并保留 L3z 的三类集合口径。"""
    keys: list[str] = []
    pvals: list[float] = []
    for row in rows:
        if row.get("_counts_only"):
            continue
        rc = row.get("recomputed") or {}
        t, n = rc.get("t_nw3"), rc.get("n")
        if t is None or n is None:
            continue
        keys.append(row["key"])
        pvals.append(mp.t_two_sided_p(t, max(int(n) - 1, 1)))
    n_scored = len(pvals)
    m_eff = _effective_m(m, n_scored)
    padded = list(pvals) + [1.0] * (m_eff - n_scored)
    flags = by_reject(padded, m_eff, q)[:n_scored]
    # 同一批 p 值上的 BH：用于核对「BY ⊆ BH」这条数学必然性。
    flags_bh = mp.bh(padded, q=q, m=m_eff)[:n_scored]
    return {
        "m": m_eff,
        "q": q,
        "n_scored": n_scored,
        "harmonic": mp.harmonic(m_eff),
        "unit_threshold": q / (m_eff * mp.harmonic(m_eff)),
        "p_threshold": mp.by_threshold(pvals, q=q, m=m_eff) if pvals else None,
        "rejected": sorted(k for k, f in zip(keys, flags, strict=False) if f),
        "rejected_bh": sorted(k for k, f in zip(keys, flags_bh, strict=False) if f),
        "items": [
            {"key": k, "p": p, "by": bool(f), "bh": bool(fb)}
            for k, p, f, fb in zip(keys, pvals, flags, flags_bh, strict=False)
        ],
    }


def _lag_rows(scored: list[dict], lag_data: dict, lag: int) -> list[dict]:
    """把 NW 滞后 grid 里的 mean / se_nw 折算回「t + n」的行结构。"""
    by_key = {r["key"]: r for r in lag_data["rows"]}
    out = []
    for row in scored:
        if row.get("_counts_only"):
            out.append({**row})
            continue
        src = by_key.get(row["key"])
        if src is None:
            raise SystemExit(f"滞后敏感性产物缺少 {row['key']}，拒绝用不完整网格出结论")
        cell = src["per_lag"][str(lag)]
        se = cell["se_nw"]
        if not se or se <= 0:
            raise SystemExit(f"[{row['key']}] lag={lag} 的 se_nw={se} 非法")
        out.append(
            {
                "key": row["key"],
                "recomputed": {
                    "mean": src["mean"],
                    "t_nw3": src["mean"] / se,
                    "n": src["n"],
                },
            }
        )
    return out


# --------------------------------------------------------------------------
# 主评估
# --------------------------------------------------------------------------


def evaluate(inputs: dict) -> dict:
    """从冻结输入算出全部结果。不做 I/O，变异测试可以反复调用。"""
    l3u = inputs["l3u"]
    l3z = inputs["l3z"]
    cards = inputs["cards"]
    lag_data = inputs["lag"]
    entries = inputs["entries"]

    scored = mp.eligible(l3u["rows"])
    m_indep = mp.count_independent_tests(scored)
    n_counts_only = sum(1 for r in scored if r.get("_counts_only"))

    l3z_main = l3z["runs"]["m_independent"]
    sets = {
        "bonferroni": sorted(i["key"] for i in l3z_main["items"] if i["bonferroni"]),
        "holm": sorted(i["key"] for i in l3z_main["items"] if i["holm"]),
        "bh": sorted(i["key"] for i in l3z_main["items"] if i["bh"]),
    }

    by24 = _run_by(scored, m=m_indep)
    by27 = _run_by(scored, m=l3z["m_all"])

    # 滞后网格下的 BY（主口径仍是 L=3，本表只作稳健性）
    by_lag = {}
    for lag in LAGS:
        run = _run_by(_lag_rows(scored, lag_data, lag), m=m_indep)
        by_lag[str(lag)] = {
            "n_rejected": len(run["rejected"]),
            "n_rejected_bh": len(run["rejected_bh"]),
            "rejected": run["rejected"],
            "rejected_bh": run["rejected_bh"],
            "p_threshold": run["p_threshold"],
        }
    # 全档交集 / 并集：用来回答「哪几条在所有滞后下都过 BY」
    lag_sets = [set(by_lag[str(l)]["rejected"]) for l in LAGS]
    lag_intersection = sorted(set.intersection(*lag_sets)) if lag_sets else []
    lag_union = sorted(set.union(*lag_sets)) if lag_sets else []
    lag_varying = sorted(
        {k for k in lag_union if any(k in s for s in lag_sets) and not all(k in s for s in lag_sets)}
    )

    # ---- 依赖诊断 ----
    scored_keys = [r["key"] for r in scored if not r.get("_counts_only")]
    entry_by_key = {e["key"]: e for e in entries}
    series: dict[str, dp.MonthSeries] = {}
    undatable: list[dict] = []
    for key in scored_keys:
        entry = entry_by_key.get(key)
        if entry is None:
            raise SystemExit(f"L3u 预登记里找不到 {key}")
        try:
            series[key] = dp.load_entry(ROOT, entry)
        except dp.DependenceError as exc:
            undatable.append({"key": key, "error": type(exc).__name__, "detail": str(exc)})

    keys = [k for k in scored_keys if k in series]
    pairs: list[dict] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            info = pair_rho(series[a], series[b])
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "n_a": series[a].n,
                    "n_b": series[b].n,
                    "n_overlap": info["n_overlap"],
                    "overlap_ratio": overlap_ratio(series[a], series[b]),
                    "max_abs_rho": info["max_abs_rho"],
                    "max_abs_rho_shift": info["max_abs_rho_shift"],
                    "rho_shift0": (info["shift0"] or {}).get("rho"),
                    "alignment_warning": info["alignment_warning"],
                }
            )

    ratios = [p["overlap_ratio"] for p in pairs]
    rhos = [abs(p["max_abs_rho"]) for p in pairs if p["max_abs_rho"] is not None]
    rhos0 = [abs(p["rho_shift0"]) for p in pairs if p["rho_shift0"] is not None]

    # 疑似同源：|ρ| ≥ 0.9999。单单「高相关」不足以下结论，还要看数值是否逐位相同——
    # 一条序列恰好是另一条的子窗口，和「两条高度相关」是两件不同的事。
    near_dup: list[dict] = []
    for p in pairs:
        rho = p["max_abs_rho"]
        if rho is None or abs(rho) < 0.9999:
            continue
        a, b = series[p["a"]], series[p["b"]]
        common = sorted(set(a.values) & set(b.values))
        max_diff = max((abs(a.values[m] - b.values[m]) for m in common), default=None)
        near_dup.append(
            {
                "a": p["a"],
                "b": p["b"],
                "rho": rho,
                "n_overlap": p["n_overlap"],
                "overlap_ratio": p["overlap_ratio"],
                "max_abs_diff": max_diff,
                "exact_subwindow": max_diff is not None and max_diff < 1e-12,
                "shorter_is_subset": p["overlap_ratio"] >= 0.999,
            }
        )

    subset, corr = dp.complete_submatrix(
        series, min_n=M_EFF_MIN_N, min_overlap=M_EFF_MIN_OVERLAP
    )
    m_eff = m_eff_calc(corr) if len(subset) >= 2 else None

    return {
        "input_shas": inputs["shas"],
        "m_independent": m_indep,
        "m_all": l3z["m_all"],
        "n_counts_only": n_counts_only,
        "n_scored_current": by24["n_scored"],
        "l3z_sets": sets,
        "l3z_sensitivity": {
            "alpha": l3z.get("sensitivity", {}).get("alpha", {}),
            "q": l3z.get("sensitivity", {}).get("q", {}),
        },
        "by_m24": by24,
        "by_m27": by27,
        "by_lag": by_lag,
        "lag_intersection": lag_intersection,
        "lag_union": lag_union,
        "lag_varying": lag_varying,
        "dependence": {
            "n_series": len(series),
            "n_pairs": len(pairs),
            "n_pairs_with_rho": len(rhos),
            "undatable": undatable,
            "median_overlap_ratio": statistics.median(ratios) if ratios else None,
            "min_overlap_ratio": min(ratios) if ratios else None,
            "n_zero_overlap": sum(1 for r in ratios if r <= 0.0),
            "median_abs_rho": statistics.median(rhos) if rhos else None,
            "median_abs_rho_shift0": statistics.median(rhos0) if rhos0 else None,
            "max_abs_rho": max(rhos) if rhos else None,
            "n_abs_rho_gt_0_5": sum(1 for r in rhos if r > 0.5),
            "n_alignment_warning": sum(1 for p in pairs if p["alignment_warning"]),
            "n_shift_helps": sum(
                1
                for p in pairs
                if p["max_abs_rho_shift"] not in (0, None)
                and p["rho_shift0"] is not None
                and abs(p["max_abs_rho"]) - abs(p["rho_shift0"]) > 0.10
            ),
            "near_duplicates": near_dup,
            "n_exact_subwindow": sum(1 for p in near_dup if p["exact_subwindow"]),
            "pairs": pairs,
        },
        "m_eff": {
            "keys": subset,
            "m_subset": len(subset),
            **(m_eff or {}),
        },
        "cards": {c["key"]: c.get("quotability") for c in cards["claims"]},
    }


# --------------------------------------------------------------------------
# 闸门与预测
# --------------------------------------------------------------------------


def check_gates(res: dict) -> list[dict]:
    """仪器自检。任一条失败都说明实现有问题，不能拿去报结论。"""
    out: list[dict] = []
    by = set(res["by_m24"]["rejected"])
    bh = set(res["l3z_sets"]["bh"])

    if not by <= bh:
        out.append(
            {
                "code": "BY_NOT_SUBSET_OF_BH",
                "detail": f"BY 拒绝集合不是 BH 的子集，多出 {sorted(by - bh)}",
            }
        )
    # 每一档滞后、每一种 m 都必须满足 BY ⊆ BH（H_m ≥ 1 的数学必然）
    for label, run in (("m27", res["by_m27"]), *_lag_runs(res)):
        over = set(run["rejected"]) - set(run["rejected_bh"])
        if over:
            out.append(
                {
                    "code": "BY_NOT_SUBSET_OF_BH",
                    "detail": f"{label}：BY 比同批 p 值上的 BH 多拒绝 {sorted(over)}",
                }
            )

    h = mp.harmonic(24)
    # G2 拆成两件事，避免一个闸门同时管两个口径：
    # (a) 第 1 档门槛必须等于 q/(m_used·H_m_used) —— 与自身 m 自洽；
    # (b) H_24 必须等于已知值 —— 调和数本身没算错。
    want = 0.05 / (res["by_m24"]["m"] * mp.harmonic(res["by_m24"]["m"]))
    if abs(res["by_m24"]["unit_threshold"] - want) > 1e-15:
        out.append(
            {
                "code": "BY_THRESHOLD_FORMULA",
                "detail": (
                    f"门槛 {res['by_m24']['unit_threshold']!r} ≠ "
                    f"q/(m·H_m)={want!r}（m={res['by_m24']['m']}）"
                ),
            }
        )
    if abs(h - 3.7759581777535067) > 1e-12:
        out.append({"code": "HARMONIC_24", "detail": f"H_24={h!r}"})

    # m=1 退化：门槛就是 q 本身
    if not mp.benjamini_yekutieli([0.049], q=0.05, m=1)[0]:
        out.append({"code": "BY_M1_DEGENERATE", "detail": "m=1 时 p=0.049 应被拒绝"})
    if mp.benjamini_yekutieli([0.051], q=0.05, m=1)[0]:
        out.append({"code": "BY_M1_DEGENERATE", "detail": "m=1 时 p=0.051 不应被拒绝"})

    # 全零效应：任何校正都不许放行
    if any(mp.benjamini_yekutieli([1.0] * 20, q=0.05, m=24)):
        out.append({"code": "BY_ZERO_EFFECT", "detail": "全 p=1 仍放行，门槛算错"})

    # m 语义：只计名额的条目必须照占 m
    if res["by_m24"]["m"] != res["m_independent"]:
        out.append(
            {
                "code": "BY_M_MISMATCH",
                "detail": f"BY 用的 m={res['by_m24']['m']} ≠ L3z 的 {res['m_independent']}",
            }
        )
    if res["by_m24"]["n_scored"] != res["n_scored_current"]:
        out.append({"code": "BY_N_SCORED", "detail": "参与评分条数与 L3z 不一致"})
    if res["n_counts_only"] != 7:
        out.append(
            {
                "code": "COUNTS_ONLY_CHANGED",
                "detail": f"只计名额的条目从 7 变成 {res['n_counts_only']}",
            }
        )

    # q 单调
    prev: set[str] | None = None
    for q in (0.01, 0.025, 0.05, 0.10, 0.20):
        run = _run_by_simple(res, q)
        cur = set(run)
        if prev is not None and not prev <= cur:
            out.append(
                {
                    "code": "BY_NON_MONOTONE_IN_Q",
                    "detail": f"q 放宽到 {q} 时拒绝集合反而少了 {sorted(prev - cur)}",
                }
            )
        prev = cur

    # 重叠率的定义性不变量：交集等于较短窗口 ⇔ 较短窗口被完全包含 ⇒ 必须是 1。
    # 分母若写成 max(n) 就会在这里露出来。
    for p in res["dependence"]["pairs"]:
        r = p["overlap_ratio"]
        if r < -1e-12 or r > 1 + 1e-12:
            out.append(
                {
                    "code": "OVERLAP_RATIO_OUT_OF_RANGE",
                    "detail": f"{p['a']}~{p['b']} ratio={r}",
                }
            )
            break
        if p["n_overlap"] == min(p["n_a"], p["n_b"]) and abs(r - 1.0) > 1e-12:
            out.append(
                {
                    "code": "OVERLAP_RATIO_NOT_ONE_ON_SUBSET",
                    "detail": (
                        f"{p['a']}~{p['b']}：交集 {p['n_overlap']} 等于较短窗口 "
                        f"{min(p['n_a'], p['n_b'])}，重叠率却为 {r}"
                    ),
                }
            )
            break

    # m_eff 的两条性质：必须在 [1, m] 内；平均相关明显时必然小于 m。
    me = res["m_eff"]
    if me.get("m_eff"):
        if not (1.0 - 1e-9 <= me["m_eff"] <= me["m"] + 1e-9):
            out.append(
                {
                    "code": "M_EFF_OUT_OF_RANGE",
                    "detail": f"m_eff={me['m_eff']} 不在 [1, {me['m']}] 内",
                }
            )
        elif me.get("mean_abs_offdiag", 0.0) > 0.10 and me["m_eff"] >= me["m"] - 1e-9:
            out.append(
                {
                    "code": "M_EFF_NOT_REDUCED",
                    "detail": (
                        f"平均 |ρ|={me['mean_abs_offdiag']:.4f} 却没有压低有效检验数"
                        f"（m_eff={me['m_eff']}，m={me['m']}）"
                    ),
                }
            )
    return out


def _lag_runs(res: dict) -> list[tuple[str, dict]]:
    """把滞后网格摊成 (标签, run) 列表，供闸门遍历。"""
    return [(f"lag={lag}", res["by_lag"][str(lag)]) for lag in LAGS]


def _run_by_simple(res: dict, q: float) -> list[str]:
    """在已算好的 items 上重跑 BY，只换 q（用于单调性检查）。"""
    items = res["by_m24"]["items"]
    pvals = [i["p"] for i in items] + [1.0] * (res["by_m24"]["m"] - len(items))
    flags = by_reject(pvals, res["by_m24"]["m"], q)[: len(items)]
    return sorted(i["key"] for i, f in zip(items, flags, strict=False) if f)


def check_predictions(res: dict) -> list[dict]:
    """逐条核对预登记里的预测。被反驳就如实记下，不改判据。"""
    by = set(res["by_m24"]["rejected"])
    bonf = set(res["l3z_sets"]["bonferroni"])
    bh = set(res["l3z_sets"]["bh"])
    dep = res["dependence"]

    preds: list[dict] = []

    ok = len(by) == 6 and by == bonf
    preds.append(
        {
            "id": "P1",
            "timing": "priori",
            "statement": "BY(m=24) 拒绝 6 条，且集合与 Bonferroni 相同",
            "observed": f"BY {len(by)} 条；与 Bonferroni({len(bonf)}) 集合相同={by == bonf}",
            "refuted": not ok,
        }
    )

    diff = sorted(bh - by)
    preds.append(
        {
            "id": "P2",
            "timing": "priori",
            "statement": "BY 比 BH 少 1 条，差集恰为 {phasep_late}",
            "observed": f"BH {len(bh)} → BY {len(by)}；差集 {diff}",
            "refuted": diff != ["phasep_late"],
        }
    )

    n27 = len(res["by_m27"]["rejected"])
    # m=27 的 Bonferroni 通过数：p ≤ 0.05/27
    thr = 0.05 / res["m_all"]
    n27b = sum(1 for i in res["by_m27"]["items"] if i["p"] <= thr)
    preds.append(
        {
            "id": "P3",
            "timing": "priori",
            "statement": "m=27 时 BY 仍拒绝 6 条，Bonferroni 只拒绝 5 条",
            "observed": f"BY@27 {n27} 条；Bonferroni@27 {n27b} 条",
            "refuted": not (n27 == 6 and n27b == 5),
        }
    )

    lag_counts = {lag: res["by_lag"][str(lag)]["n_rejected"] for lag in LAGS}
    preds.append(
        {
            "id": "P4",
            "timing": "priori",
            "statement": "NW 滞后 {0,1,2,3,4,6} 六档下 BY 通过数恒为 6",
            "observed": f"{lag_counts}",
            "refuted": any(v != 6 for v in lag_counts.values()),
        }
    )

    med_ov = dep["median_overlap_ratio"]
    preds.append(
        {
            "id": "P5",
            "timing": "priori",
            "statement": "两两月度重叠率中位数 ≥ 0.90",
            "observed": f"中位数 {med_ov:.4f}（{dep['n_pairs']} 对，零重叠 {dep['n_zero_overlap']} 对）"
            if med_ov is not None
            else "无有效对",
            "refuted": med_ov is None or med_ov < 0.90,
        }
    )

    med_rho = dep["median_abs_rho"]
    preds.append(
        {
            "id": "P6",
            "timing": "priori",
            "statement": "两两 |ρ| 中位数 ≤ 0.50",
            "observed": f"中位数 {med_rho:.4f}（有效对 {dep['n_pairs_with_rho']}，"
            f"|ρ|>0.5 的 {dep['n_abs_rho_gt_0_5']} 对）"
            if med_rho is not None
            else "无有效对",
            "refuted": med_rho is None or med_rho > 0.50,
        }
    )

    preds.append(
        {
            "id": "P7",
            "timing": "descriptive",
            "statement": "m_eff（Li-Ji）只报告，不预测",
            "observed": (
                f"子集 {res['m_eff']['m_subset']} 条，m_eff={res['m_eff'].get('m_eff'):.3f}"
                if res["m_eff"].get("m_eff")
                else "子集不足 2 条，未计算"
            ),
            "refuted": False,
        }
    )
    return preds


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def _fmt_set(keys) -> str:
    return "、".join(f"`{k}`" for k in keys) if keys else "—"


def render(res: dict, gates: list[dict], preds: list[dict], elapsed: float) -> str:
    by = res["by_m24"]
    dep = res["dependence"]
    lines: list[str] = []
    a = lines.append

    a("# 阶段 L3aa：任意依赖下的多重检验（BY）与家族重叠诊断")
    a("")
    a("> 本报告由 `scripts/l3aa_dependence_audit.py` 生成。本阶段**没有**新增因子、")
    a("> **没有**新增回测、**没有**启动实盘；BY 只会比 BH 更严格，不存在「为提高通过数而换口径」的可能。")
    a("")
    a("## 1. 这一阶段补的是哪个缺口")
    a("")
    a("L3z 审计报告第 8 节列了六项未解决缺口，其中「BH 的依赖结构」是**唯一不需要新数据**就能动的一项。")
    a("具体问题是：BH 的正确性依赖检验之间的正回归相依（PRDS）或独立性，而本项目的家族是")
    a("「同一份月度面板上反复切池、切窗口、切成本口径」，这个前提从没被检验过。")
    a("")
    a("本阶段的处理不是去证明 PRDS，而是**放弃它**——换成 Benjamini–Yekutieli：")
    a("")
    a("```")
    a("BH:  p_(k) ≤ k·q / m")
    a(f"BY:  p_(k) ≤ k·q / (m·H_m)      H_24 = {by['harmonic']:.6f}")
    a("```")
    a("")
    a("BY 在**任意依赖结构**下控制 FDR（Benjamini & Yekutieli, 2001），代价是门槛收紧 H_m 倍。")
    a("方向是单向的：H_m ≥ 1 ⇒ BY 的每个门槛都不高于 BH ⇒ **BY 拒绝集合必然是 BH 的子集**。")
    a("")
    a("## 2. 主口径 NW(3)：BY 拒绝 6 条，与 Bonferroni 相同")
    a("")
    a(f"主口径 m={by['m']}（与 L3z 相同）、q={by['q']}、NW({MAIN_LAG})。")
    a("")
    a("| 口径 | 拒绝数 | 拒绝集合 |")
    a("| --- | --- | --- |")
    a(f"| 单条 α=0.05 | {sum(1 for i in by['items'] if i['p'] <= 0.05)} | — |")
    a(f"| Bonferroni | {len(res['l3z_sets']['bonferroni'])} | {_fmt_set(res['l3z_sets']['bonferroni'])} |")
    a(f"| Holm | {len(res['l3z_sets']['holm'])} | {_fmt_set(res['l3z_sets']['holm'])} |")
    a(f"| BH（需 PRDS） | {len(res['l3z_sets']['bh'])} | {_fmt_set(res['l3z_sets']['bh'])} |")
    a(f"| **BY（任意依赖）** | **{len(by['rejected'])}** | {_fmt_set(by['rejected'])} |")
    a("")
    a("拒绝集合与 Bonferroni **完全相同**。也就是说：")
    a("")
    a("> 在**主口径 NW(3)** 下，对外可引的 6 条结论在最保守的、不依赖任何相依假设的口径下仍然成立。")
    a("")
    a("BY 比 BH 少的那 1 条是 `phasep_late`——它本来就因为「判定统计量与序列不同源」被标为 `NOT_GRADED`，")
    a("不进对外可引清单，所以这一条掉出 BY 不改变任何可引结论。")
    a("")
    a("### 2.0 必须紧跟的两条限定")
    a("")
    a(f"1. **「6 条」只对 NW({MAIN_LAG}) 成立。** 滞后网格上 BY 的通过数是 "
      + "、".join(f"L={lag}→{res['by_lag'][str(lag)]['n_rejected']}" for lag in LAGS)
      + "，会摆动。全档都在的只有 "
      + f"**{len(res['lag_intersection'])} 条**：{_fmt_set(res['lag_intersection'])}。")
    a("2. **BY 不验证 PRDS。** 它只是绕开这个假设；说「PRDS 已验证」仍然是错的（见第 5.2 节）。")
    a("")
    a("### 2.1 逐条对照")
    a("")
    a("| 结论 | p | BH（需 PRDS） | BY（任意依赖） | 定稿分级 |")
    a("| --- | --- | --- | --- | --- |")
    for item in sorted(by["items"], key=lambda x: x["p"]):
        tier = res["cards"].get(item["key"], "UNKNOWN")
        a(
            f"| `{item['key']}` | {item['p']:.6f} | {'通过' if item['key'] in res['l3z_sets']['bh'] else '—'} "
            f"| {'**通过**' if item['by'] else '—'} | {tier} |"
        )
    a("")
    a("## 3. m 口径敏感性")
    a("")
    a("| m 口径 | BY 拒绝 | Bonferroni 拒绝 | BY 第 1 档门槛 |")
    a("| --- | --- | --- | --- |")
    thr27 = 0.05 / res["m_all"]
    n27b = sum(1 for i in res["by_m27"]["items"] if i["p"] <= thr27)
    a(
        f"| {by['m']}（数值指纹去重，主口径） | {len(by['rejected'])} | "
        f"{len(res['l3z_sets']['bonferroni'])} | {by['unit_threshold']:.8f} |"
    )
    a(
        f"| {res['m_all']}（登记条目数） | {len(res['by_m27']['rejected'])} | {n27b} "
        f"| {res['by_m27']['unit_threshold']:.8f} |"
    )
    a("")
    a("值得记一笔：**BY 在两种 m 下都给 6 条，Bonferroni 从 6 掉到 5。**")
    a("原因是 BY 是 step-up，第 k 档门槛按 k 线性放宽，补偿了 H_m 的收紧；")
    a("而 Bonferroni 的门槛与 k 无关。所以 BY 对「m 取 24 还是 27」这个有争议的口径更不敏感——")
    a("这对一份要发出去接受审查的报告是好事。")
    a("")
    a("## 4. NW 滞后敏感性")
    a("")
    a("| 滞后 | " + " | ".join(f"L={lag}" for lag in LAGS) + " |")
    a("| --- | " + " | ".join("---" for _ in LAGS) + " |")
    a(
        "| BY 拒绝数 | "
        + " | ".join(str(res["by_lag"][str(lag)]["n_rejected"]) for lag in LAGS)
        + " |"
    )
    a(
        "| 同批 p 值上的 BH | "
        + " | ".join(str(res["by_lag"][str(lag)]["n_rejected_bh"]) for lag in LAGS)
        + " |"
    )
    a("")
    a(f"主口径仍是 NW({MAIN_LAG})，不因为任何一档更有利而替换。")
    a("")
    a("### 4.1 为什么这里会摆动（预登记 P4 因此被反驳）")
    a("")
    a("预登记写的是「六档下 BY 通过数恒为 6」。实测不是：")
    a("")
    a(f"- **全档都在的 {len(res['lag_intersection'])} 条**：{_fmt_set(res['lag_intersection'])}")
    if res["lag_varying"]:
        a(f"- **会摆动的那几条**：{_fmt_set(res['lag_varying'])}")
    a("")
    a("原因不难理解：L3u 当年测的是「**分级**是否跨档」（20 条 × 6 档只有 1 条跨档），")
    a("而 BY 是一个比分级严格得多的**判定**。分级只看效应/MDE 的比值落在哪个区间，")
    a("就算 p 从 0.002 变到 0.003 也不会跨档；BY 却恰好在 p 的绝对值上卡门。")
    a("所以「分级稳」推不出「BY 稳」——这两件事我原先混为一谈了。")
    a("")
    a("`phasel_alpha` 是最典型的：它的 p 在 L=0/1 时越过 BY 第 6 档门槛、在 L≥2 时回落到门槛内。")
    a("它在 L3u/L3w 里是 `NULL_CONFIRMED`（确认无效应），靠的是「效应/MDE」而不是 p 的绝对值，")
    a("所以分级从没动过，但 BY 会动。")
    a("")
    a("把这件事按分级摊开，说法可以更精确：")
    a("")
    a("> **5 条正面结论（1 条 `QUOTABLE` + 4 条 `QUOTABLE_MARGINAL`）在六档滞后下全部通过 BY；")
    a("> 会摆动的第 6 条 `phasel_alpha` 是「确认无效应」那条，`phasep_late` 本来就是 `NOT_GRADED`。**")
    a("")
    a("也就是说：**没有任何一条正面结论的 BY 通过性依赖滞后选择**。")
    a("")
    a("## 5. 依赖诊断：这些检验到底有多同步")
    a("")
    a(f"参与诊断的有 {dep['n_series']} 条逐月序列——20 条参与评分的条目全部能定到月份，")
    a(f"两两 {dep['n_pairs']} 对。")
    a("")
    a("| 指标 | 值 |")
    a("| --- | --- |")
    a(f"| 重叠率中位数（交集/较短窗口） | {dep['median_overlap_ratio']:.4f} |")
    a(f"| 零重叠对数 | {dep['n_zero_overlap']} |")
    a(f"| \\|ρ\\| 中位数（三偏移取最大） | {dep['median_abs_rho']:.4f} |")
    a(f"| \\|ρ\\| 中位数（仅 lag 0） | {dep['median_abs_rho_shift0']:.4f} |")
    a(f"| \\|ρ\\|>0.5 的对数 | {dep['n_abs_rho_gt_0_5']} / {dep['n_pairs_with_rho']} |")
    a(f"| 最大 \\|ρ\\| | {dep['max_abs_rho']:.4f} |")
    a(f"| 日期口径不一致的对数 | {dep['n_alignment_warning']} |")
    a(f"| 平移到 ±1 月后 \\|ρ\\| 提升 >0.10 的对数 | {dep['n_shift_helps']} |")
    a("")
    a("### 5.1 为什么主口径取「三偏移的 |ρ| 最大值」")
    a("")
    a("各阶段的月度文件日期列并不统一：一部分有 `month`（持有月），一部分只有 `signal_date`（信号月），")
    a("两者天然差 1 个月。若按单一偏移对齐，会人为压低相关性。**为取最大 |ρ|** 是对")
    a("「这些检验同步性不强」这一方向**最不利**的选择——如果这样算出来还是不高，")
    a("那「同步性不强」就不可能是口径错位造出来的假象。")
    a("")
    a("### 5.2 这一段不能证明什么")
    a("")
    a("**两两 Pearson ρ 既不能证明也不能否证 PRDS。** PRDS 是关于联合分布的高维性质，")
    a("而 ρ 只是二阶矩。所以本节的作用只有两个：")
    a("")
    a("1. 让读者自己判断「这批检验大致有多同步」；")
    a("2. 给 BY 提供一个参照——但 BY 的结论不依赖这里任何一个数字。")
    a("")
    a("### 5.3 有效检验数（描述性）")
    a("")
    if res["m_eff"].get("m_eff"):
        me = res["m_eff"]
        a(f"Li-Ji 指标：在**相关矩阵完整**的 {me['m_subset']} 条子集上，")
        a(f"m_eff = **{me['m_eff']:.3f}**（m={me['m']}，平均非对角 ρ={me['mean_offdiag']:.4f}，")
        a(f"λ 范围 [{me['lambda_min']:.3f}, {me['lambda_max']:.3f}]）。")
        a("")
        a(f"子集：{_fmt_set(me['keys'])}")
        a("")
        a("⚠️ 这是**子集**上的量，不是全部 24 条；而且它只是「平均相关强度」的换算，")
        a("不是 FDR 控制成立的依据。列出来是为了说明这批检验的独立性远低于名义条数，")
        a("**不用来替代 BY**。")
    else:
        a("相关矩阵完整子集不足 2 条，未计算。")
    a("")
    a("### 5.4 无法定日期的条目")
    a("")
    if dep["undatable"]:
        for item in dep["undatable"]:
            a(f"- `{item['key']}`：{item['error']} —— {item['detail']}")
        a("")
        a("这些条目不计入依赖诊断。")
    else:
        a("无。20 条参与评分的序列全部能定到月份。")
        a("")
        a("（`phasem_spread` 的月度文件只有整数行号、没有任何日期列，"
          "但它在 L3u 预登记里就是「判定不由单一均值给出」的条目，")
        a("不参与评分，也不在依赖诊断范围内。）")
    a("")
    if dep["near_duplicates"]:
        a("### 5.5 顺手查出来的一件事：有一条是另一条的精确子窗口")
        a("")
        a("把 |ρ| ≥ 0.9999 的对拉出来，再逐月比对数值：")
        a("")
        a("| a | b | ρ | 共同月 | 较短窗口是子集 | max\\|逐月差\\| | 逐位相同 |")
        a("| --- | --- | --- | --- | --- | --- | --- |")
        for p in dep["near_duplicates"]:
            diff = "—" if p["max_abs_diff"] is None else f"{p['max_abs_diff']:.3e}"
            a(
                f"| `{p['a']}` | `{p['b']}` | {p['rho']:.6f} | {p['n_overlap']} "
                f"| {'是' if p['shorter_is_subset'] else '否'} | {diff} "
                f"| {'**是**' if p['exact_subwindow'] else '否'} |"
            )
        a("")
        a(
            f"其中有 **{dep['n_exact_subwindow']} 对逐位相同**，但两者的性质不一样："
        )
        a("")
        a(
            "- `phases_net` 与 `phaset_net` 逐位相同 —— 这一对 **L3v 已经登记过**"
            "（27 条只对应 24 份独立数字，其中一组就是它们），不算新发现；"
        )
        a(
            "- `phaseo_netBASE` 与 `phasep_late` 逐位相同 —— **这一对是新的**。"
            "`phasep_late` 的口径就是「`phaseo` 的 BASE@3000万 取 late 段」，"
            "所以它在 129 个共同月上与 `phaseo_netBASE` 是同一批数字，"
            "不是「高度相关」，是**同一条序列的截取**。"
        )
        a("")
        a("这件事 L3v 的数值指纹去重抓不到：指纹是 `(mean, t, n)`，"
          "两条序列的 n 不同（237 vs 129），指纹自然不同，于是被算成两次独立检验。")
        a("**所以 m=24 其实偏大。**")
        a("")
        a("### 5.6 明知 m 偏大，为什么不去把它改小")
        a("")
        a("把 m 改小会让每个门槛都变松，只会让**更多**条目通过。这正好是"
        "「为提高通过数而调 m」的样子，也就是预登记 `anti_goals` 明令禁止的动作。")
        a("")
        a("所以本阶段只把这个发现记下来，**不重跑 m=23**：")
        a("")
        a("- 现在的 6 条是在偏严的 m 下得到的，属于**保守结论**；")
        a("- 真门槛比这更松，所以不存在「因为 m 偏大而漏掉真结论」的风险需要靠调参去补；")
        a("- 如果要处理，正确做法是**下一次预登记**里把「同源子窗口」定义进去重规则，而不是这一次就地放宽。")
        a("")
    a("## 6. 预测核对")
    a("")
    a("| # | 预测 | 实际 | 结论 |")
    a("| --- | --- | --- | --- |")
    for p in preds:
        mark = "**被反驳**" if p["refuted"] else ("描述性" if p["timing"] == "descriptive" else "成立")
        a(f"| {p['id']} | {p['statement']} | {p['observed']} | {mark} |")
    a("")
    refuted = [p["id"] for p in preds if p["refuted"]]
    n_priori = sum(1 for p in preds if p["timing"] == "priori")
    a(f"先验预测（P1–P{n_priori}）成立 {n_priori - len(refuted)}/{n_priori}"
      f"，被反驳：{('、'.join(refuted) if refuted else '无')}。")
    a("")
    a("## 7. 仪器自检")
    a("")
    if gates:
        a("| 代码 | 详情 |")
        a("| --- | --- |")
        for g in gates:
            a(f"| `{g['code']}` | {g['detail']} |")
    else:
        a(f"全部通过（{len(gates)} 处违规，0 处即通过）。")
    a("")
    mut_path = OUT_DIR / "l3aa_mutation.json"
    if mut_path.exists():
        mut = json.loads(mut_path.read_text(encoding="utf-8"))
        a("### 7.1 变异测试")
        a("")
        a("检查器自己写错、通过率是假的情况，本项目已经发生过三次，所以每个新检查器都要先注入已知缺陷。")
        a("")
        a("| # | 注入的缺陷 | 抓到了 | 触发的闸门 / 被反驳的预测 |")
        a("| --- | --- | --- | --- |")
        for m in mut["mutations"]:
            hit = "、".join(m["gates_fired"]) or "—"
            ref = "、".join(m["refuted_after"]) or "—"
            a(
                f"| {m['id']} | {m['defect']} | {'**是**' if m['caught'] else '否（预期内）'} "
                f"| {hit} / {ref} |"
            )
        a("")
        a(f"源码恢复后与基线完全一致：{mut['restored_matches_baseline']}；全部符合期望：{mut['all_satisfied']}。")
        a("")
        a("M6（相关性只取 lag 0）没有被抓到，**这是设计如此，不是漏检**：")
        a("它改变的是「披露得多保守」（|ρ| 中位数 0.41 → 0.12），")
        a("而 P6 是单侧阈值 0.5，两种口径都在下方，结论不变。闸门只管算得对不对。")
        a("这一条如实保留，不因为最终全绿而删掉。")
    else:
        a("### 7.1 变异测试")
        a("")
        a("尚未运行（`scripts/l3aa_mutation_test.py`）。")
    a("")
    a("## 8. 结论与处置")
    a("")
    a("1. **主口径 NW(3) 下，BY（任意依赖）与 Bonferroni 给出完全相同的 6 条**，")
    a("   因此对外可引的 6 条结论**不依赖 PRDS 假设**。这一条应写进定稿的分级依据。")
    a("2. 定稿**无需改动**：分级、数字、措辞都不变。本阶段只增加一个「为什么这 6 条站得住」的理由，")
    a("   而且这个理由是保守方向的。")
    a("3. `phasep_late` 在 BY 下掉出——它本来就是 `NOT_GRADED`，不进可引清单，不构成矛盾。")
    a("4. **但「不依赖滞后」这个更强的说法不成立**："
      f"六档里只有 {len(res['lag_intersection'])} 条始终通过，"
      f"{_fmt_set(res['lag_varying'])} 会摆动。预登记 P4 因此被反驳，已如实记在报告里，不改判据。")
    a("5. 依赖诊断给出了「这批检验有多同步」的实测分布，但它**不能**被写成「PRDS 已验证」。")
    a("")
    a("**状态**：" + res.get("status", "—"))
    a("")
    a("## 9. 仍未解决的")
    a("")
    a("| 缺口 | 状态 |")
    a("| --- | --- |")
    a("| BH 的 PRDS 前提 | **仍未验证**（本阶段改走 BY，绕开而非验证） |")
    a("| 不同原假设数 | UNKNOWN（同 L3z） |")
    a("| 未进入最终报告的试验 | UNKNOWN（同 L3z） |")
    a("| SciPy 独立参考 | 未完成（无网络） |")
    a("| 经济意义 / 可交易性 | 不在本阶段范围 |")
    a("")
    a(f"耗时 {elapsed:.1f}s。")
    return "\n".join(lines) + "\n"


def main() -> int:
    started = time.perf_counter()
    quiet = "--quiet" in sys.argv
    shas = _check_frozen()

    entries = ct.load_prereg(L3U_PREREG)["entries"]
    inputs = {
        "shas": shas,
        "entries": entries,
        "l3u": json.loads(L3U_RESULT.read_text(encoding="utf-8")),
        "cards": json.loads(CARDS.read_text(encoding="utf-8")),
        "l3z": json.loads(L3Z_RESULT.read_text(encoding="utf-8")),
        "lag": json.loads(LAG_SENS.read_text(encoding="utf-8")),
    }
    res = evaluate(inputs)
    gates = check_gates(res)
    preds = check_predictions(res)
    elapsed = time.perf_counter() - started

    res["gates"] = gates
    res["predictions"] = preds
    res["elapsed_seconds"] = round(elapsed, 3)
    # 状态由「闸门是否干净」与「主口径结论是否成立」分开判：
    # 闸门违规 = 仪器有问题，不能报结论；主口径被反驳 = 结论要改。
    # 只是滞后稳健性被反驳（P4）属于第三种情况：主口径结论成立，
    # 但不能说「全档不变」——必须写进状态，不能含糊成 ROBUST。
    refuted = {p["id"] for p in preds if p["refuted"]}
    if gates or refuted & {"P1", "P2", "P3"}:
        res["status"] = "REVIEW_REQUIRED"
    elif refuted:
        res["status"] = "ROBUST_AT_MAIN_SPEC_LAG_SENSITIVE"
    else:
        res["status"] = "ARBITRARY_DEPENDENCE_ROBUST"
    res["refuted_predictions"] = sorted(refuted)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3aa_dependence.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    md = render(res, gates, preds, elapsed)
    (OUT_DIR / "l3aa_dependence.md").write_text(md, encoding="utf-8", newline="\n")
    REPORT.write_text(md, encoding="utf-8", newline="\n")

    if not quiet:
        by = res["by_m24"]
        print(f"独立检验 m      {res['m_independent']}（登记 {res['m_all']}）")
        print(f"H_m             {by['harmonic']:.6f}  第 1 档门槛 {by['unit_threshold']:.8f}")
        print(
            f"拒绝数          单条 {sum(1 for i in by['items'] if i['p'] <= 0.05)}"
            f" → Bonferroni {len(res['l3z_sets']['bonferroni'])}"
            f" → BH {len(res['l3z_sets']['bh'])}"
            f" → BY {len(by['rejected'])}"
        )
        print(f"BY 与 Bonferroni 集合相同  {set(by['rejected']) == set(res['l3z_sets']['bonferroni'])}")
        print(f"BH 减 BY        {sorted(set(res['l3z_sets']['bh']) - set(by['rejected']))}")
        dep = res["dependence"]
        print(f"重叠率中位数    {dep['median_overlap_ratio']:.4f}")
        print(f"|ρ| 中位数      {dep['median_abs_rho']:.4f}（lag0 {dep['median_abs_rho_shift0']:.4f}）")
        if res["m_eff"].get("m_eff"):
            print(f"m_eff           {res['m_eff']['m_eff']:.3f}（子集 {res['m_eff']['m_subset']} 条）")
        print(f"闸门            {len(gates)} 处")
        for g in gates:
            print(f"  - {g['code']}: {g['detail']}")
        refuted = [p["id"] for p in preds if p["refuted"]]
        n_priori = sum(1 for p in preds if p["timing"] == "priori")
        print(f"预测            成立 {n_priori - len(refuted)}/{n_priori}，被反驳 {refuted or '无'}")
        print(f"状态            {res['status']}")
        print(f"报告            {REPORT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
