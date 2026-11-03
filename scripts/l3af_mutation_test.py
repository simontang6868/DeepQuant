#!/usr/bin/env python
"""L3af 变异测试：验证闸门**真的能抓**。

原则（沿用 L3ac/L3ae 的教训）：
1. 变异必须**真的改变行为**，不能只是改了个没人读的常量；
2. 每条变异都要写清「期望被谁抓到」；
3. **抓不到也要记**，并写清原因——抓不到本身就是关于闸门强度的证据。

用法：
    PYTHONPATH=backend/src python scripts/l3af_mutation_test.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "src"))

from deepquant.gates import cost_net as cn  # noqa: E402
from deepquant.gates import evidence as ev  # noqa: E402
from deepquant.gates import family_af as fa  # noqa: E402
from deepquant.gates import multiplicity as mp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AF-COST-NET" / "l3af_mutation.json"


def codes_of(gates) -> set[str]:
    return {g["code"] for g in gates}


def run_mutations(cost: dict, family: dict, evid: dict, prereg: dict) -> list[dict]:
    out: list[dict] = []

    def record(mid, title, target, expect_hit, hit, detail):
        out.append({
            "id": mid, "title": title, "expect_caught_by": target,
            "caught": bool(hit), "expected": expect_hit, "detail": detail,
        })

    # M1：成本按双边换手算（×2）
    orig = cn.cost_pp
    cn.cost_pp = lambda to, rate: 2.0 * orig(to, rate)
    try:
        bad = cn.build(ROOT)
        codes = codes_of(bad["gates"])
        record("M1", "把单边换手当双边（成本 ×2）", "G2_COST_ARITHMETIC",
               True, "G2_COST_ARITHMETIC" in codes, f"命中 {sorted(codes)}")
    finally:
        cn.cost_pp = orig

    # M2：t 用毛收益的 se 当分母（L3ae 的做法）
    bad = copy.deepcopy(cost)
    t0 = bad["tiers"][0]
    t0["net"]["t_iid"] = t0["net"]["mean_pp"] / cost["gross"]["se_iid_pp"]
    codes = codes_of(cn.gate_codes(bad, prereg))
    record("M2", "净收益 t 复用毛收益标准误", "G6_SE_FROM_NET",
           True, "G6_SE_FROM_NET" in codes, f"命中 {sorted(codes)}")

    # M3：月份少一个月（错位/丢月）
    bad = copy.deepcopy(cost)
    bad["tiers"][2]["net"]["n"] = bad["n_months"] - 1
    codes = codes_of(cn.gate_codes(bad, prereg))
    record("M3", "某一档少一个月（月份未对齐）", "G4_MONTH_ALIGNMENT",
           True, "G4_MONTH_ALIGNMENT" in codes, f"命中 {sorted(codes)}")

    # M4：把逐月成本换成常数（L3ae 的构造）——闸门不响，但 P6 必须被反驳
    bad = copy.deepcopy(cost)
    for t in bad["tiers"]:
        t["variance"]["se_net_over_gross"] = 1.0
        t["variance"]["se_net_ge_gross"] = True
    claim = cn.summarize_se_claim(bad)
    record("M4", "把成本当逐月常数（se_net 恒等于 se_gross）",
           "summarize_se_claim 的 TIER_DEPENDENT",
           True, claim["code"] != "TIER_DEPENDENT", f"判为 {claim['code']}")

    # M5：m 算术写死一个错值
    bad = copy.deepcopy(family)
    bad["m_arithmetic"]["recomputed"] = 24
    codes = codes_of(fa.gate_codes(bad, {"p_order": [r["id"] for r in family["registry"]]}))
    record("M5", "m 算术写死 24（与成员数不符）", "G9_FAMILY_CONSISTENCY",
           True, "G9_FAMILY_CONSISTENCY" in codes, f"命中 {sorted(codes)}")

    # M6：BY 漏乘调和数 H_m（把 multiplicity 的 BY 实现换成 BH）——在**真实数据**上做
    keys = list(family["runs"]["IND_m25"]["adjusted_p"])
    p_list = [_orig_p(family, k) for k in keys]
    orig_by = mp.benjamini_yekutieli
    mp.benjamini_yekutieli = lambda pvals, q=0.05, m=None: mp.bh(pvals, q=q, m=m)
    try:
        res = fa.apply_all(p_list, 25)
        chk = fa.cross_check(p_list, 25, res)
    finally:
        mp.benjamini_yekutieli = orig_by
    # 这条的 expected 故意写 False：**预测抓不到**，并说明为什么。
    # 若它在某天变成 True（数据换了、BH 与 BY 分裂），脚本会报「与预期不符」提醒复核——
    # 这是刻意的双向告警，不是笔误。
    record("M6", "BY 漏乘 H_m（真实数据）→ 预期抓不到", "cross_check 的 by 不一致",
           False, not chk["by"]["agree"],
           f"by 一致={chk['by']['agree']}；本数据上 BH 与 BY 的拒绝集合恰好相同 ⇒ 抓不到。"
           "这不是闸门弱，是**数据不给区分力**（L3ac M6 完全同型）")

    # M6b：同一缺陷，换成 BH 与 BY 必然分裂的合成数据
    p_syn = [0.00024 * k for k in range(1, 13)] + [0.9] * 38
    mp.benjamini_yekutieli = lambda pvals, q=0.05, m=None: mp.bh(pvals, q=q, m=m)
    try:
        res_syn = fa.apply_all(p_syn, 50)
        chk_syn = fa.cross_check(p_syn, 50, res_syn)
    finally:
        mp.benjamini_yekutieli = orig_by
    record("M6b", "BY 漏乘 H_m（合成数据，BH≠BY）", "cross_check 的 by 不一致",
           True, not chk_syn["by"]["agree"],
           f"by 一致={chk_syn['by']['agree']}（mine 拒 {sum(res_syn['by']['rejected'])} 条，"
           f"ref 拒 {chk_syn['by']['n_ref']} 条）")

    # M7：把没有净序列的条目升级成「有成本后证据」
    bad = copy.deepcopy(evid)
    for c in bad["cards"]:
        if c["statistics"]["S3"]["status"] == "ABSENT":
            c["has_cost_evidence"] = True
            c["statistics"]["S3"]["status"] = "PASS"
            c["statistics"]["S3"]["path"] = "invented"
            break
    codes = codes_of(ev.gate_codes(bad, family))
    record("M7", "无净序列的条目被标为成本后证据", "G10_NO_AUTO_PROMOTION",
           True, "G10_NO_AUTO_PROMOTION" in codes, f"命中 {sorted(codes)}")

    # M8：phasei_v0 借 V1 的换手凑出 PASS（tiers=None 的 PASS）
    bad = copy.deepcopy(evid)
    c0 = next(c for c in bad["cards"] if c["id"] == "phasei_v0")
    c0["statistics"]["S3"] = {"status": "PASS", "path": "borrowed_v1_turnover",
                              "note": "borrowed", "tiers": None}
    c0["has_cost_evidence"] = True
    codes = codes_of(ev.gate_codes(bad, family))
    record("M8", "phasei_v0 借用 V1 换手并直接判 PASS", "G10_NO_AUTO_PROMOTION",
           True, "G10_NO_AUTO_PROMOTION" in codes, f"命中 {sorted(codes)}")

    # M9：常数成本的浮点伪影（Va=0 时 se 比值应为 1）
    dec = cn.variance_decomposition([1.0, -2.0, 3.0, 0.5, -1.0, 2.0], [0.1] * 6)
    record("M9", "常数成本时 Var(c) 应为精确 0", "variance_decomposition 的 var_cost==0",
           True, dec["var_cost"] == 0.0, f"var_cost={dec['var_cost']!r}")
    return out


def _orig_p(family: dict, key: str) -> float:
    """从 registry 取原始 p（adjusted_p 里的 bonferroni 已校正，不能当原始 p）。"""
    for r in family["registry"]:
        if r["id"] == key:
            return float(r["stat"]["p"])
    raise KeyError(key)


def main() -> int:
    prereg = cn.load_prereg(ROOT)
    cost = cn.build(ROOT)
    family = fa.build(ROOT)
    evid = ev.build(ROOT, cost=cost, family=family)
    rows = run_mutations(cost, family, evid, prereg)

    print(f"{'ID':<4}{'变异':<38}{'期望抓到':<34}{'结果'}")
    print("-" * 100)
    bad = 0
    for r in rows:
        ok = r["caught"] == r["expected"]
        bad += 0 if ok else 1
        print(f"{r['id']:<4}{r['title'][:36]:<38}{r['expect_caught_by'][:32]:<34}"
              f"{'抓到了' if r['caught'] else '**没抓到**'}{'' if ok else '  ← 与预期不符'}")

    # 反向对照：不施加任何变异时，闸门必须一条都不报
    clean = {
        "cost": len(cost["gates"]),
        "family": len(family["gates"]),
        "evidence": len(evid["gates"]),
    }
    print(f"\n反向对照（未变异）：cost={clean['cost']} family={clean['family']} "
          f"evidence={clean['evidence']} 条违规")

    payload = {
        "experiment_id": "H-L3AF-MUTATION",
        "prereg_sha256": cost["prereg_sha256"],
        "n_mutations": len(rows),
        "n_caught_as_expected": len(rows) - bad,
        "n_mismatch": bad,
        "negative_control": clean,
        "mutations": rows,
        "notes": [
            "M4 的期望不是「闸门报错」而是「结论发生变化」：常数成本不会违反任何恒等式，",
            "它只会让 se_net 恰好等于 se_gross ——所以能不能抓到只取决于有没有把四个方差分量算出来。",
            "这就是本阶段不用「多写几条断言」而用「多算几个分量」的理由。",
            "M6 未抓到，原因是真实数据上 BH 与 BY 的拒绝集合恰好相同 —— 两法一致不能当作实现正确的证据，"
            "所以另加 M6b 用合成数据把两者分开；M6b 抓到即证明互校机制本身有效。",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"\n[产物] {OUT.relative_to(ROOT)}")
    return 0 if bad == 0 and sum(clean.values()) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
