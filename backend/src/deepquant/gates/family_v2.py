"""L3ac：检验家族口径修正——同源子窗口合并 + 未登记结论纳入。

为什么单独一个模块而不改 ``family.py``：L3ab 的产物已冻结，9 份交付文件都引用了
它，改动 ``family.py`` 的行为会让那些哈希失效。本模块只新增，不改既有函数。

三条规则与它们的边界都在预登记 ``docs/gates/l3ac_prereg.json`` 里写死：

* R1（子窗口合并）：严格更短 **且** 共同月逐位相同才算同一份数据的两种切窗。
  **等长同值不合并**——它们没有「谁是谁的片段」这个结构，合并它们等于假设
  「同数据即同假设」，而 L3ab 已证该假设与「登记判定互不相同」的证据冲突。
* R2（纳入未登记）：phasei 的 V0 / V1 是同一问题的两个假设（存在性 vs 可实现性），
  数值不同、非子窗口关系，各计 1。
* 合并的**传递性**：若 A 是 B 的截取、而 B 与 C 逐位相同，则 A、B、C 落在同一份
  数据上，并查集把它们并成一份。这一步是 R1 字面执行的结果，不是额外规则。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepquant.gates import dependence as dp
from deepquant.gates import family as fam
from deepquant.gates import multiplicity as mp

EXPERIMENT_ID = "H-L3AC-FAMILY-V2"
PREREG_PATH = "docs/gates/l3ac_prereg.json"
PREREG_SHA = "9d4c39d8873bdb650e57ee4e39b04533de17f2e73ff93117ce2e79e5f2a81086"

#: 逐位相同的判定容差。与 ``family.EXACT_TOL`` 一致。
EXACT_TOL = 1e-12


class FamilyV2Error(RuntimeError):
    """预登记校验失败或家族定义不成立。"""


# --------------------------------------------------------------------------
# 预登记
# --------------------------------------------------------------------------


def load_prereg_v2(root: Any) -> dict:
    """读预登记并校验自哈希。改一个字就拒跑。"""
    path = Path(root) / PREREG_PATH
    if not path.exists():
        raise FamilyV2Error(f"预登记不存在：{PREREG_PATH}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != PREREG_SHA:
        raise FamilyV2Error(
            f"预登记已被改动：期望 {PREREG_SHA[:12]}… 实际 {got[:12]}…"
            "（预登记一旦落盘就不应被「顺手更新」；有缺陷请另出更正记录）"
        )
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------
# R2：纳入的未登记结论
# --------------------------------------------------------------------------


def phasei_entries() -> list[dict]:
    """phasei_out 的两条结论，按 L3u 的 ``series`` 声明格式描述。

    ``scale`` 取 1（不是 100）：这两个列存的**已经是 pp**（``alpha_v0_pp``），
    再乘 100 会把 MDE 放大 100 倍——本项目「pp÷小数」已经错过四次。
    """
    return [
        {
            "key": "phasei_v0",
            "family": "D_exq5",
            "series": {
                "kind": "column",
                "path": "phasei_out/8f5c35121fdb/monthly.csv",
                "column": "alpha_v0_pp",
                "scale": 1,
            },
            "hypothesis": "exQ5 分层本身存在 alpha（不受可成交约束）",
            "stage": "I",
        },
        {
            "key": "phasei_v1",
            "family": "D_exq5",
            "series": {
                "kind": "column",
                "path": "phasei_out/8f5c35121fdb/monthly.csv",
                "column": "alpha_v1_pp",
                "scale": 1,
            },
            "hypothesis": "剔除当期不可成交标的后，同一 alpha 是否仍在（可实现性）",
            "stage": "I",
        },
    ]


def phasei_series(root: Any) -> dict[str, dp.MonthSeries]:
    out = {}
    for entry in phasei_entries():
        out[entry["key"]] = dp.load_entry(root, entry, date_priority=("signal_date",))
    return out


# --------------------------------------------------------------------------
# R1：子窗口合并
# --------------------------------------------------------------------------


def _strictly_contains(big: dp.MonthSeries, small: dp.MonthSeries) -> bool:
    """``big`` 的月份集合严格包含 ``small`` 的，且 ``small`` 在共同月上逐位相同。"""
    if small.n >= big.n:
        return False
    months_small = set(small.values)
    if not months_small <= set(big.values):
        return False
    return all(abs(small.values[m] - big.values[m]) <= EXACT_TOL for m in months_small)


def merge_edges(series: Mapping[str, dp.MonthSeries]) -> list[dict]:
    """R1 触发的每一对（有向：``inner`` 被 ``outer`` 吸收）。

    只报**严格子窗口**。等长逐位相同的对单独列在 ``equal_value_pairs`` 里，
    作为「不合并但需披露」的事实——它们是 L3ab 的 A 类候选。
    """
    keys = sorted(series)
    edges = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            sa, sb = series[a], series[b]
            # 差值在**共同月**上算，不假设包含关系——包含性由 _strictly_contains 判，
            # 这里只负责量化「有多相同」。变异测试会把 _strictly_contains 换成不含
            # 包含条件的版本，那时若仍按 inner 的全部月份取值就会 KeyError，
            # 于是变异体崩掉而不是被判定为「有缺陷」——这会让测试看起来通过。
            if _strictly_contains(sa, sb):
                common = set(sa.values) & set(sb.values)
                edges.append(
                    {
                        "inner": b,
                        "outer": a,
                        "n_inner": sb.n,
                        "n_outer": sa.n,
                        "n_common": len(common),
                        "max_abs_diff": max(
                            abs(sb.values[m] - sa.values[m]) for m in common
                        ),
                    }
                )
            elif _strictly_contains(sb, sa):
                common = set(sa.values) & set(sb.values)
                edges.append(
                    {
                        "inner": a,
                        "outer": b,
                        "n_inner": sa.n,
                        "n_outer": sb.n,
                        "n_common": len(common),
                        "max_abs_diff": max(
                            abs(sa.values[m] - sb.values[m]) for m in common
                        ),
                    }
                )
    return edges


def equal_value_pairs(series: Mapping[str, dp.MonthSeries]) -> list[dict]:
    """等长且逐位相同的对。**不合并**，但要列出来让读者判断。"""
    keys = sorted(series)
    out = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            sa, sb = series[a], series[b]
            if sa.n != sb.n:
                continue
            if set(sa.values) != set(sb.values):
                continue
            mx = max(abs(sa.values[m] - sb.values[m]) for m in sa.values)
            if mx <= EXACT_TOL:
                out.append({"a": a, "b": b, "n": sa.n, "max_abs_diff": mx})
    return out


def merge_partition(keys: Sequence[str], series: Mapping[str, dp.MonthSeries]) -> dict:
    """按 R1 的边做并查集，返回分组。

    传递性是 R1 字面执行的必然结果：A 是 B 的截取、B 与 C 逐位相同 ⇒ 三者在同一份
    数据上。这里不额外加规则，也不因为「组内某两条只是等长同值」而拆开。
    """
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    edges = merge_edges(series)
    for e in edges:
        union(e["inner"], e["outer"])

    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    ordered = sorted((sorted(v) for v in groups.values()), key=lambda g: g[0])
    return {
        "groups": ordered,
        "n_groups": len(ordered),
        "n_merged_away": len(keys) - len(ordered),
        "edges": edges,
    }


# --------------------------------------------------------------------------
# m 的各个口径
# --------------------------------------------------------------------------


def m_variants(
    *,
    n_reg_total: int,
    n_reg_merged_away: int,
    n_add: int,
    fingerprint_m: int,
    all_merged_m: int,
) -> list[dict]:
    """七种 m 口径。每一个都写明「由哪条规则推出来」，便于复算。

    为什么 m 用**全部登记条数**而不是「能加载序列的条数」：家族成员包括那些
    「只计名额、不出 p」的条目（L3z 有 4 条），它们的原假设同样在被检验，
    把它们从 m 里剔掉等于事后缩小家族。``n_reg_merged_away`` 只能在能加载的
    序列上算——没有序列就无从判断是不是子窗口，那几条一律各占 1 个名额。

    ``fingerprint_m`` / ``all_merged_m`` 由调用方从既有产物传入（24 / 23），
    不在这里重算——那两个数属于 L3z / L3ab 的冻结口径，本阶段只引用不重定义。
    """
    m_r1 = n_reg_total - n_reg_merged_away
    m_r1_add = m_r1 + n_add
    return [
        {
            "id": "A_m29",
            "m": n_reg_total + n_add,
            "rule": "全部登记 + phasei，一律不合并",
            "note": "最保守；忽略 R1 与指纹去重",
        },
        {
            "id": "B_m_r1",
            "m": m_r1_add,
            "rule": "R1 严格子窗口合并（含传递）+ phasei",
            "note": "**本阶段主口径**：R1 字面执行的实测结果",
        },
        {
            "id": "C_m26",
            "m": fingerprint_m + n_add,
            "rule": "L3z 指纹去重 + phasei",
            "note": "指纹 (mean,t,n) 抓不到子窗口 ⇒ 比 R1 大",
        },
        {
            "id": "D_m25",
            "m": all_merged_m + n_add,
            "rule": "数据同一性全合并（含等长同值）+ phasei",
            "note": "预登记 m_candidates 里误写为主口径的口径；需额外规则，R1 不支持",
        },
        {
            "id": "E_m24",
            "m": fingerprint_m,
            "rule": "L3z 指纹去重，不含 phasei",
            "note": "L3z 原主口径（对照）",
        },
        {
            "id": "F_m23",
            "m": all_merged_m,
            "rule": "数据同一性全合并，不含 phasei",
            "note": "L3ab 的 S3 口径（对照）",
        },
        {
            "id": "G_m27",
            "m": n_reg_total,
            "rule": "全部登记，不含 phasei",
            "note": "L3z 的替代口径（对照）",
        },
    ]


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def build(root: Any) -> dict:
    """装配全部输入、按每个 m 跑四种校正，返回可直接落盘的 dict。"""
    root = Path(root)
    prereg = load_prereg_v2(root)

    # 输入冻结校验
    frozen = {}
    for name, spec in prereg["inputs_frozen"].items():
        path = root / spec["path"]
        if not path.exists():
            raise FamilyV2Error(f"[{name}] 冻结输入缺失：{spec['path']}")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != spec["sha256"]:
            raise FamilyV2Error(
                f"[{name}] 冻结输入被改动：期望 {spec['sha256'][:12]}… 实际 {got[:12]}…"
            )
        frozen[name] = {"path": spec["path"], "sha256": got}

    from deepquant.gates import citation as ct

    prereg_l3u = ct.load_prereg(root / "docs/gates/l3u_prereg.json")
    entries = prereg_l3u["entries"]

    registered: dict[str, dp.MonthSeries] = {}
    skipped = []
    for entry in entries:
        try:
            registered[entry["key"]] = dp.load_entry(root, entry)
        except dp.DependenceError as exc:
            skipped.append({"key": entry["key"], "error": type(exc).__name__, "detail": str(exc)})

    added = phasei_series(root)
    if len(added) != 2:
        raise FamilyV2Error(f"R2 应纳入 2 条，实际 {len(added)}")

    # p 值：评分集合严格沿用 L3z 的 20 条（它是冻结的评分口径，不能因为
    # 「这次能加载更多序列」而扩张），再加 R2 的 2 条。被 R1 合并掉的条目
    # 用 outer（更长窗口）那条作代表——同一份数据取全窗口比取片段更有效，
    # 保留片段会把「样本短」的代价当成一条独立检验。
    l3z = json.loads((root / "reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json").read_text(encoding="utf-8"))
    l3z_p = {i["key"]: i["p"] for i in l3z["runs"]["m_independent"]["items"]}
    l3z_counts_only = int(l3z["runs"]["m_independent"].get("n_counts_only") or 0)

    merged_away = {e["inner"] for e in merge_edges(registered)}

    stats: dict[str, dict] = {}
    pvals: dict[str, float] = {}
    reproduced = []
    spec_by_key = {e["key"]: e.get("series", {}) for e in entries}
    for e in phasei_entries():
        spec_by_key[e["key"]] = e["series"]
    for key, s in registered.items():
        rec = fam.reproduce_from_series(list(s.values.values()), lag=3)
        stats[key] = {
            "n": rec["n"],
            "mean_pp": rec["mean"],
            "se_nw_pp": rec["se_nw"],
            "t_nw3": rec["t"],
            "p": rec["p"],
            "mde_pp": 2.8016 * rec["se_nw"],
            "in_registered": True,
            "series_spec": spec_by_key.get(key, {}),
            "merged_into": None,
        }
        if key in l3z_p:
            reproduced.append(
                {
                    "key": key,
                    "p_recomputed": rec["p"],
                    "p_l3z": l3z_p[key],
                    "abs_diff": abs(rec["p"] - l3z_p[key]),
                }
            )
            if key not in merged_away:
                pvals[key] = rec["p"]
    for key, s in added.items():
        rec = fam.reproduce_from_series(list(s.values.values()), lag=3)
        stats[key] = {
            "n": rec["n"],
            "mean_pp": rec["mean"],
            "se_nw_pp": rec["se_nw"],
            "t_nw3": rec["t"],
            "p": rec["p"],
            "mde_pp": 2.8016 * rec["se_nw"],
            "in_registered": False,
            "series_spec": spec_by_key.get(key, {}),
            "merged_into": None,
        }
        pvals[key] = rec["p"]

    # R1 合并：被吸收的条目记下代表是谁（选组内窗口最长、且在评分集合里的那条）
    rep_of = {}
    for group in merge_partition(sorted(registered), registered)["groups"]:
        if len(group) < 2:
            continue
        scored = [k for k in group if k in l3z_p]
        pool = scored or group
        rep = max(pool, key=lambda k: (registered[k].n, k))
        for k in group:
            if k != rep:
                rep_of[k] = rep
                if k in stats:
                    stats[k]["merged_into"] = rep

    variants = m_variants(
        n_reg_total=len(entries),
        # 合并量必须取分区的「实际减少组数」，不能用边数：并查集会把
        # {phaseo_netBASE, phasep_late, phasev_net} 三条并成一份（减 2），
        # 而边只有 2 条且都指向 phasep_late。
        n_reg_merged_away=merge_partition(sorted(registered), registered)["n_merged_away"],
        n_add=len(added),
        fingerprint_m=24,
        all_merged_m=23,
    )

    runs = {}
    keys_sorted = sorted(pvals, key=lambda k: (pvals[k], k))
    ordered_p = [pvals[k] for k in keys_sorted]
    for v in variants:
        res = fam.apply_corrections_at_m(ordered_p, m=v["m"])
        flags = {
            "bonferroni": res["bonferroni"],
            "holm": res["holm"],
            "bh": res["bh"],
            "by": res["by"],
        }
        runs[v["id"]] = {
            "m": v["m"],
            "rule": v["rule"],
            "note": v["note"],
            "bonferroni_threshold": res["bonferroni_threshold"],
            "by_unit_threshold": res["by_unit_threshold"],
            "n_padded": res["n_padded"],
            "rejected": {
                name: [k for k, f in zip(keys_sorted, fs, strict=True) if f]
                for name, fs in flags.items()
            },
            "counts": {name: sum(fs) for name, fs in flags.items()},
        }

    return {
        "experiment_id": EXPERIMENT_ID,
        "prereg_sha256": PREREG_SHA,
        "inputs_frozen": frozen,
        "n_registered": len(entries),
        "n_registered_loadable": len(registered),
        "n_scored": len(pvals),
        "n_added": len(added),
        "l3z_counts_only": l3z_counts_only,
        "skipped": skipped,
        "merged_away": sorted(merged_away),
        "merged_into": rep_of,
        "p_reproduction": {
            "n_checked": len(reproduced),
            "max_abs_diff": max((r["abs_diff"] for r in reproduced), default=0.0),
            "items": reproduced,
        },
        "stats": stats,
        "pvals": pvals,
        "p_order": keys_sorted,
        "variants": variants,
        "runs": runs,
        "merge": {
            "strict": merge_partition(sorted(registered), registered),
            "with_added": merge_partition(sorted({**registered, **added}), {**registered, **added}),
            "equal_value_pairs": equal_value_pairs(registered),
        },
        "added_meta": phasei_entries(),
        "added_rho": _added_rho(added),
    }


def _added_rho(added: Mapping[str, dp.MonthSeries]) -> dict:
    """R2 两条之间的相关性——描述性，**不用来合并**。

    它们共用同一份月度面板、只差「剔除不可成交标的」，所以同步性必然很高；
    但同步不等于同一个假设，这与 R1 拒绝用相关系数判定家族归属是同一个道理。
    """
    keys = sorted(added)
    if len(keys) != 2:
        return {}
    xs, ys, months = dp.align(added[keys[0]], added[keys[1]], 0)
    rho = dp.pearson(xs, ys)
    return {
        "a": keys[0],
        "b": keys[1],
        "n_overlap": len(months),
        "rho": rho,
        "identical_months": sum(1 for x, y in zip(xs, ys) if abs(x - y) <= EXACT_TOL),
    }


def harmonic(m: int) -> float:
    return mp.harmonic(m)
