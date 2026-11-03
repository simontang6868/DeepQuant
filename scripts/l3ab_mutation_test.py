#!/usr/bin/env python
"""L3ab 检查器的变异测试。

本项目铁律：改检查器前先注入已知缺陷，确认检查器真能检出。历史上有三次
「检查器自己写错、通过率是假的」，所以这一步不能省。

六个变异体都作用在**实现函数**上（不是改期望值），每个对应一条可证伪的不变量：

| # | 注入的缺陷 | 应被哪条不变量抓住 |
|---|---|---|
| M1 | 同源判等改用「序列路径相同」 | 同源配对数必须恰为 5（按路径判等会得 0） |
| M2 | 子窗口只报一个超集 | `phasep_late` 必须报出两个超集 |
| M3 | 复现 p 改用正态近似而非 t 分布 | 与 L3z 的 p 必须一致到 1e-9 |
| M4 | 校正不补位（m 退化成条目数） | 各口径的 m 必须等于声明值 |
| M5 | 覆盖率把只读审计也算成缺口 | 实质缺口必须恰为 1 |
| M6 | 经济属性一律判成「净收益」 | 价差类不得判成净收益 |

M1 不是虚构的缺陷：L3z 审计里真的犯过——按 `series` 路径去重得到 27（等于没去重）。
所以这条变异体是在重放一个已发生过的错误，而不是编一个不可能的错误。

用法：
    PYTHONPATH=backend/src python scripts/l3ab_mutation_test.py
退出码：全部抓到 = 0。
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AB-FAMILY" / "l3ab_mutation.json"

MUTABLE = ("exact_pairs", "subwindow_relations", "reproduce_from_series",
           "apply_corrections_at_m", "coverage_gap")


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "l3ab_audit_mod", ROOT / "scripts" / "l3ab_family_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _invariants(audit_mod) -> dict:
    res = audit_mod.collect()
    rows = [r for r in res["repro"] if r["match"] is not None]
    return {
        "n_pairs": len(res["exact_pairs"]),
        "phasep_supersets": res["subwindow"].get("phasep_late"),
        "worst_repro": max((r["worst"] for r in rows), default=float("nan")),
        "spec_m": {s["id"]: s["m"] for s in res["specs"]},
        "declared_m": {s["id"]: s["m"] for s in res["decisions"]["m_specs"]},
        "coverage_gap": res["coverage"]["n_hypothesis_unregistered"],
    }


def _m_matches_declared(baseline: dict, after: dict) -> bool:
    """m 必须等于声明值；S4 的声明是 computed，以基线的实测簇数为准。"""
    for spec_id, declared in baseline["declared_m"].items():
        want = baseline["spec_m"][spec_id] if declared == "computed" else declared
        if after["spec_m"].get(spec_id) != want:
            return False
    return True


def main() -> int:
    from deepquant.gates import current_sheet as cs
    from deepquant.gates import family as fam

    audit = _load_audit()
    baseline = _invariants(audit)
    print("基线：", json.dumps(baseline, ensure_ascii=False, default=str))
    print("")

    def m1() -> None:
        """按序列路径判等——L3z 审计真实发生过这个错误。"""

        def by_path(series):
            pairs = []
            keys = sorted(series)
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    if series[a].path == series[b].path:
                        pairs.append({"a": a, "b": b, "n_common": 0,
                                      "n_a": series[a].n, "n_b": series[b].n,
                                      "max_abs_diff": 0.0, "identical": True})
            return pairs

        fam.exact_pairs = by_path

    def m2() -> None:
        original = fam.subwindow_relations

        def single(series, pairs):
            return {k: v[0] for k, v in original(series, pairs).items()}

        fam.subwindow_relations = single

    def m3() -> None:
        original = fam.reproduce_from_series

        def normal_p(values, lag=3):
            out = dict(original(values, lag))
            t = out["t"]
            out["p"] = math.erfc(abs(t) / math.sqrt(2.0)) if t == t else float("nan")
            return out

        fam.reproduce_from_series = normal_p

    def m4() -> None:
        def no_pad(pvals, *, m, alpha=0.05, q=0.05):
            n = len(pvals)
            return {
                "m": n,
                "n_padded": 0,
                "bonferroni": fam.mp.bonferroni(pvals, alpha=alpha, m=n),
                "holm": fam.mp.holm(pvals, alpha=alpha, m=n),
                "bh": fam.mp.bh(pvals, q=q, m=n),
                "by": fam.mp.benjamini_yekutieli(pvals, q=q, m=n),
                "bonferroni_threshold": alpha / n,
                "by_unit_threshold": fam.mp.by_unit_threshold(q=q, m=n),
            }

        fam.apply_corrections_at_m = no_pad

    def m5() -> None:
        original = fam.coverage_gap

        def naive(root, entries):
            out = original(root, entries)
            out["n_hypothesis_unregistered"] = sum(
                1 for u in out["unregistered"] if u["n_summary"] > 0
            )
            return out

        fam.coverage_gap = naive

    checks = [
        ("M1", "同源判等改用序列路径", m1, lambda b, a: a["n_pairs"] == 5),
        ("M2", "子窗口只报一个超集", m2,
         lambda b, a: a["phasep_supersets"] == ["phaseo_netBASE", "phasev_net"]),
        ("M3", "复现 p 改用正态近似", m3, lambda b, a: a["worst_repro"] <= 1e-9),
        ("M4", "校正不补位（m 退化）", m4, lambda b, a: _m_matches_declared(b, a)),
        ("M5", "覆盖率把只读审计算成缺口", m5, lambda b, a: a["coverage_gap"] == 1),
    ]

    results = []
    for code, desc, mutate, invariant in checks:
        saved = {name: getattr(fam, name) for name in MUTABLE}
        try:
            mutate()
            after = _invariants(audit)
            held = bool(invariant(baseline, after))
        finally:
            for name, value in saved.items():
                setattr(fam, name, value)
        caught = not held
        results.append(
            {"id": code, "defect": desc, "caught": caught,
             "invariant_held_after_mutation": held,
             "observed_after": {k: after[k] for k in
                                ("n_pairs", "phasep_supersets", "worst_repro", "coverage_gap")}}
        )
        print(f"{code} {'抓到' if caught else '**未抓到**'}  {desc}")

    saved_basis = cs.economic_basis
    try:
        cs.economic_basis = lambda key: "net_return"
        m6_held = cs.economic_basis("phasec_d") == "cross_section_spread"
    finally:
        cs.economic_basis = saved_basis
    results.append(
        {"id": "M6", "defect": "经济属性一律判成净收益",
         "caught": not m6_held, "invariant_held_after_mutation": m6_held,
         "observed_after": {"phasec_d_basis": cs.economic_basis("phasec_d")}}
    )
    print(f"M6 {'抓到' if not m6_held else '**未抓到**'}  经济属性一律判成净收益")

    restored = _invariants(audit)
    consistent = restored == baseline
    ok = all(r["caught"] for r in results) and consistent

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "baseline": baseline,
                "mutations": results,
                "restored_matches_baseline": consistent,
                "all_caught": ok,
                "note": ("变异体都作用在实现函数上，不是改期望值。"
                         "M1 是重放 L3z 审计真实发生过的缺陷（按 series 路径去重得 27）。"
                         "任一条未抓到都说明对应不变量太松，须补例子。"),
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n恢复后与基线一致：{consistent}")
    print(f"全部抓到：{ok}")
    print(f"产物：{OUT.relative_to(ROOT).as_posix()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
