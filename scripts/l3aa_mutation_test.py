#!/usr/bin/env python
"""L3aa 检查器的变异测试。

## 为什么这一步不能省

本项目铁律：**改检查器之前先跑变异测试**——故意注入已知缺陷，确认检查器真能检出。
历史上有三次「检查器自己写错、通过率是假的」：V2 交叉验证只比交集、
PC2 阈值放大成 2.02 倍、PC2 用 |ret| 把上涨算进除权量级。

## 六个变异体

| # | 注入的缺陷 | 期望 |
|---|---|---|
| M1 | BY 门槛丢掉 H_m（退化为 BH） | **被抓**（P1/P2 被反驳） |
| M2 | 门槛保留 H_m 但不乘 k（step-up 退化） | **被抓**（拒绝数掉到 4） |
| M3 | 只计名额的条目不占 m（m 从 24 变 20） | **被抓**（`BY_M_MISMATCH`） |
| M4 | 重叠率分母用 max(n) 而不是 min(n) | **被抓**（`OVERLAP_RATIO_NOT_ONE_ON_SUBSET`） |
| M5 | m_eff 直接返回 m（无视相关性） | **被抓**（`M_EFF_NOT_REDUCED`） |
| M6 | 相关性只取 lag 0，不做 ±1 平移 | **未抓到（预期内）** |

M6 单独说明：它改变的是**披露的量**（|ρ| 中位数从 0.41 掉到 0.12），
但 P6 的门槛是单侧 0.5，两种口径都在下方，所以结论不变。
闸门按设计只管「算得对不对」，不管「披露得多保守」，因此抓不到它属于预期行为，
不是检查器失效——这一条如实记为未抓到，不删。

用法：
    PYTHONPATH=backend/src python scripts/l3aa_mutation_test.py
退出码：全部符合期望 = 0，否则 1。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "l3aa_dependence_audit.py"
OUT = ROOT / "reports" / "gates" / "H-L3AA-DEPENDENCE" / "l3aa_mutation.json"


def _load_audit():
    spec = importlib.util.spec_from_file_location("l3aa_audit", AUDIT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _inputs(mod) -> dict:
    mod._check_frozen()
    from deepquant.gates import citation as ct

    return {
        "shas": {},
        "entries": ct.load_prereg(mod.L3U_PREREG)["entries"],
        "l3u": json.loads(mod.L3U_RESULT.read_text(encoding="utf-8")),
        "cards": json.loads(mod.CARDS.read_text(encoding="utf-8")),
        "l3z": json.loads(mod.L3Z_RESULT.read_text(encoding="utf-8")),
        "lag": json.loads(mod.LAG_SENS.read_text(encoding="utf-8")),
    }


def _verdict(mod, inputs) -> dict:
    res = mod.evaluate(inputs)
    gates = mod.check_gates(res)
    preds = mod.check_predictions(res)
    return {
        "n_by": len(res["by_m24"]["rejected"]),
        "by_set": sorted(res["by_m24"]["rejected"]),
        "m_used": res["by_m24"]["m"],
        "median_overlap": res["dependence"]["median_overlap_ratio"],
        "median_abs_rho": res["dependence"]["median_abs_rho"],
        "m_eff": res["m_eff"].get("m_eff"),
        "gate_codes": sorted({g["code"] for g in gates}),
        "refuted": sorted(p["id"] for p in preds if p["refuted"]),
    }


# --------------------------------------------------------------------------
# 变异体
# --------------------------------------------------------------------------


def m1_by_without_harmonic(mod) -> None:
    """BY 门槛丢掉 H_m —— 实际变成 BH。"""

    def broken(pvals, m, q=mod.mp.Q):
        return mod.mp.bh(pvals, q=q, m=m)

    mod.by_reject = broken


def m2_by_without_k_factor(mod) -> None:
    """保留 H_m 但不乘 k：step-up 退化成单门槛（比 Bonferroni 还严）。"""

    def broken(pvals, m, q=mod.mp.Q):
        thr = q / (m * mod.mp.harmonic(m))
        return [p <= thr for p in pvals]

    mod.by_reject = broken


def m3_counts_only_not_counted(mod) -> None:
    """只计名额的条目不再占 m。"""
    mod._effective_m = lambda m, n_scored: int(n_scored)


def m4_overlap_uses_max(mod) -> None:
    """重叠率分母写成两条里较长的那条。"""

    def broken(a, b):
        info = mod.dp.overlap(a, b)
        longer = max(a.n, b.n)
        return info["n_overlap"] / longer if longer else 0.0

    mod.overlap_ratio = broken


def m5_m_eff_ignores_correlation(mod) -> None:
    """m_eff 直接返回 m，等于宣称所有检验互相独立。"""

    def broken(corr):
        out = mod.dp.li_ji_m_eff(corr)
        return {**out, "m_eff": float(out["m"]), "mean_abs_offdiag": 0.30}

    mod.m_eff_calc = broken


def m6_rho_lag0_only(mod) -> None:
    """相关性只取 lag 0。"""

    def broken(a, b):
        return mod.dp.pairwise_dependence(a, b, shifts=(0,), min_overlap=mod.MIN_OVERLAP)

    mod.pair_rho = broken


CASES = [
    ("M1", "BY 丢掉 H_m（退化为 BH）", m1_by_without_harmonic, "caught"),
    ("M2", "step-up 丢掉 k 因子", m2_by_without_k_factor, "caught"),
    ("M3", "只计名额的条目不占 m", m3_counts_only_not_counted, "caught"),
    ("M4", "重叠率分母用 max(n)", m4_overlap_uses_max, "caught"),
    ("M5", "m_eff 无视相关性", m5_m_eff_ignores_correlation, "caught"),
    ("M6", "相关性只取 lag 0", m6_rho_lag0_only, "not_caught_expected"),
]


def main() -> int:
    mod = _load_audit()
    inputs = _inputs(mod)
    baseline = _verdict(mod, inputs)
    print("基线：", json.dumps(baseline, ensure_ascii=False))

    results = []
    ok = True
    for code, desc, fn, expectation in CASES:
        original = {name: getattr(mod, name) for name in (
            "by_reject", "_effective_m", "overlap_ratio", "m_eff_calc", "pair_rho"
        ) if hasattr(mod, name)}
        try:
            fn(mod)
            after = _verdict(mod, inputs)
        finally:
            for name, value in original.items():
                setattr(mod, name, value)

        changed = after != baseline
        caught = bool(after["gate_codes"]) or set(after["refuted"]) != set(baseline["refuted"])
        if expectation == "caught":
            satisfied = caught
        else:
            # 「未抓到」这一类只要求：闸门不响、也没有任何预测被改写。
            # 它是否改变了某个被披露的量（behavior_changed）单独记录——
            # 变了不算失败，因为闸门按设计不管「披露得多保守」。
            satisfied = not caught
        ok = ok and satisfied
        results.append(
            {
                "id": code,
                "defect": desc,
                "expectation": expectation,
                "caught": caught,
                "behavior_changed": changed,
                "gates_fired": after["gate_codes"],
                "refuted_after": after["refuted"],
                "n_by_after": after["n_by"],
                "m_used_after": after["m_used"],
                "satisfied": satisfied,
            }
        )
        mark = "OK" if satisfied else "FAIL"
        print(f"{code} {mark:4s} caught={caught} gates={after['gate_codes']} refuted={after['refuted']}")

    # 恢复后必须与基线完全一致（源码没被留下改动）
    restored = _verdict(mod, inputs)
    consistent = restored == baseline
    ok = ok and consistent

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "baseline": baseline,
                "mutations": results,
                "restored_matches_baseline": consistent,
                "all_satisfied": ok,
                "note": (
                    "M6 未抓到属预期：它只改变披露的相关性口径（|ρ| 中位数 0.41→0.12），"
                    "而 P6 是单侧阈值 0.5，两种口径都在阈值下方，结论不变。"
                    "闸门按设计只管算得对不对，不管披露得多保守。"
                ),
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n恢复后与基线一致：{consistent}")
    print(f"全部符合期望：{ok}")
    print(f"产物：{OUT.relative_to(ROOT).as_posix()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
