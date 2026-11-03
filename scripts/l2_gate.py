"""L2 蒙特卡洛闸门 CLI：在 L1b 产出的权益路径上做块重抽样。

用法：
    python scripts/l2_gate.py

退出码：0 = L2_PASSED；2 = BLOCKED_PREREQUISITE；其他非零 = 被否决或不可判定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

DEFAULT_SPEC = ROOT / "docs" / "gates" / "specs" / "H_L2_MACROSS_000001.json"
DEFAULT_OUT = ROOT / "reports" / "gates" / "H-L2-MACROSS-000001"


def _load_curve(l1b_result: Path, leg: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not l1b_result.exists():
        raise FileNotFoundError(f"找不到 L1b 结果：{l1b_result}（先跑 scripts/l1b_gate.py）")
    payload = json.loads(l1b_result.read_text(encoding="utf-8"))
    result = payload.get("result") or {}
    leg_payload = result.get(leg)
    if not leg_payload or not leg_payload.get("equity_curve"):
        raise ValueError(f"L1b 结果里没有 {leg} 档的权益曲线")
    meta = {
        "l1a_verdict": payload.get("l1a_verdict"),
        "l1b_verdict": result.get("verdict"),
        "leg_label": leg_payload.get("label"),
        "leg_return": leg_payload.get("cumulative_return"),
        "leg_fill_rate": leg_payload.get("fill_rate_market"),
    }
    return list(leg_payload["equity_curve"]), meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L2 蒙特卡洛闸门")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from deepquant.gates.l2 import (
        VERDICT_PASSED,
        VERDICT_PREREQUISITE,
        parse_monte_carlo,
        render_l2_markdown,
        run_l2,
    )

    spec_path = Path(args.spec)
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    mc = parse_monte_carlo(payload.get("monte_carlo"))

    derived = payload.get("derived_from") or {}
    l1b_result = ROOT / derived.get("l1b_result", "reports/gates/H-L1B-MACROSS-000001/l1b_result.json")
    leg = str(payload.get("curve", "stressed"))
    try:
        curve, meta = _load_curve(l1b_result, leg)
    except (FileNotFoundError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3

    result = run_l2(
        curve=curve,
        hypothesis_id=str(payload.get("hypothesis_id", "H-L2")),
        spec=mc,
        l1a_verdict=meta.get("l1a_verdict"),
        l1b_verdict=meta.get("l1b_verdict"),
    )
    result.notes.append(f"曲线来源：{l1b_result.name} 的 {leg} 档（{meta.get('leg_label')}）")
    result.notes.append(f"L1b 终态：{meta.get('l1b_verdict')}；L1a 终态：{meta.get('l1a_verdict')}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_l2_markdown(result, title=str(payload.get("title", "")))
    (out_dir / "l2_report.md").write_text(markdown + "\n", encoding="utf-8", newline="\n")

    output = {
        "spec_file": str(spec_path.relative_to(ROOT)),
        "upstream": meta,
        "result": result.to_dict(),
    }
    (out_dir / "l2_result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"终态：{result.verdict}")
        print(f"观测 {result.observations} 日，实际收益 {result.actual_return:+.4f}，"
              f"实际 MDD {result.actual_max_drawdown:.4f}")
        dist = result.distribution
        print(f"重抽样：中位 {dist['terminal_median']:+.4f}｜5% 分位 {dist['terminal_p5']:+.4f}｜"
              f"MDD95 {dist['mdd_p95']:.4f}｜负收益占比 {dist['loss_probability']:.3f}")
        if result.repeatable_ratio is not None:
            print(f"可重复性 {result.repeatable_ratio:.4f}｜块长稳定 {result.block_length_stable}")
        for check in result.checks:
            mark = {True: "PASS", False: "FAIL", None: "UNDETERMINED"}[check["passed"]]
            print(f"  [{mark}] {check['name']}: {check['detail']}")
        print(f"报告：{out_dir / 'l2_report.md'}")

    if result.verdict == VERDICT_PASSED:
        return 0
    if result.verdict == VERDICT_PREREQUISITE:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
