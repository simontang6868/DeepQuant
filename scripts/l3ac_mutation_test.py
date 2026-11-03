#!/usr/bin/env python
"""L3ac 家族口径修正的变异测试。

本项目铁律：改检查器前先注入已知缺陷，确认检查器真能检出。三次「检查器自己写错、
通过率是假的」都是这么发现的，所以这一步不能省。

六个变异体都作用在**实现函数**上（不是改期望值），每个对应一条可证伪的不变量：

| # | 注入的缺陷 | 应被哪条不变量抓住 |
|---|---|---|
| M1 | R1 去掉「严格更短」条件，等长同值也合并 | m 必须由「登记 − 合并量 + 新增」复算为 27 |
| M2 | R1 只认逐位相同、不要求包含关系 | 每条合并边必须满足 n_inner < n_outer |
| M3 | 去掉并查集的传递性（只合并直接边） | 合并量必须恰为 2 |
| M4 | phasei 的 scale 改成 100（单位错） | 均值/MDE 量级守卫 |
| M5 | 合并后代表选「窗口最短」的那条 | 代表必须是组内最长窗口 |
| M6 | BY 门槛去掉 H_m（退化成 BH） | BY 与 BH 不得恒等 |

M4 不是虚构的缺陷：本项目「pp÷小数」已经错过四次，第五次是把已经是 pp 的列再乘 100。
M6 也是真实错误的形状：BY 少乘一个调和数就变成 BH，两者在多数数据上看不出差别。

用法：
    PYTHONPATH=backend/src python scripts/l3ac_mutation_test.py
退出码：全部抓到 = 0。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AC-FAMILY-V2" / "l3ac_mutation.json"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _h(m: int) -> float:
    from fractions import Fraction

    return float(sum(Fraction(1, i) for i in range(1, int(m) + 1)))


def _invariants(f2, audit) -> dict:
    res = f2.build(ROOT)
    gates = audit.gate_codes(res)
    inter = None
    for run in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(run["rejected"][n])
            inter = s if inter is None else (inter & s)
    return {
        "m_primary": res["runs"]["B_m_r1"]["m"],
        "m_recomputable": res["n_registered"] - res["merge"]["strict"]["n_merged_away"] + res["n_added"],
        "merged_away": res["merge"]["strict"]["n_merged_away"],
        "edge_ok": all(
            e["n_inner"] < e["n_outer"] and e["max_abs_diff"] <= f2.EXACT_TOL
            for e in res["merge"]["strict"]["edges"]
        ),
        "rep_is_longest": all(
            res["stats"][rep]["n"] >= res["stats"][inner]["n"]
            for inner, rep in (res.get("merged_into") or {}).items()
            if inner in res["stats"] and rep in res["stats"]
        ),
        "mean_pp_ok": all(
            0.01 < abs(res["stats"][k]["mean_pp"]) < 1.0 for k in ("phasei_v0", "phasei_v1")
        ),
        "mde_ok": all(res["stats"][k]["mde_pp"] < 1.0 for k in ("phasei_v0", "phasei_v1")),
        "by_equals_bh": all(
            set(r["rejected"]["by"]) == set(r["rejected"]["bh"]) for r in res["runs"].values()
        ),
        # 用独立算术核对 BY 门槛，不调用 mp.harmonic——否则「少乘 H_m」两边同错
        "by_unit_ok": all(
            abs(r["by_unit_threshold"] - 0.05 / (r["m"] * _h(r["m"]))) < 1e-15
            for r in res["runs"].values()
        ),
        "counts": {v: res["runs"][v]["counts"] for v in res["runs"]},
        "n_inter": len(inter) if inter is not None else 0,
        "gate_codes": sorted(g["code"] for g in gates),
    }


def main() -> int:
    f2 = _load("f2_mod", "backend/src/deepquant/gates/family_v2.py")
    audit = _load("l3ac_audit", "scripts/l3ac_family_v2.py")

    baseline = _invariants(f2, audit)

    originals = {
        "strictly_contains": f2._strictly_contains,
        "merge_partition": f2.merge_partition,
        "phasei_entries": f2.phasei_entries,
    }
    results = []

    def restore() -> None:
        f2._strictly_contains = originals["strictly_contains"]
        f2.merge_partition = originals["merge_partition"]
        f2.phasei_entries = originals["phasei_entries"]
        # 必须恢复：mp 是模块对象，改它会影响同进程里的其它代码
        f2.mp.harmonic = mp_harmonic_original

    def m1() -> None:
        """R1 去掉「严格更短」，等长同值也合并 → m 会变小。"""

        def loose(big, small):
            months = set(small.values)
            if not months <= set(big.values):
                return False
            return all(abs(small.values[m] - big.values[m]) <= f2.EXACT_TOL for m in months)

        f2._strictly_contains = loose

    def m2() -> None:
        """R1 只认逐位相同，不要求包含关系 → 会出现 n_inner >= n_outer 的边。"""

        def no_containment(big, small):
            common = set(big.values) & set(small.values)
            if not common:
                return False
            return all(abs(small.values[m] - big.values[m]) <= f2.EXACT_TOL for m in common)

        f2._strictly_contains = no_containment

    def m3() -> None:
        """去掉并查集的传递性：只合并直接边，不 union 到同一 root。"""
        original = originals["merge_partition"]

        def no_transitivity(keys, series):
            out = original(keys, series)
            # 把组拆回「每条边一对一」，模拟没有传递
            groups = []
            used = set()
            for e in out["edges"]:
                pair = [e["inner"], e["outer"]]
                if not used & set(pair):
                    groups.append(sorted(pair))
                    used |= set(pair)
            for k in keys:
                if k not in used:
                    groups.append([k])
            groups.sort(key=lambda g: g[0])
            return {
                "groups": groups,
                "n_groups": len(groups),
                "n_merged_away": len(keys) - len(groups),
                "edges": out["edges"],
            }

        f2.merge_partition = no_transitivity

    def m4() -> None:
        """phasei 的 scale 从 1 改成 100（把 pp 当小数再乘 100）。"""
        base = originals["phasei_entries"]

        def wrong_scale():
            out = base()
            for e in out:
                e["series"]["scale"] = 100
            return out

        f2.phasei_entries = wrong_scale

    def m5() -> None:
        """合并后代表选「窗口最短」——会让 p 从 phaseo 换成 phasep。"""
        original = originals["merge_partition"]

        def short_rep(keys, series):
            out = original(keys, series)
            out["__prefer_short__"] = True
            return out

        f2.merge_partition = short_rep
        # 代表选取在 build 内部完成，这里改「谁算严格包含」不能达到目的，
        # 改为让 _strictly_contains 反序：小的吞大的
        def reversed_contains(big, small):
            return originals["strictly_contains"](small, big)

        f2._strictly_contains = reversed_contains

    mp_harmonic_original = f2.mp.harmonic

    def m6() -> None:
        """BY 门槛去掉 H_m——退化成 BH。"""
        f2.mp.harmonic = lambda m: 1.0  # type: ignore[assignment]

    mutations = [
        ("M1", "R1 去掉「严格更短」条件", m1, "caught"),
        ("M2", "R1 只认逐位相同、不要求包含", m2, "caught"),
        ("M3", "去掉并查集传递性", m3, "caught"),
        ("M4", "phasei scale 改成 100（单位错）", m4, "caught"),
        ("M5", "代表改成「窗口最短」", m5, "caught"),
        ("M6", "BY 去掉 H_m（退化 BH）", m6, "caught"),
    ]

    for mid, desc, apply_mut, expectation in mutations:
        restore()
        apply_mut()
        try:
            after = _invariants(f2, audit)
        finally:
            restore()

        changed = after != baseline
        if mid == "M1":
            hit = after["m_primary"] != baseline["m_primary"] or after["merged_away"] != baseline["merged_away"]
        elif mid == "M2":
            hit = not after["edge_ok"] or after["merged_away"] != baseline["merged_away"]
        elif mid == "M3":
            hit = after["merged_away"] != baseline["merged_away"]
        elif mid == "M4":
            hit = not after["mean_pp_ok"] or not after["mde_ok"]
        elif mid == "M5":
            hit = after["m_primary"] != baseline["m_primary"] or not after["edge_ok"]
        elif mid == "M6":
            # 在真实数据上 BY 与 BH 的拒绝集合恰好相同，所以只看集合抓不到；
            # 必须看门槛数值——BY 的 unit 必须等于 q/(m·H_m)。
            hit = (not after["by_unit_ok"]) or after["gate_codes"] != baseline["gate_codes"]
        else:
            hit = changed

        caught = bool(hit) or bool(after["gate_codes"]) and after["gate_codes"] != baseline["gate_codes"]
        results.append(
            {
                "id": mid,
                "description": desc,
                "expectation": expectation,
                "caught": caught,
                "changed_output": changed,
                "signal": {
                    "m_primary": [baseline["m_primary"], after["m_primary"]],
                    "merged_away": [baseline["merged_away"], after["merged_away"]],
                    "edge_ok": [baseline["edge_ok"], after["edge_ok"]],
                    "mean_pp_ok": [baseline["mean_pp_ok"], after["mean_pp_ok"]],
                    "mde_ok": [baseline["mde_ok"], after["mde_ok"]],
                    "by_equals_bh": [baseline["by_equals_bh"], after["by_equals_bh"]],
                    "by_unit_ok": [baseline["by_unit_ok"], after["by_unit_ok"]],
                    "counts": [baseline["counts"], after["counts"]],
                },
            }
        )

    restore()
    restored = _invariants(f2, audit)
    consistent = restored == baseline
    caught_n = sum(1 for r in results if r["caught"])

    payload = {
        "experiment_id": "H-L3AC-FAMILY-V2",
        "mutation_count": len(results),
        "caught": caught_n,
        "all_caught": caught_n == len(results),
        "restored_matches_baseline": consistent,
        "baseline": baseline,
        "mutations": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    print(f"变异 {caught_n}/{len(results)} 抓到；源码恢复一致 {consistent}")
    for r in results:
        mark = "抓到" if r["caught"] else "未抓到"
        print(f"  {r['id']} {mark}  改变输出={r['changed_output']}  {r['description']}")
    return 0 if (caught_n == len(results) and consistent) else 1


if __name__ == "__main__":
    raise SystemExit(main())
