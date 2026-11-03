"""检验家族的审计：把「27 条登记结论算几个独立检验」拆成可核的几件事。

这个模块只做三件事，都刻意不做成判断：

1. **数据层面**的同一性——哪几条共用同一份序列、哪条是另一条的子窗口。
   这是可算的，算得出来就写出来。
2. **假设层面**的归类——读 `docs/gates/l3ab_family_decisions.json`，
   把「这条问的是什么」的判断放进去。判断一旦改动就改 sha，无法悄悄挪边界。
3. **口径层面**的 m 候选与校正复现——同一个成员表 + 不同 m，跑四种校正。

刻意不做的事：不因为某条「看起来和另一条一样」就把它从家族里删掉。
删家族成员会让门槛变松、通过变多，这正是审计提纲禁止的动作。所以默认全部保留，
只报告「若按另一种口径算会怎样」。
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dependence as dp
from . import multiplicity as mp

EXPERIMENT_ID = "H-L3AB-FAMILY"

#: 判定记录的 sha256。它是本审计的「判断层」，改动必须留痕。
DECISIONS_SHA = "afdf3fded8def1a090d82cb42f0439d0892fb9fc7dd7b2b93485631ec0a9df75"

#: 序列逐位相同的判等容差。产物是双精度，1e-12 足以区分「同一份」与「几乎一样」。
EXACT_TOL = 1e-12


class FamilyError(ValueError):
    """家族审计的前置条件不满足（与「某条不显著」是两件事）。"""


def load_decisions(root: Path, path: str = "docs/gates/l3ab_family_decisions.json") -> dict:
    file = Path(root) / path
    if not file.exists():
        raise FamilyError(f"判定记录不存在：{path}")
    raw = file.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != DECISIONS_SHA:
        raise FamilyError(
            f"判定记录已被改动：期望 {DECISIONS_SHA[:12]}…，实际 {got[:12]}…；"
            "拒绝运行。若确需修订，先写 amendment 再更新脚本里的 sha。"
        )
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------
# 数据层面：同一性与子窗口
# --------------------------------------------------------------------------


def series_map(root: Path, prereg_entries: Sequence[Mapping[str, Any]]) -> dict[str, dp.MonthSeries]:
    """能定到月份的序列。定不了的（json_list、无日期列）跳过并记录原因。"""
    out: dict[str, dp.MonthSeries] = {}
    for entry in prereg_entries:
        try:
            out[entry["key"]] = dp.load_entry(root, entry)
        except dp.DependenceError:
            continue
    return out


def exact_pairs(series: Mapping[str, dp.MonthSeries]) -> list[dict]:
    """在共同月上逐位相同的配对。

    只报事实，不报结论。相同与否要用数值比，不能用「均值相同」或「t 相同」代替——
    项目历史上正因为用 t 相同去推断同源，把不同源的两条判成同源过。
    """
    keys = sorted(series)
    out: list[dict] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            A, B = series[a], series[b]
            common = sorted(set(A.values) & set(B.values))
            if not common:
                continue
            diff = max(abs(A.values[m] - B.values[m]) for m in common)
            if diff <= EXACT_TOL:
                out.append(
                    {
                        "a": a,
                        "b": b,
                        "n_common": len(common),
                        "n_a": A.n,
                        "n_b": B.n,
                        "max_abs_diff": diff,
                        "identical": True,
                    }
                )
    return out


def subwindow_relations(
    series: Mapping[str, dp.MonthSeries], pairs: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    """子窗口关系：短的那条月份集合是否被长的那条完全包含。

    与「逐位相同」是两件事：逐位相同可能是等长，也可能是子窗口。
    这里只回答「谁是谁的子集」，不回答「该不该合并」。

    返回**候选列表**而不是单值：当三条序列逐位相同时，短的那条会是两条长序列的子窗口，
    此时任意挑一个都是对的、也都会让读者误以为只有那一个超集。列表避免这个歧义。

    只记严格更短的情形；等长同值不算子窗口关系。
    """
    out: dict[str, list[str]] = {}
    for p in pairs:
        a, b = p["a"], p["b"]
        ma, mb = set(series[a].values), set(series[b].values)
        # 只有**严格更短**才算子窗口。「等长且同值」是两条一样长的序列，不是子窗口关系——
        # 混在一起会让读者以为其中一条是另一条的片段，而它们的窗口长度相同。
        if p["n_a"] < p["n_b"] and ma <= mb:
            out.setdefault(a, []).append(b)
        elif p["n_b"] < p["n_a"] and mb <= ma:
            out.setdefault(b, []).append(a)
    return {k: sorted(v) for k, v in out.items()}


# --------------------------------------------------------------------------
# 复现：从序列重算 mean / NW 标准误 / t / p
# --------------------------------------------------------------------------


def reproduce_from_series(values: Sequence[float], lag: int = 3) -> dict[str, Any]:
    """从序列本身重算 mean、NW(3) 标准误、t 与 t 分布双侧 p。

    标准误算法与 ``selection.newey_west_mean`` 同一套约定（Bartlett 核、g0 用有偏方差、
    核权重 1 − j/(lag+1)）。p 用 t 分布而不是正态近似——L3z 用的就是 t 分布，
    要复现它就必须同分布。
    """
    clean = [float(v) for v in values if v == v]
    n = len(clean)
    if n < lag + 3:
        return {"n": n, "mean": float("nan"), "se_nw": float("nan"),
                "t": float("nan"), "p": float("nan"), "lag": lag}
    mean = statistics.fmean(clean)
    errors = [v - mean for v in clean]
    g0 = sum(e * e for e in errors) / n
    total = g0
    fell_back = False
    for j in range(1, lag + 1):
        gj = sum(errors[t] * errors[t - j] for t in range(j, n)) / n
        total += 2.0 * (1.0 - j / (lag + 1)) * gj
    if total <= 0:
        total, fell_back = g0, True
    se = math.sqrt(total / n)
    t = mean / se if se > 0 else float("nan")
    p = mp.t_two_sided_p(t, n - 1) if t == t else float("nan")
    return {"n": n, "mean": mean, "se_nw": se, "t": t, "p": p,
            "lag": lag, "variance_fell_back_to_g0": fell_back}


def apply_corrections_at_m(
    pvals: Sequence[float], *, m: int, alpha: float = mp.ALPHA, q: float = mp.Q
) -> dict[str, Any]:
    """在给定 m 下跑四种校正。p 值不变、只换门槛——这是本次比较的核心。

    与 ``multiplicity.apply_corrections`` 的区别：那个函数从 rows 里自己抽 p、
    并处理「只计名额」的补位；这里已经拿到 p 列表，直接按 m 补位到 m 个。
    补位规则必须一致：不足 m 的用 p=1.0 填，否则门槛会凭空放松。
    """
    n_scored = len(pvals)
    m_eff = max(int(m), n_scored)
    padded = list(pvals) + [1.0] * (m_eff - n_scored)
    return {
        "m": m_eff,
        "n_padded": m_eff - n_scored,
        "bonferroni": mp.bonferroni(padded, alpha=alpha, m=m_eff)[:n_scored],
        "holm": mp.holm(padded, alpha=alpha, m=m_eff)[:n_scored],
        "bh": mp.bh(padded, q=q, m=m_eff)[:n_scored],
        "by": mp.benjamini_yekutieli(padded, q=q, m=m_eff)[:n_scored],
        "bonferroni_threshold": alpha / m_eff,
        "by_unit_threshold": mp.by_unit_threshold(q=q, m=m_eff),
    }


def coverage_gap(root: Path, prereg_entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """家族覆盖面：产物目录里有哪些实验从未进入登记。

    这不是「多重检验做了没有」的问题，而是「这个家族是不是全部结论」的问题。
    只看目录存在与否会误判——所以对每个未覆盖目录再去读 summary.json 是否存在、
    是否给出 verdict，区分「有结论但未登记」与「本来就不产生结论」。
    """
    root = Path(root)
    registered: set[str] = set()
    for entry in prereg_entries:
        path = entry.get("series", {}).get("path") or ""
        head = path.split("/", 1)[0]
        if head.endswith("_out"):
            registered.add(head)

    on_disk = sorted(p.name for p in root.glob("*_out") if p.is_dir())
    missing = [d for d in on_disk if d not in registered]

    detail = []
    for d in missing:
        summaries = sorted((root / d).glob("*/summary.json"))
        verdict = None
        experiment = None
        kind = None
        if summaries:
            try:
                payload = json.loads(summaries[0].read_text(encoding="utf-8"))
                verdict = payload.get("verdict") or payload.get("status")
                experiment = payload.get("experiment")
                kind = payload.get("kind")
            except (json.JSONDecodeError, OSError):
                verdict = None
        # 「有 verdict」不等于「是假设检验」：只读审计也会给 verdict（如 NW_LAG_SENSITIVITY_CONFIRMED），
        # 但它是审计自己的结论，不是待校正的假设。两类必须分开，否则覆盖率会被夸大。
        is_audit = bool(kind) and ("只读" in str(kind) or "audit" in str(kind).lower())
        detail.append(
            {
                "dir": d,
                "n_summary": len(summaries),
                "experiment": experiment,
                "verdict": verdict,
                "kind": kind,
                "audit_only": is_audit,
                "has_hypothesis_conclusion": bool(summaries) and bool(verdict) and not is_audit,
            }
        )
    return {
        "n_dirs": len(on_disk),
        "n_registered_dirs": len(registered),
        "unregistered": detail,
        "n_hypothesis_unregistered": sum(
            1 for x in detail if x["has_hypothesis_conclusion"]
        ),
    }


# --------------------------------------------------------------------------
# 清单
# --------------------------------------------------------------------------


def build_inventory(
    *,
    decisions: Mapping[str, Any],
    prereg_entries: Sequence[Mapping[str, Any]],
    l3u_rows: Sequence[Mapping[str, Any]],
    cards: Sequence[Mapping[str, Any]],
    l3z_items: Sequence[Mapping[str, Any]],
    series: Mapping[str, dp.MonthSeries],
    reproduced: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把 13 个必填字段拼成一张表。每个字段都要有来源，缺来源就写 UNKNOWN。"""
    dec = {c["key"]: c for c in decisions["per_claim"]}
    entry_by_key = {e["key"]: e for e in prereg_entries}
    row_by_key = {r["key"]: r for r in l3u_rows}
    card_by_key = {c["key"]: c for c in cards}
    p_by_key = {i["key"]: i for i in l3z_items}

    out: list[dict[str, Any]] = []
    for key, d in dec.items():
        entry = entry_by_key.get(key) or {}
        row = row_by_key.get(key) or {}
        card = card_by_key.get(key) or {}
        item = p_by_key.get(key) or {}
        ser = series.get(key)

        stat_spec = entry.get("stat") or {}
        stage = key.split("_")[0].replace("gate", "Gate ").replace("phase", "阶段 ")
        n = (row.get("recomputed") or {}).get("n")
        p = item.get("p")
        counts_only = p is None

        out.append(
            {
                "key": key,
                # 研究假设：来自判定记录，明确标注是反推而非原始设计文本
                "hypothesis": d["hypothesis_zh"],
                "hypothesis_source": "verdict_original + card.headline（反推，非原始设计文本）",
                "verdict_original": row.get("verdict_original"),
                # 统计量与判定量
                "statistic": (
                    f"序列均值 / {stat_spec.get('t', '—')}"
                    if stat_spec.get("mean")
                    else "判定非单一均值（stat.applicable=false）"
                ),
                "d_definition": card.get("d_definition"),
                # 序列与窗口
                "series": ser.path if ser else (entry.get("series") or {}).get("path"),
                "window": (
                    f"{ser.span()[0]}~{ser.span()[1]}" if ser else card.get("window_note", "UNKNOWN")
                ),
                "n": n,
                # 统计量
                "p": p,
                "se_method": "Newey-West(3)，t 分布双侧" if not counts_only else "—",
                "t_nw3": (row.get("recomputed") or {}).get("t_nw3"),
                # 家族判定
                "class": d["class"],
                "cluster": d["cluster"],
                "in_family": d["in_family"],
                "reason": d["reason"],
                "counts_only": counts_only,
                # 引用状态
                "tier": card.get("quotability", "UNKNOWN"),
                # 复现结果
                "reproduced": dict(reproduced.get(key) or {}),
            }
        )
    return out


