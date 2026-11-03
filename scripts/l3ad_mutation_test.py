#!/usr/bin/env python
"""L3ad 独立性判据的变异测试。

本阶段唯一能「悄悄错」的地方就是判据本身。六个变异体全部作用在判据或代表选取上：

| # | 注入的缺陷 | 应被哪条不变量抓住 |
|---|---|---|
| M1 | 判据去掉「较短序列被完全覆盖」 | 合并量必须恰为 4；出现部分重叠的假对即失败 |
| M2 | 判据退回「只认等长同值」（即 L3ac 的做法） | m 必须为 25（退回后变 27） |
| M3 | 判据改用相关系数 > 0.99 代替逐位相同 | 合并量会暴涨，且出现 max\\|差\\| > 0 的边 |
| M4 | 代表选「窗口最短」的那条 | 代表 n 必须 ≥ 被吸收者 n |
| M5 | FWER 模拟按「两次独立抽样」算 | 计 2 个名额的拒绝率会从 ≈0.025 变 ≈0.049 |
| M6 | 容差放宽到 1e-6 | 合并量可能多于 4，或边上的 max\\|差\\| 不再是 0 |

M2 不是虚构的缺陷：L3ac 真的就是这么做的（R1 只认严格子窗口），结果是 m=27。
所以这条变异体是在重放一个已经发生过的判断，用来证明本阶段确实改变了它。
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "gates" / "H-L3AD-INDEPENDENCE" / "l3ad_mutation.json"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _invariants(ind) -> dict:
    res = ind.build(ROOT)
    return {
        "n_groups": res["partition"]["n_groups"],
        "merged_away": res["partition"]["n_merged_away"],
        "m_ind": res["runs"]["IND_m25"]["m"],
        "n_pairs": len(res["partition"]["pairs"]),
        "max_diff": max((p["max_abs_diff"] for p in res["partition"]["pairs"]), default=0.0),
        "min_coverage": min((p["coverage"] for p in res["partition"]["pairs"]), default=1.0),
        "rep_never_shorter": all(
            res["stats"][rep]["n"] >= res["stats"][a]["n"]
            for a, rep in res["merged_into"].items()
            if a in res["stats"] and rep in res["stats"]
        ),
        "fwer_m2": res["fwer"]["reject_rate_m2"],
        "counts": {v: res["runs"][v]["counts"] for v in res["runs"]},
    }


def main() -> int:
    ind = _load("l3ad_ind", "backend/src/deepquant/gates/independence.py")

    baseline = _invariants(ind)

    orig_pairs = ind.identical_pairs
    orig_groups = ind.identical_groups
    orig_rep = ind.choose_representative
    orig_fwer = ind.fwer_simulation
    orig_tol = ind.TOL

    def restore() -> None:
        ind.identical_pairs = orig_pairs
        ind.identical_groups = orig_groups
        ind.choose_representative = orig_rep
        ind.fwer_simulation = orig_fwer
        ind.TOL = orig_tol

    def m1() -> None:
        """判据去掉「完全覆盖」要求：只要有共同月同值就算。"""

        def loose(series):
            keys = sorted(series)
            out = []
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    sa, sb = series[a], series[b]
                    common = set(sa.values) & set(sb.values)
                    if not common:
                        continue
                    mx = max(abs(sa.values[m] - sb.values[m]) for m in common)
                    if mx <= ind.TOL:
                        out.append(
                            {
                                "a": a,
                                "b": b,
                                "n_a": sa.n,
                                "n_b": sb.n,
                                "n_common": len(common),
                                "coverage": len(common) / min(sa.n, sb.n),
                                "max_abs_diff": mx,
                                "equal_length": sa.n == sb.n,
                            }
                        )
            return out

        ind.identical_pairs = loose

    def m2() -> None:
        """退回 L3ac：只认严格更短（等长同值不合并）。"""

        def strict_only(series):
            out = []
            for p in orig_pairs(series):
                if p["n_a"] != p["n_b"]:
                    out.append(p)
            return out

        ind.identical_pairs = strict_only

    def m3() -> None:
        """用相关系数代替逐位相同。"""

        def by_corr(series):
            keys = sorted(series)
            out = []
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    sa, sb = series[a], series[b]
                    common = sorted(set(sa.values) & set(sb.values))
                    if len(common) < 24:
                        continue
                    xs = [sa.values[m] for m in common]
                    ys = [sb.values[m] for m in common]
                    rho = ind.dp.pearson(xs, ys)
                    if rho == rho and abs(rho) > 0.99:
                        out.append(
                            {
                                "a": a,
                                "b": b,
                                "n_a": sa.n,
                                "n_b": sb.n,
                                "n_common": len(common),
                                "coverage": len(common) / min(sa.n, sb.n),
                                "max_abs_diff": max(abs(x - y) for x, y in zip(xs, ys)),
                                "equal_length": sa.n == sb.n,
                            }
                        )
            return out

        ind.identical_pairs = by_corr

    def m4() -> None:
        """代表改成「窗口最短」。"""

        def shortest(group, series, scored):
            return sorted(group, key=lambda k: (series[k].n, k))[0]

        ind.choose_representative = shortest

    def m5() -> None:
        """FWER 模拟按「两次独立抽样」——两次读的不是同一个 p。"""

        def independent(*, n_sims: int = 200_000, alpha: float = 0.05, seed: int = 20261029):
            rng = random.Random(seed)
            hit2 = 0
            hit1 = 0
            for _ in range(n_sims):
                p1 = rng.random()
                p2 = rng.random()  # 缺陷：另抽一个，而不是读同一个
                if p1 <= alpha / 2 or p2 <= alpha / 2:
                    hit2 += 1
                if p1 <= alpha:
                    hit1 += 1
            return {
                "n_sims": n_sims,
                "alpha": alpha,
                "seed": seed,
                "reject_rate_m2": hit2 / n_sims,
                "reject_rate_m1": hit1 / n_sims,
                "expected_m2": alpha / 2,
                "expected_m1": alpha,
            }

        ind.fwer_simulation = independent

    def m6() -> None:
        """容差放宽到 0.5pp。

        最初用 1e-6 时**未抓到、也没改变任何输出**——因为本数据里三组同值对
        的 max|差| 恰好都是 0.0，1e-6 与 1e-12 等价，是个惰性缺陷。
        改成 0.5pp 才能检验闸门是否真的在看「逐位相同」这件事。
        """
        ind.TOL = 0.5

    mutations = [
        ("M1", "判据去掉「完全覆盖」要求（本数据上惰性）", m1),
        ("M2", "退回 L3ac：只认严格更短", m2),
        ("M3", "用相关系数代替逐位相同", m3),
        ("M4", "代表改成「窗口最短」", m4),
        ("M5", "FWER 按两次独立抽样", m5),
        ("M6", "容差放宽到 0.5pp", m6),
    ]

    results = []
    for mid, desc, apply_mut in mutations:
        restore()
        apply_mut()
        err = None
        try:
            after = _invariants(ind)
        except Exception as exc:  # noqa: BLE001
            after = None
            err = f"{type(exc).__name__}: {exc}"
        finally:
            restore()

        if after is None:
            caught = True
            signal = {"error": err}
        else:
            if mid == "M1":
                hit = after["merged_away"] != baseline["merged_away"] or after["min_coverage"] < 1.0
            elif mid == "M2":
                hit = after["m_ind"] != baseline["m_ind"]
            elif mid == "M3":
                hit = after["merged_away"] != baseline["merged_away"] or after["max_diff"] > ind.TOL
            elif mid == "M4":
                hit = not after["rep_never_shorter"]
            elif mid == "M5":
                hit = abs(after["fwer_m2"] - baseline["fwer_m2"]) > 0.005
            elif mid == "M6":
                hit = after["merged_away"] != baseline["merged_away"]
            else:
                hit = after != baseline
            caught = bool(hit)
            signal = {
                "merged_away": [baseline["merged_away"], after["merged_away"]],
                "m_ind": [baseline["m_ind"], after["m_ind"]],
                "max_diff": [baseline["max_diff"], after["max_diff"]],
                "min_coverage": [baseline["min_coverage"], after["min_coverage"]],
                "rep_never_shorter": [baseline["rep_never_shorter"], after["rep_never_shorter"]],
                "fwer_m2": [baseline["fwer_m2"], after["fwer_m2"]],
            }

        results.append(
            {
                "id": mid,
                "description": desc,
                "caught": caught,
                "changed_output": after != baseline if after is not None else True,
                "signal": signal,
            }
        )

    restore()
    restored = _invariants(ind)
    consistent = restored == baseline
    caught_n = sum(1 for r in results if r["caught"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "experiment_id": "H-L3AD-INDEPENDENCE",
                "mutation_count": len(results),
                "caught": caught_n,
                "all_caught": caught_n == len(results),
                "restored_matches_baseline": consistent,
                "baseline": baseline,
                "mutations": results,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"变异 {caught_n}/{len(results)} 抓到；源码恢复一致 {consistent}")
    for r in results:
        print(f"  {r['id']} {'抓到' if r['caught'] else '未抓到'}  改变输出={r['changed_output']}  {r['description']}")
    return 0 if (caught_n == len(results) and consistent) else 1


if __name__ == "__main__":
    raise SystemExit(main())
