"""L3af：检验家族台账与 m 口径复核。

与 L3ac / L3ad 的分工：
* ``family_v2``（L3ac）算「严格子窗口合并」下的 m；
* ``independence``（L3ad）算「同一随机变量合并」下的 m=25，并给出四种校正；
* **本模块** 把 29 条（27 登记 + 2 纳入）摊成一张**逐条台账**，12 个字段全部落盘，
  并在 5 个 m 口径下重算四种校正的**校正后 p**、门槛与拒绝状态。

为什么不能只存「通过几条」：只存通过数就无法回答「换一个 m，是哪一条掉出去的」，
而本项目的历史争议全部集中在「哪一条」上（`phasel_alpha` 的 p 卡在两个门槛之间）。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import independence as ind
from . import multiplicity as mp
from .cost_net import POSITIVE_CLAIMS, PREREG_SHA, load_prereg, verify_inputs

ALPHA = 0.05
Q = 0.05

#: 5 个 m 口径。m 为 None 表示「按子族分别取 m」。
VARIANTS = (
    ("FP_m24", 24, "L3z 数值指纹去重（抓不到子窗口）", "对照"),
    ("IND_m25", 25, "独立随机变量数（L3ad 主口径）", "主口径"),
    ("L3AC_m27", 27, "只合并严格子窗口（等长同值占两个名额）", "对照"),
    ("ALL_m29", 29, "全部登记 + phasei，不做任何合并", "对照（最保守）"),
    ("SUB_family", None, "按 family 字段分族，族内独立校正", "合理性检验，不采用"),
)


# --------------------------------------------------------------------------
# 校正后 p 的独立实现
# --------------------------------------------------------------------------


def adjusted_p(pvals: Sequence[float], method: str, m: int | None = None) -> list[float]:
    """校正后 p（adjusted p-value）。**独立实现**，用于与 multiplicity 的布尔结果互校。

    四条公式（p 升序，m_eff 为名额数）：
    * Bonferroni: ``min(1, m·p)``
    * Holm:       ``max_{j≤i} min(1, (m−j+1)·p_(j))``（单调化）
    * BH:         ``min_{j≥i} min(1, m/j·p_(j))``
    * BY:         ``min_{j≥i} min(1, m·H_m/j·p_(j))``，H_m 为调和数

    这四条在这里重写一遍而不是调用 ``multiplicity``，是刻意的：
    若两套实现同时来自同一个函数，「独立校验」这句话就是空的。
    """
    n = len(pvals)
    m_eff = max(int(m if m is not None else n), n)
    padded = list(pvals) + [1.0] * (m_eff - n)
    order = sorted(range(m_eff), key=lambda i: (padded[i], i))
    p_sorted = [padded[i] for i in order]
    out_sorted = [1.0] * m_eff

    if method == "bonferroni":
        out_sorted = [min(1.0, m_eff * p) for p in p_sorted]
    elif method == "holm":
        run = 0.0
        for i, p in enumerate(p_sorted):
            val = min(1.0, (m_eff - i) * p)
            run = max(run, val)
            out_sorted[i] = run
    elif method in ("bh", "by"):
        hm = sum(1.0 / k for k in range(1, m_eff + 1)) if method == "by" else 1.0
        run = 1.0
        for i in range(m_eff - 1, -1, -1):
            val = min(1.0, m_eff * hm / (i + 1) * p_sorted[i])
            run = min(run, val)
            out_sorted[i] = run
    else:
        raise ValueError(f"未知校正方法：{method}")

    res = [1.0] * m_eff
    for pos, i in enumerate(order):
        res[i] = out_sorted[pos]
    return res[:n]


def apply_all(pvals: Sequence[float], m: int | None) -> dict[str, Any]:
    n = len(pvals)
    m_eff = max(int(m if m is not None else n), n)
    out: dict[str, Any] = {"m": m_eff, "n_scored": n, "n_padded": m_eff - n}
    for method in ("bonferroni", "holm", "bh", "by"):
        adj = adjusted_p(pvals, method, m_eff)
        thresh = {"bonferroni": ALPHA, "holm": ALPHA, "bh": Q, "by": Q}[method]
        out[method] = {
            "adjusted_p": adj,
            "rejected": [a <= thresh for a in adj],
            "level": thresh,
        }
    out["params"] = {
        "alpha": ALPHA,
        "q": Q,
        "bonferroni_threshold": ALPHA / m_eff,
        "by_unit_threshold": Q / (m_eff * sum(1.0 / k for k in range(1, m_eff + 1))),
        "harmonic_m": sum(1.0 / k for k in range(1, m_eff + 1)),
    }
    return out


def cross_check(pvals: Sequence[float], m: int | None, res: Mapping[str, Any]) -> dict[str, Any]:
    """把上面那套 adjusted-p 的拒绝集合与 ``multiplicity`` 的布尔结果逐方法对比。

    两者必须完全一致。不一致说明至少一方的实现有错——这正是 L3ac M6 与 L3ae M3
    两次漏检的同一类问题（自有实现与自有实现互相印证）。
    """
    n = len(pvals)
    m_eff = max(int(m if m is not None else n), n)
    padded = list(pvals) + [1.0] * (m_eff - n)
    ref = {
        "bonferroni": mp.bonferroni(padded, alpha=ALPHA, m=m_eff)[:n],
        "holm": mp.holm(padded, alpha=ALPHA, m=m_eff)[:n],
        "bh": mp.bh(padded, q=Q, m=m_eff)[:n],
        "by": mp.benjamini_yekutieli(padded, q=Q, m=m_eff)[:n],
    }
    out = {}
    for method in ("bonferroni", "holm", "bh", "by"):
        mine = res[method]["rejected"]
        out[method] = {
            "agree": list(mine) == list(ref[method]),
            "n_mine": sum(mine),
            "n_ref": sum(ref[method]),
            "diff": [i for i in range(n) if bool(mine[i]) != bool(ref[method][i])],
        }
    return out


def subfamily_runs(
    keys: Sequence[str], pvals: Mapping[str, float], family_of: Mapping[str, str]
) -> dict[str, Any]:
    """族内独立校正。

    **不采用为主口径**：族的划分规则是在看过 p 之后才提出的，采用它等于事后换家族。
    做这一步只是为了回答一个具体质疑——「7 条通过是不是把不同问题混在一个家族里凑出来的」。
    如果族内校正也能过，那这个质疑不成立。
    """
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(family_of.get(k, "UNKNOWN"), []).append(k)
    detail = {}
    rejected: dict[str, list[str]] = {m: [] for m in ("bonferroni", "holm", "bh", "by")}
    for fam_name, members in sorted(groups.items()):
        ps = [pvals[k] for k in members]
        res = apply_all(ps, m=len(members))
        detail[fam_name] = {
            "members": members,
            "m": len(members),
            "p": ps,
            "counts": {m: sum(res[m]["rejected"]) for m in ("bonferroni", "holm", "bh", "by")},
        }
        for method in rejected:
            rejected[method].extend(
                k for k, f in zip(members, res[method]["rejected"]) if f
            )
    return {
        "groups": detail,
        "n_groups": len(groups),
        "rejected": rejected,
        "counts": {m: len(v) for m, v in rejected.items()},
    }


# --------------------------------------------------------------------------
# 逐条台账
# --------------------------------------------------------------------------


def registry(
    root: Path,
    *,
    entries: Sequence[Mapping[str, Any]],
    added: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Mapping[str, Any]],
    partition: Mapping[str, Any],
    decisions: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    pvals: Mapping[str, float],
    frozen: Mapping[str, Mapping[str, Any]],
    runs: Mapping[str, Mapping[str, Any]],
    keys_sorted: Sequence[str],
) -> list[dict[str, Any]]:
    """29 条 × 12 字段。缺失的一律写 ``NOT_RECORDED``，不猜。"""
    dec = {d["key"]: d for d in decisions["per_claim"]}
    claim_by = {c["key"]: c for c in claims}
    groups = partition["groups"]
    rep_of = {k: g[0] for g in groups for k in g}
    group_of = {k: g for g in groups for k in g}
    entry_by = {e["key"]: e for e in entries}
    added_by = {e["key"]: e for e in added}

    rows = []
    for key in sorted(set(entry_by) | set(added_by)):
        e = entry_by.get(key) or added_by[key]
        st = stats.get(key, {})
        d = dec.get(key, {})
        c = claim_by.get(key, {})
        series = e.get("series") or _phasei_series(key)
        g = group_of.get(key, [key])
        same_as = [k for k in g if k != key]
        idx = list(keys_sorted).index(key) if key in keys_sorted else None
        row = {
            "id": key,
            "in_registered": key in entry_by,
            "question": d.get("hypothesis_zh") or "NOT_RECORDED",
            "null": "H0: E[判定量] = 0（无差异/无增量）",
            "data_path": series.get("path"),
            "data_column": series.get("column") or series.get("value_column") or series.get("kind"),
            "window": _window_of(root, {"key": key, "series": series}),
            "n": st.get("n"),
            "random_variable": f"{series.get('path')}::{series.get('column') or series.get('kind')}",
            "stat": {
                "n": st.get("n"),
                "mean_pp": st.get("mean_pp"),
                "t_nw3": st.get("t_nw3"),
                "p": st.get("p"),
                "mde_pp": st.get("mde_pp"),
            },
            "bit_identical_with": same_as,
            "representative": rep_of.get(key),
            "in_partition": key in group_of,
            "is_subwindow_of": _subwindows(root, key, g, stats),
            "same_null_same_rule": (
                same_rule_label(g, key) if key in group_of else "SERIES_NOT_LOADED"
            ),
            "inclusion_reason": d.get("reason") or (
                "L3af 纳入：phasei 有完整结论但从未登记（见 L3ab family_coverage 缺口）"
                if not key in entry_by else "NOT_RECORDED"
            ),
            "exclusion_or_merge_reason": (
                "序列无法按登记声明加载，不参与分区也不占独立名额的判定（见 L3z 的排除记录）"
                if key not in group_of else
                f"与 {rep_of.get(key)} 逐位相同，按同一随机变量只占一个名额；"
                "但两者的登记判定字符串不同（见 L3ab A 类搜索 = 0 条）⇒ 合并的是名额，不是假设"
                if rep_of.get(key) != key else "未合并"
            ),
            "decisions_class": d.get("class") or ("NOT_REGISTERED" if not key in entry_by else "NOT_RECORDED"),
            "cluster": d.get("cluster") or e.get("family") or "UNKNOWN",
            "registered_verdict": c.get("headline") or d.get("verdict_original") or "",
            "quotability": c.get("quotability"),
            "evidence_source": {
                "registry_sha": frozen["l3u_prereg"]["sha256_raw"],
                "decisions_sha": frozen["l3ab_decisions"]["sha256_raw"],
                "claims_sha": frozen["l3v_claims"]["sha256_raw"],
            },
            "in_scoring_set": idx is not None,
            "corrections": {
                name: {
                    "adjusted_p": runs[name]["bonferroni"]["adjusted_p"][idx] if idx is not None else None,
                    "bonferroni": runs[name]["bonferroni"]["rejected"][idx] if idx is not None else None,
                    "holm": runs[name]["holm"]["rejected"][idx] if idx is not None else None,
                    "bh": runs[name]["bh"]["rejected"][idx] if idx is not None else None,
                    "by": runs[name]["by"]["rejected"][idx] if idx is not None else None,
                }
                for name in runs
            },
        }
        rows.append(row)
    return rows


#: phasei 两条的序列声明（family_v2 里的口径，抄来这里只为取窗口，不重复判定）。
_PHASEI_SERIES = {
    "phasei_v0": {"kind": "column", "path": "phasei_out/8f5c35121fdb/monthly.csv",
                  "column": "alpha_v0_pp", "scale": 1},
    "phasei_v1": {"kind": "column", "path": "phasei_out/8f5c35121fdb/monthly.csv",
                  "column": "alpha_v1_pp", "scale": 1},
}


def _phasei_series(key: str) -> dict:
    if key not in _PHASEI_SERIES:
        raise fa_err(key)
    return dict(_PHASEI_SERIES[key])


def fa_err(key: str) -> Exception:
    return ValueError(f"条目 {key} 既不在 l3u 登记里，也不在 phasei 纳入清单里")


def _window_of(root: Path, entry: Mapping[str, Any]) -> str:
    """从序列本身取月份范围，**不读 summary 里的 window 标签**。

    为什么不用标签：``phasec_d`` 这类条目的窗口标签在不同阶段报告里写法不一
    （"single_window"、"2016-2026"…），照着标签填会把窗口抄错。
    直接加载序列、取月份索引的 min/max，是唯一不会抄错的做法。
    """
    series = entry.get("series") or {}
    path = series.get("path")
    if not path:
        return "NOT_RECORDED"
    full = root / path
    if not full.exists():
        return "MISSING"
    try:
        from . import dependence as dp

        s = dp.load_entry(root, entry)
        return f"{min(s.values)}~{max(s.values)}（n={s.n}）"
    except Exception as exc:  # 载入失败要暴露原因，不能只说 UNLOADABLE
        return f"UNLOADABLE:{type(exc).__name__}"


def _subwindows(root: Path, key: str, group: Sequence[str], stats: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """严格更短且被完全覆盖的组内成员。用于回答「是否子窗口」。"""
    nk = stats.get(key, {}).get("n")
    out = []
    for other in group:
        if other == key:
            continue
        no = stats.get(other, {}).get("n")
        if nk is None or no is None:
            continue
        if no < nk:
            out.append(other)
        elif no > nk:
            out.append(f"<-{other}")
    return out


def same_rule_label(group: Sequence[str], key: str) -> str:
    """「是否同一个零假设 + 同一个拒绝规则」。

    三态，**不允许**用布尔糊过去：

    * 组内只有自己 ⇒ ``SAME_TRIVIALLY``；
    * 组内有其他成员 ⇒ ``NOT_RECORDED_IN_PRODUCTS``：l3u/l3v 里没有逐条记录零假设与拒绝规则的
      原文，只有判定字符串。判定字符串不同只能说明**结论标签**不同，不能反推假设相同或不同。
      L3ab 的 A 类搜索（=0 条）是**人读预登记原文**得出的，不是从产物自动推出来的，
      所以这里不能假装机械可得。
    """
    if len(group) <= 1:
        return "SAME_TRIVIALLY"
    return "NOT_RECORDED_IN_PRODUCTS"


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def build(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    prereg = load_prereg(root)
    frozen = verify_inputs(root, prereg)

    base = ind.build(root)
    pvals = base["pvals"]
    keys_sorted = base["p_order"]
    p_list = [pvals[k] for k in keys_sorted]

    entries = json.loads(
        (root / "docs/gates/l3u_prereg.json").read_text(encoding="utf-8")
    )["entries"]
    added = json.loads(
        json.dumps(
            [
                {"key": "phasei_v0", "family": "D_exq5"},
                {"key": "phasei_v1", "family": "D_exq5"},
            ]
        )
    )
    decisions = json.loads(
        (root / "docs/gates/l3ab_family_decisions.json").read_text(encoding="utf-8")
    )
    claims = json.loads(
        (root / "docs/gates/l3v_claims.json").read_text(encoding="utf-8")
    )["claims"]

    runs = {}
    checks = {}
    for vid, m, rule, role in VARIANTS:
        if m is None:
            continue
        res = apply_all(p_list, m)
        res["rule"] = rule
        res["role"] = role
        runs[vid] = res
        checks[vid] = cross_check(p_list, m, res)

    family_of = {e["key"]: e["family"] for e in entries}
    family_of.update({"phasei_v0": "D_exq5", "phasei_v1": "D_exq5"})
    sub = subfamily_runs(keys_sorted, pvals, family_of)

    # 每条检验在每个口径下的拒绝状态（只存被拒绝的 key，便于逐条对账）
    rejected = {
        vid: {method: [k for k, f in zip(keys_sorted, runs[vid][method]["rejected"]) if f]
              for method in ("bonferroni", "holm", "bh", "by")}
        for vid in runs
    }

    pos_by_variant = {
        vid: {
            method: [k for k in rejected[vid][method] if k in POSITIVE_CLAIMS]
            for method in ("bonferroni", "holm", "bh", "by")
        }
        for vid in runs
    }
    pos_uniform = {
        vid: len({tuple(sorted(v)) for v in pos_by_variant[vid].values()}) == 1
        for vid in runs
    }

    reg = registry(
        root,
        entries=entries,
        added=added,
        stats=base["stats"],
        partition=base["partition_with_added"],
        decisions=decisions,
        claims=claims,
        pvals=pvals,
        frozen=frozen,
        runs=runs,
        keys_sorted=keys_sorted,
    )

    res = {
        "experiment_id": "H-L3AF-FAMILY",
        "prereg_sha256": PREREG_SHA,
        "n_registered": len(entries),
        "n_added": len(added),
        "n_entries_total": len(entries) + len(added),
        "n_scored": len(pvals),
        "m_adopted": 25,
        "m_arithmetic": {
            "n_registered": len(entries),
            "n_merged_away": base["partition_with_added"]["n_merged_away"],
            "n_added": len(added),
            "recomputed": len(entries) - base["partition_with_added"]["n_merged_away"] + len(added),
        },
        "partition": base["partition_with_added"]["groups"],
        "merged_into": base["merged_into"],
        "variants": [{"id": v, "m": m, "rule": r, "role": role} for v, m, r, role in VARIANTS],
        "runs": {
            vid: {
                "m": runs[vid]["m"],
                "rule": runs[vid]["rule"],
                "role": runs[vid]["role"],
                "n_scored": runs[vid]["n_scored"],
                "n_padded": runs[vid]["n_padded"],
                "params": runs[vid]["params"],
                "counts": {meth: sum(runs[vid][meth]["rejected"]) for meth in ("bonferroni", "holm", "bh", "by")},
                "rejected": rejected[vid],
                "adjusted_p": {
                    k: {meth: runs[vid][meth]["adjusted_p"][i] for meth in ("bonferroni", "holm", "bh", "by")}
                    for i, k in enumerate(keys_sorted)
                },
            }
            for vid in runs
        },
        "cross_check": checks,
        "subfamily": sub,
        "positive_claims": list(POSITIVE_CLAIMS),
        "positive_by_variant": pos_by_variant,
        "positive_uniform_within_variant": pos_uniform,
        "registry": reg,
        "fwer": base["fwer"],
        "unknowns": [
            "研究问题原文（question）在 l3u/l3v 里都没有逐条记录；台账里的 question 取自 L3ab 判定记录的 hypothesis_zh，缺失的写 NOT_RECORDED。",
            "历史上尝试但未报告的策略/窗口/成本档位无法从产物恢复 —— 这是本阶段无法关闭的缺口，见 family_coverage。",
            "26 个 *_out 目录只覆盖 23 个；phaseu_out 是只读审计、probe_out 无 summary，判定为「不产生结论」。",
        ],
    }
    res["gates"] = gate_codes(res, base)
    res["predictions"] = check_predictions(res)
    return res


def gate_codes(res: Mapping[str, Any], base: Mapping[str, Any]) -> list[dict[str, str]]:
    out = []
    a = res["m_arithmetic"]
    if a["recomputed"] != res["m_adopted"]:
        out.append({"code": "G9_FAMILY_CONSISTENCY",
                    "detail": f"m 算术 {a['recomputed']} ≠ 主口径 {res['m_adopted']}"})

    # 评分集合里的 key 必须都能在台账里找到，且台账条数 = 登记 + 纳入
    ids = {r["id"] for r in res["registry"]}
    if len(ids) != res["n_entries_total"]:
        out.append({"code": "G9_FAMILY_CONSISTENCY",
                    "detail": f"台账 {len(ids)} 条 ≠ 登记+纳入 {res['n_entries_total']}"})
    missing = [k for k in base["p_order"] if k not in ids]
    if missing:
        out.append({"code": "G9_FAMILY_CONSISTENCY", "detail": f"评分集合有台账外的 key：{missing}"})

    # 两套实现必须一致
    for vid, chk in res["cross_check"].items():
        for method, row in chk.items():
            if not row["agree"]:
                out.append({"code": "G8_CORRECTION_MISMATCH",
                            "detail": f"{vid}/{method} 我的实现与 multiplicity 不一致：{row['diff']}"})

    # 正面结论在四个 m 口径下必须相同
    for vid, rows in res["positive_by_variant"].items():
        sets = {tuple(sorted(v)) for v in rows.values()}
        if len(sets) != 1:
            out.append({"code": "G9B_M_ROBUST",
                        "detail": f"{vid} 四种校正给出不同结论：{rows}"})

    base_set = {tuple(sorted(res["positive_by_variant"]["IND_m25"][m])) for m in ("bonferroni",)}
    for vid, rows in res["positive_by_variant"].items():
        got = {tuple(sorted(rows[m])) for m in ("bonferroni",)}
        if got != base_set:
            out.append({"code": "G9B_M_ROBUST",
                        "detail": f"{vid} 的正面集合与 m=25 不同：{sorted(got)} vs {sorted(base_set)}"})
    return out


def check_predictions(res: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    pos = res["positive_by_variant"]
    all_sets = {
        vid: {m: sorted(v) for m, v in rows.items()} for vid, rows in pos.items()
    }
    flat = {vid: sorted({k for v in rows.values() for k in v}) for vid, rows in all_sets.items()}
    uniform = len({tuple(v) for v in flat.values()}) == 1
    out.append(
        {
            "id": "P7",
            "timing": "priori",
            "recon_observed": False,
            "claim": "m=24/25/27/29 下 7 条正面结论的通过集合完全相同",
            "observed": "; ".join(f"{vid}: {len(v)} 条" for vid, v in flat.items())
            + f"（集合相同={uniform}）",
            "holds": uniform,
            "note": "差异只应出现在非正面结论上（phasel_alpha）",
        }
    )
    sub = res["subfamily"]
    sub_pos = {m: sorted(k for k in sub["rejected"][m] if k in POSITIVE_CLAIMS)
               for m in ("bonferroni", "holm", "bh", "by")}
    holds = all(len(v) == len(POSITIVE_CLAIMS) for v in sub_pos.values())
    out.append(
        {
            "id": "P8",
            "timing": "priori",
            "recon_observed": False,
            "claim": "子族口径（族内独立校正）下 7 条仍全部通过",
            "observed": "; ".join(f"{m}: {len(v)}/7" for m, v in sub_pos.items())
            + f"；{sub['n_groups']} 个子族",
            "holds": holds,
            "note": "不采用子族口径为主口径：分族规则在看过 p 之后才提出",
        }
    )
    return out
