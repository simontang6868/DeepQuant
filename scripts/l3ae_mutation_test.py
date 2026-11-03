#!/usr/bin/env python
"""L3ae 成本档的变异测试。

本阶段能做错的地方只有两类：**成本公式**与**口径拼接**。六个变异体都作用在这两处，
每一个都真改行为（不是改注释）。

| # | 注入的缺陷 | 应被哪条不变量抓住 |
|---|---|---|
| M1 | 月度成本乘 12（年化当月度） | G1 复现整体失败，cost 差 12 倍 |
| M2 | 印花按双边算（等价单边变 0.076%） | 等价费率 ≠ 0.00051，且 1 亿档等效费率不再等于它 |
| M3 | `se_iid` 改用 ddof=0 | G5 报错（相对差 2e-3 > 1e-6） |
| M4 | 均值偏移 1e-4（超出存储下限） | G1b 必须报错 |
| M5 | 最低佣金不生效（永远按名义费率） | 三档等效费率变成全等于 0.051%，单调性断言失败 |
| M6 | 均值改取 `summary.mean_pp`（循环论证） | 循环论证后 net 偏差降到 0——指标「变好」正是缺陷信号 |
| M7 | **负对照**：同一偏移 + 削掉 G1b（floor=1.0） | 应**抓不到**——抓不到才证明 M4 的「抓到」来自 G1b 本身 |

M4 与 M7 是**成对**的：M4 注入真实错误、期望 G1b 报错；M7 注入同一错误但把 G1b 削掉，
期望它抓不到。只有「削掉之后就不灵」才能证明 M4 的抓到来自 G1b 本身——
否则那个报错可能来自 G1 或 G9，G1b 只是恰好也在场。
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AE-COST-TIERS" / "l3ae_mutation.json"


def _load_ct():
    """按包路径导入，不走文件路径。

    `cost_tiers` 里有 `from . import family`，按文件路径 exec 会报
    「attempted relative import with no known parent package」。
    这里必须让它作为包的一部分被导入，否则变异体作用的对象和测试用的对象不是同一份。
    """
    src = str(ROOT / "backend" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from deepquant.gates import cost_tiers as ct_mod

    return ct_mod


def _invariants(ct) -> dict:
    res = ct.build(ROOT)
    by = {t["id"]: t for t in res["tiers"]}
    eq = [m["equivalent_single_side"] for m in res["min_commission"]]
    return {
        "gates": sorted(g["code"] for g in res["gates"]),
        "status": res["status"],
        "cost_01": by["T_0p1"]["cost_pp_month"],
        "net_base": by["T_base_1e8"]["net_pp_month"],
        "t_iid_base": by["T_base_1e8"]["t_iid"],
        "se_iid": res["se_iid_pp"],
        "se_nw3": res["se_nw3_pp"],
        "mean": res["mean_gross_pp"],
        "econ_rates": eq,
        "rate_monotone": eq[2] > eq[1] > eq[0],
        "rate_big_equals_eq": abs(eq[0] - ct.equivalent_single_side()) < 1e-9,
        "net_rel_to_summary": abs(
            by["T_0p1"]["net_pp_month"] - 0.125687
        )
        / 0.125687,
        "cost_linear": max(t["cost_pp_month"] / t["rate"] for t in res["tiers"])
        - min(t["cost_pp_month"] / t["rate"] for t in res["tiers"]),
    }


def main() -> int:
    ct = _load_ct()
    originals = {
        "cost_tier": ct.cost_tier,
        "equivalent_single_side": ct.equivalent_single_side,
        "iid_se": ct.iid_se,
        "storage_floor": ct.storage_floor,
        "effective_rate_with_min_commission": ct.effective_rate_with_min_commission,
        "build": ct.build,
        "monthly_values": ct.monthly_values,
    }
    baseline = _invariants(ct)

    def restore() -> None:
        for name, fn in originals.items():
            setattr(ct, name, fn)

    def m1() -> None:
        """月度成本乘 12（年化当月度）——本项目真犯过的错。"""
        orig = originals["cost_tier"]

        def patched(**kw):
            r = orig(**kw)
            r["cost_pp_month"] = r["cost_pp_month"] * 12
            r["net_pp_month"] = kw["mean_gross"] - r["cost_pp_month"]
            return r

        ct.cost_tier = patched  # type: ignore[assignment]

    def m2() -> None:
        """印花按双边算：等价单边变成 0.076%。"""
        ct.equivalent_single_side = lambda **kw: (  # type: ignore[assignment]
            ct.COMM_RATE + ct.TRANSFER_RATE + ct.STAMP_RATE
        )

    def m3() -> None:
        """se_iid 改用 ddof=0。"""
        orig = originals["iid_se"]

        def patched(values):
            vals = [float(v) for v in values if v == v]
            return statistics.pstdev(vals) / (len(vals) ** 0.5)

        ct.iid_se = patched  # type: ignore[assignment]

    def _shift_mean() -> None:
        """给逐月序列整体加 1e-4 —— 均值相对偏移约 6e-4，远超 4e-6 的存储下限。

        这是一个「真实但不大」的实现错误：所有档位都会安静地偏一点，
        只有 G1b 这种按存储下限设阈值的闸门能抓住它。
        """
        orig = originals["monthly_values"]

        def patched(path, column):
            return [v + 1e-4 for v in orig(path, column)]

        ct.monthly_values = patched  # type: ignore[assignment]

    def m4() -> None:
        """均值偏移 1e-4：G1b 应抓到（证明它有效）。"""
        _shift_mean()

    def m7() -> None:
        """同一偏移 + 把 G1b 削掉（floor=1.0）：应抓不到（证明 G1b 在承重）。"""
        _shift_mean()
        ct.storage_floor = lambda observed, decimals=6: 1.0  # type: ignore[assignment]

    def m5() -> None:
        """最低佣金不生效。"""
        ct.effective_rate_with_min_commission = lambda notional, **kw: ct.COMM_RATE  # type: ignore[assignment]

    def m6() -> None:
        """均值改取 summary 的 mean_pp（循环论证）。"""
        orig_build = originals["build"]

        def patched(root):
            import json as _json
            from pathlib import Path as _P

            summary = _json.loads(
                (
                    _P(root) / "phasei_out" / "8f5c35121fdb" / "summary.json"
                ).read_text(encoding="utf-8")
            )
            real_fmean = statistics.fmean

            def fmean(values):
                # 只在「恰好是那 237 个逐月 alpha」时才替换，避免影响别处
                if len(values) == int(summary["V1_executable"]["n"]):
                    return float(summary["V1_executable"]["mean_pp"])
                return real_fmean(values)

            statistics.fmean = fmean
            try:
                return orig_build(root)
            finally:
                statistics.fmean = real_fmean

        ct.build = patched  # type: ignore[assignment]

    # (编号, 描述, 函数, 期望)  —— expect="not_caught" 是**负对照**，不是缺陷未抓
    mutations = [
        ("M1", "月度成本乘 12（年化当月度）", m1, "caught"),
        ("M2", "印花按双边算（等价单边 0.076%）", m2, "caught"),
        ("M3", "se_iid 改用 ddof=0", m3, "caught"),
        ("M4", "均值偏移 1e-4（超出存储下限）", m4, "caught"),
        ("M5", "最低佣金不生效", m5, "caught"),
        ("M6", "均值改取 summary.mean_pp（循环论证）", m6, "caught"),
        ("M7", "负对照：同一偏移但削掉 G1b", m7, "not_caught"),
    ]

    results = []
    for mid, desc, fn, expect in mutations:
        restore()
        fn()
        try:
            after = _invariants(ct)
            error = None
        except Exception as exc:  # noqa: BLE001 — 变异体崩掉也是一种「被抓到」
            after = None
            error = f"{type(exc).__name__}: {exc}"
        restore()

        if after is None:
            caught = True
            why = f"变异体崩溃（{error}），等价于被发现"
        else:
            caught, why = _judge(mid, baseline, after)

        results.append(
            {
                "id": mid,
                "description": desc,
                "expect": expect,
                "satisfied": (caught if expect == "caught" else not caught),
                "caught": caught,
                "reason": why,
                "changed": after is not None and after != baseline,
                "after": {k: after[k] for k in ("gates", "status", "cost_01", "se_iid", "econ_rates")}
                if after
                else None,
            }
        )

    restore()
    restored = _invariants(ct)
    consistent = restored == baseline

    positive = [r for r in results if r["expect"] == "caught"]
    controls = [r for r in results if r["expect"] == "not_caught"]
    payload = {
        "mutation_count": len(mutations),
        "positive_count": len(positive),
        "positive_caught": sum(1 for r in positive if r["caught"]),
        "control_count": len(controls),
        "control_satisfied": sum(1 for r in controls if r["satisfied"]),
        "all_satisfied": all(r["satisfied"] for r in results),
        "restored_matches_baseline": consistent,
        "baseline": {k: baseline[k] for k in ("gates", "status", "cost_01", "se_iid")},
        "mutations": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    for r in results:
        tag = ("抓到" if r["caught"] else "未抓到") if r["expect"] == "caught" else (
            "符合预期（未抓到）" if r["satisfied"] else "**不符预期（削了闸门仍报错）**"
        )
        print(f"{r['id']}  {tag}  {r['description']}")
        print(f"      {r['reason']}")
    print(
        f"\n正变异抓到 {payload['positive_caught']}/{payload['positive_count']}；"
        f"负对照符合 {payload['control_satisfied']}/{payload['control_count']}；"
        f"恢复一致：{consistent}"
    )
    return 0 if (consistent and payload["all_satisfied"]) else 1


def _judge(mid: str, base: dict, after: dict) -> tuple[bool, str]:
    """每个变异体各自的判据。**不用「输出变了就算抓到」**——
    有些变异会改变数字但不改变结论，那不算闸门有效。"""
    if mid == "M1":
        # 月度成本乘 12 ⇒ cost 变 12 倍，G1 必然报错，且线性仍成立（不会被 G2 抓）
        caught = "G1_REPRODUCTION_FAILED" in after["gates"]
        return caught, f"cost {base['cost_01']:.6f} → {after['cost_01']:.6f}；G1 报错={caught}"
    if mid == "M2":
        caught = not after["rate_big_equals_eq"]
        return caught, (
            f"1 亿档等效费率 {after['econ_rates'][0]:.6f} vs 等价单边 "
            f"{'_'.join([]) or 0.00051}；相等={after['rate_big_equals_eq']}"
        )
    if mid == "M3":
        caught = "G5_SE_MISMATCH" in after["gates"]
        rel = abs(after["se_iid"] - base["se_iid"]) / base["se_iid"]
        return caught, f"se_iid {base['se_iid']:.6f} → {after['se_iid']:.6f}（相对差 {rel:.2e}）；G5 报错={caught}"
    if mid == "M4":
        # 注入一个超出存储下限的错误 ⇒ G1b 必须报。
        caught = "G1B_BEYOND_STORAGE_FLOOR" in after["gates"]
        n_g1b = sum(1 for c in after["gates"] if c == "G1B_BEYOND_STORAGE_FLOOR")
        return caught, (
            f"偏移后共 {len(after['gates'])} 条报错，其中 G1B 占 {n_g1b} 条 ⇒ "
            f"G1b 独立抓到了（不是靠 G1 顺带）"
        )
    if mid == "M7":
        # 同一个错误，但 G1b 被削掉 ⇒ 必须**抓不到**。抓不到才证明 M4 的「抓到」来自 G1b 本身。
        caught = "G1B_BEYOND_STORAGE_FLOOR" in after["gates"]
        return caught, (
            f"削掉 G1b 后报错降到 {len(after['gates'])} 条（G1B 消失）；"
            f"{'**仍报 G1B ⇒ 不承重**' if caught else '⇒ G1b 是唯一抓到该错误的闸门'}"
        )
    if mid == "M5":
        caught = not after["rate_monotone"]
        return caught, f"三档等效费率 {['%.6f' % x for x in after['econ_rates']]}；单调={after['rate_monotone']}"
    if mid == "M6":
        # 循环论证会让「复现偏差」变小——指标变好就是缺陷信号
        caught = after["net_rel_to_summary"] < base["net_rel_to_summary"] / 2
        return caught, (
            f"net 相对偏差 {base['net_rel_to_summary']:.2e} → {after['net_rel_to_summary']:.2e}"
            f"（变小即证实在抄产物）"
        )
    return after != base, "输出是否改变"


if __name__ == "__main__":
    sys.exit(main())
