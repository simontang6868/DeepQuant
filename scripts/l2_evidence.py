"""L2 闸门验收取证：机制、判据、终态、以及「用 IID 会低估多少尾部」。

产出 ``reports/L2_GATE_EVIDENCE.json``。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

OUT = ROOT / "reports" / "L2_GATE_EVIDENCE.json"
L1B_RESULT = ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001" / "l1b_result.json"


def _ar1(phi: float = 0.8, n: int = 4000, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    series = np.zeros(n)
    for index in range(1, n):
        series[index] = phi * series[index - 1] + rng.normal(0, 0.01)
    return series


def _lag1(values: np.ndarray) -> float:
    return float(np.corrcoef(values[:-1], values[1:])[0, 1])


def evidence_block_vs_iid() -> dict[str, Any]:
    """机制验证：块抽样保留自相关，IID 摧毁自相关。"""
    from deepquant.gates.l2 import stationary_bootstrap

    series = _ar1()
    block = stationary_bootstrap(series, 40, np.random.default_rng(5))
    iid = np.random.default_rng(5).permutation(series)
    return {
        "original_lag1": _lag1(series),
        "block_lag1": _lag1(block),
        "iid_lag1": _lag1(iid),
        "block_keeps_correlation": _lag1(block) > 0.4,
        "iid_destroys_correlation": abs(_lag1(iid)) < 0.1,
    }


def evidence_iid_understates_tail(returns: np.ndarray, spec_dict: dict[str, Any]) -> dict[str, Any]:
    """同一条真实收益序列，块抽样 vs IID 抽样：尾部差多少。

    这条是"必须用块抽样"的实证依据——若偷懒用 IID，会得到更好看的尾部，
    即系统性低估风险。
    """
    from deepquant.gates.l2 import max_drawdown, summarise_distribution, stationary_bootstrap

    rng = np.random.default_rng(int(spec_dict.get("seed", 20261027)))
    reps = int(spec_dict.get("replications", 2000))
    length = int(spec_dict.get("mean_block_length", 20))

    block_terminal = np.empty(reps)
    block_mdd = np.empty(reps)
    iid_terminal = np.empty(reps)
    iid_mdd = np.empty(reps)
    for index in range(reps):
        sample = stationary_bootstrap(returns, length, rng)
        path = np.cumprod(1.0 + sample)
        block_terminal[index] = path[-1] - 1.0
        block_mdd[index] = max_drawdown(path)

        iid_sample = rng.permutation(returns)
        iid_path = np.cumprod(1.0 + iid_sample)
        iid_terminal[index] = iid_path[-1] - 1.0
        iid_mdd[index] = max_drawdown(iid_path)

    block = summarise_distribution(block_terminal, block_mdd)
    iid = summarise_distribution(iid_terminal, iid_mdd)
    # IID 置换不改变复合收益（prod(1+r) 与顺序无关），因此其期末收益分布退化为一个点。
    # 这不是"低估"，而是"完全无信息"——必须单独报出来，否则"5% 分位比块抽样高"
    # 会被误读成"IID 更乐观"，实际是 IID 根本估不出分布。
    degenerate = bool(np.std(iid_terminal) < 1e-12)
    return {
        "block": {
            "terminal_p5": block["terminal_p5"],
            "cvar5": block["cvar5"],
            "mdd_p95": block["mdd_p95"],
            "loss_probability": block["loss_probability"],
        },
        "iid": {
            "terminal_p5": iid["terminal_p5"],
            "cvar5": iid["cvar5"],
            "mdd_p95": iid["mdd_p95"],
            "loss_probability": iid["loss_probability"],
        },
        "iid_optimism": {
            "terminal_p5_diff": iid["terminal_p5"] - block["terminal_p5"],
            "mdd_p95_diff": iid["mdd_p95"] - block["mdd_p95"],
            "loss_probability_diff": iid["loss_probability"] - block["loss_probability"],
        },
        "iid_understates_risk": bool(
            iid["mdd_p95"] < block["mdd_p95"] or iid["terminal_p5"] > block["terminal_p5"]
        ),
        "iid_terminal_degenerate": degenerate,
        "iid_terminal_std": float(np.std(iid_terminal, ddof=1)),
        "block_terminal_std": float(np.std(block_terminal, ddof=1)),
        "interpretation": (
            "IID 置换不改变复合收益，期末收益分布退化为单点（std≈0）："
            "它对『收益分布』零信息，只在回撤上给出系统性偏低的估计。"
            if degenerate
            else "IID 与块抽样的期末收益分布均非退化，差异体现在分位点上。"
        ),
    }


def evidence_reproducible(returns: np.ndarray, spec_dict: dict[str, Any]) -> dict[str, Any]:
    from deepquant.gates.l2 import MonteCarloSpec, parse_monte_carlo, simulate

    spec = parse_monte_carlo(spec_dict)
    first = simulate(returns, spec)
    second = simulate(returns, spec)
    return {
        "same_seed_identical": bool(np.array_equal(first["terminal"], second["terminal"])),
        "digest": spec.digest,
    }


def evidence_trim_profile(returns: np.ndarray) -> dict[str, Any]:
    from deepquant.gates.l2 import trimmed_return

    fractions = (0.002, 0.005, 0.01, 0.02, 0.05)
    rows = [trimmed_return(returns, fraction) for fraction in fractions]
    values = [row["trimmed_return"] for row in rows]
    monotone = all(later <= earlier + 1e-12 for earlier, later in zip(values, values[1:]))
    crossing = next(
        (row["removed_days"] for row in rows if row["trimmed_return"] <= 0),
        None,
    )
    return {
        "profile": [
            {
                "fraction": row["fraction"],
                "removed_days": row["removed_days"],
                "trimmed_return": row["trimmed_return"],
                "removed_total_return": row["removed_total_return"],
            }
            for row in rows
        ],
        "monotone_decreasing": monotone,
        "first_nonpositive_at_days": crossing,
    }


def evidence_verdicts() -> dict[str, Any]:
    from deepquant.gates.l2 import ALL_VERDICTS, MonteCarloSpec, run_l2

    rng = np.random.default_rng(7)
    stable = rng.normal(0.0010, 0.004, 1200)
    spike = np.zeros(1200)
    spike[::100] = 0.05
    equity_stable = 100_000 * np.cumprod(1 + stable)
    equity_spike = 100_000 * np.cumprod(1 + spike)
    short = 100_000 * np.cumprod(1 + rng.normal(0.001, 0.004, 100))

    cases = {
        "passed": run_l2(equity_stable, l1a_verdict="L1A_PASSED").verdict,
        "extreme_dependence": run_l2(equity_spike, l1a_verdict="L1A_PASSED").verdict,
        "tail": run_l2(
            equity_stable,
            spec=MonteCarloSpec(max_mdd_p95=0.001, max_terminal_loss_p5=-0.001),
            l1a_verdict="L1A_PASSED",
        ).verdict,
        "inconclusive": run_l2(short, l1a_verdict="L1A_PASSED").verdict,
        "prerequisite": run_l2(equity_stable, l1a_verdict="L1A_REJECTED_BOUNDS").verdict,
        "spec": run_l2([], l1a_verdict="L1A_PASSED").verdict,
    }
    covered = set(cases.values())
    not_covered = sorted(set(ALL_VERDICTS) - covered)
    justified = {
        "L2_REJECTED_PATH_DEPENDENCE": (
            "需要一条『整段收益为正、但打乱顺序后优势消失』的序列；"
            "已在 tests/gates/test_gates_l2.py::test_path_dependence_rejected_when_shuffle_kills_edge "
            "中构造并验证（该序列同时会触发 E1，故终态归入 E1）。"
        )
    }
    return {
        "cases": cases,
        "covered_verdicts": sorted(covered),
        "not_covered": not_covered,
        "not_covered_justification": {key: justified[key] for key in not_covered if key in justified},
        "coverage_ok": set(not_covered) <= set(justified),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L2 闸门验收取证")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)

    from deepquant.gates.l2 import returns_from_curve

    spec_payload = json.loads((ROOT / "docs" / "gates" / "specs" / "H_L2_MACROSS_000001.json").read_text(encoding="utf-8"))
    spec_dict = spec_payload.get("monte_carlo") or {}

    if not L1B_RESULT.exists():
        print("✗ 缺少 L1b 结果，先跑 scripts/l1b_gate.py", file=sys.stderr)
        return 3
    l1b = json.loads(L1B_RESULT.read_text(encoding="utf-8"))
    curve = (l1b.get("result") or {}).get("stressed", {}).get("equity_curve") or []
    if not curve:
        print("✗ L1b 结果里没有精验档权益曲线", file=sys.stderr)
        return 3
    returns, missing = returns_from_curve(curve)

    print("1/5 块抽样 vs IID 的自相关…", flush=True)
    block_vs_iid = evidence_block_vs_iid()
    print("2/5 IID 对尾部的影响…", flush=True)
    tail_gap = evidence_iid_understates_tail(returns, spec_dict)
    print("3/5 可复现性…", flush=True)
    reproducible = evidence_reproducible(returns, spec_dict)
    print("4/5 剔除剖面…", flush=True)
    profile = evidence_trim_profile(returns)
    print("5/5 终态覆盖…", flush=True)
    verdicts = evidence_verdicts()

    standards = {
        "块抽样保留自相关且 IID 摧毁它": block_vs_iid["block_keeps_correlation"]
        and block_vs_iid["iid_destroys_correlation"],
        "同种子重跑逐位一致": reproducible["same_seed_identical"],
        "剔除剖面单调（剔除越多收益越低）": profile["monotone_decreasing"],
        "终态覆盖无静默缺口": verdicts["coverage_ok"],
        "IID 的期末收益分布退化为单点（证明其无信息）": tail_gap["iid_terminal_degenerate"],
        "IID 在回撤上系统性低估（块抽样更保守）": tail_gap["iid"]["mdd_p95"]
        < tail_gap["block"]["mdd_p95"],
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_curve": {
            "file": str(L1B_RESULT.relative_to(ROOT)),
            "leg": "stressed",
            "observations": int(returns.size),
            "missing_valuation_days": int(missing),
        },
        "spec": spec_dict,
        "standards": standards,
        "all_passed": all(standards.values()),
        "block_vs_iid": block_vs_iid,
        "tail_gap": tail_gap,
        "reproducible": reproducible,
        "trim_profile": profile,
        "verdicts": verdicts,
        "limitations": [
            "stationary bootstrap 只重排历史日收益，不会产生历史上没出现过的亏损幅度：真实尾部可能更厚。",
            "块长是主观参数，已用 10/20/40/60 四档做敏感性。",
            "E1 与 E2 互补而非替代：块抽样保留极端日，E2 可能给出『可重复』的假象。",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    print()
    for name, passed in standards.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print()
    print(f"尾部对比：块抽样 MDD95 {tail_gap['block']['mdd_p95']:.4f} vs "
          f"IID {tail_gap['iid']['mdd_p95']:.4f}")
    print(f"期末收益分布标准差：块抽样 {tail_gap['block_terminal_std']:.4f} vs "
          f"IID {tail_gap['iid_terminal_std']:.6f}（IID 退化={tail_gap['iid_terminal_degenerate']}）")
    print(f"\n{'全部通过' if payload['all_passed'] else '存在未通过项'} → {out}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
