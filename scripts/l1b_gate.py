"""L1b 精验闸门 CLI：把 L1a 存活策略放进事件驱动内核重跑。

用法：
    python scripts/l1b_gate.py --spec docs/gates/specs/H_L1B_MACROSS_000001.json

退出码：0 = L1B_PASSED；2 = BLOCKED_PREREQUISITE（L1a 未放行，结果仅供参考）；
        其他非零 = 被否决或不可判定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

DEFAULT_SPEC = ROOT / "docs" / "gates" / "specs" / "H_L1B_MACROSS_000001.json"
DEFAULT_L1A_RESULT = ROOT / "reports" / "gates" / "H-L1A-MACROSS-000001" / "result.json"
DEFAULT_OUT_DIR = ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001"
EVIDENCE = ROOT / "reports" / "L1B_GATE_EVIDENCE.json"


def _load_l1a(path: Path) -> tuple[str | None, dict[str, Any]]:
    if not path.exists():
        return None, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    best = (payload.get("sweep") or {}).get("best") or {}
    return payload.get("verdict"), dict(best.get("params") or {})


def _sensitivity(spec_payload: dict[str, Any], best: dict[str, Any], cashes: list[float]) -> list[dict[str, Any]]:
    """资金敏感性：同策略、同区间，只改资金规模（不参与主判定）。"""
    from deepquant.gates.l1b import parse_execution, run_l1b

    rows: list[dict[str, Any]] = []
    for cash in cashes:
        result = run_l1b(spec_payload, best_params=best, cash=cash)
        base = result.baseline
        stress = result.stressed
        rows.append(
            {
                "cash": cash,
                "verdict": result.verdict,
                "baseline_return": base.cumulative_return if base else None,
                "stressed_return": stress.cumulative_return if stress else None,
                "retention": result.retention,
                "fill_rate_market": stress.fill_rate_market if stress else None,
                "reasons": stress.reasons if stress else {},
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L1b 事件驱动精验闸门")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--l1a-result", default=str(DEFAULT_L1A_RESULT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from deepquant.gates.l1b import (
        VERDICT_PASSED,
        VERDICT_PREREQUISITE,
        parse_execution,
        render_l1b_markdown,
        run_l1b,
    )
    from deepquant.gates.spec import load_spec

    spec_path = Path(args.spec)
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = load_spec(spec_path)
    l1a_verdict, best = _load_l1a(Path(args.l1a_result))
    if not best:
        print("✗ 找不到 L1a 最优参数：先跑 scripts/l1a_gate.py", file=sys.stderr)
        return 3

    result = run_l1b(spec, best_params=best, l1a_verdict=l1a_verdict)

    execution = parse_execution(payload.get("execution"))
    cashes = [float(value) for value in (payload.get("execution") or {}).get("sensitivity_cash", [])]
    sensitivity = _sensitivity(payload, best, cashes) if cashes else []

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_l1b_markdown(result, sensitivity=sensitivity, title=payload.get("title", ""))
    (out_dir / "l1b_report.md").write_text(markdown + "\n", encoding="utf-8", newline="\n")

    output = {
        "spec_file": str(spec_path.relative_to(ROOT)),
        "l1a_verdict": l1a_verdict,
        "execution_digest": execution.digest,
        "sensitivity": sensitivity,
        "result": result.to_dict(),
    }
    (out_dir / "l1b_result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"终态：{result.verdict}")
        if result.baseline and result.stressed:
            print(
                f"基线 {result.baseline.cumulative_return:+.4f} → "
                f"精验 {result.stressed.cumulative_return:+.4f}，"
                f"保留率 {result.retention:.4f}，"
                f"市场性成交率 {result.stressed.fill_rate_market}"
            )
        for check in result.checks:
            mark = {True: "PASS", False: "FAIL", None: "UNDETERMINED"}[check["passed"]]
            print(f"  [{mark}] {check['name']}: {check['detail']}")
        print(f"报告：{out_dir / 'l1b_report.md'}")

    if result.verdict == VERDICT_PASSED:
        return 0
    if result.verdict == VERDICT_PREREQUISITE:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