def spec_table(
    inventory: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """把一条 m 口径落成可比的集合：成员、m、四种校正。"""
    scored = [r for r in inventory if not r["counts_only"]]
    pvals = [r["p"] for r in scored]
    m = spec["m"]
    if m == "computed":
        m = len({r["cluster"] for r in inventory if r["in_family"]})
    run = apply_corrections_at_m(pvals, m=int(m))
    items = []
    for i, r in enumerate(scored):
        items.append(
            {
                "key": r["key"],
                "p": r["p"],
                "bonferroni": run["bonferroni"][i],
                "holm": run["holm"][i],
                "bh": run["bh"][i],
                "by": run["by"][i],
            }
        )
    return {
        "id": spec["id"],
        "label": spec["label"],
        "role": spec["role"],
        "adopted": spec["adopted"],
        "m": run["m"],
        "n_scored": len(pvals),
        "n_counts_only": len(inventory) - len(pvals),
        "n_padded": run["n_padded"],
        "bonferroni_threshold": run["bonferroni_threshold"],
        "by_unit_threshold": run["by_unit_threshold"],
        "rejected": {
            "bonferroni": [i["key"] for i in items if i["bonferroni"]],
            "holm": [i["key"] for i in items if i["holm"]],
            "bh": [i["key"] for i in items if i["bh"]],
            "by": [i["key"] for i in items if i["by"]],
        },
        "items": items,
        "limitation": spec["limitation"],
    }
